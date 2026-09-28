"""Report generated-client ownership of asynchronous client-close finalizers."""

from __future__ import annotations

import asyncio
import importlib
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, TypeAlias

from tests.data.python.client_runtime import arecord, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping
    from contextlib import AbstractAsyncContextManager
    from types import ModuleType

_EventLog: TypeAlias = list[tuple[str, str, str | None, int, int, int]]


class _ClosingResponse:
    status_code = 200

    def __init__(self, responses: ModuleType) -> None:
        self.headers = responses.HeadersView((("content-type", "application/octet-stream"),))
        self.closed = 0
        self.finished = asyncio.Event()
        self.proceed: asyncio.Event | None = None

    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        yield b"response"

    async def aclose(self) -> None:
        self.closed += 1
        if self.proceed is not None:
            await self.proceed.wait()
        self.finished.set()


class _ClosingAdapter:
    def __init__(self, transports: ModuleType, responses: ModuleType, failure: BaseException | None = None) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
        )
        self.response = _ClosingResponse(responses)
        self.failure = failure
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()
        self.finished = asyncio.Event()
        self.closes = 0
        self.sends = 0
        self.cancelled = False

    async def send(self, request: object, context: object) -> _ClosingResponse:
        del request, context
        self.sends += 1
        return self.response

    async def aclose(self) -> None:
        self.closes += 1
        self.started.set()
        try:
            await self.proceed.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished.set()
        if self.failure is not None:
            raise self.failure


class _Response:
    status_code = 200

    def __init__(self, responses: ModuleType) -> None:
        self.headers = responses.HeadersView((("content-type", "application/octet-stream"),))

    def iter_raw_bytes(self) -> Iterator[bytes]:
        yield b"response"

    def close(self) -> None:
        pass


class _Adapter:
    def __init__(self, transports: ModuleType, responses: ModuleType) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
        )
        self.response = _Response(responses)

    def send(self, request: object, context: object) -> _Response:
        del request, context
        return self.response

    def close(self) -> None:
        pass


