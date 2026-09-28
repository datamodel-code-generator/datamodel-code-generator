"""One call's identity, admission counters, time limits, and the release of its asynchronous work."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, TypeVar
from uuid import uuid4

from .errors import (
    BudgetExceededError,
    CleanupError,
    ClientClosedError,
    DeadlineExceededError,
    DeliveryState,
    PhaseTimeoutError,
    RequestCancelledError,
    SDKError,
    TransportError,
    add_secondary,
    set_error_counters,
)
from .lifecycle import LEFT_WORK, TaskInterruptionError, cleanup_secondary, task_failure, task_result
from .options import network_send_limit
from .timing import absolute_deadline
from .transports import AttemptIOContext, ResolvedTimeoutOptions, set_io_timing

if TYPE_CHECKING:
    import asyncio
    from _thread import LockType
    from collections.abc import Awaitable, Callable

    from .errors import DeadlinePhase, IOPhase
    from .lifecycle import CleanupOwner
    from .options import Settings
    from .timing import Deadline
    from .transports import AttemptTrace

T = TypeVar("T")
ErrorT = TypeVar("ErrorT", bound=SDKError)
StopReason: TypeAlias = Literal["token", "closing", "deadline", "idle"]
TOKEN_INTERVAL = 0.05


class _Scope(Protocol):
    lock: LockType

    def closing(self) -> ClientClosedError | None: ...

    def closing_signals(self) -> tuple[asyncio.Future[None], ...]: ...

    def wait(self, timeout: float) -> None: ...

    def retain_cleanup(
        self, task: asyncio.Task[object], error: BaseException | None = None, *, owner: CleanupOwner | None = None
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PhaseCap:
    """The configured and effective cap of a native phase, with the constraint that selected it."""

    configured: float | None
    effective: float | None
    cap_source: Literal["phase", "deadline", "idle"]
    deadline_at: float | None = None


def _cap(
    configured: float | None,
    remaining: float | None,
    deadline: Deadline | None,
    idle: float | None = None,
) -> PhaseCap:
    effective = configured
    source: Literal["phase", "deadline", "idle"] = "phase"
    if idle is not None and (effective is None or idle <= effective):
        effective, source = idle, "idle"
    if remaining is not None and (effective is None or remaining <= effective) and deadline is not None:
        return PhaseCap(configured, remaining, "deadline", deadline.at)
    return PhaseCap(configured, effective, source)


def _secondary(error: BaseException, task: asyncio.Task[object]) -> None:
    if (failure := task_failure(task)) is not None:
        cleanup_secondary(error, failure)


async def _released(operation: Callable[[], Awaitable[None]]) -> None:
    try:
        await operation()
    except BaseException as error:
        if isinstance(error, Exception):
            raise
        raise TaskInterruptionError(error) from None


def _cancelling(task: asyncio.Task[object] | None) -> int:
    return task.cancelling() if sys.version_info >= (3, 11) and task is not None else 0


def _uncancel(task: asyncio.Task[object] | None) -> int:
    return task.uncancel() if sys.version_info >= (3, 11) and task is not None else 0


async def _joined(tasks: tuple[asyncio.Task[None], ...]) -> None:
    import asyncio  # noqa: PLC0415

    await asyncio.wait(tasks)
    if failures := [failure for task in tasks if (failure := task_failure(task)) is not None]:
        raise CleanupError(cause=failures[0], secondary_errors=tuple(failures[1:]))


class _Guard:
    """Cancel the task awaiting a call's work once its token, client closing, deadline, or stream idle limit stops it.

    Like asyncio.timeout, it counts its own cancellation request so that a native cancellation of the same task keeps
    precedence, and it withdraws its request when the work ends. Without a token it arms no polling timer.
    """

    __slots__ = (
        "_armed",
        "_handles",
        "_loop",
        "_poller",
        "_signals",
        "_token",
        "error",
        "idle_timeout",
        "level",
        "reason",
        "task",
    )

    def __init__(self, call: LogicalCallContext, idle_timeout: float | None) -> None:
        """Arm the call's deadline, stream idle limit, client closing signals, and token polling."""
        import asyncio  # noqa: PLC0415

        self._loop = loop = asyncio.get_running_loop()
        self.task: asyncio.Task[object] | None = asyncio.current_task()
        self.level = _cancelling(self.task)
        self.reason: StopReason | None = None
        self.error: BaseException | None = None
        self.idle_timeout = idle_timeout
        self._armed = True
        self._token = call.settings.cancel_token
        self._handles: list[asyncio.TimerHandle] = []
        if (deadline := call.deadline) is not None:
            self._handles.append(loop.call_at(loop.time() + deadline.remaining(), self.stop, "deadline"))
        if idle_timeout is not None:
            self._handles.append(loop.call_at(loop.time() + idle_timeout, self.stop, "idle"))
        self._poller = None if self._token is None else loop.call_later(TOKEN_INTERVAL, self._poll)
        self._signals = call.closing_signals()
        for signal in self._signals:
            signal.add_done_callback(self._closing)

    def stop(self, reason: StopReason) -> None:
        """Cancel the awaiting task once, for the first reason that stops the call."""
        if self._armed and self.reason is None and self.task is not None:
            self.reason = reason
            self.task.cancel()

    def _closing(self, _signal: asyncio.Future[None]) -> None:
        self.stop("closing")

    def _poll(self) -> None:
        if self._token is not None and self._token.cancelled:
            self.stop("token")
        else:
            self._poller = self._loop.call_later(TOKEN_INTERVAL, self._poll)

    def external(self) -> bool:
        """Return whether something else also requested the task's cancellation, however the work reacted to it."""
        return _cancelling(self.task) > self.level + (self.reason is not None)

    def disarm(self) -> bool:
        """Remove every timer and callback, and return whether this guard alone cancelled the task."""
        self._armed = False
        for handle in self._handles:
            handle.cancel()
        if self._poller is not None:
            self._poller.cancel()
        for signal in self._signals:
            signal.remove_done_callback(self._closing)
        return self.reason is not None and _uncancel(self.task) <= self.level


