"""WebSocket helpers: the handshake as one child call of a session of their own, and the sessions holding the socket.

A helper opens its channel's operation through the client's call path with an adapter of its own, which hands the
request to a WebSocket connector, so authentication, limiters, hooks, and send budgets apply to the handshake as to any
call; a refused handshake is never redirected or retried, and only a handshake proven unsent is retried. The 101 is
handed over as a streaming handle whose close closes the connection; the session reads and writes whole messages through
the connection and ends that handle once it closes or fails.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, Literal, cast, final

import httpx2
from typing_extensions import Self, TypeVar

from ..client.errors import (
    APITimeoutError,
    ConfigurationError,
    DecodeError,
    DeliveryState,
    ProtocolError,
    SDKError,
    is_client_closed,
    is_deadline,
    is_phase_timeout,
    is_transport,
)
from ..client.native import request_fields
from ..client.options import RequestOptions
from ..client.raw import afinished, finished
from ..client.responses import HeadersView
from ..client.timing import SYSTEM_CLOCK, Clock, Deadline, SessionOptions, real_end, wait_left
from ..model_codecs.errors import (
    CodecBindingError,
    CodecResourceLimitError,
    ModelProjectionError,
    NativeValidationError,
    ParameterEncodingError,
    WireValidationError,
)
from ..model_codecs.media import decode_json, encode_json
from ..model_codecs.unset import UNSET, Unset
from .errors import (
    MAX_CLOSE_REASON,
    MAX_RAW_PREFIX,
    ConcurrentReceiveError,
    DeliveryUnknownError,
    HandshakeResponse,
    ProtocolStateError,
    StreamDecodeError,
    WebSocketClosedError,
    WebSocketHandshakeError,
)
from .options import WSOptions, resolved_transport
from .websocket_types import Message, PingReceipt, ResolvedWSOptions, WebSocketOpenRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.codecs import NativeValue
    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import Encoder, OperationPlan
    from ..client.raw import AsyncRawResponse, RawResponse
    from ..client.responses import ResponseInfo
    from .records import ProtocolProgress
    from .references import OperationRef
    from .websocket_types import (
        AsyncWebSocketConnection,
        AsyncWebSocketConnector,
        ResolvedWebSocketTransportOptions,
        WebSocketConnection,
        WebSocketConnector,
        WSFrame,
    )

__all__ = (
    "AsyncWebSocketSession",
    "ChannelPlan",
    "WebSocketSession",
    "aconnect_socket",
    "connect_socket",
)

SendT = TypeVar("SendT")
RecvT = TypeVar("RecvT")
V = TypeVar("V")
ErrorT = TypeVar("ErrorT", bound=SDKError)
OpenedT = TypeVar("OpenedT")

_NATIVE_FRAMING: Final = frozenset({"host", "content-length", "transfer-encoding"})
_MANAGED: Final = frozenset({
    "connection",
    "content-length",
    "host",
    "sec-websocket-accept",
    "sec-websocket-extensions",
    "sec-websocket-key",
    "sec-websocket-protocol",
    "sec-websocket-version",
    "transfer-encoding",
    "upgrade",
})
_SCHEMES: Final = {"https": "wss", "http": "ws"}
_UPGRADED: Final = HeadersView(())
_SWITCHING: Final = 101
_FINAL: Final = 200
_NORMAL: Final = 1000
_GOING_AWAY: Final = 1001
_PROTOCOL_ERROR: Final = 1002
_APPLICATION_CODES: Final = range(3000, 5000)
_MAX_PING: Final = 125
_DATA_ERRORS: Final = (
    CodecBindingError,
    CodecResourceLimitError,
    ModelProjectionError,
    NativeValidationError,
    ParameterEncodingError,
    WireValidationError,
)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelPlan(Generic[SendT, RecvT]):
    """Everything fixed about one generated WebSocket helper: its identity, handshake call, and messages.

    A message's codec is `json`, decoded or encoded by the message's schema, `utf8` text, or `bytes`; its frame is the
    kind every message of that direction uses. `connectors` create the client's own synchronous and asyncio connectors,
    used when the client was given none. Deflate compression is allowed only where `compression` permits it.
    """

    helper_id: str
    operation: OperationRef
    call: OperationPlan[object]
    fingerprint: str
    connectors: tuple[Callable[[], WebSocketConnector], Callable[[], AsyncWebSocketConnector]]
    send_codec: Literal["json", "utf8", "bytes"] = "json"
    send_frame: Literal["text", "binary"] = "text"
    encoder: Encoder | None = None
    receive_codec: Literal["json", "utf8", "bytes"] = "json"
    receive_frame: Literal["text", "binary"] = "text"
    decoder: NativeValue[RecvT] | None = None
    subprotocols: tuple[str, ...] = ()
    compression: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective WebSocket limits, session limits, call options, and transport settings of one connect.

    A WebSocket session has no total timeout.
    """

    socket: ResolvedWSOptions
    transport: ResolvedWebSocketTransportOptions
    total_timeout: float | None = None
    deadline: Deadline | None = None
    options: RequestOptions | None = None
    clock: Clock = SYSTEM_CLOCK


