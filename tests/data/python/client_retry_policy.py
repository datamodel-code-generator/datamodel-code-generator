"""Report retry gates, vendor controls, timing, and retained keys through generated public clients."""

from __future__ import annotations

import importlib
import math
from functools import partial
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, failing, injected, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType


class _Statuses:
    """Record the status of each response the injected HTTP client receives, through its own response hook."""

    def __init__(self) -> None:
        self.values: list[int] = []

    def __call__(self, response: httpx2.Response) -> None:
        self.values.append(response.status_code)

    async def asynchronous(self, response: httpx2.Response) -> None:
        self(response)


class _Waits:
    """A fake monotonic clock that each recorded retry wait advances, so no wait passes in real time.

    As the injected HTTP client's response hook, it also lets `received` seconds pass when error headers arrive.
    """

    def __init__(self, *, received: float = 0.0) -> None:
        self.value = 100.0
        self.received = received
        self.delays: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.delays.append(round(duration, 12))
        self.value += duration

    def response(self, response: httpx2.Response) -> None:
        if response.status_code != 200:
            self.value += self.received


class _Draw:
    def __init__(self, value: float) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value


class _Elapsed(httpx2.ByteStream):
    """A response body whose close lets time pass on a fake clock after the client chose its retry wait."""

    def __init__(self, waits: _Waits, seconds: float) -> None:
        super().__init__(b"server payload")
        self.waits = waits
        self.seconds = seconds

    def close(self) -> None:
        self.waits.value += self.seconds


def _response(status: int, headers: tuple[tuple[str, str], ...] = ()) -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(
            status,
            headers=(("Content-Type", "text/plain"), *headers),
            stream=httpx2.ByteStream(b"server payload"),
        )

    return respond


