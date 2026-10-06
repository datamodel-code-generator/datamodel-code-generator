"""Call identities, absolute deadlines and cooperative boundaries for native HTTP calls."""

from __future__ import annotations

from dataclasses import replace
from time import sleep
from typing import TYPE_CHECKING, Final, TypeVar
from uuid import uuid4

import anyio

from .errors import (
    APIConnectionError,
    APITimeoutError,
    AuthError,
    DeadlinePhase,
    DeliveryState,
    SDKError,
    kept_primary,
)
from .timing import ResolvedTimeoutOptions, absolute_deadline, on_clock, real_end, wait_left

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .options import Settings
    from .timing import Clock, Deadline

T = TypeVar("T")
ErrorT = TypeVar("ErrorT", bound=SDKError)

_REACHED: Final = (DeliveryState.NOT_SENT, DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED)
_PHASES: Final = {"connect": 0, "read": 1, "write": 2, "pool": 3}


class OperationSession:
    """The identity and earliest absolute deadline shared by a helper's calls."""

    def __init__(self, *, total_timeout: float | None, deadline: Deadline | None, clock: Clock) -> None:
        self.started = clock.monotonic()
        self.session_id = str(uuid4())
        deadline = None if deadline is None else on_clock(deadline, clock)
        if total_timeout is not None and (deadline is None or self.started + total_timeout < deadline.at):
            deadline = absolute_deadline(self.started + total_timeout, clock=clock)
        self.deadline: Deadline | None = deadline
        self.sends = 0


