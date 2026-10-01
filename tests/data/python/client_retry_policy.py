"""Report retry gates, vendor controls, timing, and retained keys through generated public clients."""

from __future__ import annotations

import importlib
import math
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import TYPE_CHECKING, NoReturn
from uuid import UUID

import httpx2

from tests.data.python.client_runtime import Exchange, arecord, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


class _Events:
    def __init__(self) -> None:
        self.values: list[tuple[object, ...]] = []

    def on_event(self, event: object) -> None:
        self.values.append(
            tuple(
                getattr(event, name)
                for name in ("name", "attempt_index", "status", "sent", "outcome", "retry_reason", "attempts", "sends")
            )
        )


class _FaultAdapter:
    def __init__(self, transports: ModuleType, failure: Exception) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
        )
        self.failure = failure
        self.sends = 0

    def send(self, request: object, context: object) -> NoReturn:
        del request, context
        self.sends += 1
        raise self.failure

    def close(self) -> None:
        pass


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class _Draw:
    def __init__(self, value: float) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value


class _TimingHook:
    def __init__(self, clock: _Clock, *, headers_elapsed: float = 0.0, end_elapsed: float = 0.0) -> None:
        self.clock = clock
        self.headers_elapsed = headers_elapsed
        self.end_elapsed = end_elapsed
        self.delays: list[float | None] = []

    def on_event(self, event: object) -> None:
        name = getattr(event, "name", None)
        if name == "response_headers" and getattr(event, "status", None) != 200:
            self.clock.value += self.headers_elapsed
        if name == "attempt_end":
            self.clock.value += self.end_elapsed
        if name == "retry_scheduled":
            delay = getattr(event, "duration", None)
            self.delays.append(round(delay, 12) if isinstance(delay, float) else None)
            if isinstance(delay, float):
                self.clock.value += delay


def _response(status: int, headers: tuple[tuple[str, str], ...] = ()) -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(
            status,
            headers=(("Content-Type", "text/plain"), *headers),
            stream=httpx2.ByteStream(b"server payload"),
        )

    return respond


def _outcome(call: Callable[[], object], *, error_type: type[Exception]) -> tuple[object, ...]:
    try:
        value = call()
    except error_type as error:
        info = getattr(error, "info", None)
        return (
            type(error).__name__,
            getattr(error, "retry_stop_reason", None),
            getattr(error, "resource_attempt_count", None),
            getattr(error, "network_send_count", None),
            getattr(error, "network_send_budget_used", None),
            getattr(error, "body_bytes", None),
            getattr(error, "error_data", None),
            getattr(error, "field_path", None),
            getattr(error, "truncated", None),
            getattr(info, "status_code", None),
        )
    info = getattr(value, "info", None)
    return (
        getattr(value, "data", None),
        getattr(info, "resource_attempt_count", None),
        getattr(info, "network_send_count", None),
        getattr(info, "network_send_budget_used", None),
    )


