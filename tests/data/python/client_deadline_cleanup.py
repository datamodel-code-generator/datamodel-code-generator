"""Report how generated clients release responses and borrowed clients when hooks fail or a task is cancelled."""

from __future__ import annotations

import asyncio
import importlib
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, TypeAlias

import httpx2

from tests.data.python.client_runtime import arecord, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping
    from types import ModuleType

_EventLog: TypeAlias = list[tuple[str, str, str | None, int]]


class _Body(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """A response body that counts its closes and, when gated, stalls after its first chunk until released."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.waiting = asyncio.Event()
        self.closed = 0

    def __iter__(self) -> Iterator[bytes]:
        yield b"response"

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"response"
        if (gate := self.gate) is not None:
            self.waiting.set()
            await gate.wait()

    def close(self) -> None:
        self.closed += 1

    async def aclose(self) -> None:
        self.closed += 1


class _Transport(httpx2.BaseTransport, httpx2.AsyncBaseTransport):
    """Answer every request with the same counted body."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.body = _Body(gate)
        self.sends = 0

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        del request
        self.sends += 1
        return httpx2.Response(200, headers={"content-type": "application/octet-stream"}, stream=self.body)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return self.handle_request(request)


class _Event(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def outcome(self) -> str | None: ...

    @property
    def attempt_count(self) -> int: ...


class _FaultHook:
    def __init__(self, name: str, events: _EventLog, *, failures: Mapping[str, BaseException] | None = None) -> None:
        self.name = name
        self.events = events
        self.failures = {} if failures is None else failures

    async def on_event(self, event: _Event) -> None:
        name = event.name
        self.events.append((self.name, name, event.outcome, event.attempt_count))
        if (failure := self.failures.get(name)) is not None:
            raise failure


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
        self.events.append((name, event.outcome, event.attempt_count))
        if name == "attempt_end":
            self.started.set()
            await self.proceed.wait()
        if name == "call_end" and self.failure:
            msg = "late terminal hook failure"
            raise RuntimeError(msg)


def _measurements(error: BaseException) -> tuple[object, ...]:
    return (
        getattr(error, "reason", None),
        getattr(error, "attempt_count", None),
        getattr(getattr(error, "info", None), "attempt_count", None),
    )


def _stream_error(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    transport = _Transport()
    with httpx2.Client(transport=transport) as native:
        api = package.Client(http_client=native, options=options.ClientOptions(hooks=(_StreamHook(),)))
        try:
            with api.with_streaming_response.request_raw("GET", "https://close.example/"):
                pass
        except errors.SDKError as error:
            record(lines, "stream end error measurements", lambda error=error: _measurements(error))
        api.close()
        record(lines, "stream end resources", lambda: (transport.sends, transport.body.closed))


async def _async_stream_error(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    transport = _Transport()
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native, options=options.ClientOptions(hooks=(_AsyncStreamHook(),)))
        try:
            async with api.with_streaming_response.request_raw("GET", "https://close.example/"):
                pass
        except errors.SDKError as error:
            record(lines, "async stream end error measurements", lambda error=error: _measurements(error))
        await api.aclose()
        record(lines, "async stream end resources", lambda: (transport.sends, transport.body.closed))


async def _borrowed(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Close a client twice without closing the borrowed native client, which its views share and cannot close."""
    transport = _Transport()
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native)
        view = api.with_options(options.RequestOptions())
        raw = await view.request_raw("GET", "https://close.example/")
        record(lines, "view call", lambda: (raw.info.status_code, hasattr(view, "aclose"), hasattr(view, "close")))
        await api.aclose()
        await api.aclose()
        record(lines, "borrowed client remains open", lambda: not native.is_closed)
        await arecord(lines, "closed client refuses calls", lambda: api.request_raw("GET", "https://close.example/"))
        await arecord(lines, "closed view refuses calls", lambda: view.request_raw("GET", "https://close.example/"))


async def _terminal_cancel(
    package: ModuleType, options: ModuleType, lines: list[str], *, failure: bool = False
) -> None:
    """Cancel the caller's task while a terminal hook runs: the cancellation propagates and the response closes."""
    transport, hook = _Transport(), _TerminalHook(failure=failure)
    label = "terminal late failure" if failure else "terminal"
    errors: list[BaseException] = []
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native, options=options.ClientOptions(hooks=(hook,)))

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
        hook.proceed.set()
        with suppress(asyncio.CancelledError):
            await caller
        await api.aclose()
    record(lines, f"{label} events after cancellation", lambda: tuple(hook.events))
    record(lines, f"{label} cancellation closes response", lambda: transport.body.closed)
    record(lines, f"{label} cleanup notes", lambda: tuple(getattr(errors[0], "__notes__", ())))


async def _terminal_native(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    primary = asyncio.CancelledError("original native")
    primary.__dict__["__notes__"] = ["original native note"]
    reused = asyncio.CancelledError("reused native")
    for label, before, caused in (
        ("terminal native", None, False),
        ("SDK failure then native", RuntimeError("response hook failed"), False),
        ("SDK failure then caused native", RuntimeError("response hook failed"), True),
        ("native already primary", primary, False),
        ("same native reused", reused, False),
    ):
        transport = _Transport()
        events: _EventLog = []
        native = reused if before is reused else asyncio.CancelledError("first terminal native")
        if caused:
            native.__cause__ = KeyError("native cause")
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
        async with httpx2.AsyncClient(transport=transport) as client:
            api = package.AsyncClient(http_client=client, options=options.ClientOptions(hooks=hooks))
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
                        getattr(error, "cause", None) is before
                        if isinstance(before, RuntimeError)
                        else error is expected,
                        getattr(error, "attempt_count", None),
                        tuple(getattr(error, "__notes__", ())),
                        getattr(error.__cause__, "reason", type(error.__cause__).__name__),
                    ),
                )
            else:
                record(lines, f"{label} unexpected success", lambda: True)
            await api.aclose()
        record(lines, f"{label} hook order", lambda events=events: tuple(events))
        record(lines, f"{label} resources", lambda transport=transport: (transport.sends, transport.body.closed))


async def _expired(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    """Stop a call whose deadline expired at its first boundary, still ending the hooks that saw it start."""
    transport = _Transport()
    events: _EventLog = []
    hooks = (
        _FaultHook("first", events, failures={"call_start": RuntimeError("start hook failed")}),
        _FaultHook("last", events),
    )
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native, options=options.ClientOptions(hooks=hooks, total_timeout=0.0))
        try:
            await api.request_raw("GET", "https://close.example/")
        except errors.SDKError as error:
            record(
                lines,
                "expired deadline result",
                lambda error=error: (
                    type(error).__name__,
                    error.reason,
                    error.attempt_count,
                    tuple(getattr(error, "__notes__", ())),
                ),
            )
        await api.aclose()
    record(lines, "expired deadline hook order", lambda: tuple(events))
    record(lines, "expired deadline resources", lambda: (transport.sends, transport.body.closed))


async def _stream_cancel(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Cancel a task reading a streamed body: the cancellation propagates unchanged and the response closes."""
    transport = _Transport(asyncio.Event())
    events: _EventLog = []
    hooks = (
        _FaultHook("first", events, failures={"stream_end": ValueError("stream hook failed")}),
        _FaultHook("last", events),
    )
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native, options=options.ClientOptions(hooks=hooks))

        async def read() -> None:
            async with api.with_streaming_response.request_raw("GET", "https://close.example/") as raw:
                await raw.read()

        reader = asyncio.create_task(read())
        await transport.body.waiting.wait()
        reader.cancel("stream reader cancelled")
        try:
            await reader
        except asyncio.CancelledError as error:
            record(
                lines,
                "stream cancellation result",
                lambda error=error: (type(error).__name__, error.args, tuple(getattr(error, "__notes__", ()))),
            )
        await api.aclose()
    record(lines, "stream cancellation hook order", lambda: tuple(events))
    record(lines, "stream cancellation resources", lambda: (transport.sends, transport.body.closed))


async def _async(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    await _borrowed(package, options, lines)
    await _terminal_cancel(package, options, lines)
    await _terminal_cancel(package, options, lines, failure=True)
    await _async_stream_error(package, options, errors, lines)
    await _terminal_native(package, options, errors, lines)
    await _expired(package, options, errors, lines)
    await _stream_cancel(package, options, lines)


def deadline_cleanup(package: ModuleType, lines: list[str]) -> None:
    """Exercise public generated close, hook, and cancellation paths over borrowed HTTPX2 clients."""
    options, errors = (importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "errors"))
    _stream_error(package, options, errors, lines)
    run(lambda: _async(package, options, errors, lines))
