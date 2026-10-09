"""WebSocket helpers: the handshake as one child call of a session of their own, and typed sessions over HTTPX2's.

A helper sends its channel's GET with the upgrade headers through the client's call path and HTTP client, so
authentication, limiters, hooks, timeouts, and the HTTP client's transport, proxy, and TLS settings apply to the
handshake as to any call; a handshake is never redirected, and only one proven unsent is retried. The 101's connection
then carries an HTTPX2 WebSocket session, through which the typed session sends and receives whole messages.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, Literal, cast, final

import anyio
from httpx2.websockets import AsyncWebSocketSession as _AsyncNative
from httpx2.websockets import WebSocketDisconnect
from httpx2.websockets import WebSocketSession as _Native
from typing_extensions import Self, TypeVar
from wsproto.connection import ConnectionState
from wsproto.events import TextMessage
from wsproto.utilities import LocalProtocolError

from ..client.errors import (
    APIConnectionError,
    APITimeoutError,
    AuthError,
    ConfigurationError,
    DecodeError,
    DeliveryState,
    ProtocolError,
    ProtocolSizeError,
    SDKError,
    is_client_closed,
    is_phase_timeout,
)
from ..client.operations import request_errors
from ..client.options import RequestOptions
from ..client.raw import afinished, finished
from ..client.timing import SYSTEM_CLOCK, Budget, Clock, SessionOptions
from ..model_codecs.unset import UNSET, Unset
from .errors import (
    MAX_CLOSE_REASON,
    MAX_RAW_PREFIX,
    ConcurrentReceiveError,
    DeliveryUnknownError,
    ProtocolStateError,
    StreamDecodeError,
    WebSocketClosedError,
    WebSocketHandshakeError,
)
from .options import WSOptions

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from types import TracebackType

    import httpx2
    from wsproto.events import BytesMessage, Event

    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import InboundModelCodec, OperationPlan, OutboundModelCodec
    from ..client.raw import AsyncRawResponse, RawResponse
    from ..client.responses import HeadersView, ResponseInfo
    from .client import AsyncClientCore, ClientCore
    from .records import ProtocolProgress
    from .references import OperationRef

__all__ = (
    "AsyncWebSocketSession",
    "ChannelPlan",
    "Message",
    "PingReceipt",
    "WebSocketSession",
    "aconnect_socket",
    "connect_socket",
)

SendT = TypeVar("SendT")
RecvT = TypeVar("RecvT")
T_co = TypeVar("T_co", covariant=True, default=object)
V = TypeVar("V")
ErrorT = TypeVar("ErrorT", bound=SDKError)

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
_NORMAL: Final = 1000
_GOING_AWAY: Final = 1001
_PROTOCOL_ERROR: Final = 1002
_INTERNAL_ERROR: Final = 1011
_CLEAN: Final = frozenset({_NORMAL, _GOING_AWAY})
_APPLICATION_CODES: Final = range(3000, 5000)
_MAX_PING: Final = 125


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelPlan(Generic[SendT, RecvT]):
    """Everything fixed about one generated WebSocket helper: its identity, handshake call, and messages.

    A message's codec is `json`, decoded or encoded by the message's schema, `utf8` text, or `bytes`; its frame is the
    kind every message of that direction uses.
    """

    helper_id: str
    operation: OperationRef
    call: OperationPlan[object]
    fingerprint: str
    send_codec: Literal["json", "utf8", "bytes"] = "json"
    send_frame: Literal["text", "binary"] = "text"
    encoder: OutboundModelCodec | None = None
    receive_codec: Literal["json", "utf8", "bytes"] = "json"
    receive_frame: Literal["text", "binary"] = "text"
    decoder: InboundModelCodec[RecvT] | None = None
    subprotocols: tuple[str, ...] = ()


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


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective WebSocket limits, session limits, and call options of one connect."""

    open_timeout: float | None
    idle_timeout: float | None
    max_message_bytes: int
    ping_interval: float | None
    pong_timeout: float | None
    total_timeout: float | None = None
    options: RequestOptions | None = None
    clock: Clock = SYSTEM_CLOCK


