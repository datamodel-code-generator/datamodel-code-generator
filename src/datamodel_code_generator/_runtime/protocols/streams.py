"""Server-sent event streams: the WHATWG event-stream parser, typed events, and the handles that read them.

A stream helper opens its operation as one child call of a session of its own and hands the response to a handle, which
reads only the bytes the next event needs. The response's idle limit counts only while a step waits for bytes; between
steps only the stream's total limit and the session's deadline run.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, Literal, cast, final

from typing_extensions import Self, TypeVar

from ..client.errors import (
    BudgetExceededError,
    PhaseTimeoutError,
    ProtocolConfigurationError,
    ProtocolError,
    ProtocolSizeError,
    TransportError,
    set_error_counters,
)
from ..client.options import RequestOptions
from ..client.timing import SessionOptions
from ..model_codecs.errors import (
    CodecBindingError,
    CodecResourceLimitError,
    ModelProjectionError,
    NativeValidationError,
    ParameterEncodingError,
    WireValidationError,
)
from ..model_codecs.media import decode_json
from ..model_codecs.unset import UNSET, Unset
from .errors import (
    MAX_RAW_PREFIX,
    IncompleteFrameError,
    ProtocolStateError,
    SessionLimitError,
    StreamDecodeError,
    StreamInterruptedError,
    StreamRemoteError,
)
from .options import StreamOptions
from .values import MISSING, resolve

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Generator, Iterator, Mapping
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.codecs import NativeValue
    from ..client.logical import OperationSession
    from ..client.operations import OperationPlan
    from ..client.raw import AsyncRawResponse, RawResponse
    from ..client.responses import ResponseInfo
    from ..client.timing import Deadline
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .records import BodySelector, ProtocolProgress
    from .references import OperationRef

__all__ = (
    "AsyncEventStream",
    "EventPlan",
    "EventStream",
    "StreamEvent",
    "UnknownEvent",
    "aopen_events",
    "open_events",
    "unknown_event",
)

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")
T_co = TypeVar("T_co", covariant=True, default=object)
ProtocolErrorT = TypeVar("ProtocolErrorT", bound=ProtocolError)

_TERMINATOR: Final = re.compile(rb"[\r\n]")
_RETRY: Final = re.compile(rb"[0-9]{1,18}")
_BOM: Final = b"\xef\xbb\xbf"
_CR: Final = 0x0D
_LF: Final = 0x0A
_COLON: Final = 0x3A
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
class StreamEvent(Generic[T_co]):
    """One event of a stream: its decoded data, SSE type, last event ID, reconnection time, sequence, and raw data.

    The event ID is the last one the stream set, None when it set none or an empty one, and the reconnection time the
    last valid `retry` in milliseconds. Events are numbered from 1 in the order the stream dispatched them. The data,
    the event ID, and the raw data never appear in the representation.
    """

    data: T_co = field(repr=False)
    event_type: str
    event_id: str | None = field(repr=False)
    retry_ms: int | None
    sequence: int
    raw_data: str = field(repr=False)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownEvent:
    """An event whose discriminator the helper does not map, kept as its raw data under `unknown: raw`."""

    discriminator: str
    raw_data: str = field(repr=False)


def unknown_event(discriminator: str, raw_data: str) -> UnknownEvent:
    """Return the event of an unmapped discriminator."""
    return UnknownEvent(discriminator=discriminator, raw_data=raw_data)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EventPlan(Generic[T]):
    """Everything fixed about one generated SSE helper: its identity, call, media type, events, errors, and end.

    `event` decodes the data of a helper with one event schema; `routes` decode it by the discriminator the event's SSE
    type gives, or the string `discriminator` reads from its JSON data, and `unknown` keeps an unmapped one. `errors`
    decode the declared error events, by the same discriminator. A `sentinel` completion ends at the raw data
    `terminal` and an `event_type` one at that SSE type; neither terminal event is decoded or delivered.
    """

    helper_id: str
    operation: OperationRef
    call: OperationPlan[object, object]
    media: str
    fingerprint: str
    event: NativeValue[T] | None = None
    routes: tuple[tuple[str, NativeValue[T]], ...] = ()
    discriminator: BodySelector | None = None
    unknown: Callable[[str, str], T] | None = None
    errors: tuple[tuple[str, NativeValue[object]], ...] = ()
    completion: Literal["eof", "sentinel", "event_type"] = "eof"
    terminal: str | None = None


@dataclass(frozen=True, slots=True)
class _Frame:
    """A dispatched event: its joined data, its type, the last event ID, and the reconnection time."""

    data: str
    event_type: str
    event_id: str | None
    retry_ms: int | None


class _Parser:
    """Parse an event stream's bytes as the WHATWG event-stream interpretation does, one dispatched event at a time.

    A leading byte order mark is skipped, and CR, LF, and CRLF end lines, a CRLF split across chunks included. A line
    over the line limit and data over the event limit, counted in bytes before they are kept, raise ProtocolSizeError.
    Invalid UTF-8 decodes to replacement characters. A `retry` of more than 18 digits is ignored.
    """

    __slots__ = (
        "_buffer",
        "_data",
        "_data_bytes",
        "_event_id",
        "_event_type",
        "_fields",
        "_frame_bytes",
        "_lf",
        "_retry",
        "_started",
        "max_event",
        "max_line",
    )

    def __init__(self, max_line: int, max_event: int) -> None:
        """Start before the first byte, with empty buffers."""
        self.max_line = max_line
        self.max_event = max_event
        self._buffer = bytearray()
        self._started = False
        self._lf = False
        self._data: list[str] = []
        self._data_bytes = 0
        self._event_type = ""
        self._event_id = ""
        self._retry: int | None = None
        self._frame_bytes = 0
        self._fields = False

    def feed(self, chunk: bytes) -> None:
        """Keep the bytes of the next chunk."""
        self._buffer += chunk

    def next(self) -> _Frame | None:
        """Return the next dispatched event from the bytes kept, or None when the next one needs more bytes.

        The LF of a CRLF counts toward the bytes of the frame its line belongs to, none after a frame's blank line.
        """
        buffer = self._buffer
        if not self._started:
            if len(buffer) < len(_BOM) and _BOM.startswith(buffer):
                return None
            if buffer.startswith(_BOM):
                del buffer[: len(_BOM)]
            self._started = True
        position = 0
        try:
            while True:
                if self._lf and position < len(buffer):
                    self._lf = False
                    if buffer[position] == _LF:
                        position += 1
                        self._frame_bytes += self._frame_bytes > 0
                if (found := _TERMINATOR.search(buffer, position)) is None:
                    _limit("line", self.max_line, len(buffer) - position)
                    return None
                end = found.start()
                _limit("line", self.max_line, end - position)
                line = bytes(buffer[position:end])
                self._lf = buffer[end] == _CR
                position = end + 1
                self._frame_bytes += len(line) + 1
                if (frame := self._line(line)) is not None:
                    return frame
        finally:
            del buffer[:position]

    def incomplete(self) -> int | None:
        """Return the bytes of the frame an end of the stream cuts, or None when it ends between frames."""
        pending = len(self._buffer)
        return self._frame_bytes + pending if pending or self._fields else None

    def _line(self, line: bytes) -> _Frame | None:
        """Interpret one line: dispatch at a blank one, skip a comment, and keep a field's value."""
        if not line:
            return self._dispatch()
        if line[0] == _COLON:
            return None
        self._fields = True
        name, colon, value = line.partition(b":")
        if colon and value[:1] == b" ":
            value = value[1:]
        match name:
            case b"data":
                size = self._data_bytes + len(value) + (1 if self._data else 0)
                _limit("event", self.max_event, size)
                self._data.append(value.decode("utf-8", "replace"))
                self._data_bytes = size
            case b"event":
                self._event_type = value.decode("utf-8", "replace")
            case b"id" if b"\x00" not in value:
                self._event_id = value.decode("utf-8", "replace")
            case b"retry" if _RETRY.fullmatch(value):
                self._retry = int(value)
            case _:
                pass
        return None

    def _dispatch(self) -> _Frame | None:
        """End a frame, returning its event unless it has no data; its type and data are cleared either way."""
        data, self._data, self._data_bytes = self._data, [], 0
        event_type, self._event_type = self._event_type, ""
        self._frame_bytes = 0
        self._fields = False
        if not data:
            return None
        return _Frame("\n".join(data), event_type or "message", self._event_id or None, self._retry)


