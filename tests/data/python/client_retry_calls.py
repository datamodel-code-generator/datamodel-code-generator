"""Observe complete retry calls, response ownership, and interruptible waits through generated clients."""

from __future__ import annotations

import asyncio
import importlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, injected, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from types import ModuleType


class _Events:
    """Record each native send and response through the injected HTTP client's own event hooks."""

    def __init__(self, *, fail: bool = False, responded: Callable[[], None] | None = None) -> None:
        self.fail = fail
        self.responded = responded
        self.values: list[tuple[object, ...]] = []

    def request(self, request: httpx2.Request) -> None:
        self.values.append(("request", request.method, request.url.path))

    def response(self, response: httpx2.Response) -> None:
        self.values.append(("response", response.status_code))
        if self.responded is not None:
            self.responded()
        if self.fail:
            message = "injected response hook failure"
            raise RuntimeError(message)

    async def arequest(self, request: httpx2.Request) -> None:
        self.request(request)

    async def aresponse(self, response: httpx2.Response) -> None:
        self.response(response)

    def hooks(self) -> dict[str, list[Callable[..., object]]]:
        return {"request": [self.request], "response": [self.response]}

    def async_hooks(self) -> dict[str, list[Callable[..., object]]]:
        return {"request": [self.arequest], "response": [self.aresponse]}


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


class _RewindFile(io.BytesIO):
    def __init__(self, failure: BaseException) -> None:
        super().__init__(b"body")
        self.failure = failure
        self.armed = False
        self.calls = 0

    def arm(self) -> None:
        self.armed = True

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if self.armed:
            self.calls += 1
            if self.calls <= 2:
                raise self.failure
            message = "finite guard against pre-send retry"
            raise RuntimeError(message)
        return super().seek(offset, whence)


def _fault(body: _Broken, status: int = 200) -> Callable[[httpx2.Request], httpx2.Response]:
    return injected(lambda _: httpx2.Response(status, headers={"Content-Type": "text/plain"}, stream=body))


def _error(error: BaseException) -> tuple[object, ...]:
    return (
        type(error).__name__,
        getattr(error, "reason", None),
        getattr(error, "attempt_count", None),
        tuple(getattr(error, "__notes__", ())),
    )


def _secondary(error: BaseException) -> list[str]:
    return list(getattr(error, "__notes__", ()))


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
    lines.append(f"    events={events.values!r} queued={len(exchange.responders)}")
    events.values.clear()
    exchange.responders.clear()


def retry_calls(package: ModuleType, lines: list[str]) -> None:
    """Exercise released candidates, saved raw responses, and cooperative wait termination."""
    options = importlib.import_module(f"{package.__name__}.options")
    exchange, events = Exchange([]), _Events()
    config = options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
    with (
        exchange.client(event_hooks=events.hooks()) as native,
        package.Client(http_client=native, options=config) as api,
    ):
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"done", "text/plain"))
        record(lines, "typed status retry", lambda: _capture(api.retry.with_response.get_safe))
        _report(lines, events, exchange)
        body = _Broken(read=True)
        exchange.respond(_fault(body), raw_response(200, b"complete", "text/plain"))
        record(lines, "typed read failure never resent", lambda: _capture(api.retry.with_response.get_safe))
        record(lines, "typed read failed response closes", lambda body=body: body.closes)
        _report(lines, events, exchange)
        body = _Broken(read=True)
        exchange.respond(_fault(body), raw_response(200, b"buffered", "text/plain"))
        record(lines, "buffered read failure never resent", lambda: _capture(api.retry.with_raw_response.get_safe))
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
        events.fail = True
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        record(lines, "stop response hook", lambda: _capture(api.retry.with_response.get_safe))
        _report(lines, events, exchange)
        events.fail = False
        body = _Broken(read=False, close=True)
        exchange.respond(_fault(body, 503), raw_response(200, b"unused", "text/plain"))
        record(lines, "failed cleanup stops status retry", lambda: _capture(api.retry.with_response.get_safe))
        record(lines, "failed cleanup count", lambda body=body: body.closes)
        _report(lines, events, exchange)
        _close_interruptions(api, events, exchange, lines)
    _presend(package, options, lines)
    _key_inheritance(package, options, lines)
    run(lambda: _async_calls(package, options, lines))


