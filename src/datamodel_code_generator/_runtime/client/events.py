"""The events one call's hooks observe, and how a failing hook ends the call.

Every event reaches every hook in order. A hook failure stops the call's further network actions: it raises as a
`HookExecutionError`, unless the call already failed, when it joins that failure's secondary errors.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from time import monotonic
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from ..model_codecs.unset import UNSET, Unset
from .errors import (
    CleanupError,
    ConfigurationError,
    DeliveryState,
    HookExecutionError,
    RequestCancelledError,
    TransportError,
    add_secondary,
)
from .hooks import CallEvent

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..model_codecs.wire import JSONScalar
    from .hooks import AsyncHook, CallOutcome, EventName, Hook, RetryReason
    from .logical import LogicalCallContext
    from .options import Settings
    from .responses import Response, ResponseInfo


def _interrupted(primary: BaseException | None, interruption: BaseException) -> BaseException:
    """Return the error a terminal hook's interruption leaves.

    An interruption such as KeyboardInterrupt replaces an ordinary failure and keeps it beside itself; a cancellation
    the hook raised itself stays beside the failure already propagating.
    """
    import asyncio  # noqa: PLC0415

    if primary is None:
        return interruption
    if isinstance(primary, Exception) and not isinstance(interruption, asyncio.CancelledError):
        add_secondary(interruption, primary)
        return interruption
    if primary is not interruption:
        add_secondary(primary, interruption)
    return primary


class CallEvents:
    """The hooks of one call and the facts its events share: its identifiers, path template, origin, and counters."""

    __slots__ = (
        "attempt",
        "attempt_sent",
        "attempts",
        "call",
        "call_id",
        "context",
        "delivery",
        "handed",
        "hooks",
        "info",
        "operation_id",
        "origin",
        "path",
        "sends",
        "sent",
        "start_issued",
        "started",
        "terminal",
    )

    def __init__(
        self,
        hooks: tuple[Hook | AsyncHook, ...],
        settings: Settings,
        *,
        call: LogicalCallContext,
        path: str | None,
    ) -> None:
        """Start observing a call; the origin it sends to comes once its request is prepared."""
        self.hooks = hooks
        self.context = settings.context
        self.call = call
        self.call_id = call.call_id
        self.operation_id = call.operation_id
        self.path = path
        self.origin: str | None = None
        self.attempts = 0
        self.attempt_sent = False
        self.start_issued = False
        self.sends = 0
        self.sent = False
        self.delivery = DeliveryState.NOT_SENT
        self.info: ResponseInfo | None = None
        self.attempt: float | None = None
        self.handed: float | None = None
        self.started = call.started
        self.terminal = False

    def event(  # noqa: PLR0913
        self,
        name: EventName,
        *,
        sent: bool = False,
        status: int | None = None,
        duration: float | None = None,
        outcome: CallOutcome | None = None,
        options: Mapping[str, JSONScalar] | None = None,
        failure: TransportError | None = None,
        retry_reason: RetryReason | None = None,
    ) -> CallEvent:
        """Return one event of this call with the facts it shares and the ones given."""
        info = self.info
        return CallEvent(
            name=name,
            call_id=self.call_id,
            operation_id=self.operation_id,
            path=self.path,
            origin=self.origin,
            parent_session_id=self.call.parent_session_id,
            attempt_index=None if self.attempts == 0 else self.attempts - 1,
            sent=sent,
            phase=None if failure is None else failure.phase,
            retry_reason=retry_reason,
            status=status,
            duration=duration,
            request_id=None if info is None else info.request_id,
            outcome=outcome,
            attempts=self.call.resource_attempt_count,
            sends=self.call.network_send_count,
            resource_attempt_count=self.call.resource_attempt_count,
            redirect_count=self.call.redirect_count,
            auth_exchange_count=self.call.auth_exchange_count,
            network_send_count=self.call.network_send_count,
            network_send_budget_used=self.call.network_send_budget_used,
            auth_exchange_budget_used=self.call.auth_exchange_budget_used,
            auth_refresh_ids=self.call.auth_refresh_ids,
            auth_refresh_pending=self.call.auth_refresh_pending,
            options=options,
            context=self.context,
        )

    def emit(self, event: CallEvent) -> None:
        """Pass an event to every hook, then raise the failure of those that raised."""
        if failures := self.notify(event):
            raise self.failed(event.name, failures)

    async def aemit(self, event: CallEvent) -> None:
        """Pass an event to every hook, awaiting the asynchronous ones, then raise the failure of those that raised."""
        if failures := await self.anotify(event):
            raise self.failed(event.name, failures)

    def notify(
        self, event: CallEvent, *, terminal: bool = False, error: BaseException | None = None
    ) -> list[Exception]:
        """Pass an event to every hook, preserving an existing failure during terminal notification."""
        failures: list[Exception] = []
        try:
            interrupted = self._notified(event, failures, terminal=terminal)
        except BaseException as interruption:
            for failure in failures:
                add_secondary(interruption, failure)
            raise
        if interrupted is not None and (error is None or _interrupted(error, interrupted) is not error):
            for failure in failures:
                add_secondary(interrupted, failure)
            raise interrupted
        return failures

    def _notified(self, event: CallEvent, failures: list[Exception], *, terminal: bool) -> BaseException | None:
        """Stop an ordinary event at a native interruption, but drain terminal hooks and return the interruption."""
        interruption: BaseException | None = None
        for hook in self.hooks:
            if not terminal:
                self.call.check()
            try:
                hook.on_event(event)
            except Exception as error:  # noqa: BLE001 - Every hook sees the event, even after one fails.
                failures.append(error)
            except BaseException as error:
                if not terminal:
                    raise
                if interruption is None:
                    interruption = error
                else:
                    add_secondary(interruption, error)
            if not terminal:
                self.call.check()
        return interruption

    async def anotify(
        self, event: CallEvent, *, terminal: bool = False, error: BaseException | None = None
    ) -> list[Exception]:
        """Pass an event to every hook, awaiting the asynchronous ones, returning the failures of those that raised."""
        failures: list[Exception] = []
        if terminal:

            async def notify() -> None:
                if (interrupted := await self._anotified(event, failures, terminal=True)) is not None and (
                    error is None or _interrupted(error, interrupted) is not error
                ):
                    for failure in failures:
                        add_secondary(interrupted, failure)
                    raise interrupted
                if failures:
                    raise self.failed(event.name, failures)

            try:
                await self.call.cleanup(notify, wrap_errors=False)
            except HookExecutionError:
                return failures
            except CleanupError as cleanup_error:
                return [*failures, cleanup_error]
        else:
            try:
                await self.call.bounded(lambda: self._anotified(event, failures, terminal=False))
            except BaseException as interrupted:
                for failure in failures:
                    add_secondary(interrupted, failure)
                raise
        return failures

    async def _anotified(self, event: CallEvent, failures: list[Exception], *, terminal: bool) -> BaseException | None:
        """Deliver an event in its owner's task, preserving ordinary callback failures in order."""
        interruption: BaseException | None = None
        for hook in self.hooks:
            if not terminal:
                self.call.check()
            try:
                await _async_hook(hook, event)
            except Exception as error:  # noqa: BLE001 - Every hook sees the event, even after one fails.
                failures.append(error)
            except BaseException as error:
                if not terminal:
                    raise
                if interruption is None:
                    interruption = error
                else:
                    add_secondary(interruption, error)
            if not terminal:
                self.call.check()
        return interruption

    def failed(
        self, name: EventName, failures: list[Exception], completed: Response[object] | Unset = UNSET
    ) -> HookExecutionError[object]:
        """Return the error of the hooks that failed on an event, keeping a success the call completed."""
        return self.call.snapshot_error(
            HookExecutionError(
                event_name=name,
                sent=self.sent,
                delivery_state=self.delivery,
                completed_result=completed,
                operation_id=self.operation_id,
                call_id=self.call_id,
                info=self.info,
                cause=failures[0],
                secondary_errors=tuple(failures[1:]),
            )
        )

    def starting(self, settings: Settings) -> CallEvent:
        """Return the call's first event, with a summary of its effective settings that holds no secret."""
        self.start_issued = True
        return self.event(
            "call_start",
            options=MappingProxyType({
                "max_response_bytes": settings.max_response_bytes,
                "max_error_body_bytes": settings.max_error_body_bytes,
                "max_stream_bytes": settings.max_stream_bytes,
                "cleanup_timeout": settings.cleanup_timeout,
                "total_timeout": settings.total_timeout,
                "max_network_sends": self.call.send_limit,
                "stream_idle_timeout": settings.stream_idle_timeout,
                "stream_total_timeout": settings.stream_total_timeout,
            }),
        )

    def prepare(self, url: str, index: int | None) -> None:
        """Record a destination, beginning its resource candidate only when an index is supplied."""
        parts = urlsplit(url)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.info = None
        self.delivery = DeliveryState.NOT_SENT
        if index is not None:
            self.attempts = index + 1
            self.attempt = monotonic()
            self.attempt_sent = False

    def attempting(self) -> CallEvent:
        """Report the prepared candidate immediately before its first send admission."""
        return self.event("attempt_start")

    def sending(self) -> None:
        """Count a send of the resource request, which the transport may have received from now on."""
        self.sends += 1
        self.sent = True
        self.attempt_sent = True
        self.delivery = DeliveryState.MAYBE_SENT

    def responding(self, info: ResponseInfo) -> CallEvent:
        """Keep the response whose headers arrived and return its event."""
        self.info = info
        self.delivery = DeliveryState.RESPONSE_STARTED
        return self.event("response_headers", sent=True, status=info.status_code)

    def _attempt_ending(self, error: BaseException | None) -> list[CallEvent]:
        """Close one prepared candidate after its response and permit have been released."""
        events: list[CallEvent] = []
        status = None if self.info is None else self.info.status_code
        if isinstance(error, TransportError):
            events.append(self.event("transport_failure", sent=self.attempt_sent, failure=error))
        if self.attempt is not None:
            events.append(
                self.event("attempt_end", sent=self.attempt_sent, status=status, duration=monotonic() - self.attempt)
            )
            self.attempt = None
        return events

    def ending(self, error: BaseException | None, *, handed_off: bool = False) -> list[CallEvent]:
        """Return the events that end the open candidate and the logical call."""
        if self.terminal:
            return []
        self.terminal = True
        events = self._attempt_ending(error)
        status = None if self.info is None else self.info.status_code
        match error:
            case None:
                outcome: CallOutcome = "handed_off" if handed_off else "success"
            case RequestCancelledError():
                outcome = "cancel"
            case Exception():
                outcome = "error"
            case _:
                outcome = "cancel"
        events.append(
            self.event("call_end", sent=self.sent, status=status, duration=monotonic() - self.started, outcome=outcome)
        )
        return events

    def ended(self, error: BaseException) -> None:
        """End a failed call, keeping its hooks' failures beside the error it raises."""
        self.finish(UNSET, error=error)

    async def aended(self, error: BaseException, *, starting: bool = False) -> None:
        """End a failed asynchronous call, keeping its hooks' failures beside the error it raises."""
        await self._aterminal(error, starting=starting)

    def finish(
        self,
        completed: Response[object] | Unset,
        *,
        handed_off: bool = False,
        error: BaseException | None = None,
        intermediate: bool = False,
    ) -> None:
        """End a call that succeeded, raising the first hook failure with the success it completed.

        A handle handed over this way is the caller's once no hook failed, and then reports its stream's end.
        """
        pending = self._attempt_ending(error) if intermediate else self.ending(error, handed_off=handed_off)
        if pending and not intermediate and not self.start_issued:
            pending.insert(0, self.starting(self.call.settings))
        primary, failed = self._notified_end(pending, error)
        if primary is not None and primary is not error:
            if failed is not None:
                for failure in failed[1]:
                    add_secondary(primary, failure)
            raise primary
        if failed is not None:
            self.call.retry_blocked = True
            if error is None:
                raise self.failed(*failed, completed)
            for failure in failed[1]:
                add_secondary(error, failure)
        if handed_off:
            self.handed = monotonic()

    def _notified_end(
        self, pending: list[CallEvent], error: BaseException | None
    ) -> tuple[BaseException | None, tuple[EventName, list[Exception]] | None]:
        """Deliver every terminal event while retaining the first native interruption and all ordinary failures."""
        failed: tuple[EventName, list[Exception]] | None = None
        primary = error
        for pending_event in pending:
            event = pending_event
            if event.name == "call_end":
                event, primary = self._observed_end(event, primary)
            failures: list[Exception] = []
            if (interrupted := self._notified(event, failures, terminal=True)) is not None:
                self.call.retry_blocked = True
                primary = _interrupted(primary, interrupted)
            if failures:
                if failed is None:
                    failed = (event.name, failures)
                else:
                    failed[1].extend(failures)
        return primary, failed

    async def afinish(
        self,
        completed: Response[object] | Unset,
        *,
        handed_off: bool = False,
        error: BaseException | None = None,
        intermediate: bool = False,
    ) -> None:
        """End an asynchronous call that succeeded, raising the first hook failure with the success it completed.

        A handle handed over this way is the caller's once no hook failed, and then reports its stream's end.
        """
        await self._aterminal(error, completed, handed_off=handed_off, intermediate=intermediate)
        if handed_off:
            self.handed = monotonic()

    async def _aterminal(
        self,
        error: BaseException | None,
        completed: Response[object] | Unset = UNSET,
        *,
        handed_off: bool = False,
        starting: bool = False,
        intermediate: bool = False,
    ) -> None:
        """Retain the entire terminal event sequence if its waiting caller is interrupted."""
        events = self._attempt_ending(error) if intermediate else self.ending(error, handed_off=handed_off)
        if not events:
            return
        if not intermediate and (starting or not self.start_issued):
            events.insert(0, self.starting(self.call.settings))

        async def notify() -> None:
            primary = error
            failed: tuple[EventName, list[Exception]] | None = None
            for pending in events:
                event = pending
                if pending.name == "call_end":
                    event, primary = self._observed_end(pending, primary)
                failures: list[Exception] = []
                if (interrupted := await self._anotified(event, failures, terminal=True)) is not None:
                    self.call.retry_blocked = True
                    primary = _interrupted(primary, interrupted)
                if failures:
                    self.call.retry_blocked = True
                    if failed is None:
                        failed = (event.name, failures)
                    else:
                        failed[1].extend(failures)
            if primary is not None and failed is not None:
                for failure in failed[1]:
                    add_secondary(primary, failure)
            if primary is not error and primary is not None:
                raise primary
            if error is None and failed is not None:
                raise self.failed(*failed, completed)

        await self.call.cleanup(notify, error=error, wrap_errors=False)

    def _observed_end(self, event: CallEvent, primary: BaseException | None) -> tuple[CallEvent, BaseException | None]:
        """Report termination observed while earlier terminal hooks were running."""
        try:
            self.call.check()
        except BaseException as stopped:  # noqa: BLE001
            primary = stopped if primary is None else self.call.failure(primary)
        outcome = event.outcome
        if primary is not None:
            outcome = (
                "cancel"
                if isinstance(primary, RequestCancelledError) or not isinstance(primary, Exception)
                else "error"
            )
        return replace(event, outcome=outcome, duration=monotonic() - self.started), primary

    def stream_ending(self, error: BaseException | None, *, early: bool) -> CallEvent | None:
        """Return the end of a handed-over stream: read to its end, closed early, failed, or cancelled."""
        if (handed := self.handed) is None:
            return None
        self.handed = None
        match error:
            case None:
                outcome: CallOutcome = "cancel" if early else "success"
            case RequestCancelledError():
                outcome = "cancel"
            case Exception():
                outcome = "error"
            case _:
                outcome = "cancel"
        status = None if self.info is None else self.info.status_code
        return self.event("stream_end", sent=True, status=status, duration=monotonic() - handed, outcome=outcome)

    def streamed(self, error: BaseException | None, *, early: bool) -> None:
        """Report a handed-over stream's end, raising a hook failure unless the stream already failed."""
        if (event := self.stream_ending(error, early=early)) is not None and (
            failures := self.notify(event, terminal=True, error=error)
        ):
            self._stream_failed(error, failures)

    async def astreamed(self, error: BaseException | None, *, early: bool) -> None:
        """Report a handed-over asynchronous stream's end, raising a hook failure unless the stream already failed."""
        if (event := self.stream_ending(error, early=early)) is not None and (
            failures := await self.anotify(event, terminal=True, error=error)
        ):
            self._stream_failed(error, failures)

    def _stream_failed(self, error: BaseException | None, failures: list[Exception]) -> None:
        """Raise the failure of the hooks that failed on a stream's end, or keep it beside the stream's own failure."""
        if error is None:
            name: EventName = "stream_end"
            raise self.failed(name, failures)
        for failure in failures:
            add_secondary(error, failure)