def _limit(kind: Literal["line", "event"], limit: int, observed: int) -> None:
    if observed > limit:
        raise ProtocolSizeError(kind=kind, limit=limit, observed=observed, unit="bytes")


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective limits of one stream, each from the first layer that sets it; None removes a limit."""

    idle_timeout: float | Unset | None = UNSET
    reconnect: bool = False
    max_line_bytes: int = 262144
    max_event_bytes: int = 1048576
    total_timeout: float | None = None
    deadline: Deadline | None = None
    max_network_sends: int | None = 16
    options: RequestOptions | None = None


_DEFAULTS: Final = _Limits()


def _first(layers: tuple[object, ...], name: str, default: V) -> V:
    """Return a limit from the first options layer that sets it, or its default."""
    for layer in layers:
        if layer is not None and not isinstance(layer, Unset) and not isinstance(value := getattr(layer, name), Unset):
            return cast("V", value)
    return default


def _invalid(
    plan: EventPlan[T], path: tuple[str, ...], condition: Literal["invalid_value", "missing_metadata"]
) -> ProtocolConfigurationError:
    return ProtocolConfigurationError(
        field_path=path, condition=condition, helper_id=plan.helper_id, operation=plan.operation
    )


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: EventPlan[T],
    stream_options: object,
    options: object,
    session_options: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    An idle timeout either layer sets replaces the call's merged stream idle timeout. Reconnecting needs resume
    metadata, which the helper does not declare, so a stream that would reconnect is refused.
    """
    for name, value, kind in (
        ("stream_options", stream_options, StreamOptions),
        ("options", options, RequestOptions),
        ("session_options", session_options, SessionOptions),
    ):
        if value is not None and not isinstance(value, kind):
            raise _invalid(plan, (name,), "invalid_value")
    defaults = core.protocol_defaults(plan.helper_id)
    kinds = (stream_options, UNSET if defaults is None else defaults.options)
    sessions = (session_options, UNSET if defaults is None else defaults.session)
    if _first(kinds, "reconnect", _DEFAULTS.reconnect):
        raise _invalid(plan, ("stream_options", "reconnect"), "missing_metadata")
    request = options if isinstance(options, RequestOptions) else None
    if not isinstance(idle := _first(kinds, "idle_timeout", _DEFAULTS.idle_timeout), Unset):
        request = replace(request or RequestOptions(), stream_idle_timeout=idle)
    return _Limits(
        max_line_bytes=_first(kinds, "max_line_bytes", _DEFAULTS.max_line_bytes),
        max_event_bytes=_first(kinds, "max_event_bytes", _DEFAULTS.max_event_bytes),
        total_timeout=_first(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=_first(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=_first(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
    )


def _progress(session: OperationSession) -> ProtocolProgress:
    return MappingProxyType({
        "reconnects": 0,
        "network_send_count": session.network_send_count,
        "network_send_budget_used": session.network_send_budget_used,
    })


def _session(plan: EventPlan[T], limits: _Limits) -> OperationSession:
    """Start the stream's session, refusing to open the stream when the session has no send slot."""
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only an open loads the call runtime.

    session = OperationSession(
        total_timeout=limits.total_timeout, deadline=limits.deadline, max_network_sends=limits.max_network_sends
    )
    if (limit := session.send_limit) is not None and limit <= 0:
        raise SessionLimitError(
            kind="network_sends",
            limit=limit,
            progress=_progress(session),
            helper_id=plan.helper_id,
            operation=plan.operation,
            parent_session_id=session.session_id,
        )
    return session


def _refused(plan: EventPlan[T], session: OperationSession, error: BudgetExceededError) -> SessionLimitError:
    """Return the session's limit error for an open whose retries its session had no send slot for."""
    return SessionLimitError(
        kind="network_sends",
        limit=error.limit,
        progress=_progress(session),
        helper_id=plan.helper_id,
        operation=plan.operation,
        operation_id=error.operation_id,
        call_id=error.call_id,
        parent_session_id=error.parent_session_id,
        info=error.info,
        cause=error,
        resource_attempt_count=error.resource_attempt_count,
        redirect_count=error.redirect_count,
        auth_exchange_count=error.auth_exchange_count,
        network_send_count=error.network_send_count,
        network_send_budget_used=error.network_send_budget_used,
        auth_exchange_budget_used=error.auth_exchange_budget_used,
        auth_refresh_ids=error.auth_refresh_ids,
        auth_refresh_pending=error.auth_refresh_pending,
        wire_send_count=error.wire_send_count,
    )


class _State(Enum):
    OPEN = "open"
    ENDED = "ended"
    FAILED = "failed"
    CLOSED = "closed"


class _End(Enum):
    END = "end"


_ENDED: Final = _End.END


class _Events(Generic[T]):
    """What the synchronous and asyncio streams share: the parser, the plan's decoding, the state, and the counts."""

    __slots__ = (
        "_delivered",
        "_errors",
        "_frames",
        "_info",
        "_lock",
        "_max_event_bytes",
        "_native",
        "_plan",
        "_routes",
        "_sequence",
        "_session",
        "_state",
    )

    def __init__(
        self, plan: EventPlan[T], limits: _Limits, session: OperationSession, info: ResponseInfo, *, native: bool
    ) -> None:
        """Start before the first event of an open response."""
        self._plan = plan
        self._session = session
        self._info = info
        self._native = native
        self._max_event_bytes = limits.max_event_bytes
        self._frames = _Parser(limits.max_line_bytes, limits.max_event_bytes)
        self._routes: Mapping[str, NativeValue[T]] = dict(plan.routes)
        self._errors: Mapping[str, NativeValue[object]] = dict(plan.errors)
        self._lock = threading.Lock()
        self._state = _State.OPEN
        self._sequence = 0
        self._delivered = 0

    @property
    def response(self) -> ResponseInfo:
        """Return the metadata of the stream's response."""
        return self._info

    @property
    def progress(self) -> ProtocolProgress:
        """Return the reconnections, none without resume metadata, and the session's sends so far."""
        return _progress(self._session)

    def _enter(self, action: str) -> bool:
        """Take the stream for one step, or return False once it ended; refuse a concurrent, failed, or closed one."""
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, "receiving")
        if (state := self._state) is _State.OPEN:
            return True
        self._lock.release()
        if state is _State.ENDED:
            return False
        raise self._state_error(action, state.value)

    def _closing(self, action: str) -> bool:
        """Mark an open stream closed, returning whether it still held its response."""
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, "receiving")
        held = self._state is _State.OPEN
        if held:
            self._state = _State.CLOSED
        self._lock.release()
        return held

    def _state_error(self, action: str, state: str) -> ProtocolStateError:
        plan = self._plan
        return ProtocolStateError(
            state=state,
            action=action,
            helper_id=plan.helper_id,
            operation=plan.operation,
            parent_session_id=self._session.session_id,
        )

    def _stamped(self, error: ProtocolErrorT) -> ProtocolErrorT:
        """Give a failure of the stream the helper's context, the stream call's identity, and its counters."""
        info, plan = self._info, self._plan
        error.helper_id = plan.helper_id
        error.operation = plan.operation
        error.info = info
        error.operation_id = plan.call.operation_id
        error.call_id = info.call_id
        error.parent_session_id = self._session.session_id
        set_error_counters(
            error,
            resource_attempt_count=info.resource_attempt_count,
            redirect_count=info.redirect_count,
            auth_exchange_count=info.auth_exchange_count,
            network_send_count=info.network_send_count,
            network_send_budget_used=info.network_send_budget_used,
            auth_exchange_budget_used=info.auth_exchange_budget_used,
            auth_refresh_ids=info.auth_refresh_ids,
            auth_refresh_pending=info.auth_refresh_pending,
            wire_send_count=info.wire_send_count,
        )
        return error

    def _frame(self) -> _Frame | None:
        """Return the next dispatched event the bytes read so far hold, or None when it needs more."""
        try:
            return self._frames.next()
        except ProtocolSizeError as error:
            raise self._stamped(error) from None

    def _ended(self) -> _End:
        """Return the end of a response that ended the stream as declared, raising the failure of one that did not."""
        if (buffered := self._frames.incomplete()) is not None:
            raise self._stamped(IncompleteFrameError(buffered_bytes=buffered, sequence=self._delivered))
        if self._plan.completion != "eof":
            raise self._stamped(StreamInterruptedError(condition="eof", sequence=self._delivered))
        return _ENDED

    def _broken(self, error: BaseException) -> BaseException:
        """Return how a failure ends the stream: a transport failure reading it, but no phase timeout, interrupts it."""
        if isinstance(error, TransportError) and not isinstance(error, PhaseTimeoutError):
            return self._stamped(StreamInterruptedError(condition="transport", sequence=self._delivered, cause=error))
        return error

    def _event(self, frame: _Frame) -> StreamEvent[T] | _End:
        """Return a dispatched event decoded, or the stream's end at its terminal event; an error event raises."""
        self._sequence += 1
        plan = self._plan
        if (
            plan.completion != "eof"
            and (frame.data if plan.completion == "sentinel" else frame.event_type) == plan.terminal
        ):
            return _ENDED
        wire: WireValue | None = None
        key = frame.event_type
        if (selector := plan.discriminator) is not None:
            wire = self._wire(frame)
            if (found := resolve(wire, selector.pointer)) is MISSING or not isinstance(found, str):
                condition: Literal["missing", "null", "type"] = (
                    "missing" if found is MISSING else "null" if found is None else "type"
                )
                raise self._stamped(self._decode_error(frame, condition, location=selector))
            key = found
        if (error := self._errors.get(key)) is not None:
            data = self._decoded(error, frame, wire)
            raise self._stamped(StreamRemoteError(event_type=frame.event_type, data=data, sequence=self._sequence))
        if (decoder := plan.event) is None and (decoder := self._routes.get(key)) is None:
            if (unknown := plan.unknown) is None:
                raise self._stamped(self._decode_error(frame, "value", location=selector))
            value = unknown(key, frame.data)
        else:
            value = self._decoded(decoder, frame, wire)
        self._delivered = self._sequence
        return StreamEvent(
            data=value,
            event_type=frame.event_type,
            event_id=frame.event_id,
            retry_ms=frame.retry_ms,
            sequence=self._sequence,
            raw_data=frame.data,
        )

    def _wire(self, frame: _Frame) -> WireValue:
        """Return an event's data parsed as JSON, raising StreamDecodeError for data that does not parse."""
        try:
            return decode_json(frame.data.encode())
        except _DATA_ERRORS as error:
            raise self._stamped(self._decode_error(frame, "malformed", cause=error)) from None

    def _decoded(self, decoder: NativeValue[U], frame: _Frame, wire: WireValue | None) -> U:
        """Return an event's data decoded, by its schema or through its converter alone as the call validates."""
        if wire is None:
            wire = self._wire(frame)
        try:
            return decoder.convert(wire) if self._native else decoder(wire)
        except _DATA_ERRORS as error:
            raise self._stamped(self._decode_error(frame, "value", cause=error)) from None

    def _decode_error(
        self,
        frame: _Frame,
        condition: Literal["missing", "null", "type", "value", "malformed"],
        *,
        location: BodySelector | None = None,
        cause: BaseException | None = None,
    ) -> StreamDecodeError:
        """Return the decode failure of an event, keeping at most the event limit or 64 KiB of its raw data."""
        data = frame.data.encode()
        limit = min(self._max_event_bytes, MAX_RAW_PREFIX)
        return StreamDecodeError(
            sequence=self._sequence,
            raw_prefix=data[:limit],
            truncated=len(data) > limit,
            condition=condition,
            location=location,
            cause=cause,
        )


@final
class EventStream(_Events[T]):
    """A synchronous SSE stream: iterate over its events, or over their data with `data()`.

    The stream owns its response until it ends, fails, or closes. It is read by one consumer at a time; after a failure
    or `close()` every step raises ProtocolStateError, and after its end every step stops.
    """

    __slots__ = ("_chunks", "_response")

    def __init__(
        self,
        plan: EventPlan[T],
        limits: _Limits,
        session: OperationSession,
        response: RawResponse,
        *,
        native: bool,
    ) -> None:
        """Take the open response, whose body is read only as the events need it."""
        super().__init__(plan, limits, session, response.info, native=native)
        self._response = response
        self._chunks = cast("Generator[bytes, None, None]", response.iter_bytes())

    def __iter__(self) -> Self:
        """Iterate over the events."""
        return self

    def __next__(self) -> StreamEvent[T]:
        """Return the next event, reading only the bytes it needs."""
        if not self._enter("next"):
            raise StopIteration
        try:
            return self._step()
        finally:
            self._lock.release()

    def data(self) -> Iterator[T]:
        """Return an iterator over the data of the same events, which advances this stream."""
        return _Data(self)

    def _step(self) -> StreamEvent[T]:
        """Return the next event, releasing the response once the stream ends or fails."""
        try:
            event = self._read()
        except BaseException as error:  # noqa: BLE001
            self._state = _State.FAILED
            failure = self._broken(error)
            self._release(failure)
            raise failure from None
        if isinstance(event, _End):
            self._state = _State.ENDED
            self._release(None)
            raise StopIteration
        return event

    def _read(self) -> StreamEvent[T] | _End:
        while (frame := self._frame()) is None:
            if (chunk := next(self._chunks, None)) is None:
                return self._ended()
            self._frames.feed(chunk)
        return self._event(frame)

    def _release(self, failure: BaseException | None) -> None:
        """Stop reading the body and release the response, keeping a close failure beside a failure."""
        self._chunks.close()
        if failure is None:
            self._response.close()
        else:
            self._response.discard(failure)

    def close(self) -> None:
        """Release the response; later steps raise ProtocolStateError, and closing again does nothing."""
        if self._closing("close"):
            self._release(None)

    def __enter__(self) -> Self:
        """Return this stream, which leaving the block closes."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the stream."""
        self.close()


@final
class AsyncEventStream(_Events[T]):
    """An asyncio SSE stream: iterate over its events with `async for`, or over their data with `data()`.

    The stream owns its response until it ends, fails, or closes. It is read by one task at a time; after a failure or
    `aclose()` every step raises ProtocolStateError, and after its end every step stops.
    """

    __slots__ = ("_chunks", "_response")

    def __init__(
        self,
        plan: EventPlan[T],
        limits: _Limits,
        session: OperationSession,
        response: AsyncRawResponse,
        *,
        native: bool,
    ) -> None:
        """Take the open response, whose body is read only as the events need it."""
        super().__init__(plan, limits, session, response.info, native=native)
        self._response = response
        self._chunks = cast("AsyncGenerator[bytes, None]", response.iter_bytes())

    def __aiter__(self) -> Self:
        """Iterate over the events."""
        return self

    async def __anext__(self) -> StreamEvent[T]:
        """Return the next event, reading only the bytes it needs."""
        if not self._enter("anext"):
            raise StopAsyncIteration
        try:
            return await self._step()
        finally:
            self._lock.release()

    def data(self) -> AsyncIterator[T]:
        """Return an asyncio iterator over the data of the same events, which advances this stream."""
        return _AsyncData(self)

    async def _step(self) -> StreamEvent[T]:
        """Return the next event, releasing the response once the stream ends or fails."""
        try:
            event = await self._read()
        except BaseException as error:  # noqa: BLE001
            self._state = _State.FAILED
            failure = self._broken(error)
            await self._release(failure)
            raise failure from None
        if isinstance(event, _End):
            self._state = _State.ENDED
            await self._release(None)
            raise StopAsyncIteration
        return event

    async def _read(self) -> StreamEvent[T] | _End:
        while (frame := self._frame()) is None:
            if (chunk := await anext(self._chunks, None)) is None:
                return self._ended()
            self._frames.feed(chunk)
        return self._event(frame)

    async def _release(self, failure: BaseException | None) -> None:
        """Stop reading the body and release the response, keeping a close failure beside a failure."""
        await self._chunks.aclose()
        if failure is None:
            await self._response.aclose()
        else:
            await self._response.discard(failure)

    async def aclose(self) -> None:
        """Release the response; later steps raise ProtocolStateError, and closing again does nothing."""
        if self._closing("aclose"):
            await self._release(None)

    async def __aenter__(self) -> Self:
        """Return this stream, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the stream."""
        await self.aclose()


@final
class _Data(Generic[T]):
    """Iterate over the data of a stream's events through the stream itself."""

    __slots__ = ("_stream",)

    def __init__(self, stream: EventStream[T]) -> None:
        self._stream = stream

    def __iter__(self) -> Self:
        """Return this iterator."""
        return self

    def __next__(self) -> T:
        """Return the data of the stream's next event."""
        return next(self._stream).data


@final
class _AsyncData(Generic[T]):
    """Iterate over the data of an asyncio stream's events through the stream itself."""

    __slots__ = ("_stream",)

    def __init__(self, stream: AsyncEventStream[T]) -> None:
        self._stream = stream

    def __aiter__(self) -> Self:
        """Return this iterator."""
        return self

    async def __anext__(self) -> T:
        """Return the data of the stream's next event."""
        return (await anext(self._stream)).data


def open_events(  # noqa: PLR0913
    core: ClientCore,
    plan: EventPlan[T],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    stream_options: object = None,
    options: object = None,
    session_options: object = None,
) -> EventStream[T]:
    """Open a helper's stream in a session of its own, returning once its response is a declared success."""
    limits = _limits(core, plan, stream_options, options, session_options)
    native = core.native_responses(limits.options, plan.call.operation_id)
    session = _session(plan, limits)
    try:
        response = core.execute_raw(
            plan.call,
            arguments,
            body=body,
            media_type=media_type,
            options=limits.options,
            response_media_type=plan.media,
            stream=True,
            session=session,
        )
    except BudgetExceededError as error:
        if error.budget_kind != "parent_network":
            raise
        raise _refused(plan, session, error) from None
    return EventStream(plan, limits, session, response, native=native)


async def aopen_events(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: EventPlan[T],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    stream_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncEventStream[T]:
    """Open a helper's stream with asyncio, returning once its response is a declared success."""
    limits = _limits(core, plan, stream_options, options, session_options)
    native = core.native_responses(limits.options, plan.call.operation_id)
    session = _session(plan, limits)
    try:
        response = await core.execute_raw(
            plan.call,
            arguments,
            body=body,
            media_type=media_type,
            options=limits.options,
            response_media_type=plan.media,
            stream=True,
            session=session,
        )
    except BudgetExceededError as error:
        if error.budget_kind != "parent_network":
            raise
        raise _refused(plan, session, error) from None
    return AsyncEventStream(plan, limits, session, response, native=native)
