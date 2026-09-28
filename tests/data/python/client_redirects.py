"""Report redirect methods, destination restrictions, and replay through generated public clients."""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import TYPE_CHECKING

import httpx2

from tests.data.python.client_retry_policy import _response
from tests.data.python.client_runtime import Exchange, arecord, record, run
from tests.data.python.fixture_native import NativeFixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from types import ModuleType


class _Events:
    def __init__(self) -> None:
        self.values: list[tuple[object, ...]] = []

    def on_event(self, event: object) -> None:
        self.values.append(
            tuple(
                getattr(event, name)
                for name in ("name", "attempt_index", "status", "origin", "attempts", "sends", "outcome")
            )
        )


async def _chunks() -> AsyncIterator[bytes]:
    for chunk in (b"one", b"two"):
        await asyncio.sleep(0)
        yield chunk


def _outcome(call: Callable[[], object], *, error_type: type[Exception]) -> tuple[object, ...]:
    try:
        value = call()
    except error_type as error:
        info = getattr(error, "info", None)
        return (
            type(error).__name__,
            getattr(error, "delivery_state", None),
            getattr(error, "body_available", None),
            getattr(info, "status_code", None),
            getattr(error, "resource_attempt_count", None),
            getattr(error, "redirect_count", None),
            getattr(error, "network_send_count", None),
            getattr(error, "network_send_budget_used", None),
            type(getattr(error, "cause", None)).__name__,
        )
    info = getattr(value, "info", None)
    return (
        getattr(value, "data", getattr(value, "body_bytes", None)),
        getattr(info, "status_code", None),
        getattr(info, "resource_attempt_count", None),
        getattr(info, "redirect_count", None),
        getattr(info, "network_send_count", None),
        getattr(info, "network_send_budget_used", None),
    )


def _statuses(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    events = _Events()
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                retry=options.RetryOptions(initial_delay=0),
                redirects=options.RedirectOptions(enabled=True),
                hooks=(events,),
            ),
        ) as api,
    ):
        for label, status, fields, configured in (
            ("disabled", 301, (("Location", "/done"),), options.RedirectOptions(enabled=False)),
            ("301 GET", 301, (("Location", "/done"),), options.RedirectOptions()),
            ("302 GET", 302, (("Location", "/done"),), options.RedirectOptions()),
            ("303 GET", 303, (("Location", "/done"),), options.RedirectOptions()),
            ("307 GET", 307, (("Location", "/done"),), options.RedirectOptions()),
            ("308 GET", 308, (("Location", "/done"),), options.RedirectOptions()),
            (
                "explicit default port",
                302,
                (("Location", "https://API.EXAMPLE.COM:443/done"),),
                options.RedirectOptions(),
            ),
            (
                "custom port",
                302,
                (("Location", "https://other.example.com:9443/done"),),
                options.RedirectOptions(allowed_origins=("https://other.example.com:9443",)),
            ),
            (
                "IDNA origin",
                302,
                (("Location", "https://xn--tst-qla.example.com/done"),),
                options.RedirectOptions(allowed_origins=("https://täst.example.com", "https://[::1]:443")),
            ),
            ("unsupported305", 305, (("Location", "/done"),), options.RedirectOptions()),
            ("missing Location", 302, (), options.RedirectOptions()),
            ("multiple Location", 302, (("Location", "/one"), ("Location", "/two")), options.RedirectOptions()),
            ("relative encoded", 302, (("Location", "next%2Fpart?q=a%2Fb#omitted"),), options.RedirectOptions()),
            ("fragment loop", 302, (("Location", "/safe#fragment"),), options.RedirectOptions()),
            ("zero limit", 302, (("Location", "/done"),), options.RedirectOptions(max_redirects=0)),
            ("unlisted origin", 302, (("Location", "https://other.example.com/done"),), options.RedirectOptions()),
            (
                "listed origin",
                302,
                (("Location", "https://other.example.com/done"),),
                options.RedirectOptions(allowed_origins=("https://other.example.com",)),
            ),
            (
                "userinfo rejected",
                302,
                (("Location", "https://user@api.example.com/done"),),
                options.RedirectOptions(),
            ),
            ("scheme rejected", 302, (("Location", "ftp://api.example.com/done"),), options.RedirectOptions()),
            ("zero port rejected", 302, (("Location", "https://api.example.com:0/done"),), options.RedirectOptions()),
            (
                "overflow port rejected",
                302,
                (("Location", "https://api.example.com:65536/done"),),
                options.RedirectOptions(),
            ),
            (
                "downgrade rejected",
                302,
                (("Location", "http://other.example.com/done"),),
                options.RedirectOptions(allowed_origins=("http://other.example.com",)),
            ),
        ):
            exchange.responders.clear()
            events.values.clear()
            exchange.respond(_response(status, fields), _response(200))
            request = options.RequestOptions(redirects=configured)
            record(
                lines,
                label,
                lambda request=request: outcome(lambda: api.retry.with_response.get_safe(options=request)),
            )
            lines.append(f"    unused={len(exchange.responders)} events={events.values!r}")
        exchange.responders.clear()
        exchange.respond(_response(302, (("Location", "/next"),)), _response(302, (("Location", "/last"),)))
        request = options.RequestOptions(redirects=options.RedirectOptions(max_redirects=1))
        record(
            lines,
            "cumulative limit",
            lambda request=request: outcome(lambda: api.retry.with_response.get_safe(options=request)),
        )
        exchange.respond(_response(302, (("Location", "/next"),)))
        request = options.RequestOptions(max_network_sends=1)
        record(
            lines,
            "hop budget",
            lambda request=request: outcome(lambda: api.retry.with_response.get_safe(options=request)),
        )


