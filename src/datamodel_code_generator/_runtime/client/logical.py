"""One call's identity, admission counters, time limits, and owned asynchronous work."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, Literal, Protocol, TypeVar
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
    transfer_secondary,
)
from .lifecycle import TaskInterruptionError, cleanup_secondary, task_failure, task_result
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


class _Scope(Protocol):
    lock: LockType

    def closing(self) -> ClientClosedError | None: ...

    def closing_signals(self) -> tuple[asyncio.Future[None], ...]: ...

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


async def _late_result(
    task: asyncio.Task[T], cleanup: Callable[[T], Awaitable[None]] | None, error: BaseException
) -> None:
    try:
        result = await task
    except BaseException as failure:  # noqa: BLE001
        if isinstance(failure, TaskInterruptionError):
            failure = failure.cause
        if failure is error:
            return
        if isinstance(failure, (RequestCancelledError, ClientClosedError, DeadlineExceededError)) and isinstance(
            error, (RequestCancelledError, ClientClosedError, DeadlineExceededError)
        ):
            for secondary in failure.secondary_errors:
                cleanup_secondary(error, secondary)
            if failure.cause is not None and not isinstance(
                failure.cause, (RequestCancelledError, ClientClosedError, DeadlineExceededError)
            ):
                add_secondary(error, failure.cause)
        elif not task.cancelled():
            add_secondary(error, failure)
        return
    if cleanup is not None:
        await cleanup(result)


class LogicalCallContext:
    """Keep all state belonging to a call, through stream handoff and the release of its owned work."""

    __slots__ = (
        "_active_task",
        "_deadline_signal",
        "_interrupted",
        "_io_context",
        "_owned_cancellation",
        "_phase",
        "_scope",
        "_timer",
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
        self._deadline_signal: asyncio.Future[None] | None = None
        self._active_task: asyncio.Task[object] | None = None
        self._owned_cancellation: tuple[asyncio.Task[object], BaseException] | None = None
        self._interrupted: BaseException | None = None
        self._timer: asyncio.TimerHandle | None = None

    def remaining(self) -> float | None:
        """Return the current call or stream deadline's remaining seconds, never negative."""
        return None if self.deadline is None else self.deadline.remaining()

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
        """Preserve native interruption, otherwise select observed termination before the operation's failure."""
        error = self._interruption(error)
        if not isinstance(error, Exception):
            return error
        try:
            self.check(phase, delivery_state, error)
        except BaseException as failure:  # noqa: BLE001
            return failure
        return self.snapshot_error(error) if isinstance(error, SDKError) else error

    def admit_send(self) -> None:
        """Atomically consume one nonrefundable send slot immediately before the adapter invocation."""
        with self._scope.lock:
            self.check("send")
            limit = self.settings.max_network_sends
            if limit is not None and self.network_send_budget_used >= limit:
                raise self.snapshot_error(
                    BudgetExceededError(budget_kind="network", limit=limit, used=self.network_send_budget_used)
                )
            self.network_send_budget_used += 1
            self.resource_attempt_count += 1
            self.network_send_count += 1
            self.delivery_state = DeliveryState.MAYBE_SENT

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
                    cap.deadline_at, "stream" if self.streaming else "send", delivery_state, error
                )
            if cap.effective is not None and phase != "unknown":
                return self.snapshot_error(
                    PhaseTimeoutError(
                        effective_timeout=cap.effective,
                        phase=phase,
                        delivery_state=delivery_state,
                        retry_stop_reason="transport_not_retryable",
                        cause=error,
                    )
                )
        return self.snapshot_error(TransportError(delivery_state=delivery_state, phase=phase, cause=error))

    def handoff(self) -> None:
        """Start stream lifetime limits and replace the completed acquisition's caps before the first body read."""
        self._disarm()
        self.streaming = True
        self.delivery_state = DeliveryState.RESPONSE_STARTED
        total = self.settings.stream_total_timeout
        self.deadline = None if total is None else absolute_deadline(monotonic() + total)
        timeout = self.timeout()
        set_io_timing(self._io_context, timeout, self.deadline)

    def finish(self) -> None:
        """Disarm the call's single lazy deadline timer on every terminal path."""
        self.finished = True
        self._disarm()

    def _disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._deadline_signal = None

    def _signals(self) -> tuple[asyncio.Future[None], ...]:
        signals = self._scope.closing_signals()
        if (deadline := self.deadline) is None:
            return signals
        if self._deadline_signal is None:
            import asyncio  # noqa: PLC0415

            loop = asyncio.get_running_loop()
            self._deadline_signal = loop.create_future()
            self._timer = loop.call_at(loop.time() + deadline.remaining(), self._deadline_signal.set_result, None)
        return (*signals, self._deadline_signal)

    def _check_idle(self, idle_at: float | None, timeout: float | None, delivery_state: DeliveryState | None) -> None:
        if idle_at is not None and monotonic() >= idle_at and timeout is not None:
            raise self.snapshot_error(
                PhaseTimeoutError(
                    effective_timeout=timeout,
                    phase="read",
                    delivery_state=self.delivery_state if delivery_state is None else delivery_state,
                    retry_stop_reason="transport_not_retryable",
                )
            )

    async def _invoked(
        self, operation: Callable[[], Awaitable[T]], phase: DeadlinePhase, delivery_state: DeliveryState | None
    ) -> T:
        import asyncio  # noqa: PLC0415

        previous, self._active_task = self._active_task, asyncio.current_task()
        try:
            self.check(phase, delivery_state)
            return await operation()
        except BaseException as error:
            error = self._interruption(error)
            if isinstance(error, Exception):
                raise error from None
            raise TaskInterruptionError(error) from None
        finally:
            self._active_task = previous

    async def _nested(
        self,
        operation: Callable[[], Awaitable[T]],
        phase: DeadlinePhase,
        delivery_state: DeliveryState | None,
        cleanup: Callable[[T], Awaitable[None]] | None,
    ) -> T:
        try:
            result = await operation()
        except BaseException as error:  # noqa: BLE001
            raise self.failure(error, phase, delivery_state) from None
        try:
            self.check(phase, delivery_state)
        except BaseException as error:
            if cleanup is not None:
                await self.cleanup(partial(cleanup, result), error=error)
            raise
        return result

    def _interruption(self, error: BaseException) -> BaseException:
        """Distinguish cancellation of an owned operation from native cancellation delivered to its caller."""
        if self._owned_cancellation is not None:
            import asyncio  # noqa: PLC0415

            task, failure = self._owned_cancellation
            if isinstance(error, asyncio.CancelledError) and task is asyncio.current_task():
                transfer_secondary(error, failure)
                return failure
        return error

    def _cancel(self, task: asyncio.Task[object], error: BaseException) -> None:
        """Cancel the call's sole monitored task; the first cancellation always terminates this logical call."""
        if task.cancel():
            self._owned_cancellation = (task, error)
            task.add_done_callback(self._cancellation_finished)

    def _cancellation_finished(self, _task: asyncio.Task[object]) -> None:
        self._owned_cancellation = None

    async def bounded(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        phase: DeadlinePhase = "unknown",
        delivery_state: DeliveryState | None = None,
        cleanup: Callable[[T], Awaitable[None]] | None = None,
        idle_timeout: float | None = None,
    ) -> T:
        """Wait on SDK-owned work, using one call timer and token polling only when a token is present."""
        import asyncio  # noqa: PLC0415

        self.check(phase, delivery_state)
        if (
            self._active_task is not None
            and self._active_task is asyncio.current_task()
            and idle_timeout is None
            and not (self.streaming and phase == "stream")
        ):
            return await self._nested(operation, phase, delivery_state, cleanup)
        if self.streaming and phase == "stream" and idle_timeout is None:
            read, idle = self.settings.stream_read_timeout, self.settings.stream_idle_timeout
            idle_timeout = idle if read is None else read if idle is None else min(read, idle)
        signals = self._signals()
        task = asyncio.create_task(self._invoked(operation, phase, delivery_state))
        waiters: tuple[asyncio.Task[T] | asyncio.Future[None], ...] = (task, *signals)
        idle_at = None if idle_timeout is None else monotonic() + idle_timeout
        try:
            while True:
                timeout = None if idle_at is None else max(0.0, idle_at - monotonic())
                if self.settings.cancel_token is not None:
                    timeout = 0.05 if timeout is None else min(0.05, timeout)
                await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if task.done() and (
                    task.cancelled()
                    or ((failure := task_failure(task)) is not None and not isinstance(failure, Exception))
                ):
                    task_result(task)
                self.check(phase, delivery_state)
                self._check_idle(idle_at, idle_timeout, delivery_state)
                if task.done():
                    try:
                        return task_result(task)
                    except Exception as error:  # noqa: BLE001
                        raise self.failure(error, phase, delivery_state) from None
        except BaseException as error:  # noqa: BLE001
            error = self._interruption(error)
            if not isinstance(error, Exception) and self._interrupted is None:
                self._interrupted = error
            if task.done() and cleanup is None:
                await _late_result(task, None, error)
            else:
                self._cancel(task, error)
                await self.cleanup(partial(_late_result, task, cleanup, error), error=error)
            raise self.failure(error, phase, delivery_state) from None

    async def cleanup(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        error: BaseException | None = None,
        wrap_errors: bool = True,
    ) -> None:
        """Release owned work within the cleanup cap, retaining late work and preserving any primary failure."""
        import asyncio  # noqa: PLC0415

        task = asyncio.create_task(_released(operation))
        self._scope.retain_cleanup(task, error, owner=self)
        try:
            await asyncio.wait((task,), timeout=self.settings.cleanup_timeout)
        except BaseException as interrupted:
            interrupted = self._interruption(interrupted)
            if not isinstance(interrupted, Exception) and self._interrupted is None:
                self._interrupted = interrupted
            task.add_done_callback(partial(_secondary, interrupted))
            if error is not None and not isinstance(error, Exception):
                raise error from None
            raise interrupted from None
        if not task.done():
            failure = self.snapshot_error(CleanupError(pending_calls=1, timeout=self.settings.cleanup_timeout))
            if error is not None:
                add_secondary(error, failure)
                return
            task.add_done_callback(partial(_secondary, failure))
            raise failure
        try:
            task_result(task)
        except BaseException as failure:
            if error is None:
                if not wrap_errors or not isinstance(failure, Exception):
                    raise
                cleanup_error = failure if isinstance(failure, CleanupError) else CleanupError(cause=failure)
                raise self.snapshot_error(cleanup_error) from None