_SOCKET: Final = ResolvedWSOptions(
    open_timeout=5.0,
    idle_timeout=None,
    max_message_bytes=1048576,
    max_queue=16,
    send_timeout=30.0,
    ping_interval=20.0,
    pong_timeout=20.0,
    close_timeout=5.0,
    compression=None,
)


def _first(layers: tuple[object, ...], name: str, default: V) -> V:
    """Return a limit from the first options layer that sets it, or its default."""
    for layer in layers:
        if layer is not None and not isinstance(layer, Unset) and not isinstance(value := getattr(layer, name), Unset):
            return cast("V", value)
    return default


def _socket(layers: tuple[object, ...], idle: float | None) -> ResolvedWSOptions:
    """Return the WebSocket limits the layers set, the inherited idle timeout and the kind's defaults below them."""
    d = _SOCKET
    return ResolvedWSOptions(
        open_timeout=_first(layers, "open_timeout", d.open_timeout),
        idle_timeout=_first(layers, "idle_timeout", idle),
        max_message_bytes=_first(layers, "max_message_bytes", d.max_message_bytes),
        max_queue=_first(layers, "max_queue", d.max_queue),
        send_timeout=_first(layers, "send_timeout", d.send_timeout),
        ping_interval=_first(layers, "ping_interval", d.ping_interval),
        pong_timeout=_first(layers, "pong_timeout", d.pong_timeout),
        close_timeout=_first(layers, "close_timeout", d.close_timeout),
        compression=_first(layers, "compression", d.compression),
    )


def _invalid(
    plan: ChannelPlan[SendT, RecvT], path: tuple[str, ...], condition: Literal["invalid_value"]
) -> ConfigurationError:
    return ConfigurationError(field_path=path, reason=condition, helper_id=plan.helper_id, operation=plan.operation)


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: ChannelPlan[SendT, RecvT],
    ws_options: object,
    options: object,
    session_options: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    An idle timeout neither layer sets is the call's merged stream idle timeout. Compression requires the helper's
    permission.
    """
    for name, value, kind in (
        ("ws_options", ws_options, WSOptions),
        ("options", options, RequestOptions),
        ("session_options", session_options, SessionOptions),
    ):
        if value is not None and not isinstance(value, kind):
            raise _invalid(plan, (name,), "invalid_value")
    request = options if isinstance(options, RequestOptions) else None
    defaults = core.protocol_defaults(plan.helper_id)
    kinds = (ws_options, UNSET if defaults is None else defaults.options)
    sessions = (session_options, UNSET if defaults is None else defaults.session)
    socket = _socket(kinds, core.call_settings(request, plan.call).stream_idle_timeout)
    if socket.compression is not None and not plan.compression:
        raise _invalid(plan, ("ws_options", "compression"), "invalid_value")
    protocols = core.protocol_options()
    return _Limits(
        socket=socket,
        transport=resolved_transport(UNSET if protocols is None else protocols.websocket_transport),
        total_timeout=_first(sessions, "total_timeout", None),
        deadline=_first(sessions, "deadline", None),
        options=request,
        clock=core.clock,
    )


def _progress(sent: int = 0, received: int = 0) -> ProtocolProgress:
    return MappingProxyType({
        "messages_sent": sent,
        "messages_received": received,
    })


def _session(limits: _Limits) -> OperationSession:
    """Start the socket's session."""
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only a connect loads the call runtime.

    return OperationSession(total_timeout=limits.total_timeout, deadline=limits.deadline, clock=limits.clock)


def _checked_headers(headers: HeadersView) -> None:
    """Refuse a header the WebSocket handshake itself writes, before anything is sent."""
    for name in dict(headers.items()):
        if name.lower() in _MANAGED:
            raise ConfigurationError(field_path=("headers", name), reason="managed")


def _encoded(plan: ChannelPlan[SendT, RecvT], value: object) -> bytes:
    """Return a value encoded as the plan's sent message: JSON by its schema, UTF-8 text, or the bytes themselves."""
    match plan.send_codec:
        case "json":
            assert plan.encoder is not None
            return encode_json(plan.encoder.encode(value))
        case "utf8" if isinstance(value, str):
            return value.encode("utf-8")
        case "bytes" if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        case _:
            pass
    msg = f"A {plan.send_codec} message cannot carry a {type(value).__name__}"
    raise TypeError(msg)


def _socket_url(url: str) -> str:
    """Return the ws or wss URL of a prepared http or https URL."""
    scheme, _, rest = url.partition(":")
    return f"{_SCHEMES[scheme.lower()]}:{rest}"


