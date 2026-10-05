"""Observe complete retry calls, response ownership, and interruptible waits through generated clients."""

from __future__ import annotations

import asyncio
import importlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, ExitStack
from typing import TYPE_CHECKING

import httpx2

from tests.data.python.client_body_replay import _Attempt, _Factory
from tests.data.python.client_limiters import _AsyncSemaphoreLimiter, _SemaphoreLimiter
from tests.data.python.client_runtime import Exchange, arecord, failing, injected, raw_response, record, run
from tests.data.python.client_transports import Adapter, AsyncAdapter, Response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from types import ModuleType


class _Events:
    def __init__(
        self,
        *,
        fail: str | None = None,
        scheduled: Callable[[], None] | None = None,
        ended: Callable[[], None] | None = None,
    ) -> None:
        self.fail = fail
        self.ended = ended
        self.scheduled = scheduled
        self.values: list[tuple[object, ...]] = []
        self.ids: list[object] = []

    def on_event(self, event: object) -> None:
        name = getattr(event, "name", None)
        self.values.append(
            tuple(
                getattr(event, key)
                for key in (
                    "name",
                    "attempt_index",
                    "status",
                    "sent",
                    "attempt_count",
                    "retry_reason",
                    "outcome",
                )
            )
        )
        self.ids.append(getattr(event, "call_id", None))
        if name == "retry_scheduled" and self.scheduled is not None:
            self.scheduled()
        if name == "attempt_end" and self.ended is not None:
            self.ended()
        if name == self.fail:
            message = "injected retry hook failure"
            raise RuntimeError(message)


