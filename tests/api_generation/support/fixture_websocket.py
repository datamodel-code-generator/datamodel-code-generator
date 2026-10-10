"""Local WebSocket peers that generated clients reach over real TLS sockets: a scripted server, a raw one, a proxy.

The scripted server runs the websockets library's threading server with the HTTPS fixture's certificate and answers
each handshake as the next queued play directs: an HTTP response that refuses it, or an accepted connection the play's
script talks on. Each play records the handshake request it got and the close the client sent, which a scenario reports
once the play is done, so reports do not depend on thread timing. The raw peer answers a handshake with raw bytes, or
never, and then answers nothing; the proxy tunnels CONNECT requests or refuses them.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import threading
from contextlib import suppress
from socketserver import BaseRequestHandler, ThreadingTCPServer
from typing import TYPE_CHECKING, Any, Final

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from tests.api_generation.support.fixture_server import _contexts

if TYPE_CHECKING:
    import ssl
    from collections.abc import Callable

    from websockets.http11 import Request, Response
    from websockets.sync.server import ServerConnection

FIRST: Final = "first offered"
_GUID: Final = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_HEADERS: Final = ("authorization", "sec-websocket-protocol", "sec-websocket-extensions", "x-trace")


class Play:
    """One handshake's script: an HTTP refusal, or the subprotocol to select and the conversation on the connection."""

    def __init__(
        self,
        *,
        refuse: tuple[int, tuple[tuple[str, str], ...], bytes] | None = None,
        subprotocol: str | None = FIRST,
        talk: Callable[[ServerConnection], None] | None = None,
    ) -> None:
        """Keep the script; the play records the request and the client's close as the server meets them."""
        self.refuse = refuse
        self.subprotocol = subprotocol
        self.talk = talk
        self.request = ""
        self.closed = "no close"
        self.done = threading.Event()

    def report(self) -> str:
        """Return what the server saw, once the play ended."""
        if not self.done.wait(10):
            return f"    server {self.request} | not ended"
        return f"    server {self.request} | {self.closed}"


def _request(request: Request) -> str:
    headers = ", ".join(f"{name}: {request.headers[name]}" for name in _HEADERS if name in request.headers)
    return f"GET {request.path} [{headers}]"


class SocketServer:
    """A TLS WebSocket server on a free local port that answers each handshake with the next queued play."""

    def __init__(self) -> None:
        """Start serving in a daemon thread."""
        self.plays: list[Play] = []
        self._connections: list[ServerConnection] = []
        self._server = serve(
            self._handle,
            "127.0.0.1",
            0,
            ssl=_contexts()[0],
            select_subprotocol=self._select,
            process_request=self._process,
            ping_interval=None,
            close_timeout=None,
            max_size=None,
            server_header=None,
        )
        self.port = self._server.socket.getsockname()[1]
        self.url = f"https://localhost:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def play(self, *plays: Play) -> tuple[Play, ...]:
        """Queue the plays of the next handshakes and return them."""
        self.plays.extend(plays)
        return plays

    def _process(self, connection: ServerConnection, request: Request) -> Response | None:
        play = self.plays.pop(0)
        play.request = _request(request)
        connection.play = play  # ty: ignore[unresolved-attribute]
        self._connections.append(connection)
        if play.refuse is None:
            return None
        status, headers, body = play.refuse
        response = connection.respond(status, body.decode())
        del response.headers["Content-Type"]
        for name, value in headers:
            response.headers[name] = value
        play.closed = "refused"
        play.done.set()
        return response

    @staticmethod
    def _select(connection: ServerConnection, offered: Any) -> str | None:
        play: Play = connection.play  # ty: ignore[unresolved-attribute]
        return (offered[0] if offered else None) if play.subprotocol == FIRST else play.subprotocol

    @staticmethod
    def _handle(connection: ServerConnection) -> None:
        play: Play = connection.play  # ty: ignore[unresolved-attribute]
        try:
            if play.talk is not None:
                play.talk(connection)
            while True:
                connection.recv(timeout=10)
        except ConnectionClosed as closed:
            received = closed.rcvd
            play.closed = "no close" if received is None else f"closed {received.code} {received.reason!r}"
        except TimeoutError:
            play.closed = "still open"
        finally:
            play.done.set()

    def stop(self) -> None:
        """Hang up the connections clients left open, stop serving, and release the port."""
        for connection in self._connections:
            with suppress(OSError):
                socket.socket.shutdown(connection.socket, socket.SHUT_RDWR)
        self._server.shutdown()
        self._thread.join(timeout=5)


