"""Exercise generated raw and streaming clients under deadlines, stream limits, close failures and cancellation."""

from __future__ import annotations

import asyncio
import importlib
import time
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_runtime import Exchange, aoutcome, arecord, injected, outcome, raw_response, record, run
from tests.data.python.fixture_http2 import Http2Fixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType


class _DelayedBody(httpx2.SyncByteStream):
    """Let the TLS server send headers before it waits to send the response body."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def __iter__(self) -> Iterator[bytes]:
        time.sleep(self.delay)
        yield b"ready"


def _delayed(delay: float, status: int = 200) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _: httpx2.Response(
        status,
        headers={"content-type": "text/plain", "content-length": "5"},
        stream=_DelayedBody(delay),
    )


class _ReadCapFailure(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """Observe the public timeout extension when a body read fails after handoff."""

    def __init__(self, request: httpx2.Request, seen: list[float | None]) -> None:
        self.request, self.seen = request, seen

    def __iter__(self) -> Iterator[bytes]:
        self.seen.append(self.request.extensions["timeout"]["read"])
        yield b"partial"
        raise httpx2.ReadError("stream read failed")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self:
            yield chunk


def _read_cap_failure(seen: list[float | None]) -> Callable[[httpx2.Request], httpx2.Response]:
    return injected(lambda request: httpx2.Response(200, stream=_ReadCapFailure(request, seen)))


class _Stop(BaseException):
    """An interruption that is not an Exception, as KeyboardInterrupt is."""


class _WaitingBody(httpx2.AsyncByteStream):
    """Yield a first chunk, then wait for a cancellation, counting closes."""

    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.closes = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"partial"
        self.waiting.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closes += 1


class _StoppedBody(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """Interrupt a body after its first chunk unless it has no original failure, then fail its close once released."""

    def __init__(self, original: BaseException | None, cleanup: BaseException | None, *, gated: bool = False) -> None:
        self.original, self.cleanup = original, cleanup
        self.gated = gated
        self.entered = asyncio.Event() if gated else None
        self.released = asyncio.Event() if gated else None
        self.closes = 0

    def __iter__(self) -> Iterator[bytes]:
        yield b"partial"
        if self.original is not None:
            raise self.original

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self:
            yield chunk

    def close(self) -> None:
        self.closes += 1
        if self.cleanup is not None:
            raise self.cleanup

    async def aclose(self) -> None:
        if self.entered is not None and self.released is not None:
            self.entered.set()
            await self.released.wait()
        self.close()


def _answer(
    stream: httpx2.SyncByteStream | httpx2.AsyncByteStream, status: int = 200
) -> Callable[[httpx2.Request], httpx2.Response]:
    return injected(lambda _: httpx2.Response(status, headers={"content-type": "text/plain"}, stream=stream))


def deadline_streams(package: ModuleType, lines: list[str]) -> None:
    """Read generated raw handles over real TLS, then inject only interruption and same-turn race failures."""
    options = importlib.import_module(f"{package.__name__}.options")
    exchange = Exchange(lines)
    with exchange.client() as native_client_148, package.Client(http_client=native_client_148) as api:
        exchange.respond(_delayed(1.5))
        with api.with_streaming_response.request_raw(
            "GET",
            "https://example.com/handoff",
            options=options.RequestOptions(total_timeout=1),
        ) as response:
            record(lines, "stream read past the acquisition deadline", response.read)
        for label, idle in (("default", options.UNSET), ("disabled", None)):
            seen: list[float | None] = []
            exchange.respond(_read_cap_failure(seen))
            with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/read-cap",
                options=options.RequestOptions(timeout=options.TimeoutOptions(read=idle), total_timeout=None),
            ) as response:
                lines.append(f"  stream read cap {label} {outcome(response.read)}")
            lines.append(f"  stream observed read cap {seen}")
        for label, settings in (
            ("idle", options.RequestOptions(total_timeout=5, timeout=options.TimeoutOptions(read=0.2))),
            ("read", options.RequestOptions(timeout=options.TimeoutOptions(read=0.2))),
        ):
            exchange.respond(_delayed(2))
            with api.with_streaming_response.request_raw(
                "GET", f"https://example.com/{label}", options=settings
            ) as response:
                lines.append(f"  stream {label} {outcome(response.read).partition(' secondary ')[0]}")
        for status in (200, 503):
            exchange.respond(_delayed(2, status))
            record(
                lines,
                f"buffered acquisition {status}",
                lambda: api.request_raw(
                    "GET",
                    "https://example.com/buffered",
                    options=options.RequestOptions(total_timeout=1, retry=options.RetryOptions(max_retries=0)),
                ),
            )
        exchange.respond(raw_response(200, b'{"ready":true}', "application/json"))
        saved = api.request_raw("GET", "https://example.com/saved")
        for primary in (False, True):
            body = _StoppedBody(None, RuntimeError("close failed"))
            exchange.respond(_answer(body))
            with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/release",
                options=options.RequestOptions(max_stream_bytes=0 if primary else None),
            ) as response:
                read_body = (lambda: list(response.iter_bytes())) if primary else response.read
                lines.append(f"  stream close failure primary={primary} {outcome(read_body)}")
            lines.append(f"  stream close count {body.closes}")
    lines.append(f"  buffered survives close {saved.read()!r} {saved.json()!r} {list(saved.iter_bytes())!r}")
    _interruptions(package, lines)
    _http2(package, lines)
    run(lambda: _async_streams(package, lines))


def _interruptions(package: ModuleType, lines: list[str]) -> None:
    """Interrupt a body read, then its close: the read's interruption propagates and the response closes once."""
    options = importlib.import_module(f"{package.__name__}.options")
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        for action in ("read", "iter_bytes", "raise_for_status", "close"):
            body = _StoppedBody(_Stop("read"), _Stop("close"))
            exchange.respond(_answer(body, 503 if action == "raise_for_status" else 200))
            try:
                with api.with_streaming_response.request_raw(
                    "GET",
                    "https://example.com/interruption",
                    options=options.RequestOptions(retry=options.RetryOptions(max_retries=0)),
                ) as raw:
                    if action == "iter_bytes":
                        list(raw.iter_bytes())
                    else:
                        getattr(raw, action)()
            except _Stop as error:
                lines.append(
                    f"  stream interruption {action} original"
                    f" {error is (body.cleanup if action == 'close' else body.original)} released {body.closes}"
                )
        body = _StoppedBody(httpx2.ReadError("read"), _Stop("close"))
        exchange.respond(_answer(body))
        try:
            with api.with_streaming_response.request_raw("GET", "https://example.com/interruption") as raw:
                raw.read()
        except _Stop as error:
            lines.append(
                f"  stream close interruption over a read failure {error is body.cleanup}"
                f" {error.__notes__} released {body.closes}"
            )


