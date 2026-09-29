"""Exercise retry, redirect, key, and construction options through a generated client's public surface."""

from __future__ import annotations

import importlib
import inspect
import ssl
import subprocess
import sys
from dataclasses import fields
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_type_hints
from uuid import UUID

from tests.data.python.client_runtime import Exchange, arecord, describe, raw_response, record, run
from tests.data.python.client_transports import Adapter
from tests.data.python.fixture_native import NativeFixture
from tests.data.python.fixture_server import FixtureServer, _contexts

if TYPE_CHECKING:
    from types import ModuleType

    import httpx2


class _NoOffset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> None:
        del dt

    def dst(self, dt: datetime | None) -> None:
        del dt

    def tzname(self, dt: datetime | None) -> None:
        del dt


class _InvalidOffset(_NoOffset):
    def utcoffset(self, dt: datetime | None) -> None:
        del dt
        msg = "invalid timezone offset"
        raise ValueError(msg)


def _invalid_numbers(options: ModuleType, lines: list[str]) -> None:
    invalid_numbers = (
        ("bool", True),
        ("negative", -1),
        ("nan", float("nan")),
        ("infinity", float("inf")),
        ("negative infinity", float("-inf")),
        ("text", "1"),
        ("none", None),
    )
    for label, value in invalid_numbers:
        for name in ("initial_delay", "max_delay", "max_retry_after"):
            if name != "max_retry_after" or value is not None:
                record(
                    lines,
                    f"retry {name} {label}",
                    lambda name=name, value=value: options.RetryOptions(**{name: value}),
                )
        for owner, name in (
            (options.RetryOptions, "max_retries"),
            (options.RedirectOptions, "max_redirects"),
            (options.TransportOptions, "max_connections"),
            (options.TransportOptions, "max_keepalive_connections"),
            (options.TransportOptions, "keepalive_expiry"),
        ):
            record(
                lines,
                f"{owner.__name__} {name} {label}",
                lambda owner=owner, name=name, value=value: owner(**{name: value}),
            )
    for name in ("initial_delay", "max_delay", "max_retry_after"):
        record(lines, f"retry {name} overflow", lambda name=name: options.RetryOptions(**{name: 10**400}))
    record(lines, "transport expiry overflow", lambda: options.TransportOptions(keepalive_expiry=10**400))
    record(lines, "retry cap zero", lambda: options.RetryOptions(max_retry_after=0))
    record(lines, "retry delay order", lambda: options.RetryOptions(initial_delay=2, max_delay=1))
    for owner, name in (
        (options.RetryOptions, "max_retries"),
        (options.RedirectOptions, "max_redirects"),
        (options.TransportOptions, "max_connections"),
        (options.TransportOptions, "max_keepalive_connections"),
    ):
        record(lines, f"{owner.__name__} {name} fractional", lambda owner=owner, name=name: owner(**{name: 0.5}))


