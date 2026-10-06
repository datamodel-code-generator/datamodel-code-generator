"""Exercise generated raw and streaming clients under acquisition, stream, and cancellation limits."""

from __future__ import annotations

import asyncio
import importlib
import time
from typing import TYPE_CHECKING

import httpx2

from tests.data.python.client_runtime import Exchange, aoutcome, arecord, injected, outcome, raw_response, record, run
from tests.data.python.client_transports import Adapter, AsyncAdapter, AsyncResponse, Response, Stop
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


class _InterruptedBody(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """Cancel the caller's token while a native chunk is completing."""

    def __init__(self, action: Callable[[], None]) -> None:
        self.action = action
        self.closes = 0

    def __iter__(self) -> Iterator[bytes]:
        self.action()
        yield b"partial"

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self:
            yield chunk

    def close(self) -> None:
        self.closes += 1

    async def aclose(self) -> None:
        self.close()


class _CloseInterruption:
    cleanup: BaseException
    closes = 0

    def close(self) -> None:
        self.closes += 1
        raise self.cleanup


class _InterruptedResponse(_CloseInterruption, Response):
    """Fail cleanup after the SDK has observed a native body interruption."""


class _AsyncInterruptedResponse(_CloseInterruption, AsyncResponse):
    """Fail asynchronous cleanup after the SDK has observed a native body interruption."""


class _GatedResponse(AsyncResponse):
    """Keep cleanup pending so caller cancellation can race with an earlier native read interruption."""

    def __init__(self, lines: list[str], headers: object, failure: BaseException) -> None:
        super().__init__(lines, 200, headers, (b"partial", failure))
        self.entered = asyncio.Event()
        self.released = asyncio.Event()
        self.closes = 0

    async def aclose(self) -> None:
        self.entered.set()
        await self.released.wait()
        self.closes += 1


class _FailingLimiter:
    """Return a permit that fails when released, recording that cleanup never retries it."""

    released = 0

    def acquire(self, context: object) -> _FailingLimiter:
        return self

    def release(self) -> None:
        self.released += 1
        raise RuntimeError("permit release failed")


class _AsyncFailingLimiter:
    """Return an asynchronous permit that fails when released."""

    released = 0

    async def acquire(self, context: object) -> _AsyncFailingLimiter:
        return self

    async def release(self) -> None:
        self.released += 1
        raise RuntimeError("permit release failed")


def _interrupted(body: _InterruptedBody, status: int = 200) -> Callable[[httpx2.Request], httpx2.Response]:
    return injected(lambda _: httpx2.Response(status, headers={"content-type": "text/plain"}, stream=body))


def deadline_streams(package: ModuleType, lines: list[str]) -> None:
    """Read generated raw handles over real TLS, then inject only interruption and same-turn race failures."""
    options = importlib.import_module(f"{package.__name__}.options")
    exchange = Exchange(lines)
    with exchange.client() as native_client_148, package.Client(http_client=native_client_148) as api:
        exchange.respond(_delayed(1.5))
        with api.with_streaming_response.request_raw(
            "GET",
            "https://example.com/handoff",
            options=options.RequestOptions(total_timeout=1, stream_idle_timeout=3),
        ) as response:
            record(lines, "stream acquisition released", response.read)
        for label, idle in (("default", options.UNSET), ("disabled", None)):
            seen: list[float | None] = []
            exchange.respond(_read_cap_failure(seen))
            with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/read-cap",
                options=options.RequestOptions(stream_idle_timeout=idle),
            ) as response:
                lines.append(f"  stream read cap {label} {outcome(response.read)}")
            lines.append(f"  stream observed read cap {seen}")
        for label, settings in (
            ("idle", options.RequestOptions(total_timeout=5, stream_idle_timeout=0.2)),
            ("read", options.RequestOptions(timeout=options.TimeoutOptions(read=0.2), stream_idle_timeout=None)),
            ("total", options.RequestOptions(stream_total_timeout=0.2, stream_idle_timeout=3)),
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
        token = options.CancelToken()
        exchange.respond(raw_response(200, b'{"ready":true}', "application/json"))
        saved = api.request_raw("GET", "https://example.com/saved", options=options.RequestOptions(cancel_token=token))
        token.cancel()
        for action in ("read", "iter_bytes", "iter_raw_bytes", "raise_for_status"):
            token = options.CancelToken()
            body = _InterruptedBody(action=token.cancel)
            exchange.respond(_interrupted(body, 503 if action == "raise_for_status" else 200))
            with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/cancel",
                options=options.RequestOptions(cancel_token=token, retry=options.RetryOptions(max_retries=0)),
            ) as response:
                read = getattr(response, action)
                record(
                    lines, f"stream cancel {action}", lambda: b"".join(read()) if action.startswith("iter_") else read()
                )
            lines.append(f"  stream cancel released {body.closes}")
        for primary in (False, True):
            limiter = _FailingLimiter()
            exchange.respond(raw_response(200, b"ready", "text/plain"))
            with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/release",
                options=options.RequestOptions(limiter=limiter, max_stream_bytes=0 if primary else None),
            ) as response:
                read_body = (lambda: list(response.iter_bytes())) if primary else response.read
                lines.append(f"  stream permit failure primary={primary} {outcome(read_body)}")
            lines.append(f"  stream permit release count {limiter.released}")
    lines.append(f"  buffered survives cancel and close {saved.read()!r} {saved.json()!r} {list(saved.iter_bytes())!r}")
    _interruptions(package, lines)
    _http2(package, lines)
    run(lambda: _async_streams(package, lines))


