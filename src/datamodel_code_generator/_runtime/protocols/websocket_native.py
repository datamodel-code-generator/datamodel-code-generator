"""The client's own WebSocket connectors, on the threading and asyncio clients of the websockets library.

Each open performs one handshake and follows no redirect: a response other than 101 becomes HandshakeResponse for the
client's call path to handle, and every other failure is classified with how far the handshake got. Timeouts, limits,
TLS, and proxies are always given explicitly instead of the library's defaults or environment; environment proxies apply
only when the transport settings trust the environment. Only packages with WebSocket helpers import this module.
"""

from __future__ import annotations

import logging
import ssl
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any, Final, cast

from websockets.asyncio.client import ClientConnection as _AsyncClientConnection
from websockets.asyncio.client import connect as _aconnect
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedOK,
    InvalidHandshake,
    InvalidHeader,
    InvalidMessage,
    InvalidProxyStatus,
    InvalidStatus,
    InvalidUpgrade,
    NegotiationError,
    PayloadTooBig,
    ProxyError,
    SecurityError,
)
from websockets.http11 import USER_AGENT
from websockets.protocol import State
from websockets.proxy import get_proxy
from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as _connect
from websockets.uri import parse_uri

from ..client.errors import (
    DeliveryState,
    PhaseTimeoutError,
    ProtocolConfigurationError,
    ProtocolSizeError,
    TransportError,
)
from ..client.responses import HeadersView
from ..client.transports import attempt_trace
from .errors import (
    MAX_RAW_PREFIX,
    HandshakeCondition,
    HandshakeResponse,
    WebSocketClosedError,
    WebSocketHandshakeError,
    WebSocketProxyError,
)
from .websocket_types import WSFrame

if TYPE_CHECKING:
    import socket
    from collections.abc import Iterator

    from websockets.client import ClientProtocol
    from websockets.datastructures import HeadersLike
    from websockets.http11 import Response

    from ..client.timing import Deadline
    from ..client.transports import AttemptIOContext, TransportTraceSink
    from .websocket_types import ResolvedWebSocketTransportOptions, ResolvedWSOptions, WebSocketOpenRequest

__all__ = ("AsyncNativeConnection", "AsyncNativeConnector", "NativeConnection", "NativeConnector")

_FRAGMENT: Final = 32768
_PROXY_SCHEMES: Final = ("http://", "https://")
_WRITE_LIMIT: Final = 32768
_TOO_BIG: Final = 1009
_CONDITIONS: Final[tuple[tuple[type[InvalidHandshake], HandshakeCondition], ...]] = (
    (SecurityError, "security"),
    (InvalidUpgrade, "upgrade"),
    (InvalidHeader, "invalid_header"),
    (NegotiationError, "negotiation"),
)


@dataclass(slots=True)
class _Evidence:
    """How far one open got: whether its handshake request started, and whether its response headers arrived."""

    context: AttemptIOContext
    entered: bool = False
    responded: bool = False

    @property
    def trace(self) -> TransportTraceSink:
        """Return the sink the open reports to."""
        return self.context.trace

    @property
    def delivery(self) -> DeliveryState:
        """Return how far the request got."""
        if self.responded:
            return DeliveryState.RESPONSE_STARTED
        return DeliveryState.MAYBE_SENT if self.entered else DeliveryState.NOT_SENT


_OPENING: Final[ContextVar[_Evidence]] = ContextVar("dcg_websocket_opening")


class _Quiet(logging.LoggerAdapter[logging.Logger]):
    """The library's client logger with debug records off, since they hold handshake headers and message bytes."""

    def isEnabledFor(self, level: int) -> bool:  # noqa: N802 - It overrides the logging API.
        """Refuse debug records, and defer every other level to the logger."""
        return level > logging.DEBUG and self.logger.isEnabledFor(level)


_LOGGER: Final = _Quiet(logging.getLogger("websockets.client"))


def _rejected(response: Response) -> HandshakeResponse:
    """Return the client's signal for a response other than 101, with at most 64 KiB of its body."""
    body = bytes(response.body)
    return HandshakeResponse(
        status_code=response.status_code,
        headers=HeadersView(tuple(response.headers.raw_items())),
        body_prefix=body[:MAX_RAW_PREFIX],
        truncated=len(body) > MAX_RAW_PREFIX,
    )


