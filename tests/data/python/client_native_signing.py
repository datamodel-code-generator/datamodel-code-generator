"""Compare public signing inputs with actual TLS request framing and native-client isolation."""

from __future__ import annotations

import importlib
import io
from typing import TYPE_CHECKING

import httpx2

from tests.data.python.client_runtime import arecord, record, run
from tests.data.python.fixture_native import NativeFixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import ModuleType


class _Signatures:
    def __init__(self, auth: ModuleType, origin: str, events: list[str], index: int = 1) -> None:
        self.auth = auth
        self.origin = origin
        self.events = events
        self.index = index
        self.inputs: list[tuple[object, ...]] = []
        self._capabilities = auth.SignerCapabilities(
            allowed_origins=(origin,),
            managed_headers=(f"X-Signature-{index}",),
            managed_query=(f"sig{index}",),
        )

    @property
    def capabilities(self) -> object:
        return self._capabilities

    def fields(self, request: object) -> object:
        authority = self.origin.partition("://")[2]
        self.inputs.append((
            request.method,
            request.url.replace(self.origin, "<origin>"),
            request.origin.replace(self.origin, "<origin>"),
            request.query,
            tuple((name.lower(), value.replace(authority, "<authority>")) for name, value in request.headers),
            request.attempt_index,
            request.hop_index,
            tuple(self.events),
        ))
        return self.auth.SignatureFields(
            headers=((f"X-Signature-{self.index}", f"signature-{self.index}-café"),),
            query=((f"sig{self.index}", "a +/é"),),
        )


class _Signer(_Signatures):
    def sign(self, request: object) -> object:
        return self.fields(request)


class _AsyncSigner(_Signatures):
    async def sign(self, request: object) -> object:
        return self.fields(request)


class _Attempt:
    def __init__(self, events: list[str], *, known: bool) -> None:
        self.events = events
        self.known = known

    @property
    def content_length(self) -> int | None:
        self.events.append("length")
        return 19 if self.known else None

    @property
    def content_type(self) -> str | None:
        self.events.append("type")
        return "application/octet-stream"

    def iter_bytes(self) -> Iterator[bytes]:
        self.events.append("read")
        yield b"chunk-one"
        yield b"\x00chunk-two"

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self.iter_bytes():
            yield chunk

    def close(self) -> None:
        self.events.append("close")

    async def aclose(self) -> None:
        self.close()




def _operation(
    api: object,
    bodies: ModuleType,
    label: str,
    origin: str,
    events: list[str],
    *,
    asynchronous: bool,
) -> Callable[[], object]:
    resource = api.auth
    if label == "typed-get":
        return resource.anonymous
    if label == "typed-absent":
        return resource.signed_body
    if label == "typed-empty":
        return lambda: resource.signed_body(body=b"")
    if label == "typed-known":
        return lambda: resource.signed_body(body=b"signed\x00bytes")
    body: object = b"encoded\x00bytes"
    if label != "raw-encoded":
        attempt = _Attempt(events, known=label == "raw-known")
        body = io.BytesIO(b"chunk-one\x00chunk-two") if label == "raw-known" else (attempt.aiter_bytes() if asynchronous else attempt.iter_bytes())
    return lambda: api.request_raw(
        "PUT", origin + "/raw/%7e/%2F?dup=one&dup=two&blank=&plus=+&space=%20&slash=%2f", body=body
    )


def _observed(
    lines: list[str],
    server: NativeFixture,
    signer: _Signatures,
    events: list[str],
) -> None:
    authority = signer.origin.partition("://")[2].encode()
    lines.append(f"    input={signer.inputs}")
    lines.append(
        f"    wire={server.requests[-1:]} headers={tuple((name.lower(), value.replace(authority, b'<authority>')) for name, value in server.request_headers[-1]) if server.request_headers else ()} events={events}"
    )
    signer.inputs.clear()
    server.requests.clear()
    server.request_headers.clear()
    events.clear()


def _framing(package: ModuleType, auth: ModuleType, options: ModuleType, bodies: ModuleType, lines: list[str]) -> None:
    labels = ("typed-known", "typed-empty", "typed-absent", "typed-get", "raw-encoded", "raw-known", "raw-unknown")
    for asynchronous in (False, True):
        mode = "async" if asynchronous else "sync"
        for protocol in ("h1", "h2", "fallback"):
            server = NativeFixture(http2=protocol == "h2")
            server.content_type = b"application/octet-stream"
            events: list[str] = []
            signer = (_AsyncSigner if asynchronous else _Signer)(auth, server.url, events)
            settings = options.ClientOptions(
                base_url=server.url,
                transport=options.TransportOptions(ssl_context=server.verify, http2=protocol != "h1"),
                headers=(("X-Duplicate", "first"), ("x-duplicate", "second")),
                auth=auth.AuthConfig({}, signers=(signer,), allowed_origins=(server.url,), send_on_anonymous=True),
            )
            try:
                if asynchronous:

                    async def calls() -> None:
                        async with package.AsyncClient(options=settings) as api:
                            for label in labels:
                                operation = _operation(api, bodies, label, server.url, events, asynchronous=True)

                                async def call() -> object:
                                    result = await operation()
                                    return getattr(result, "body_bytes", result)

                                await arecord(lines, f"{mode} {protocol} {label}", call)
                                _observed(lines, server, signer, events)

                    run(calls)
                else:
                    with package.Client(options=settings) as api:
                        for label in labels:
                            operation = _operation(api, bodies, label, server.url, events, asynchronous=False)

                            def call() -> object:
                                result = operation()
                                return getattr(result, "body_bytes", result)

                            record(lines, f"{mode} {protocol} {label}", call)
                            _observed(lines, server, signer, events)
                lines.append(f"  {mode} {protocol} alpn={server.protocols}")
            finally:
                server.stop()