def _first(layers: tuple[object, ...], name: str, default: V) -> V:
    """Return a limit from the first options layer that sets it, or its default."""
    for layer in layers:
        if layer is not None and not isinstance(layer, Unset) and not isinstance(value := getattr(layer, name), Unset):
            return cast("V", value)
    return default


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: ChannelPlan[SendT, RecvT],
    ws_options: object,
    options: object,
    session_options: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    An idle timeout neither layer sets is the call's merged native read timeout.
    """
    for name, value, kind in (
        ("ws_options", ws_options, WSOptions),
        ("options", options, RequestOptions),
        ("session_options", session_options, SessionOptions),
    ):
        if value is not None and not isinstance(value, kind):
            raise ConfigurationError(
                field_path=(name,), reason="invalid_value", helper_id=plan.helper_id, operation=plan.operation
            )
    request = options if isinstance(options, RequestOptions) else None
    defaults = core.protocol_defaults(plan.helper_id)
    kinds = (ws_options, UNSET if defaults is None else defaults.options)
    sessions = (session_options, UNSET if defaults is None else defaults.session)
    return _Limits(
        open_timeout=_first(kinds, "open_timeout", 5.0),
        idle_timeout=_first(kinds, "idle_timeout", core.call_settings(request, plan.call).timeout.read),
        max_message_bytes=_first(kinds, "max_message_bytes", 1048576),
        ping_interval=_first(kinds, "ping_interval", 20.0),
        pong_timeout=_first(kinds, "pong_timeout", 20.0),
        total_timeout=_first(sessions, "total_timeout", None),
        options=request,
        clock=core.clock,
    )


def _session(limits: _Limits) -> OperationSession:
    """Start the socket's session."""
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only a connect loads the call runtime.

    return OperationSession(total_timeout=limits.total_timeout, clock=limits.clock)


def _checked_headers(headers: HeadersView) -> None:
    """Refuse a header the WebSocket handshake itself writes, before anything is sent."""
    for name in dict(headers.items()):
        if name.lower() in _MANAGED:
            raise ConfigurationError(field_path=("headers", name), reason="managed")


def _upgrade(subprotocols: tuple[str, ...]) -> Mapping[str, str]:
    """Return the upgrade headers of one handshake, with a fresh key and the offered subprotocols in order."""
    headers = {
        "Connection": "Upgrade",
        "Upgrade": "websocket",
        "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode("ascii"),
        "Sec-WebSocket-Version": "13",
    }
    if subprotocols:
        headers["Sec-WebSocket-Protocol"] = ", ".join(subprotocols)
    return headers


def _negotiated(offered: tuple[str, ...], info: ResponseInfo) -> None:
    """Refuse a 101 that selected no subprotocol the helper offered, or selected one it did not offer."""
    selected = info.headers.get("sec-websocket-protocol")
    if (selected is None and offered) or (selected is not None and selected not in offered):
        raise WebSocketHandshakeError(condition="negotiation", delivery_state=DeliveryState.RESPONSE_STARTED)


def _encoded(plan: ChannelPlan[SendT, RecvT], value: object) -> str | bytes:
    """Return a value as the plan's sent message: text for a text frame, else bytes."""
    data: str | bytes
    match plan.send_codec:
        case "json":
            assert plan.encoder is not None
            data = plan.encoder.encode(value)
        case "utf8" if isinstance(value, str):
            data = value
        case "bytes" if isinstance(value, (bytes, bytearray)):
            data = bytes(value)
        case _:
            msg = f"A {plan.send_codec} message cannot carry a {type(value).__name__}"
            raise TypeError(msg)
    if plan.send_frame == "text" and isinstance(data, bytes):
        return data.decode("utf-8")
    return data