def _methods(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    now = datetime.now(timezone.utc)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                retry=options.RetryOptions(initial_delay=0), redirects=options.RedirectOptions(enabled=True)
            ),
        ) as api,
    ):
        for label, method, status, request in (
            ("301 POST forbidden", "post_idempotent", 301, options.RequestOptions()),
            ("302 POST forbidden", "post_idempotent", 302, options.RequestOptions()),
            ("303 POST opt-in required", "post_unsafe", 303, options.RequestOptions()),
            (
                "303 POST allowed",
                "post_unsafe",
                303,
                options.RequestOptions(redirects=options.RedirectOptions(allow_303_to_get=True)),
            ),
            (
                "303 never forbidden",
                "post_never",
                303,
                options.RequestOptions(redirects=options.RedirectOptions(allow_303_to_get=True)),
            ),
            ("307 unsafe forbidden", "post_unsafe", 307, options.RequestOptions()),
            ("307 declared idempotent", "post_idempotent", 307, options.RequestOptions()),
            ("308 declared idempotent", "post_idempotent", 308, options.RequestOptions()),
            (
                "308 retained key",
                "post_keyed",
                308,
                options.RequestOptions(idempotency_key=options.IdempotencyKey("same-key", first_used_at=now)),
            ),
            (
                "308 unknown key age",
                "post_keyed",
                308,
                options.RequestOptions(idempotency_key=options.IdempotencyKey("unknown-age")),
            ),
            (
                "303 expired key",
                "post_keyed",
                303,
                options.RequestOptions(
                    idempotency_key=options.IdempotencyKey("expired-key", first_used_at=now - timedelta(days=2)),
                    redirects=options.RedirectOptions(allow_303_to_get=True),
                ),
            ),
        ):
            exchange.responders.clear()
            exchange.respond(_response(status, (("Location", "/done"),)), _response(200))
            record(
                lines,
                label,
                lambda method=method, request=request: outcome(
                    lambda: getattr(api.retry.with_response, method)(body=b"payload", options=request)
                ),
            )
            lines.append(f"    unused={len(exchange.responders)}")
        exchange.responders.clear()
        exchange.respond(_response(303, (("Location", "/done"),)), _response(200))
        record(
            lines,
            "HEAD303 becomes GET",
            lambda: outcome(lambda: api.request_raw("HEAD", "https://api.example.com/start")),
        )
        for status in (301, 303, 307):
            exchange.responders.clear()
            exchange.respond(_response(status, (("Location", "/done"),)), _response(200))
            body = bodies.StreamBody(iter((b"one", b"two")))
            request = options.RequestOptions(
                redirects=options.RedirectOptions(allow_303_to_get=True),
                headers=(("Content-Encoding", "identity"), ("Content-Language", "en")),
            )
            record(
                lines,
                f"{status} one-shot body",
                lambda body=body, request=request: outcome(
                    lambda: api.retry.with_response.post_idempotent(body=body, options=request)
                ),
            )
            lines.append(f"    unused={len(exchange.responders)}")
        exchange.responders.clear()
        exchange.respond(
            _response(303, (("Location", "/middle"),)),
            _response(302, (("Location", "/done"),)),
            _response(200),
        )
        body = bodies.StreamBody(iter((b"one", b"two")))
        request = options.RequestOptions(redirects=options.RedirectOptions(allow_303_to_get=True))
        record(
            lines,
            "one-shot POST303 GET302 chain",
            lambda: outcome(lambda: api.retry.with_response.post_unsafe(body=body, options=request)),
        )
        lines.append(f"    unused={len(exchange.responders)}")


def _canonical_loops(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                base_url="https://API.EXAMPLE.COM:443", redirects=options.RedirectOptions(enabled=True)
            ),
        ) as api,
    ):
        exchange.respond(_response(302, (("Location", "https://api.example.com/safe"),)))
        record(lines, "canonical typed loop seed", lambda: outcome(api.retry.with_response.get_safe))
        exchange.respond(_response(302, (("Location", "https://api.example.com/safe"),)))
        record(
            lines,
            "canonical raw lowercase method loop seed",
            lambda: outcome(lambda: api.request_raw("get", "https://API.EXAMPLE.COM:443/safe")),
        )
        lines.append(f"    unused={len(exchange.responders)}")


