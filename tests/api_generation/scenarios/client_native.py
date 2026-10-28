"""Exercise generated native clients over real wire protocols and narrowly injected abnormal failures."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
from contextlib import contextmanager, nullcontext
from functools import partial
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from tests.api_generation.support.client_generation import SOURCE
from tests.api_generation.support.client_runtime import Stop, arecord, argument, record, request_body, run
from tests.api_generation.support.fixture_native import NativeFixture

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from types import ModuleType


def _details(value: object) -> str:
    if isinstance(value, BaseException):
        info = getattr(value, "info", None)
        return (
            f"{type(value).__name__} "
            f"attempts={getattr(value, 'attempt_count', None)} reason={getattr(value, 'reason', None)} "
            f"status={None if info is None else info.status_code} cause={type(getattr(value, 'cause', None)).__name__}"
        )
    info = getattr(value, "info", None)
    return (
        f"{type(value).__name__} status={None if info is None else info.status_code} "
        f"attempts={None if info is None else info.attempt_count}"
    )


def _header_details(value: object) -> str:
    headers = getattr(value, "headers", value.info.headers)
    return f"{_details(value)} headers={headers.items()}"


def _called(
    call: Callable[[], object],
    *,
    headers: bool = False,
    error_details: Callable[[Exception], str] = _details,
) -> str:
    try:
        return (_header_details if headers else _details)(call())
    except Exception as error:  # ruff: ignore[blind-except]
        return error_details(error)


async def _acalled(
    call: Callable[[], Awaitable[object]],
    *,
    headers: bool = False,
    error_details: Callable[[Exception], str] = _details,
) -> str:
    try:
        return (_header_details if headers else _details)(await call())
    except Exception as error:  # ruff: ignore[blind-except]
        return error_details(error)


def _http(server: NativeFixture, *, asynchronous: bool, certificate: bool = True) -> Any:
    """Return an HTTP client with the fixture's authority, protocol, and proxy, ignoring the environment."""
    return (httpx2.AsyncClient if asynchronous else httpx2.Client)(
        verify=server.verify if certificate else True,
        http2=server.http2,
        proxy=server.proxy_url if server.proxy else None,
        trust_env=False,
    )


def _url(server: NativeFixture) -> str:
    if server.proxy:
        return ("http" if server.proxy == "forward" else "https") + "://" + ("[::1]" if server.ipv6 else "origin.test")
    return server.url


@contextmanager
def _trusted(server: NativeFixture) -> Iterator[None]:
    """Let an HTTP client the SDK creates trust the fixture through the environment HTTPX2 reads, without proxies."""
    with pytest.MonkeyPatch.context() as environment, TemporaryDirectory() as directory:
        for key in tuple(os.environ):
            if key.casefold() == "no_proxy":
                environment.delenv(key)
        environment.setenv("NO_PROXY", "*")
        authority = Path(directory) / "ca.pem"
        authority.write_bytes(server.ca_pem)
        environment.setenv("SSL_CERT_FILE", str(authority))
        yield


