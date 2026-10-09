"""Call identities and an optional monotonic budget across native HTTP attempts."""

from __future__ import annotations

from functools import partial
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
    add_secondary,
    kept_primary,
)
from .timing import Budget, ResolvedTimeoutOptions

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .options import Settings
    from .timing import Clock

ErrorT = TypeVar("ErrorT", bound=SDKError)
T = TypeVar("T")

_REACHED: Final = (DeliveryState.NOT_SENT, DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED)


async def in_thread(function: Callable[..., T], *arguments: object) -> T:
    """Run a blocking file call in a thread and return or raise only once it has finished.

    A cancelled caller still waits for the running call, so a file is never closed, moved or removed under it; the
    first cancellation then propagates, with a failure of that call beside it. The call is an executor future, not a
    task, so cancelling every task of the loop cannot end the wait early.
    """
    import asyncio  # noqa: PLC0415 - Only an asyncio call that opens a path reaches a thread.

    work = asyncio.get_running_loop().run_in_executor(None, partial(function, *arguments))
    cancelled: asyncio.CancelledError | None = None
    while not work.done():
        try:
            if cancelled is None:
                await asyncio.wait((work,))
            else:
                with anyio.CancelScope(shield=True):
                    await asyncio.wait((work,))
        except asyncio.CancelledError as error:  # noqa: PERF203 - Every cancellation waits for the same call.
            cancelled = cancelled or error
    if cancelled is None:
        return work.result()
    if (failure := work.exception()) is not None:
        add_secondary(cancelled, failure)
    raise cancelled


class OperationSession:
    """The identity and earliest absolute deadline shared by a helper's calls."""

    def __init__(self, *, total_timeout: float | None, clock: Clock) -> None:
        self.started = clock.monotonic()
        self.session_id = str(uuid4())
        self.deadline: Budget | None = None if total_timeout is None else Budget(self.started + total_timeout, clock)
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
        self.deadline: Budget | None = (
            None if settings.total_timeout is None else Budget(self.started + settings.total_timeout, settings.clock)
        )
        self.delivery_state = self.earlier = DeliveryState.NOT_SENT
        self.streaming = self.retry_blocked = self.handing_off = False
        self.attempt_count = self.sends = 0
        self.redirects_followed = 0
        self.token_unreceived = False

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
        phase: DeadlinePhase = "unknown",  # noqa: ARG002
        delivery_state: DeliveryState | None = None,
        cause: BaseException | None = None,
    ) -> APITimeoutError | None:
        """Return the failure of a total deadline that has passed, or None while time remains."""
        if self.deadline is None or self.monotonic() < self.deadline.at:
            return None
        return self._deadline_failure(delivery_state, cause)

    def _deadline_failure(self, delivery_state: DeliveryState | None, cause: BaseException | None) -> APITimeoutError:
        assert self.deadline is not None
        return self.snapshot_error(
            APITimeoutError(
                deadline_at=self.deadline.at,
                elapsed=self.monotonic() - self.started,
                phase="unknown",
                delivery_state=self.furthest() if delivery_state is None else delivery_state,
                cause=cause,
                reason="deadline_exceeded",
            )
        )

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

    def admit_send(self) -> None:
        """Record one native send invocation at its boundary."""
        self.check("send")
        self.sends += 1
        if self.session is not None:
            self.session.sends += 1
        self.attempt_count += 1
        self.delivery_state = DeliveryState.MAYBE_SENT

    def _wait(self, not_before: float) -> float:
        """Return one retry wait, capped by the time remaining before the next attempt."""
        self.check("sleep")
        duration = max(0.0, not_before - self.monotonic())
        remaining = self.remaining()
        return duration if remaining is None else min(duration, remaining)

    def sleep_until(self, not_before: float) -> None:
        """Sleep once before the next attempt."""
        if duration := self._wait(not_before):
            sleep(duration)
        self.check("sleep")

    async def asleep_until(self, not_before: float) -> None:
        """Await one retry wait with ordinary task cancellation."""
        if duration := self._wait(not_before):
            await anyio.sleep(duration)
        self.check("sleep")

    def timeout(self) -> ResolvedTimeoutOptions:
        """Cap native I/O phase timeouts by the opt-in request budget before sending."""
        self.check("send")
        remaining = self.remaining()
        phases = self.settings.timeout
        if remaining is None:
            return phases
        connect, read, write, pool = (
            remaining if limit is None else min(limit, remaining)
            for limit in (phases.connect, phases.read, phases.write, phases.pool)
        )
        return ResolvedTimeoutOptions(connect=connect, read=read, write=write, pool=pool)

    def handoff(self) -> None:
        """Leave stream I/O timeouts to the native client and retain any helper session's budget."""
        self.streaming = True
        self.delivery_state = DeliveryState.RESPONSE_STARTED
        self.deadline = None if self.session is None else self.session.deadline

    async def cleanup(self, operation: Callable[[], Awaitable[None]], *, error: BaseException | None = None) -> bool:
        """Release a resource with ordinary cancellation, retaining an earlier failure."""
        try:
            await operation()
        except BaseException as failure:
            if error is None:
                raise
            self.retry_blocked = True
            if kept_primary(error, failure) is failure:
                raise
            return False
        return True
