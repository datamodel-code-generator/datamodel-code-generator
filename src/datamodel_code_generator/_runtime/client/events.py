"""The events one call's hooks observe, and how a failing hook ends the call.

Every event reaches every hook in order. A hook failure stops the call's further network actions: it raises as a
SDKError with the reason `hook_failed`, unless the call already failed, when the failure's notes name it.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .errors import (
    APIConnectionError,
    ConfigurationError,
    SDKError,
    add_secondary,
    is_hook_failure,
    is_transport,
    kept_primary,
)
from .hooks import CallEvent
from .native import io_phase

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .hooks import AsyncHook, CallOutcome, EventName, Hook, JSONScalar, RetryReason
    from .logical import LogicalCallContext
    from .options import Settings
    from .responses import ResponseInfo


def _interrupted(primary: BaseException | None, interruption: BaseException) -> BaseException:
    """Return the error a terminal hook's interruption leaves, which replaces an ordinary failure and keeps it."""
    return interruption if primary is None or primary is interruption else kept_primary(primary, interruption)


class CallEvents:
    """The hooks of one call and the facts its events share: its identifiers, path template, origin, and attempts."""

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
        "monotonic",
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
        self.info: ResponseInfo | None = None
        self.attempt: float | None = None
        self.handed: float | None = None
        self.started = call.started
        self.monotonic = call.monotonic
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
        failure: APIConnectionError | None = None,
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
            phase=None if failure is None else io_phase(failure),
            retry_reason=retry_reason,
            status=status,
            duration=duration,
            request_id=None if info is None else info.request_id,
            outcome=outcome,
            attempt_count=self.call.attempt_count,
            options=options,
            context=self.context,
        )

    def emit(self, event: CallEvent) -> None:
        """Pass an event to every hook, then raise the failure of those that raised."""
        if failures := self.notify(event):
            raise self.failed(failures)

    async def aemit(self, event: CallEvent) -> None:
        """Pass an event to every hook, awaiting the asynchronous ones, then raise the failure of those that raised."""
        if failures := await self.anotify(event):
            raise self.failed(failures)

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
            try:
                hook.on_event(event)
            except Exception as error:  # noqa: BLE001, PERF203 - Every hook sees the event, even after one fails.
                failures.append(error)
            except BaseException as error:
                if not terminal:
                    raise
                if interruption is None:
                    interruption = error
                else:
                    add_secondary(interruption, error)
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
                    raise self.failed(failures)

            try:
                await notify()
            except SDKError as hook_error:
                if not is_hook_failure(hook_error):
                    raise
                return failures
        else:
            try:
                await self._anotified(event, failures, terminal=False)
            except BaseException as interrupted:
                for failure in failures:
                    add_secondary(interrupted, failure)
                raise
        return failures

    async def _anotified(self, event: CallEvent, failures: list[Exception], *, terminal: bool) -> BaseException | None:
        """Deliver an event in its owner's task, preserving ordinary callback failures in order."""
        interruption: BaseException | None = None
        for hook in self.hooks:
            try:
                await _async_hook(hook, event)
            except Exception as error:  # noqa: BLE001, PERF203 - Every hook sees the event, even after one fails.
                failures.append(error)
            except BaseException as error:
                if not terminal:
                    raise
                if interruption is None:
                    interruption = error
                else:
                    add_secondary(interruption, error)
        return interruption

    def failed(self, failures: list[Exception]) -> SDKError:
        """Return the error of the hooks that failed on an event, naming any later failures in its notes."""
        error = self.call.snapshot_error(
            SDKError(reason="hook_failed", operation_id=self.operation_id, info=self.info, cause=failures[0])
        )
        add_secondary(error, *failures[1:])
        return error

    def starting(self, settings: Settings) -> CallEvent:
        """Return the call's first event, with a summary of its effective settings that holds no secret."""
        self.start_issued = True
        return self.event(
            "call_start",
            options=MappingProxyType({
                "max_response_bytes": settings.max_response_bytes,
                "max_error_body_bytes": settings.max_error_body_bytes,
                "max_stream_bytes": settings.max_stream_bytes,
                "total_timeout": settings.total_timeout,
            }),
        )

    def prepare(self, url: str, index: int) -> None:
        """Record the destination of a resource candidate and begin it."""
        parts = urlsplit(url)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.info = None
        self.attempts = index + 1
        self.attempt = self.monotonic()
        self.attempt_sent = False

    def attempting(self) -> CallEvent:
        """Report the prepared candidate immediately before its first send admission."""
        return self.event("attempt_start")

    def sending(self) -> None:
        """Count a send of the resource request, which the transport may have received from now on."""
        self.sends += 1
        self.sent = True
        self.attempt_sent = True

    def responding(self, info: ResponseInfo) -> CallEvent:
        """Keep the response whose headers arrived and return its event."""
        self.info = info
        return self.event("response_headers", sent=True, status=info.status_code)

    def _attempt_ending(self, error: BaseException | None) -> list[CallEvent]:
        """Close one prepared candidate after its response and permit have been released."""
        events: list[CallEvent] = []
        status = None if self.info is None else self.info.status_code
        if is_transport(error):
            events.append(self.event("transport_failure", sent=self.attempt_sent, failure=error))
        if self.attempt is not None:
            events.append(
                self.event(
                    "attempt_end", sent=self.attempt_sent, status=status, duration=self.monotonic() - self.attempt
                )
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
        outcome: CallOutcome = "cancel"
        match error:
            case None:
                outcome = "handed_off" if handed_off else "success"
            case Exception():
                outcome = "error"
            case _:
                pass
        events.append(
            self.event(
                "call_end", sent=self.sent, status=status, duration=self.monotonic() - self.started, outcome=outcome
            )
        )
        return events

    def ended(self, error: BaseException) -> None:
        """End a failed call, keeping its hooks' failures beside the error it raises."""
        self.finish(error=error)

    async def aended(self, error: BaseException, *, starting: bool = False) -> None:
        """End a failed asynchronous call, keeping its hooks' failures beside the error it raises."""
        await self._aterminal(error, starting=starting)

    def finish(
        self,
        *,
        handed_off: bool = False,
        error: BaseException | None = None,
        intermediate: bool = False,
    ) -> None:
        """End a call that succeeded, raising the first hook failure.

        A handle handed over this way is the caller's once no hook failed, and then reports its stream's end.
        """
        pending = self._attempt_ending(error) if intermediate else self.ending(error, handed_off=handed_off)
        if pending and not intermediate and not self.start_issued:
            pending.insert(0, self.starting(self.call.settings))
        primary, failed = self._notified_end(pending, error)
        if primary is not None and primary is not error:
            if failed is not None:
                for failure in failed:
                    add_secondary(primary, failure)
            raise primary
        if failed is not None:
            self.call.retry_blocked = True
            if error is None:
                raise self.failed(failed)
            for failure in failed:
                add_secondary(error, failure)
        if handed_off:
            self.handed = self.monotonic()

    def _notified_end(
        self, pending: list[CallEvent], error: BaseException | None
    ) -> tuple[BaseException | None, list[Exception] | None]:
        """Deliver every terminal event while retaining the first native interruption and all ordinary failures."""
        failed: list[Exception] | None = None
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
                    failed = failures
                else:
                    failed.extend(failures)
        return primary, failed

    async def afinish(
        self,
        *,
        handed_off: bool = False,
        error: BaseException | None = None,
        intermediate: bool = False,
    ) -> None:
        """End an asynchronous call that succeeded, raising the first hook failure.

        A handle handed over this way is the caller's once no hook failed, and then reports its stream's end.
        """
        await self._aterminal(error, handed_off=handed_off, intermediate=intermediate)
        if handed_off:
            self.handed = self.monotonic()

    async def _aterminal(
        self,
        error: BaseException | None,
        *,
        handed_off: bool = False,
        starting: bool = False,
        intermediate: bool = False,
    ) -> None:
        """Deliver terminal events in the caller task without shielding user callbacks."""
        events = self._attempt_ending(error) if intermediate else self.ending(error, handed_off=handed_off)
        if not events:
            return
        if not intermediate and (starting or not self.start_issued):
            events.insert(0, self.starting(self.call.settings))

        async def notify() -> None:
            primary = error
            failed: list[Exception] | None = None
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
                        failed = failures
                    else:
                        failed.extend(failures)
            if primary is not None and failed is not None:
                for failure in failed:
                    add_secondary(primary, failure)
            if primary is not error and primary is not None:
                raise primary
            if error is None and failed is not None:
                raise self.failed(failed)

        await notify()

    def _observed_end(self, event: CallEvent, primary: BaseException | None) -> tuple[CallEvent, BaseException | None]:
        """Report termination observed while earlier terminal hooks were running."""
        outcome = event.outcome
        if primary is not None:
            outcome = "cancel" if not isinstance(primary, Exception) else "error"
        return replace(event, outcome=outcome, duration=self.monotonic() - self.started), primary

    def stream_ending(self, error: BaseException | None, *, early: bool) -> CallEvent | None:
        """Return the end of a handed-over stream: read to its end, closed early, failed, or cancelled."""
        if (handed := self.handed) is None:
            return None
        self.handed = None
        outcome: CallOutcome = "cancel"
        match error:
            case None:
                outcome = "cancel" if early else "success"
            case Exception():
                outcome = "error"
            case _:
                pass
        status = None if self.info is None else self.info.status_code
        return self.event("stream_end", sent=True, status=status, duration=self.monotonic() - handed, outcome=outcome)

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
            raise self.failed(failures)
        for failure in failures:
            add_secondary(error, failure)


def call_events(
    settings: Settings, *, call: LogicalCallContext, path: str | None, asynchronous: bool
) -> CallEvents | None:
    """Return the events of a call, or None when no hook observes it; a synchronous client takes no async hook."""
    if not (hooks := settings.hooks):
        return None
    if not asynchronous and settings.async_hooks:
        raise ConfigurationError(field_path=("hooks",), reason="async_hook", operation_id=call.operation_id)
    return CallEvents(hooks, settings, call=call, path=path)


async def _async_hook(hook: Hook | AsyncHook, event: CallEvent) -> None:
    """Run one hook inside the task whose lifetime the call controls."""
    if inspect.isawaitable(result := hook.on_event(event)):
        await result