class _Broken(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    def __init__(self, *, read: bool, close: bool | BaseException = False) -> None:
        self.read_failure = read
        self.close_failure = close
        self.closes = 0

    def __iter__(self) -> Iterator[bytes]:
        yield b"partial"
        if self.read_failure:
            message = "injected truncated response"
            raise httpx2.ReadError(message)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for part in self:
            yield part

    def close(self) -> None:
        self.closes += 1
        if isinstance(self.close_failure, BaseException):
            raise self.close_failure
        if self.close_failure:
            message = "injected response close failure"
            raise RuntimeError(message)

    async def aclose(self) -> None:
        self.close()


class _Stop(BaseException):
    pass


class _ClosingResponse(Response):
    def __init__(self, responses: ModuleType, failure: BaseException) -> None:
        super().__init__([], 200, responses.HeadersView((("content-type", "text/plain"),)), (b"held",))
        self.failure = failure
        self.closes = 0

    def close(self) -> None:
        self.closes += 1
        raise self.failure


class _AsyncClosingResponse(_ClosingResponse):
    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        yield b"held"

    async def aclose(self) -> None:
        self.close()


class _RewindFile(io.BytesIO):
    def __init__(self, failure: BaseException) -> None:
        super().__init__(b"body")
        self.failure = failure
        self.armed = False
        self.calls = 0

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if self.armed:
            self.calls += 1
            if self.calls <= 2:
                raise self.failure
            message = "finite guard against pre-send retry"
            raise RuntimeError(message)
        return super().seek(offset, whence)


class _ArmRewind:
    def __init__(self, file: _RewindFile, event: str) -> None:
        self.file = file
        self.event = event

    def on_event(self, event: object) -> None:
        if getattr(event, "name", None) == self.event:
            self.file.armed = True


def _fault(body: _Broken, status: int = 200) -> Callable[[httpx2.Request], httpx2.Response]:
    return injected(lambda _: httpx2.Response(status, headers={"Content-Type": "text/plain"}, stream=body))


def _error(error: BaseException) -> tuple[object, ...]:
    return (
        type(error).__name__,
        getattr(error, "retry_stop_reason", None),
        getattr(error, "attempt_count", None),
        getattr(error, "phase", None),
        tuple(type(item).__name__ for item in getattr(error, "secondary_errors", ())),
    )


def _capture(call: Callable[[], object]) -> tuple[object, ...]:
    try:
        result = call()
    except BaseException as error:  # noqa: BLE001
        return _error(error)
    info = getattr(result, "info", None)
    return (
        getattr(result, "data", result),
        getattr(info, "attempt_count", None),
    )


async def _acapture(call: Callable[[], Awaitable[object]]) -> tuple[object, ...]:
    try:
        result = await call()
    except BaseException as error:  # noqa: BLE001
        return _error(error)
    info = getattr(result, "info", None)
    return (
        getattr(result, "data", result),
        getattr(info, "attempt_count", None),
    )


def _report(lines: list[str], events: _Events, exchange: Exchange) -> None:
    lines.append(f"    events={events.values!r} one-call={len(set(events.ids)) == 1} queued={len(exchange.responders)}")
    events.values.clear()
    events.ids.clear()
    exchange.responders.clear()


def retry_calls(package: ModuleType, lines: list[str]) -> None:
    """Exercise released candidates, saved raw responses, and cooperative wait termination."""
    options = importlib.import_module(f"{package.__name__}.options")
    exchange, events = Exchange([]), _Events()
    config = options.ClientOptions(retry=options.RetryOptions(initial_delay=0), hooks=(events,))
    with exchange.client() as native, package.Client(http_client=native, options=config) as api:
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"done", "text/plain"))
        record(lines, "typed status retry", lambda: _capture(api.retry.with_response.get_safe))
        _report(lines, events, exchange)
        body = _Broken(read=True)
        exchange.respond(_fault(body), raw_response(200, b"complete", "text/plain"))
        record(lines, "typed read retry", lambda: _capture(api.retry.with_response.get_safe))
        record(lines, "typed read failed response closes", lambda body=body: body.closes)
        _report(lines, events, exchange)
        body = _Broken(read=True)
        exchange.respond(_fault(body), raw_response(200, b"buffered", "text/plain"))
        saved = api.retry.with_raw_response.get_safe()
        record(
            lines,
            "buffered raw retry",
            lambda: (saved.read(), saved.info.attempt_count),
        )
        record(lines, "buffered raw repeated status", lambda: (saved.raise_for_status(), saved.raise_for_status()))
        record(lines, "buffered read failed response closes", lambda body=body: body.closes)
        _report(lines, events, exchange)
        body = _Broken(read=True)
        exchange.respond(_fault(body), raw_response(200, b"unused", "text/plain"))
        with api.retry.with_streaming_response.get_safe() as response:
            record(lines, "post-handoff read never retries", lambda: _capture(response.read))
        record(lines, "stream failed response closes", lambda body=body: body.closes)
        _report(lines, events, exchange)
        for label, retries in (("open", 0), ("partial", 2)):
            exchange.respond(*(raw_response(503, b"busy", "text/plain") for _ in range(retries + 1)))
            request = options.RequestOptions(retry=options.RetryOptions(max_retries=retries))
            with api.retry.with_streaming_response.get_safe(options=request) as response:
                if label == "partial":
                    chunks = response.iter_bytes()
                    record(lines, "partial raw bytes", lambda chunks=chunks: next(chunks))
                record(lines, f"stream status reason {label}", lambda: _capture(response.raise_for_status))
                record(lines, f"stream repeated status reason {label}", lambda: _capture(response.raise_for_status))
            _report(lines, events, exchange)
        for mode in ("typed", "buffered", "stream"):
            token = options.CancelToken()
            terminal = _Events(ended=token.cancel)
            view = api.with_options(options.RequestOptions(cancel_token=token, hooks=(terminal,)))
            exchange.respond(raw_response(200, b"done", "text/plain"))

            def completed(mode: str = mode, view: object = view) -> object:
                if mode == "typed":
                    return view.retry.with_response.get_safe()
                if mode == "buffered":
                    return view.retry.with_raw_response.get_safe()
                with view.retry.with_streaming_response.get_safe() as response:
                    return response.info.status_code

            record(lines, f"terminal hook cancellation {mode}", lambda: _capture(completed))
            _report(lines, terminal, exchange)
        for name in ("response_headers", "attempt_end", "retry_scheduled"):
            events.fail = name
            exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
            record(lines, f"stop hook {name}", lambda: _capture(api.retry.with_response.get_safe))
            _report(lines, events, exchange)
        events.fail = None
        body = _Broken(read=False, close=True)
        exchange.respond(_fault(body, 503), raw_response(200, b"unused", "text/plain"))
        record(lines, "failed cleanup stops status retry", lambda: _capture(api.retry.with_response.get_safe))
        record(lines, "failed cleanup count", lambda body=body: body.closes)
        _report(lines, events, exchange)
        _close_interruptions(api, events, exchange, lines)
    _waits(package, options, lines)
    _resource_interruptions(package, options, lines)
    _handles(package, options, lines)
    _presend(package, options, lines)
    _key_inheritance(package, options, lines)
    run(lambda: _async_calls(package, options, lines))