class _RejectedBytes(httpx2.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.content


class _Upgraded(httpx2.SyncByteStream):
    """The 101 of a synchronous handshake, holding its connection until the handle that owns it closes.

    Closing sends the code the session chose, going away by default, waiting at most the close timeout; without a code,
    or when the closing handshake fails, the connection is dropped.
    """

    __slots__ = ("code", "connection", "reason", "subprotocol", "timeout")

    def __init__(self, connection: WebSocketConnection, timeout: float) -> None:
        self.connection = connection
        self.subprotocol = connection.subprotocol
        self.timeout = timeout
        self.code: int | None = _GOING_AWAY
        self.reason = ""

    def __iter__(self) -> Iterator[bytes]:
        return iter(())

    def close(self) -> None:
        """Close the connection with the chosen code, or drop it."""
        connection = self.connection
        if (code := self.code) is None:
            connection.abort()
            return
        try:
            connection.close(code=code, reason=self.reason, timeout=self.timeout)
        except BaseException:
            connection.abort()
            raise


class _AsyncUpgraded(httpx2.AsyncByteStream):
    """The 101 of an asyncio handshake, holding its connection, as the synchronous one does."""

    __slots__ = ("code", "connection", "reason", "subprotocol", "timeout")

    def __init__(self, connection: AsyncWebSocketConnection, timeout: float) -> None:
        self.connection = connection
        self.subprotocol = connection.subprotocol
        self.timeout = timeout
        self.code: int | None = _GOING_AWAY
        self.reason = ""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        chunk: bytes
        for chunk in ():
            yield chunk

    async def aclose(self) -> None:
        """Close the connection with the chosen code, or drop it."""
        connection = self.connection
        if (code := self.code) is None:
            connection.abort()
            return
        try:
            await connection.aclose(code=code, reason=self.reason, timeout=self.timeout)
        except BaseException:
            connection.abort()
            raise


class _Handshakes(Generic[OpenedT]):
    """What the synchronous and asyncio handshake adapters share: the plan, the limits, and the checks of a 101."""

    __slots__ = ("_plan", "_socket", "_transport", "opened")

    def __init__(self, plan: ChannelPlan[SendT, RecvT], limits: _Limits) -> None:
        self._plan: ChannelPlan[object, object] = cast("ChannelPlan[object, object]", plan)
        self._socket = limits.socket
        self._transport = limits.transport
        self.opened: OpenedT | None = None

    def _request(self, request: httpx2.Request) -> WebSocketOpenRequest:
        return WebSocketOpenRequest(
            url=_socket_url(str(request.url)),
            headers=HeadersView(
                (name, value) for name, value in request_fields(request) if name.lower() not in _NATIVE_FRAMING
            ),
            subprotocols=self._plan.subprotocols,
        )

    def _options(self, call: LogicalCallContext) -> ResolvedWSOptions:
        return replace(self._socket, open_timeout=call.timeout().connect)

    def _problem(self, headers: object, selected: object) -> SDKError | None:
        if not isinstance(headers, HeadersView) or not isinstance(selected, (str, type(None))):
            return WebSocketHandshakeError(condition="negotiation", delivery_state=DeliveryState.RESPONSE_STARTED)
        offered = self._plan.subprotocols
        if (selected is None and offered) or (selected is not None and selected not in offered):
            return WebSocketHandshakeError(condition="negotiation", delivery_state=DeliveryState.RESPONSE_STARTED)
        return None


@final
class _Handshake(_Handshakes[_Upgraded]):
    """Open each handshake attempt of a synchronous helper through its connector, as one adapter send."""

    __slots__ = ("_connector",)

    def __init__(self, plan: ChannelPlan[SendT, RecvT], limits: _Limits, connector: WebSocketConnector) -> None:
        super().__init__(plan, limits)
        self._connector = connector

    def open(self, request: httpx2.Request, call: LogicalCallContext) -> httpx2.Response:
        """Open the socket once, returning a native response whose stream owns its close."""
        opening = self._request(request)
        try:
            connection = self._connector.open(
                opening, deadline=call.deadline, options=self._options(call), transport=self._transport
            )
        except HandshakeResponse as response:
            if response.status_code < _FINAL:
                raise WebSocketHandshakeError(
                    condition="negotiation", delivery_state=DeliveryState.RESPONSE_STARTED, cause=response
                ) from None
            return httpx2.Response(
                response.status_code, headers=response.headers.items(), stream=httpx2.ByteStream(response.body_prefix)
            )
        if (problem := self._problem(connection.handshake_headers, connection.subprotocol)) is not None:
            connection.abort()
            raise problem
        self.opened = opened = _Upgraded(connection, self._socket.close_timeout)
        return httpx2.Response(_SWITCHING, headers=connection.handshake_headers.items(), stream=opened)

    def close(self) -> None:
        """Release nothing: the connector is the client's or borrowed."""


@final
class _AsyncHandshake(_Handshakes[_AsyncUpgraded]):
    """Open each handshake attempt of an asyncio helper through its connector."""

    __slots__ = ("_connector",)

    def __init__(self, plan: ChannelPlan[SendT, RecvT], limits: _Limits, connector: AsyncWebSocketConnector) -> None:
        super().__init__(plan, limits)
        self._connector = connector

    async def open(self, request: httpx2.Request, call: LogicalCallContext) -> httpx2.Response:
        """Open the socket once, returning a native response whose stream owns its close."""
        opening = self._request(request)
        try:
            connection = await self._connector.open(
                opening, deadline=call.deadline, options=self._options(call), transport=self._transport
            )
        except HandshakeResponse as response:
            if response.status_code < _FINAL:
                raise WebSocketHandshakeError(
                    condition="negotiation", delivery_state=DeliveryState.RESPONSE_STARTED, cause=response
                ) from None
            return httpx2.Response(
                response.status_code, headers=response.headers.items(), stream=_RejectedBytes(response.body_prefix)
            )
        if (problem := self._problem(connection.handshake_headers, connection.subprotocol)) is not None:
            connection.abort()
            raise problem
        self.opened = opened = _AsyncUpgraded(connection, self._socket.close_timeout)
        return httpx2.Response(_SWITCHING, headers=connection.handshake_headers.items(), stream=opened)

    async def aclose(self) -> None:
        """Release nothing: the connector is the client's or borrowed."""


class _State(Enum):
    OPEN = "open"
    ENDED = "ended"
    FAILED = "failed"
    CLOSED = "closed"


class _Sockets(Generic[SendT, RecvT]):
    """What the synchronous and asyncio sessions share: the plan, the limits, the state, and the message codecs."""

    __slots__ = (
        "_call",
        "_closed",
        "_info",
        "_lock",
        "_opened",
        "_plan",
        "_prefix",
        "_received",
        "_receiving",
        "_sent",
        "_session",
        "_socket",
        "_state",
    )

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        plan: ChannelPlan[SendT, RecvT],
        limits: _Limits,
        session: OperationSession,
        info: ResponseInfo,
        call: LogicalCallContext,
        opened: _Upgraded | _AsyncUpgraded,
    ) -> None:
        """Start an open session on the connection a 101 handed over."""
        self._plan = plan
        self._socket = limits.socket
        self._session = session
        self._info = info
        self._call = call
        self._opened = opened
        self._prefix = min(limits.socket.max_message_bytes, MAX_RAW_PREFIX)
        self._lock = threading.Lock()
        self._receiving = threading.Lock()
        self._state = _State.OPEN
        self._closed: tuple[int | None, str, bool] = (None, "", False)
        self._sent = 0
        self._received = 0

    def __repr__(self) -> str:
        """Name the helper, the state, and the selected subprotocol, never a message or a URL."""
        return (
            f"{type(self).__name__}(helper_id={self._plan.helper_id!r}, state={self._state.value!r}, "
            f"subprotocol={self._opened.subprotocol!r})"
        )

    @property
    def response(self) -> ResponseInfo:
        """Return the metadata of the handshake's 101 response."""
        return self._info

    @property
    def subprotocol(self) -> str | None:
        """Return the subprotocol the server selected, or None."""
        return self._opened.subprotocol

    @property
    def progress(self) -> ProtocolProgress:
        """Return the session's sends and the messages sent and received so far."""
        return _progress(self._sent, self._received)

    def _stamped(self, error: ErrorT) -> ErrorT:
        """Give a failure of the session the helper's context, the handshake call's identity, and its counters."""
        if isinstance(error, (ProtocolError, WebSocketHandshakeError)):
            error.helper_id = self._plan.helper_id
            error.operation = self._plan.operation
        failure = self._call.snapshot_error(error)
        failure.info = self._info
        return failure

    def _own(self, error: BaseException) -> BaseException:
        """Return a failure as the session's: stamped, with a connection's unclassified exception as its cause.

        A native interruption, such as cancellation, stays itself.
        """
        if isinstance(error, SDKError):
            return self._stamped(error)
        if isinstance(error, Exception):
            return self._stamped(
                WebSocketHandshakeError(
                    condition="negotiation", delivery_state=DeliveryState.RESPONSE_STARTED, cause=error
                )
            )
        return error

    def _halted(self) -> Exception | None:
        """Return what stops the session's call now: cancellation, the client closing, or the deadline.

        A native cancellation propagates as it is.
        """
        try:
            self._call.check("stream", DeliveryState.RESPONSE_STARTED)
        except Exception as error:  # noqa: BLE001
            return error
        return None

    def _checked(self) -> None:
        self._call.check("stream", DeliveryState.RESPONSE_STARTED)

    def _state_error(self, action: str) -> ProtocolStateError:
        return self._stamped(ProtocolStateError(state=self._state.value, action=action))

    def _close_error(self) -> WebSocketClosedError:
        code, reason, clean = self._closed
        return self._stamped(WebSocketClosedError(code=code, reason=reason, clean=clean))

    def _usable(self, action: str) -> None:
        """Refuse an action once the session ended: the server's closure again, else the state it ended in."""
        if (state := self._state) is _State.OPEN:
            return
        if state is _State.ENDED:
            raise self._close_error()
        raise self._state_error(action)

    def _enter_receive(self) -> None:
        """Take the session's one receive, refusing a concurrent one and an ended session."""
        if not self._receiving.acquire(blocking=False):
            raise self._stamped(ConcurrentReceiveError())
        try:
            self._usable("receive")
        except BaseException:
            self._receiving.release()
            raise

    def _closed_here(self) -> bool:
        """Return whether this session's own close ended it, which a wait in progress learns only afterwards."""
        return self._state is _State.CLOSED

    def _ending(self, state: _State) -> bool:
        """Leave the open state for an end once; return whether this call ended it."""
        with self._lock:
            if self._state is not _State.OPEN:
                return False
            self._state = state
            return True

    def _peer(self, closed: WebSocketClosedError) -> WebSocketClosedError | ProtocolStateError:
        """Return what a closed connection means: this session's own close, or the server's closure, kept."""
        if self._state is _State.CLOSED:
            return self._state_error("receive")
        self._closed = (closed.code, closed.reason or "", closed.clean)
        return self._close_error()

    def _payload(self, value: object) -> tuple[bytes, bool]:
        """Return a value's message bytes and whether they go as text, refusing a value the codec cannot carry."""
        plan = self._plan
        try:
            data = _encoded(plan, value)
        except (*_DATA_ERRORS, ValueError, TypeError) as error:
            raise self._stamped(
                DecodeError(reason="unencodable", direction="request", location=("message",), cause=error)
            ) from None
        return data, plan.send_frame == "text"

    def _message(self, frame: WSFrame) -> Message[RecvT]:
        """Return a received frame decoded as the declared message, raising StreamDecodeError for one that is not."""
        self._received += 1
        plan = self._plan
        data = frame.data
        kind: Literal["text", "binary"] = "text" if frame.text else "binary"
        if kind != plan.receive_frame:
            raise self._decode_error(data, "type")
        value: object = data
        match plan.receive_codec:
            case "json":
                assert plan.decoder is not None
                try:
                    wire = decode_json(data)
                except _DATA_ERRORS as error:
                    raise self._decode_error(data, "malformed", error) from None
                try:
                    value = plan.decoder.convert(wire)
                except _DATA_ERRORS as error:
                    raise self._decode_error(data, "value", error) from None
            case "utf8":
                try:
                    value = data.decode("utf-8")
                except UnicodeDecodeError:
                    raise self._decode_error(data, "malformed") from None
            case _:
                pass
        return Message(data=cast("RecvT", value), frame=kind, sequence=self._received, raw=data)

    def _decode_error(
        self, data: bytes, condition: Literal["type", "value", "malformed"], cause: BaseException | None = None
    ) -> StreamDecodeError:
        """Return the decode failure of a message, keeping at most the message limit or 64 KiB of its bytes."""
        limit = self._prefix
        return self._stamped(
            StreamDecodeError(
                sequence=self._received,
                raw_prefix=data[:limit],
                truncated=len(data) > limit,
                condition=condition,
                cause=cause,
            )
        )

    @staticmethod
    def _close_code(error: BaseException) -> int | None:
        """Return the code a failure closes the connection with, or None to drop the connection."""
        if isinstance(error, StreamDecodeError):
            return _PROTOCOL_ERROR
        if is_client_closed(error) or (is_phase_timeout(error) and error.phase == "read"):
            return _GOING_AWAY
        return None

    def _phase_timeout(
        self, timeout: float | None, phase: Literal["read", "write"], delivery: DeliveryState
    ) -> BaseException:
        """Return what stops the call when its deadline passed, else the expiry of a phase's own timeout."""
        if (halt := self._halted()) is not None:
            return halt
        assert timeout is not None
        return self._stamped(
            APITimeoutError(
                reason="phase_timeout",
                effective_timeout=timeout,
                phase=phase,
                delivery_state=delivery,
                retry_stop_reason="transport_not_retryable",
            )
        )

    def _unsent(self) -> BaseException:
        """Return the failure of a send that sent nothing before the send timeout or the deadline."""
        return self._phase_timeout(self._socket.send_timeout, "write", DeliveryState.NOT_SENT)

    def _idle(self) -> BaseException:
        """Return the failure of a receive that waited longer than the idle timeout or the deadline."""
        return self._phase_timeout(self._socket.idle_timeout, "read", DeliveryState.RESPONSE_STARTED)

    def _unanswered(self) -> BaseException:
        """Return the failure of a ping whose pong did not arrive before the pong timeout or the deadline."""
        return self._phase_timeout(self._socket.pong_timeout, "read", DeliveryState.RESPONSE_STARTED)

    def _undelivered(self, error: BaseException, cap: Deadline | None = None) -> BaseException:
        """Return how a send in progress ended: a message that may have gone is undelivered and never sent again.

        Only a connection's proof that nothing was sent keeps its failure, and a native interruption stays itself; a
        stop at the send's own cap is the cause of the unknown delivery.
        """
        if is_transport(error) and error.delivery_state is DeliveryState.NOT_SENT:
            return self._stamped(error)
        if not isinstance(error, Exception):
            return error
        cause = self._unsent() if self._capped(error, cap) else error
        return self._stamped(DeliveryUnknownError(delivery_state=DeliveryState.MAYBE_SENT, cause=cause))

    def _deadline(self, cap: float | None) -> Deadline | None:
        """Return the earlier of the session's deadline and a cap counted from now, both on the client's clock."""
        deadline = self._call.deadline
        if cap is None:
            return deadline
        capped = Deadline.after(cap, clock=self._call.settings.clock)
        return capped if deadline is None or capped.at < deadline.at else deadline

    def _capped(self, error: BaseException, cap: Deadline | None) -> bool:
        """Return whether a stop is the expiry of an operation's own cap rather than of the session's deadline."""
        deadline = self._call.deadline
        return is_deadline(error) and cap is not None and (deadline is None or cap.at < deadline.at)

    def _checked_close(self, code: object, reason: object) -> None:
        """Refuse a close code a client may not send, or a reason that is not at most 123 bytes of strict UTF-8."""
        if type(code) is not int or (code not in {_NORMAL, _GOING_AWAY} and code not in _APPLICATION_CODES):
            raise self._stamped(ConfigurationError(field_path=("code",), reason="invalid_value"))
        if (encoded := _utf8(reason)) is None or len(encoded) > MAX_CLOSE_REASON:
            raise self._stamped(ConfigurationError(field_path=("reason",), reason="invalid_value"))

    def _checked_ping(self, payload: object) -> None:
        if not isinstance(payload, bytes) or len(payload) > _MAX_PING:
            raise self._stamped(ConfigurationError(field_path=("payload",), reason="invalid_value"))


