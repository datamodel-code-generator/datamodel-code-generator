"""WebSocket records and the connector contracts that WebSocket helpers open their connections through.

Importing them loads no WebSocket library, thread, or network code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, Literal, Protocol, final

from typing_extensions import TypeVar

from ..client.responses import HeadersView  # noqa: TC001 - Public annotations support get_type_hints().

if TYPE_CHECKING:
    from collections.abc import Callable
    from ssl import SSLContext

    from ..client.timing import Deadline
    from ..client.transports import AttemptIOContext

__all__ = (
    "AsyncWebSocketConnection",
    "AsyncWebSocketConnector",
    "Message",
    "PingReceipt",
    "ResolvedWSOptions",
    "ResolvedWebSocketTransportOptions",
    "WSFrame",
    "WebSocketConnection",
    "WebSocketConnector",
    "WebSocketOpenRequest",
)

T_co = TypeVar("T_co", covariant=True, default=object)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedWSOptions:
    """The effective WebSocket limits a connector receives, every default and inherited value already applied."""

    open_timeout: float | None
    idle_timeout: float | None
    max_message_bytes: int
    max_queue: int
    send_timeout: float | None
    ping_interval: float | None
    pong_timeout: float | None
    close_timeout: float
    compression: Literal["deflate"] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedWebSocketTransportOptions:
    """The effective WebSocket transport settings a connector receives; the proxy URL never appears in the repr."""

    ssl_context: SSLContext | None
    proxy: str | None = field(repr=False)
    proxy_ssl_context: SSLContext | None
    trust_env: bool


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class WebSocketOpenRequest:
    """One handshake a connector opens: its ws or wss URL, its headers, and the subprotocols it offers in order.

    The method is GET without a body. Neither the URL nor the headers appear in the representation.
    """

    url: str = field(repr=False)
    headers: HeadersView = field(repr=False)
    subprotocols: tuple[str, ...] = ()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class WSFrame:
    """One whole message a connection received: its bytes, and whether it arrived as text, which is UTF-8."""

    data: bytes = field(repr=False)
    text: bool


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Message(Generic[T_co]):
    """One message a session received: its decoded data, its frame kind, its sequence from 1, and its raw bytes.

    Neither the data nor the raw bytes appear in the representation.
    """

    data: T_co = field(repr=False)
    frame: Literal["text", "binary"]
    sequence: int
    raw: bytes = field(repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PingReceipt:
    """The answer to an explicit ping: how many seconds its pong took."""

    latency: float


class WebSocketConnection(Protocol):
    """One open connection a connector returned after a validated 101; the session that holds it always closes it.

    A method whose deadline passes first raises TimeoutError and leaves the connection usable, without having sent or
    consumed anything. Once the connection closed, `receive` raises WebSocketClosedError and `send` and `ping` raise it
    when nothing of theirs was sent. An I/O failure raises APIConnectionError with how far the message got; a message
    over `max_message_bytes` raises ProtocolSizeError after closing with 1009.
    """

    @property
    def handshake_headers(self) -> HeadersView:
        """Return the headers of the 101 response."""
        ...

    @property
    def subprotocol(self) -> str | None:
        """Return the subprotocol the server selected, or None."""
        ...

    def send(self, data: bytes, *, text: bool, deadline: Deadline | None) -> None:
        """Send one message as a text frame, whose bytes are UTF-8, or as a binary one."""

    def receive(self, *, deadline: Deadline | None) -> WSFrame:
        """Return the next whole message."""
        ...

    def ping(self, payload: bytes, *, deadline: Deadline | None, check: Callable[[], None] | None = None) -> float:
        """Send a ping and return the seconds until its pong arrived.

        An empty payload asks for a unique one; a payload another ping still waits for raises ProtocolStateError. With
        a check, the wait for the pong runs it at least every 50 ms and stops with what it raises.
        """
        ...

    def close(self, *, code: int = 1000, reason: str = "", timeout: float = 5) -> None:
        """Close with a code and a reason, waiting at most the timeout for the closing handshake; repeats do nothing."""

    def abort(self) -> None:
        """Drop the connection at once, without a closing handshake."""


class AsyncWebSocketConnection(Protocol):
    """The asyncio form of an open connection, with the same contract."""

    @property
    def handshake_headers(self) -> HeadersView:
        """Return the headers of the 101 response."""
        ...

    @property
    def subprotocol(self) -> str | None:
        """Return the subprotocol the server selected, or None."""
        ...

    async def send(self, data: bytes, *, text: bool, deadline: Deadline | None) -> None:
        """Send one message as a text frame, whose bytes are UTF-8, or as a binary one."""

    async def receive(self, *, deadline: Deadline | None) -> WSFrame:
        """Return the next whole message."""
        ...

    async def ping(self, payload: bytes, *, deadline: Deadline | None) -> float:
        """Send a ping and return the seconds until its pong arrived.

        An empty payload asks for a unique one; a payload another ping still waits for raises ProtocolStateError.
        """
        ...

    async def aclose(self, *, code: int = 1000, reason: str = "", timeout: float = 5) -> None:
        """Close with a code and a reason, waiting at most the timeout for the closing handshake; repeats do nothing."""

    def abort(self) -> None:
        """Drop the connection at once, without a closing handshake."""


class WebSocketConnector(Protocol):
    """A borrowed opener of WebSocket connections: each open performs at most one handshake.

    It follows no redirect and retries, authenticates, and reconnects nothing itself. A response other than 101 raises
    HandshakeResponse; any other failure raises the client's APIConnectionError, ConfigurationError, or one of their
    WebSocket subclasses with how far the handshake got.
    """

    def open(
        self,
        request: WebSocketOpenRequest,
        *,
        context: AttemptIOContext,
        options: ResolvedWSOptions,
        transport: ResolvedWebSocketTransportOptions,
    ) -> WebSocketConnection:
        """Open one connection within the context's connect timeout."""
        ...


class AsyncWebSocketConnector(Protocol):
    """A borrowed asyncio opener of WebSocket connections, with the same contract."""

    async def open(
        self,
        request: WebSocketOpenRequest,
        *,
        context: AttemptIOContext,
        options: ResolvedWSOptions,
        transport: ResolvedWebSocketTransportOptions,
    ) -> AsyncWebSocketConnection:
        """Open one connection within the context's connect timeout."""
        ...