class _RawHandler(BaseRequestHandler):
    server: RawPeer

    def handle(self) -> None:
        stream = self.server.context.wrap_socket(self.request, server_side=True, do_handshake_on_connect=False)
        try:
            stream.do_handshake()
            head = b""
            while b"\r\n\r\n" not in head and (received := stream.recv(65536)):
                head += received
            if (reply := self.server.reply) == b"":
                return
            if reply is not None:
                key = next(
                    line.split(b":", 1)[1].strip()
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"sec-websocket-key:")
                )
                accept = base64.b64encode(hashlib.sha1(key + _GUID).digest())  # noqa: S324
                stream.sendall(reply.replace(b"{accept}", accept))
                socket.socket.sendall(stream, self.server.garbled)
            threading.Thread(target=self._released, args=(stream,), daemon=True).start()
            while stream.recv(65536):
                self.server.arrived()
                if self.server.hangup:
                    break
        except OSError:
            pass
        finally:
            stream.close()


    def _released(self, stream: socket.socket) -> None:
        """Hang up once the peer is released, which ends the read loop; the TLS state is left to the reading thread."""
        self.server.release.wait()
        try:
            socket.socket.shutdown(stream, socket.SHUT_RDWR)
        except OSError:
            pass


class RawPeer(ThreadingTCPServer):
    """A TLS peer that reads a handshake request and sends a raw reply, then reads until the client leaves.

    Without a reply it never answers, and with an empty one it closes at once; `{accept}` in a reply becomes the
    request key's accept value, and garbled bytes follow the reply outside TLS. The peer never answers a frame, not even
    a ping; it counts the records the client sends, and hangs up every connection once released.
    """

    daemon_threads = True

    def __init__(self, reply: bytes | None, *, hangup: bool = False, garbled: bytes = b"") -> None:
        """Bind a free local port and serve in a daemon thread; with `hangup`, close once the client sends anything."""
        super().__init__(("127.0.0.1", 0), _RawHandler)
        self.context = _contexts()[0]
        self.reply = reply
        self.hangup = hangup
        self.garbled = garbled
        self.release = threading.Event()
        self.records = 0
        self._arrival = threading.Condition()
        self.url = f"https://localhost:{self.server_address[1]}"
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.005}, daemon=True)
        self.thread.start()

    def arrived(self) -> None:
        """Count one TLS record the client sent after the handshake, such as a ping frame."""
        with self._arrival:
            self.records += 1
            self._arrival.notify_all()

    def wait_records(self, count: int) -> None:
        """Wait until the client sent at least so many records after its handshakes."""
        with self._arrival:
            self._arrival.wait_for(lambda: self.records >= count, 10)

    def handle_error(self, request: object, client_address: object) -> None:
        """Ignore a connection the client dropped."""

    def stop(self) -> None:
        """Hang up every connection, stop serving, and release the port."""
        self.release.set()
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


def _pipe(source: socket.socket, target: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            target.sendall(data)
    except OSError:
        pass
    finally:
        for end in (source, target):
            try:
                end.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class _ProxyHandler(BaseRequestHandler):
    server: TunnelProxy

    def handle(self) -> None:
        client: socket.socket = self.request
        head = b""
        while b"\r\n\r\n" not in head and (received := client.recv(65536)):
            head += received
        line = head.split(b"\r\n", 1)[0].decode()
        self.server.requests.append(line)
        if (refusal := self.server.refuse) is not None:
            client.sendall(refusal)
            return
        with socket.create_connection(("127.0.0.1", self.server.target)) as upstream:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            thread = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            thread.start()
            _pipe(client, upstream)
            thread.join(timeout=5)


class TunnelProxy(ThreadingTCPServer):
    """An HTTP proxy that tunnels every CONNECT request to one local port, or answers each with a refusal."""

    daemon_threads = True

    def __init__(self, target: int, *, refuse: bytes | None = None) -> None:
        """Bind a free local port and serve in a daemon thread; a refusal is sent instead of tunnelling."""
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.target = target
        self.refuse = refuse
        self.requests: list[str] = []
        self.url = f"http://127.0.0.1:{self.server_address[1]}"
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.005}, daemon=True)
        self.thread.start()

    def handle_error(self, request: object, client_address: object) -> None:
        """Ignore a tunnel either end dropped."""

    def stop(self) -> None:
        """Stop serving and release the port."""
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


def client_context() -> ssl.SSLContext:
    """Return a client TLS context that trusts the fixture servers' certificate."""
    return _contexts()[1]