def _gates(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    events = _Events()
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0), hooks=(events,)),
        ) as api,
    ):
        for label, method, statuses, request in (
            ("default succeeds", "get_safe", (503, 200), options.RequestOptions()),
            ("default exhausted", "get_safe", (503, 503, 503), options.RequestOptions()),
            (
                "excluded before disabled",
                "get_safe",
                (404,),
                options.RequestOptions(retry=options.RetryOptions(max_retries=0)),
            ),
            (
                "never before disabled",
                "get_never",
                (503,),
                options.RequestOptions(retry=options.RetryOptions(max_retries=0)),
            ),
            ("disabled", "get_safe", (503,), options.RequestOptions(retry=options.RetryOptions(max_retries=0))),
            ("unsafe before network", "post_unsafe", (503,), options.RequestOptions(max_network_sends=1)),
            ("safe network exhausted", "get_safe", (503,), options.RequestOptions(max_network_sends=1)),
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
            events.values.clear()
            exchange.respond(*(_response(status) for status in statuses))
            record(
                lines,
                label,
                lambda method=method, request=request: outcome(
                    lambda: getattr(api.retry.with_response, method)(options=request)
                ),
            )
            lines.append(f"    events={events.values!r} unused={len(exchange.responders)}")


def _hints(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        for label, status, values, retry in (
            ("true adds404", 404, ("TrUe",), options.RetryOptions()),
            ("ASCII whitespace", 404, (" \ttrue \t",), options.RetryOptions()),
            ("false wins", 503, ("true", "FALSE", "true"), options.RetryOptions()),
            ("false before disabled", 503, ("false",), options.RetryOptions(max_retries=0)),
            ("excluded before false", 404, ("false",), options.RetryOptions()),
            ("invalid hint", 404, ("yes", "1"), options.RetryOptions()),
            ("false then true", 503, ("false", "true"), options.RetryOptions()),
            ("true ignores200", 200, ("true",), options.RetryOptions()),
            ("true ignores302", 302, ("true",), options.RetryOptions()),
            ("true ignores401", 401, ("true",), options.RetryOptions()),
            ("true ignores403", 403, ("true",), options.RetryOptions()),
            ("true ignores407", 407, ("true",), options.RetryOptions()),
            ("hint explicitly disabled", 404, ("true",), options.RetryOptions(should_retry_header=None)),
            (
                "hint case-insensitive binding",
                404,
                ("true",),
                options.RetryOptions(should_retry_header="x-retry-permitted"),
            ),
        ):
            exchange.responders.clear()
            exchange.respond(_response(status, tuple(("X-Retry-Permitted", value) for value in values)), _response(200))
            request = options.RequestOptions(retry=retry)
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
        package.Client(
            http_client=native,
            options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0, max_retry_after=0.01)),
        ) as api,
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
    now = datetime.now(timezone.utc)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        for label, method, key in (
            ("retained caller key", "post_keyed", options.IdempotencyKey("stable-key", first_used_at=now)),
            ("unknown prior use", "post_keyed", options.IdempotencyKey("unknown-key")),
            (
                "expired key",
                "post_keyed",
                options.IdempotencyKey("expired-key", first_used_at=now - timedelta(days=2)),
            ),
            (
                "key declaration insufficient",
                "post_key_only",
                options.IdempotencyKey("header-only", first_used_at=now),
            ),
            ("key suppressed", "post_keyed", None),
            ("undeclared key", "post_unsafe", options.IdempotencyKey("no-target", first_used_at=now)),
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
        for label, key in (
            ("safe method fresh key", options.IdempotencyKey("fresh-safe", first_used_at=now)),
            ("safe method expired key", options.IdempotencyKey("expired-safe", first_used_at=now - timedelta(days=2))),
        ):
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
            lambda request: _response(503, (("X-Observed-Key", request.headers.get("Idempotency-Key", "")),))(request),
            _response(200),
        )
        events = _Events()
        raw = api.retry.with_raw_response.post_key_only(
            body=b"automatic", options=options.RequestOptions(hooks=(events,))
        )
        wire_key = raw.info.headers.get("X-Observed-Key")
        parsed = None if wire_key is None else UUID(wire_key)
        record(lines, "automatic false declaration", lambda: outcome(raw.raise_for_status))
        lines.append(
            f"    uuid4={parsed is not None and parsed.version == 4} "
            f"canonical={parsed is not None and str(parsed) == wire_key} "
            f"counts={raw.info.resource_attempt_count}/{raw.info.network_send_count} "
            f"retries={sum(event[0] == 'retry_scheduled' for event in events.values)} "
            f"unused={len(exchange.responders)}"
        )