def _interruptions(package: ModuleType, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    headers = importlib.import_module(f"{package.__name__}.responses").HeadersView([("content-type", "text/plain")])
    adapter = Adapter(transports, lines)
    with package.Client(transport_adapter=adapter) as api:
        for action in ("read", "iter_bytes", "raise_for_status", "close"):
            original, closing = Stop("read"), Stop("close")
            response = _InterruptedResponse(
                lines, 503 if action == "raise_for_status" else 200, headers, (b"partial", original)
            )
            response.cleanup = closing
            adapter.replies.append(lambda request, context: response)
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
            except Stop as error:
                lines.append(
                    f"  stream interruption {action} original {error is (closing if action == 'close' else original)} released {response.closes}"
                )


def _http2(package: ModuleType, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    server = Http2Fixture(0)
    try:
        http = httpx2.Client(http1=False, http2=True, verify=server.client_context, trust_env=False)
        with package.Client(http_client=http) as api:
            warmed = api.request_raw("GET", server.url, options=options.RequestOptions(total_timeout=10))
            lines.append(f"  HTTP2 sync warm connection {warmed.read()!r}")
            server.delay = 1.5
            with api.with_streaming_response.request_raw(
                "GET",
                server.url,
                options=options.RequestOptions(total_timeout=1, stream_idle_timeout=3),
            ) as response:
                record(lines, "HTTP2 stream acquisition released", response.read)
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
            options=options.RequestOptions(total_timeout=1, stream_idle_timeout=3),
        ) as response:
            await arecord(lines, "async stream acquisition released", response.read)
        for label, idle in (("default", options.UNSET), ("disabled", None)):
            seen: list[float | None] = []
            exchange.respond(_read_cap_failure(seen))
            async with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/read-cap",
                options=options.RequestOptions(stream_idle_timeout=idle),
            ) as response:
                lines.append(f"  async stream read cap {label} {await aoutcome(response.read)}")
            lines.append(f"  async stream observed read cap {seen}")
        for label, settings in (
            ("idle", options.RequestOptions(total_timeout=5, stream_idle_timeout=0.2)),
            ("read", options.RequestOptions(timeout=options.TimeoutOptions(read=0.2), stream_idle_timeout=None)),
            ("total", options.RequestOptions(stream_total_timeout=0.2, stream_idle_timeout=3)),
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
        token = options.CancelToken()
        exchange.respond(_delayed(2))
        async with api.with_streaming_response.request_raw(
            "GET",
            "https://example.com/cancel",
            options=options.RequestOptions(cancel_token=token),
        ) as response:
            timer = asyncio.get_running_loop().call_later(0.05, token.cancel)
            try:
                lines.append(
                    f"  async stream waiting token {(await aoutcome(response.read)).partition(' secondary ')[0]}"
                )
            finally:
                timer.cancel()
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
        token = options.CancelToken()
        exchange.respond(raw_response(200, b'{"ready":true}', "application/json"))
        saved = await api.request_raw(
            "GET", "https://example.com/saved", options=options.RequestOptions(cancel_token=token)
        )
        token.cancel()
        for action in ("read", "iter_bytes", "iter_raw_bytes", "raise_for_status"):
            token = options.CancelToken()
            body = _InterruptedBody(action=token.cancel)
            exchange.respond(_interrupted(body, 503 if action == "raise_for_status" else 200))
            async with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/cancel",
                options=options.RequestOptions(cancel_token=token, retry=options.RetryOptions(max_retries=0)),
            ) as response:
                if action.startswith("iter_"):
                    await arecord(lines, f"async stream cancel {action}", lambda: anext(getattr(response, action)()))
                else:
                    await arecord(lines, f"async stream cancel {action}", getattr(response, action))
            lines.append(f"  async stream cancel released {body.closes}")
        for primary in (False, True):
            limiter = _AsyncFailingLimiter()
            exchange.respond(raw_response(200, b"ready", "text/plain"))
            async with api.with_streaming_response.request_raw(
                "GET",
                "https://example.com/release",
                options=options.RequestOptions(limiter=limiter, max_stream_bytes=0 if primary else None),
            ) as response:
                read_body = (lambda: anext(response.iter_bytes())) if primary else response.read
                lines.append(f"  async stream permit failure primary={primary} {await aoutcome(read_body)}")
            lines.append(f"  async stream permit release count {limiter.released}")
    lines.append(
        f"  async buffered survives cancel and close {await saved.read()!r} {await saved.json()!r} {[part async for part in saved.iter_bytes()]!r}"
    )
    await _async_interruptions(package, lines)
    await _async_http2(package, lines)


