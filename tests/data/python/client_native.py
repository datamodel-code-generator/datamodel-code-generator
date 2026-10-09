"""Exercise generated native clients over real wire protocols and narrowly injected abnormal failures."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import httpx2
import pytest

from tests.data.python.client_generation import SOURCE
from tests.data.python.client_runtime import Stop, arecord, argument, record, request_body, run
from tests.data.python.fixture_native import NativeFixture

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
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


def _options(module: ModuleType, server: NativeFixture, *, certificate: bool = True) -> object:
    return module.ClientOptions(
        base_url=_url(server),
        transport=module.TransportOptions(
            ssl_context=server.verify if certificate else None,
            http2=server.http2,
            proxy=server.proxy_url if server.proxy else None,
        ),
        retry=module.RetryOptions(initial_delay=0, jitter="none"),
    )


def _url(server: NativeFixture) -> str:
    if server.proxy:
        return ("http" if server.proxy == "forward" else "https") + "://" + ("[::1]" if server.ipv6 else "origin.test")
    return server.url


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
        client_options = _options(options, server, certificate="certificate" not in kind)
        try:
            if asynchronous:

                async def call() -> None:
                    async with package.AsyncClient(options=client_options) as api:
                        await arecord(
                            lines,
                            f"{mode} {kind}",
                            lambda: _acalled(api.retry.with_response.get_safe, headers=kind in {"h1", "h2"}),
                        )

                run(call)
            else:
                with package.Client(options=client_options) as api:
                    record(
                        lines,
                        f"{mode} {kind}",
                        lambda: _called(api.retry.with_response.get_safe, headers=kind in {"h1", "h2"}),
                    )
            lines.append(f"  arrivals={len(server.requests)} connects={server.connects} alpn={server.protocols}")
        finally:
            server.stop()

    defaults = json.loads((SOURCE / "defaults.json").read_text(encoding="utf-8"))
    environment_cases = defaults["transport_environment"] + [
        {"name": name, "proxy": None} for name in defaults["transport_direct"]
    ]
    for case in environment_cases:
        name = case["name"]
        server = NativeFixture(proxy="forward" if case["proxy"] in {"forward", "all"} else case["proxy"])
        peer = NativeFixture(proxy="tunnel")
        try:
            with pytest.MonkeyPatch.context() as environment, TemporaryDirectory() as directory:
                for key in tuple(os.environ):
                    if key.casefold() == "no_proxy":
                        environment.delenv(key)
                environment.setenv("NO_PROXY", "*")
                transport = options.TransportOptions(ssl_context=server.verify)
                base_url = server.url
                if variable := case.get("variable"):
                    environment.setenv("NO_PROXY", "")
                    environment.setenv(variable, server.proxy_url)
                    base_url = ("https" if case["proxy"] == "tunnel" else "http") + "://origin.test"
                elif name.startswith("no-proxy") or name == "opt-out":
                    environment.setenv("HTTPS_PROXY", peer.proxy_url)
                    if name.startswith("no-proxy"):
                        environment.delenv("NO_PROXY")
                        environment.setenv("NO_PROXY" if name.endswith("upper") else "no_proxy", "127.0.0.1")
                    else:
                        environment.setenv("NO_PROXY", "")
                        transport = options.TransportOptions(ssl_context=server.verify, trust_env=False)
                else:
                    ca_file = Path(directory) / "ca.pem"
                    ca_file.write_bytes(
                        peer.ca_pem if name in {"ca-opt-out", "ca-context", "injected"} else server.ca_pem
                    )
                    environment.setenv("SSL_CERT_FILE", str(ca_file))
                    if name.startswith("ca-default"):
                        transport = options.UNSET
                    elif name == "ca-opt-out":
                        transport = options.TransportOptions(trust_env=False)
                    elif name == "injected":
                        environment.setenv("HTTPS_PROXY", peer.proxy_url)
                        environment.setenv("NO_PROXY", "")
                        transport = options.UNSET
                settings = options.ClientOptions(
                    base_url=base_url, transport=transport, retry=options.RetryOptions(max_retries=0)
                )
                if asynchronous:

                    async def call() -> None:
                        async with httpx2.AsyncClient(verify=server.verify, trust_env=False) as native:
                            async with package.AsyncClient(
                                options=settings, **({"http_client": native} if name == "injected" else {})
                            ) as api:
                                await arecord(
                                    lines,
                                    f"{mode} environment {name}",
                                    lambda: _acalled(api.retry.with_response.get_safe),
                                )
                            if name == "injected":
                                lines.append(f"  native closed={native.is_closed}")

                    run(call)
                else:
                    with httpx2.Client(verify=server.verify, trust_env=False) as native:
                        with package.Client(
                            options=settings, **({"http_client": native} if name == "injected" else {})
                        ) as api:
                            record(
                                lines, f"{mode} environment {name}", lambda: _called(api.retry.with_response.get_safe)
                            )
                        if name == "injected":
                            lines.append(f"  native closed={native.is_closed}")
                lines.append(
                    f"  origin arrivals={len(server.requests)} connects={server.connects} "
                    f"proxy arrivals={len(peer.requests)} connects={peer.connects}"
                )
        finally:
            server.stop()
            peer.stop()

    sizes = defaults["response_limits"]
    server = NativeFixture()
    try:
        for layer, size in (
            ("default", sizes["large_bytes"]),
            *(
                (layer, size)
                for layer in ("client", "view", "call")
                for size in (sizes["explicit_cap"], sizes["explicit_cap"] + 1)
            ),
            ("error-default", sizes["error_bytes"]),
            ("error-explicit", sizes["error_bytes"]),
        ):
            server.body = b"x" * size
            server.status = 500 if layer.startswith("error") else 200
            cap = options.RequestOptions(max_response_bytes=sizes["explicit_cap"])
            settings = options.ClientOptions(
                base_url=server.url,
                transport=options.TransportOptions(ssl_context=server.verify),
                retry=options.RetryOptions(max_retries=0),
                max_response_bytes=sizes["explicit_cap"] if layer in {"client", "error-explicit"} else options.UNSET,
            )
            for raw in (False, True):
                before = len(server.requests)
                if asynchronous:

                    async def call() -> None:
                        async with package.AsyncClient(options=settings) as api:
                            view = api.with_options(cap) if layer == "view" else api
                            operation = (
                                view.retry.with_raw_response.get_safe if raw else view.retry.with_response.get_safe
                            )
                            try:
                                result = await operation(**({"options": cap} if layer == "call" else {}))
                                lines.append(
                                    f"{mode} size {layer}/{size} raw={raw}: status={result.info.status_code} "
                                    f"bytes={len(result.body_bytes if raw else result.data.root)}"
                                )
                                if raw and layer.startswith("error"):
                                    await result.raise_for_status()
                            except Exception as error:
                                lines.append(
                                    f"{mode} size {layer}/{size} raw={raw}: {type(error).__name__} "
                                    f"limit={getattr(error, 'limit', None)} "
                                    f"bytes={len(getattr(error, 'body_bytes', b''))} "
                                    f"truncated={getattr(error, 'truncated', None)}"
                                )

                    run(call)
                else:
                    with package.Client(options=settings) as api:
                        view = api.with_options(cap) if layer == "view" else api
                        operation = view.retry.with_raw_response.get_safe if raw else view.retry.with_response.get_safe
                        try:
                            result = operation(**({"options": cap} if layer == "call" else {}))
                            lines.append(
                                f"{mode} size {layer}/{size} raw={raw}: status={result.info.status_code} "
                                f"bytes={len(result.body_bytes if raw else result.data.root)}"
                            )
                            if raw and layer.startswith("error"):
                                result.raise_for_status()
                        except Exception as error:
                            lines.append(
                                f"{mode} size {layer}/{size} raw={raw}: {type(error).__name__} "
                                f"limit={getattr(error, 'limit', None)} "
                                f"bytes={len(getattr(error, 'body_bytes', b''))} "
                                f"truncated={getattr(error, 'truncated', None)}"
                            )
                lines.append(f"  arrivals={len(server.requests) - before}")
    finally:
        server.stop()


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
            settings = options.ClientOptions(base_url=url, retry=options.RetryOptions(initial_delay=0, jitter="none"))
            if asynchronous:

                async def call() -> None:
                    async with package.AsyncClient(options=settings) as api:
                        await arecord(
                            lines,
                            f"{mode} refused {host} unsafe",
                            lambda: _acalled(api.retry.with_response.post_unsafe, error_details=error_details),
                        )

                run(call)
            else:
                with package.Client(options=settings) as api:
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
    events: list[str] = []
    client_options = options.ClientOptions(
        base_url=server.url,
        transport=options.TransportOptions(ssl_context=server.verify),
        retry=options.RetryOptions(initial_delay=0, jitter="none"),
        hooks=(_Events(events),),
    )
    try:
        if asynchronous:

            async def calls() -> None:
                async with package.AsyncClient(options=client_options) as api:
                    for status in (301, 302, 303, 307, 308):
                        server.status = status
                        for name, location in _LOCATIONS:
                            server.location = location
                            for enabled in (False, True):
                                request_options = options.RequestOptions(follow_redirects=enabled)
                                for raw in (False, True):
                                    events.clear()
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
                                    lines.append(f"  header events={events.count('response_headers')}")

            run(calls)
        else:
            with package.Client(options=client_options) as api:
                for status in (301, 302, 303, 307, 308):
                    server.status = status
                    for name, location in _LOCATIONS:
                        server.location = location
                        for enabled in (False, True):
                            request_options = options.RequestOptions(follow_redirects=enabled)
                            for raw in (False, True):
                                events.clear()
                                call = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                                record(
                                    lines,
                                    f"{mode} location {status}/{name}/{enabled}/{raw}",
                                    lambda: _called(lambda: call(options=request_options)),
                                )
                                lines.append(f"  header events={events.count('response_headers')}")
        lines.append(f"  {mode} location arrivals={len(server.requests)}")
    finally:
        server.stop()


def _borrowed(package: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
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
                        async with package.AsyncClient(
                            options=options.ClientOptions(base_url=server.url),
                            http_client=native,
                        ) as api:
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
                    with package.Client(
                        options=options.ClientOptions(base_url=server.url),
                        http_client=native,
                    ) as api:
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
        _refusal(package, options, lines, asynchronous=asynchronous)
        _locations(package, options, lines, asynchronous=asynchronous)
        _borrowed(package, lines, asynchronous=asynchronous)
        _location_hooks(package, options, lines, asynchronous=asynchronous)


class _HeaderHook:
    """Fail or cancel only after the malformed redirect's resource headers have been published."""

    def __init__(self, token: object, error: BaseException | None) -> None:
        self.token = token
        self.error = error
        self.events: list[str] = []

    def on_event(self, event: object) -> None:
        name = event.name
        self.events.append(name)
        if name == "response_headers":
            if self.error is not None:
                raise self.error
            self.token.cancel()