def _http2(package: ModuleType, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    server = Http2Fixture(0)
    try:
        with (
            httpx2.Client(http1=False, http2=True, verify=server.client_context, trust_env=False) as http,
            package.Client(http_client=http) as api,
        ):
            warmed = api.request_raw("GET", server.url, options=options.RequestOptions(total_timeout=10))
            lines.append(f"  HTTP2 sync warm connection {warmed.read()!r}")
            server.delay = 1.5
            with api.with_streaming_response.request_raw(
                "GET",
                server.url,
                options=options.RequestOptions(total_timeout=1),
            ) as response:
                record(lines, "HTTP2 stream read past the acquisition deadline", response.read)
        lines.append(f"  HTTP2 sync protocols {server.protocols!r} requests {server.requests}")
    finally:
        server.stop()


async def _async_streams(package: ModuleType, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    exchange = Exchange(lines)
    async with exchange.async_client() as native_client_268, package.AsyncClient(http_client=native_client_268) as api:
        exchange.respond(_delayed(1.5))
        async with api.with_streaming_response.request_raw(
            "GET",
            "https://example.com/handoff",
            options=options.RequestOptions(total_timeout=1),
        ) as response:
            await arecord(lines, "async stream read past the acquisition deadline", response.read)
        for label, idle in (("default", options.UNSET), ("disabled", None)):
            seen: list[float | None] = []
            exchange.respond(_read_cap_failure(seen))
            async with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/read-cap",
                options=options.RequestOptions(timeout=options.TimeoutOptions(read=idle), total_timeout=None),
            ) as response:
                lines.append(f"  async stream read cap {label} {await aoutcome(response.read)}")
            lines.append(f"  async stream observed read cap {seen}")
        for label, settings in (
            ("idle", options.RequestOptions(total_timeout=5, timeout=options.TimeoutOptions(read=0.2))),
            ("read", options.RequestOptions(timeout=options.TimeoutOptions(read=0.2))),
        ):
            exchange.respond(_delayed(2))
            async with api.with_streaming_response.request_raw(
                "GET", f"https://example.com/{label}", options=settings
            ) as response:
                lines.append(f"  async stream {label} {(await aoutcome(response.read)).partition(' secondary ')[0]}")
        exchange.respond(_delayed(2))
        await arecord(
            lines,
            "async buffered acquisition",
            lambda: api.request_raw(
                "GET",
                "https://example.com/buffered",
                options=options.RequestOptions(total_timeout=1, retry=options.RetryOptions(max_retries=0)),
            ),
        )
        exchange.respond(_delayed(2))
        async with api.with_streaming_response.request_raw("GET", "https://example.com/native-cancel") as response:
            reader = asyncio.create_task(response.read())
            await asyncio.sleep(0)
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                lines.append("  async stream native cancellation propagated")
            else:
                lines.append("  async stream native cancellation not propagated")
        exchange.respond(raw_response(200, b'{"ready":true}', "application/json"))
        saved = await api.request_raw("GET", "https://example.com/saved")
        for action in ("read", "iter_bytes", "iter_raw_bytes", "raise_for_status"):
            body = _WaitingBody()
            exchange.respond(_answer(body, 503 if action == "raise_for_status" else 200))
            await _cancelled_read(api, options, body, action, lines)
        for primary in (False, True):
            body = _StoppedBody(None, RuntimeError("close failed"))
            exchange.respond(_answer(body))
            async with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/release",
                options=options.RequestOptions(max_stream_bytes=0 if primary else None),
            ) as response:
                read_body = (lambda: anext(response.iter_bytes())) if primary else response.read
                lines.append(f"  async stream close failure primary={primary} {await aoutcome(read_body)}")
            lines.append(f"  async stream close count {body.closes}")
    lines.append(
        f"  async buffered survives close {await saved.read()!r} {await saved.json()!r} {[part async for part in saved.iter_bytes()]!r}"
    )
    await _async_interruptions(package, lines)
    await _async_http2(package, lines)