def _utf8(value: object) -> bytes | None:
    """Return a string as strict UTF-8, or None for another value or a string with a lone surrogate."""
    if not isinstance(value, str):
        return None
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _slice(left: float | None, polled: bool) -> float | None:  # noqa: FBT001
    """Return how long to wait next: the time left, and at most the token interval when polling."""
    if polled and (left is None or left > 0.05):  # noqa: PLR2004
        return 0.05
    return left


def _left(deadline: Deadline | None, end: float | None) -> float | None:
    """Return the time a wait has left: until its deadline's clock expires it, or real time reaches the wait's end."""
    return None if deadline is None or end is None else wait_left(deadline.remaining(), end)


def _end(deadline: Deadline | None) -> float | None:
    """Return the real time by which a wait for a deadline ends, as the deadline's clock measures it now."""
    return None if deadline is None else real_end(deadline.remaining())


class _Queue:
    """Admit a synchronous session's sends one at a time, in the order they arrived.

    Each send takes a ticket; a send that gives up leaves its ticket behind, which the turn then skips.
    """

    __slots__ = ("_condition", "_gone", "_next", "_serving")

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._gone: set[int] = set()
        self._next = 0
        self._serving = 0

    def acquire(self, deadline: Deadline | None, check: Callable[[], None] | None) -> bool:
        """Wait for the turn until the deadline, running the check at each token interval; False once it passed."""
        end = _end(deadline)
        with self._condition:
            ticket = self._next
            self._next += 1
            try:
                while ticket != self._serving:
                    wait = _slice(_left(deadline, end), check is not None)
                    if check is not None:
                        check()
                    if wait is not None and not wait > 0:
                        self._gone.add(ticket)
                        return False
                    self._condition.wait(wait)
            except BaseException:
                self._gone.add(ticket)
                raise
            return True

    def release(self) -> None:
        """Give the turn to the next ticket that still waits."""
        with self._condition:
            self._serving += 1
            while self._serving in self._gone:
                self._gone.remove(self._serving)
                self._serving += 1
            self._condition.notify_all()