def _location_hooks(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:

    mode = "async" if asynchronous else "sync"
    server = NativeFixture()
    server.status, server.location = 302, _LOCATIONS[0][1]
    try:
        for error in (RuntimeError("controlled"), Stop()):
            for raw in (False, True):
                hook = _HeaderHook(None, error)
                settings = options.ClientOptions(
                    base_url=server.url,
                    transport=options.TransportOptions(ssl_context=server.verify),
                    hooks=(hook,),
                )
                label = f"{mode} malformed Location header-hook {type(error).__name__} raw={raw}"
                if asynchronous:

                    async def call() -> None:
                        async with package.AsyncClient(options=settings) as api:
                            operation = (
                                api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                            )
                            try:
                                await operation()
                            except BaseException as failure:
                                lines.append(
                                    f"  {label}: {_details(failure)} notes={getattr(failure, '__notes__', [])} same={failure is error}"
                                )
                            else:
                                lines.append(f"  {label}: unexpectedly returned")

                    run(call)
                else:
                    with package.Client(options=settings) as api:
                        operation = api.retry.with_raw_response.get_safe if raw else api.retry.with_response.get_safe
                        try:
                            operation()
                        except BaseException as failure:
                            lines.append(
                                f"  {label}: {_details(failure)} notes={getattr(failure, '__notes__', [])} same={failure is error}"
                            )
                        else:
                            lines.append(f"  {label}: unexpectedly returned")
                lines.append(f"  header-hook events={hook.events}")
        lines.append(f"  {mode} header-hook arrivals={len(server.requests)}")
    finally:
        server.stop()


class _Events:
    """Collect only event kinds, without retaining contexts or request material."""

    def __init__(self, values: list[str]) -> None:
        self.values = values

    def on_event(self, event: object) -> None:
        self.values.append(event.name)


def native_faults(package: ModuleType, lines: list[str]) -> None:
    """Inject abnormal native send exceptions at the HTTP transport boundary, without trace events."""
    from unittest.mock import patch

    options = importlib.import_module(f"{package.__name__}.options")
    settings = options.ClientOptions(retry=options.RetryOptions(initial_delay=0, jitter="none"))
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
                    else options.RequestOptions(idempotency_key=options.IdempotencyKey(key))
                )
                if asynchronous:

                    async def exercise() -> None:
                        with patch.object(httpx2.AsyncHTTPTransport, "handle_async_request", ahandle):
                            async with package.AsyncClient(options=settings) as api:
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
                        with package.Client(options=settings) as api:
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