class _Paused:
    """A socket whose reads wait until the handshake request was written, then go straight through.

    The threading client reads in a thread of its own from the start. With TLS 1.3, a read that meets the server's
    session tickets while the request is being written can lose the request, as OpenSSL allows no concurrent use of one
    connection; the tickets arrive before the 101, so later reads and writes do not meet them. Shutting the socket down
    releases a waiting read.
    """

    __slots__ = ("_socket", "_written")

    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock
        self._written = threading.Event()

    def recv(self, size: int) -> bytes:
        """Read once the request was written."""
        self._written.wait()
        return self._socket.recv(size)

    def sendall(self, data: bytes) -> None:
        """Write, releasing the reads."""
        try:
            self._socket.sendall(data)
        finally:
            self._written.set()

    def shutdown(self, how: int) -> None:
        """Shut the socket down, releasing a waiting read."""
        self._written.set()
        self._socket.shutdown(how)

    def __getattr__(self, name: str) -> object:
        return getattr(self._socket, name)


class _Connection(ClientConnection):
    """The threading connection, recording how far its handshake got and giving statuses back to the client."""

    def __init__(self, sock: socket.socket, protocol: ClientProtocol, **settings: Any) -> None:
        """Take the connected socket, whose reads wait for the handshake request."""
        super().__init__(cast("socket.socket", _Paused(sock)), protocol, **settings)

    def handshake(
        self,
        additional_headers: HeadersLike | None = None,
        user_agent_header: str | None = USER_AGENT,
        timeout: float | None = None,
    ) -> None:
        """Perform the opening handshake, raising HandshakeResponse for a response other than 101."""
        evidence = _OPENING.get()
        evidence.entered = True
        evidence.trace.request_headers_started()
        try:
            super().handshake(additional_headers, user_agent_header, timeout)
        except InvalidStatus as error:
            raise _rejected(error.response) from None
        finally:
            evidence.responded = self.response is not None


class _AsyncConnection(_AsyncClientConnection):
    """The asyncio connection, recording how far its handshake got and giving statuses back to the client."""

    async def handshake(
        self, additional_headers: HeadersLike | None = None, user_agent_header: str | None = USER_AGENT
    ) -> None:
        """Perform the opening handshake, raising HandshakeResponse for a response other than 101."""
        evidence = _OPENING.get()
        evidence.entered = True
        evidence.trace.request_headers_started()
        try:
            await super().handshake(additional_headers, user_agent_header)
        except InvalidStatus as error:
            raise _rejected(error.response) from None
        finally:
            evidence.responded = self.response is not None


def _failure(error: Exception, evidence: _Evidence, timeout: float | None) -> Exception:
    """Return the client's failure of a native error that ended an open before its connection was handed over.

    A timeout is the open's cap expiring, unless the open had none and the operating system timed out.
    """
    delivery = evidence.delivery
    if delivery is DeliveryState.NOT_SENT:
        attempt_trace(evidence.context).proven_not_sent = True
    failure: Exception = TransportError(delivery_state=delivery, phase="connect", cause=error)
    match error:
        case TimeoutError() if timeout is not None:
            failure = PhaseTimeoutError(
                effective_timeout=timeout, phase="connect", delivery_state=delivery, cause=error
            )
        case InvalidProxyStatus():
            failure = WebSocketProxyError(proxy_status_code=error.response.status_code, cause=error)
        case ProxyError():
            failure = WebSocketProxyError(cause=error)
        case InvalidMessage() if isinstance(error.__cause__, (EOFError, OSError)):
            pass
        case InvalidHandshake():
            failure = WebSocketHandshakeError(condition=_condition(error), delivery_state=delivery, cause=error)
        case _:
            pass
    return failure


def _condition(error: InvalidHandshake) -> HandshakeCondition:
    """Return what a handshake broke: a size limit of its response, its upgrade, a header, or its negotiation."""
    if isinstance(error, InvalidMessage):
        return "size" if isinstance(error.__cause__, SecurityError) else "invalid_message"
    found: list[HandshakeCondition] = [name for kind, name in _CONDITIONS if isinstance(error, kind)]
    return found[0] if found else "invalid_message"


def _closed(error: ConnectionClosed, parsed: BaseException | None, limit: int) -> Exception:
    """Return the client's failure of a closed connection: a message over the limit, or the closure received."""
    if isinstance(parsed, PayloadTooBig) or (error.sent is not None and error.sent.code == _TOO_BIG):
        size = parsed.size if isinstance(parsed, PayloadTooBig) and parsed.size is not None else 0
        current = parsed.current_size if isinstance(parsed, PayloadTooBig) and parsed.current_size is not None else 0
        return ProtocolSizeError(
            kind="message", limit=limit, observed=max(limit + 1, size + current), unit="bytes", cause=error
        )
    received = error.rcvd
    return WebSocketClosedError(
        code=None if received is None else received.code,
        reason="" if received is None else received.reason,
        clean=isinstance(error, ConnectionClosedOK),
        cause=error,
    )


