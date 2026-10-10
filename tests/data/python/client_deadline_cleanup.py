"""Report how generated clients release responses and borrowed clients when closes fail or a task is cancelled."""

from __future__ import annotations

import asyncio
import importlib
from contextlib import suppress
from typing import TYPE_CHECKING

import httpx2

from tests.data.python.client_runtime import arecord, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from types import ModuleType


class _Body(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """A response body that counts its closes and, when gated, stalls after its first chunk until released.

    Its asyncio read may instead fail after the first chunk, and its close may fail once counted.
    """

    def __init__(
        self,
        gate: asyncio.Event | None = None,
        *,
        failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self.gate = gate
        self.failure = failure
        self.close_failure = close_failure
        self.waiting = asyncio.Event()
        self.closed = 0

    def __iter__(self) -> Iterator[bytes]:
        yield b"response"

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"response"
        if (failure := self.failure) is not None:
            raise failure
        if (gate := self.gate) is not None:
            self.waiting.set()
            await gate.wait()

    def close(self) -> None:
        self.closed += 1
        if (failure := self.close_failure) is not None:
            raise failure

    async def aclose(self) -> None:
        self.close()


class _Transport(httpx2.BaseTransport, httpx2.AsyncBaseTransport):
    """Answer every request with the same counted body."""

    def __init__(self, body: _Body | None = None, status: int = 200) -> None:
        self.body = _Body() if body is None else body
        self.status = status
        self.sends = 0

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        del request
        self.sends += 1
        return httpx2.Response(self.status, headers={"content-type": "application/octet-stream"}, stream=self.body)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return self.handle_request(request)


def _measurements(error: BaseException) -> tuple[object, ...]:
    return (
        getattr(error, "reason", None),
        getattr(error, "attempt_count", None),
        getattr(getattr(error, "info", None), "attempt_count", None),
    )


def _stream_error(package: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    transport = _Transport(_Body(close_failure=RuntimeError("stream close failed")))
    with httpx2.Client(transport=transport) as native:
        api = package.Client(http_client=native)
        try:
            with api.with_streaming_response.request_raw("GET", "https://close.example/"):
                pass
        except errors.SDKError as error:
            record(lines, "stream close error measurements", lambda error=error: _measurements(error))
        api.close()
        record(lines, "stream close resources", lambda: (transport.sends, transport.body.closed))


async def _async_stream_error(package: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    transport = _Transport(_Body(close_failure=RuntimeError("stream close failed")))
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native)
        try:
            async with api.with_streaming_response.request_raw("GET", "https://close.example/"):
                pass
        except errors.SDKError as error:
            record(lines, "async stream close error measurements", lambda error=error: _measurements(error))
        await api.aclose()
        record(lines, "async stream close resources", lambda: (transport.sends, transport.body.closed))


async def _borrowed(package: ModuleType, lines: list[str]) -> None:
    """Close a client twice without closing the borrowed native client, which its views share and cannot close."""
    transport = _Transport()
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native)
        view = api.with_options()
        raw = await view.request_raw("GET", "https://close.example/")
        record(lines, "view call", lambda: (raw.info.status_code, hasattr(view, "aclose"), hasattr(view, "close")))
        await api.aclose()
        await api.aclose()
        record(lines, "borrowed client remains open", lambda: not native.is_closed)
        await arecord(lines, "closed client refuses calls", lambda: api.request_raw("GET", "https://close.example/"))
        await arecord(lines, "closed view refuses calls", lambda: view.request_raw("GET", "https://close.example/"))


async def _buffered_cancel(package: ModuleType, lines: list[str], *, failure: bool = False) -> None:
    """Cancel the caller's task while the call reads its body: the cancellation propagates and the response closes."""
    body = _Body(asyncio.Event(), close_failure=RuntimeError("late response close failed") if failure else None)
    transport = _Transport(body)
    label = "buffered read close failure" if failure else "buffered read"
    errors: list[BaseException] = []
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native)

        async def request() -> None:
            try:
                await api.request_raw("GET", "https://close.example/")
            except asyncio.CancelledError as error:
                errors.append(error)
                record(lines, f"{label} caller cancellation", lambda error=error: (type(error).__name__, error.args))
                raise

        caller = asyncio.create_task(request())
        await body.waiting.wait()
        caller.cancel("buffered read caller cancelled")
        with suppress(asyncio.CancelledError):
            await caller
        await api.aclose()
    record(lines, f"{label} cancellation closes response", lambda: (transport.sends, body.closed))
    record(lines, f"{label} cleanup notes", lambda: tuple(getattr(errors[0], "__notes__", ())))


async def _interrupted(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    """Interrupt a call's response read or close, keeping one interruption primary and the rest as notes."""
    primary = asyncio.CancelledError("original native")
    primary.__dict__["__notes__"] = ["original native note"]
    reused = asyncio.CancelledError("reused native")
    caused = asyncio.CancelledError("caused native close")
    caused.__cause__ = KeyError("native cause")
    for label, status, body in (
        ("interrupted close", 200, _Body(close_failure=asyncio.CancelledError("native close"))),
        ("status failure then interrupted close", 503, _Body(close_failure=asyncio.CancelledError("native close"))),
        ("status failure then caused interrupted close", 503, _Body(close_failure=caused)),
        ("status failure then failed close", 503, _Body(close_failure=RuntimeError("close failed"))),
        ("interruption already primary", 200, _Body(failure=primary, close_failure=RuntimeError("close failed"))),
        ("same interruption reused", 200, _Body(failure=reused, close_failure=reused)),
    ):
        transport = _Transport(body, status)
        async with httpx2.AsyncClient(transport=transport) as client:
            api = package.AsyncClient(http_client=client, retry=options.RetryOptions(initial_delay=0))
            try:
                await api.request_raw("GET", "https://close.example/")
            except (asyncio.CancelledError, errors.SDKError) as error:
                record(
                    lines,
                    f"{label} result",
                    lambda error=error, body=body: (
                        type(error).__name__,
                        error.args,
                        error is (body.close_failure if body.failure is None else body.failure),
                        getattr(error, "attempt_count", None),
                        tuple(getattr(error, "__notes__", ())),
                        getattr(error.__cause__, "reason", type(error.__cause__).__name__),
                    ),
                )
            else:
                record(lines, f"{label} unexpected success", lambda: True)
            await api.aclose()
        record(lines, f"{label} resources", lambda transport=transport: (transport.sends, transport.body.closed))


def _sync_interrupted(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Interrupt a retryable status's close in a synchronous call, keeping the interruption's cause or linking one."""
    caused = KeyboardInterrupt("caused native close")
    caused.__cause__ = KeyError("native cause")
    for label, failure in (
        ("sync status failure then interrupted close", KeyboardInterrupt("native close")),
        ("sync status failure then caused interrupted close", caused),
    ):
        transport = _Transport(_Body(close_failure=failure), 503)
        with httpx2.Client(transport=transport) as client:
            api = package.Client(http_client=client, retry=options.RetryOptions(initial_delay=0))
            try:
                api.request_raw("GET", "https://close.example/")
            except KeyboardInterrupt as error:
                record(
                    lines,
                    f"{label} result",
                    lambda error=error, failure=failure: (
                        error is failure,
                        tuple(getattr(error, "__notes__", ())),
                        getattr(error.__cause__, "reason", type(error.__cause__).__name__),
                    ),
                )
            api.close()
        record(lines, f"{label} resources", lambda transport=transport: (transport.sends, transport.body.closed))


async def _expired(package: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    """Stop a call whose deadline expired at its first boundary before anything is sent."""
    transport = _Transport()
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native, total_timeout=0.0)
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
    record(lines, "expired deadline resources", lambda: (transport.sends, transport.body.closed))


async def _stream_cancel(package: ModuleType, lines: list[str]) -> None:
    """Cancel a task reading a streamed body: the cancellation propagates unchanged and the response closes."""
    transport = _Transport(_Body(asyncio.Event()))
    async with httpx2.AsyncClient(transport=transport) as native:
        api = package.AsyncClient(http_client=native)

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
    record(lines, "stream cancellation resources", lambda: (transport.sends, transport.body.closed))


async def _async(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    await _borrowed(package, lines)
    await _buffered_cancel(package, lines)
    await _buffered_cancel(package, lines, failure=True)
    await _async_stream_error(package, errors, lines)
    await _interrupted(package, options, errors, lines)
    await _expired(package, errors, lines)
    await _stream_cancel(package, lines)


def deadline_cleanup(package: ModuleType, lines: list[str]) -> None:
    """Exercise public generated close, cleanup failure, and cancellation paths over borrowed HTTPX2 clients."""
    options, errors = (importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "errors"))
    _stream_error(package, errors, lines)
    _sync_interrupted(package, options, lines)
    run(lambda: _async(package, options, errors, lines))
