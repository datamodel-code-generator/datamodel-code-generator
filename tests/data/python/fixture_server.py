"""A local HTTPS fixture server that generated clients reach over real TLS sockets, whatever host their URLs name.

Its transports hand each request to HTTPX2's own connection pool, whose network backend connects every host to the
server, so the TLS handshake, the HTTP/1.1 framing, and the connection lifecycle are real; the server answers with the
responses a scenario queues. A responder marked as injected answers in-process instead, for the failures a server cannot
produce, such as a refused connection or an interrupted read.
"""

from __future__ import annotations

import ssl
import threading
from functools import cache
from http.client import responses
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Final

import httpcore2
import httpx2
import trustme

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from httpcore2 import AsyncNetworkStream, NetworkStream

_HOSTS: Final = ("example.com", "*.example.com", "localhost")
_BODYLESS: Final = frozenset({204, 304})
_CHUNK_END: Final = b"0\r\n\r\n"


class Injected:
    """A responder that answers in-process, for failures that no real server can produce."""

    __slots__ = ("responder",)

    def __init__(self, responder: Callable[[httpx2.Request], httpx2.Response]) -> None:
        """Wrap the responder, which may raise the failure it injects."""
        self.responder = responder

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Answer or fail as the wrapped responder does."""
        return self.responder(request)


@cache
def _contexts() -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """Return the server context of a private CA's certificate for the fixture hosts, and a client context trusting it."""
    authority = trustme.CA()
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    authority.issue_cert(*_HOSTS).configure_cert(server)
    client = ssl.create_default_context()
    authority.configure_trust(client)
    return server, client


class _Aborted(Exception):
    """A request whose client stopped sending before its body ended."""


def _read(handler: BaseHTTPRequestHandler, size: int) -> bytes:
    if len(data := handler.rfile.read(size)) != size:
        raise _Aborted
    return data


def _line(handler: BaseHTTPRequestHandler) -> bytes:
    if not (line := handler.rfile.readline()).endswith(b"\n"):
        raise _Aborted
    return line


def _body(handler: BaseHTTPRequestHandler) -> bytes:
    """Read a request body framed by its length or by chunks, raising _Aborted when it ends early."""
    if handler.headers.get("transfer-encoding", "").lower() != "chunked":
        return _read(handler, int(handler.headers.get("content-length", "0")))
    parts: list[bytes] = []
    while size := int(_line(handler).split(b";")[0], 16):
        parts.append(_read(handler, size))
        _line(handler)
    while _line(handler).strip():
        pass
    return b"".join(parts)


class _Handler(BaseHTTPRequestHandler):
    """Answer each request with the server's next queued response, framed as a server frames it."""

    protocol_version = "HTTP/1.1"
    server: FixtureServer

    def _answer(self) -> None:
        try:
            content = _body(self)
        except (_Aborted, ValueError):
            self.close_connection = True
            return
        request = httpx2.Request(
            self.command,
            f"https://{self.headers['host']}{self.path}",
            headers=[(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in self.headers.items()],
            stream=httpx2.ByteStream(content),
        )
        try:
            response = self.server.serve(request)
        except Exception as error:  # noqa: BLE001 - The scenario sees the failure as a lost connection.
            self.server.failures.append(error)
            self.close_connection = True
            return
        self._send(response)

    def _send(self, response: httpx2.Response) -> None:
        self.send_response_only(response.status_code, responses.get(response.status_code, ""))
        headers = response.headers.raw
        names = {name.lower() for name, _ in headers}
        chunks: Iterable[bytes] = response.stream  # ty: ignore[invalid-assignment]
        bodyless = self.command == "HEAD" or response.status_code in _BODYLESS
        framed = bodyless or b"content-length" in names or b"transfer-encoding" in names
        if not framed and isinstance(response.stream, httpx2.ByteStream):
            content = b"".join(chunks)
            chunks, framed = (content,), True
            headers = [*headers, (b"content-length", str(len(content)).encode())]
        for name, value in headers:
            self.send_header(name.decode("latin-1"), value.decode("latin-1"))
        if not framed:
            self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        if bodyless:
            return
        for chunk in chunks:
            self.wfile.write(chunk if framed else f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
        if not framed:
            self.wfile.write(_CHUNK_END)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _answer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Keep the scenario report free of access logs."""


class FixtureServer(ThreadingHTTPServer):
    """A TLS server on a free local port that answers through a callback, in a daemon thread."""

    daemon_threads = True

    def __init__(self, serve: Callable[[httpx2.Request], httpx2.Response]) -> None:
        """Bind a local port, wrap it in TLS, and start serving."""
        super().__init__(("127.0.0.1", 0), _Handler)
        self.socket = _contexts()[0].wrap_socket(self.socket, server_side=True)
        self.serve = serve
        self.failures: list[Exception] = []
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.005}, daemon=True)
        self._thread.start()

    def handle_error(self, request: object, client_address: object) -> None:
        """Ignore a connection the client dropped, as an aborted request."""

    def stop(self) -> None:
        """Stop serving and release the port."""
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)


class _LocalBackend(httpcore2.NetworkBackend):
    """Connect every host to the fixture server, as a hosts file would."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.backend = httpcore2.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> NetworkStream:
        del host, port
        return self.backend.connect_tcp("127.0.0.1", self.port, timeout, local_address, socket_options)

    def sleep(self, seconds: float) -> None:
        self.backend.sleep(seconds)


class _AsyncLocalBackend(httpcore2.AsyncNetworkBackend):
    """Connect every host of an asyncio client to the fixture server."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.backend = httpcore2.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> AsyncNetworkStream:
        del host, port
        return await self.backend.connect_tcp("127.0.0.1", self.port, timeout, local_address, socket_options)

    async def sleep(self, seconds: float) -> None:
        await self.backend.sleep(seconds)


class LocalTransport(httpx2.HTTPTransport):
    """HTTPX2's transport over a pool that reaches the fixture server, except for injected failures."""

    def __init__(self, exchange: Any) -> None:
        """Replace the pool's network backend; injected responders answer through the exchange in-process."""
        client = _contexts()[1]
        super().__init__(verify=client)
        self._pool = httpcore2.ConnectionPool(ssl_context=client, network_backend=_LocalBackend(exchange.port()))
        self.exchange = exchange

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        """Send the request to the fixture server, or answer an injected failure in-process."""
        return self.exchange.handle(request) if self.exchange.injected() else super().handle_request(request)

    def close(self) -> None:
        """Close the pool, then let the exchange stop its server once none of its transports is open."""
        super().close()
        self.exchange.release()


class AsyncLocalTransport(httpx2.AsyncHTTPTransport):
    """HTTPX2's asyncio transport over a pool that reaches the fixture server, except for injected failures."""

    def __init__(self, exchange: Any) -> None:
        """Replace the pool's network backend; injected responders answer through the exchange in-process."""
        client = _contexts()[1]
        super().__init__(verify=client)
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=client, network_backend=_AsyncLocalBackend(exchange.port())
        )
        self.exchange = exchange

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        """Send the request to the fixture server, or answer an injected failure in-process."""
        if self.exchange.injected():
            return await self.exchange.ahandle(request)
        return await super().handle_async_request(request)

    async def aclose(self) -> None:
        """Close the pool, then let the exchange stop its server once none of its transports is open."""
        await super().aclose()
        self.exchange.release()