class _Event(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def outcome(self) -> str | None: ...

    @property
    def network_send_count(self) -> int: ...

    @property
    def resource_attempt_count(self) -> int: ...

    @property
    def network_send_budget_used(self) -> int: ...


class _FaultHook:
    def __init__(
        self,
        name: str,
        events: _EventLog,
        *,
        failures: Mapping[str, BaseException] | None = None,
        pause: str | None = None,
    ) -> None:
        self.name = name
        self.events = events
        self.failures = {} if failures is None else failures
        self.pause = pause
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()
        self.finished = asyncio.Event()
        self.ended = asyncio.Event()

    async def on_event(self, event: _Event) -> None:
        name = event.name
        self.events.append((
            self.name,
            name,
            event.outcome,
            event.resource_attempt_count,
            event.network_send_count,
            event.network_send_budget_used,
        ))
        try:
            if name == self.pause:
                self.started.set()
                await self.proceed.wait()
            if (failure := self.failures.get(name)) is not None:
                raise failure
        finally:
            if name == self.pause:
                self.finished.set()
            if name in {"call_end", "stream_end"}:
                self.ended.set()


class _StreamHook:
    def on_event(self, event: _Event) -> None:
        if event.name == "stream_end":
            msg = "stream end hook failed"
            raise RuntimeError(msg)


class _AsyncStreamHook:
    async def on_event(self, event: _Event) -> None:
        if event.name == "stream_end":
            msg = "stream end hook failed"
            raise RuntimeError(msg)


class _TerminalHook:
    def __init__(self, *, failure: bool = False) -> None:
        self.events: list[tuple[str, str | None, int]] = []
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()
        self.failure = failure

    async def on_event(self, event: _Event) -> None:
        name = event.name
        self.events.append((name, event.outcome, event.network_send_count))
        if name == "attempt_end":
            self.started.set()
            await self.proceed.wait()
            if self.failure:
                msg = "late terminal hook failure"
                raise RuntimeError(msg)


async def _cancelled(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    adapter = _ClosingAdapter(transports, responses)
    api = package.AsyncClient(
        transport_adapter=transports.OwnedTransportAdapter(adapter),
        options=options.ClientOptions(cleanup_timeout=0.05),
    )

    async def close() -> None:
        try:
            await api.aclose()
        except asyncio.CancelledError as error:
            record(lines, "close native cancellation", lambda error=error: (type(error).__name__, error.args))
            raise

    caller = asyncio.create_task(close())
    await adapter.started.wait()
    caller.cancel("close caller cancelled")
    with suppress(asyncio.CancelledError):
        await caller
    waiter = asyncio.create_task(api.aclose())
    await asyncio.sleep(0)
    record(lines, "close retained", lambda: (adapter.closes, adapter.cancelled, waiter.done()))
    adapter.proceed.set()
    await waiter
    await api.aclose()
    record(lines, "close completed once", lambda: (adapter.closes, adapter.finished.is_set(), adapter.cancelled))
    await arecord(lines, "closed client refuses calls", lambda: api.request_raw("GET", "https://close.example/"))


async def _timeouts(
    package: ModuleType,
    options: ModuleType,
    transports: ModuleType,
    responses: ModuleType,
    errors: ModuleType,
    lines: list[str],
) -> None:
    for label, failure in (("success", None), ("late failure", RuntimeError("adapter close failed"))):
        adapter = _ClosingAdapter(transports, responses, failure)
        api = package.AsyncClient(
            transport_adapter=transports.OwnedTransportAdapter(adapter),
            options=options.ClientOptions(cleanup_timeout=0.01),
        )
        caller = asyncio.create_task(api.aclose())
        await adapter.started.wait()
        await asyncio.wait({caller}, timeout=1.0)
        record(lines, f"close {label} bounded", caller.done)
        if not caller.done():
            caller.cancel()
        try:
            await caller
        except errors.CleanupError as error:
            record(
                lines,
                f"close {label} timeout",
                lambda error=error: (
                    error.pending_calls,
                    error.pending_leases,
                    error.timeout,
                    type(error.cause).__name__,
                ),
            )
        except asyncio.CancelledError:
            record(lines, f"close {label} uncapped", lambda: True)
        await arecord(
            lines, f"close {label} remains closing", lambda api=api: api.request_raw("GET", "https://close.example/")
        )
        adapter.proceed.set()
        await adapter.finished.wait()
        await arecord(lines, f"close {label} final result", api.aclose)
        await arecord(lines, f"close {label} closed again", api.aclose)
        record(lines, f"close {label} resource", lambda adapter=adapter: (adapter.closes, adapter.cancelled))


async def _concurrent(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    adapter = _ClosingAdapter(transports, responses)
    api = package.AsyncClient(
        transport_adapter=transports.OwnedTransportAdapter(adapter),
        options=options.ClientOptions(cleanup_timeout=1.0),
    )
    first, second = asyncio.create_task(api.aclose()), asyncio.create_task(api.aclose())
    await adapter.started.wait()
    adapter.proceed.set()
    await asyncio.gather(first, second)
    record(lines, "concurrent close resource", lambda: (adapter.closes, adapter.cancelled))


async def _views(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    adapter = _ClosingAdapter(transports, responses)
    api = package.AsyncClient(transport_adapter=transports.OwnedTransportAdapter(adapter))
    view = api.with_options(options.RequestOptions())
    await view.aclose()
    await view.aclose()
    record(lines, "view leaves parent transport", lambda: adapter.closes)
    raw = await api.request_raw("GET", "https://close.example/")
    record(lines, "parent usable after view closes", lambda: raw.info.status_code)
    adapter.proceed.set()
    await api.aclose()
    record(lines, "parent closes transport", lambda: adapter.closes)
    borrowed = _ClosingAdapter(transports, responses)
    api = package.AsyncClient(transport_adapter=borrowed)
    await api.aclose()
    record(lines, "borrowed transport remains open", lambda: borrowed.closes)


async def _draining(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    adapter = _ClosingAdapter(transports, responses)
    adapter.proceed.set()
    api = package.AsyncClient(
        transport_adapter=transports.OwnedTransportAdapter(adapter),
        options=options.ClientOptions(cleanup_timeout=0.02),
    )
    async with api.with_streaming_response.request_raw("GET", "https://close.example/"):
        caller = asyncio.create_task(api.aclose())
        await asyncio.sleep(0)
        caller.cancel()
        try:
            await caller
        except asyncio.CancelledError:
            record(lines, "drain caller cancelled", lambda: True)
        await adapter.finished.wait()
        await api.aclose()
    record(lines, "cancelled drain releases resources", lambda: (adapter.closes, adapter.response.closed))


async def _native_cleanup(package: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    adapter = _ClosingAdapter(transports, responses, asyncio.CancelledError("adapter interruption"))
    adapter.proceed.set()
    api = package.AsyncClient(transport_adapter=transports.OwnedTransportAdapter(adapter))
    try:
        await api.aclose()
    except asyncio.CancelledError as error:
        record(
            lines,
            "adapter native interruption",
            lambda error=error: (type(error).__name__, error.args, adapter.closes),
        )
    await arecord(lines, "adapter interrupted close again", api.aclose)


async def _retained_response(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    adapter = _ClosingAdapter(transports, responses)
    response_gate = adapter.response.proceed = asyncio.Event()
    adapter.proceed.set()
    api = package.AsyncClient(
        transport_adapter=transports.OwnedTransportAdapter(adapter),
        options=options.ClientOptions(cleanup_timeout=0.01),
    )
    async with api.with_streaming_response.request_raw("GET", "https://close.example/"):
        await arecord(lines, "retained response first close", api.aclose)
        await adapter.finished.wait()
        await arecord(lines, "retained response second close", api.aclose)
        response_gate.set()
        await adapter.response.finished.wait()
        await arecord(lines, "retained response final close", api.aclose)
        await arecord(lines, "retained response closed again", api.aclose)
    record(lines, "retained response resources", lambda: (adapter.closes, adapter.response.closed))


async def _terminal_cancel(
    package: ModuleType,
    options: ModuleType,
    transports: ModuleType,
    responses: ModuleType,
    lines: list[str],
    *,
    failure: bool = False,
) -> None:
    adapter, hook = _ClosingAdapter(transports, responses), _TerminalHook(failure=failure)
    label = "terminal late failure" if failure else "terminal"
    errors: list[BaseException] = []
    api = package.AsyncClient(
        transport_adapter=adapter, options=options.ClientOptions(hooks=(hook,), cleanup_timeout=0.05)
    )

    async def request() -> None:
        try:
            await api.request_raw("GET", "https://close.example/")
        except asyncio.CancelledError as error:
            errors.append(error)
            record(lines, f"{label} caller cancellation", lambda error=error: (type(error).__name__, error.args))
            raise

    caller = asyncio.create_task(request())
    await hook.started.wait()
    caller.cancel("terminal hook caller cancelled")
    with suppress(asyncio.CancelledError):
        await caller
    hook.proceed.set()
    await api.aclose()
    record(lines, f"{label} events after cancellation", lambda: tuple(hook.events))
    record(lines, f"{label} cancellation closes response", lambda: adapter.response.closed)
    record(
        lines,
        f"{label} cleanup notes",
        lambda: tuple(getattr(errors[0], "__notes__", ())),
    )


def _stream_error(package: ModuleType, lines: list[str]) -> None:
    options, transports, responses, errors = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("options", "transports", "responses", "errors")
    )
    api = package.Client(
        transport_adapter=_Adapter(transports, responses), options=options.ClientOptions(hooks=(_StreamHook(),))
    )
    try:
        with api.with_streaming_response.request_raw("GET", "https://close.example/"):
            pass
    except errors.HookExecutionError as error:
        record(
            lines,
            "stream end error counters",
            lambda error=error: (
                error.resource_attempt_count,
                error.network_send_count,
                error.network_send_budget_used,
                error.wire_send_count,
                error.info.network_send_count,
                error.has_completed_result,
            ),
        )
    api.close()


async def _async_stream_error(
    package: ModuleType,
    options: ModuleType,
    transports: ModuleType,
    responses: ModuleType,
    errors: ModuleType,
    lines: list[str],
) -> None:
    adapter = _ClosingAdapter(transports, responses)
    api = package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(hooks=(_AsyncStreamHook(),)))
    try:
        async with api.with_streaming_response.request_raw("GET", "https://close.example/"):
            pass
    except errors.HookExecutionError as error:
        record(
            lines,
            "async stream end error counters",
            lambda error=error: (
                error.resource_attempt_count,
                error.network_send_count,
                error.network_send_budget_used,
                error.wire_send_count,
                error.info.network_send_count,
                error.has_completed_result,
            ),
        )
    await api.aclose()


async def _terminal_native(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    primary = asyncio.CancelledError("original native")
    primary.__dict__["__notes__"] = ["original native note"]
    reused = asyncio.CancelledError("reused native")
    for label, before in (
        ("terminal native", None),
        ("SDK failure then native", RuntimeError("response hook failed")),
        ("native already primary", primary),
        ("same native reused", reused),
    ):
        adapter = _ClosingAdapter(transports, responses)
        events: _EventLog = []
        native = reused if before is reused else asyncio.CancelledError("first terminal native")
        failures: dict[str, BaseException] = {"attempt_end": native}
        if before is not None:
            failures["response_headers"] = before
        hooks = (
            _FaultHook("first", events, failures=failures),
            _FaultHook(
                "second",
                events,
                failures={
                    "attempt_end": reused if before is reused else asyncio.CancelledError("second terminal native")
                },
            ),
            _FaultHook(
                "last",
                events,
                failures={"attempt_end": ValueError("attempt hook failed"), "call_end": LookupError("end hook failed")},
            ),
        )
        api = package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(hooks=hooks))
        expected = before if isinstance(before, asyncio.CancelledError) else native
        try:
            await api.request_raw("GET", "https://close.example/")
        except (asyncio.CancelledError, errors.SDKError) as error:
            record(
                lines,
                f"{label} result",
                lambda error=error, expected=expected, before=before: (
                    type(error).__name__,
                    error.args,
                    getattr(error, "cause", None) is before if isinstance(before, RuntimeError) else error is expected,
                    getattr(error, "resource_attempt_count", None),
                    getattr(error, "network_send_count", None),
                    getattr(error, "network_send_budget_used", None),
                    tuple(type(failure).__name__ for failure in getattr(error, "secondary_errors", ())),
                    tuple(getattr(error, "__notes__", ())),
                ),
            )
        else:
            record(lines, f"{label} unexpected success", lambda: True)
        await api.aclose()
        record(lines, f"{label} hook order", lambda events=events: tuple(events))
        record(lines, f"{label} resources", lambda adapter=adapter: (adapter.sends, adapter.response.closed))


async def _capped_hooks(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    for label, pause, total in (
        ("terminal cap", "attempt_end", None),
        ("expired start cap", "call_start", 0.0),
        ("stream end cap", "stream_end", None),
    ):
        adapter = _ClosingAdapter(transports, responses)
        events: _EventLog = []
        first = _FaultHook("first", events, failures={pause: RuntimeError("late hook failed")}, pause=pause)
        last = _FaultHook("last", events)
        api = package.AsyncClient(
            transport_adapter=adapter,
            options=options.ClientOptions(hooks=(first, last), cleanup_timeout=0.005, total_timeout=total),
        )
        failures: list[BaseException] = []
        caller = asyncio.create_task(
            _close_stream(api.with_streaming_response.request_raw("GET", "https://close.example/"))
            if pause == "stream_end"
            else api.request_raw("GET", "https://close.example/")
        )
        await first.started.wait()
        try:
            await caller
        except errors.SDKError as error:
            failures.append(error)
            record(
                lines,
                f"{label} result",
                lambda error=error: (
                    type(error).__name__,
                    getattr(error, "resource_attempt_count", None),
                    getattr(error, "network_send_count", None),
                    getattr(error, "network_send_budget_used", None),
                    tuple(type(item).__name__ for item in getattr(error, "secondary_errors", ())),
                ),
            )
        record(lines, f"{label} hook order before release", lambda events=events: tuple(events))
        first.proceed.set()
        await first.finished.wait()
        await last.ended.wait()
        await asyncio.sleep(0)
        await api.aclose()
        record(lines, f"{label} final hook order", lambda events=events: tuple(events))
        record(
            lines,
            f"{label} late failures",
            lambda failures=failures: tuple(
                (
                    type(error).__name__,
                    type(getattr(error, "cause", None)).__name__,
                    getattr(error, "network_send_count", None),
                    tuple(type(nested).__name__ for nested in getattr(error, "secondary_errors", ())),
                )
                for error in getattr(failures[0], "secondary_errors", ())
            ),
        )
        record(
            lines,
            f"{label} cause late failures",
            lambda failures=failures: tuple(
                (
                    type(error).__name__,
                    type(getattr(error, "cause", None)).__name__,
                    getattr(error, "network_send_count", None),
                )
                for error in getattr(getattr(failures[0], "cause", None), "secondary_errors", ())
            ),
        )
        record(lines, f"{label} resources", lambda adapter=adapter: (adapter.sends, adapter.response.closed))


async def _close_stream(manager: AbstractAsyncContextManager[object]) -> None:
    async with manager:
        pass


async def _stream_cancel(
    package: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    adapter, token = _ClosingAdapter(transports, responses), options.CancelToken()
    events: _EventLog = []
    hooks = (
        _FaultHook("first", events, failures={"stream_end": ValueError("stream hook failed")}),
        _FaultHook("last", events),
    )
    api = package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(hooks=hooks, cancel_token=token))
    async with api.with_streaming_response.request_raw("GET", "https://close.example/") as raw:
        token.cancel()
        try:
            await raw.read()
        except errors.RequestCancelledError as error:
            record(
                lines,
                "stream cancellation result",
                lambda error=error: (
                    type(error).__name__,
                    getattr(error, "resource_attempt_count", None),
                    getattr(error, "network_send_count", None),
                    getattr(error, "network_send_budget_used", None),
                    tuple(type(failure).__name__ for failure in getattr(error, "secondary_errors", ())),
                ),
            )
    await api.aclose()
    record(lines, "stream cancellation hook order", lambda: tuple(events))
    record(lines, "stream cancellation resources", lambda: (adapter.sends, adapter.response.closed))


async def _async(package: ModuleType, lines: list[str]) -> None:
    options, transports, responses, errors = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("options", "transports", "responses", "errors")
    )
    await _cancelled(package, options, transports, responses, lines)
    await _timeouts(package, options, transports, responses, errors, lines)
    await _concurrent(package, options, transports, responses, lines)
    await _views(package, options, transports, responses, lines)
    await _draining(package, options, transports, responses, lines)
    await _native_cleanup(package, transports, responses, lines)
    await _retained_response(package, options, transports, responses, lines)
    await _terminal_cancel(package, options, transports, responses, lines)
    await _terminal_cancel(package, options, transports, responses, lines, failure=True)
    await _async_stream_error(package, options, transports, responses, errors, lines)
    await _terminal_native(package, options, transports, responses, lines)
    await _capped_hooks(package, options, transports, responses, lines)
    await _stream_cancel(package, options, transports, responses, lines)


def deadline_cleanup(package: ModuleType, lines: list[str]) -> None:
    """Exercise public generated close APIs with independent transport and caller lifetimes."""
    _stream_error(package, lines)
    run(lambda: _async(package, lines))
