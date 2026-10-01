"""Server-sent event and NDJSON streams: their parsers, typed events, and the handles that read them.

A stream helper opens its operation as one child call of a session of its own and hands the response to a handle, which
reads only the bytes the next event or record needs. The response's idle limit counts only while a step waits for
bytes; between steps only the stream's total limit and the session's deadline run.
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
from ..client.raw import afinished, aheld, checked, finished, held
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
    from collections.abc import AsyncIterator, Callable, Iterator, Mapping
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
    last valid `retry` in milliseconds. Events are numbered from 1 in the order the stream dispatched them. An NDJSON
    record has the empty string as its type, and neither an ID nor a reconnection time. The data, the event ID, and the
    raw data never appear in the representation.
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
    """Everything fixed about one generated stream helper: its identity, call, media type, events, errors, and end.

    `kind` frames the body as server-sent events or as NDJSON records, one line each. `event` decodes the data of a
    helper with one event schema; `routes` decode it by the discriminator the event's SSE type gives, or the string
    `discriminator` reads from its JSON data, and `unknown` keeps an unmapped one. `errors` decode the declared error
    events, by the same discriminator. A `sentinel` completion ends at the raw data `terminal` and an `event_type` one
    at that SSE type; neither terminal event is decoded or delivered. An NDJSON body's bytes after its last line end
    are a record only when `final_line` is `allow_eof`.
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
    kind: Literal["sse", "ndjson"] = "sse"
    final_line: Literal["require_newline", "allow_eof"] = "require_newline"


@dataclass(frozen=True, slots=True)
class _Frame:
    """A dispatched event: its joined data, its type, the last event ID, and the reconnection time.

    An NDJSON record is a frame of its line, without a type, an event ID, or a reconnection time.
    """

    data: str
    event_type: str
    event_id: str | None
    retry_ms: int | None


class _Parser:
    """Parse an event stream's bytes as the WHATWG event-stream interpretation does, one dispatched event at a time.

    A leading byte order mark is skipped, and CR, LF, and CRLF end lines, a CRLF split across chunks included. A line
    over the line limit and data over the event limit, counted in bytes before they are kept, raise ProtocolSizeError.
    Invalid UTF-8 decodes to replacement characters. A `retry` of more than 18 digits is ignored. The bytes kept are
    searched for a line end at most twice, so a line arriving in many chunks costs time linear in its length.
    """

    __slots__ = (
        "_buffer",
        "_data",
        "_event_id",
        "_event_type",
        "_fields",
        "_frame_bytes",
        "_lf",
        "_lines",
        "_retry",
        "_scanned",
        "_started",
        "max_event",
        "max_line",
    )

    def __init__(self, max_line: int, max_event: int) -> None:
        """Start before the first byte, with empty buffers."""
        self.max_line = max_line
        self.max_event = max_event
        self._buffer = bytearray()
        self._scanned: int = 0
        self._started = False
        self._lf = False
        self._data = bytearray()
        self._lines = 0
        self._event_type = ""
        self._event_id = ""
        self._retry: int | None = None
        self._frame_bytes = 0
        self._fields = False

    def feed(self, chunk: bytes) -> None:
        """Keep the bytes of the next chunk, refusing them first when they extend the unended line over its limit.

        Once the stream started, the bytes kept are all of a line that has not ended, as `next` consumed the others.
        """
        if self._started:
            ended = _TERMINATOR.search(chunk)
            _limit("line", self.max_line, len(self._buffer) + (len(chunk) if ended is None else ended.start()))
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
                if (found := _TERMINATOR.search(buffer, max(position, self._scanned))) is None:
                    self._scanned = len(buffer)
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
            self._scanned = max(0, self._scanned - position)

    def incomplete(self) -> int | None:
        """Return the bytes of the frame an end of the stream cuts, or None when it ends between frames."""
        pending = len(self._buffer)
        return self._frame_bytes + pending if pending or self._fields else None

    @staticmethod
    def last() -> None:
        """Return no event at the end of the stream, which dispatches only at a blank line."""

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
                data = self._data
                _limit("event", self.max_event, len(data) + len(value) + (1 if self._lines else 0))
                if self._lines:
                    data += b"\n"
                data += value
                self._lines += 1
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
        data, lines, self._data, self._lines = self._data, self._lines, bytearray(), 0
        event_type, self._event_type = self._event_type, ""
        self._frame_bytes = 0
        self._fields = False
        if not lines:
            return None
        return _Frame(data.decode("utf-8", "replace"), event_type or "message", self._event_id or None, self._retry)