async def _cancelled_read(api: Any, options: ModuleType, body: _WaitingBody, action: str, lines: list[str]) -> None:
    """Cancel a task reading a stream while its body waits: the cancellation propagates and the response closes."""

    async def read() -> None:
        async with api.with_streaming_response.request_raw(
            "GET",
            "https://example.com/cancel",
            options=options.RequestOptions(retry=options.RetryOptions(max_retries=0)),
        ) as response:
            if action.startswith("iter_"):
                async for _chunk in getattr(response, action)():
                    pass
            else:
                await getattr(response, action)()

    reader = asyncio.create_task(read())
    await body.waiting.wait()
    reader.cancel(f"{action} cancelled")
    try:
        await reader
    except asyncio.CancelledError as error:
        lines.append(f"  async stream cancel {action} propagated {error.args} released {body.closes}")


async def _async_interruptions(package: ModuleType, lines: list[str]) -> None:
    """Interrupt async body reads and closes, and cancel a reader while its interrupted response closes."""
    options = importlib.import_module(f"{package.__name__}.options")
    exchange = Exchange(lines)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        for action in ("read", "iter_bytes", "raise_for_status", "aclose"):
            body = _StoppedBody(_Stop("read"), _Stop("close"))
            exchange.respond(_answer(body, 503 if action == "raise_for_status" else 200))
            try:
                async with api.with_streaming_response.request_raw(
                    "GET",
                    "https://example.com/interruption",
                    options=options.RequestOptions(retry=options.RetryOptions(max_retries=0)),
                ) as raw:
                    if action == "iter_bytes":
                        async for _ in raw.iter_bytes():
                            pass
                    else:
                        await getattr(raw, action)()
            except _Stop as error:
                lines.append(
                    f"  async stream interruption {action} original"
                    f" {error is (body.cleanup if action == 'aclose' else body.original)} released {body.closes}"
                )
        body = _StoppedBody(httpx2.ReadError("read"), _Stop("close"))
        exchange.respond(_answer(body))
        try:
            async with api.with_streaming_response.request_raw("GET", "https://example.com/interruption") as raw:
                await raw.read()
        except _Stop as error:
            lines.append(
                f"  async stream close interruption over a read failure {error is body.cleanup}"
                f" {error.__notes__} released {body.closes}"
            )
        gated = _StoppedBody(_Stop("read"), None, gated=True)
        exchange.respond(_answer(gated))
        async with api.with_streaming_response.request_raw("GET", "https://example.com/cleanup-cancel") as response:
            reader = asyncio.create_task(response.read())
            await gated.entered.wait()
            reader.cancel()
            gated.released.set()
            try:
                await reader
            except (_Stop, asyncio.CancelledError) as error:
                lines.append(
                    f"  async stream cleanup cancellation {type(error).__name__} original {error is gated.original}"
                )
    lines.append(f"  async stream cancelled cleanup released {gated.closes}")


async def _async_http2(package: ModuleType, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    server = Http2Fixture(0)
    try:
        async with (
            httpx2.AsyncClient(http1=False, http2=True, verify=server.client_context, trust_env=False) as http,
            package.AsyncClient(http_client=http) as api,
        ):
            warmed = await api.request_raw("GET", server.url, options=options.RequestOptions(total_timeout=10))
            lines.append(f"  HTTP2 async warm connection {await warmed.read()!r}")
            server.delay = 1.5
            async with api.with_streaming_response.request_raw(
                "GET",
                server.url,
                options=options.RequestOptions(total_timeout=1),
            ) as response:
                await arecord(lines, "HTTP2 async stream read past the acquisition deadline", response.read)
        lines.append(f"  HTTP2 async protocols {server.protocols!r} requests {server.requests}")
    finally:
        server.stop()