def _fault_gates(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")
    transports = importlib.import_module(f"{package.__name__}.transports")
    outcome = partial(_outcome, error_type=errors.SDKError)
    cause = OSError("injected I/O failure")
    for label, phase, owner, method, declared_unsent, pool in (
        ("unclassified before owner", "unknown", "transport", "get_never", False, False),
        ("owner before never", "read", "transport", "get_never", False, False),
        ("custom read candidate", "read", "sdk", "get_safe", False, False),
        ("custom write candidate", "write", "sdk", "get_safe", False, False),
        ("custom unsent cannot bypass safety", "read", "sdk", "post_unsafe", True, False),
        ("never applies to unsent", "read", "sdk", "post_never", True, False),
        ("pool default excluded", "pool", "sdk", "get_safe", True, False),
        ("pool explicitly enabled", "pool", "sdk", "get_safe", True, True),
    ):
        state = errors.DeliveryState.NOT_SENT if declared_unsent else errors.DeliveryState.MAYBE_SENT
        failure = (
            errors.PhaseTimeoutError(phase=phase, delivery_state=state, effective_timeout=0.01, cause=cause)
            if phase == "pool"
            else errors.TransportError(phase=phase, delivery_state=state, cause=cause)
        )
        adapter = _FaultAdapter(transports, failure)
        configured = options.ClientOptions(
            transport=options.TransportOptions(retry_owner=owner),
            retry=options.RetryOptions(initial_delay=0, max_retries=1, retry_on_pool_timeout=pool),
        )
        with package.Client(transport_adapter=adapter, options=configured) as api:
            record(lines, label, lambda method=method: outcome(getattr(api.retry.with_response, method)))
        lines.append(f"    sends={adapter.sends} cause-retained={failure.cause is cause}")


def _bodies(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    exchange = Exchange(lines)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        for label, method, maximum in (
            ("body before unsafe", "post_unsafe", 2),
            ("idempotent still needs body", "post_idempotent", 2),
            ("disabled before body", "post_idempotent", 0),
        ):
            exchange.respond(_response(503))
            body = bodies.StreamBody(iter((b"one", b"two")))
            request = options.RequestOptions(retry=options.RetryOptions(max_retries=maximum))
            record(
                lines,
                label,
                lambda method=method, body=body, request=request: outcome(
                    lambda: getattr(api.retry.with_response, method)(body=body, options=request)
                ),
            )


def _timing(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
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
            ("full lower bound", {"initial_delay": 1}, (503, 200), (), 0.0, None, (0.0,), 1),
            ("full upper edge", {"initial_delay": 1}, (503, 200), (), math.nextafter(1.0, 0.0), None, (1.0,), 1),
            (
                "subnormal upper edge",
                {"initial_delay": 5e-324, "max_delay": 5e-324},
                (503, 200),
                (),
                math.nextafter(1.0, 0.0),
                None,
                (0.0,),
                1,
            ),
            (
                "zero remains zero",
                {"initial_delay": 0, "max_delay": 5e-324},
                (503, 503, 200),
                (),
                0.5,
                None,
                (0.0, 0.0),
                0,
            ),
            ("server exact cap", {"initial_delay": 0}, (503, 200), (("Retry-After", "60"),), 0.5, None, (60.0,), 0),
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
            ("server deadline equality no RNG", {"initial_delay": 1}, (503,), (("Retry-After", "1"),), 0.5, 1.0, (), 0),
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
            clock, draw = _Clock(), _Draw(draw_value)
            hook = _TimingHook(clock)
            exchange.responders.clear()
            exchange.respond(*(_response(status, fields) for status in statuses))
            with package.Client(
                http_client=native,
                options=options.ClientOptions(
                    retry=options.RetryOptions(**retry_fields),
                    total_timeout=total,
                    hooks=(hook,),
                    clock=options.Clock(monotonic=clock, random=draw),
                ),
            ) as api:
                record(lines, label, lambda api=api: outcome(api.retry.with_response.get_vendor))
            lines.append(
                f"    delays={tuple(hook.delays)!r} expected={expected!r} match={tuple(hook.delays) == expected} "
                f"draws={draw.calls}/{draws} unused={len(exchange.responders)}"
            )

        for label, fields, retry_fields, elapsed, end_elapsed, expected in (
            (
                "receipt target survives headers hook",
                (("Retry-After", "1"),),
                {"initial_delay": 0.25, "jitter": "none"},
                0.5,
                0.0,
                (0.5,),
            ),
            ("chosen target survives end hook", (), {"initial_delay": 0.5, "jitter": "none"}, 0.0, 0.25, (0.25,)),
            (
                "original server cap survives hook",
                (("X-Retry-In-Ms", "64"),),
                {"initial_delay": 0, "max_retry_after": 0.06},
                1.0,
                0.0,
                (),
            ),
        ):
            clock = _Clock()
            hook = _TimingHook(clock, headers_elapsed=elapsed, end_elapsed=end_elapsed)
            exchange.responders.clear()
            exchange.respond(_response(503, fields), _response(200))
            with package.Client(
                http_client=native,
                options=options.ClientOptions(
                    retry=options.RetryOptions(**retry_fields), hooks=(hook,), clock=options.Clock(monotonic=clock)
                ),
            ) as api:
                record(lines, label, lambda api=api: outcome(api.retry.with_response.get_vendor))
            lines.append(
                f"    delays={tuple(hook.delays)!r} expected={expected!r} match={tuple(hook.delays) == expected}"
            )


def _retention_boundaries(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    exchange = Exchange(lines)
    with exchange.client() as native:
        for label, elapsed in (("key expires during response hook", 2.0), ("key expires during retry hook", 0.0)):
            clock = _Clock()
            hook = _TimingHook(clock, headers_elapsed=elapsed)
            key = options.IdempotencyKey(
                "retained-expiring-key", first_used_at=datetime.now(timezone.utc) - timedelta(seconds=86399)
            )
            exchange.responders.clear()
            exchange.respond(_response(503), _response(200))
            with package.Client(
                http_client=native,
                options=options.ClientOptions(
                    retry=options.RetryOptions(initial_delay=2, jitter="none"),
                    hooks=(hook,),
                    idempotency_key=key,
                    clock=options.Clock(monotonic=clock),
                ),
            ) as api:
                record(
                    lines, label, lambda api=api: outcome(lambda: api.retry.with_response.post_keyed(body=b"payload"))
                )
            lines.append(f"    delays={tuple(hook.delays)!r} unused={len(exchange.responders)}")


async def _async(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    events = _Events()
    async with (
        exchange.async_client() as native,
        package.AsyncClient(
            http_client=native,
            options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0), hooks=(events,)),
        ) as api,
    ):
        exchange.respond(_response(503), _response(200))
        response = await arecord(lines, "async retry", api.retry.with_response.get_safe)
        info = getattr(response, "info", None)
        counts = tuple(getattr(info, name, None) for name in ("resource_attempt_count", "network_send_count"))
        lines.append(f"    counts={counts!r} events={events.values!r}")
        events.values.clear()
        exchange.respond(
            lambda request: _response(503, (("X-Observed-Key", request.headers.get("Idempotency-Key", "")),))(request),
            _response(200),
        )
        raw = await api.retry.with_raw_response.post_key_only(body=b"automatic")
        wire_key = raw.info.headers.get("X-Observed-Key")
        parsed = None if wire_key is None else UUID(wire_key)
        await arecord(lines, "async automatic false declaration", raw.raise_for_status)
        lines.append(
            f"    uuid4={parsed is not None and parsed.version == 4} "
            f"canonical={parsed is not None and str(parsed) == wire_key} "
            f"counts={raw.info.resource_attempt_count}/{raw.info.network_send_count} "
            f"retries={sum(event[0] == 'retry_scheduled' for event in events.values)} "
            f"unused={len(exchange.responders)}"
        )


def retry_policy(package: ModuleType, lines: list[str]) -> None:
    """Exercise policy decisions through generated operations over real local TLS."""
    options = importlib.import_module(f"{package.__name__}.options")
    _gates(package, options, lines)
    _hints(package, options, lines)
    _server_delays(package, options, lines)
    _keys(package, options, lines)
    _fault_gates(package, options, lines)
    _bodies(package, options, lines)
    _timing(package, options, lines)
    _retention_boundaries(package, options, lines)
    run(lambda: _async(package, options, lines))