@final
class WebSocketSession(_Sockets[SendT, RecvT]):
    """A synchronous WebSocket session: send typed messages, and receive them or iterate over them.

    The session owns its connection until it closes or fails; closing the client closes it too. One receive runs at a
    time, beside sends that go one at a time in arrival order. A closure by the server ends iteration when it was
    normal; `receive` raises WebSocketClosedError either way. After a failure or `close()` every step raises
    ProtocolStateError, and iteration after `close()` stops.
    """

    __slots__ = ("_connection", "_queue", "_response")

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        plan: ChannelPlan[SendT, RecvT],
        limits: _Limits,
        session: OperationSession,
        response: RawResponse,
        call: LogicalCallContext,
        opened: _Upgraded,
    ) -> None:
        """Take the handed-over handshake and its connection."""
        super().__init__(plan, limits, session, response.info, call, opened)
        self._response = response
        self._connection = opened.connection
        self._queue = _Queue()

    def _check(self) -> Callable[[], None] | None:
        """Return the call's check when a cancel token needs polling while waiting, or None."""
        return self._checked

    def send(self, value: SendT) -> None:
        """Send one message after the earlier sends, within the send timeout; a message that may have gone is final."""
        data, text = self._payload(value)
        self._usable("send")
        cap = self._deadline(self._socket.send_timeout)
        if not self._queue.acquire(cap, self._check()):
            raise self._unsent()
        try:
            self._usable("send")
            self._checked()
            self._connection.send(data, text=text, deadline=cap)
        except TimeoutError:
            raise self._unsent() from None
        except WebSocketClosedError as closed:
            raise self._ended(closed) from None
        except BaseException as error:
            if isinstance(error, (ProtocolStateError)) or is_deadline(error) or is_client_closed(error):
                raise
            raise self._failed(self._undelivered(error)) from None
        finally:
            self._queue.release()
        self._sent += 1

    def receive(self) -> Message[RecvT]:
        """Return the next message, waiting at most the idle timeout for it."""
        self._enter_receive()
        try:
            return self._receive()
        finally:
            self._receiving.release()

    def _receive(self) -> Message[RecvT]:
        try:
            frame = self._frame(self._deadline(self._socket.idle_timeout))
        except TimeoutError:
            raise self._failed(self._idle()) from None
        except WebSocketClosedError as closed:
            raise self._ended(closed) from None
        except BaseException as error:  # noqa: BLE001
            raise self._failed(self._own(error)) from None
        try:
            return self._message(frame)
        except StreamDecodeError as error:
            del frame
            raise self._failed(error.with_traceback(None)) from None

    def _frame(self, deadline: Deadline | None) -> WSFrame:
        """Wait once with the remaining absolute socket deadline."""
        self._checked()
        left = _left(deadline, _end(deadline))
        return self._connection.receive(deadline=None if left is None else Deadline.after(max(0.0, left)))

    def ping(self, payload: bytes = b"") -> PingReceipt:
        """Send a ping, beside any send, and wait at most the pong timeout for its pong."""
        self._checked_ping(payload)
        self._usable("ping")
        try:
            self._checked()
            latency = self._connection.ping(
                payload, deadline=self._deadline(self._socket.pong_timeout), check=self._check()
            )
        except TimeoutError:
            raise self._failed(self._unanswered()) from None
        except WebSocketClosedError as closed:
            raise self._ended(closed) from None
        except ProtocolStateError as refused:
            raise self._stamped(refused) from None
        except BaseException as error:  # noqa: BLE001
            raise self._failed(self._own(error)) from None
        return PingReceipt(latency=latency)

    def _failed(self, error: BaseException) -> BaseException:
        """End the session with a failure, closing the connection with the failure's code or dropping it."""
        if self._ending(_State.FAILED):
            self._opened.code = self._close_code(error)
            finished(self._response, error)
        return error

    def _ended(self, closed: WebSocketClosedError) -> BaseException:
        """End the session at a closed connection, unless the call stopped: cancelled, its client closing, or expired.

        A synchronous wait has no guard, so a connection the client's closing closed reports that first. A closure
        that was not normal ends the session as failed.
        """
        if (halt := self._halted()) is not None:
            return self._failed(halt)
        error = self._peer(closed)
        if isinstance(error, WebSocketClosedError) and self._ending(_State.ENDED):
            finished(self._response, None if error.clean else error)
        return error

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Close with a code and a reason, waiting at most the close timeout; closing again does nothing."""
        self._checked_close(code, reason)
        if self._ending(_State.CLOSED):
            opened = self._opened
            opened.code, opened.reason = code, reason
            self._response.close()

    def __iter__(self) -> Self:
        """Iterate over the messages."""
        return self

    def __next__(self) -> Message[RecvT]:
        """Return the next message; a normal closure, the server's or this session's own, ends the iteration."""
        if self._closed_here():
            raise StopIteration
        try:
            return self.receive()
        except WebSocketClosedError as closed:
            if closed.clean:
                raise StopIteration from None
            raise
        except ProtocolStateError:
            if self._closed_here():
                raise StopIteration from None
            raise

    def __enter__(self) -> Self:
        """Return this session, which leaving the block closes."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the session."""
        self.close()