def auth_ended(events: CallEvents, started: float, error: BaseException | None = None) -> None:
    """Complete actual credential work while retaining an existing callback or termination failure."""
    event = events.event("auth_end", sent=events.sent, duration=monotonic() - started)
    if error is None:
        events.emit(event)
    elif failures := events.notify(event, terminal=True, error=error):
        add_secondary(error, events.failed(event.name, failures))


async def aauth_ended(events: CallEvents, started: float, error: BaseException | None = None) -> None:
    """Complete an asynchronous credential span without losing its primary failure."""
    event = events.event("auth_end", sent=events.sent, duration=monotonic() - started)
    if error is None:
        await events.aemit(event)
    elif failures := await events.anotify(event, terminal=True, error=error):
        add_secondary(error, events.failed(event.name, failures))


def call_events(
    settings: Settings, *, call: LogicalCallContext, path: str | None, asynchronous: bool
) -> CallEvents | None:
    """Return the events of a call, or None when no hook observes it; a synchronous client takes no async hook."""
    if not (hooks := settings.hooks):
        return None
    if not asynchronous and settings.async_hooks:
        raise ConfigurationError(
            field_path=("hooks",), condition="async_hook", operation_id=call.operation_id, call_id=call.call_id
        )
    return CallEvents(hooks, settings, call=call, path=path)


async def _async_hook(hook: Hook | AsyncHook, event: CallEvent) -> None:
    """Run one hook inside the task whose lifetime the call controls."""
    if inspect.isawaitable(result := hook.on_event(event)):
        await result
