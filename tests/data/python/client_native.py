"""Exercise generated native clients over real wire protocols and narrowly injected abnormal failures."""

from __future__ import annotations

import importlib
import json
import os
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import httpx2
import pytest

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup

from tests.data.python.client_generation import SOURCE
from tests.data.python.client_runtime import arecord, record, run
from tests.data.python.fixture_native import NativeFixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from types import ModuleType


def _details(value: object) -> str:
    if isinstance(value, BaseException):
        info = getattr(value, "info", None)
        return (
            f"{type(value).__name__} delivery={getattr(value, 'delivery_state', None)} "
            f"phase={getattr(value, 'phase', None)} stop={getattr(value, 'retry_stop_reason', None)} "
            f"attempts={getattr(value, 'resource_attempt_count', None)} sends={getattr(value, 'network_send_count', None)} "
            f"wire={getattr(value, 'wire_send_count', None)} status={None if info is None else info.status_code} "
            f"body={getattr(value, 'body_available', None)} cause={type(getattr(value, 'cause', None)).__name__}"
        )
    info = getattr(value, "info", None)
    return (
        f"{type(value).__name__} status={None if info is None else info.status_code} "
        f"attempts={None if info is None else info.resource_attempt_count} "
        f"sends={None if info is None else info.network_send_count} wire={None if info is None else info.wire_send_count}"
    )


def _header_details(value: object) -> str:
    headers = getattr(value, "headers", getattr(value, "info").headers)
    return f"{_details(value)} headers={headers.items()}"


def _called(
    call: Callable[[], object],
    *,
    headers: bool = False,
    error_details: Callable[[Exception], str] = _details,
) -> str:
    try:
        return (_header_details if headers else _details)(call())
    except Exception as error:  # noqa: BLE001
        return error_details(error)