def _restored(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(initial_delay=0))
        ) as api,
    ):
        for label, limit in (("303 retry restores original POST", 5), ("redirect limit survives retry", 1)):
            exchange.responders.clear()
            exchange.respond(
                _response(303, (("Location", "/done"),)),
                _response(503),
                _response(303, (("Location", "/done"),)),
                _response(200),
            )
            request = options.RequestOptions(
                redirects=options.RedirectOptions(enabled=True, allow_303_to_get=True, max_redirects=limit),
                headers=(("Content-Encoding", "identity"), ("Content-Language", "en")),
            )
            record(
                lines,
                label,
                lambda request=request: outcome(
                    lambda: api.retry.with_response.post_idempotent(body=b"original", options=request)
                ),
            )
            lines.append(f"    unused={len(exchange.responders)}")


def _origins(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                headers=(
                    ("Authorization", "original-secret"),
                    ("Proxy-Authorization", "proxy-secret"),
                    ("Cookie", "private=1"),
                ),
                retry=options.RetryOptions(initial_delay=0),
                redirects=options.RedirectOptions(enabled=True, allowed_origins=("https://other.example.com",)),
            ),
        ) as api,
    ):
        exchange.respond(
            _response(302, (("Location", "https://other.example.com/next"),)),
            _response(302, (("Location", "https://api.example.com/back"),)),
            _response(200),
        )
        record(lines, "origin chain returns to initial", lambda: outcome(api.retry.with_response.get_safe))
        exchange.respond(
            _response(302, (("Location", "/next"),)),
            _response(503),
            _response(302, (("Location", "/next"),)),
            _response(200),
        )
        record(lines, "retry resets URL and loop set", lambda: outcome(api.retry.with_response.get_safe))


def _downgrade(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    outcome = partial(_outcome, error_type=importlib.import_module(f"{package.__name__}.errors").SDKError)
    secure, plain = NativeFixture(), NativeFixture()
    plain.tls = False
    destination = f"http://localhost:{plain.port}"
    secure.status = 302
    secure.location = f"{destination}/done".encode()
    try:
        with httpx2.Client(verify=secure.verify, trust_env=False) as native:
            for label, allowed, downgrade in (
                ("downgrade flag still requires allowed origin", (), True),
                ("allowed HTTP still requires downgrade flag", (destination,), False),
                ("allowed HTTP and downgrade", (destination,), True),
            ):
                secure.requests.clear()
                plain.requests.clear()
                with package.Client(
                    http_client=native,
                    options=options.ClientOptions(
                        base_url=secure.url,
                        redirects=options.RedirectOptions(
                            enabled=True, allowed_origins=allowed, allow_https_downgrade=downgrade
                        ),
                    ),
                ) as api:
                    record(lines, label, lambda api=api: outcome(api.retry.with_response.get_safe))
                lines.append(f"    secure={secure.requests!r} plain={plain.requests!r}")
    finally:
        secure.stop()
        plain.stop()


async def _async(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    async with (
        exchange.async_client() as native,
        package.AsyncClient(
            http_client=native, options=options.ClientOptions(redirects=options.RedirectOptions(enabled=True))
        ) as api,
    ):
        exchange.respond(_response(307, (("Location", "/done"),)), _response(200))
        response = await arecord(lines, "async307", api.retry.with_response.get_safe)
        if response is None:
            message = "The async redirect did not return a response"
            raise RuntimeError(message)
        info = getattr(response, "info", None)
        counts = tuple(
            getattr(info, name, None) for name in ("resource_attempt_count", "redirect_count", "network_send_count")
        )
        lines.append(f"    counts={counts!r}")
        exchange.respond(
            _response(303, (("Location", "/middle"),)),
            _response(302, (("Location", "/done"),)),
            _response(200),
        )
        body = bodies.AsyncStreamBody(_chunks())
        request = options.RequestOptions(redirects=options.RedirectOptions(allow_303_to_get=True))
        response = await arecord(
            lines,
            "async one-shot POST303 GET302 chain",
            lambda: api.retry.with_response.post_unsafe(body=body, options=request),
        )
        info = getattr(response, "info", None)
        counts = tuple(
            getattr(info, name, None) for name in ("resource_attempt_count", "redirect_count", "network_send_count")
        )
        lines.append(f"    counts={counts!r} unused={len(exchange.responders)}")


def redirects(package: ModuleType, lines: list[str]) -> None:
    """Exercise redirect policy with fresh requests and wire-visible origin/method/body changes."""
    options = importlib.import_module(f"{package.__name__}.options")
    _statuses(package, options, lines)
    _methods(package, options, lines)
    _canonical_loops(package, options, lines)
    _restored(package, options, lines)
    _origins(package, options, lines)
    _downgrade(package, options, lines)
    run(lambda: _async(package, options, lines))