def _frame(data: str | bytes) -> WSFrame:
    """Return a received message as its bytes and frame kind."""
    if isinstance(data, str):
        return WSFrame(data=data.encode("utf-8"), text=True)
    return WSFrame(data=bytes(data), text=False)


def _proxy(url: str, transport: ResolvedWebSocketTransportOptions) -> str | None:
    """Return the proxy to use: the explicit one, else the environment's when trusted, which must be HTTP or HTTPS."""
    if transport.proxy is not None or not transport.trust_env:
        return transport.proxy
    if (proxy := get_proxy(parse_uri(url))) is not None and not proxy.lower().startswith(_PROXY_SCHEMES):
        raise ProtocolConfigurationError(field_path=("websocket_transport", "trust_env"), condition="invalid_value")
    return proxy


class _Connector:
    """What the threading and asyncio connectors share: the standard verifying TLS context and the handshake options."""

    @cached_property
    def _tls(self) -> ssl.SSLContext:
        return ssl.create_default_context()

    def _arguments(
        self, request: WebSocketOpenRequest, options: ResolvedWSOptions, transport: ResolvedWebSocketTransportOptions
    ) -> dict[str, Any]:
        """Return every handshake setting explicitly, none taken from the library's defaults or environment.

        The proxy TLS context is given only with an explicit HTTPS proxy, which its options require.
        """
        secure = request.url.startswith("wss:")
        context = transport.proxy_ssl_context
        return {
            "origin": None,
            "logger": _LOGGER,
            "subprotocols": list(request.subprotocols) or None,
            "compression": options.compression,
            "additional_headers": list(request.headers.items()),
            "user_agent_header": None,
            "proxy": _proxy(request.url, transport),
            "open_timeout": options.open_timeout,
            "ping_interval": options.ping_interval,
            "ping_timeout": options.pong_timeout,
            "close_timeout": options.close_timeout,
            "max_size": options.max_message_bytes,
            "max_queue": options.max_queue,
            "ssl": (transport.ssl_context or self._tls) if secure else None,
            **({} if context is None else {"proxy_ssl": context}),
        }


class NativeConnector(_Connector):
    """The client's own threading WebSocket connector; it holds no connection and needs no closing."""

    def open(
        self,
        request: WebSocketOpenRequest,
        *,
        context: AttemptIOContext,
        options: ResolvedWSOptions,
        transport: ResolvedWebSocketTransportOptions,
    ) -> NativeConnection:
        """Open one connection within the open timeout, performing one handshake."""
        evidence = _Evidence(context)
        token = _OPENING.set(evidence)
        try:
            connection = _connect(
                request.url,
                create_connection=_Connection,
                legacy=True,
                **self._arguments(request, options, transport),
            )
        except (OSError, InvalidHandshake) as error:
            raise _failure(error, evidence, options.open_timeout) from None
        finally:
            _OPENING.reset(token)
        return NativeConnection(connection, options.max_message_bytes)


class AsyncNativeConnector(_Connector):
    """The client's own asyncio WebSocket connector; it holds no connection and needs no closing."""

    async def open(
        self,
        request: WebSocketOpenRequest,
        *,
        context: AttemptIOContext,
        options: ResolvedWSOptions,
        transport: ResolvedWebSocketTransportOptions,
    ) -> AsyncNativeConnection:
        """Open one connection within the open timeout, performing one handshake."""
        evidence = _Evidence(context)
        token = _OPENING.set(evidence)
        try:
            connection = await _aconnect(
                request.url,
                create_connection=_AsyncConnection,
                write_limit=_WRITE_LIMIT,
                **self._arguments(request, options, transport),
            )
        except (OSError, InvalidHandshake) as error:
            raise _failure(error, evidence, options.open_timeout) from None
        finally:
            _OPENING.reset(token)
        return AsyncNativeConnection(connection, options.max_message_bytes)


class _Overdue(Exception):  # noqa: N818 - An internal signal, never raised to callers.
    """The deadline of a fragmented send passed between two fragments."""


def _fragments(data: bytes, deadline: Deadline | None, sent: list[int]) -> Iterator[bytes]:
    """Yield a message's fragments, refusing the next one once the deadline passed, counting those handed over."""
    for start in range(0, len(data), _FRAGMENT):
        if deadline is not None and deadline.remaining() <= 0:
            raise _Overdue
        yield data[start : start + _FRAGMENT]
        sent[0] += 1