def _waits(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for stop in ("token", "view", "owner"):
        _wait(package, options, lines, stop)


def _close_interruptions(api: object, events: _Events, exchange: Exchange, lines: list[str]) -> None:
    for error_type in (asyncio.CancelledError, _Stop, KeyboardInterrupt, SystemExit):
        interrupted = error_type("native retry close")
        interrupted.__dict__["__notes__"] = ["original close note"]
        body = _Broken(read=False, close=interrupted)
        exchange.respond(_fault(body, 503), raw_response(200, b"unused", "text/plain"))
        try:
            api.retry.with_response.get_safe()
        except BaseException as error:  # noqa: BLE001
            observed = type(error).__name__, error is interrupted, error.args, getattr(error, "__notes__", ())
        else:
            observed = ("returned",)
        record(
            lines,
            f"native retry close {error_type.__name__}",
            lambda observed=observed, body=body: (observed, body.closes),
        )
        _report(lines, events, exchange)


def _resource_interruptions(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    for phase in ("send", "response", "ordinary response"):
        first, second = _Stop("first cleanup interruption"), _Stop("permit interruption")
        limiter = _SemaphoreLimiter(release_failure=second)
        attempt = _Attempt(failure=first if phase == "send" else None)
        body = _Broken(read=False, close=first if phase == "response" else True)
        exchange, events = Exchange([]), _Events()
        exchange.respond(
            failing(httpx2.ReadError) if phase == "send" else _fault(body, 503),
            raw_response(200, b"unused", "text/plain"),
        )
        config = options.ClientOptions(limiter=limiter, retry=options.RetryOptions(initial_delay=0), hooks=(events,))
        with exchange.client() as native, package.Client(http_client=native, options=config) as api:
            try:
                api.retry.post_idempotent(body=bodies.BodyFactory(_Factory((attempt,))))
            except BaseException as error:  # noqa: BLE001
                expected = second if phase == "ordinary response" else first
                observed = type(error).__name__, error is expected, error.args, getattr(error, "__notes__", ())
            else:
                observed = ("returned",)
        record(
            lines,
            f"drained interrupted {phase}",
            lambda observed=observed, attempt=attempt, body=body, limiter=limiter: (
                observed,
                attempt.closes,
                body.closes,
                limiter.usage.releases,
                limiter.usage.active,
            ),
        )
        _report(lines, events, exchange)


def _handles(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("transports", "responses")
    )
    primary = _Stop("first handle interruption")
    held = [_ClosingResponse(responses, failure) for failure in (ValueError(), primary, _Stop("later handle"))]
    adapter = Adapter(transports, [])
    adapter.replies.extend(lambda _request, _context, response=response: response for response in held)
    closed: list[str] = []
    close_failure = OSError("adapter close failure")

    def close_adapter() -> None:
        closed.append("adapter")
        raise close_failure

    adapter.close = close_adapter
    api = package.Client(
        transport_adapter=transports.OwnedTransportAdapter(adapter),
        options=options.ClientOptions(cleanup_timeout=0.001),
    )
    with ExitStack() as stack:
        for _ in held:
            stack.enter_context(api.with_streaming_response.request_raw("GET", "https://close.example/"))
        try:
            api.close()
        except BaseException as error:  # noqa: BLE001
            observed = type(error).__name__, error is primary, error.args, getattr(error, "__notes__", ())
        else:
            observed = ("returned",)
    record(lines, "close drains every handle", lambda: (observed, [response.closes for response in held], closed))
    api.close()
    record(lines, "drained client close once", lambda: [response.closes for response in held] + [len(closed)])


def _presend(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    bodies, errors = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "errors"))
    for stage in ("call_start", "retry_scheduled"):
        failure = errors.APITimeoutError(
            effective_timeout=1.0, phase="read", delivery_state=errors.DeliveryState.NOT_SENT
        )
        file = _RewindFile(failure)
        exchange, events = Exchange([]), _Events()
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        config = options.ClientOptions(
            retry=options.RetryOptions(initial_delay=0),
            total_timeout=None if stage == "call_start" else 0.5,
            hooks=(_ArmRewind(file, stage), events),
        )
        with exchange.client() as native, package.Client(http_client=native, options=config) as api:
            try:
                api.retry.post_idempotent(body=bodies.FileBody(file))
            except BaseException as error:  # noqa: BLE001
                observed = _error(error), error is failure, getattr(error, "cause", None) is failure, file.calls
            else:
                observed = ("returned",)
        record(lines, f"pre-send failure {stage}", lambda observed=observed: observed)
        _report(lines, events, exchange)
        file.close()


def _wait(package: ModuleType, options: ModuleType, lines: list[str], stop: str) -> None:
    token = options.CancelToken() if stop == "token" else None
    exchange = Exchange([])
    timers: list[threading.Timer] = []
    failures: list[str] = []

    def scheduled() -> None:
        def close() -> None:
            try:
                if token is not None:
                    token.cancel()
                elif stop == "view":
                    view.close()
                else:
                    owner.close()
            except BaseException as error:  # noqa: BLE001
                failures.append(type(error).__name__)

        timer = threading.Timer(0.01, close)
        timers.append(timer)
        timer.start()

    events = _Events(scheduled=scheduled)
    config = options.ClientOptions(
        retry=options.RetryOptions(initial_delay=0.2, jitter="none"),
        hooks=(events,),
        cancel_token=token,
        total_timeout=2,
        cleanup_timeout=0.5,
    )
    with exchange.client() as native, package.Client(http_client=native, options=config) as owner:
        view = owner.with_options(options.RequestOptions())
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        record(lines, f"sync wait {stop}", lambda: _capture(view.retry.with_response.get_safe))
        for timer in timers:
            timer.join(1)
        record(lines, "sync wait cleanup", lambda: (all(not timer.is_alive() for timer in timers), failures))
        _report(lines, events, exchange)


async def _async_calls(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange, events = Exchange([]), _Events()
    config = options.ClientOptions(retry=options.RetryOptions(initial_delay=0), hooks=(events,))
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
        for label, raw in (("typed", False), ("buffered", True)):
            body = _Broken(read=True)
            exchange.respond(_fault(body), raw_response(200, b"complete", "text/plain"))
            if raw:
                saved = await api.retry.with_raw_response.get_safe()
                record(
                    lines,
                    f"async {label} read retry",
                    lambda saved=saved: (
                        saved.body_bytes,
                        saved.info.attempt_count,
                    ),
                )
            else:
                await arecord(lines, f"async {label} read retry", lambda: _acapture(api.retry.with_response.get_safe))
            record(lines, "async failed response closes", lambda body=body: body.closes)
            _report(lines, events, exchange)
        body = _Broken(read=True)
        exchange.respond(_fault(body), raw_response(200, b"unused", "text/plain"))
        async with api.retry.with_streaming_response.get_safe() as response:
            await arecord(lines, "async post-handoff never retries", lambda: _acapture(response.read))
        _report(lines, events, exchange)
        for label, retries in (("open", 0), ("partial", 2)):
            exchange.respond(*(raw_response(503, b"busy", "text/plain") for _ in range(retries + 1)))
            request = options.RequestOptions(retry=options.RetryOptions(max_retries=retries))
            async with api.retry.with_streaming_response.get_safe(options=request) as response:
                if label == "partial":
                    chunks = response.iter_bytes()
                    await arecord(lines, "async partial raw bytes", lambda chunks=chunks: anext(chunks))
                await arecord(
                    lines, f"async stream status reason {label}", lambda: _acapture(response.raise_for_status)
                )
                await arecord(
                    lines, f"async repeated status reason {label}", lambda: _acapture(response.raise_for_status)
                )
            _report(lines, events, exchange)
        for name in ("attempt_end", "retry_scheduled"):
            events.fail = name
            exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
            await arecord(lines, f"async stop hook {name}", lambda: _acapture(api.retry.with_response.get_safe))
            _report(lines, events, exchange)
        events.fail = None
        body = _Broken(read=False, close=True)
        exchange.respond(_fault(body, 503), raw_response(200, b"unused", "text/plain"))
        await arecord(lines, "async failed cleanup stops retry", lambda: _acapture(api.retry.with_response.get_safe))
        _report(lines, events, exchange)
        await _async_close_interruptions(api, events, exchange, lines)
    await _async_waits(package, options, lines)
    await _async_resource_interruptions(package, options, lines)
    await _async_handles(package, options, lines)
    await _async_presend(package, options, lines)
    await _async_key_inheritance(package, options, lines)


async def _async_close_interruptions(api: object, events: _Events, exchange: Exchange, lines: list[str]) -> None:
    for error_type in (asyncio.CancelledError, _Stop, KeyboardInterrupt, SystemExit):
        interrupted = error_type("native retry close")
        interrupted.__dict__["__notes__"] = ["original close note"]
        body = _Broken(read=False, close=interrupted)
        exchange.respond(_fault(body, 503), raw_response(200, b"unused", "text/plain"))
        try:
            await api.retry.with_response.get_safe()
        except BaseException as error:  # noqa: BLE001
            observed = type(error).__name__, error is interrupted, error.args, getattr(error, "__notes__", ())
        else:
            observed = ("returned",)
        record(
            lines,
            f"async native retry close {error_type.__name__}",
            lambda observed=observed, body=body: (observed, body.closes),
        )
        _report(lines, events, exchange)


async def _async_resource_interruptions(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    for phase in ("send", "response", "ordinary response"):
        first, second = _Stop("first cleanup interruption"), _Stop("permit interruption")
        limiter = _AsyncSemaphoreLimiter(release_failure=second)
        attempt = _Attempt(failure=first if phase == "send" else None)
        body = _Broken(read=False, close=first if phase == "response" else True)
        exchange, events = Exchange([]), _Events()
        exchange.respond(
            failing(httpx2.ReadError) if phase == "send" else _fault(body, 503),
            raw_response(200, b"unused", "text/plain"),
        )
        config = options.ClientOptions(limiter=limiter, retry=options.RetryOptions(initial_delay=0), hooks=(events,))
        async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
            try:
                await api.retry.post_idempotent(body=bodies.AsyncBodyFactory(_Factory((attempt,)).async_call))
            except BaseException as error:  # noqa: BLE001
                expected = second if phase == "ordinary response" else first
                observed = type(error).__name__, error is expected, error.args, getattr(error, "__notes__", ())
            else:
                observed = ("returned",)
        record(
            lines,
            f"async drained interrupted {phase}",
            lambda observed=observed, attempt=attempt, body=body, limiter=limiter: (
                observed,
                attempt.closes,
                body.closes,
                limiter.usage.releases,
                limiter.usage.active,
            ),
        )
        _report(lines, events, exchange)


async def _async_handles(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("transports", "responses")
    )
    primary = asyncio.CancelledError("first handle interruption")
    held = [_AsyncClosingResponse(responses, failure) for failure in (ValueError(), primary, _Stop("later handle"))]
    adapter = AsyncAdapter(transports, [])
    adapter.replies.extend(lambda _request, _context, response=response: response for response in held)
    finished = asyncio.Event()
    closed: list[str] = []
    close_failure = OSError("adapter close failure")

    def close_adapter() -> None:
        closed.append("adapter")
        finished.set()
        raise close_failure

    adapter.close = close_adapter
    api = package.AsyncClient(
        transport_adapter=transports.OwnedTransportAdapter(adapter),
        options=options.ClientOptions(cleanup_timeout=0.001),
    )
    async with AsyncExitStack() as stack:
        for _ in held:
            await stack.enter_async_context(api.with_streaming_response.request_raw("GET", "https://close.example/"))
        observed: tuple[object, ...] = ("returned",)
        for _ in range(2):
            try:
                await api.aclose()
            except BaseException as error:  # noqa: BLE001
                observed = type(error).__name__, error is primary, error.args, getattr(error, "__notes__", ())
            await finished.wait()
    record(
        lines,
        "async close drains every handle",
        lambda: (observed, [response.closes for response in held], closed),
    )


async def _async_presend(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    bodies, errors = (importlib.import_module(f"{package.__name__}.{name}") for name in ("bodies", "errors"))
    for stage in ("call_start", "retry_scheduled"):
        failure = errors.APITimeoutError(
            effective_timeout=1.0, phase="read", delivery_state=errors.DeliveryState.NOT_SENT
        )
        file = _RewindFile(failure)
        exchange, events = Exchange([]), _Events()
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        config = options.ClientOptions(
            retry=options.RetryOptions(initial_delay=0),
            total_timeout=None if stage == "call_start" else 0.5,
            hooks=(_ArmRewind(file, stage), events),
        )
        async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as api:
            try:
                await api.retry.post_idempotent(body=bodies.AsyncFileBody(file))
            except BaseException as error:  # noqa: BLE001
                observed = _error(error), error is failure, getattr(error, "cause", None) is failure, file.calls
            else:
                observed = ("returned",)
        record(lines, f"async pre-send failure {stage}", lambda observed=observed: observed)
        _report(lines, events, exchange)
        file.close()


async def _async_waits(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for stop in ("token", "view", "native"):
        await _async_wait(package, options, lines, stop)


async def _async_wait(package: ModuleType, options: ModuleType, lines: list[str], stop: str) -> None:
    ready = asyncio.Event()
    token = options.CancelToken() if stop == "token" else None
    events = _Events(scheduled=ready.set)
    exchange = Exchange([])
    config = options.ClientOptions(
        retry=options.RetryOptions(initial_delay=0.2, jitter="none"),
        hooks=(events,),
        cancel_token=token,
        total_timeout=2,
        cleanup_timeout=0.5,
    )
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as owner:
        view = owner.with_options(options.RequestOptions())
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))

        async def observed() -> tuple[object, ...]:
            try:
                await view.retry.get_safe()
            except asyncio.CancelledError as error:
                return type(error).__name__, error.args
            except Exception as error:  # noqa: BLE001
                return _error(error)
            return ("returned",)

        task = asyncio.create_task(observed())
        await ready.wait()
        await asyncio.sleep(0.01)
        if token is not None:
            token.cancel()
        elif stop == "view":
            await view.aclose()
        else:
            task.cancel("retry wait interrupted")
        await arecord(lines, f"async wait {stop}", lambda: task)
        _report(lines, events, exchange)


class _KeyReply:
    """Echo the idempotency header received by the real TLS server."""

    def __init__(self) -> None:
        self.keys: list[str | None] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        key = request.headers.get("Idempotency-Key")
        self.keys.append(key)
        return raw_response(200, ("absent" if key is None else key).encode(), "text/plain")(request)


def _key_cases(options: ModuleType, key: object) -> Iterator[tuple[str, bool, str, object]]:
    requests = (
        ("omitted", None),
        ("empty", options.RequestOptions()),
        ("UNSET", options.RequestOptions(idempotency_key=options.UNSET)),
        ("None", options.RequestOptions(idempotency_key=None)),
        ("override", options.RequestOptions(idempotency_key=options.IdempotencyKey("call-key"))),
        ("same object", options.RequestOptions(idempotency_key=key)),
    )
    for declared in (True, False):
        surfaces = ("typed", "response", "buffered", "stream")
        if not declared:
            surfaces += ("request raw", "request stream")
        for surface in surfaces:
            for label, request in requests:
                yield surface, declared, label, request


def _key_call(api: object, surface: str, declared: bool, request: object) -> object:
    keywords = {} if request is None else {"options": request}
    operation = "post_keyed" if declared else "get_safe"
    if surface == "typed":
        return getattr(api.retry, operation)(**keywords)
    if surface == "response":
        return getattr(api.retry.with_response, operation)(**keywords).data
    if surface == "buffered":
        return getattr(api.retry.with_raw_response, operation)(**keywords).read()
    if surface == "stream":
        with getattr(api.retry.with_streaming_response, operation)(**keywords) as response:
            return response.read()
    if surface == "request raw":
        return api.request_raw("GET", "https://api.example.com/safe", **keywords).read()
    with api.with_streaming_response.request_raw("GET", "https://api.example.com/safe", **keywords) as response:
        return response.read()


async def _async_key_call(api: object, surface: str, declared: bool, request: object) -> object:
    keywords = {} if request is None else {"options": request}
    operation = "post_keyed" if declared else "get_safe"
    if surface == "typed":
        return await getattr(api.retry, operation)(**keywords)
    if surface == "response":
        return (await getattr(api.retry.with_response, operation)(**keywords)).data
    if surface == "buffered":
        response = await getattr(api.retry.with_raw_response, operation)(**keywords)
        return await response.read()
    if surface == "stream":
        async with getattr(api.retry.with_streaming_response, operation)(**keywords) as response:
            return await response.read()
    if surface == "request raw":
        response = await api.request_raw("GET", "https://api.example.com/safe", **keywords)
        return await response.read()
    async with api.with_streaming_response.request_raw("GET", "https://api.example.com/safe", **keywords) as response:
        return await response.read()


def _key_inheritance(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange, reply = Exchange([]), _KeyReply()
    client_key, view_key = options.IdempotencyKey("client-key"), options.IdempotencyKey("view-key")
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=options.ClientOptions(idempotency_key=client_key)) as api,
        api.with_options(options.RequestOptions(idempotency_key=view_key)) as view,
    ):
        for layer, current, key in (("client", api, client_key), ("view", view, view_key)):
            for surface, declared, label, request in _key_cases(options, key):
                before = len(reply.keys)
                exchange.respond(reply)
                record(
                    lines,
                    f"key {layer} {surface} declared={declared} {label}",
                    lambda current=current, surface=surface, declared=declared, request=request: _key_call(
                        current, surface, declared, request
                    ),
                )
                record(lines, "key admission", lambda before=before: (reply.keys[before:], len(exchange.responders)))
                exchange.responders.clear()
        barrier = threading.Barrier(4)
        actions = (
            lambda: _key_call(api, "typed", True, None),
            lambda: _key_call(api, "stream", False, None),
            lambda: _key_call(view, "response", True, None),
            lambda: _key_call(view, "request raw", False, None),
        )

        def concurrent(action: Callable[[], object]) -> object:
            barrier.wait(timeout=5)
            return action()

        exchange.respond(reply, reply, reply, reply)
        with ThreadPoolExecutor(max_workers=4) as executor:
            record(lines, "concurrent inherited keys", lambda: tuple(executor.map(concurrent, actions)))
        record(lines, "concurrent key admission", lambda: (sorted(reply.keys[-4:], key=repr), len(exchange.responders)))
        for layer, current in (("client", api), ("view", view)):
            exchange.respond(reply)
            record(
                lines,
                f"key {layer} unchanged after mixed calls",
                lambda current=current: current.retry.post_keyed(),
            )


async def _async_key_inheritance(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange, reply = Exchange([]), _KeyReply()
    client_key, view_key = options.IdempotencyKey("client-key"), options.IdempotencyKey("view-key")
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native, options=options.ClientOptions(idempotency_key=client_key)) as api,
        api.with_options(options.RequestOptions(idempotency_key=view_key)) as view,
    ):
        for layer, current, key in (("client", api, client_key), ("view", view, view_key)):
            for surface, declared, label, request in _key_cases(options, key):
                before = len(reply.keys)
                exchange.respond(reply)
                await arecord(
                    lines,
                    f"async key {layer} {surface} declared={declared} {label}",
                    lambda current=current, surface=surface, declared=declared, request=request: _async_key_call(
                        current, surface, declared, request
                    ),
                )
                record(
                    lines, "async key admission", lambda before=before: (reply.keys[before:], len(exchange.responders))
                )
                exchange.responders.clear()
        exchange.respond(reply, reply, reply, reply)
        await arecord(
            lines,
            "async concurrent inherited keys",
            lambda: asyncio.gather(
                _async_key_call(api, "typed", True, None),
                _async_key_call(api, "stream", False, None),
                _async_key_call(view, "response", True, None),
                _async_key_call(view, "request raw", False, None),
            ),
        )
        record(
            lines,
            "async concurrent key admission",
            lambda: (sorted(reply.keys[-4:], key=repr), len(exchange.responders)),
        )
        for layer, current in (("client", api), ("view", view)):
            exchange.respond(reply)
            await arecord(
                lines,
                f"async key {layer} unchanged after mixed calls",
                lambda current=current: current.retry.post_keyed(),
            )