async def _acalled(
    call: Callable[[], Awaitable[object]],
    *,
    headers: bool = False,
    error_details: Callable[[Exception], str] = _details,
) -> str:
    try:
        return (_header_details if headers else _details)(await call())
    except Exception as error:  # noqa: BLE001
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
                        environment.setenv("NO_PROXY" if name.endswith("upper") else "no_proxy", "localhost")
                    else:
                        environment.setenv("NO_PROXY", "")
                        transport = options.TransportOptions(ssl_context=server.verify, trust_env=False)
                else:
                    ca_file = Path(directory) / "ca.pem"
                    ca_file.write_bytes(peer.ca_pem if name in {"ca-context", "injected"} else server.ca_pem)
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
        "TransportError delivery=DeliveryState.NOT_SENT phase=connect "
        "retry_outcome=permitted wire=0 status=None body=None cause=ConnectError"
        if (
            type(error) is errors.TransportError
            and getattr(error, "delivery_state", None) is errors.DeliveryState.NOT_SENT
            and getattr(error, "phase", None) == "connect"
            and getattr(error, "wire_send_count", None) == 0
            and getattr(error, "info", None) is None
            and getattr(error, "body_available", None) is None
            and type(getattr(error, "cause", None)) is httpx2.ConnectError
            and (
                getattr(error, "retry_stop_reason", None),
                getattr(error, "resource_attempt_count", None),
                getattr(error, "network_send_count", None),
            )
            in (("transport_not_retryable", 1, 1), ("max_retries_exhausted", 3, 3))
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
                                request_options = options.RequestOptions(
                                    redirects=options.RedirectOptions(enabled=enabled)
                                )
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
                            request_options = options.RequestOptions(redirects=options.RedirectOptions(enabled=enabled))
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
        for ownership in ("borrowed", "owned"):
            if asynchronous:

                async def calls() -> None:
                    async def async_response(value: httpx2.Response) -> None:
                        response(value)

                    async with httpx2.AsyncClient(
                        verify=server.verify, http2=True, trust_env=False, event_hooks={"response": [async_response]}
                    ) as native:
                        async with package.AsyncClient(
                            options=options.ClientOptions(base_url=server.url),
                            http_client=native,
                            http_client_ownership=ownership,
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
                    verify=server.verify, http2=True, trust_env=False, event_hooks={"response": [response]}
                ) as native:
                    with package.Client(
                        options=options.ClientOptions(base_url=server.url),
                        http_client=native,
                        http_client_ownership=ownership,
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
        _configured_origins(package, options, lines, asynchronous=asynchronous)
        _location_hooks(package, options, lines, asynchronous=asynchronous)


def _configured_origins(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    mode = "async" if asynchronous else "sync"
    server = NativeFixture()
    try:
        settings = options.ClientOptions(
            base_url=server.url,
            transport=options.TransportOptions(ssl_context=server.verify),
            redirects=options.RedirectOptions(enabled=True, allowed_origins=("https://☃.example",)),
        )
        if asynchronous:

            async def call() -> None:
                async with package.AsyncClient(options=settings) as api:
                    await arecord(
                        lines,
                        "async configured native-invalid origin",
                        lambda: _acalled(api.retry.with_response.get_safe),
                    )

            run(call)
        else:
            with package.Client(options=settings) as api:
                record(
                    lines,
                    "sync configured native-invalid origin",
                    lambda: _called(api.retry.with_response.get_safe),
                )
        lines.append(f"  {mode} invalid configured origin arrivals={len(server.requests)}")
    finally:
        server.stop()


class _HeaderHook:
    """Fail or cancel only after the malformed redirect's resource headers have been published."""

    def __init__(self, token: object, error: BaseException | None) -> None:
        self.token = token
        self.error = error
        self.events: list[str] = []

    def on_event(self, event: object) -> None:
        name = getattr(event, "name")
        self.events.append(name)
        if name == "response_headers":
            if self.error is not None:
                raise self.error
            getattr(self.token, "cancel")()


def _location_hooks(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    from tests.data.python.client_transports import Stop

    mode = "async" if asynchronous else "sync"
    server = NativeFixture()
    server.status, server.location = 302, _LOCATIONS[0][1]
    try:
        for error in (RuntimeError("controlled"), None, Stop()):
            for raw in (False, True):
                token = options.CancelToken()
                hook = _HeaderHook(token, error)
                settings = options.ClientOptions(
                    base_url=server.url,
                    transport=options.TransportOptions(ssl_context=server.verify),
                    hooks=(hook,),
                    cancel_token=token,
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
                                    f"  {label}: {_details(failure)} secondary={[type(item).__name__ for item in getattr(failure, 'secondary_errors', ())]} same={failure is error}"
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
                                f"  {label}: {_details(failure)} secondary={[type(item).__name__ for item in getattr(failure, 'secondary_errors', ())]} same={failure is error}"
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
        self.values.append(getattr(event, "name"))


class _CustomFailure:
    """A declared explicit adapter that publishes a classified failure without using HTTPX."""

    def __init__(self, transports: ModuleType, failure: Callable[[], BaseException]) -> None:
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=()
        )
        self.failure = failure
        self.calls = 0

    def send(self, request: object, context: object) -> object:
        self.calls += 1
        getattr(context, "trace").phase_started("read")
        raise self.failure()

    def close(self) -> None:
        """No connection is owned by this abnormal adapter fixture."""


class _AsyncCustomFailure(_CustomFailure):
    async def send(self, request: object, context: object) -> object:
        return super().send(request, context)

    async def aclose(self) -> None:
        """No connection is owned by this abnormal adapter fixture."""


def _custom_failures(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    import errno

    errors = importlib.import_module(f"{package.__name__}.errors")
    transports = importlib.import_module(f"{package.__name__}.transports")
    mode = "async" if asynchronous else "sync"
    cases: list[tuple[str, Callable[[], BaseException]]] = [
        ("read", lambda: errors.TransportError(phase="read", delivery_state=errors.DeliveryState.MAYBE_SENT)),
        ("write", lambda: errors.TransportError(phase="write", delivery_state=errors.DeliveryState.MAYBE_SENT)),
        ("pool", lambda: errors.TransportError(phase="pool", delivery_state=errors.DeliveryState.MAYBE_SENT)),
        ("unknown", lambda: errors.TransportError(delivery_state=errors.DeliveryState.MAYBE_SENT)),
        ("claimed-unsent", lambda: errors.TransportError(phase="read", delivery_state=errors.DeliveryState.NOT_SENT)),
        (
            "connect-transient",
            lambda: errors.TransportError(
                phase="connect",
                delivery_state=errors.DeliveryState.NOT_SENT,
                cause=OSError(errno.ECONNREFUSED, "controlled"),
            ),
        ),
        (
            "connect-unknown",
            lambda: errors.TransportError(phase="connect", delivery_state=errors.DeliveryState.NOT_SENT),
        ),
        (
            "connect-permanent",
            lambda: errors.TransportError(
                phase="connect",
                delivery_state=errors.DeliveryState.NOT_SENT,
                cause=socket.gaierror(socket.EAI_NONAME, "controlled"),
            ),
        ),
        ("programming-value", lambda: ValueError("controlled")),
        ("programming-type", lambda: TypeError("controlled")),
        ("unclassified-os", lambda: OSError(errno.ECONNREFUSED, "controlled")),
    ]
    for phase in ("connect", "read", "write", "pool"):
        cases.append((
            f"{phase}-timeout",
            lambda phase=phase: errors.PhaseTimeoutError(
                phase=phase, effective_timeout=0.1, delivery_state=errors.DeliveryState.MAYBE_SENT
            ),
        ))
    for name, failure in cases:
        for unsafe in (False, True):
            adapter = (_AsyncCustomFailure if asynchronous else _CustomFailure)(transports, failure)
            client_options = options.ClientOptions(retry=options.RetryOptions(initial_delay=0, jitter="none"))
            if asynchronous:

                async def call() -> None:
                    async with package.AsyncClient(transport_adapter=adapter, options=client_options) as api:
                        operation = api.retry.with_response.post_unsafe if unsafe else api.retry.with_response.get_safe
                        await arecord(lines, f"{mode} custom {name} unsafe={unsafe}", lambda: _acalled(operation))

                run(call)
            else:
                with package.Client(transport_adapter=adapter, options=client_options) as api:
                    operation = api.retry.with_response.post_unsafe if unsafe else api.retry.with_response.get_safe
                    record(lines, f"{mode} custom {name} unsafe={unsafe}", lambda: _called(operation))
            lines.append(f"  custom calls={adapter.calls}")
    for deadline in (False, True):
        adapter = (_AsyncCustomFailure if asynchronous else _CustomFailure)(
            transports,
            lambda: errors.PhaseTimeoutError(
                phase="pool", effective_timeout=1, delivery_state=errors.DeliveryState.NOT_SENT
            ),
        )
        client_options = options.ClientOptions(
            total_timeout=1 if deadline else None,
            retry=options.RetryOptions(initial_delay=0, jitter="none", retry_on_pool_timeout=True),
        )
        if asynchronous:

            async def call() -> None:
                async with package.AsyncClient(transport_adapter=adapter, options=client_options) as api:
                    await arecord(
                        lines,
                        f"{mode} custom pool enabled deadline={deadline}",
                        lambda: _acalled(api.retry.with_response.get_safe),
                    )

            run(call)
        else:
            with package.Client(transport_adapter=adapter, options=client_options) as api:
                record(
                    lines,
                    f"{mode} custom pool enabled deadline={deadline}",
                    lambda: _called(api.retry.with_response.get_safe),
                )
        lines.append(f"  custom calls={adapter.calls}")


class _NativeFailure:
    """Drive only abnormal public trace callbacks, then raise an injected native exception."""

    def __init__(
        self,
        events: Callable[[httpx2.Request], Iterator[tuple[object, object]]],
        *,
        ignore_trace_failure: bool = False,
    ) -> None:
        self.events = events
        self.ignore_trace_failure = ignore_trace_failure
        self.calls = 0

    def send(self, request: httpx2.Request) -> object:
        self.calls += 1
        callback = request.extensions["trace"]
        if not callable(callback):
            raise TypeError("missing public native trace")
        for name, info in self.events(request):
            try:
                callback(name, info)
            except Exception:
                if not self.ignore_trace_failure:
                    raise
        raise RuntimeError("fault failed to raise")

    async def asend(self, request: httpx2.Request) -> object:
        import inspect

        self.calls += 1
        callback = request.extensions["trace"]
        if not callable(callback):
            raise TypeError("missing public native trace")
        for name, info in self.events(request):
            result = callback(name, info)
            if not inspect.isawaitable(result):
                raise TypeError("native async trace was not awaitable")
            try:
                await result
            except Exception:
                if not self.ignore_trace_failure:
                    raise
        raise RuntimeError("fault failed to raise")


def _head_events(
    request: httpx2.Request, *, status: object = 302, headers: object = None
) -> Iterator[tuple[object, object]]:
    import httpcore2

    core = httpcore2.Request(request.method, str(request.url))
    yield "http11.send_request_headers.started", {"request": core}
    yield "http11.receive_response_headers.started", {"request": core}
    yield (
        "http11.receive_response_headers.complete",
        {"return_value": (b"HTTP/1.1", status, b"Fixture", [(b"location", b"/next")] if headers is None else headers)},
    )


def _native_failure(
    kind: type[BaseException], *, headers: bool = False, core_origin: bool = False
) -> Callable[[httpx2.Request], Iterator[tuple[object, object]]]:
    import httpcore2

    def events(request: httpx2.Request) -> Iterator[tuple[object, object]]:
        yield "connection.connect_tcp.started", {}
        if headers:
            yield from _head_events(request)
        error = kind("controlled")
        if core_origin:
            error.__cause__ = httpcore2.RemoteProtocolError("controlled")
        raise error

    return events


def _observed(
    factory: Callable[[], BaseException], *, final: str = "same", started: bool = False
) -> Callable[[httpx2.Request], Iterator[tuple[object, object]]]:
    import errno
    import ssl

    import httpcore2

    def events(request: httpx2.Request) -> Iterator[tuple[object, object]]:
        yield "connection.connect_tcp.started", {}
        core = httpcore2.ConnectError("controlled")
        core.__cause__ = factory()
        yield "connection.connect_tcp.failed", {"exception": core}
        core.__cause__ = None
        core.__suppress_context__ = True
        if started:
            yield from _head_events(request)
        delivered: BaseException = core
        if final == "unknown":
            delivered = BaseExceptionGroup("controlled", (core, RuntimeError("controlled")))
        elif final == "permanent":
            delivered = BaseExceptionGroup("controlled", (core, OSError(errno.EINVAL, "controlled")))
        elif final == "transient":
            delivered = BaseExceptionGroup("controlled", (core, OSError(errno.ECONNRESET, "controlled")))
        elif final == "mismatch":
            delivered = httpcore2.ConnectError("different")
        elif final == "suppressed":
            delivered = httpcore2.ConnectError("different")
            delivered.__context__ = core
            delivered.__suppress_context__ = True
        elif final == "ssl":
            delivered = ssl.SSLError("controlled")
            delivered.__cause__ = core
        elif final == "rewritten":
            core.__cause__ = OSError(errno.ECONNREFUSED, "controlled")
        elif final == "cycle":
            core.__cause__ = core
        elif final == "depth-limit":
            delivered = _chain(15, core)
        elif final == "depth-excess":
            delivered = _chain(16, core)
        elif final == "node-limit":
            delivered = _shared_group(core, 62)
        elif final == "node-excess":
            delivered = _shared_group(core, 63)
        error = httpx2.ConnectError("controlled")
        error.__cause__ = delivered
        raise error

    return events


def _chain(length: int, leaf: BaseException) -> BaseException:
    for _ in range(length):
        outer = RuntimeError("controlled")
        outer.__cause__ = leaf
        leaf = outer
    return leaf


def _native_fault_cases() -> list[tuple[str, Callable[[httpx2.Request], Iterator[tuple[object, object]]]]]:
    import errno
    import ssl

    cases: list[tuple[str, Callable[[httpx2.Request], Iterator[tuple[object, object]]]]] = []
    for kind in (
        httpx2.ConnectTimeout,
        httpx2.PoolTimeout,
        httpx2.ReadTimeout,
        httpx2.WriteTimeout,
        httpx2.ReadError,
        httpx2.WriteError,
        httpx2.ConnectError,
        httpx2.ProxyError,
        httpx2.UnsupportedProtocol,
        httpx2.LocalProtocolError,
        httpx2.DecodingError,
        httpx2.CloseError,
        httpx2.SSEError,
        httpx2.InvalidURL,
        httpx2.TransportError,
    ):
        cases.append((kind.__name__, _native_failure(kind)))
    cases.extend((
        ("remote-client", _native_failure(httpx2.RemoteProtocolError)),
        ("remote-client-head", _native_failure(httpx2.RemoteProtocolError, headers=True)),
        ("invalid-head", _native_failure(httpx2.InvalidURL, headers=True)),
        ("remote-core", _native_failure(httpx2.RemoteProtocolError, core_origin=True)),
        ("remote-core-head", _native_failure(httpx2.RemoteProtocolError, headers=True, core_origin=True)),
    ))
    transient = lambda: OSError(errno.ECONNREFUSED, "controlled")
    graphs: tuple[tuple[str, Callable[[], BaseException]], ...] = (
        ("errno", transient),
        ("dns-again", lambda: socket.gaierror(socket.EAI_AGAIN, "controlled")),
        ("dns-permanent", lambda: socket.gaierror(socket.EAI_NONAME, "controlled")),
        ("enoent", lambda: FileNotFoundError(errno.ENOENT, "controlled")),
        ("unknown", lambda: OSError("controlled")),
        ("group", lambda: BaseExceptionGroup("controlled", (transient(), OSError(errno.EHOSTUNREACH, "controlled")))),
        ("group-before-cause", lambda: _with_cause(_shared_group(transient()), ssl.SSLError("controlled"))),
        ("mixed", lambda: BaseExceptionGroup("controlled", (transient(), ssl.SSLError("controlled")))),
        ("ssl-wrapper", lambda: _with_cause(ssl.SSLError("controlled"), transient())),
        ("shared", lambda: _shared_group(transient())),
        ("context", lambda: _with_context(RuntimeError("controlled"), transient(), suppressed=False)),
        ("suppressed-context", lambda: _with_context(RuntimeError("controlled"), transient(), suppressed=True)),
        (
            "explicit-before-context",
            lambda: _with_context(
                _with_cause(RuntimeError("controlled"), transient()), ssl.SSLError("controlled"), suppressed=False
            ),
        ),
        ("depth-limit", lambda: _chain(15, transient())),
        ("depth-excess", lambda: _chain(16, transient())),
        ("node-limit", lambda: _shared_group(transient(), 62)),
        ("node-excess", lambda: _shared_group(transient(), 63)),
        ("cycle", lambda: _cycle(transient())),
    )
    for name, factory in graphs:
        cases.append((f"connect-observed-{name}", _observed(factory)))
    for final in (
        "unknown",
        "permanent",
        "transient",
        "mismatch",
        "suppressed",
        "ssl",
        "cycle",
        "depth-limit",
        "depth-excess",
        "node-limit",
        "node-excess",
    ):
        cases.append((f"connect-final-{final}", _observed(transient, final=final)))
    cases.append(("connect-observed-ssl-rewritten", _observed(lambda: ssl.SSLError("controlled"), final="rewritten")))
    cases.append(("connect-after-headers", _observed(transient, started=True)))
    return cases


def _with_cause(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


def _with_context(outer: BaseException, cause: BaseException, *, suppressed: bool) -> BaseException:
    outer.__context__ = cause
    outer.__suppress_context__ = suppressed
    return outer


def _shared_group(leaf: BaseException, count: int = 2) -> BaseException:
    return BaseExceptionGroup("controlled", (leaf,) * count)


def _cycle(leaf: BaseException) -> BaseException:
    leaf.__cause__ = leaf
    return leaf


def _native_faults(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    from unittest.mock import patch

    mode = "async" if asynchronous else "sync"
    settings = options.ClientOptions(retry=options.RetryOptions(initial_delay=0, jitter="none"))
    for name, events in (*_native_fault_cases(), *((f"trace-{name}", _trace_fault(name)) for name in _trace_cases())):
        driver = _NativeFailure(events, ignore_trace_failure=name == "trace-broken-before-error")
        if asynchronous:

            async def handle(transport: httpx2.AsyncHTTPTransport, request: httpx2.Request) -> object:
                return await driver.asend(request)

            async def call() -> None:
                with patch.object(httpx2.AsyncHTTPTransport, "handle_async_request", handle):
                    async with package.AsyncClient(options=settings) as api:
                        await arecord(
                            lines, f"{mode} native fault {name}", lambda: _acalled(api.retry.with_response.get_safe)
                        )

            run(call)
        else:
            with patch.object(httpx2.HTTPTransport, "handle_request", lambda transport, request: driver.send(request)):
                with package.Client(options=settings) as api:
                    record(lines, f"{mode} native fault {name}", lambda: _called(api.retry.with_response.get_safe))
        lines.append(f"  injected adapter invocations={driver.calls}")


def native_faults(package: ModuleType, lines: list[str]) -> None:
    """Classify explicit-adapter I/O and abnormal native failure graphs through generated public operations."""
    options = importlib.import_module(f"{package.__name__}.options")
    for asynchronous in (False, True):
        _custom_failures(package, options, lines, asynchronous=asynchronous)
        _native_faults(package, options, lines, asynchronous=asynchronous)
        _missing_trace(package, options, lines, asynchronous=asynchronous)
        _phase_faults(package, lines, asynchronous=asynchronous)
        _contract_close(package, lines, asynchronous=asynchronous)


def _trace_fault(name: str) -> Callable[[httpx2.Request], Iterator[tuple[object, object]]]:
    import httpcore2

    def events(request: httpx2.Request) -> Iterator[tuple[object, object]]:
        core = httpcore2.Request(request.method, str(request.url))
        if name == "name":
            yield 3, {}
        elif name == "info":
            yield "connection.connect_tcp.started", None
        elif name == "failed-value":
            yield "connection.connect_tcp.failed", {"exception": "not an exception"}
        elif name == "broken-before-error":
            yield "http11.receive_response_headers.complete", {"return_value": None}
        elif name == "missing-request":
            yield "http11.send_request_headers.started", {}
        elif name == "request-method":
            setattr(core, "method", "GET")
            yield "http11.send_request_headers.started", {"request": core}
        elif name == "request-url":
            setattr(core, "url", "https://example.com")
            yield "http11.send_request_headers.started", {"request": core}
        elif name == "request-target":
            setattr(core.url, "target", None)
            yield "http11.send_request_headers.started", {"request": core}
        elif name == "different-request":
            yield (
                "http11.send_request_headers.started",
                {"request": httpcore2.Request("POST", "https://elsewhere.test/other")},
            )
        elif name == "complete-before-start":
            yield "http11.receive_response_headers.complete", {"return_value": (b"HTTP/1.1", 302, b"Fixture", [])}
        elif name.startswith("head-"):
            yield "http11.send_request_headers.started", {"request": core}
            protocol = "http2" if name == "head-h2-length" else "http11"
            yield f"{protocol}.receive_response_headers.started", {"request": core}
            values: dict[str, object] = {
                "head-type": [],
                "head-length": (b"HTTP/1.1", 302),
                "head-version": ("HTTP/1.1", 302, b"Fixture", []),
                "head-reason": (b"HTTP/1.1", 302, "Fixture", []),
                "head-h2-length": (302,),
                "head-empty-version": (b"", 302, b"Fixture", []),
                "head-headers-type": (b"HTTP/1.1", 302, b"Fixture", {}),
                "head-pair-type": (b"HTTP/1.1", 302, b"Fixture", [b"location"]),
                "head-pair-length": (b"HTTP/1.1", 302, b"Fixture", [(b"location",)]),
                "head-name-type": (b"HTTP/1.1", 302, b"Fixture", [("location", b"/next")]),
                "head-value-type": (b"HTTP/1.1", 302, b"Fixture", [(b"location", "/next")]),
                "head-status-type": (b"HTTP/1.1", "302", b"Fixture", []),
                "head-status-range": (b"HTTP/1.1", 700, b"Fixture", []),
            }
            yield f"{protocol}.receive_response_headers.complete", {"return_value": values[name]}
        elif name == "harmless-events":
            yield "future.harmless.started", {}
            yield "http2.receive_remote_settings.complete", {"return_value": None}
            yield "connection.start_tls.complete", {}
            yield "connection.connect_tcp.failed", {"exception": RuntimeError("controlled")}
        elif name == "repeated-headers":
            yield from _head_events(request)
            yield from _head_events(request)
        elif name == "nonredirect-head":
            yield from _head_events(request, status=200)
            raise httpx2.InvalidURL("controlled")
        elif name == "missing-location":
            yield from _head_events(request, headers=[])
            raise httpx2.InvalidURL("controlled")
        elif name == "different-core-provenance":
            yield from _head_events(request)
            raise _with_cause(httpx2.InvalidURL("controlled"), httpcore2.ProxyError("controlled"))
        elif name == "processing-cycle":
            yield from _head_events(request)
            raise _cycle(httpx2.RemoteProtocolError("controlled"))
        elif name == "processing-excess":
            yield from _head_events(request)
            raise _with_cause(httpx2.RemoteProtocolError("controlled"), _chain(17, ValueError("controlled")))
        raise httpx2.ConnectError("controlled")

    return events


def _trace_cases() -> tuple[str, ...]:
    return (
        "name",
        "info",
        "failed-value",
        "broken-before-error",
        "missing-request",
        "request-method",
        "request-url",
        "request-target",
        "different-request",
        "complete-before-start",
        "head-type",
        "head-length",
        "head-version",
        "head-reason",
        "head-h2-length",
        "head-empty-version",
        "head-headers-type",
        "head-pair-type",
        "head-pair-length",
        "head-name-type",
        "head-value-type",
        "head-status-type",
        "head-status-range",
        "harmless-events",
        "repeated-headers",
        "nonredirect-head",
        "missing-location",
        "different-core-provenance",
        "processing-cycle",
        "processing-excess",
    )


def _missing_trace(package: ModuleType, options: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    from unittest.mock import patch

    server = NativeFixture()
    try:
        if asynchronous:
            original_async = httpx2.AsyncHTTPTransport.handle_async_request

            async def handle(transport: httpx2.AsyncHTTPTransport, request: httpx2.Request) -> httpx2.Response:
                request.extensions.pop("trace")
                return await original_async(transport, request)

            async def call() -> None:
                with patch.object(httpx2.AsyncHTTPTransport, "handle_async_request", handle):
                    async with package.AsyncClient(options=_options(options, server)) as api:
                        await arecord(
                            lines,
                            "async native missing required trace",
                            lambda: _acalled(api.retry.with_response.get_safe),
                        )

            run(call)
        else:
            original = httpx2.HTTPTransport.handle_request

            def handle(transport: httpx2.HTTPTransport, request: httpx2.Request) -> httpx2.Response:
                request.extensions.pop("trace")
                return original(transport, request)

            with patch.object(httpx2.HTTPTransport, "handle_request", handle):
                with package.Client(options=_options(options, server)) as api:
                    record(
                        lines, "sync native missing required trace", lambda: _called(api.retry.with_response.get_safe)
                    )
        lines.append(f"  missing trace actual arrivals={len(server.requests)}")
    finally:
        server.stop()


def _phase_faults(package: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    from tests.data.python.client_transports import Adapter, AsyncAdapter, AsyncResponse, Response

    transports = importlib.import_module(f"{package.__name__}.transports")
    responses = importlib.import_module(f"{package.__name__}.responses")
    mode = "async" if asynchronous else "sync"
    adapter = (AsyncAdapter if asynchronous else Adapter)(transports, [], evidence=True)
    response = AsyncResponse if asynchronous else Response
    for value in ([], {}, None, "invalid"):

        def invalid(request: object, context: object, value: object = value) -> object:
            getattr(context, "trace").phase_started(value)
            return response([], 200, responses.HeadersView(), ())

        adapter.replies.append(invalid)
        if asynchronous:

            async def call() -> None:
                async with package.AsyncClient(transport_adapter=adapter) as api:
                    await arecord(
                        lines,
                        f"{mode} custom malformed phase {type(value).__name__}",
                        lambda: _acalled(api.retry.with_response.get_safe),
                    )

            run(call)
        else:
            with package.Client(transport_adapter=adapter) as api:
                record(
                    lines,
                    f"{mode} custom malformed phase {type(value).__name__}",
                    lambda: _called(api.retry.with_response.get_safe),
                )


class _CloseFailure(httpx2.SyncByteStream, httpx2.AsyncByteStream):
    """Raise only while releasing a response that failed the required native evidence contract."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.closes = 0

    def __iter__(self) -> Iterator[bytes]:
        yield b"not delivered"

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self:
            yield chunk

    def close(self) -> None:
        self.closes += 1
        raise self.error

    async def aclose(self) -> None:
        self.close()


def _contract_close(package: ModuleType, lines: list[str], *, asynchronous: bool) -> None:
    from unittest.mock import patch

    from tests.data.python.client_transports import Stop

    mode = "async" if asynchronous else "sync"
    for error in (OSError("controlled"), Stop()):
        body = _CloseFailure(error)
        if asynchronous:

            async def handle(transport: httpx2.AsyncHTTPTransport, request: httpx2.Request) -> httpx2.Response:
                return httpx2.Response(200, stream=body)

            async def call() -> None:
                with patch.object(httpx2.AsyncHTTPTransport, "handle_async_request", handle):
                    async with package.AsyncClient() as api:
                        try:
                            await api.retry.with_response.get_safe()
                        except BaseException as failure:
                            lines.append(
                                f"  {mode} contract close {type(error).__name__}: {type(failure).__name__} secondary={[type(item).__name__ for item in getattr(failure, 'secondary_errors', ())]} same={failure is error}"
                            )
                        else:
                            lines.append(f"  {mode} contract close unexpectedly returned")

            run(call)
        else:
            with patch.object(
                httpx2.HTTPTransport, "handle_request", lambda transport, request: httpx2.Response(200, stream=body)
            ):
                with package.Client() as api:
                    try:
                        api.retry.with_response.get_safe()
                    except BaseException as failure:
                        lines.append(
                            f"  {mode} contract close {type(error).__name__}: {type(failure).__name__} secondary={[type(item).__name__ for item in getattr(failure, 'secondary_errors', ())]} same={failure is error}"
                        )
                    else:
                        lines.append(f"  {mode} contract close unexpectedly returned")
        lines.append(f"  {mode} contract stream closes={body.closes}")