class NativeConnection:
    """An open threading connection: whole messages in and out, pings, and closing."""

    __slots__ = ("_connection", "_limit", "handshake_headers")

    def __init__(self, connection: ClientConnection, limit: int) -> None:
        """Take the connection a 101 opened."""
        self._connection = connection
        self._limit = limit
        response = connection.response
        assert response is not None
        self.handshake_headers = HeadersView(tuple(response.headers.raw_items()))

    @property
    def subprotocol(self) -> str | None:
        """Return the subprotocol the server selected, or None."""
        return self._connection.subprotocol

    def _closed(self, error: ConnectionClosed) -> Exception:
        return _closed(error, self._connection.protocol.parser_exc, self._limit)

    def send(self, data: bytes, *, text: bool, deadline: Deadline | None) -> None:
        """Send one message, in fragments of 32 KiB checked against the deadline when it is longer."""
        if deadline is not None and deadline.remaining() <= 0:
            raise TimeoutError
        sent = [0]
        try:
            if len(data) <= _FRAGMENT:
                self._connection.send(data, text=text)
            else:
                self._connection.send(_fragments(data, deadline, sent), text=text)
        except _Overdue:
            raise TransportError(delivery_state=DeliveryState.MAYBE_SENT, phase="write", cause=TimeoutError()) from None
        except ConnectionClosed as error:
            raise self._undelivered(error, partial=bool(sent[0])) from None

    def _undelivered(self, error: ConnectionClosed, *, partial: bool) -> Exception:
        """Return a send's closed connection: a message that may have gone, or the closure when nothing was written."""
        maybe = partial or isinstance(error.__cause__, OSError)
        written = TransportError(delivery_state=DeliveryState.MAYBE_SENT, phase="write", cause=error)
        return written if maybe else self._closed(error)

    def receive(self, *, deadline: Deadline | None) -> WSFrame:
        """Return the next whole message, raising TimeoutError when the deadline passes first."""
        try:
            data = self._connection.recv(None if deadline is None else deadline.remaining())
        except ConnectionClosed as error:
            raise self._closed(error) from None
        return _frame(data)

    def ping(self, payload: bytes, *, deadline: Deadline | None) -> float:
        """Send a ping and return the seconds until its pong, raising TimeoutError when the deadline passes first."""
        connection = self._connection
        try:
            pong = connection.ping(payload, ack_on_close=True)
        except ConnectionClosed as error:
            raise self._closed(error) from None
        if not pong.wait(None if deadline is None else deadline.remaining()):
            raise TimeoutError
        if (protocol := connection.protocol).state is State.CLOSED:
            raise self._closed(protocol.close_exc)
        return connection.latency

    def close(self, *, code: int = 1000, reason: str = "", timeout: float = 5) -> None:
        """Close with a code and a reason, waiting at most the timeout for the closing handshake."""
        self._connection.close_timeout = timeout
        self._connection.close(code, reason)

    def abort(self) -> None:
        """Drop the connection at once."""
        self._connection.close_socket()


class AsyncNativeConnection:
    """An open asyncio connection: whole messages in and out, pings, and closing."""

    __slots__ = ("_connection", "_limit", "handshake_headers")

    def __init__(self, connection: _AsyncClientConnection, limit: int) -> None:
        """Take the connection a 101 opened."""
        self._connection = connection
        self._limit = limit
        response = connection.response
        assert response is not None
        self.handshake_headers = HeadersView(tuple(response.headers.raw_items()))

    @property
    def subprotocol(self) -> str | None:
        """Return the subprotocol the server selected, or None."""
        return self._connection.subprotocol

    def _closed(self, error: ConnectionClosed) -> Exception:
        return _closed(error, self._connection.protocol.parser_exc, self._limit)

    async def send(self, data: bytes, *, text: bool, deadline: Deadline | None) -> None:
        """Send one message; the client bounds the await by the deadline itself."""
        del deadline
        try:
            await self._connection.send(data, text=text)
        except ConnectionClosed as error:
            maybe = isinstance(error.__cause__, OSError)
            written = TransportError(delivery_state=DeliveryState.MAYBE_SENT, phase="write", cause=error)
            raise (written if maybe else self._closed(error)) from None

    async def receive(self, *, deadline: Deadline | None) -> WSFrame:
        """Return the next whole message; the client bounds the await by the deadline itself."""
        del deadline
        try:
            data = await self._connection.recv()
        except ConnectionClosed as error:
            raise self._closed(error) from None
        return _frame(data)

    async def ping(self, payload: bytes, *, deadline: Deadline | None) -> float:
        """Send a ping and return the seconds until its pong; the client bounds the await by the deadline itself."""
        del deadline
        try:
            return await (await self._connection.ping(payload))
        except ConnectionClosed as error:
            raise self._closed(error) from None

    async def aclose(self, *, code: int = 1000, reason: str = "", timeout: float = 5) -> None:
        """Close with a code and a reason, waiting at most the timeout for the closing handshake."""
        self._connection.close_timeout = timeout
        await self._connection.close(code, reason)

    def abort(self) -> None:
        """Drop the connection at once."""
        self._connection.transport.abort()
