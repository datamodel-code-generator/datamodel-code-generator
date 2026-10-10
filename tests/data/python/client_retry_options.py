"""Exercise retry, redirect, key, and construction options through a generated client's public surface."""

from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, get_type_hints

import httpx2

from tests.data.python.client_retry_policy import _Waits
from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run
from tests.data.python.fixture_native import NativeFixture

if TYPE_CHECKING:
    from types import ModuleType


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
    for name in ("initial_delay", "max_delay", "max_retry_after"):
        record(lines, f"retry {name} overflow", lambda name=name: options.RetryOptions(**{name: 10**400}))
    record(lines, "retry cap zero", lambda: options.RetryOptions(max_retry_after=0))
    record(lines, "retry delay order", lambda: options.RetryOptions(initial_delay=2, max_delay=1))


def _retry_counts(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Refuse a retry count that is not a nonnegative integer at the client, a view, and a call; honor zero at each."""
    with package.Client() as api:
        for label, value in (("bool", True), ("negative", -1), ("fractional", 0.5)):
            record(lines, f"client max_retries {label}", lambda value=value: package.Client(max_retries=value))
            record(lines, f"view max_retries {label}", lambda value=value: api.with_options(max_retries=value))
            record(lines, f"call max_retries {label}", lambda value=value: options.RequestOptions(max_retries=value))
    exchange = Exchange([])
    with exchange.client() as native:
        for layer in ("client", "view", "call"):
            exchange.respond(raw_response(503, b"busy", "text/plain"), raw_response(200, b"unused", "text/plain"))
            with package.Client(
                http_client=native,
                max_retries=0 if layer == "client" else 2,
                retry=options.RetryOptions(initial_delay=0),
            ) as api:
                current = api.with_options(max_retries=0) if layer == "view" else api
                request = options.RequestOptions(max_retries=0) if layer == "call" else None
                record(
                    lines,
                    f"{layer} max_retries zero",
                    lambda current=current, request=request: current.retry.get_safe(options=request),
                )
            lines.append(f"  {layer} max_retries zero pending responses={len(exchange.responders)}")
            exchange.responders.clear()


def _invalid_values(options: ModuleType, lines: list[str]) -> None:
    for label, value in (
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


def _records(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    value = options.RetryOptions()
    omitted = tuple((item.name, getattr(value, item.name) is options.UNSET) for item in fields(value))
    lines.append(f"  omitted RetryOptions={omitted}")
    statuses = {408, 409, 500, 501}
    retry = options.RetryOptions(statuses=statuses, initial_delay=0, max_delay=0, jitter="none", max_retry_after=None)
    statuses.add(503)
    lines.append(f"  frozen statuses={sorted(retry.statuses)} frozenset={type(retry.statuses) is frozenset}")
    record(lines, "retry empty statuses", lambda: options.RetryOptions(statuses=frozenset()))
    record(
        lines,
        "retry explicit defaults",
        lambda: options.RetryOptions(
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
    for value in (False, True):
        record(lines, f"follow redirects {value}", lambda value=value: options.RequestOptions(follow_redirects=value))
    for value, name, replacement in (
        (retry, "initial_delay", 7),
        (options.RequestOptions(), "follow_redirects", True),
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
    for owner in (options.RetryOptions, options.RequestOptions):
        lines.extend((
            f"  hints {owner.__name__}={tuple(get_type_hints(owner, include_extras=True))}",
            f"  init hints {owner.__name__}={tuple(get_type_hints(owner.__init__, include_extras=True))}",
        ))
    for label, method in (("Client", package.Client.__init__), ("with_options", package.Client.with_options)):
        lines.append(f"  init hints {label}={tuple(get_type_hints(method, include_extras=True))}")
    value = options.RequestOptions()
    omitted = (
        value.max_retries is None,
        value.retry is None,
        value.follow_redirects is None,
        value.idempotency_key is options.UNSET,
    )
    lines.append(f"  option omitted RequestOptions={omitted}")


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
for name in ("RetryOptions", "RequestOptions", "ServerSelection"):
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


class _Statuses:
    """Record the status of each response the injected HTTP client receives, through its own response hook."""

    def __init__(self) -> None:
        self.values: list[int] = []

    def __call__(self, response: httpx2.Response) -> None:
        self.values.append(response.status_code)


def _merges(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    exchange.respond(
        raw_response(501, b"first", "text/plain", **{"Retry-After": "999"}),
        raw_response(501, b"second", "text/plain", **{"Retry-After": "999"}),
        raw_response(200, b"merged", "text/plain"),
    )
    received, waits = _Statuses(), _Waits()
    with (
        exchange.client(event_hooks={"response": [received]}) as native,
        package.Client(
            http_client=native,
            max_retries=1,
            retry=options.RetryOptions(
                initial_delay=0.25,
                max_delay=0.5,
                jitter="none",
                statuses={409},
                max_retry_after=1,
                respect_retry_after=False,
            ),
            clock=options.Clock(monotonic=waits.monotonic, sleep=waits.sleep),
        ) as api,
    ):
        first = api.with_options(max_retries=2)
        view = first.with_options(retry=options.RetryOptions(max_retry_after=None))
        record(
            lines,
            "retry nested client/view/view/call",
            lambda: view.retry.get_safe(options=options.RequestOptions(retry=options.RetryOptions(statuses={501}))),
        )
    lines.append(
        f"  merged retry statuses={received.values} waits={tuple(waits.delays)}"
        f" pending responses={len(exchange.responders)}"
    )

    exchange = Exchange(lines)
    exchange.respond(raw_response(200, b"valid inherited delay", "text/plain"))
    with (
        exchange.client() as native,
        package.Client(http_client=native, retry=options.RetryOptions(max_delay=20)) as api,
    ):
        view = api.with_options(retry=options.RetryOptions(initial_delay=10))
        record(lines, "partial delay inherits larger maximum", view.retry.get_safe)
        record(
            lines,
            "invalid delay after request merge",
            lambda: view.retry.get_safe(options=options.RequestOptions(retry=options.RetryOptions(max_delay=5))),
        )
        record(
            lines,
            "invalid delay after view merge",
            lambda: api.with_options(retry=options.RetryOptions(initial_delay=21)),
        )
    lines.append(f"  merged delay pending responses={len(exchange.responders)}")
    record(
        lines,
        "invalid delay after client merge",
        lambda: package.Client(retry=options.RetryOptions(initial_delay=9)),
    )

    exchange = Exchange(lines)
    exchange.respond(
        raw_response(302, Location="/one"),
        raw_response(302, Location="/two"),
        raw_response(503, b"retry", "text/plain"),
        raw_response(200, b"four sends", "text/plain"),
    )
    with (
        exchange.client(max_redirects=2) as native,
        package.Client(
            http_client=native,
            max_retries=0,
            retry=options.RetryOptions(initial_delay=0, max_delay=0, jitter="none"),
        ) as api,
    ):
        view = api.with_options(follow_redirects=True)
        record(
            lines,
            "redirects and a retry within the final fields",
            lambda: (
                (response := view.retry.with_response.get_safe(options=options.RequestOptions(max_retries=1))).data,
                response.info.attempt_count,
            ),
        )
    lines.append(f"  merged redirect pending responses={len(exchange.responders)}")

    exchange = Exchange(lines)
    exchange.respond(
        raw_response(303, Location="https://other.example.com/accepted"),
        raw_response(200, b"inherited destination and method permission", "text/plain"),
    )
    with (
        exchange.client() as native,
        package.Client(http_client=native, max_retries=0, follow_redirects=True) as api,
    ):
        view = api.with_options(follow_redirects=False)
        record(
            lines,
            "a view's redirect choice over the client's, and the call's over the view's",
            lambda: view.retry.post_unsafe(
                body=b"removed after 303", options=options.RequestOptions(follow_redirects=True)
            ),
        )
    lines.append(f"  merged redirect permissions pending responses={len(exchange.responders)}")


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
        retry = options.RetryOptions(initial_delay=0, max_delay=0, jitter="none")
        with exchange.client() as native, package.Client(http_client=native, retry=retry) as api:
            view = api.with_options(retry=configured)
            record(
                lines,
                f"vendor {label}",
                lambda view=view: view.retry.get_vendor(options=options.RequestOptions(retry=options.RetryOptions())),
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


def _key_calls(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    observed: list[str | None] = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        observed.append(request.headers.get("Idempotency-Key"))
        status, body = (503, b"retry") if len(observed) == 1 else (200, b"accepted")
        return raw_response(status, body, "text/plain")(request)

    exchange = Exchange(lines)
    exchange.respond(reply, reply, reply, reply)
    retry = options.RetryOptions(initial_delay=0, max_delay=0, jitter="none")
    with exchange.client() as native, package.Client(http_client=native, retry=retry) as api:
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
            lambda: api.retry.get_safe(options=options.RequestOptions(idempotency_key="unbound")),
        )
    lines.append(
        f"  automatic keys count={len(observed)} same retry={observed[0] == observed[1]}"
        f" fresh call={observed[1] != observed[2]} suppressed={observed[3] is None}"
    )

    observed.clear()
    exchange = Exchange(lines)
    exchange.respond(reply, reply)
    with exchange.client() as native, package.Client(http_client=native, retry=retry) as api:
        record(
            lines,
            "caller key retained through retry",
            lambda: api.retry.post_keyed(options=options.RequestOptions(idempotency_key="caller-owned")),
        )
    lines.append(f"  caller key observed={observed}")


def _key_fields(server: NativeFixture) -> tuple[bytes, ...]:
    return tuple(value for name, value in server.request_headers[-1] if name.lower() == b"idempotency-key")


_KEY_VALUES = ("a b", "a\tb", "a \t b", "clé", "\u00a0key\u00a0", "\ud7ffkey\ue000")


def _key_wire(package: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Send caller keys unchanged over HTTP/1.1 and HTTP/2 through injected HTTP clients configured for TLS."""
    for http2 in (False, True):
        server = NativeFixture(http2=http2)
        protocol = "h2" if http2 else "h1"
        try:
            with (
                httpx2.Client(verify=server.verify, http2=http2) as native,
                package.Client(base_url=server.url, http_client=native) as api,
            ):
                for value in _KEY_VALUES:
                    record(
                        lines,
                        f"key sync {protocol} value={value!r}",
                        lambda value=value: api.retry.post_keyed(options=options.RequestOptions(idempotency_key=value)),
                    )
                    lines.append(f"  key wire={_key_fields(server)!r}")

            async def calls(protocol: str = protocol, server: NativeFixture = server, http2: bool = http2) -> None:
                async with (
                    httpx2.AsyncClient(verify=server.verify, http2=http2) as native,
                    package.AsyncClient(base_url=server.url, http_client=native) as api,
                ):
                    for value in _KEY_VALUES:
                        await arecord(
                            lines,
                            f"key async {protocol} value={value!r}",
                            lambda value=value: api.retry.post_keyed(
                                options=options.RequestOptions(idempotency_key=value)
                            ),
                        )
                        lines.append(f"  key wire={_key_fields(server)!r}")

            run(calls)
            lines.append(f"  key {protocol} arrivals={len(server.requests)} alpn={server.protocols}")
        finally:
            server.stop()


def retry_options(package: ModuleType, lines: list[str]) -> None:
    """Report public record validation and effective layered options using generated clients."""
    options = importlib.import_module(f"{package.__name__}.options")
    _invalid_numbers(options, lines)
    _retry_counts(package, options, lines)
    _invalid_values(options, lines)
    _records(package, options, lines)
    _imports(package, lines)
    _merges(package, options, lines)
    _vendor_headers(package, options, lines)
    _key_calls(package, options, lines)
    _key_wire(package, options, lines)