def _wire(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    for kind in (
        "h1",
        "h2",
        "tunnel",
        "forward",
        "tunnel-ipv6",
        "forward-ipv6",
        "rejected-proxy",
        "malformed",
        "certificate",
        "tunnel-certificate",
    ):
        _wire_kind(package, options, lines, kind, asynchronous=asynchronous)

    sizes = json.loads((SOURCE / "defaults.json").read_text(encoding="utf-8"))["response_limits"]
    server = NativeFixture()
    try:
        for layer, size in (("default", sizes["large_bytes"]), ("error-default", sizes["error_bytes"])):
            server.body = b"x" * size
            server.status = 500 if layer.startswith("error") else 200
            for raw in (False, True):
                _wire_size(package, server, lines, layer, size, raw=raw, asynchronous=asynchronous)
    finally:
        server.stop()


def _wire_kind(package: ModuleType, options: ModuleType, lines: list[str], kind: str, *, asynchronous: bool) -> None:
    """Call through one kind of wire peer with an injected HTTP client, recording what the peer saw."""
    mode = "async" if asynchronous else "sync"
    proxy = {
        "tunnel": "tunnel",
        "forward": "forward",
        "tunnel-ipv6": "tunnel",
        "forward-ipv6": "forward",
        "rejected-proxy": "reject",
        "tunnel-certificate": "tunnel",
    }.get(kind)
    server = NativeFixture(http2=kind == "h2", proxy=proxy, malformed=kind == "malformed", ipv6=kind.endswith("ipv6"))
    if kind in {"h1", "h2"}:
        server.extra_headers = ((b"x-duplicate", b"first"), (b"x-duplicate", b"second"), (b"x-text", b"caf\xe9"))
    if kind == "rejected-proxy":
        server.status, server.location = 407, b"https://localhost:bad/path"
    settings = {"base_url": _url(server), "retry": options.RetryOptions(initial_delay=0, jitter="none")}
    native = _http(server, asynchronous=asynchronous, certificate="certificate" not in kind)
    try:
        if asynchronous:

            async def call() -> None:
                async with native, package.AsyncClient(http_client=native, **settings) as api:
                    await arecord(
                        lines,
                        f"{mode} {kind}",
                        lambda: _acalled(api.retry.with_response.get_safe, headers=kind in {"h1", "h2"}),
                    )

            run(call)
        else:
            with native, package.Client(http_client=native, **settings) as api:
                record(
                    lines,
                    f"{mode} {kind}",
                    lambda: _called(api.retry.with_response.get_safe, headers=kind in {"h1", "h2"}),
                )
        lines.append(f"  arrivals={len(server.requests)} connects={server.connects} alpn={server.protocols}")
    finally:
        server.stop()


def _wire_size(
    package: ModuleType,
    server: NativeFixture,
    lines: list[str],
    layer: str,
    size: int,
    *,
    raw: bool,
    asynchronous: bool,
) -> None:
    """Read a body of one configured size limit, decoded or raw, recording how much the SDK kept."""
    mode = "async" if asynchronous else "sync"
    before = len(server.requests)
    if asynchronous:

        async def call() -> None:
            async with (
                _http(server, asynchronous=True) as native,
                package.AsyncClient(http_client=native, base_url=server.url, max_retries=0) as api,
            ):
                operation = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                try:
                    result = await operation()
                    lines.append(
                        f"{mode} size {layer}/{size} raw={raw}: status={result.info.status_code} "
                        f"bytes={len(result.body_bytes if raw else result.data.root)}"
                    )
                    if raw and layer.startswith("error"):
                        await result.raise_for_status()
                except Exception as error:  # noqa: BLE001
                    lines.append(
                        f"{mode} size {layer}/{size} raw={raw}: {type(error).__name__} "
                        f"bytes={len(getattr(error, 'body_bytes', b''))} "
                        f"truncated={getattr(error, 'truncated', None)}"
                    )

        run(call)
    else:
        with (
            _http(server, asynchronous=False) as native,
            package.Client(http_client=native, base_url=server.url, max_retries=0) as api,
        ):
            operation = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
            try:
                result = operation()
                lines.append(
                    f"{mode} size {layer}/{size} raw={raw}: status={result.info.status_code} "
                    f"bytes={len(result.body_bytes if raw else result.data.root)}"
                )
                if raw and layer.startswith("error"):
                    result.raise_for_status()
            except Exception as error:  # noqa: BLE001
                lines.append(
                    f"{mode} size {layer}/{size} raw={raw}: {type(error).__name__} "
                    f"bytes={len(getattr(error, 'body_bytes', b''))} "
                    f"truncated={getattr(error, 'truncated', None)}"
                )
    lines.append(f"  arrivals={len(server.requests) - before}")


def _environment(package: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    """Let an HTTP client the SDK creates proxy through the environment HTTPX2 reads, and bypass a peer by NO_PROXY."""
    for name, proxy, variable, bypass in (
        ("HTTPS_PROXY tunnel", "tunnel", "HTTPS_PROXY", ""),
        ("HTTP_PROXY forward", "forward", "HTTP_PROXY", ""),
        ("NO_PROXY bypass", None, "HTTPS_PROXY", "127.0.0.1"),
    ):
        _environment_case(package, lines, name, proxy, variable, bypass, asynchronous=asynchronous)


def _environment_case(
    package: ModuleType,
    lines: list[str],
    name: str,
    proxy: str | None,
    variable: str,
    bypass: str,
    *,
    asynchronous: bool,
) -> None:
    """Call through one proxy environment variable, counting what the origin and the proxy peer saw."""
    mode = "async" if asynchronous else "sync"
    server, peer = NativeFixture(proxy=proxy), NativeFixture(proxy="tunnel")
    base_url = server.url if proxy is None else ("https" if proxy == "tunnel" else "http") + "://origin.test"
    try:
        with _trusted(server), pytest.MonkeyPatch.context() as environment:
            environment.setenv("NO_PROXY", bypass)
            environment.setenv(variable, server.proxy_url if proxy else peer.proxy_url)
            if asynchronous:

                async def call() -> None:
                    async with package.AsyncClient(base_url=base_url, max_retries=0) as api:
                        await arecord(
                            lines,
                            f"{mode} environment {name}",
                            lambda: _acalled(api.retry.with_response.get_safe),
                        )

                run(call)
            else:
                with package.Client(base_url=base_url, max_retries=0) as api:
                    record(lines, f"{mode} environment {name}", lambda: _called(api.retry.with_response.get_safe))
        lines.append(
            f"  origin arrivals={len(server.requests)} connects={server.connects} "
            f"proxy arrivals={len(peer.requests)} connects={peer.connects}"
        )
    finally:
        server.stop()
        peer.stop()


def _refusal(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    errors = importlib.import_module(f"{package.__name__}.errors")

    def error_details(error: Exception) -> str:
        return (
            "APIConnectionError retry_outcome=permitted status=None cause=ConnectError"
            if (
                type(error) is errors.APIConnectionError
                and getattr(error, "info", None) is None
                and type(getattr(error, "cause", None)) is httpx2.ConnectError
                and getattr(error, "attempt_count", None) in {1, 3}
            )
            else _details(error)
        )

    for host in ("127.0.0.1", "::1", "localhost"):
        _refused(package, options, lines, host, error_details, asynchronous=asynchronous)


def _refused(
    package: ModuleType,
    options: ModuleType,
    lines: list[str],
    host: str,
    error_details: Callable[[Exception], str],
    *,
    asynchronous: bool,
) -> None:
    """Send an unsafe call to a port on one host that was just released, so the connection is refused."""
    mode = "async" if asynchronous else "sync"
    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    with socket.socket(family) as held:
        held.bind(("::1" if family == socket.AF_INET6 else "127.0.0.1", 0))
        port = held.getsockname()[1]
        held.close()
        address = f"[{host}]" if ":" in host else host
        url = f"https://{address}:{port}"
        settings = {"base_url": url, "retry": options.RetryOptions(initial_delay=0, jitter="none")}
        if asynchronous:

            async def call() -> None:
                async with package.AsyncClient(**settings) as api:
                    await arecord(
                        lines,
                        f"{mode} refused {host} unsafe",
                        lambda: _acalled(api.retry.with_response.post_unsafe, error_details=error_details),
                    )

            run(call)
        else:
            with package.Client(**settings) as api:
                record(
                    lines,
                    f"{mode} refused {host} unsafe",
                    lambda: _called(api.retry.with_response.post_unsafe, error_details=error_details),
                )


_LOCATIONS = (
    ("port", b"https://localhost:bad/path"),
    ("ipv6", b"https://[bad/path"),
    ("idna", "https://☃.example/path".encode()),
    ("control", b"https://localhost/\x01bad"),
    ("length", b"https://localhost/" + b"a" * 65537),
)
_LOCATION_CASES = tuple(product((301, 302, 303, 307, 308), _LOCATIONS, (False, True), (False, True)))


def _locations(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    mode = "async" if asynchronous else "sync"
    server = NativeFixture()
    settings = {"base_url": server.url, "retry": options.RetryOptions(initial_delay=0, jitter="none")}
    try:
        if asynchronous:

            async def calls() -> None:
                async with (
                    _http(server, asynchronous=True) as native,
                    package.AsyncClient(http_client=native, **settings) as api,
                ):
                    for status, (name, location), enabled, raw in _LOCATION_CASES:
                        server.status, server.location = status, location
                        call = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                        request_options = options.RequestOptions(follow_redirects=enabled)
                        await arecord(
                            lines,
                            f"{mode} location {status}/{name}/{enabled}/{raw}",
                            partial(_acalled, partial(call, options=request_options)),
                        )

            run(calls)
        else:
            with _http(server, asynchronous=False) as native, package.Client(http_client=native, **settings) as api:
                for status, (name, location), enabled, raw in _LOCATION_CASES:
                    server.status, server.location = status, location
                    call = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                    request_options = options.RequestOptions(follow_redirects=enabled)
                    record(
                        lines,
                        f"{mode} location {status}/{name}/{enabled}/{raw}",
                        partial(_called, partial(call, options=request_options)),
                    )
        lines.append(f"  {mode} location arrivals={len(server.requests)}")
    finally:
        server.stop()


def _borrowed(package: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    mode = "async" if asynchronous else "sync"
    server = NativeFixture(http2=True)
    server.extra_headers = ((b"x-remove", b"wire"),)
    held: list[httpx2.Response] = []

    def response(value: httpx2.Response) -> None:
        held.append(value)
        if value.status_code == 200:
            value.headers["content-type"] = "text/plain; charset=ascii"
            del value.headers["x-remove"]
            value.headers.update((("X-Native-Hook", "first"), ("X-Native-Hook", "second")))

    try:
        ownership = "borrowed"
        if asynchronous:

            async def calls() -> None:
                async def async_response(value: httpx2.Response) -> None:  # noqa: RUF029
                    response(value)

                async with httpx2.AsyncClient(
                    verify=server.verify,
                    http2=True,
                    trust_env=False,
                    event_hooks={"response": [async_response]},
                    auth=("foreign", "credential"),
                    cookies={"foreign": "cookie"},
                    headers={"authorization": "Bearer foreign", "x-foreign": "default"},
                    params={"foreign": "query"},
                ) as native:
                    async with package.AsyncClient(base_url=server.url, http_client=native) as api:
                        server.status, server.location = 200, None
                        for raw in (False, True):
                            operation = (
                                api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                            )
                            await arecord(
                                lines,
                                f"{mode} native {ownership} raw={raw}",
                                partial(_acalled, operation, headers=True),
                            )
                        server.status, server.location = 302, _LOCATIONS[0][1]
                        await arecord(
                            lines,
                            f"{mode} h2 location {ownership}",
                            lambda: _acalled(api.retry.with_response.get_safe),
                        )
                    lines.append(f"  {mode} native {ownership} closed={native.is_closed}")

            run(calls)
        else:
            with httpx2.Client(
                verify=server.verify,
                http2=True,
                trust_env=False,
                event_hooks={"response": [response]},
                auth=("foreign", "credential"),
                cookies={"foreign": "cookie"},
                headers={"authorization": "Bearer foreign", "x-foreign": "default"},
                params={"foreign": "query"},
            ) as native:
                with package.Client(base_url=server.url, http_client=native) as api:
                    server.status, server.location = 200, None
                    for raw in (False, True):
                        operation = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                        record(
                            lines,
                            f"{mode} native {ownership} raw={raw}",
                            partial(_called, operation, headers=True),
                        )
                    server.status, server.location = 302, _LOCATIONS[0][1]
                    record(lines, f"{mode} h2 location {ownership}", lambda: _called(api.retry.with_response.get_safe))
                lines.append(f"  {mode} native {ownership} closed={native.is_closed}")
        lines.append(f"  {mode} native captured responses closed={[response.is_closed for response in held]}")
        leaked = any(
            name.lower() in {b"authorization", b"cookie", b"x-foreign"}
            for fields in server.request_headers
            for name, _ in fields
        ) or any(b"foreign" in target for _, target, _ in server.requests)
        lines.append(f"  {mode} borrowed client defaults leaked={leaked}")
    finally:
        server.stop()


def native_wire(package: ModuleType, lines: list[str]) -> None:
    """Observe SDK-created native requests and metadata through real TCP, TLS, HTTP/2 and proxy peers."""
    options = importlib.import_module(f"{package.__name__}.options")
    for asynchronous in (False, True):
        _wire(package, options, lines, asynchronous=asynchronous)
        _environment(package, lines, asynchronous=asynchronous)
        _refusal(package, options, lines, asynchronous=asynchronous)
        _locations(package, options, lines, asynchronous=asynchronous)
        _borrowed(package, lines, asynchronous=asynchronous)
        _location_hooks(package, lines, asynchronous=asynchronous)


class _ResponseHook:
    """Fail or interrupt each response the injected HTTP client receives, recording the statuses it saw."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.statuses: list[int] = []

    def __call__(self, response: httpx2.Response) -> None:
        self.statuses.append(response.status_code)
        raise self.error

    async def asynchronous(self, response: httpx2.Response) -> None:
        self(response)


def _location_hooks(package: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    """Fail or interrupt a malformed redirect in the injected client's response hook, before its Location is read."""
    mode = "async" if asynchronous else "sync"
    errors = importlib.import_module(f"{package.__name__}.errors")
    server = NativeFixture()
    server.status, server.location = 302, _LOCATIONS[0][1]
    try:
        for error in (RuntimeError("controlled"), Stop()):
            for raw in (False, True):
                _location_hook(package, errors, server, lines, error, raw=raw, asynchronous=asynchronous)
        lines.append(f"  {mode} response-hook arrivals={len(server.requests)}")
    finally:
        server.stop()


def _location_hook(
    package: ModuleType,
    errors: ModuleType,
    server: NativeFixture,
    lines: list[str],
    error: BaseException,
    *,
    raw: bool,
    asynchronous: bool,
) -> None:
    """Raise one error from the injected client's response hook, recording what the call raised."""
    mode = "async" if asynchronous else "sync"
    hook = _ResponseHook(error)
    label = f"{mode} malformed Location response-hook {type(error).__name__} raw={raw}"
    if asynchronous:

        async def call() -> None:
            async with (
                httpx2.AsyncClient(
                    verify=server.verify, trust_env=False, event_hooks={"response": [hook.asynchronous]}
                ) as native,
                package.AsyncClient(http_client=native, base_url=server.url) as api,
            ):
                operation = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                try:
                    await operation()
                except (errors.SDKError, Stop) as failure:
                    lines.append(
                        f"  {label}: {_details(failure)} notes={getattr(failure, '__notes__', [])} "
                        f"same={failure is error}"
                    )
                else:
                    lines.append(f"  {label}: unexpectedly returned")

        run(call)
    else:
        with (
            httpx2.Client(verify=server.verify, trust_env=False, event_hooks={"response": [hook]}) as native,
            package.Client(http_client=native, base_url=server.url) as api,
        ):
            operation = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
            try:
                operation()
            except (errors.SDKError, Stop) as failure:
                lines.append(
                    f"  {label}: {_details(failure)} notes={getattr(failure, '__notes__', [])} same={failure is error}"
                )
            else:
                lines.append(f"  {label}: unexpectedly returned")
    lines.append(f"  response-hook statuses={hook.statuses}")


_NATIVE_FAULT_KINDS = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.PoolTimeout,
    httpx2.ReadError,
    httpx2.ReadTimeout,
    httpx2.WriteError,
    httpx2.WriteTimeout,
    httpx2.RemoteProtocolError,
    RuntimeError,
)


@pytest.mark.abnormal_path("A local peer cannot produce each kind of native send failure on demand.")
def native_faults(package: ModuleType, lines: list[str]) -> None:
    """Inject abnormal native send exceptions at the HTTP transport boundary."""
    options = importlib.import_module(f"{package.__name__}.options")
    retry = options.RetryOptions(initial_delay=0, jitter="none")
    for asynchronous in (False, True):
        for kind in _NATIVE_FAULT_KINDS:
            for method, key in (
                ("get_safe", None),
                ("get_keyed_safe", None),
                ("get_keyed_safe", "caller-key"),
                ("get_keyed_safe", "disabled"),
                ("post_unsafe", None),
            ):
                _native_fault(package, options, retry, lines, kind, method, key, asynchronous=asynchronous)
    run(lambda: _native_cancel(package, lines))
    _native_close(package, lines)
    _phases(package, lines)


@pytest.mark.abnormal_path("A local peer cannot produce each kind of native send failure on demand.")
def _native_fault(
    package: ModuleType,
    options: ModuleType,
    retry: object,
    lines: list[str],
    kind: type[Exception],
    method: str,
    key: str | None,
    *,
    asynchronous: bool,
) -> None:
    """Fail every native send of one call with one exception kind, counting the sends the SDK made."""
    from unittest.mock import patch

    calls = 0

    def handle(transport: httpx2.HTTPTransport, request: httpx2.Request) -> httpx2.Response:  # noqa: ARG001
        nonlocal calls
        calls += 1
        msg = "controlled native send failure"
        raise kind(msg)

    async def ahandle(transport: httpx2.AsyncHTTPTransport, request: httpx2.Request) -> httpx2.Response:  # noqa: ARG001, RUF029
        nonlocal calls
        calls += 1
        msg = "controlled native send failure"
        raise kind(msg)

    label = f"{'async' if asynchronous else 'sync'} native {kind.__name__} {method} key={key!r}"
    request_options = (
        None
        if key is None
        else options.RequestOptions(idempotency_key=None)
        if key == "disabled"
        else options.RequestOptions(idempotency_key=key)
    )
    if asynchronous:

        async def exercise() -> None:
            with patch.object(httpx2.AsyncHTTPTransport, "handle_async_request", ahandle):
                async with package.AsyncClient(retry=retry) as api:
                    await arecord(
                        lines,
                        label,
                        lambda: _acalled(lambda: getattr(api.retry.with_response, method)(options=request_options)),
                    )

        run(exercise)
    else:
        with patch.object(httpx2.HTTPTransport, "handle_request", handle), package.Client(retry=retry) as api:
            record(
                lines,
                label,
                lambda: _called(lambda: getattr(api.retry.with_response, method)(options=request_options)),
            )
    expected = 3 if kind in {httpx2.ConnectError, httpx2.ConnectTimeout} else 1
    lines.append(f"  native send invocations={calls} required={expected}")


def _phases(package: ModuleType, lines: list[str]) -> None:
    """Observe the timeouts and redirects each request goes out with, on an SDK-owned and on an injected client."""
    from unittest.mock import patch

    options = importlib.import_module(f"{package.__name__}.options")
    seen: list[object] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.extensions["timeout"])
        if request.url.path == "/moved":
            return httpx2.Response(200, headers={"Content-Type": "text/plain"}, stream=httpx2.ByteStream(b"moved"))
        return httpx2.Response(302, headers={"Location": "/moved"}, stream=httpx2.ByteStream(b""))

    def handle(transport: httpx2.HTTPTransport, request: httpx2.Request) -> httpx2.Response:  # noqa: ARG001
        return answer(request)

    async def ahandle(transport: httpx2.AsyncHTTPTransport, request: httpx2.Request) -> httpx2.Response:  # noqa: ARG001, RUF029
        return answer(request)

    phases = options.RequestOptions(timeout=httpx2.Timeout(1, connect=3))
    native = {
        "timeout": httpx2.Timeout(7, connect=2),
        "follow_redirects": True,
        "transport": httpx2.MockTransport(answer),
    }
    for label, injected, root, view, call in (
        ("owned default", False, {}, {}, None),
        ("owned follows when asked", False, {"follow_redirects": True}, {}, None),
        ("owned timeout", False, {"timeout": 2.5}, {}, None),
        ("view lifts the limits", False, {"timeout": 2.5}, {"timeout": None}, None),
        ("call phases over the view", False, {"timeout": 2.5}, {"timeout": None}, phases),
        ("injected keeps its own", True, {}, {}, None),
        ("injected given a timeout", True, {"timeout": 4}, {}, None),
        ("injected redirects declined", True, {"follow_redirects": False}, {}, None),
        ("injected view redirects declined", True, {}, {"follow_redirects": False}, None),
    ):
        seen.clear()
        with (
            patch.object(httpx2.HTTPTransport, "handle_request", handle),
            httpx2.Client(**native) if injected else nullcontext() as http,
            package.Client(http_client=http, **root) as api,
        ):
            record(lines, f"sync {label}", partial(_called, partial(_phase_call, api, view, call)))
        lines.append(f"    phases={seen}")
        seen.clear()
        with patch.object(httpx2.AsyncHTTPTransport, "handle_async_request", ahandle):
            run(partial(_aphase, package, lines, native, label, root, view, call, injected=injected))
        lines.append(f"    phases={seen}")


def _phase_call(api: Any, view: dict[str, Any], call: object) -> Any:
    """Call through a view of a client with request options, as one phase row asks."""
    return api.with_options(**view).retry.with_response.get_safe(options=call)


async def _aphase(
    package: ModuleType,
    lines: list[str],
    native: dict[str, Any],
    label: str,
    root: dict[str, Any],
    view: dict[str, Any],
    call: object,
    *,
    injected: bool,
) -> None:
    """Call one phase row on an asynchronous client, owned or injected."""
    async with (
        httpx2.AsyncClient(**native) if injected else nullcontext() as http,
        package.AsyncClient(http_client=http, **root) as api,
    ):
        await arecord(lines, f"async {label}", partial(_acalled, partial(_phase_call, api, view, call)))


@pytest.mark.abnormal_path("A failing close of the transport the SDK owns cannot be produced with a real connection.")
def _native_close(package: ModuleType, lines: list[str]) -> None:
    """Preserve a primary failure while the native owned client's close fails after release."""
    from unittest.mock import patch

    server = NativeFixture()
    closed = 0
    original = httpx2.HTTPTransport.close

    def close(transport: httpx2.HTTPTransport) -> None:
        nonlocal closed
        closed += 1
        original(transport)
        msg = "native close failed"
        raise RuntimeError(msg)

    try:
        with _trusted(server), patch.object(httpx2.HTTPTransport, "close", close):
            try:
                with package.Client(base_url=server.url) as api:
                    api.retry.get_safe()
                    msg = "primary failure"
                    raise ValueError(msg)  # noqa: TRY301
            except ValueError as error:
                lines.append(
                    f"  owned sync primary={type(error).__name__} notes={len(getattr(error, '__notes__', ()))}"
                )
            api.close()
            lines.append(f"  owned sync native closes={closed}")
        with _trusted(server):
            run(lambda: _native_async_close(package, lines, server.url))
    finally:
        server.stop()


@pytest.mark.abnormal_path("A failing close of the transport the SDK owns cannot be produced with a real connection.")
async def _native_async_close(package: ModuleType, lines: list[str], base_url: str) -> None:
    """Keep task cancellation primary beside an owned native asynchronous close failure."""
    from unittest.mock import patch

    closed = 0
    original = httpx2.AsyncHTTPTransport.aclose

    async def close(transport: httpx2.AsyncHTTPTransport) -> None:
        nonlocal closed
        closed += 1
        await original(transport)
        msg = "native close failed"
        raise RuntimeError(msg)

    async def body() -> None:
        async with package.AsyncClient(base_url=base_url) as api:
            await api.retry.get_safe()
            task = asyncio.current_task()
            task.cancel()
            await asyncio.sleep(0)

    with patch.object(httpx2.AsyncHTTPTransport, "aclose", close):
        pending = asyncio.create_task(body())
        try:
            await pending
        except asyncio.CancelledError as error:
            lines.append(
                f"  owned async primary={type(error).__name__} notes={len(getattr(error, '__notes__', ()))} "
                f"count={pending.cancelling()}"
            )
        lines.append(f"  owned async native closes={closed}")


async def _native_cancel(package: ModuleType, lines: list[str]) -> None:
    """Cancel an actual TLS call in the native response hook and retain caller cancellation."""
    server = NativeFixture()
    reached, release = asyncio.Event(), asyncio.Event()

    async def response(value: httpx2.Response) -> None:  # noqa: ARG001
        reached.set()
        await release.wait()

    try:
        async with httpx2.AsyncClient(
            verify=server.verify, trust_env=False, event_hooks={"response": [response]}
        ) as native:
            async with package.AsyncClient(http_client=native, base_url=server.url) as api:
                view = api.with_options()
                pending = asyncio.create_task(view.retry.get_safe())
                await asyncio.wait_for(reached.wait(), 5)
                pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    lines.append(f"  native task cancellation propagated count={pending.cancelling()}")
                release.set()
                await arecord(lines, "call after native cancellation", api.retry.get_safe)
                await api.aclose()
                await api.aclose()
                lines.append(f"  borrowed native after repeated root close={native.is_closed}")
            lines.append(f"  real requests including cancelled response={len(server.requests)}")
    finally:
        server.stop()


def native_codec_backends(package: ModuleType, lines: list[str]) -> None:
    """Decode and send fixed pet models through each backend over actual sync and async TLS calls."""
    trace = argument(package, "listPets", "header", "X-Trace", "trace")
    body = request_body(package, "createPet", "application/json", {"name": "dog", "tag": "a"})
    for asynchronous in (False, True):
        _native_codec_backend(package, lines, trace, body, asynchronous=asynchronous)


def _native_codec_backend(
    package: ModuleType, lines: list[str], trace: object, body: object, *, asynchronous: bool
) -> None:
    """Decode and send the fixed pet models over one sync or async TLS client, recording the requests sent."""
    server = NativeFixture()
    server.content_type = b"application/json"
    server.body = b'[{"id":1,"name":"dog","tag":"a"}]'
    mode = "async" if asynchronous else "sync"
    try:
        if asynchronous:

            async def call() -> None:
                async with (
                    _http(server, asynchronous=True) as native,
                    package.AsyncClient(http_client=native, base_url=server.url) as api,
                ):
                    await arecord(lines, f"{mode} native decode", lambda: api.pets.list_pets(X_Trace=trace))
                    server.status, server.body = 201, b'{"id":2,"name":"dog","tag":"a"}'
                    await arecord(
                        lines,
                        f"{mode} native encode",
                        lambda: api.pets.create_pet(body=body, media_type="application/json"),
                    )

            run(call)
        else:
            with (
                _http(server, asynchronous=False) as native,
                package.Client(http_client=native, base_url=server.url) as api,
            ):
                record(lines, f"{mode} native decode", lambda: api.pets.list_pets(X_Trace=trace))
                server.status, server.body = 201, b'{"id":2,"name":"dog","tag":"a"}'
                record(
                    lines,
                    f"{mode} native encode",
                    lambda: api.pets.create_pet(body=body, media_type="application/json"),
                )
        lines.extend(
            f"  native request {method.decode()} {path.decode()} {content!r}"
            for method, path, content in server.requests
        )
    finally:
        server.stop()