def _invalid_values(options: ModuleType, lines: list[str]) -> None:
    for owner, names in (
        (options.RetryOptions, ("respect_retry_after", "retry_on_pool_timeout")),
        (options.RedirectOptions, ("enabled", "allow_303_to_get", "allow_https_downgrade")),
        (options.TransportOptions, ("verify", "trust_env", "http2")),
    ):
        for name in names:
            for value in (0, "true", None):
                record(
                    lines,
                    f"{owner.__name__} {name} {value!r}",
                    lambda owner=owner, name=name, value=value: owner(**{name: value}),
                )
    for owner, name in ((options.RetryOptions, "jitter"), (options.TransportOptions, "retry_owner")):
        for value in (None, [], "automatic"):
            record(
                lines,
                f"{owner.__name__} {name} {value!r}",
                lambda owner=owner, name=name, value=value: owner(**{name: value}),
            )
    for label, value in (
        ("none", None),
        ("sequence", [408, 500]),
        ("scalar", 500),
        ("bool", {False, 500}),
        ("fractional", {500.5}),
        ("text", {"500"}),
        ("low", {399}),
        ("high", {600}),
        ("unauthorized", {401}),
        ("forbidden", {403}),
        ("proxy", {407}),
    ):
        record(lines, f"retry statuses {label}", lambda value=value: options.RetryOptions(statuses=value))
    for name in ("retry_after_ms_header", "should_retry_header"):
        for value in (True, "", "bad name", "bad\rname", "bad:name", "café"):
            record(
                lines,
                f"retry {name} {value!r}",
                lambda name=name, value=value: options.RetryOptions(**{name: value}),
            )
    for name, value in (("ssl_context", True), ("proxy", {}), ("proxy", 123)):
        record(
            lines, f"transport {name} type", lambda name=name, value=value: options.TransportOptions(**{name: value})
        )
    for owner in (options.ClientOptions, options.RequestOptions):
        for name, value in (
            ("retry", None),
            ("retry", True),
            ("redirects", None),
            ("redirects", {}),
            ("idempotency_key", "key"),
        ):
            record(
                lines,
                f"{owner.__name__} {name} {value!r}",
                lambda owner=owner, name=name, value=value: owner(**{name: value}),
            )
    for value in (None, False, {}):
        record(lines, f"client transport {value!r}", lambda value=value: options.ClientOptions(transport=value))
    record(lines, "request transport unavailable", lambda: options.RequestOptions(transport=options.TransportOptions()))


def _origins(options: ModuleType, lines: list[str]) -> None:
    for label, value in (
        ("none", None),
        ("string", "https://example.com"),
        ("mapping", {}),
        ("non-string", (1,)),
        ("relative", ("example.com",)),
        ("scheme", ("ftp://example.com",)),
        ("missing host", ("https://:443",)),
        ("userinfo", ("https://name:secret@example.com",)),
        ("path", ("https://example.com/path",)),
        ("root path", ("https://example.com/",)),
        ("query", ("https://example.com?",)),
        ("fragment", ("https://example.com#",)),
        ("space", ("https://example.com ",)),
        ("control", ("https://example.com\x00",)),
        ("backslash", ("https://example.com\\host",)),
        ("port zero", ("https://example.com:0",)),
        ("port syntax", ("https://example.com:port",)),
        ("port large", ("https://example.com:65536",)),
        ("ipv6", ("https://[::1",)),
    ):
        record(lines, f"redirect origin {label}", lambda value=value: options.RedirectOptions(allowed_origins=value))
    values = ["https://example.com", "http://example.com:80", "HTTPS://EXAMPLE.COM:443", "https://[::1]:443"]
    configured = options.RedirectOptions(allowed_origins=values)
    values.append("https://changed.example.com")
    lines.append(f"  frozen origins={configured.allowed_origins!r} tuple={type(configured.allowed_origins) is tuple}")