def _close_interruptions(api: object, events: _Events, exchange: Exchange, lines: list[str]) -> None:
    for error_type in (asyncio.CancelledError, _Stop, KeyboardInterrupt, SystemExit):
        interrupted = error_type("native retry close")
        interrupted.__dict__["__notes__"] = ["original close note"]
        body = _Broken(read=False, close=interrupted)
        exchange.respond(_fault(body, 503), raw_response(200, b"unused", "text/plain"))
        try:
            api.retry.with_response.get_safe()
        except BaseException as error:  # noqa: BLE001
            observed = (
                type(error).__name__,
                error is interrupted,
                error.args,
                getattr(error, "__notes__", ()),
                _secondary(error),
            )
        else:
            observed = ("returned",)
        record(
            lines,
            f"native retry close {error_type.__name__}",
            lambda observed=observed, body=body: (observed, body.closes),
        )
        _report(lines, events, exchange)


def _presend(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    for stage in ("before call", "first response"):
        failure = errors.APITimeoutError(reason="phase_timeout")
        file = _RewindFile(failure)
        exchange, events = Exchange([]), _Events(responded=None if stage == "before call" else file.arm)
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        config = options.ClientOptions(
            retry=options.RetryOptions(initial_delay=0), total_timeout=None if stage == "before call" else 0.5
        )
        if stage == "before call":
            file.arm()
        with (
            exchange.client(event_hooks=events.hooks()) as native,
            package.Client(http_client=native, options=config) as api,
        ):
            try:
                api.retry.post_idempotent(body=file)
            except BaseException as error:  # noqa: BLE001
                observed = _error(error), error is failure, getattr(error, "cause", None) is failure, file.calls
            else:
                observed = ("returned",)
        record(lines, f"pre-send failure {stage}", lambda observed=observed: observed)
        _report(lines, events, exchange)
        file.close()


async def _async_calls(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange, events = Exchange([]), _Events()
    config = options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
    async with (
        exchange.async_client(event_hooks=events.async_hooks()) as native,
        package.AsyncClient(http_client=native, options=config) as api,
    ):
        for label, call in (
            ("typed", api.retry.with_response.get_safe),
            ("buffered", api.retry.with_raw_response.get_safe),
        ):
            body = _Broken(read=True)
            exchange.respond(_fault(body), raw_response(200, b"complete", "text/plain"))
            await arecord(lines, f"async {label} read failure never resent", lambda call=call: _acapture(call))
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
        events.fail = True
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        await arecord(lines, "async stop response hook", lambda: _acapture(api.retry.with_response.get_safe))
        _report(lines, events, exchange)
        events.fail = False
        body = _Broken(read=False, close=True)
        exchange.respond(_fault(body, 503), raw_response(200, b"unused", "text/plain"))
        await arecord(lines, "async failed cleanup stops retry", lambda: _acapture(api.retry.with_response.get_safe))
        _report(lines, events, exchange)
        await _async_close_interruptions(api, events, exchange, lines)
    await _async_wait(package, options, lines)
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
            observed = (
                type(error).__name__,
                error is interrupted,
                error.args,
                getattr(error, "__notes__", ()),
                _secondary(error),
            )
        else:
            observed = ("returned",)
        record(
            lines,
            f"async native retry close {error_type.__name__}",
            lambda observed=observed, body=body: (observed, body.closes),
        )
        _report(lines, events, exchange)


async def _async_presend(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    for stage in ("before call", "first response"):
        failure = errors.APITimeoutError(reason="phase_timeout")
        file = _RewindFile(failure)
        exchange, events = Exchange([]), _Events(responded=None if stage == "before call" else file.arm)
        exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
        config = options.ClientOptions(
            retry=options.RetryOptions(initial_delay=0), total_timeout=None if stage == "before call" else 0.5
        )
        if stage == "before call":
            file.arm()
        async with (
            exchange.async_client(event_hooks=events.async_hooks()) as native,
            package.AsyncClient(http_client=native, options=config) as api,
        ):
            try:
                await api.retry.post_idempotent(body=file)
            except BaseException as error:  # noqa: BLE001
                observed = _error(error), error is failure, getattr(error, "cause", None) is failure, file.calls
            else:
                observed = ("returned",)
        record(lines, f"async pre-send failure {stage}", lambda observed=observed: observed)
        _report(lines, events, exchange)
        file.close()


async def _async_wait(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Cancel the caller's task once its retryable response arrived, and observe the cancellation propagate.

    The in-process response closes without suspending the task, so the cancellation reaches its retry wait.
    """
    ready = asyncio.Event()
    events = _Events(responded=ready.set)
    exchange = Exchange([])
    config = options.ClientOptions(
        retry=options.RetryOptions(initial_delay=30, max_delay=30, jitter="none"), total_timeout=60
    )
    async with (
        exchange.async_client(event_hooks=events.async_hooks()) as native,
        package.AsyncClient(http_client=native, options=config) as api,
    ):
        exchange.respond(injected(raw_response(503, b"busy", "text/plain")), raw_response(200, b"unused", "text/plain"))

        async def observed() -> tuple[object, ...]:
            try:
                await api.retry.get_safe()
            except asyncio.CancelledError as error:
                return type(error).__name__, error.args
            except Exception as error:  # noqa: BLE001
                return _error(error)
            return ("returned",)

        task = asyncio.create_task(observed())
        await ready.wait()
        task.cancel("retry wait interrupted")
        await arecord(lines, "async wait native", lambda: task)
        _report(lines, events, exchange)


class _KeyReply:
    """Echo the idempotency header received by the real TLS server."""

    def __init__(self) -> None:
        self.keys: list[str | None] = []

    def __call__(self, request: httpx2.Request, *, status: int = 200) -> httpx2.Response:
        key = request.headers.get("Idempotency-Key")
        self.keys.append(key)
        return raw_response(status, ("absent" if key is None else key).encode(), "text/plain")(request)


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
        package.Client(
            http_client=native,
            options=options.ClientOptions(idempotency_key=client_key, retry=options.RetryOptions(initial_delay=0)),
        ) as api,
    ):
        view = api.with_options(options.RequestOptions(idempotency_key=view_key))
        for layer, current, key in (("client", api, client_key), ("view", view, view_key)):
            for surface, declared, label, request in _key_cases(options, key):
                before = len(reply.keys)
                exchange.respond(lambda request: reply(request, status=503), reply)
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
            return _capture(action)

        before = len(reply.keys)
        exchange.respond(reply, reply, reply, reply)
        with ThreadPoolExecutor(max_workers=4) as executor:
            record(lines, "concurrent inherited keys", lambda: tuple(executor.map(concurrent, actions)))
        record(
            lines, "concurrent key admission", lambda: (sorted(reply.keys[before:], key=repr), len(exchange.responders))
        )
        exchange.responders.clear()
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
        package.AsyncClient(
            http_client=native,
            options=options.ClientOptions(idempotency_key=client_key, retry=options.RetryOptions(initial_delay=0)),
        ) as api,
    ):
        view = api.with_options(options.RequestOptions(idempotency_key=view_key))
        for layer, current, key in (("client", api, client_key), ("view", view, view_key)):
            for surface, declared, label, request in _key_cases(options, key):
                before = len(reply.keys)
                exchange.respond(lambda request: reply(request, status=503), reply)
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
        before = len(reply.keys)
        exchange.respond(reply, reply, reply, reply)
        await arecord(
            lines,
            "async concurrent inherited keys",
            lambda: asyncio.gather(
                _acapture(lambda: _async_key_call(api, "typed", True, None)),
                _acapture(lambda: _async_key_call(api, "stream", False, None)),
                _acapture(lambda: _async_key_call(view, "response", True, None)),
                _acapture(lambda: _async_key_call(view, "request raw", False, None)),
            ),
        )
        record(
            lines,
            "async concurrent key admission",
            lambda: (sorted(reply.keys[before:], key=repr), len(exchange.responders)),
        )
        exchange.responders.clear()
        for layer, current in (("client", api), ("view", view)):
            exchange.respond(reply)
            await arecord(
                lines,
                f"async key {layer} unchanged after mixed calls",
                lambda current=current: current.retry.post_keyed(),
            )

    reply.keys.clear()
    exchange.responders.clear()
    exchange.respond(lambda request: reply(request, status=503), reply, reply)
    async with (
        exchange.async_client() as native,
        package.AsyncClient(
            http_client=native,
            options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0)),
        ) as automatic,
    ):
        await arecord(lines, "async automatic key retained through retry", automatic.retry.post_keyed)
        await arecord(lines, "async automatic key fresh next call", automatic.retry.post_keyed)
    record(
        lines,
        "async automatic key admission",
        lambda: (len(reply.keys), reply.keys[0] == reply.keys[1], reply.keys[1] != reply.keys[2]),
    )