def _elapsed(
    status: int, headers: tuple[tuple[str, str], ...], waits: _Waits, seconds: float
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return an in-process response whose close lets seconds pass on the fake clock."""
    return injected(
        lambda _: httpx2.Response(
            status, headers=(("Content-Type", "text/plain"), *headers), stream=_Elapsed(waits, seconds)
        )
    )


def _outcome(call: Callable[[], object], *, error_type: type[Exception]) -> tuple[object, ...]:
    try:
        value = call()
    except error_type as error:
        info = getattr(error, "info", None)
        return (
            type(error).__name__,
            getattr(error, "attempt_count", None),
            getattr(error, "body_bytes", None),
            getattr(error, "body", None),
            getattr(error, "field_path", None),
            getattr(error, "truncated", None),
            getattr(info, "status_code", None),
        )
    info = getattr(value, "info", None)
    return (
        getattr(value, "data", None),
        getattr(info, "attempt_count", None),
    )


def _gates(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    received = _Statuses()
    with (
        exchange.client(event_hooks={"response": [received]}) as native,
        package.Client(http_client=native, retry=options.RetryOptions(initial_delay=0)) as api,
    ):
        for label, method, statuses, request in (
            ("default succeeds", "get_safe", (503, 200), options.RequestOptions()),
            ("default exhausted", "get_safe", (503, 503, 503), options.RequestOptions()),
            ("excluded before disabled", "get_safe", (404,), options.RequestOptions(max_retries=0)),
            ("never before disabled", "get_never", (503,), options.RequestOptions(max_retries=0)),
            ("disabled", "get_safe", (503,), options.RequestOptions(max_retries=0)),
            ("unsafe", "post_unsafe", (503,), options.RequestOptions()),
            ("declared idempotent", "post_idempotent", (503, 200), options.RequestOptions()),
            (
                "explicit statuses replace",
                "get_safe",
                (503,),
                options.RequestOptions(retry=options.RetryOptions(statuses={409})),
            ),
            (
                "explicit conflict status",
                "get_safe",
                (409, 200),
                options.RequestOptions(retry=options.RetryOptions(statuses={409})),
            ),
        ):
            exchange.responders.clear()
            received.values.clear()
            exchange.respond(*(_response(status) for status in statuses))
            record(
                lines,
                label,
                lambda method=method, request=request: outcome(
                    lambda: getattr(api.retry.with_response, method)(options=request)
                ),
            )
            lines.append(f"    statuses={received.values!r} unused={len(exchange.responders)}")


def _hints(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(http_client=native, retry=options.RetryOptions(initial_delay=0)) as api,
    ):
        for label, status, values, request in (
            ("true adds404", 404, ("TrUe",), options.RequestOptions()),
            ("ASCII whitespace", 404, (" \ttrue \t",), options.RequestOptions()),
            ("false wins", 503, ("true", "FALSE", "true"), options.RequestOptions()),
            ("false before disabled", 503, ("false",), options.RequestOptions(max_retries=0)),
            ("excluded before false", 404, ("false",), options.RequestOptions()),
            ("invalid hint", 404, ("yes", "1"), options.RequestOptions()),
            ("false then true", 503, ("false", "true"), options.RequestOptions()),
            ("true ignores200", 200, ("true",), options.RequestOptions()),
            ("true ignores302", 302, ("true",), options.RequestOptions()),
            ("true ignores401", 401, ("true",), options.RequestOptions()),
            ("true ignores403", 403, ("true",), options.RequestOptions()),
            ("true ignores407", 407, ("true",), options.RequestOptions()),
            (
                "hint explicitly disabled",
                404,
                ("true",),
                options.RequestOptions(retry=options.RetryOptions(should_retry_header=None)),
            ),
            (
                "hint case-insensitive binding",
                404,
                ("true",),
                options.RequestOptions(retry=options.RetryOptions(should_retry_header="x-retry-permitted")),
            ),
        ):
            exchange.responders.clear()
            exchange.respond(_response(status, tuple(("X-Retry-Permitted", value) for value in values)), _response(200))
            record(
                lines,
                label,
                lambda request=request: outcome(lambda: api.retry.with_response.get_vendor(options=request)),
            )
            lines.append(f"    unused={len(exchange.responders)}")
        for field in ("retry_after_ms_header", "should_retry_header"):
            for method, name in (("get_safe", "X-Unbound"), ("get_vendor", "X-Wrong")):
                request = options.RequestOptions(retry=options.RetryOptions(**{field: name}))
                record(
                    lines,
                    f"binding {method} {field}",
                    lambda method=method, request=request: outcome(
                        lambda: getattr(api.retry.with_response, method)(options=request)
                    ),
                )


def _server_delays(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(http_client=native, retry=options.RetryOptions(initial_delay=0, max_retry_after=0.01)) as api,
    ):
        for label, values in (
            ("zero", ("0",)),
            ("positive", ("1",)),
            ("duplicates maximum", ("bad", "0", "2", "1")),
            ("HTTP date", ("Sun, 06 Nov 2094 08:49:37 GMT",)),
            ("past HTTP date", ("Sun, 06 Nov 1994 08:49:37 GMT",)),
            ("obsolete asctime", ("Sun Nov  6 08:49:37 1994",)),
            ("obsolete RFC850", ("Sunday, 06-Nov-94 08:49:37 GMT",)),
            ("RFC850 future window", ("Sunday, 06-Nov-74 08:49:37 GMT",)),
            ("unknown date timezone", ("Sun, 06 Nov 2094 08:49:37 BOGUS",)),
            ("date trailing junk", ("Sun, 06 Nov 2094 08:49:37 GMT ignored",)),
            ("date wrong case", ("sun, 06 Nov 2094 08:49:37 GMT",)),
            ("leap second", ("Sat, 31 Dec 2016 23:59:60 GMT",)),
            ("invalid second", ("Sat, 31 Dec 2016 23:59:61 GMT",)),
            ("fraction", ("0.5",)),
            ("negative", ("-1",)),
            ("positive sign", ("+1",)),
            ("exponent", ("1e2",)),
            ("nan", ("NaN",)),
            ("infinity", ("Infinity",)),
            ("overflow", ("9" * 400,)),
            ("invalid date", ("Sun, 32 Nov 2094 08:49:37 GMT",)),
        ):
            exchange.responders.clear()
            exchange.respond(_response(503, tuple(("Retry-After", value) for value in values)), _response(200))
            record(lines, f"delay {label}", lambda: outcome(api.retry.with_response.get_safe))
            lines.append(f"    unused={len(exchange.responders)}")
        for label, fields, retry in (
            (
                "vendor maximum",
                (("Retry-After", "0"), ("X-Retry-In-Ms", "0"), ("X-Retry-In-Ms", "11")),
                options.RetryOptions(),
            ),
            ("vendor zero overrides", (("Retry-After", "61"), ("X-Retry-In-Ms", "0")), options.RetryOptions()),
            ("vendor invalid falls back", (("Retry-After", "1"), ("X-Retry-In-Ms", "0.1")), options.RetryOptions()),
            ("vendor empty falls back", (("Retry-After", "1"),), options.RetryOptions()),
            (
                "vendor case binding",
                (("X-Retry-In-Ms", "0"),),
                options.RetryOptions(retry_after_ms_header="x-retry-in-ms"),
            ),
            ("server timing disabled", (("Retry-After", "61"),), options.RetryOptions(respect_retry_after=False)),
            (
                "vendor disabled",
                (("Retry-After", "0"), ("X-Retry-In-Ms", "61000")),
                options.RetryOptions(retry_after_ms_header=None),
            ),
        ):
            exchange.responders.clear()
            exchange.respond(_response(503, fields), _response(200))
            request = options.RequestOptions(retry=retry)
            record(
                lines,
                label,
                lambda request=request: outcome(lambda: api.retry.with_response.get_vendor(options=request)),
            )
            lines.append(f"    unused={len(exchange.responders)}")
        exchange.responders.clear()
        exchange.respond(_response(503, (("Retry-After", "2"),)))
        request = options.RequestOptions(total_timeout=1, retry=options.RetryOptions(max_retry_after=None))
        record(
            lines,
            "server exceeds deadline",
            lambda request=request: outcome(lambda: api.retry.with_response.get_safe(options=request)),
        )
        exchange.respond(_response(503))
        request = options.RequestOptions(
            total_timeout=1, retry=options.RetryOptions(initial_delay=2, max_delay=2, jitter="none")
        )
        record(
            lines,
            "backoff exceeds deadline",
            lambda request=request: outcome(lambda: api.retry.with_response.get_safe(options=request)),
        )


def _keys(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(http_client=native, retry=options.RetryOptions(initial_delay=0)) as api,
    ):
        for label, method, key in (
            ("caller key", "post_keyed", "stable-key"),
            ("header-only declaration", "post_key_only", "header-only"),
            ("key suppressed", "post_keyed", None),
            ("undeclared key", "post_unsafe", "no-target"),
        ):
            exchange.responders.clear()
            exchange.respond(_response(503), _response(200))
            request = options.RequestOptions(idempotency_key=key)
            record(
                lines,
                label,
                lambda method=method, request=request: outcome(
                    lambda: getattr(api.retry.with_response, method)(body=b"payload", options=request)
                ),
            )
            lines.append(f"    unused={len(exchange.responders)}")
        for label, key in (("safe method caller key", "safe-key"),):
            exchange.responders.clear()
            exchange.respond(_response(503), _response(200))
            request = options.RequestOptions(idempotency_key=key)
            record(
                lines,
                label,
                lambda request=request: outcome(lambda: api.retry.with_response.get_keyed_safe(options=request)),
            )
            lines.append(f"    unused={len(exchange.responders)}")

        exchange.responders.clear()
        exchange.respond(
            _response(503),
            lambda request: _response(200, (("X-Observed-Key", request.headers.get("Idempotency-Key", "")),))(request),
        )
        raw = api.retry.with_raw_response.post_key_only(body=b"automatic")
        wire_key = raw.info.headers.get("X-Observed-Key")
        parsed = None if wire_key is None else UUID(wire_key)
        record(lines, "automatic declared key", lambda: outcome(raw.raise_for_status))
        lines.append(
            f"    uuid4={parsed is not None and parsed.version == 4} "
            f"canonical={parsed is not None and str(parsed) == wire_key} "
            f"attempts={raw.info.attempt_count} "
            f"unused={len(exchange.responders)}"
        )


_UNSENT: Final = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)
_STARTED: Final = (
    httpx2.WriteError,
    httpx2.WriteTimeout,
    httpx2.ReadError,
    httpx2.ReadTimeout,
    httpx2.RemoteProtocolError,
)


def _broken() -> Iterator[bytes]:
    """Send a first chunk, then fail as the source breaks while the request is on the wire."""
    yield b"partial"
    message = "body source failed mid-send"
    raise OSError(message)


def _delivered(result: object) -> tuple[object, ...]:
    """Describe a delivery outcome: the error class and reason, attempts, and cause."""
    if not isinstance(result, BaseException):
        return getattr(result, "data", None), getattr(getattr(result, "info", None), "attempt_count", None)
    return (
        type(result).__name__,
        getattr(result, "reason", None),
        getattr(result, "attempt_count", None),
        type(getattr(result, "cause", None)).__name__,
    )


def _calls(api: Any, options: ModuleType) -> tuple[tuple[str, Callable[[], Any]], ...]:
    """Return the safe, keyed, unsafe, and never-retried operations a delivery failure is reported through."""
    key = options.RequestOptions(idempotency_key="delivery-key")
    return (
        ("get_safe", api.retry.with_response.get_safe),
        ("post_keyed", lambda: api.retry.with_response.post_keyed(body=b"payload", options=key)),
        ("post_unsafe", lambda: api.retry.with_response.post_unsafe(body=b"payload")),
        ("get_never", api.retry.with_response.get_never),
    )


def _delivery_cases(options: ModuleType) -> Iterator[tuple[str, type[httpx2.TransportError], dict[str, object]]]:
    """Yield each native failure with the view settings that leave one retry available."""
    view: dict[str, object] = {"max_retries": 1}
    for error in (*_UNSENT, *_STARTED):
        yield error.__name__, error, view
    yield "PoolTimeout enabled", httpx2.PoolTimeout, {**view, "retry": options.RetryOptions(retry_on_pool_timeout=True)}


def _delivery(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Classify native failures by class: only an unsent request may be resent, and only by an eligible operation."""
    exchange = Exchange([])
    retry = options.RetryOptions(initial_delay=0)
    with exchange.client() as native, package.Client(http_client=native, retry=retry) as api:
        for label, error, view in _delivery_cases(options):
            for name, call in _calls(api.with_options(**view), options):
                exchange.respond(failing(error), _response(200))
                lines.append(f"  native {label} {name} = {_delivered(_failed(call))} unused={len(exchange.responders)}")
                exchange.responders.clear()
    _started(package, options, lines)


async def _adelivery(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange([])
    retry = options.RetryOptions(initial_delay=0)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, retry=retry) as api:
        for label, error, view in _delivery_cases(options):
            for name, call in _calls(api.with_options(**view), options):
                exchange.respond(failing(error), _response(200))
                result = _delivered(await _afailed(call))
                lines.append(f"  async native {label} {name} = {result} unused={len(exchange.responders)}")
                exchange.responders.clear()
    await _astarted(package, options, lines)


def _dropped(request: httpx2.Request) -> httpx2.Response:
    """Fail on the server after it read the whole request, so the client loses the connection without a response."""
    del request
    message = "server lost the request after reading it"
    raise ConnectionResetError(message)


def _started(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Fail real TLS exchanges after their send started and observe that the server receives each request once."""
    exchange, opened = Exchange(lines), []

    def factory() -> Iterator[bytes]:
        opened.append("consumed")
        yield from _broken()

    config = {"max_retries": 1, "retry": options.RetryOptions(initial_delay=0)}
    with exchange.client() as native, package.Client(http_client=native, **config) as api:
        for name, call in _calls(api, options)[:2]:
            exchange.respond(_dropped, _response(200))
            record(lines, f"server dropped {name}", lambda call=call: _delivered(_failed(call)))
            lines.append(f"    unused={len(exchange.responders)}")
            exchange.responders.clear()
        exchange.respond(_response(200))
        key = options.RequestOptions(idempotency_key="delivery-key")
        record(
            lines,
            "body fails mid-send",
            lambda: _delivered(_failed(lambda: api.retry.post_keyed(body=factory(), options=key))),
        )
        lines.append(f"    opened={opened} unused={len(exchange.responders)}")
        exchange.responders.clear()
    exchange = Exchange(lines)
    pool = {**config, "timeout": httpx2.Timeout(5.0, pool=0.05)}
    with exchange.client(connections=1) as native, package.Client(http_client=native, **pool) as api:
        exchange.respond(_response(200))
        with api.retry.with_streaming_response.get_safe() as held:
            for enabled in (False, True):
                request = options.RequestOptions(retry=options.RetryOptions(retry_on_pool_timeout=enabled))
                record(
                    lines,
                    f"held pool retry_on_pool_timeout={enabled}",
                    lambda request=request: _delivered(_failed(lambda: api.retry.get_safe(options=request))),
                )
            record(lines, "held response still readable", held.read)


def _failed(call: Callable[[], object]) -> object:
    """Return a call's result, or the ordinary failure it raised."""
    try:
        return call()
    except Exception as error:  # noqa: BLE001
        return error


async def _afailed(call: Callable[[], Any]) -> object:
    """Return an async call's result, or the ordinary failure it raised."""
    try:
        return await call()
    except Exception as error:  # noqa: BLE001
        return error


async def _astarted(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange, opened = Exchange(lines), []

    async def factory() -> AsyncIterator[bytes]:  # noqa: RUF029
        opened.append("consumed")
        for chunk in _broken():
            yield chunk

    config = {"max_retries": 1, "retry": options.RetryOptions(initial_delay=0)}
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, **config) as api:
        for name, call in _calls(api, options)[:2]:
            exchange.respond(_dropped, _response(200))
            lines.extend((
                f"  async server dropped {name} = {_delivered(await _afailed(call))}",
                f"    unused={len(exchange.responders)}",
            ))
            exchange.responders.clear()
        exchange.respond(_response(200))
        key = options.RequestOptions(idempotency_key="delivery-key")
        body = factory()
        result = await _afailed(lambda: api.retry.post_keyed(body=body, options=key))
        lines.extend((
            f"  async body fails mid-send = {_delivered(result)}",
            f"    opened={opened} unused={len(exchange.responders)}",
        ))
        exchange.responders.clear()
    exchange = Exchange(lines)
    pool = {**config, "timeout": httpx2.Timeout(5.0, pool=0.05)}
    async with (
        exchange.async_client(connections=1) as native,
        package.AsyncClient(http_client=native, **pool) as api,
    ):
        exchange.respond(_response(200))
        async with api.retry.with_streaming_response.get_safe() as held:
            for enabled in (False, True):
                request = options.RequestOptions(retry=options.RetryOptions(retry_on_pool_timeout=enabled))
                result = await _afailed(lambda request=request: api.retry.get_safe(options=request))
                lines.append(f"  async held pool retry_on_pool_timeout={enabled} = {_delivered(result)}")
            await arecord(lines, "async held response still readable", held.read)


def _bodies(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    exchange = Exchange(lines)
    with (
        exchange.client() as native,
        package.Client(http_client=native, retry=options.RetryOptions(initial_delay=0)) as api,
    ):
        for label, method, maximum in (
            ("body before unsafe", "post_unsafe", 2),
            ("idempotent still needs body", "post_idempotent", 2),
            ("disabled before body", "post_idempotent", 0),
        ):
            exchange.respond(_response(503))
            body = iter((b"one", b"two"))
            request = options.RequestOptions(max_retries=maximum)
            record(
                lines,
                label,
                lambda method=method, body=body, request=request: outcome(
                    lambda: getattr(api.retry.with_response, method)(body=body, options=request)
                ),
            )


def _timing(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Record each retry wait through the client's clock, which advances a fake monotonic clock by it."""
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    exchange = Exchange(lines)
    with exchange.client() as native:
        for label, retry_fields, statuses, fields, draw_value, total, expected, draws in (
            (
                "saturated none",
                {"initial_delay": 0.25, "max_delay": 0.5, "jitter": "none", "max_retries": 4},
                (503, 503, 503, 503, 200),
                (),
                0.5,
                None,
                (0.25, 0.5, 0.5, 0.5),
                0,
            ),
            ("full jitter", {"initial_delay": 2, "max_delay": 4}, (503, 503, 200), (), 0.5, None, (1.0, 2.0), 2),
            ("full lower bound", {"initial_delay": 1}, (503, 200), (), 0.0, None, (), 1),
            ("full upper edge", {"initial_delay": 1}, (503, 200), (), math.nextafter(1.0, 0.0), None, (1.0,), 1),
            (
                "subnormal upper edge",
                {"initial_delay": 5e-324, "max_delay": 5e-324},
                (503, 200),
                (),
                math.nextafter(1.0, 0.0),
                None,
                (),
                1,
            ),
            (
                "zero remains zero",
                {"initial_delay": 0, "max_delay": 5e-324},
                (503, 503, 200),
                (),
                0.5,
                None,
                (),
                0,
            ),
            (
                "server exact cap",
                {"initial_delay": 0},
                (503, 200),
                (("Retry-After", "60"),),
                0.5,
                None,
                (60.0,),
                0,
            ),
            ("server over cap no RNG", {"initial_delay": 1}, (503,), (("Retry-After", "61"),), 0.5, None, (), 0),
            (
                "server unlimited cap",
                {"initial_delay": 0, "max_retry_after": None},
                (503, 200),
                (("Retry-After", "61"),),
                0.5,
                None,
                (61.0,),
                0,
            ),
            (
                "server deadline equality no RNG",
                {"initial_delay": 1},
                (503,),
                (("Retry-After", "1"),),
                0.5,
                1.0,
                (),
                0,
            ),
            ("backoff deadline equality", {"initial_delay": 1, "jitter": "none"}, (503,), (), 0.5, 1.0, (), 0),
            ("jitter deadline rejection", {"initial_delay": 2}, (503,), (), 0.75, 1.0, (), 1),
            ("success no RNG", {"initial_delay": 1}, (200,), (), 0.5, None, (), 0),
            ("excluded status no RNG", {"initial_delay": 1}, (404,), (), 0.5, None, (), 0),
            (
                "server forbids no RNG",
                {"initial_delay": 1},
                (503,),
                (("X-Retry-Permitted", "false"),),
                0.5,
                None,
                (),
                0,
            ),
        ):
            waits, draw = _Waits(), _Draw(draw_value)
            exchange.responders.clear()
            exchange.respond(*(_response(status, fields) for status in statuses))
            retry = dict(retry_fields)
            maximum = retry.pop("max_retries", 2)
            with package.Client(
                http_client=native,
                max_retries=maximum,
                retry=options.RetryOptions(**retry),
                total_timeout=total,
                clock=options.Clock(monotonic=waits.monotonic, random=draw, sleep=waits.sleep),
            ) as api:
                record(lines, label, lambda api=api: outcome(api.retry.with_response.get_vendor))
            delays = tuple(waits.delays)
            lines.append(
                f"    delays={delays!r} expected={expected!r} match={delays == expected} "
                f"draws={draw.calls}/{draws} unused={len(exchange.responders)}"
            )

        for label, fields, retry, received, closed, expected in (
            (
                "receipt target survives response close",
                (("Retry-After", "1"),),
                {"initial_delay": 0.25, "jitter": "none"},
                0.0,
                0.5,
                (0.5,),
            ),
            ("chosen target survives response close", (), {"initial_delay": 0.5, "jitter": "none"}, 0.0, 0.25, (0.25,)),
            (
                "original server cap survives headers hook",
                (("X-Retry-In-Ms", "64"),),
                {"initial_delay": 0, "max_retry_after": 0.06},
                1.0,
                0.0,
                (),
            ),
        ):
            waits = _Waits(received=received)
            exchange.responders.clear()
            exchange.respond(_elapsed(503, fields, waits, closed), _response(200))
            with (
                exchange.client(event_hooks={"response": [waits.response]}) as hooked,
                package.Client(
                    http_client=hooked,
                    retry=options.RetryOptions(**retry),
                    clock=options.Clock(monotonic=waits.monotonic, sleep=waits.sleep),
                ) as api,
            ):
                record(lines, label, lambda api=api: outcome(api.retry.with_response.get_vendor))
            delays = tuple(waits.delays)
            lines.append(f"    delays={delays!r} expected={expected!r} match={delays == expected}")


def _frozen(options: ModuleType) -> dict[str, object]:
    """Return client settings whose clock never moves, so each retry waits as long in real time as its policy chose."""
    return {
        "retry": options.RetryOptions(initial_delay=0.05, jitter="none"),
        "total_timeout": 5,
        "clock": options.Clock(monotonic=lambda: 1000.0),
    }


def _frozen_retries(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, **_frozen(options)) as api:
        exchange.respond(_response(503), _response(503), _response(200))
        response = record(lines, "retries on a frozen clock", api.retry.with_response.get_safe)
        lines.append(f"    attempts={getattr(getattr(response, 'info', None), 'attempt_count', None)}")


async def _async(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    received = _Statuses()
    async with (
        exchange.async_client(event_hooks={"response": [received.asynchronous]}) as native,
        package.AsyncClient(http_client=native, retry=options.RetryOptions(initial_delay=0)) as api,
    ):
        exchange.respond(_response(503), _response(200))
        response = await arecord(lines, "async retry", api.retry.with_response.get_safe)
        info = getattr(response, "info", None)
        counts = tuple(getattr(info, name, None) for name in ("attempt_count", "request_id"))
        lines.append(f"    counts={counts!r} statuses={received.values!r}")
        async with package.AsyncClient(http_client=native, **_frozen(options)) as frozen:
            exchange.respond(_response(503), _response(503), _response(200))
            response = await arecord(lines, "async retries on a frozen clock", frozen.retry.with_response.get_safe)
            lines.append(f"    attempts={getattr(getattr(response, 'info', None), 'attempt_count', None)}")
        exchange.respond(
            _response(503),
            lambda request: _response(200, (("X-Observed-Key", request.headers.get("Idempotency-Key", "")),))(request),
        )
        raw = await api.retry.with_raw_response.post_key_only(body=b"automatic")
        wire_key = raw.info.headers.get("X-Observed-Key")
        parsed = None if wire_key is None else UUID(wire_key)
        await arecord(lines, "async automatic declared key", raw.raise_for_status)
        lines.append(
            f"    uuid4={parsed is not None and parsed.version == 4} "
            f"canonical={parsed is not None and str(parsed) == wire_key} "
            f"attempts={raw.info.attempt_count} "
            f"unused={len(exchange.responders)}"
        )
    await _adelivery(package, options, lines)


def retry_policy(package: ModuleType, lines: list[str]) -> None:
    """Exercise policy decisions through generated operations over real local TLS."""
    options = importlib.import_module(f"{package.__name__}.options")
    _gates(package, options, lines)
    _hints(package, options, lines)
    _server_delays(package, options, lines)
    _keys(package, options, lines)
    _delivery(package, options, lines)
    _bodies(package, options, lines)
    _timing(package, options, lines)
    _frozen_retries(package, options, lines)
    run(lambda: _async(package, options, lines))