def _limit(kind: Literal["line", "event"], limit: int, observed: int) -> None:
    if observed > limit:
        raise ProtocolSizeError(kind=kind, limit=limit, observed=observed, unit="bytes")


_NO_TYPE: Final = ""


class _Lines:
    """Split an NDJSON body into records, one line each, ended by LF, or CRLF whose CR is not part of the record.

    A record is strict UTF-8, whose failure raises UnicodeDecodeError. A line counts toward the line and the record
    limits, the smaller of which, counted in bytes before the line is kept, raises ProtocolSizeError of its kind. Each
    byte kept is searched for LF at most twice, so a line arriving in many chunks costs time linear in its length. The
    bytes after the last LF are a final record at the end of the body only when `allow_eof` is set.
    """

    __slots__ = ("_buffer", "_kind", "_maximum", "_scanned", "allow_eof")

    def __init__(self, max_line: int, max_record: int, *, allow_eof: bool) -> None:
        """Start before the first byte, with an empty buffer."""
        self._kind: Literal["line", "event"] = "line" if max_line <= max_record else "event"
        self._maximum = min(max_line, max_record)
        self.allow_eof = allow_eof
        self._buffer = bytearray()
        self._scanned = 0

    def feed(self, chunk: bytes) -> None:
        """Keep the bytes of the next chunk, refusing them first when they extend the unended line over its limit.

        The bytes kept are all of a line that has not ended, as `next` consumed the others.
        """
        ended = chunk.find(b"\n")
        _limit(self._kind, self._maximum, len(self._buffer) + (len(chunk) if ended < 0 else ended))
        self._buffer += chunk

    def next(self) -> _Frame | None:
        """Return the next record from the bytes kept, or None when its line has not ended yet."""
        buffer = self._buffer
        if (end := buffer.find(b"\n", self._scanned)) < 0:
            _limit(self._kind, self._maximum, len(buffer))
            self._scanned = len(buffer)
            return None
        _limit(self._kind, self._maximum, end)
        record = buffer[:end]
        del buffer[: end + 1]
        self._scanned = 0
        return _record(record)

    def last(self) -> _Frame | None:
        """Return the record of the bytes after the last line end at the end of the body, when it allows one."""
        if not self.allow_eof or not (buffer := self._buffer):
            return None
        record = _record(buffer)
        buffer.clear()
        return record

    def incomplete(self) -> int | None:
        """Return the bytes of the line an end of the stream cuts, or None when it ends after a line end."""
        return len(self._buffer) or None