class LogicalCallContext:
    """Keep one call's clock, identity and deadline without owning its task or resources."""

    session: OperationSession | None = None

    def __init__(self, settings: Settings, operation_id: str | None = None) -> None:
        self.settings = settings
        self.monotonic = settings.clock.monotonic
        self.started = self.monotonic()
        self.operation_id = operation_id
        self.call_id = str(uuid4())
        deadline = None if settings.deadline is None else on_clock(settings.deadline, settings.clock)
        if settings.total_timeout is not None:
            at = self.started + settings.total_timeout
            if deadline is None or at < deadline.at:
                deadline = absolute_deadline(at, clock=settings.clock)
        self.deadline: Deadline | None = deadline
        self.delivery_state = self.earlier = DeliveryState.NOT_SENT
        self.streaming = self.retry_blocked = self.handing_off = False
        self.capped: tuple[bool, ...] = ()
        self.attempt_count = self.sends = 0
        self.redirects_followed = 0

    @property
    def parent_session_id(self) -> str | None:
        """Return the helper session's safe correlation identifier."""
        return None if self.session is None else self.session.session_id

    def remaining(self) -> float | None:
        """Return seconds until the absolute deadline, or None for an unlimited call."""
        return None if self.deadline is None else self.deadline.remaining()

    def furthest(self) -> DeliveryState:
        """Return how far the call got over all its attempts and hops, never forgetting an earlier send or response."""
        current, earlier = self.delivery_state, self.earlier
        return current if _REACHED.index(current) >= _REACHED.index(earlier) else earlier

    def next_send(self) -> None:
        """Begin another attempt or hop, which has sent nothing yet, keeping how far the earlier ones got."""
        self.earlier = self.furthest()
        self.delivery_state = DeliveryState.NOT_SENT

    def snapshot_error(self, error: ErrorT) -> ErrorT:
        """Attach the call's identity and final execution measurements to an error to publish.

        An error without its own delivery evidence, NOT_SENT, takes how far the call got. A transport or auth failure's
        NOT_SENT holds for its own send only, so it takes how far the call's earlier attempts and hops got.
        """
        error.operation_id = self.operation_id
        error.call_id = self.call_id
        error.parent_session_id = self.parent_session_id
        if error.delivery_state is DeliveryState.NOT_SENT:
            error.delivery_state = (
                self.earlier if isinstance(error, (APIConnectionError, AuthError)) else self.furthest()
            )
        if error.info is None:
            error.attempt_count = self.attempt_count
            error.elapsed = max(0.0, self.monotonic() - self.started)
        return error

    def expired(
        self,
        phase: DeadlinePhase = "unknown",
        delivery_state: DeliveryState | None = None,
        cause: BaseException | None = None,
    ) -> APITimeoutError | None:
        """Return the failure of a total deadline that has passed, or None while time remains."""
        if self.deadline is None or self.monotonic() < self.deadline.at:
            return None
        return self._deadline_failure(phase, delivery_state, cause)

    def _deadline_failure(
        self, phase: DeadlinePhase, delivery_state: DeliveryState | None, cause: BaseException | None
    ) -> APITimeoutError:
        assert self.deadline is not None
        return self.snapshot_error(
            APITimeoutError(
                deadline_at=self.deadline.at,
                elapsed=self.monotonic() - self.started,
                phase=phase,
                delivery_state=self.furthest() if delivery_state is None else delivery_state,
                cause=cause,
                reason="deadline_exceeded",
            )
        )

    def capped_failure(self, error: APITimeoutError) -> APITimeoutError | None:
        """Return the deadline failure of an acquisition's phase timeout whose cap was the time the call had left.

        A tie between a phase's own limit and the time left belongs to the deadline.
        """
        index = _PHASES.get(error.phase)
        if self.streaming or index is None or index >= len(self.capped) or not self.capped[index]:
            return None
        return self._deadline_failure("send", None, error.cause)

    def check(
        self,
        phase: DeadlinePhase = "unknown",
        delivery_state: DeliveryState | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Check the total deadline at an SDK boundary without replacing a primary failure."""
        if (error := self.expired(phase, delivery_state, cause)) is not None:
            raise error

    def failure(self, error: BaseException) -> BaseException:
        """Preserve native interruptions and the failure that occurred before cleanup."""
        if isinstance(error, SDKError):
            return self.snapshot_error(error)
        return error

    def admit_send(self, *, redirect: bool = False) -> None:
        """Record one native send invocation at its boundary."""
        self.check("send")
        self.sends += 1
        if self.session is not None:
            self.session.sends += 1
        if redirect:
            self.redirects_followed += 1
        else:
            self.attempt_count += 1
        self.delivery_state = DeliveryState.MAYBE_SENT

    def sleep_until(self, not_before: float) -> None:
        """Wait against the same absolute call deadline and injected clock."""
        end = real_end(not_before - self.monotonic())
        while True:
            self.check("sleep")
            left = wait_left(not_before - self.monotonic(), end)
            if not left > 0:
                return
            if (remaining := self.remaining()) is not None:
                left = min(left, remaining)
            sleep(left)

    async def asleep_until(self, not_before: float) -> None:
        """Sleep in the current backend, allowing native task cancellation to propagate."""
        end = real_end(not_before - self.monotonic())
        while True:
            self.check("sleep")
            left = wait_left(not_before - self.monotonic(), end)
            if not left > 0:
                return
            if (remaining := self.remaining()) is not None:
                left = min(left, remaining)
            await anyio.sleep(left)

    def timeout(self) -> ResolvedTimeoutOptions:
        """Clamp every native phase to the time left once, before this attempt's request, recording the capped phases.

        A call that hands its response over as a stream reads with the smaller of its stream idle limit and an
        explicit read limit, since its request keeps that read limit for every body read.
        """
        self.check("send")
        remaining = self.remaining()
        phases = self.settings.timeout
        read = phases.read
        if self.handing_off:
            read, idle = self.settings.stream_read_timeout, self.settings.stream_idle_timeout
            read = idle if read is None else read if idle is None else min(read, idle)
        limits = (phases.connect, read, phases.write, phases.pool)
        self.capped = tuple(remaining is not None and (limit is None or remaining <= limit) for limit in limits)
        connect, read, write, pool = (
            limit if remaining is None else remaining if limit is None else min(limit, remaining) for limit in limits
        )
        return ResolvedTimeoutOptions(connect=connect, read=read, write=write, pool=pool)

    def handoff(self) -> None:
        """Start the stream's own limits: its total timeout and its session's deadline replace the acquisition's."""
        self.streaming = True
        self.delivery_state = DeliveryState.RESPONSE_STARTED
        total = self.settings.stream_total_timeout
        deadline = None if total is None else absolute_deadline(self.monotonic() + total, clock=self.settings.clock)
        if (session := self.session) is not None and (limit := session.deadline) is not None:
            deadline = limit if deadline is None or limit.at < deadline.at else deadline
        self.deadline = deadline

    def idle(self, started: float, limit: float | None = None) -> None:
        """Raise a read timeout when a handed-over stream's read that began at `started` took its idle limit or more.

        The limit is the stream's idle timeout unless one is given; the check runs once the read returned.
        """
        limit = self.settings.stream_idle_timeout if limit is None else limit
        if self.streaming and limit is not None and self.monotonic() - started >= limit:
            raise APITimeoutError(
                phase="read",
                reason="phase_timeout",
                effective_timeout=limit,
                delivery_state=DeliveryState.RESPONSE_STARTED,
            )

    def lane(self, deadline: Deadline | None) -> LogicalCallContext:
        """Give a socket waiter its own boundary deadline, retaining call identity."""
        lane = LogicalCallContext(replace(self.settings, total_timeout=None, deadline=None), self.operation_id)
        lane.call_id, lane.session, lane.started = self.call_id, self.session, self.started
        lane.deadline, lane.streaming, lane.delivery_state = deadline, True, self.delivery_state
        lane.earlier = self.earlier
        return lane

    async def bounded(  # noqa: PLR0913
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        phase: DeadlinePhase = "unknown",
        delivery_state: DeliveryState | None = None,
        cleanup: Callable[[T], Awaitable[None]] | None = None,
        idle_timeout: float | None = None,
        idle: bool = True,
    ) -> T:
        """Await work directly, checking limits only at cooperative boundaries."""
        self.check(phase, delivery_state)
        started = self.monotonic()
        result = await operation()
        try:
            self.check(phase, delivery_state)
            if phase == "stream" and idle:
                self.idle(started, idle_timeout)
        except BaseException as error:
            if cleanup is not None:
                await self.cleanup(lambda: cleanup(result), error=error)
            raise
        return result

    async def cleanup(self, operation: Callable[[], Awaitable[None]], *, error: BaseException | None = None) -> bool:
        """Release a resource in the current task under a narrow cleanup shield."""
        try:
            with anyio.CancelScope(shield=True):
                await operation()
        except BaseException as failure:
            if error is None:
                raise
            self.retry_blocked = True
            if kept_primary(error, failure) is failure:
                raise
            return False
        return True