@final
class AsyncWebSocketSession(_Sockets[SendT, RecvT]):
    """An asyncio WebSocket session, with the synchronous session's contract.

    Each wait runs under the call's guard in a lane of its own, so the cancel token, the client closing, and the
    deadline stop only the waiting task. A receive or send they stop drops the connection, since a frame may be half
    read or written; cancelling a receive's task leaves the session usable, as the library reads whole frames.
    """

    __slots__ = ("_connection", "_queue", "_response")

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        plan: ChannelPlan[SendT, RecvT],
        limits: _Limits,
        session: OperationSession,
        response: AsyncRawResponse,
        call: LogicalCallContext,
        opened: _AsyncUpgraded,
    ) -> None:
        """Take the handed-over handshake and its connection."""
        import asyncio  # noqa: PLC0415

        super().__init__(plan, limits, session, response.info, call, opened)
        self._response = response
        self._connection = opened.connection
        self._queue = asyncio.Lock()

    async def send(self, value: SendT) -> None:
        """Send one message after the earlier sends, within the send timeout; a message that may have gone is final."""
        data, text = self._payload(value)
        self._usable("send")
        cap = self._deadline(self._socket.send_timeout)
        lane = self._call.lane(cap)
        try:
            await lane.bounded(self._queue.acquire, phase="stream", idle=False, cleanup=self._release_turn)
        except BaseException as error:  # noqa: BLE001
            raise (self._unsent() if self._capped(error, cap) else self._own(error)) from None
        try:
            self._usable("send")
            await lane.bounded(
                lambda: self._connection.send(data, text=text, deadline=cap),
                phase="stream",
                delivery_state=DeliveryState.MAYBE_SENT,
                idle=False,
            )
        except TimeoutError:
            raise self._unsent() from None
        except WebSocketClosedError as closed:
            raise await self._end(self._peer(closed)) from None
        except ProtocolStateError:
            raise
        except BaseException as error:  # noqa: BLE001
            raise await self._failed(self._undelivered(error, cap)) from None
        finally:
            self._queue.release()
        self._sent += 1

    async def _release_turn(self, acquired: object) -> None:
        """Give back the send turn a send got just as the call stopped it, so later sends can take it."""
        del acquired
        self._queue.release()

    async def receive(self) -> Message[RecvT]:
        """Return the next message, waiting at most the idle timeout for it."""
        self._enter_receive()
        try:
            return await self._receive()
        finally:
            self._receiving.release()

    async def _receive(self) -> Message[RecvT]:
        idle = self._socket.idle_timeout
        deadline = self._deadline(idle)
        lane = self._call.lane(self._call.deadline)
        try:
            frame = await lane.bounded(
                lambda: self._connection.receive(deadline=deadline),
                phase="stream",
                delivery_state=DeliveryState.RESPONSE_STARTED,
                idle_timeout=idle,
                idle=idle is not None,
            )
        except TimeoutError:
            raise await self._failed(self._idle()) from None
        except WebSocketClosedError as closed:
            raise await self._end(self._peer(closed)) from None
        except BaseException as error:
            if not isinstance(error, Exception):
                raise
            raise await self._failed(self._own(error)) from None
        try:
            return self._message(frame)
        except StreamDecodeError as error:
            del frame
            raise await self._failed(error.with_traceback(None)) from None

    async def ping(self, payload: bytes = b"") -> PingReceipt:
        """Send a ping, beside any send, and wait at most the pong timeout for its pong."""
        self._checked_ping(payload)
        self._usable("ping")
        cap = self._deadline(self._socket.pong_timeout)
        try:
            latency = await self._call.lane(cap).bounded(
                lambda: self._connection.ping(payload, deadline=cap), phase="stream", idle=False
            )
        except TimeoutError:
            raise await self._failed(self._unanswered()) from None
        except WebSocketClosedError as closed:
            raise await self._end(self._peer(closed)) from None
        except ProtocolStateError as refused:
            raise self._stamped(refused) from None
        except BaseException as error:  # noqa: BLE001
            stopped = self._unanswered() if self._capped(error, cap) else self._own(error)
            raise await self._failed(stopped) from None
        return PingReceipt(latency=latency)

    async def _failed(self, error: BaseException) -> BaseException:
        """End the session with a failure, closing the connection with the failure's code or dropping it."""
        if self._ending(_State.FAILED):
            self._opened.code = self._close_code(error)
            await afinished(self._response, error)
        return error

    async def _end(self, error: WebSocketClosedError | ProtocolStateError) -> BaseException:
        """End the session at the server's closure; a closure that was not normal ends it as failed."""
        if isinstance(error, WebSocketClosedError) and self._ending(_State.ENDED):
            await afinished(self._response, None if error.clean else error)
        return error

    async def aclose(self, code: int = 1000, reason: str = "") -> None:
        """Close with a code and a reason, waiting at most the close timeout; closing again does nothing."""
        self._checked_close(code, reason)
        if self._ending(_State.CLOSED):
            opened = self._opened
            opened.code, opened.reason = code, reason
            await self._response.aclose()

    def __aiter__(self) -> Self:
        """Iterate over the messages."""
        return self

    async def __anext__(self) -> Message[RecvT]:
        """Return the next message; a normal closure, the server's or this session's own, ends the iteration."""
        if self._closed_here():
            raise StopAsyncIteration
        try:
            return await self.receive()
        except WebSocketClosedError as closed:
            if closed.clean:
                raise StopAsyncIteration from None
            raise
        except ProtocolStateError:
            if self._closed_here():
                raise StopAsyncIteration from None
            raise

    async def __aenter__(self) -> Self:
        """Return this session, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the session."""
        await self.aclose()


