"""Exercise generated native clients over real wire protocols and narrowly injected abnormal failures."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from tests.data.python.client_generation import SOURCE
from tests.data.python.client_runtime import Stop, arecord, argument, record, request_body, run
from tests.data.python.fixture_native import NativeFixture

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
    mode = "async" if asynchronous else "sync"
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
        proxy = {
            "tunnel": "tunnel",
            "forward": "forward",
            "tunnel-ipv6": "tunnel",
            "forward-ipv6": "forward",
            "rejected-proxy": "reject",
            "tunnel-certificate": "tunnel",
        }.get(kind)
        server = NativeFixture(
            http2=kind == "h2", proxy=proxy, malformed=kind == "malformed", ipv6=kind.endswith("ipv6")
        )
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

    sizes = json.loads((SOURCE / "defaults.json").read_text(encoding="utf-8"))["response_limits"]
    server = NativeFixture()
    try:
        for layer, size in (("default", sizes["large_bytes"]), ("error-default", sizes["error_bytes"])):
            server.body = b"x" * size
            server.status = 500 if layer.startswith("error") else 200
            for raw in (False, True):
                before = len(server.requests)
                if asynchronous:

                    async def call() -> None:
                        async with (
                            _http(server, asynchronous=True) as native,
                            package.AsyncClient(http_client=native, base_url=server.url, max_retries=0) as api,
                        ):
                            operation = (
                                api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                            )
                            try:
                                result = await operation()
                                lines.append(
                                    f"{mode} size {layer}/{size} raw={raw}: status={result.info.status_code} "
                                    f"bytes={len(result.body_bytes if raw else result.data.root)}"
                                )
                                if raw and layer.startswith("error"):
                                    await result.raise_for_status()
                            except Exception as error:
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
                        except Exception as error:
                            lines.append(
                                f"{mode} size {layer}/{size} raw={raw}: {type(error).__name__} "
                                f"bytes={len(getattr(error, 'body_bytes', b''))} "
                                f"truncated={getattr(error, 'truncated', None)}"
                            )
                lines.append(f"  arrivals={len(server.requests) - before}")
    finally:
        server.stop()


def _environment(package: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    """Let an HTTP client the SDK creates proxy through the environment HTTPX2 reads, and bypass a peer by NO_PROXY."""
    mode = "async" if asynchronous else "sync"
    for name, proxy, variable, bypass in (
        ("HTTPS_PROXY tunnel", "tunnel", "HTTPS_PROXY", ""),
        ("HTTP_PROXY forward", "forward", "HTTP_PROXY", ""),
        ("NO_PROXY bypass", None, "HTTPS_PROXY", "127.0.0.1"),
    ):
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
    mode = "async" if asynchronous else "sync"
    errors = importlib.import_module(f"{package.__name__}.errors")
    error_details: Callable[[Exception], str] = lambda error: (
        "APIConnectionError retry_outcome=permitted status=None cause=ConnectError"
        if (
            type(error) is errors.APIConnectionError
            and getattr(error, "info", None) is None
            and type(getattr(error, "cause", None)) is httpx2.ConnectError
            and getattr(error, "attempt_count", None) in (1, 3)
        )
        else _details(error)
    )
    for host in ("127.0.0.1", "::1", "localhost"):
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
                    for status in (301, 302, 303, 307, 308):
                        server.status = status
                        for name, location in _LOCATIONS:
                            server.location = location
                            for enabled in (False, True):
                                request_options = options.RequestOptions(follow_redirects=enabled)
                                for raw in (False, True):
                                    call = (
                                        api.retry.with_raw_response.get_safe
                                        if raw
                                        else api.retry.with_response.get_safe
                                    )
                                    await arecord(
                                        lines,
                                        f"{mode} location {status}/{name}/{enabled}/{raw}",
                                        lambda: _acalled(lambda: call(options=request_options)),
                                    )

            run(calls)
        else:
            with _http(server, asynchronous=False) as native, package.Client(http_client=native, **settings) as api:
                for status in (301, 302, 303, 307, 308):
                    server.status = status
                    for name, location in _LOCATIONS:
                        server.location = location
                        for enabled in (False, True):
                            request_options = options.RequestOptions(follow_redirects=enabled)
                            for raw in (False, True):
                                call = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                                record(
                                    lines,
                                    f"{mode} location {status}/{name}/{enabled}/{raw}",
                                    lambda: _called(lambda: call(options=request_options)),
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
        for ownership in ("borrowed",):
            if asynchronous:

                async def calls() -> None:
                    async def async_response(value: httpx2.Response) -> None:
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
                                    lambda: _acalled(operation, headers=True),
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
                            operation = (
                                api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                            )
                            record(
                                lines,
                                f"{mode} native {ownership} raw={raw}",
                                lambda: _called(operation, headers=True),
                            )
                        server.status, server.location = 302, _LOCATIONS[0][1]
                        record(
                            lines, f"{mode} h2 location {ownership}", lambda: _called(api.retry.with_response.get_safe)
                        )
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
    """Fail or interrupt a malformed redirect in the injected client's response hook, before HTTPX2 reads its Location."""
    mode = "async" if asynchronous else "sync"
    errors = importlib.import_module(f"{package.__name__}.errors")
    server = NativeFixture()
    server.status, server.location = 302, _LOCATIONS[0][1]
    try:
        for error in (RuntimeError("controlled"), Stop()):
            for raw in (False, True):
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
                            operation = (
                                api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                            )
                            try:
                                await operation()
                            except (errors.SDKError, Stop) as failure:
                                lines.append(
                                    f"  {label}: {_details(failure)} notes={getattr(failure, '__notes__', [])} same={failure is error}"
                                )
                            else:
                                lines.append(f"  {label}: unexpectedly returned")

                    run(call)
                else:
                    with (
                        httpx2.Client(
                            verify=server.verify, trust_env=False, event_hooks={"response": [hook]}
                        ) as native,
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
        lines.append(f"  {mode} response-hook arrivals={len(server.requests)}")
    finally:
        server.stop()


@pytest.mark.abnormal_path("A local peer cannot produce each kind of native send failure on demand.")
def native_faults(package: ModuleType, lines: list[str]) -> None:
    """Inject abnormal native send exceptions at the HTTP transport boundary."""
    from unittest.mock import patch

    options = importlib.import_module(f"{package.__name__}.options")
    retry = options.RetryOptions(initial_delay=0, jitter="none")
    kinds = (
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
    for asynchronous in (False, True):
        for kind in kinds:
            for method, key in (
                ("get_safe", None),
                ("get_keyed_safe", None),
                ("get_keyed_safe", "caller-key"),
                ("get_keyed_safe", "disabled"),
                ("post_unsafe", None),
            ):
                calls = 0

                def handle(transport: httpx2.HTTPTransport, request: httpx2.Request) -> httpx2.Response:
                    nonlocal calls
                    calls += 1
                    raise kind("controlled native send failure")

                async def ahandle(transport: httpx2.AsyncHTTPTransport, request: httpx2.Request) -> httpx2.Response:
                    nonlocal calls
                    calls += 1
                    raise kind("controlled native send failure")

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
                                    lambda: _acalled(
                                        lambda: getattr(api.retry.with_response, method)(options=request_options)
                                    ),
                                )

                    run(exercise)
                else:
                    with patch.object(httpx2.HTTPTransport, "handle_request", handle):
                        with package.Client(retry=retry) as api:
                            record(
                                lines,
                                label,
                                lambda: _called(
                                    lambda: getattr(api.retry.with_response, method)(options=request_options)
                                ),
                            )
                expected = 3 if kind in {httpx2.ConnectError, httpx2.ConnectTimeout} else 1
                lines.append(f"  native send invocations={calls} required={expected}")
    run(lambda: _native_cancel(package, lines))
    _native_close(package, lines)
    _phases(package, lines)


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

    def handle(transport: httpx2.HTTPTransport, request: httpx2.Request) -> httpx2.Response:
        return answer(request)

    async def ahandle(transport: httpx2.AsyncHTTPTransport, request: httpx2.Request) -> httpx2.Response:
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
            record(
                lines,
                f"sync {label}",
                lambda: _called(lambda: api.with_options(**view).retry.with_response.get_safe(options=call)),
            )
        lines.append(f"    phases={seen}")
        seen.clear()

        async def exercise() -> None:
            async with (
                httpx2.AsyncClient(**native) if injected else nullcontext() as http,
                package.AsyncClient(http_client=http, **root) as api,
            ):
                await arecord(
                    lines,
                    f"async {label}",
                    lambda: _acalled(lambda: api.with_options(**view).retry.with_response.get_safe(options=call)),
                )

        with patch.object(httpx2.AsyncHTTPTransport, "handle_async_request", ahandle):
            run(exercise)
        lines.append(f"    phases={seen}")


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
        raise RuntimeError("native close failed")

    try:
        with _trusted(server), patch.object(httpx2.HTTPTransport, "close", close):
            try:
                with package.Client(base_url=server.url) as api:
                    api.retry.get_safe()
                    raise ValueError("primary failure")
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
        raise RuntimeError("native close failed")

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
                f"  owned async primary={type(error).__name__} notes={len(getattr(error, '__notes__', ()))} count={pending.cancelling()}"
            )
        lines.append(f"  owned async native closes={closed}")


async def _native_cancel(package: ModuleType, lines: list[str]) -> None:
    """Cancel an actual TLS call in the native response hook and retain caller cancellation."""
    server = NativeFixture()
    reached, release = asyncio.Event(), asyncio.Event()

    async def response(value: httpx2.Response) -> None:
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