class LogicalCallContext:
    """Keep all state belonging to a call, through stream handoff and the release of its owned work."""

    __slots__ = (
        "_guard",
        "_interrupted",
        "_io_context",
        "_left",
        "_phase",
        "_scope",
        "_task",
        "auth_exchange_budget_used",
        "auth_exchange_count",
        "auth_refresh_ids",
        "auth_refresh_pending",
        "call_id",
        "deadline",
        "delivery_state",
        "finished",
        "network_send_budget_used",
        "network_send_count",
        "operation_id",
        "phase_caps",
        "redirect_count",
        "resource_attempt_count",
        "retry_blocked",
        "send_limit",
        "settings",
        "started",
        "streaming",
        "wire_send_count",
    )

    def __init__(self, settings: Settings, scope: _Scope, operation_id: str | None = None) -> None:
        """Bind effective settings and compute the minimum total and explicit monotonic deadline."""
        self.started = monotonic()
        self.call_id = str(uuid4())
        self.operation_id = operation_id
        self.settings = settings
        self._scope = scope
        deadline = settings.deadline
        if settings.total_timeout is not None:
            total_at = self.started + settings.total_timeout
            if deadline is None or total_at < deadline.at:
                deadline = absolute_deadline(total_at)
        self.deadline = deadline
        self.resource_attempt_count = 0
        self.redirect_count = 0
        self.auth_exchange_count = 0
        self.network_send_count = 0
        self.network_send_budget_used = 0
        self.send_limit = network_send_limit(settings)
        self.retry_blocked = False
        self.auth_exchange_budget_used = 0
        self.auth_refresh_ids: tuple[str, ...] = ()
        self.auth_refresh_pending = 0
        self.wire_send_count: int | None = None
        self.delivery_state = DeliveryState.NOT_SENT
        self.finished = False
        self.streaming = False
        self.phase_caps: tuple[PhaseCap, ...] = ()
        self._io_context: AttemptIOContext
        self._phase: DeadlinePhase = "unknown"
        self._guard: _Guard | None = None
        self._interrupted: BaseException | None = None
        self._left: list[asyncio.Task[None]] = []
        self._task: asyncio.Task[object] | None = None

    def remaining(self) -> float | None:
        """Return the current call or stream deadline's remaining seconds, never negative."""
        return None if self.deadline is None else self.deadline.remaining()

    def closing_signals(self) -> tuple[asyncio.Future[None], ...]:
        """Return the signals that complete once the call's client or view starts closing."""
        return self._scope.closing_signals()

    def snapshot_error(self, error: ErrorT) -> ErrorT:
        """Attach this call's identity and a readonly counter snapshot to an error before publication."""
        error.operation_id = self.operation_id
        error.call_id = self.call_id
        set_error_counters(
            error,
            resource_attempt_count=self.resource_attempt_count,
            redirect_count=self.redirect_count,
            auth_exchange_count=self.auth_exchange_count,
            network_send_count=self.network_send_count,
            network_send_budget_used=self.network_send_budget_used,
            auth_exchange_budget_used=self.auth_exchange_budget_used,
            auth_refresh_ids=self.auth_refresh_ids,
            auth_refresh_pending=self.auth_refresh_pending,
            wire_send_count=self.wire_send_count,
        )
        return error

    def _deadline_error(
        self, at: float, phase: DeadlinePhase, delivery: DeliveryState, cause: BaseException | None = None
    ) -> DeadlineExceededError:
        return self.snapshot_error(
            DeadlineExceededError(
                deadline_at=at,
                elapsed=monotonic() - self.started,
                delivery_state=delivery,
                phase=phase,
                cause=cause,
            )
        )

    def check(
        self,
        phase: DeadlinePhase = "unknown",
        delivery_state: DeliveryState | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Enforce token, closing, then deadline precedence at a cooperative SDK boundary."""
        if phase != "unknown":
            self._phase = phase
        else:
            phase = self._phase
        if (interrupted := self._interrupted) is not None:
            raise interrupted
        if (guard := self._guard) is not None and guard.external():
            raise self._native(None)
        delivery = self.delivery_state if delivery_state is None else delivery_state
        if (token := self.settings.cancel_token) is not None and token.cancelled:
            if isinstance(cause, RequestCancelledError):
                raise self.snapshot_error(cause)
            raise self.snapshot_error(
                RequestCancelledError(source="cancel_token", delivery_state=delivery, cause=cause)
            )
        if (error := self._scope.closing()) is not None:
            if isinstance(cause, ClientClosedError):
                raise self.snapshot_error(cause)
            error.cause = cause
            raise self.snapshot_error(error)
        if (deadline := self.deadline) is not None and monotonic() >= deadline.at:
            if isinstance(cause, DeadlineExceededError):
                raise self.snapshot_error(cause)
            raise self._deadline_error(deadline.at, phase, delivery, cause)

    def failure(
        self,
        error: BaseException,
        phase: DeadlinePhase = "unknown",
        delivery_state: DeliveryState | None = None,
    ) -> BaseException:
        """Preserve native interruption, otherwise select observed termination before the operation's failure.

        The guard's own cancellation becomes its stop error at once, so cleanup reports late failures on that error.
        """
        if (guard := self._stopper(error)) is not None:
            return self._stopped(guard, phase, delivery_state, None)
        if not isinstance(error, Exception):
            return error
        try:
            self.check(phase, delivery_state, error)
        except BaseException as failure:  # noqa: BLE001
            return failure
        return self.snapshot_error(error) if isinstance(error, SDKError) else error

    def admit_send(self, *, redirect: bool = False) -> None:
        """Atomically consume one nonrefundable send slot immediately before the adapter invocation."""
        with self._scope.lock:
            self.check("send")
            limit = self.send_limit
            if limit is not None and self.network_send_budget_used >= limit:
                raise self.snapshot_error(
                    BudgetExceededError(budget_kind="network", limit=limit, used=self.network_send_budget_used)
                )
            self.network_send_budget_used += 1
            if redirect:
                self.redirect_count += 1
            else:
                self.resource_attempt_count += 1
            self.network_send_count += 1
            self.delivery_state = DeliveryState.MAYBE_SENT

    def observe_send(self, trace: AttemptTrace) -> None:
        """Count resource header evidence once after the adapter invocation, before publishing its outcome."""
        if self.wire_send_count is not None and trace.headers_started:
            self.wire_send_count += 1

    def sleep_until(self, not_before: float) -> None:
        """Wait until a retry target, waking for client close and checking an explicit token when present."""
        while True:
            self.check("sleep")
            remaining = not_before - monotonic()
            if remaining <= 0:
                return
            if (deadline := self.deadline) is not None:
                remaining = min(remaining, deadline.remaining())
            if self.settings.cancel_token is not None:
                remaining = min(remaining, 0.05)
            self._scope.wait(remaining)

    async def asleep_until(self, not_before: float) -> None:
        """Wait inside the existing monitored task, retaining the call's original deadline and cancellation."""
        import asyncio  # noqa: PLC0415

        await self.bounded(lambda: asyncio.sleep(max(0.0, not_before - monotonic())), phase="sleep")

    def timeout(self) -> ResolvedTimeoutOptions:
        """Resolve each native phase and record whether its own cap, stream idle, or the deadline constrained it."""
        configured = self.settings.timeout
        remaining = self.remaining()
        read = self.settings.stream_read_timeout if self.streaming else configured.read
        idle = self.settings.stream_idle_timeout if self.streaming else None
        connect_cap = _cap(configured.connect, remaining, self.deadline)
        read_cap = _cap(read, remaining, self.deadline, idle)
        write_cap = _cap(configured.write, remaining, self.deadline)
        pool_cap = _cap(configured.pool, remaining, self.deadline)
        self.phase_caps = (connect_cap, read_cap, write_cap, pool_cap)
        return ResolvedTimeoutOptions(
            connect=connect_cap.effective,
            read=read_cap.effective,
            write=write_cap.effective,
            pool=pool_cap.effective,
        )

    def io_context(self, trace: AttemptTrace) -> AttemptIOContext:
        """Build the per-send context retained by adapters through stream handoff."""
        context = AttemptIOContext(
            self.timeout(), trace, deadline=self.deadline, cancel_token=self.settings.cancel_token
        )
        self._io_context = context
        return context

    def timeout_failure(self, error: BaseException, phase: IOPhase, delivery_state: DeliveryState) -> SDKError:
        """Classify a native timeout from the cap's recorded source, with ties always belonging to the deadline."""
        index = {"connect": 0, "read": 1, "write": 2, "pool": 3}.get(phase)
        if index is not None and self.phase_caps:
            cap = self.phase_caps[index]
            if cap.deadline_at is not None:
                return self._deadline_error(
                    cap.deadline_at,
                    "stream" if self.streaming else "send",
                    delivery_state,
                    error.cause if isinstance(error, PhaseTimeoutError) and error.cause is not None else error,
                )
            if isinstance(error, PhaseTimeoutError):
                error.retry_stop_reason = "transport_not_retryable" if self.streaming else None
                return self.snapshot_error(error)
            if cap.effective is not None and phase != "unknown":
                return self.snapshot_error(
                    PhaseTimeoutError(
                        effective_timeout=cap.effective,
                        phase=phase,
                        delivery_state=delivery_state,
                        retry_stop_reason="transport_not_retryable" if self.streaming else None,
                        cause=error,
                    )
                )
        if isinstance(error, PhaseTimeoutError):
            error.retry_stop_reason = "transport_not_retryable" if self.streaming else None
            return self.snapshot_error(error)
        return self.snapshot_error(TransportError(delivery_state=delivery_state, phase=phase, cause=error))

    def handoff(self) -> None:
        """Start stream lifetime limits and replace the completed acquisition's caps before the first body read."""
        self.streaming = True
        self.delivery_state = DeliveryState.RESPONSE_STARTED
        total = self.settings.stream_total_timeout
        self.deadline = None if total is None else absolute_deadline(monotonic() + total)
        timeout = self.timeout()
        set_io_timing(self._io_context, timeout, self.deadline)

    def finish(self) -> None:
        """Mark the call finished, so its remaining cleanup counts as a lease rather than a running call."""
        self.finished = True

    def _native(self, error: BaseException | None) -> BaseException:
        """Return the native cancellation that stops the call, even when the work suppressed or converted it."""
        import asyncio  # noqa: PLC0415

        if self._interrupted is None:
            self._interrupted = error if isinstance(error, asyncio.CancelledError) else asyncio.CancelledError()
        return self._interrupted

    def _owning(self) -> bool:
        """Return whether the current task runs the call, or its armed guard's work, so its interruption stops it.

        Another task that closes a handed-over stream keeps its own interruption.
        """
        import asyncio  # noqa: PLC0415

        return asyncio.current_task() is (self._task if (guard := self._guard) is None else guard.task)

    def _stopper(self, error: BaseException) -> _Guard | None:
        """Return the guard whose own request a cancellation is, rather than a native interruption."""
        import asyncio  # noqa: PLC0415

        guard = self._guard
        return (
            guard
            if isinstance(error, asyncio.CancelledError)
            and guard is not None
            and guard.reason is not None
            and guard.task is asyncio.current_task()
            and _cancelling(guard.task) <= guard.level + 1
            else None
        )

    def _stopped(
        self,
        guard: _Guard,
        phase: DeadlinePhase,
        delivery_state: DeliveryState | None,
        cause: BaseException | None,
    ) -> BaseException:
        """Return the one error of a call its guard stopped, selected when the stop is first observed."""
        if (stopped := guard.error) is None:
            stopped = guard.error = self._stop_error(guard, phase, delivery_state, cause)
        return stopped

    def _stop_error(
        self,
        guard: _Guard,
        phase: DeadlinePhase,
        delivery_state: DeliveryState | None,
        cause: BaseException | None,
    ) -> BaseException:
        """Return the error of a stopped call, even when a timer fired within the clock's resolution."""
        failure = cause if isinstance(cause, Exception) else None
        try:
            self.check(phase, delivery_state, failure)
        except BaseException as error:  # noqa: BLE001
            return error
        delivery = self.delivery_state if delivery_state is None else delivery_state
        if guard.reason == "idle" and (idle_timeout := guard.idle_timeout) is not None:
            return self.snapshot_error(
                PhaseTimeoutError(
                    effective_timeout=idle_timeout,
                    phase="read",
                    delivery_state=delivery,
                    retry_stop_reason="transport_not_retryable",
                    cause=failure,
                )
            )
        if isinstance(failure, DeadlineExceededError):
            return self.snapshot_error(failure)
        at = monotonic() if self.deadline is None else self.deadline.at
        return self._deadline_error(at, self._phase, delivery, failure)

    async def _settle_left(self, failure: BaseException) -> None:
        """Wait within the cleanup cap for the work interrupted callbacks left, retaining what is still running."""
        if left := self._left:
            self._left = []
            await self.cleanup(partial(_joined, tuple(left)), error=failure)

    async def _checked(
        self,
        result: T,
        phase: DeadlinePhase,
        delivery_state: DeliveryState | None,
        cleanup: Callable[[T], Awaitable[None]] | None,
    ) -> T:
        try:
            self.check(phase, delivery_state)
        except BaseException as error:
            if cleanup is not None:
                await self.cleanup(partial(cleanup, result), error=error)
            raise
        return result

    async def _nested(
        self,
        operation: Callable[[], Awaitable[T]],
        phase: DeadlinePhase,
        delivery_state: DeliveryState | None,
    ) -> T:
        try:
            result = await operation()
        except BaseException as error:  # noqa: BLE001
            raise self.failure(error, phase, delivery_state) from None
        self.check(phase, delivery_state)
        return result

    async def bounded(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        phase: DeadlinePhase = "unknown",
        delivery_state: DeliveryState | None = None,
        cleanup: Callable[[T], Awaitable[None]] | None = None,
        idle_timeout: float | None = None,
    ) -> T:
        """Await SDK work in the caller's task, cancelling it only when the call's token, closing, or limits stop it.

        Work nested in a guarded call runs directly under that guard. A result the stopped work still returned, such
        as a response whose close a callback delayed, is released through cleanup.
        """
        self.check(phase, delivery_state)
        if self._guard is not None and idle_timeout is None and not (self.streaming and phase == "stream"):
            return await self._nested(operation, phase, delivery_state)
        if self.streaming and phase == "stream" and idle_timeout is None:
            read, idle = self.settings.stream_read_timeout, self.settings.stream_idle_timeout
            idle_timeout = idle if read is None else read if idle is None else min(read, idle)
        guard = self._guard = _Guard(self, idle_timeout)
        if self._task is None:
            self._task = guard.task
        left = LEFT_WORK.set(self._left)
        try:
            result = await operation()
        except BaseException as error:  # noqa: BLE001
            import asyncio  # noqa: PLC0415

            LEFT_WORK.reset(left)
            self._guard = None
            if guard.external():
                guard.disarm()
                failure = self._native(error)
            elif guard.disarm() and isinstance(error, (Exception, asyncio.CancelledError)):
                failure = self._stopped(guard, phase, delivery_state, error)
            else:
                if not isinstance(error, Exception) and self._interrupted is None:
                    self._interrupted = error
                failure = self.failure(error, phase, delivery_state)
            await self._settle_left(failure)
            raise failure from None
        try:
            return await self._checked(result, phase, delivery_state, cleanup)
        finally:
            LEFT_WORK.reset(left)
            self._guard = None
            guard.disarm()

    async def cleanup(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        error: BaseException | None = None,
        wrap_errors: bool = True,
    ) -> bool:
        """Release owned work within the cleanup cap, retaining late work and preserving any primary failure."""
        import asyncio  # noqa: PLC0415

        task = asyncio.create_task(_released(operation))
        self._scope.retain_cleanup(task, error, owner=self)
        try:
            await asyncio.wait((task,), timeout=self.settings.cleanup_timeout)
        except BaseException as interrupted:  # noqa: BLE001
            primary = interrupted
            if (guard := self._stopper(interrupted)) is None:
                if not isinstance(interrupted, Exception) and self._interrupted is None and self._owning():
                    self._interrupted = interrupted
                task.add_done_callback(partial(_secondary, interrupted))
            elif error is None or isinstance(error, Exception):
                primary = self._stopped(guard, "cleanup", None, error)
                if primary is not error:
                    task.add_done_callback(partial(_secondary, primary))
            if error is not None and not isinstance(error, Exception):
                raise error from None
            raise primary from None
        if not task.done():
            failure = self.snapshot_error(CleanupError(pending_calls=1, timeout=self.settings.cleanup_timeout))
            if error is not None:
                add_secondary(error, failure)
                self.retry_blocked = True
                return False
            task.add_done_callback(partial(_secondary, failure))
            raise failure
        try:
            task_result(task)
        except BaseException as failure:
            if not isinstance(failure, Exception) and (error is None or isinstance(error, Exception)):
                interruption = self._interrupted
                if interruption is None:
                    interruption = self._interrupted = failure
                cleanup_secondary(interruption, failure)
                raise interruption from None
            if error is None:
                if not wrap_errors:
                    raise
                cleanup_error = failure if isinstance(failure, CleanupError) else CleanupError(cause=failure)
                raise self.snapshot_error(cleanup_error) from None
            self.retry_blocked = True
            return False
        return True