def _isolation(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    for asynchronous in (False, True):
        mode = "async" if asynchronous else "sync"
        for ownership in ("borrowed",):
            server = NativeFixture(http2=True)
            authority = server.url.partition("://")[2]
            server.content_type = b"application/octet-stream"
            events: list[str] = []
            signers = tuple(
                (_AsyncSigner if asynchronous else _Signer)(auth, server.url, events, index) for index in (1, 2)
            )
            seen: list[tuple[object, ...]] = []

            def native_request(request: httpx2.Request, authority: str = authority) -> None:
                seen.append((
                    str(request.url).replace(server.url, "<origin>"),
                    tuple(
                        (name.lower(), value.replace(authority, "<authority>"))
                        for name, value in request.headers.multi_items()
                    ),
                ))

            settings = options.ClientOptions(
                base_url=server.url,
                auth=auth.AuthConfig(
                    {
                        "header_key": (
                            auth.AsyncStaticCredentialProvider if asynchronous else auth.StaticCredentialProvider
                        )(auth.ApiKeyCredential("café-鍵"))
                    },
                    signers=signers,
                    allowed_origins=(server.url,),
                    send_on_anonymous=True,
                    anonymous_schemes=("header_key",),
                ),
            )
            native_options = {
                "verify": server.verify,
                "http2": True,
                "trust_env": False,
                "auth": ("injected-user", "injected-password"),
                "headers": {"X-Native-Default": "must-not-send", "Host": "wrong.example"},
                "params": {"native_query": "must-not-send"},
                "cookies": {"native_cookie": "must-not-send"},
                "base_url": "https://wrong.example/base",
            }
            try:
                if asynchronous:

                    async def calls() -> None:
                        async def async_request(request: httpx2.Request) -> None:
                            native_request(request)

                        async with httpx2.AsyncClient(
                            **native_options, event_hooks={"request": [async_request]}
                        ) as native:
                            async with package.AsyncClient(options=settings, http_client=native) as api:
                                await arecord(lines, f"{mode} {ownership} typed", api.auth.anonymous)

                                async def raw() -> bytes:
                                    result = await api.request_raw("PUT", server.url + "/raw?original=%2f", body=b"raw")
                                    return result.body_bytes

                                await arecord(lines, f"{mode} {ownership} raw", raw)
                            lines.append(f"    native closed={native.is_closed}")

                    run(calls)
                else:
                    with httpx2.Client(**native_options, event_hooks={"request": [native_request]}) as native:
                        with package.Client(options=settings, http_client=native) as api:
                            record(lines, f"{mode} {ownership} typed", api.auth.anonymous)
                            record(
                                lines,
                                f"{mode} {ownership} raw",
                                lambda: (
                                    api.request_raw("PUT", server.url + "/raw?original=%2f", body=b"raw").body_bytes
                                ),
                            )
                        lines.append(f"    native closed={native.is_closed}")
                lines.append(f"    signer1={signers[0].inputs} signer2={signers[1].inputs}")
                lines.append(f"    native requests={seen}")
                lines.append(
                    f"    wire={server.requests} headers={tuple(tuple((name, value.replace(authority.encode(), b'<authority>')) for name, value in fields) for fields in server.request_headers)} alpn={server.protocols}"
                )
            finally:
                server.stop()


def _proxies(package: ModuleType, auth: ModuleType, options: ModuleType, bodies: ModuleType, lines: list[str]) -> None:
    for asynchronous in (False, True):
        mode = "async" if asynchronous else "sync"
        for proxy in ("tunnel", "forward"):
            for host in ("origin.test", "[::1]"):
                server = NativeFixture(proxy=proxy)
                events: list[str] = []
                origin = ("https" if proxy == "tunnel" else "http") + "://" + host
                signer = (_AsyncSigner if asynchronous else _Signer)(auth, origin, events)
                settings = options.ClientOptions(
                    base_url=origin,
                    transport=options.TransportOptions(ssl_context=server.verify, proxy=server.proxy_url),
                    auth=auth.AuthConfig({}, signers=(signer,), allowed_origins=(origin,), send_on_anonymous=True),
                )
                try:
                    if asynchronous:

                        async def call() -> bytes:
                            async with package.AsyncClient(options=settings) as api:
                                operation = _operation(api, bodies, "raw-unknown", origin, events, asynchronous=True)
                                result = await operation()
                                return result.body_bytes

                        run(lambda: arecord(lines, f"{mode} {proxy} {host}", call))
                    else:
                        with package.Client(options=settings) as api:
                            operation = _operation(api, bodies, "raw-unknown", origin, events, asynchronous=False)
                            record(lines, f"{mode} {proxy} {host}", lambda: operation().body_bytes)
                    _observed(lines, server, signer, events)
                    lines.append(f"    connects={server.connects}")
                finally:
                    server.stop()


def native_signing(package: ModuleType, lines: list[str]) -> None:
    """Exercise signer-visible framing, exact raw queries and native default isolation over real TLS."""
    auth = importlib.import_module(f"{package.__name__}.auth")
    options = importlib.import_module(f"{package.__name__}.options")
    bodies = importlib.import_module(f"{package.__name__}.bodies")
    _framing(package, auth, options, bodies, lines)
    _isolation(package, auth, options, lines)
    _proxies(package, auth, options, bodies, lines)