def _keys(options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    for value in (
        None,
        False,
        1,
        "",
        "key\rvalue",
        "key\nvalue",
        "key\0value",
        "key\x01value",
        "key\x08value",
        "key\x0bvalue",
        "key\x0cvalue",
        "key\x1fvalue",
        "key\x7fvalue",
        " key",
        "key ",
        "\tkey",
        "key\t",
        " ",
        "\t",
        " \t ",
        "key\ud800value",
        "key\udfffvalue",
    ):
        try:
            accepted = options.IdempotencyKey(value)
        except errors.ConfigurationError as error:
            lines.append(f"  key value {value!r} ! {describe(error)}")
            lines.append(
                f"  rejected key attempts={error.resource_attempt_count} redirects={error.redirect_count}"
                f" auth={error.auth_exchange_count} sends={error.network_send_count}"
                f" budget={error.network_send_budget_used} auth_budget={error.auth_exchange_budget_used}"
                f" refresh_ids={error.auth_refresh_ids} pending={error.auth_refresh_pending}"
                f" wire={error.wire_send_count} info={error.info}"
            )
        else:
            record(lines, f"unexpected accepted key {value!r}", lambda accepted=accepted: accepted)
    for label, value in (
        ("text", "2026-09-28"),
        ("number", 0),
        ("naive", datetime(2026, 9, 28, tzinfo=timezone.utc).replace(tzinfo=None)),
        ("no offset", datetime(2026, 9, 28, tzinfo=_NoOffset())),
        ("invalid offset", datetime(2026, 9, 28, tzinfo=_InvalidOffset())),
    ):
        record(lines, f"key first-used {label}", lambda value=value: options.IdempotencyKey("key", first_used_at=value))
    record(lines, "key first-used positional", lambda: options.IdempotencyKey("key", datetime.now(timezone.utc)))
    record(lines, "key unknown use", lambda: options.IdempotencyKey("caller-key"))
    record(lines, "key unicode value", lambda: options.IdempotencyKey("clé"))
    offset = datetime(2026, 9, 28, 9, tzinfo=timezone(timedelta(hours=9)))
    caller = options.IdempotencyKey(value="caller-key", first_used_at=offset)
    lines.append(f"  key timezone preserved={caller.first_used_at is offset} value={caller.value!r}")
    before = datetime.now(timezone.utc)
    created = options.IdempotencyKey.new()
    after = datetime.now(timezone.utc)
    lines.append(
        f"  new key version={UUID(created.value).version} utc={created.first_used_at.tzinfo is timezone.utc}"
        f" current={before <= created.first_used_at <= after}"
    )
    values = options.RequestOptions(idempotency_key=caller)
    lines.append(f"  option preserves key={values.idempotency_key is caller}")


def _records(options: ModuleType, lines: list[str]) -> None:
    for owner in (options.RetryOptions, options.RedirectOptions):
        value = owner()
        omitted = tuple((item.name, getattr(value, item.name) is options.UNSET) for item in fields(value))
        lines.append(f"  omitted {owner.__name__}={omitted}")
    statuses = {408, 409, 500, 501}
    retry = options.RetryOptions(
        statuses=statuses, max_retries=0, initial_delay=0, max_delay=0, jitter="none", max_retry_after=None
    )
    statuses.add(503)
    lines.append(f"  frozen statuses={sorted(retry.statuses)} frozenset={type(retry.statuses) is frozenset}")
    record(lines, "retry empty statuses", lambda: options.RetryOptions(statuses=frozenset()))
    record(
        lines,
        "retry explicit defaults",
        lambda: options.RetryOptions(
            max_retries=2,
            initial_delay=0.5,
            max_delay=8,
            jitter="full",
            max_retry_after=60,
            respect_retry_after=True,
            retry_on_pool_timeout=False,
        ),
    )
    record(
        lines,
        "retry explicit switches",
        lambda: options.RetryOptions(
            respect_retry_after=False, retry_on_pool_timeout=True, retry_after_ms_header=None, should_retry_header=None
        ),
    )
    record(
        lines,
        "redirect explicit values",
        lambda: options.RedirectOptions(
            enabled=False, max_redirects=0, allow_303_to_get=True, allowed_origins=(), allow_https_downgrade=True
        ),
    )
    record(lines, "transport fixed defaults", options.TransportOptions)
    record(
        lines,
        "transport explicit values",
        lambda: options.TransportOptions(
            verify=False,
            proxy="http://proxy.example.com:8080",
            trust_env=True,
            http2=True,
            max_connections=0,
            max_keepalive_connections=0,
            keepalive_expiry=0,
            retry_owner="transport",
        ),
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    configured = options.TransportOptions(ssl_context=context)
    lines.append(
        f"  transport SSLContext identity={configured.ssl_context is context}"
        f" verify omitted={configured.verify is options.UNSET}"
    )
    for value in (True, False):
        record(
            lines,
            f"transport context verify={value}",
            lambda value=value: options.TransportOptions(verify=value, ssl_context=context),
        )
    for value, name, replacement in (
        (retry, "max_retries", 7),
        (options.RedirectOptions(), "enabled", True),
        (configured, "verify", False),
        (options.IdempotencyKey("fixed"), "value", "changed"),
    ):
        previous = getattr(value, name)
        try:
            setattr(value, name, replacement)
        except (AttributeError, TypeError):
            rejected = True
        else:
            rejected = False
        lines.append(
            f"  immutable {type(value).__name__} rejected={rejected}"
            f" unchanged={getattr(value, name) == previous} slotted={not hasattr(value, '__dict__')}"
        )
    for owner in (
        options.RetryOptions,
        options.RedirectOptions,
        options.TransportOptions,
        options.IdempotencyKey,
        options.ClientOptions,
        options.RequestOptions,
    ):
        lines.extend((
            f"  hints {owner.__name__}={tuple(get_type_hints(owner, include_extras=True))}",
            f"  init hints {owner.__name__}={tuple(get_type_hints(owner.__init__, include_extras=True))}",
        ))
    key_signature = tuple(
        (name, item.kind.name) for name, item in inspect.signature(options.IdempotencyKey).parameters.items()
    )
    lines.extend((
        f"  new hints={tuple(get_type_hints(options.IdempotencyKey.new, include_extras=True))}",
        f"  key signature={key_signature}",
    ))
    for owner in (options.ClientOptions, options.RequestOptions):
        value = owner()
        omitted = tuple(
            getattr(value, name) is options.UNSET
            for name in ("retry", "redirects", "idempotency_key", "max_network_sends")
        )
        lines.append(f"  option omitted {owner.__name__}={omitted}")


def _imports(package: ModuleType, lines: list[str]) -> None:
    if package.__file__ is None:
        msg = "Generated package has no file"
        raise RuntimeError(msg)
    script = """
import importlib
import sys
from typing import get_type_hints

sys.path.insert(0, sys.argv[1])
blocked = {
    "httpx2", "httpcore2", "anyio", "pydantic", "msgspec", "websockets", "datamodel_code_generator",
    sys.argv[2] + "_models",
}

class Blocked:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked:
            raise ImportError("Unexpected optional import: " + fullname)

sys.meta_path.insert(0, Blocked())
options = importlib.import_module(sys.argv[2] + ".options")
owners = ("RetryOptions", "RedirectOptions", "TransportOptions", "IdempotencyKey", "ClientOptions", "RequestOptions")
for name in owners:
    get_type_hints(getattr(options, name), include_extras=True)
print("  optional-free public options=" + str(not any(name in sys.modules for name in blocked)))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(Path(package.__file__).parent.parent), package.__name__],
        check=True,
        capture_output=True,
        text=True,
    )
    lines.extend(result.stdout.splitlines())


class _Events:
    def __init__(self) -> None:
        self.retries: list[tuple[object, object]] = []

    def on_event(self, event: Any) -> None:
        if event.name == "retry_scheduled":
            self.retries.append((event.duration, event.retry_reason))


def _merges(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    exchange.respond(
        raw_response(501, b"first", "text/plain", **{"Retry-After": "999"}),
        raw_response(501, b"second", "text/plain", **{"Retry-After": "999"}),
        raw_response(200, b"merged", "text/plain"),
    )
    events = _Events()
    client = options.ClientOptions(
        retry=options.RetryOptions(
            max_retries=1,
            initial_delay=0,
            max_delay=0,
            jitter="none",
            statuses={409},
            max_retry_after=1,
            respect_retry_after=False,
        ),
        hooks=(events,),
    )
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=client) as api,
        api.with_options(options.RequestOptions(retry=options.RetryOptions(max_retries=2))) as first,
        first.with_options(options.RequestOptions(retry=options.RetryOptions(max_retry_after=None))) as view,
    ):
        record(
            lines,
            "retry nested client/view/view/call",
            lambda: view.retry.get_safe(options=options.RequestOptions(retry=options.RetryOptions(statuses={501}))),
        )
    lines.append(f"  merged retry waits={events.retries} pending responses={len(exchange.responders)}")

    exchange = Exchange(lines)
    exchange.respond(raw_response(200, b"valid inherited delay", "text/plain"))
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(retry=options.RetryOptions(max_delay=20))
        ) as api,
    ):
        with api.with_options(options.RequestOptions(retry=options.RetryOptions(initial_delay=10))) as view:
            record(lines, "partial delay inherits larger maximum", view.retry.get_safe)
            record(
                lines,
                "invalid delay after request merge",
                lambda: view.retry.get_safe(options=options.RequestOptions(retry=options.RetryOptions(max_delay=5))),
            )
        record(
            lines,
            "invalid delay after view merge",
            lambda: api.with_options(options.RequestOptions(retry=options.RetryOptions(initial_delay=21))),
        )
    lines.append(f"  merged delay pending responses={len(exchange.responders)}")
    record(
        lines,
        "invalid delay after client merge",
        lambda: package.Client(options=options.ClientOptions(retry=options.RetryOptions(initial_delay=9))),
    )

    exchange = Exchange(lines)
    exchange.respond(
        raw_response(302, Location="/one"),
        raw_response(302, Location="/two"),
        raw_response(503, b"retry", "text/plain"),
        raw_response(200, b"four sends", "text/plain"),
    )
    client = options.ClientOptions(
        retry=options.RetryOptions(max_retries=0, initial_delay=0, max_delay=0, jitter="none"),
        redirects=options.RedirectOptions(max_redirects=2),
    )
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=client) as api,
        api.with_options(options.RequestOptions(redirects=options.RedirectOptions(enabled=True))) as view,
    ):
        record(
            lines,
            "derived cap uses final retry and redirect fields",
            lambda: (
                (
                    response := view.retry.with_response.get_safe(
                        options=options.RequestOptions(retry=options.RetryOptions(max_retries=1))
                    )
                ).data,
                response.info.resource_attempt_count,
                response.info.redirect_count,
                response.info.network_send_count,
                response.info.network_send_budget_used,
            ),
        )
    lines.append(f"  merged redirect pending responses={len(exchange.responders)}")

    exchange = Exchange(lines)
    exchange.respond(
        raw_response(303, Location="https://other.example.com/accepted"),
        raw_response(200, b"inherited destination and method permission", "text/plain"),
    )
    client = options.ClientOptions(
        retry=options.RetryOptions(max_retries=0),
        redirects=options.RedirectOptions(
            max_redirects=1,
            allow_303_to_get=True,
            allowed_origins=("https://other.example.com",),
        ),
    )
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=client) as api,
        api.with_options(options.RequestOptions(redirects=options.RedirectOptions(enabled=True))) as view,
    ):
        record(
            lines,
            "redirect nested permissions",
            lambda: view.retry.post_unsafe(
                body=b"removed after 303",
                options=options.RequestOptions(redirects=options.RedirectOptions()),
            ),
        )
    lines.append(f"  merged redirect permissions pending responses={len(exchange.responders)}")


def _budgets(package: ModuleType, options: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    for label, inherited, retries, cap in (
        ("derived defaults", options.UNSET, options.UNSET, options.UNSET),
        ("derived disabled retry", options.UNSET, 0, options.UNSET),
        ("explicit one inherited", 1, 4, options.UNSET),
        ("uncapped inherited", None, 1, options.UNSET),
        ("zero override", None, 2, 0),
        ("seven inherited", 7, 9, options.UNSET),
        ("explicit call replaces one", 1, 2, None),
    ):
        exchange = Exchange(lines)
        exchange.respond(*(raw_response(503, b"unavailable", "text/plain") for _ in range(12)))
        client = options.ClientOptions(
            max_network_sends=inherited,
            retry=options.RetryOptions(initial_delay=0, max_delay=0, jitter="none"),
        )
        with (
            exchange.client() as native,
            package.Client(http_client=native, options=client) as api,
            api.with_options(options.RequestOptions(max_network_sends=options.UNSET)) as view,
        ):
            try:
                view.retry.get_safe(
                    options=options.RequestOptions(
                        max_network_sends=cap, retry=options.RetryOptions(max_retries=retries)
                    )
                )
            except errors.SDKError as error:
                lines.append(
                    f"  budget {label}={type(error).__name__} attempts={error.resource_attempt_count}"
                    f" sends={error.network_send_count} used={error.network_send_budget_used}"
                    f" stop={getattr(error, 'retry_stop_reason', None)}"
                )
            else:
                lines.append(f"  budget {label}=unexpected success")
        lines.append(f"  budget {label} arrivals={12 - len(exchange.responders)}")


def _vendor_headers(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for label, configured in (
        ("inherited declaration", options.RetryOptions()),
        (
            "case-insensitive match",
            options.RetryOptions(retry_after_ms_header="x-retry-in-ms", should_retry_header="x-retry-permitted"),
        ),
        ("disabled", options.RetryOptions(retry_after_ms_header=None, should_retry_header=None)),
    ):
        exchange = Exchange(lines)
        exchange.respond(
            raw_response(501, b"vendor", "text/plain", **{"X-Retry-In-Ms": "0", "X-Retry-Permitted": "true"}),
            raw_response(200, b"accepted", "text/plain"),
        )
        client = options.ClientOptions(retry=options.RetryOptions(initial_delay=0, max_delay=0, jitter="none"))
        with (
            exchange.client() as native,
            package.Client(http_client=native, options=client) as api,
            api.with_options(options.RequestOptions(retry=configured)) as view,
        ):
            record(
                lines,
                f"vendor {label}",
                lambda: view.retry.get_vendor(options=options.RequestOptions(retry=options.RetryOptions())),
            )
        lines.append(f"  vendor {label} arrivals={2 - len(exchange.responders)}")
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        for name in ("retry_after_ms_header", "should_retry_header"):
            record(
                lines,
                f"vendor mismatched {name}",
                lambda name=name: api.retry.get_vendor(
                    options=options.RequestOptions(retry=options.RetryOptions(**{name: "X-Undeclared"}))
                ),
            )
            record(
                lines,
                f"vendor undeclared {name}",
                lambda name=name: api.retry.get_safe(
                    options=options.RequestOptions(retry=options.RetryOptions(**{name: "X-Undeclared"}))
                ),
            )


class _KeySigner:
    """A signer that claims the idempotency key header, which the call refuses before signing."""

    def __init__(self, auth: ModuleType) -> None:
        self.capabilities = auth.SignerCapabilities((), ("Idempotency-Key",), (), False)

    def sign(self, request: object) -> object:
        return request


def _key_calls(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    observed: list[str | None] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        observed.append(request.headers.get("Idempotency-Key"))
        status, body = (503, b"retry") if len(observed) == 1 else (200, b"accepted")
        return raw_response(status, body, "text/plain")(request)

    exchange = Exchange(lines)
    exchange.respond(reply, reply, reply, reply)
    client = options.ClientOptions(retry=options.RetryOptions(initial_delay=0, max_delay=0, jitter="none"))
    with exchange.client() as native, package.Client(http_client=native, options=client) as api:
        record(lines, "automatic key retained through retry", lambda: api.retry.post_keyed(body=b"same bytes"))
        record(lines, "automatic key fresh next call", lambda: api.retry.post_keyed(body=b"new call"))
        record(
            lines,
            "automatic key suppressed",
            lambda: api.retry.post_keyed(options=options.RequestOptions(idempotency_key=None)),
        )
        record(
            lines,
            "explicit undeclared key",
            lambda: api.retry.get_safe(
                options=options.RequestOptions(idempotency_key=options.IdempotencyKey("unbound"))
            ),
        )
        auth = importlib.import_module(f"{package.__name__}.auth")
        signing = options.RequestOptions(auth=auth.AuthConfig({}, send_on_anonymous=True, signers=(_KeySigner(auth),)))
        record(lines, "signer claiming the key header", lambda: api.retry.post_keyed(options=signing))
    lines.append(
        f"  automatic keys count={len(observed)} same retry={observed[0] == observed[1]}"
        f" fresh call={observed[1] != observed[2]} suppressed={observed[3] is None}"
    )

    observed.clear()
    exchange = Exchange(lines)
    exchange.respond(reply, reply)
    key = options.IdempotencyKey("caller-owned", first_used_at=datetime.now(timezone.utc))
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(idempotency_key=key, retry=client.retry),
        ) as api,
        api.with_options(options.RequestOptions()) as view,
    ):
        record(
            lines,
            "caller key inherited and retained through retry",
            lambda: view.retry.post_keyed(options=options.RequestOptions(idempotency_key=options.UNSET)),
        )
    lines.append(f"  caller key observed={observed}")

    observed.clear()
    exchange = Exchange(lines)
    exchange.respond(reply)
    with (
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(idempotency_key=options.IdempotencyKey("unused"))
        ) as api,
        api.with_options(options.RequestOptions(idempotency_key=None)) as view,
    ):
        record(
            lines,
            "view clears caller key",
            lambda: view.retry.post_keyed(options=options.RequestOptions(idempotency_key=options.UNSET)),
        )
    lines.append(f"  cleared key observed={observed}")


def _key_wire(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    mode = "async" if asynchronous else "sync"
    values = ("a b", "a\tb", "a \t b", "clé", "\u00a0key\u00a0", "\ud7ffkey\ue000")
    for http2 in (False, True):
        server = NativeFixture(http2=http2)
        protocol = "h2" if http2 else "h1"
        client_options = options.ClientOptions(
            base_url=server.url,
            transport=options.TransportOptions(ssl_context=server.verify, http2=http2),
        )
        try:
            if asynchronous:

                async def calls(
                    client_options: object = client_options, protocol: str = protocol, server: NativeFixture = server
                ) -> None:
                    async with package.AsyncClient(options=client_options) as api:
                        await arecord(
                            lines,
                            f"key {mode} {protocol} pre-send rejection",
                            lambda: api.retry.post_keyed(
                                options=options.RequestOptions(idempotency_key=options.IdempotencyKey(" key"))
                            ),
                        )
                        lines.append(f"  rejected key arrivals={len(server.requests)}")
                        for value in values:
                            key = options.IdempotencyKey(value)
                            await arecord(
                                lines,
                                f"key {mode} {protocol} value={value!r}",
                                lambda key=key: api.retry.post_keyed(
                                    options=options.RequestOptions(idempotency_key=key)
                                ),
                            )
                            fields = tuple(
                                value
                                for name, value in server.request_headers[-1]
                                if name.lower() == b"idempotency-key"
                            )
                            lines.append(f"  unchanged={key.value is value} key wire={fields!r}")

                run(calls)
            else:
                with package.Client(options=client_options) as api:
                    record(
                        lines,
                        f"key {mode} {protocol} pre-send rejection",
                        lambda: api.retry.post_keyed(
                            options=options.RequestOptions(idempotency_key=options.IdempotencyKey(" key"))
                        ),
                    )
                    lines.append(f"  rejected key arrivals={len(server.requests)}")
                    for value in values:
                        key = options.IdempotencyKey(value)
                        record(
                            lines,
                            f"key {mode} {protocol} value={value!r}",
                            lambda key=key: api.retry.post_keyed(options=options.RequestOptions(idempotency_key=key)),
                        )
                        fields = tuple(
                            value for name, value in server.request_headers[-1] if name.lower() == b"idempotency-key"
                        )
                        lines.append(f"  unchanged={key.value is value} key wire={fields!r}")
            lines.append(f"  key {mode} {protocol} arrivals={len(server.requests)} alpn={server.protocols}")
        finally:
            server.stop()


def _transports(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    transport_module = importlib.import_module(f"{package.__name__}.transports")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    changes = (
        ("verify", False),
        ("ssl_context", context),
        ("proxy", "http://proxy.example.com"),
        ("trust_env", True),
        ("http2", True),
        ("max_connections", 2),
        ("max_keepalive_connections", 1),
        ("keepalive_expiry", 1),
    )
    exchange = Exchange(lines)
    with exchange.client() as native:
        for name, value in changes:
            record(
                lines,
                f"borrowed transport conflict {name}",
                lambda name=name, value=value: package.Client(
                    http_client=native,
                    options=options.ClientOptions(transport=options.TransportOptions(**{name: value})),
                ).close(),
            )
            record(
                lines,
                f"adapter transport conflict {name}",
                lambda name=name, value=value: package.Client(
                    transport_adapter=Adapter(transport_module, lines),
                    options=options.ClientOptions(transport=options.TransportOptions(**{name: value})),
                ).close(),
            )
        defaults = options.TransportOptions(
            verify=True,
            ssl_context=None,
            proxy=None,
            trust_env=False,
            http2=False,
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5,
            retry_owner="sdk",
        )
        record(
            lines,
            "borrowed accepts explicit construction defaults",
            lambda: package.Client(http_client=native, options=options.ClientOptions(transport=defaults)).close(),
        )
        record(
            lines,
            "adapter accepts explicit construction defaults",
            lambda: package.Client(
                transport_adapter=Adapter(transport_module, lines), options=options.ClientOptions(transport=defaults)
            ).close(),
        )
        transport_owned = options.ClientOptions(transport=options.TransportOptions(retry_owner="transport"))
        record(
            lines,
            "borrowed native rejects transport retry owner",
            lambda: package.Client(http_client=native, options=transport_owned).close(),
        )
        record(
            lines,
            "owned native rejects transport retry owner",
            lambda: package.Client(http_client=native, http_client_ownership="owned", options=transport_owned).close(),
        )
    record(lines, "SDK native rejects transport retry owner", lambda: package.Client(options=transport_owned).close())
    record(
        lines,
        "adapter accepts transport retry owner",
        lambda: package.Client(transport_adapter=Adapter(transport_module, lines), options=transport_owned).close(),
    )
    record(
        lines,
        "owned adapter accepts transport retry owner",
        lambda: package.Client(
            transport_adapter=transport_module.OwnedTransportAdapter(Adapter(transport_module, lines)),
            options=transport_owned,
        ).close(),
    )

    arrivals: list[tuple[str, str]] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        arrivals.append((request.method, request.url.path))
        return raw_response(200, b"configured TLS", "text/plain")(request)

    server = FixtureServer(reply)
    try:
        client = options.ClientOptions(
            base_url=f"https://localhost:{server.server_port}",
            transport=options.TransportOptions(
                ssl_context=_contexts()[1], max_connections=2, max_keepalive_connections=1, keepalive_expiry=1
            ),
        )
        with package.Client(options=client) as api:
            record(lines, "SDK native custom CA and pool", api.retry.get_safe)
            record(
                lines,
                "view rejects client construction options",
                lambda: api.with_options(options.ClientOptions(transport=options.TransportOptions())),
            )

        async def asynchronous() -> None:
            async with package.AsyncClient(options=client) as api:
                await arecord(lines, "SDK async custom CA and pool", api.retry.get_safe)

        run(asynchronous)
    finally:
        server.stop()
    lines.append(f"  configured native arrivals={arrivals}")


def retry_options(package: ModuleType, lines: list[str]) -> None:
    """Report public record validation and effective layered options using generated clients."""
    options = importlib.import_module(f"{package.__name__}.options")
    errors = importlib.import_module(f"{package.__name__}.errors")
    _invalid_numbers(options, lines)
    _invalid_values(options, lines)
    _origins(options, lines)
    _keys(options, errors, lines)
    _records(options, lines)
    _imports(package, lines)
    _merges(package, options, lines)
    _budgets(package, options, errors, lines)
    _vendor_headers(package, options, lines)
    _key_calls(package, options, lines)
    _key_wire(package, options, lines, asynchronous=False)
    _key_wire(package, options, lines, asynchronous=True)
    _transports(package, options, lines)