def _utf8(value: object) -> bytes | None:
    """Return a string as strict UTF-8, or None for another value or a string with a lone surrogate."""
    if not isinstance(value, str):
        return None
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _reason(value: str) -> str:
    """Return a received close reason cut to at most 123 UTF-8 bytes."""
    return value.encode("utf-8")[:MAX_CLOSE_REASON].decode("utf-8", "ignore")


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
        "_limits",
        "_lock",
        "_plan",
        "_prefix",
        "_received",
        "_receiving",
        "_sent",
        "_session",
        "_state",
    )

    def __init__(
        self,
        plan: ChannelPlan[SendT, RecvT],
        limits: _Limits,
        session: OperationSession,
        info: ResponseInfo,
        call: LogicalCallContext,
    ) -> None:
        """Start an open session on the connection a 101 handed over."""
        self._plan = plan
        self._limits = limits
        self._session = session
        self._info = info
        self._call = call
        self._prefix = min(limits.max_message_bytes, MAX_RAW_PREFIX)
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
            f"subprotocol={self.subprotocol!r})"
        )

    @property
    def response(self) -> ResponseInfo:
        """Return the metadata of the handshake's 101 response."""
        return self._info

    @property
    def subprotocol(self) -> str | None:
        """Return the subprotocol the server selected, or None."""
        return self._info.headers.get("sec-websocket-protocol")

    @property
    def progress(self) -> ProtocolProgress:
        """Return the messages sent and received so far."""
        return MappingProxyType({"messages_sent": self._sent, "messages_received": self._received})

    def _stamped(self, error: ErrorT) -> ErrorT:
        """Give a failure of the session the helper's context, the handshake call's identity, and its 101.

        A failure keeps its own delivery evidence; one without any, NOT_SENT, takes how far the handshake call got,
        unless it is a transport or auth failure, whose NOT_SENT is its proof.
        """
        if isinstance(error, ProtocolError):
            error.helper_id = self._plan.helper_id
            error.operation = self._plan.operation
        state = error.delivery_state
        failure = self._call.snapshot_error(error)
        if state is not DeliveryState.NOT_SENT or isinstance(error, (APIConnectionError, AuthError)):
            failure.delivery_state = state
        failure.info = self._info
        return failure

    def _lost(self, error: BaseException) -> APIConnectionError:
        """Return a failure of the HTTPX2 session, such as its network error, as the session's lost connection."""
        return self._stamped(
            APIConnectionError(phase="read", delivery_state=DeliveryState.RESPONSE_STARTED, cause=error)
        )

    def _halted(self) -> Exception | None:
        """Return the session deadline's failure once it passed, or None."""
        return self._call.expired("stream", DeliveryState.RESPONSE_STARTED)

    def _checked(self) -> None:
        self._call.check("stream", DeliveryState.RESPONSE_STARTED)

    def _state_error(self, action: str) -> ProtocolStateError:
        return self._stamped(ProtocolStateError(state=self._state.value, action=action))

    def _close_error(self) -> WebSocketClosedError:
        code, reason, clean = self._closed
        return self._stamped(WebSocketClosedError(code=code, reason=reason, clean=clean))

    def _closing(self) -> WebSocketClosedError:
        """Return the refusal of a send or ping on a connection already closing, before the session read its end."""
        return self._stamped(WebSocketClosedError(code=None, reason="", clean=False))

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

    def _peer(self, closed: WebSocketDisconnect, connection: ConnectionState) -> SDKError:
        """Return what a received closure means: a message over the limit, or the server's closure.

        HTTPX2 closes with 1009 itself before reporting a message over the limit, so the closure is this end's own then.
        """
        if connection is ConnectionState.LOCAL_CLOSING:
            limit = self._limits.max_message_bytes
            return self._stamped(
                ProtocolSizeError(kind="message", limit=limit, observed=limit + 1, unit="bytes", cause=closed)
            )
        code = int(closed.code)
        self._closed = (code, _reason(closed.reason), code in _CLEAN)
        return self._close_error()

    def _payload(self, value: object) -> str | bytes:
        """Return a value's message, refusing a value the codec cannot carry."""
        plan = self._plan
        try:
            return _encoded(plan, value)
        except request_errors(plan.encoder) as error:
            raise self._stamped(
                DecodeError(reason="unencodable", direction="request", location=("message",), cause=error)
            ) from None

    def _message(self, event: Event) -> Message[RecvT]:
        """Return a received message decoded as declared, raising StreamDecodeError for one that is not."""
        self._received += 1
        plan = self._plan
        text = isinstance(event, TextMessage)
        data = event.data.encode("utf-8") if isinstance(event, TextMessage) else bytes(cast("BytesMessage", event).data)
        kind: Literal["text", "binary"] = "text" if text else "binary"
        if kind != plan.receive_frame:
            raise self._decode_error(data, "type")
        value: object = data
        match plan.receive_codec:
            case "json":
                codec = plan.decoder
                assert codec is not None
                try:
                    value = codec.decode(data)
                except codec.errors as error:
                    condition: Literal["value", "malformed"] = "malformed" if codec.malformed(error) else "value"
                    raise self._decode_error(data, condition, error) from None
            case "utf8":
                value = cast("TextMessage", event).data
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
    def _close_code(error: BaseException) -> int:
        """Return the code a failure closes the connection with."""
        if isinstance(error, StreamDecodeError):
            return _PROTOCOL_ERROR
        if is_client_closed(error) or (is_phase_timeout(error) and error.phase == "read"):
            return _GOING_AWAY
        return _INTERNAL_ERROR

    def _phase_timeout(self, timeout: float | None) -> Exception:
        """Return what stops the call when its deadline passed, else the expiry of a read wait's own timeout.

        A wait without a timeout of its own ended at the deadline: by the client's clock, or by the real time it
        measured when it began when that clock lags behind.
        """
        if (halt := self._halted()) is not None:
            return halt
        if timeout is None and (deadline := self._call.deadline) is not None:
            return self._stamped(
                APITimeoutError(
                    reason="deadline_exceeded",
                    deadline_at=deadline.at,
                    phase="stream",
                    delivery_state=DeliveryState.RESPONSE_STARTED,
                )
            )
        return self._stamped(
            APITimeoutError(
                reason="phase_timeout",
                effective_timeout=timeout,
                phase="read",
                delivery_state=DeliveryState.RESPONSE_STARTED,
                retry_stop_reason="transport_not_retryable",
            )
        )

    def _ended_as(self, error: Exception) -> tuple[_State, int, str, BaseException | None]:
        """Return how a failure or a received closure ends the session: its state, close code and reason, and error.

        The server's closure is answered with its code and reason, and only one that was not normal is a failure.
        """
        if isinstance(error, WebSocketClosedError):
            return _State.ENDED, error.code or _NORMAL, error.reason, None if error.clean else error
        return _State.FAILED, self._close_code(error), "", error

    def _undelivered(self, error: Exception) -> DeliveryUnknownError:
        """Return how a send in progress ended: a message that may have gone is undelivered and never sent again."""
        return self._stamped(DeliveryUnknownError(delivery_state=DeliveryState.MAYBE_SENT, cause=error))

    def _wait(self, cap: float | None) -> float | None:
        """Return how long a native wait may block: the earlier of a cap and the session's deadline, never negative."""
        deadline = self._call.deadline
        if cap is not None:
            capped = Budget.after(cap, clock=self._call.settings.clock)
            deadline = capped if deadline is None or capped.at < deadline.at else deadline
        return None if deadline is None else max(0.0, deadline.remaining())

    def _checked_close(self, code: object, reason: object) -> None:
        """Refuse a close code a client may not send, or a reason that is not at most 123 bytes of strict UTF-8."""
        if type(code) is not int or (code not in _CLEAN and code not in _APPLICATION_CODES):
            raise self._stamped(ConfigurationError(field_path=("code",), reason="invalid_value"))
        if (encoded := _utf8(reason)) is None or len(encoded) > MAX_CLOSE_REASON:
            raise self._stamped(ConfigurationError(field_path=("reason",), reason="invalid_value"))

    def _checked_ping(self, payload: object) -> None:
        if not isinstance(payload, bytes) or len(payload) > _MAX_PING:
            raise self._stamped(ConfigurationError(field_path=("payload",), reason="invalid_value"))


