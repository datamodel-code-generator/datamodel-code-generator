"""Call identities, absolute deadlines and cooperative boundaries for native HTTP calls."""

from __future__ import annotations

from dataclasses import replace
from time import sleep
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

import anyio

from .errors import (
    APIConnectionError,
    APITimeoutError,
    AuthError,
    DeadlinePhase,
    DeliveryState,
    IOPhase,
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
        self.delivery_state = DeliveryState.NOT_SENT
        self.finished = self.streaming = self.retry_blocked = False
        self.attempt_count = self.sends = 0
        self.redirects_followed = 0

    @property
    def parent_session_id(self) -> str | None:
        """Return the helper session's safe correlation identifier."""
        return None if self.session is None else self.session.session_id

    def remaining(self) -> float | None:
        """Return seconds until the absolute deadline, or None for an unlimited call."""
        return None if self.deadline is None else self.deadline.remaining()

    def snapshot_error(self, error: ErrorT) -> ErrorT:
        """Attach the call's identity and final execution measurements."""
        error.delivery_state = self.delivery_state
        error.operation_id = self.operation_id
        error.call_id = self.call_id
        error.parent_session_id = self.parent_session_id
        if error.delivery_state is DeliveryState.NOT_SENT and not isinstance(error, (APIConnectionError, AuthError)):
            error.delivery_state = self.delivery_state
        if error.info is None:
            error.attempt_count = self.attempt_count
            error.elapsed = max(0.0, self.monotonic() - self.started)
        return error

    def check(
        self,
        phase: DeadlinePhase = "unknown",
        delivery_state: DeliveryState | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Check the total deadline at an SDK boundary without replacing a primary failure."""
        if self.deadline is not None and self.monotonic() >= self.deadline.at:
            raise self.snapshot_error(
                APITimeoutError(
                    deadline_at=self.deadline.at,
                    elapsed=self.monotonic() - self.started,
                    phase=phase,
                    delivery_state=self.delivery_state if delivery_state is None else delivery_state,
                    cause=cause,
                    reason="deadline_exceeded",
                )
            )

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
        """Clamp every configured native phase once, before constructing this attempt's request."""
        self.check("send")
        remaining = self.remaining()
        phases = self.settings.timeout

        def cap(value: float | None) -> float | None:
            return value if remaining is None else remaining if value is None else min(value, remaining)

        return ResolvedTimeoutOptions(
            connect=cap(phases.connect), read=cap(phases.read), write=cap(phases.write), pool=cap(phases.pool)
        )

    def timeout_failure(self, error: BaseException, phase: IOPhase, delivery_state: DeliveryState) -> SDKError:
        """Keep native timeout classification without reconstructing deadline provenance."""
        return self.snapshot_error(APIConnectionError(delivery_state=delivery_state, phase=phase, cause=error))

    def handoff(self) -> None:
        """Begin stream boundary limits without mutating the native request's timeout."""
        self.streaming = True
        self.delivery_state = DeliveryState.RESPONSE_STARTED
        if (total := self.settings.stream_total_timeout) is not None:
            at = self.monotonic() + total
            if self.deadline is None or at < self.deadline.at:
                self.deadline = absolute_deadline(at, clock=self.settings.clock)
        if (
            self.session is not None
            and (limit := self.session.deadline) is not None
            and (self.deadline is None or limit.at < self.deadline.at)
        ):
            self.deadline = limit

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

    def finish(self) -> None:
        """Mark the call or handed-over stream finished."""
        self.finished = True

    def lane(self, deadline: Deadline | None) -> LogicalCallContext:
        """Give a socket waiter its own boundary deadline, retaining call identity."""
        lane = LogicalCallContext(replace(self.settings, total_timeout=None, deadline=None), self.operation_id)
        lane.call_id, lane.session, lane.started = self.call_id, self.session, self.started
        lane.deadline, lane.streaming, lane.delivery_state = deadline, True, self.delivery_state
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