async def _async_interruptions(package: ModuleType, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    transports = importlib.import_module(f"{package.__name__}.transports")
    headers = importlib.import_module(f"{package.__name__}.responses").HeadersView([("content-type", "text/plain")])
    adapter = AsyncAdapter(transports, lines)
    async with package.AsyncClient(transport_adapter=adapter) as api:
        for action in ("read", "iter_bytes", "raise_for_status", "aclose"):
            original, closing = Stop("read"), Stop("close")
            response = _AsyncInterruptedResponse(
                lines, 503 if action == "raise_for_status" else 200, headers, (b"partial", original)
            )
            response.cleanup = closing
            adapter.replies.append(lambda request, context: response)
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
            except Stop as error:
                lines.append(
                    f"  async stream interruption {action} original {error is (closing if action == 'aclose' else original)} released {response.closes}"
                )
        original = Stop("read")
        gated = _GatedResponse(lines, headers, original)
        adapter.replies.append(lambda request, context: gated)
        async with api.with_streaming_response.request_raw("GET", "https://example.com/cleanup-cancel") as response:
            reader = asyncio.create_task(response.read())
            await gated.entered.wait()
            reader.cancel()
            try:
                await reader
            except Stop as error:
                lines.append(f"  async stream cleanup cancellation original {error is original}")
            finally:
                gated.released.set()
    lines.append(f"  async stream retained cleanup released {gated.closes}")


async def _async_http2(package: ModuleType, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    server = Http2Fixture(0)
    try:
        http = httpx2.AsyncClient(http1=False, http2=True, verify=server.client_context, trust_env=False)
        async with package.AsyncClient(http_client=http) as api:
            warmed = await api.request_raw("GET", server.url, options=options.RequestOptions(total_timeout=10))
            lines.append(f"  HTTP2 async warm connection {await warmed.read()!r}")
            server.delay = 1.5
            async with api.with_streaming_response.request_raw(
                "GET",
                server.url,
                options=options.RequestOptions(total_timeout=1, stream_idle_timeout=3),
            ) as response:
                await arecord(lines, "HTTP2 async stream acquisition released", response.read)
        lines.append(f"  HTTP2 async protocols {server.protocols!r} requests {server.requests}")
    finally:
        server.stop()