@final
class WebSocketSession(_Sockets[SendT, RecvT]):
    """A synchronous WebSocket session: send typed messages, and receive them or iterate over them.

    The session owns its connection until it closes or fails; the connection belongs to the HTTP client's pool, which
    closes it with the client. One receive runs at a time, beside sends, which HTTPX2 writes one at a time. A closure by
    the server ends iteration when it was normal; `receive` raises WebSocketClosedError either way. After a failure or
    `close()` every step raises ProtocolStateError, and iteration after `close()` stops.
    """

    __slots__ = ("_native", "_response")

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        plan: ChannelPlan[SendT, RecvT],
        limits: _Limits,
        session: OperationSession,
        response: RawResponse,
        call: LogicalCallContext,
        upgraded: httpx2.Response,
    ) -> None:
        """Take the handed-over handshake and run an HTTPX2 session on its connection."""
        super().__init__(plan, limits, session, response.info, call)
        self._response = response
        self._native = _Native(
            upgraded.extensions["network_stream"],
            max_message_size_bytes=limits.max_message_bytes,
            queue_size=0,
            keepalive_ping_interval_seconds=limits.ping_interval,
            keepalive_ping_timeout_seconds=limits.pong_timeout,
            response=upgraded,
        ).__enter__()

    def _shut(self, code: int, reason: str = "") -> None:
        """Close the HTTPX2 session with a code and a reason and wait for its threads."""
        native = self._native
        native.close(code, reason)
        native.__exit__(None, None, None)

    def _end(self, error: Exception, action: str) -> Exception:
        """End the session at a failure or a received closure, or report the end another step reached first."""
        state, code, reason, failure = self._ended_as(error)
        if not self._ending(state):
            return self._state_error(action)
        self._shut(code, reason)
        finished(self._response, failure)
        return error

    def send(self, value: SendT) -> None:
        """Send one whole message; a message that may have gone is final."""
        data = self._payload(value)
        self._usable("send")
        self._checked()
        try:
            if isinstance(data, str):
                self._native.send_text(data)
            else:
                self._native.send_bytes(data)
        except Exception as error:  # noqa: BLE001
            closing = isinstance(error, LocalProtocolError)
            raise self._closing() if closing else self._end(self._undelivered(error), "send") from None
        self._sent += 1

    def receive(self) -> Message[RecvT]:
        """Return the next message, waiting at most the idle timeout for it."""
        self._enter_receive()
        try:
            return self._receive()
        finally:
            self._receiving.release()

    def _receive(self) -> Message[RecvT]:
        self._checked()
        native = self._native
        try:
            event = native.receive(self._wait(self._limits.idle_timeout))
        except TimeoutError:
            raise self._end(self._phase_timeout(self._limits.idle_timeout), "receive") from None
        except WebSocketDisconnect as closed:
            raise self._end(self._peer(closed, native.connection.state), "receive") from None
        except Exception as error:  # noqa: BLE001
            raise self._end(self._lost(error), "receive") from None
        try:
            return self._message(event)
        except StreamDecodeError as error:
            del event
            raise self._end(error.with_traceback(None), "receive") from None

    def ping(self, payload: bytes = b"") -> PingReceipt:
        """Send a ping, beside any send, and wait at most the pong timeout for its pong.

        An empty payload asks HTTPX2 for a random one, so pings sent at once never share one.
        """
        self._checked_ping(payload)
        self._usable("ping")
        self._checked()
        started = time.monotonic()
        try:
            pong = self._native.ping(payload)
        except Exception as error:  # noqa: BLE001
            closing = isinstance(error, LocalProtocolError)
            raise self._closing() if closing else self._end(self._lost(error), "ping") from None
        if not pong.wait(self._wait(timeout := self._limits.pong_timeout)):
            raise self._end(self._phase_timeout(timeout), "ping")
        return PingReceipt(latency=time.monotonic() - started)

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Close with a code and a reason; closing again does nothing."""
        self._checked_close(code, reason)
        if self._ending(_State.CLOSED):
            self._shut(code, reason)
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
    """An asyncio WebSocket session, with the synchronous session's contract, open for the block of its connect.

    The HTTPX2 session runs in the task that entered the block, since its task group must end there; tasks the block
    starts may use and close the session. Cancelling a receive's task leaves the session usable, as HTTPX2 queues whole
    messages; a cancelled send or ping fails the session, since a message may be half written.
    """

    __slots__ = ("_native", "_response")

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        plan: ChannelPlan[SendT, RecvT],
        limits: _Limits,
        session: OperationSession,
        response: AsyncRawResponse,
        call: LogicalCallContext,
        upgraded: httpx2.Response,
    ) -> None:
        """Take the handed-over handshake and prepare an HTTPX2 session on its connection."""
        super().__init__(plan, limits, session, response.info, call)
        self._response = response
        self._native = _AsyncNative(
            upgraded.extensions["network_stream"],
            max_message_size_bytes=limits.max_message_bytes,
            keepalive_ping_interval_seconds=limits.ping_interval,
            keepalive_ping_timeout_seconds=limits.pong_timeout,
            response=upgraded,
        )

    @asynccontextmanager
    async def _opened(self) -> AsyncGenerator[Self, None]:
        """Run the HTTPX2 session for one block in the caller's task, then close the session.

        The block's own failure leaves it as raised, not in the group HTTPX2's task group wraps it in; a failure of
        HTTPX2's background tasks, which cancels the block, leaves it as the session's lost connection.
        """
        escaped: BaseException | None = None
        try:
            async with self._native:
                try:
                    yield self
                except BaseException as error:
                    escaped = error
                    raise
        except Exception as exited:  # noqa: BLE001
            escaped = escaped if getattr(exited, "exceptions", None) == (escaped,) else self._lost(exited)
        finally:
            with anyio.CancelScope(shield=True):
                await self.aclose()
        if escaped is not None:
            raise escaped

    async def _shut(self, code: int, reason: str = "") -> None:
        """Close the HTTPX2 session with a code and a reason; leaving the block ends its tasks."""
        with anyio.CancelScope(shield=True):
            await self._native.close(code, reason)

    async def _end(self, error: Exception, action: str) -> Exception:
        """End the session at a failure or a received closure, or report the end another step reached first."""
        state, code, reason, failure = self._ended_as(error)
        if not self._ending(state):
            return self._state_error(action)
        await self._shut(code, reason)
        with anyio.CancelScope(shield=True):
            await afinished(self._response, failure)
        return error

    async def send(self, value: SendT) -> None:
        """Send one whole message; a message that may have gone is final."""
        data = self._payload(value)
        self._usable("send")
        self._checked()
        try:
            await (self._native.send_text(data) if isinstance(data, str) else self._native.send_bytes(data))
        except Exception as error:  # noqa: BLE001
            closing = isinstance(error, LocalProtocolError)
            raise self._closing() if closing else await self._end(self._undelivered(error), "send") from None
        except BaseException:
            await self._end(self._state_error("send"), "send")
            raise
        self._sent += 1

    async def receive(self) -> Message[RecvT]:
        """Return the next message, waiting at most the idle timeout for it."""
        self._enter_receive()
        try:
            return await self._receive()
        finally:
            self._receiving.release()

    async def _receive(self) -> Message[RecvT]:
        self._checked()
        native = self._native
        try:
            event = await native.receive(self._wait(self._limits.idle_timeout))
        except TimeoutError:
            raise await self._end(self._phase_timeout(self._limits.idle_timeout), "receive") from None
        except WebSocketDisconnect as closed:
            raise await self._end(self._peer(closed, native.connection.state), "receive") from None
        except Exception as error:  # noqa: BLE001
            raise await self._end(self._lost(error), "receive") from None
        try:
            return self._message(event)
        except StreamDecodeError as error:
            del event
            raise await self._end(error.with_traceback(None), "receive") from None

    async def ping(self, payload: bytes = b"") -> PingReceipt:
        """Send a ping, beside any send, and wait at most the pong timeout for its pong."""
        self._checked_ping(payload)
        self._usable("ping")
        self._checked()
        started = time.monotonic()
        try:
            pong = await self._native.ping(payload)
            with anyio.move_on_after(self._wait(timeout := self._limits.pong_timeout)) as waited:
                await pong.wait()
        except Exception as error:  # noqa: BLE001
            closing = isinstance(error, LocalProtocolError)
            raise self._closing() if closing else await self._end(self._lost(error), "ping") from None
        except BaseException:
            await self._end(self._state_error("ping"), "ping")
            raise
        if waited.cancelled_caught:
            raise await self._end(self._phase_timeout(timeout), "ping")
        return PingReceipt(latency=time.monotonic() - started)

    async def aclose(self, code: int = 1000, reason: str = "") -> None:
        """Close with a code and a reason; closing again does nothing."""
        self._checked_close(code, reason)
        if self._ending(_State.CLOSED):
            await self._shut(code, reason)
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
    response, call, upgraded = core.open_socket(
        plan.call,
        arguments,
        _upgrade(plan.subprotocols),
        options=limits.options,
        session=session,
        open_timeout=limits.open_timeout,
        check=_checked_headers,
        accept=partial(_negotiated, plan.subprotocols),
    )
    return WebSocketSession(plan, limits, session, response, call, upgraded)


@asynccontextmanager
async def aconnect_socket(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: ChannelPlan[SendT, RecvT],
    arguments: tuple[object, ...],
    *,
    ws_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncGenerator[AsyncWebSocketSession[SendT, RecvT], None]:
    """Open a helper's WebSocket with asyncio for one block, entered once its handshake got a valid 101.

    The session runs in the task that enters the block and closes when it leaves.
    """
    limits = _limits(core, plan, ws_options, options, session_options)
    session = _session(limits)
    response, call, upgraded = await core.open_socket(
        plan.call,
        arguments,
        _upgrade(plan.subprotocols),
        options=limits.options,
        session=session,
        open_timeout=limits.open_timeout,
        check=_checked_headers,
        accept=partial(_negotiated, plan.subprotocols),
    )
    opened = AsyncWebSocketSession(plan, limits, session, response, call, upgraded)
    async with opened._opened():  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        yield opened