def _record(line: bytearray) -> _Frame:
    """Return the record of a line, without the CR of a CRLF, decoded as strict UTF-8."""
    if line.endswith(b"\r"):
        del line[-1]
    return _Frame(line.decode(), _NO_TYPE, None, None)


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
    """What the synchronous and asyncio streams share: the parser, the plan's decoding, the state, and the counts.

    The plan's kind chooses the parser: the event-stream parser for SSE, or the line splitter for NDJSON.
    """

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
        self._frames = (
            _Parser(limits.max_line_bytes, limits.max_event_bytes)
            if plan.kind == "sse"
            else _Lines(limits.max_line_bytes, limits.max_event_bytes, allow_eof=plan.final_line == "allow_eof")
        )
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

    def _frame(self, chunk: bytes | None = None, *, last: bool = False) -> _Frame | None:
        """Keep a chunk read, then return the next dispatched event the bytes kept hold, or None when it needs more.

        At the end of the body, `last` returns the event the bytes kept end with instead. A record that is not UTF-8
        raises StreamDecodeError without a cause, which would hold the whole record.
        """
        frames = self._frames
        try:
            if last:
                return frames.last()
            if chunk is not None:
                frames.feed(chunk)
            return frames.next()
        except ProtocolSizeError as error:
            raise self._stamped(error) from None
        except UnicodeDecodeError as error:
            self._sequence += 1
            raise self._stamped(self._decode_error(error.object, "malformed")) from None

    def _last(self) -> StreamEvent[T] | _End:
        """Return the event the body ends with, or else how the stream ended."""
        frame = self._frame(last=True)
        return self._ended() if frame is None else self._event(frame)

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
                raise self._stamped(self._decode_error(frame.data.encode(), condition, location=selector))
            key = found
        if (error := self._errors.get(key)) is not None:
            data = self._decoded(error, frame, wire)
            raise self._stamped(
                StreamRemoteError(event_type=frame.event_type or None, data=data, sequence=self._sequence)
            )
        if (decoder := plan.event) is None and (decoder := self._routes.get(key)) is None:
            if (unknown := plan.unknown) is None:
                raise self._stamped(self._decode_error(frame.data.encode(), "value", location=selector))
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
        data = frame.data.encode()
        try:
            return decode_json(data)
        except _DATA_ERRORS as error:
            raise self._stamped(self._decode_error(data, "malformed", cause=error)) from None

    def _decoded(self, decoder: NativeValue[U], frame: _Frame, wire: WireValue | None) -> U:
        """Return an event's data decoded, by its schema or through its converter alone as the call validates."""
        if wire is None:
            wire = self._wire(frame)
        try:
            return decoder.convert(wire) if self._native else decoder(wire)
        except _DATA_ERRORS as error:
            raise self._stamped(self._decode_error(frame.data.encode(), "value", cause=error)) from None

    def _decode_error(
        self,
        data: bytes,
        condition: Literal["missing", "null", "type", "value", "malformed"],
        *,
        location: BodySelector | None = None,
        cause: BaseException | None = None,
    ) -> StreamDecodeError:
        """Return the decode failure of an event, keeping at most the event limit or 64 KiB of its raw data."""
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
    """A synchronous SSE or NDJSON stream: iterate over its events, or over their data with `data()`.

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
        self._chunks = held(response)

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
            self._chunks.close()
            finished(self._response, failure)
            raise failure from None
        if isinstance(event, _End):
            self._state = _State.ENDED
            self._chunks.close()
            finished(self._response)
            raise StopIteration
        return event

    def _read(self) -> StreamEvent[T] | _End:
        checked(self._response)
        frame = self._frame()
        while frame is None:
            if (chunk := next(self._chunks, None)) is None:
                return self._last()
            frame = self._frame(chunk)
        return self._event(frame)

    def close(self) -> None:
        """Release the response; later steps raise ProtocolStateError, and closing again does nothing."""
        if self._closing("close"):
            self._chunks.close()
            self._response.close()

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
    """An asyncio SSE or NDJSON stream: iterate over its events with `async for`, or over their data with `data()`.

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
        self._chunks = aheld(response)

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
            await self._chunks.aclose()
            await afinished(self._response, failure)
            raise failure from None
        if isinstance(event, _End):
            self._state = _State.ENDED
            await self._chunks.aclose()
            await afinished(self._response)
            raise StopAsyncIteration
        return event

    async def _read(self) -> StreamEvent[T] | _End:
        checked(self._response)
        frame = self._frame()
        while frame is None:
            if (chunk := await anext(self._chunks, None)) is None:
                return self._last()
            frame = self._frame(chunk)
        return self._event(frame)

    async def aclose(self) -> None:
        """Release the response; later steps raise ProtocolStateError, and closing again does nothing."""
        if self._closing("aclose"):
            await self._chunks.aclose()
            await self._response.aclose()

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