def _native_close(package: ModuleType, lines: list[str]) -> None:
    """Preserve a primary failure while the native owned client's close fails after release."""
    from unittest.mock import patch

    options = importlib.import_module(f"{package.__name__}.options")
    server = NativeFixture()
    settings = options.ClientOptions(
        base_url=server.url, transport=options.TransportOptions(ssl_context=server.verify, trust_env=False)
    )
    closed = 0
    original = httpx2.HTTPTransport.close

    def close(transport: httpx2.HTTPTransport) -> None:
        nonlocal closed
        closed += 1
        original(transport)
        raise RuntimeError("native close failed")

    try:
        with patch.object(httpx2.HTTPTransport, "close", close):
            try:
                with package.Client(options=settings) as api:
                    api.retry.get_safe()
                    raise ValueError("primary failure")
            except ValueError as error:
                lines.append(
                    f"  owned sync primary={type(error).__name__} notes={len(getattr(error, '__notes__', ()))}"
                )
            api.close()
            lines.append(f"  owned sync native closes={closed}")
        run(lambda: _native_async_close(package, lines, settings))
    finally:
        server.stop()


async def _native_async_close(package: ModuleType, lines: list[str], settings: object) -> None:
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
        async with package.AsyncClient(options=settings) as api:
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
    options = importlib.import_module(f"{package.__name__}.options")
    server = NativeFixture()
    reached, release = asyncio.Event(), asyncio.Event()

    async def response(value: httpx2.Response) -> None:
        reached.set()
        await release.wait()

    try:
        async with httpx2.AsyncClient(
            verify=server.verify, trust_env=False, event_hooks={"response": [response]}
        ) as native:
            async with package.AsyncClient(
                http_client=native, options=options.ClientOptions(base_url=server.url)
            ) as api:
                view = api.with_options(options.RequestOptions())
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
    options = importlib.import_module(f"{package.__name__}.options")
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
                    async with package.AsyncClient(options=_options(options, server)) as api:
                        await arecord(lines, f"{mode} native decode", lambda: api.pets.list_pets(x_trace=trace))
                        server.status, server.body = 201, b'{"id":2,"name":"dog","tag":"a"}'
                        await arecord(
                            lines,
                            f"{mode} native encode",
                            lambda: api.pets.create_pet(body=body, media_type="application/json"),
                        )

                run(call)
            else:
                with package.Client(options=_options(options, server)) as api:
                    record(lines, f"{mode} native decode", lambda: api.pets.list_pets(x_trace=trace))
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