def _connector(core: ClientCore | AsyncClientCore) -> object:
    """Return the connector the client was given, or None."""
    protocols = core.protocol_options()
    connector = None if protocols is None else protocols.websocket_connector
    return None if isinstance(connector, Unset) else connector


def connect_socket(  # noqa: PLR0913
    core: ClientCore,
    plan: ChannelPlan[SendT, RecvT],
    arguments: tuple[object, ...],
    *,
    ws_options: object = None,
    options: object = None,
    session_options: object = None,
) -> WebSocketSession[SendT, RecvT]:
    """Open a helper's WebSocket in a session of its own, returning once its handshake got a valid 101."""
    limits = _limits(core, plan, ws_options, options, session_options)
    session = _session(limits)
    injected = _connector(core)
    connector = cast("WebSocketConnector", core.owned_connector(plan.connectors[0]) if injected is None else injected)
    adapter = _Handshake(plan, limits, connector)
    response, call = core.open_socket(
        plan.call,
        arguments,
        adapter.open,
        options=limits.options,
        session=session,
        open_timeout=limits.socket.open_timeout,
        check=_checked_headers,
    )
    assert adapter.opened is not None
    return WebSocketSession(plan, limits, session, response, call, adapter.opened)


async def aconnect_socket(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: ChannelPlan[SendT, RecvT],
    arguments: tuple[object, ...],
    *,
    ws_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncWebSocketSession[SendT, RecvT]:
    """Open a helper's WebSocket with asyncio, returning once its handshake got a valid 101."""
    limits = _limits(core, plan, ws_options, options, session_options)
    session = _session(limits)
    injected = _connector(core)
    connector = cast(
        "AsyncWebSocketConnector", core.owned_connector(plan.connectors[1]) if injected is None else injected
    )
    adapter = _AsyncHandshake(plan, limits, connector)
    response, call = await core.open_socket(
        plan.call,
        arguments,
        adapter.open,
        options=limits.options,
        session=session,
        open_timeout=limits.socket.open_timeout,
        check=_checked_headers,
    )
    assert adapter.opened is not None
    return AsyncWebSocketSession(plan, limits, session, response, call, adapter.opened)
