"""The events one call's hooks observe, and how a failing hook ends the call.

Every event reaches every hook in order. A hook failure stops the call's further network actions: it raises as a
`HookExecutionError`, unless the call already failed, when it joins that failure's secondary errors.
"""

from __future__ import annotations

import inspect
from time import monotonic
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from ..model_codecs.unset import UNSET, Unset
from .errors import ConfigurationError, DeliveryState, HookExecutionError, TransportError, add_secondary
from .hooks import CallEvent

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..model_codecs.wire import JSONScalar
    from .hooks import AsyncHook, CallOutcome, EventName, Hook
    from .options import Settings
    from .responses import Response, ResponseInfo


class CallEvents:
    """The hooks of one call and the facts its events share: its identifiers, path template, origin, and counters."""

    __slots__ = (
        "attempt",
        "attempts",
        "call_id",
        "context",
        "delivery",
        "hooks",
        "info",
        "operation_id",
        "origin",
        "path",
        "sends",
        "sent",
        "started",
    )

    def __init__(
        self,
        hooks: tuple[Hook | AsyncHook, ...],
        settings: Settings,
        *,
        call_id: str,
        operation_id: str | None,
        path: str | None,
    ) -> None:
        """Start observing a call; the origin it sends to comes once its request is prepared."""
        self.hooks = hooks
        self.context = settings.context
        self.call_id = call_id
        self.operation_id = operation_id
        self.path = path
        self.origin: str | None = None
        self.attempts = 0
        self.sends = 0
        self.sent = False
        self.delivery = DeliveryState.NOT_SENT
        self.info: ResponseInfo | None = None
        self.attempt: float | None = None
        self.started = monotonic()

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
    ) -> CallEvent:
        """Return one event of this call with the facts it shares and the ones given."""
        info = self.info
        return CallEvent(
            name=name,
            call_id=self.call_id,
            operation_id=self.operation_id,
            path=self.path,
            origin=self.origin,
            attempt_index=None if self.attempts == 0 else self.attempts - 1,
            sent=sent,
            phase=None if failure is None else failure.phase,
            status=status,
            duration=duration,
            request_id=None if info is None else info.request_id,
            outcome=outcome,
            attempts=self.attempts,
            sends=self.sends,
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

    def notify(self, event: CallEvent) -> list[Exception]:
        """Pass an event to every hook, returning the failures of those that raised."""
        failures: list[Exception] = []
        for hook in self.hooks:
            try:
                hook.on_event(event)
            except Exception as error:  # noqa: BLE001, PERF203 - Every hook sees the event, even after one fails.
                failures.append(error)
        return failures

    async def anotify(self, event: CallEvent) -> list[Exception]:
        """Pass an event to every hook, awaiting the asynchronous ones, returning the failures of those that raised."""
        failures: list[Exception] = []
        for hook in self.hooks:
            try:
                if inspect.isawaitable(result := hook.on_event(event)):
                    await result
            except Exception as error:  # noqa: BLE001, PERF203 - Every hook sees the event, even after one fails.
                failures.append(error)
        return failures

    def failed(
        self, name: EventName, failures: list[Exception], completed: Response[object] | Unset = UNSET
    ) -> HookExecutionError[object]:
        """Return the error of the hooks that failed on an event, keeping a success the call completed."""
        return HookExecutionError(
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

    def starting(self, settings: Settings) -> CallEvent:
        """Return the call's first event, with a summary of its effective settings that holds no secret."""
        return self.event(
            "call_start",
            options=MappingProxyType({
                "max_response_bytes": settings.max_response_bytes,
                "max_error_body_bytes": settings.max_error_body_bytes,
                "max_stream_bytes": settings.max_stream_bytes,
                "cleanup_timeout": settings.cleanup_timeout,
            }),
        )

    def attempting(self, url: str) -> CallEvent:
        """Open the call's next attempt to a URL, keeping only its origin, and return its event, which sent nothing."""
        parts = urlsplit(url)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.attempts += 1
        self.attempt = monotonic()
        return self.event("attempt_start")

    def sending(self) -> None:
        """Count a send of the resource request, which the transport may have received from now on."""
        self.sends += 1
        self.sent = True
        self.delivery = DeliveryState.MAYBE_SENT

    def responding(self, info: ResponseInfo) -> CallEvent:
        """Keep the response whose headers arrived and return its event."""
        self.info = info
        self.delivery = DeliveryState.RESPONSE_STARTED
        return self.event("response_headers", sent=True, status=info.status_code)

    def ending(self, error: BaseException | None, *, handed_off: bool = False) -> list[CallEvent]:
        """Return the events that end the call: a transport failure's, its open attempt's, and the call's own."""
        events: list[CallEvent] = []
        status = None if self.info is None else self.info.status_code
        if isinstance(error, TransportError):
            events.append(self.event("transport_failure", sent=self.sent, failure=error))
        if self.attempt is not None:
            events.append(self.event("attempt_end", sent=self.sent, status=status, duration=monotonic() - self.attempt))
            self.attempt = None
        match error:
            case None:
                outcome: CallOutcome = "handed_off" if handed_off else "success"
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
        for event in self.ending(error):
            for failure in self.notify(event):
                add_secondary(error, failure)

    async def aended(self, error: BaseException) -> None:
        """End a failed asynchronous call, keeping its hooks' failures beside the error it raises."""
        for event in self.ending(error):
            for failure in await self.anotify(event):
                add_secondary(error, failure)

    def finish(self, completed: Response[object] | Unset, *, handed_off: bool = False) -> None:
        """End a call that succeeded, raising the first hook failure with the success it completed."""
        failed: tuple[EventName, list[Exception]] | None = None
        for event in self.ending(None, handed_off=handed_off):
            if (failures := self.notify(event)) and failed is None:
                failed = (event.name, failures)
            elif failures and failed is not None:
                failed[1].extend(failures)
        if failed is not None:
            raise self.failed(*failed, completed)

    async def afinish(self, completed: Response[object] | Unset, *, handed_off: bool = False) -> None:
        """End an asynchronous call that succeeded, raising the first hook failure with the success it completed."""
        failed: tuple[EventName, list[Exception]] | None = None
        for event in self.ending(None, handed_off=handed_off):
            if (failures := await self.anotify(event)) and failed is None:
                failed = (event.name, failures)
            elif failures and failed is not None:
                failed[1].extend(failures)
        if failed is not None:
            raise self.failed(*failed, completed)


def call_events(
    settings: Settings, *, call_id: str, operation_id: str | None, path: str | None, asynchronous: bool
) -> CallEvents | None:
    """Return the events of a call, or None when no hook observes it; a synchronous client takes no async hook."""
    if not (hooks := settings.hooks):
        return None
    if not asynchronous and settings.async_hooks:
        raise ConfigurationError(
            field_path=("hooks",), condition="async_hook", operation_id=operation_id, call_id=call_id
        )
    return CallEvents(hooks, settings, call_id=call_id, operation_id=operation_id, path=path)
