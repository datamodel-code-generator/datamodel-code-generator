"""Server-sent event and NDJSON streams: their parsers, typed events, and the handles that read them.

A stream helper opens its operation as one child call of a session of its own and hands the response to a handle, which
reads only the bytes the next event or record needs. The response's idle limit counts only while a step waits for
bytes; between steps only the stream's total limit and the session's deadline run. A helper whose metadata declares
resumption tracks the cursor of the last event it delivered: its `checkpoint` saves it, its `resume` reopens the stream
after it in a session of its own, and an interruption reopens it as one more child call of the same session when the
call enables reconnection.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from hashlib import sha256
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, Literal, cast, final

from typing_extensions import Self, TypeVar

from ..client.errors import (
    BudgetExceededError,
    PhaseTimeoutError,
    ProtocolConfigurationError,
    ProtocolError,
    ProtocolSizeError,
    RequestEncodingError,
    TransportError,
    set_error_counters,
)
from ..client.options import RequestOptions
from ..client.raw import afinished, aheld, checked, finished, held
from ..client.timing import SessionOptions
from ..model_codecs.errors import (
    CodecBindingError,
    CodecError,
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
    ProtocolDataError,
    ProtocolStateError,
    ResumeStateError,
    SessionLimitError,
    StreamDecodeError,
    StreamInterruptedError,
    StreamRemoteError,
    StreamResumeExhaustedError,
)
from .options import StreamOptions, layered
from .records import canonical_json
from .resume import (
    MalformedStateError,
    ResumeState,
    helper_state,
    require_state,
    server_expiry,
    state_array,
    state_count,
    state_fields,
)
from .values import MISSING, Missing, Patch, RepeatedValueError, resolve, selected, written

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from types import TracebackType
    from typing import TypeAlias

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.codecs import NativeValue
    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import OperationPlan
    from ..client.raw import AsyncRawResponse, RawResponse
    from ..client.responses import ResponseInfo
    from ..client.timing import Deadline
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .errors import _DataCondition  # pyright: ignore[reportPrivateUsage]
    from .pagination import PageBinding
    from .records import BodySelector, HeaderSelector, ProtocolProgress, RequestTarget, Selector
    from .references import OperationRef
    from .writes import ReadPaths, Writes

    _Given: TypeAlias = tuple[tuple[object, ...], object, str | MediaSelector | None]

__all__ = (
    "AsyncEventStream",
    "EventPlan",
    "EventStream",
    "StreamEvent",
    "StreamResumePlan",
    "UnknownEvent",
    "aopen_events",
    "aresume_events",
    "open_events",
    "resume_events",
    "unknown_event",
)

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")
T_co = TypeVar("T_co", covariant=True, default=object)
ProtocolErrorT = TypeVar("ProtocolErrorT", bound=ProtocolError)

_TERMINATOR: Final = re.compile(rb"[\r\n]")
_RETRY_DIGITS: Final = 18
_RETRY: Final = re.compile(rb"[0-9]{1,%d}" % _RETRY_DIGITS)
_MAX_RETRY: Final = 10**_RETRY_DIGITS - 1
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
_STATE: Final = frozenset({"cursor", "sequence", "reconnects", "retry_ms", "bound", "arguments", "body"})
_ENCODING_ERRORS: Final = (RequestEncodingError, ParameterEncodingError)


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
class StreamResumePlan:
    """How a helper reopens its stream after the cursor of the last event it delivered.

    The cursor is the SSE event ID, or what `cursor` reads from an event's JSON data: a missing one is the event
    before's under `missing='inherit'` and null clears it under `null='clear'`, while `error` refuses the event. A
    reopen calls `call`, answered with `media`, writing each binding's value and then the cursor into `write`; one of
    the helper's `own` operation repeats the caller's first request with them, and another operation sends only what is
    written. A cleared cursor's parameter is omitted. A binding reads the open response for `initial` and the latest
    open or reopen response for `previous`. `reconnect_on` names the interruptions a call that enables it reconnects
    after, and `expires_at` the header of the open response that gives the server's expiry. `dotted` are the path
    segments of the reopen a binding's read value is written to, which must not encode to a dot segment, and `blank`
    the positions of the optional parameters a reopen writes, whose arguments a checkpoint never saves.
    """

    operation: OperationRef
    call: OperationPlan[object, object]
    media: str
    write: RequestTarget
    own: bool = False
    cursor: BodySelector | None = None
    missing: Literal["inherit", "error"] = "inherit"
    null: Literal["clear", "error"] = "clear"
    bindings: tuple[PageBinding, ...] = ()
    reconnect_on: tuple[Literal["transport_interruption", "incomplete_eof"], ...] = ("transport_interruption",)
    expires_at: HeaderSelector | None = None
    reopened: OperationPlan[object, object] = field(init=False)
    writes: Writes = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)
    dotted: ReadPaths = field(init=False)
    blank: frozenset[int] = field(init=False)

    def __post_init__(self) -> None:
        """Derive the operation of a reopen taking the bindings' values and then the cursor as wire values.

        The header and query parameters it writes are those a call's options must not patch.
        """
        from .writes import read_paths, targeted  # noqa: PLC0415 - Only a plan loads the operation runtime.

        sources = tuple((binding.target, binding.selector) for binding in self.bindings)
        reopened, writes, headers, queries = targeted(self.call, (*(target for target, _ in sources), self.write))
        object.__setattr__(self, "reopened", reopened)
        object.__setattr__(self, "writes", writes)
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "queries", queries)
        object.__setattr__(self, "dotted", read_paths(self.call, sources))
        parameters = self.call.parameters
        object.__setattr__(
            self,
            "blank",
            frozenset(
                position
                for position, pointer in writes
                if position is not None and pointer is None and not parameters[position].plan.required
            ),
        )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class EventPlan(Generic[T]):
    """Everything fixed about one generated stream helper: its identity, call, media type, events, errors, and end.

    `kind` frames the body as server-sent events or as NDJSON records, one line each. `event` decodes the data of a
    helper with one event schema; `routes` decode it by the discriminator the event's SSE type gives, or the string
    `discriminator` reads from its JSON data, and `unknown` keeps an unmapped one. `errors` decode the declared error
    events, by the same discriminator. A `sentinel` completion ends at the raw data `terminal` and an `event_type` one
    at that SSE type; neither terminal event is decoded or delivered. An NDJSON body's bytes after its last line end
    are a record only when `final_line` is `allow_eof`. `resume` is how a helper declaring resumption reopens it.
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
    resume: StreamResumePlan | None = None


@dataclass(frozen=True, slots=True)
class _Frame:
    """A dispatched event: its joined data, its type, the last event ID, and the reconnection time.

    An NDJSON record is a frame of its line, without a type, an event ID, or a reconnection time, that keeps the line's
    bytes as `raw`, which its data was decoded from.
    """

    data: str
    event_type: str
    event_id: str | None
    retry_ms: int | None
    raw: bytes | None = None

    @property
    def body(self) -> bytes:
        """Return the bytes of the data: the line's own, or the data encoded as UTF-8."""
        return self.data.encode() if self.raw is None else self.raw


class _Parser:
    """Parse an event stream's bytes as the WHATWG event-stream interpretation does, one dispatched event at a time.

    A leading byte order mark is skipped, and CR, LF, and CRLF end lines, a CRLF split across chunks included. A line
    over the line limit and data over the event limit, counted in bytes before they are kept, raise ProtocolSizeError.
    Invalid UTF-8 decodes to replacement characters. A `retry` of more than 18 digits is ignored. The bytes kept are
    searched for a line end at most twice, so a line arriving in many chunks costs time linear in its length. A reopened
    stream starts with the last event ID and reconnection time of the stream before, as WHATWG's reconnection does.
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

    def __init__(self, max_line: int, max_event: int, event_id: str = "", retry: int | None = None) -> None:
        """Start before the first byte, with empty buffers, a last event ID, and a reconnection time."""
        self.max_line = max_line
        self.max_event = max_event
        self._buffer = bytearray()
        self._scanned: int = 0
        self._started = False
        self._lf = False
        self._data = bytearray()
        self._lines = 0
        self._event_type = ""
        self._event_id = event_id
        self._retry = retry
        self._frame_bytes = 0
        self._fields = False

    @property
    def retry(self) -> int | None:
        """Return the last valid reconnection time in milliseconds, or None before any."""
        return self._retry

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

    A record is strict UTF-8, whose failure raises UnicodeDecodeError. A record counts toward the line and the record
    limits, the smaller of which, counted in bytes before the line is kept, raises ProtocolSizeError of its kind; the
    CR of a CRLF is not counted, nor is a CR that ends the bytes kept, which may be one. Each byte kept is searched for
    LF at most twice, so a line arriving in many chunks costs time linear in its length. The bytes after the last LF
    are a final record at the end of the body only when `allow_eof` is set.
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
        buffer = self._buffer
        end = len(chunk) if (ended := chunk.find(b"\n")) < 0 else ended
        last = chunk[end - 1] if end else buffer[-1] if buffer else None
        _limit(self._kind, self._maximum, len(buffer) + end - (last == _CR))
        buffer += chunk

    def next(self) -> _Frame | None:
        """Return the next record from the bytes kept, or None when its line has not ended yet."""
        buffer = self._buffer
        if (end := buffer.find(b"\n", self._scanned)) < 0:
            self._scanned = len(buffer)
            self._measure(buffer, len(buffer))
            return None
        self._measure(buffer, end)
        record = buffer[:end]
        del buffer[: end + 1]
        self._scanned = 0
        return _record(record)

    def _measure(self, buffer: bytearray, end: int) -> None:
        """Refuse the record of the bytes kept up to an end over its limit, without the CR the end may follow."""
        _limit(self._kind, self._maximum, end - (end > 0 and buffer[end - 1] == _CR))

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

    @property
    def retry(self) -> None:
        """Return no reconnection time, which NDJSON has none of."""


def _record(line: bytearray) -> _Frame:
    """Return the record of a line, without the CR of a CRLF, decoded as strict UTF-8."""
    if line.endswith(b"\r"):
        del line[-1]
    raw = bytes(line)
    return _Frame(raw.decode(), _NO_TYPE, None, None, raw)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective limits of one stream, each from the first layer that sets it; None removes a limit."""

    idle_timeout: float | Unset | None = UNSET
    reconnect: bool = False
    max_reconnects: int | None = 5
    max_reconnect_wait: float | None = 60.0
    max_line_bytes: int = 262144
    max_event_bytes: int = 1048576
    total_timeout: float | None = None
    deadline: Deadline | None = None
    max_network_sends: int | None = 16
    options: RequestOptions | None = None


_DEFAULTS: Final = _Limits()


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
    metadata, so a stream of a helper without it that would reconnect is refused. A helper with it refuses effective
    options fixing an idempotency key, since each reopen is a child call of its own, and header or query patches of a
    parameter a reopen writes.
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
    reconnect = layered(kinds, "reconnect", _DEFAULTS.reconnect)
    request = options if isinstance(options, RequestOptions) else None
    if (resume := plan.resume) is None:
        if reconnect:
            raise _invalid(plan, ("stream_options", "reconnect"), "missing_metadata")
    else:
        _unpatched(core, plan, resume, request)
    if not isinstance(idle := layered(kinds, "idle_timeout", _DEFAULTS.idle_timeout), Unset):
        request = replace(request or RequestOptions(), stream_idle_timeout=idle)
    return _Limits(
        reconnect=reconnect,
        max_reconnects=layered(kinds, "max_reconnects", _DEFAULTS.max_reconnects),
        max_reconnect_wait=layered(kinds, "max_reconnect_wait", _DEFAULTS.max_reconnect_wait),
        max_line_bytes=layered(kinds, "max_line_bytes", _DEFAULTS.max_line_bytes),
        max_event_bytes=layered(kinds, "max_event_bytes", _DEFAULTS.max_event_bytes),
        total_timeout=layered(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=layered(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=layered(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
    )


def _unpatched(
    core: ClientCore | AsyncClientCore, plan: EventPlan[T], resume: StreamResumePlan, request: RequestOptions | None
) -> None:
    """Refuse effective options fixing an idempotency key or patching a header or query parameter a reopen writes.

    The effective options are the client's, a view's, and the call's own, each of which patches every reopen.
    """
    if core.fixes_key(request):
        raise _invalid(plan, ("options", "idempotency_key"), "invalid_value")
    headers, queries = core.patches(request)
    for patch in headers:
        for name, _ in patch:
            if name.lower() in resume.headers:
                raise _invalid(plan, ("options", "headers", name), "invalid_value")
    for patch in queries:
        for name, _ in patch:
            if name in resume.queries:
                raise _invalid(plan, ("options", "query", name), "invalid_value")


def _progress(session: OperationSession, reconnects: int = 0) -> ProtocolProgress:
    return MappingProxyType({
        "reconnects": reconnects,
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


def _refused(  # noqa: PLR0913
    plan: EventPlan[T],
    operation: OperationRef,
    progress: ProtocolProgress,
    error: BudgetExceededError,
    *,
    error_type: type[SessionLimitError] = SessionLimitError,
    resume_state: ResumeState | None = None,
) -> SessionLimitError:
    """Return the session's limit error for an open or reopen whose retries its session had no send slot for.

    It names the operation sent, the helper's for an open and the reopen operation for a reopen.
    """
    return error_type(
        kind="network_sends",
        limit=error.limit,
        progress=progress,
        resume_state=resume_state,
        helper_id=plan.helper_id,
        operation=operation,
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


@dataclass(frozen=True, slots=True)
class _Position:
    """Where a stream of a helper declaring resumption starts: its request, cursor, counts, and the values it keeps.

    `given` is the caller's first request a reopen of the helper's own operation repeats. `cursored` tells that some
    delivered event gave a cursor, `cursor` is the last one, None once cleared, and `bound` the bindings' values.
    """

    given: _Given | None = None
    cursor: WireValue = None
    cursored: bool = False
    sequence: int = 0
    reconnects: int = 0
    retry_ms: int | None = None
    bound: tuple[WireValue, ...] = ()
    expires_at: datetime | None = None


_START: Final = _Position()


@dataclass(frozen=True, slots=True)
class _Reconnect:
    """An interruption a stream reconnects after, and how long it waits before the reopen."""

    failure: StreamInterruptedError
    delay: float


def _data_error(
    plan: EventPlan[T], operation: OperationRef, info: ResponseInfo, condition: _DataCondition, location: Selector
) -> ProtocolDataError:
    return ProtocolDataError(
        condition=condition, location=location, helper_id=plan.helper_id, operation=operation, info=info
    )


def _dotted(resume: StreamResumePlan, bound: tuple[WireValue, ...], given: _Given | None) -> Selector | None:
    """Return the selector of a read value that makes a path segment of the reopen a dot segment once encoded, or None.

    The caller's own path arguments in the segment, which only a reopen of the helper's own operation repeats, are
    encoded again by their parameters' codecs, without validation.
    """
    from .writes import dotted_read  # noqa: PLC0415 - A plan loaded the operation runtime.

    parameters = resume.call.parameters

    def callers() -> dict[str, str]:
        arguments = () if given is None else given[0]
        return {
            name: parameters[position].path_text(parameters[position].encode(arguments[position], "none"))
            for _, parts in resume.dotted
            for name, index, _, position in parts
            if index is None
        }

    return next(
        (
            read
            for segment, parts in resume.dotted
            if (read := dotted_read(parameters, segment, parts, bound, callers)) is not None
        ),
        None,
    )


def _bound(  # noqa: PLR0913, PLR0917
    plan: EventPlan[T],
    resume: StreamResumePlan,
    operation: OperationRef,
    info: ResponseInfo,
    given: _Given | None,
    kept: tuple[WireValue, ...] = (),
) -> tuple[WireValue, ...]:
    """Return the values the bindings write: their literals, kept `initial` values, and what a response gives.

    A stream response's header or status gives a value; a missing one, a header repeated where one is read, and read
    values that make a path segment of the reopen a dot segment once encoded are refused.
    """
    values: list[WireValue] = []
    for index, binding in enumerate(resume.bindings):
        if (read := binding.selector) is None:
            values.append(binding.literal)
            continue
        if kept and binding.source == "initial":
            values.append(kept[index])
            continue
        try:
            value = selected(read, None, info)
        except RepeatedValueError:
            raise _data_error(plan, operation, info, "malformed", read) from None
        if value is MISSING:
            raise _data_error(plan, operation, info, "missing", read)
        values.append(value)
    if (read := _dotted(resume, bound := tuple(values), given)) is not None:
        raise _data_error(plan, operation, info, "value", read)
    return bound


def _json(frame: _Frame) -> WireValue | Missing:
    """Return an event's data parsed as JSON, or MISSING when it is not JSON."""
    try:
        return decode_json(frame.body)
    except _DATA_ERRORS:
        return MISSING


def _absence(value: WireValue | Missing) -> Literal["missing", "null"]:
    return "missing" if value is MISSING else "null"


def _expiry(plan: EventPlan[T], read: HeaderSelector, info: ResponseInfo) -> datetime:
    """Return the server's expiry an open response's header gives, an RFC 3339 date-time with offset or an HTTP date.

    A value that is no string, which only a selector of every occurrence would read, is refused like an unreadable one.
    """
    try:
        value = selected(read, None, info)
    except RepeatedValueError:
        raise _data_error(plan, plan.operation, info, "malformed", read) from None
    if value is MISSING:
        raise _data_error(plan, plan.operation, info, "missing", read)
    if (expires_at := server_expiry(value) if isinstance(value, str) else None) is None:
        raise _data_error(plan, plan.operation, info, "value", read)
    return expires_at


def _opened(plan: EventPlan[T], resume: StreamResumePlan, given: _Given, info: ResponseInfo) -> _Position:
    """Return where a stream starts after its open response: the bindings' values and the server's expiry it gives."""
    kept = given if resume.own else None
    return _Position(
        given=kept,
        bound=_bound(plan, resume, plan.operation, info, kept),
        expires_at=None if (read := resume.expires_at) is None else _expiry(plan, read, info),
    )


def _reopen_request(resume: StreamResumePlan, position: _Position) -> _Given:
    """Return the arguments, body, and media type of a reopen: each binding's value and then the cursor written.

    A reopen of the helper's own operation writes them into the caller's first request, and another operation's
    request takes nothing else. A cleared cursor's parameter is omitted.
    """
    if (given := position.given) is not None:
        arguments, body, media_type = given
    else:
        arguments, body, media_type = (UNSET,) * len(resume.call.parameters), UNSET, None
    arguments, body = _written(resume, arguments, body, position.bound, position.cursor)
    return arguments, body, media_type


def _written(
    resume: StreamResumePlan,
    arguments: tuple[object, ...],
    body: object,
    bound: tuple[WireValue, ...],
    cursor: WireValue,
) -> tuple[tuple[object, ...], object]:
    """Return a request's arguments and body with each binding's value and then the cursor written.

    A cleared cursor's parameter is omitted.
    """
    writes = resume.writes
    if cursor is not None:
        return written(writes, arguments, body, (*bound, cursor))
    cleared = cast("int", writes[-1][0])
    return written(writes[:-1], (*arguments[:cleared], UNSET, *arguments[cleared + 1 :]), body, bound)


def _wire(value: object) -> WireValue:
    """Return a value written by wire value with its writes applied, or the value itself."""
    return value.applied(_same) if isinstance(value, Patch) else cast("WireValue", value)


def _wires(arguments: tuple[object, ...]) -> tuple[WireValue | Unset, ...]:
    """Return arguments written by wire value with their writes applied."""
    return cast("tuple[WireValue | Unset, ...]", tuple(map(_wire, arguments)))


def _same(value: object) -> WireValue:
    return cast("WireValue", value)


def _fitting(
    core: ClientCore | AsyncClientCore,
    resume: StreamResumePlan,
    fields: Mapping[str, WireValue],
    bound: tuple[WireValue, ...],
    cursor: WireValue,
) -> None:
    """Refuse a saved cursor or binding value that does not fit where the reopen writes it or that is never saved.

    The values are written into the saved request's wire values as a reopen writes them, and the request is built again
    from them as a saved request is, its body validated whole; a value that does not fit raises RequestEncodingError.
    """
    call = resume.call
    arguments: tuple[object, ...] = (UNSET,) * len(call.parameters)
    body: object = UNSET
    declared: str | None = None
    concrete: str | None = None
    if resume.own:
        arguments = tuple(
            item[0] if (item := state_array(saved)) else UNSET for saved in state_array(fields["arguments"])
        )
        if sent := state_array(fields["body"]):
            body, declared, concrete = sent[0], cast("str", sent[1]), cast("str | None", sent[2])
    patched, written_body = _written(resume, arguments, body, bound, cursor)
    wire = _wires(patched)
    require_state(core.unsaved_argument(call, wire) is None)
    saved: tuple[WireValue, str, str | None] | None = None
    if not isinstance(written_body, Unset) and (request := call.body) is not None:
        saved = _wire(written_body), declared or request.select(call.operation_id, None).media_type, concrete
    core.restored_request(call, wire, saved)


def _security(
    core: ClientCore | AsyncClientCore, resume: StreamResumePlan, options: RequestOptions | None
) -> tuple[str, bool]:
    """Return the digest of the security a checkpoint is bound to, that of the reopen, and whether it may leave."""
    facts, exportable = core.checkpoint_security(resume.call, options)
    return sha256(canonical_json(facts)).hexdigest(), exportable


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

    The plan's kind chooses the parser: the event-stream parser for SSE, or the line splitter for NDJSON. A stream of a
    helper declaring resumption also keeps the cursor of the last event it delivered, the bindings' values, and the
    caller's first request a reopen repeats; others keep none of them, and each event costs them nothing more.
    """

    __slots__ = (
        "_bound",
        "_cap",
        "_client",
        "_cursor",
        "_cursored",
        "_delivered",
        "_errors",
        "_expires_at",
        "_frames",
        "_given",
        "_info",
        "_limits",
        "_lock",
        "_native",
        "_operation",
        "_operation_id",
        "_plan",
        "_prefix",
        "_reconnects",
        "_resume",
        "_routes",
        "_sequence",
        "_session",
        "_state",
    )

    def __init__(  # noqa: PLR0913
        self,
        client: ClientCore | AsyncClientCore,
        plan: EventPlan[T],
        limits: _Limits,
        session: OperationSession,
        info: ResponseInfo,
        *,
        native: bool,
        position: _Position,
        reopened: bool,
    ) -> None:
        """Start before the first event of an open or reopen response, at the position the stream continues from."""
        self._client = client
        self._plan = plan
        self._resume = resume = plan.resume
        self._limits = limits
        self._session = session
        self._info = info
        self._native = native
        self._prefix = min(limits.max_event_bytes, MAX_RAW_PREFIX)
        self._routes: Mapping[str, NativeValue[T]] = dict(plan.routes)
        self._errors: Mapping[str, NativeValue[object]] = dict(plan.errors)
        self._lock = threading.Lock()
        self._state = _State.OPEN
        self._given = position.given
        self._cursor = position.cursor
        self._cursored = position.cursored
        self._sequence = self._delivered = position.sequence
        self._reconnects = position.reconnects
        self._bound = position.bound
        self._expires_at = position.expires_at
        self._cap: float | None = None
        source = resume if reopened and resume is not None else plan
        self._operation, self._operation_id = source.operation, source.call.operation_id
        self._frames = self._framer(position.retry_ms)

    @property
    def response(self) -> ResponseInfo:
        """Return the metadata of the stream's response, the latest reopen's once it reconnected."""
        return self._info

    @property
    def progress(self) -> ProtocolProgress:
        """Return the reconnections so far, none without resume metadata, and the session's sends."""
        return _progress(self._session, self._reconnects)

    def checkpoint(self) -> ResumeState:
        """Return a checkpoint the helper's `resume` reopens the stream from, sending nothing.

        It saves the cursor of the last event delivered, the counts so far, the last reconnection time, the bindings'
        values, and the caller's first request when the reopen repeats it, but never events, the response, the session,
        or the call's options. A stream that ended, failed, or closed checkpoints as it stood; one running a step
        refuses with ProtocolStateError, and so does one that delivered no cursor yet.
        """
        action = "checkpoint"
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, "receiving")
        try:
            return self._saved()
        finally:
            self._lock.release()

    def _saved(self) -> ResumeState:
        """Return a checkpoint of the stream, bound to the helper and the security its reopen runs under.

        A helper without resume metadata refuses, and so does a call giving an argument a checkpoint never saves, or a
        cursor or binding value the reopen writes as one, such as a security scheme's query field. The
        caller's arguments at the optional parameters a reopen writes are not saved, and a cursor the reopen request
        cannot encode raises ProtocolDataError rather than saving a state that `resume` refuses.
        """
        plan, client = self._plan, self._client
        if (resume := self._resume) is None:
            raise _invalid(plan, ("resume",), "missing_metadata")
        if not self._cursored:
            action = "checkpoint"
            raise self._state_error(action, "uncursored")
        options = self._limits.options
        position = self._position()
        media_type = None if (given := self._given) is None else given[2]
        try:
            client.checked_page(
                resume.reopened, lambda: (*_reopen_request(resume, position)[:2], None), media_type, options
            )
        except _ENCODING_ERRORS as error:
            raise self._unencodable(error) from None
        sent = _written(resume, (UNSET,) * len(resume.call.parameters), UNSET, self._bound, self._cursor)[0]
        if (unsaved := client.unsaved_argument(resume.call, _wires(sent))) is not None:
            raise ProtocolConfigurationError(
                field_path=("arguments", *unsaved),
                condition="wrong_capability",
                helper_id=plan.helper_id,
                operation=plan.operation,
            )
        arguments: WireValue = ()
        body: WireValue = ()
        if given is not None:
            from .pagination import saved_request  # noqa: PLC0415 - Only a checkpoint saves a request.

            blank = resume.blank
            kept = tuple(UNSET if index in blank else value for index, value in enumerate(given[0]))
            arguments, body = saved_request(*client.saved_request(plan, plan.call, kept, *given[1:], options))
        security, exportable = _security(client, resume, options)
        state: WireValue = {
            "cursor": self._cursor,
            "sequence": self._sequence,
            "reconnects": self._reconnects,
            "retry_ms": self._frames.retry,
            "bound": self._bound,
            "arguments": arguments,
            "body": body,
        }
        return helper_state(
            helper_fingerprint=plan.fingerprint,
            security_fingerprint=security,
            state=state,
            payload=b"",
            exportable=exportable,
            expires_at=self._expires_at,
        )

    def _resumable(self) -> ResumeState | None:
        """Return a checkpoint for an error to keep, or None when the stream cannot be checkpointed.

        Whatever keeps the stream from being checkpointed never replaces the error that keeps the checkpoint.
        """
        try:
            return self._saved()
        except Exception:  # noqa: BLE001
            return None

    def _framer(self, retry: int | None) -> _Parser | _Lines:
        """Return the parser of a response: an SSE one starting at the event ID cursor and the reconnection time."""
        plan, limits = self._plan, self._limits
        if plan.kind != "sse":
            return _Lines(limits.max_line_bytes, limits.max_event_bytes, allow_eof=plan.final_line == "allow_eof")
        cursor = self._cursor
        last = cursor if (resume := self._resume) is not None and resume.cursor is None and cursor is not None else ""
        return _Parser(limits.max_line_bytes, limits.max_event_bytes, cast("str", last), retry)

    def _position(self) -> _Position:
        """Return where the stream stands, which its next reopen continues from."""
        return _Position(given=self._given, cursor=self._cursor, bound=self._bound)

    def _advance(self, resume: StreamResumePlan, frame: _Frame, wire: WireValue | None) -> None:
        """Take the cursor of an event about to be delivered: its event ID, or what the cursor reads from its data.

        A missing body cursor keeps the one before under `inherit` and a null one clears it under `clear`; otherwise
        either refuses the event with StreamDecodeError. An unknown event whose data is not JSON gives no cursor.
        """
        if (read := resume.cursor) is None:
            if (cursor := frame.event_id) is not None:
                self._cursored = True
            self._cursor = cursor
            return
        data = _json(frame) if wire is None else wire
        found = MISSING if data is MISSING else resolve(data, read.pointer)
        if found is MISSING or found is None:
            if (resume.missing if found is MISSING else resume.null) == "error":
                raise self._stamped(self._decode_error(frame.body, _absence(found), location=read))
            if found is None:
                self._cursor = None
            return
        self._cursor, self._cursored = found, True

    def _reconnection(self, failure: BaseException) -> _Reconnect | None:
        """Return how the stream reconnects after a failure, or None when the failure ends it.

        Only an interruption the metadata names reconnects, once a cursor was delivered and the call enables
        reconnection: a transport one only after a read-phase failure the shared retry classification retries. One
        that does not keeps a checkpoint as its resume state, and so does one whose longest wait, the retry backoff's
        cap and at least the last reconnection time, is longer than allowed; the wait itself draws its jitter below
        that cap. A stream out of reconnects or send slots raises StreamResumeExhaustedError.
        """
        if not isinstance(failure, StreamInterruptedError) or (resume := self._resume) is None:
            return None
        limits, session, client = self._limits, self._session, self._client
        reason: Literal["transport_interruption", "incomplete_eof"] | None = "incomplete_eof"
        if failure.condition == "transport":
            cause = failure.cause
            reason = "transport_interruption" if isinstance(cause, TransportError) and self._retryable(cause) else None
        if not (limits.reconnect and self._cursored and reason in resume.reconnect_on):
            failure.resume_state = self._resumable()
            return None
        if (cap := limits.max_reconnects) is not None and self._reconnects >= cap:
            raise self._exhausted(cap, failure, kind="reconnects")
        if (slots := session.send_limit) is not None and session.network_send_budget_used >= slots:
            raise self._exhausted(slots, failure, kind="network_sends")
        backoff_cap, backoff = client.reconnect_backoff(limits.options, resume.call.operation_id, self._cap)
        retry = (self._frames.retry or 0) / 1000
        if (allowed := limits.max_reconnect_wait) is not None and max(backoff_cap, retry) > allowed:
            failure.resume_state = self._resumable()
            return None
        self._cap = backoff_cap
        return _Reconnect(failure, max(backoff, retry))

    def _waiter(self, reconnect: _Reconnect) -> LogicalCallContext:
        """Return the context the wait before a reopen runs in, failing with the interruption past the deadline.

        A wait that the session's or the call's deadline would cut short is not begun.
        """
        resume = cast("StreamResumePlan", self._resume)
        waiter = self._client.waiting(self._limits.options, self._session, resume.call.operation_id)
        if (remaining := waiter.remaining()) is not None and reconnect.delay >= remaining:
            waiter.finish()
            failure = reconnect.failure
            failure.resume_state = self._resumable()
            raise failure
        return waiter

    def _unencodable(self, error: Exception, failure: StreamInterruptedError | None = None) -> ProtocolDataError:
        """Return the failure of a reopen request that cannot encode the cursor, which keeps no checkpoint.

        It names the cursor's selector, or the target an event ID cursor is written to, and the reopen operation. The
        encoding failure is its cause, and the interruption a reconnection followed is its context.
        """
        resume = cast("StreamResumePlan", self._resume)
        refused = ProtocolDataError(
            condition="value",
            location=resume.cursor or resume.write,
            helper_id=self._plan.helper_id,
            operation=resume.operation,
            operation_id=resume.call.operation_id,
            parent_session_id=self._session.session_id,
            cause=error,
        )
        refused.__context__ = failure
        return refused

    def _exhausted(
        self,
        limit: int,
        failure: StreamInterruptedError,
        refused: BudgetExceededError | None = None,
        *,
        kind: Literal["reconnects", "network_sends"],
    ) -> SessionLimitError:
        """Return the error of a reconnection out of budget, keeping a checkpoint and the interruption or refusal."""
        progress, resume_state = self.progress, self._resumable()
        if refused is not None:
            return _refused(
                self._plan,
                cast("StreamResumePlan", self._resume).operation,
                progress,
                refused,
                error_type=StreamResumeExhaustedError,
                resume_state=resume_state,
            )
        return self._stamped(
            StreamResumeExhaustedError(
                kind=kind, limit=limit, progress=progress, resume_state=resume_state, cause=failure
            )
        )

    def _reopened(self, info: ResponseInfo) -> None:
        """Take a reopen's response: the reopen operation's identity and the bindings' values it gives."""
        resume = cast("StreamResumePlan", self._resume)
        self._bound = _bound(self._plan, resume, resume.operation, info, self._given, self._bound)
        self._operation, self._operation_id = resume.operation, resume.call.operation_id

    def _switched(self, info: ResponseInfo) -> None:
        """Read on from a reopened response: a parser starting where the one before stopped, and the state open."""
        self._info = info
        self._frames = self._framer(self._frames.retry)
        self._state = _State.OPEN

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
        info = self._info
        error.helper_id = self._plan.helper_id
        error.operation = self._operation
        error.info = info
        error.operation_id = self._operation_id
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
        raises StreamDecodeError with neither a cause nor a context, either of which would hold the whole record.
        """
        frames = self._frames
        prefix = b""
        try:
            if last:
                return frames.last()
            if chunk is not None:
                frames.feed(chunk)
            return frames.next()
        except ProtocolSizeError as error:
            raise self._stamped(error) from None
        except UnicodeDecodeError as error:
            prefix = error.object[: self._prefix + 1]
        self._sequence += 1
        raise self._stamped(self._decode_error(prefix, "malformed"))

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
        """Return how a failure ends the stream: a transport failure reading it, but no phase timeout, interrupts it.

        A stream of a helper declaring resumption is also interrupted by a read timeout the call's own read timeout
        set, which it may reconnect after, but never by its idle limit.
        """
        if not isinstance(error, TransportError) or (
            isinstance(error, PhaseTimeoutError) and (self._resume is None or not self._retryable(error))
        ):
            return error
        return self._stamped(StreamInterruptedError(condition="transport", sequence=self._delivered, cause=error))

    def _retryable(self, error: TransportError) -> bool:
        """Return whether a transport failure reading the body is one an automatic reconnection may follow."""
        return self._client.reconnects_after(error, self._limits.options, self._operation_id)

    def _event(self, frame: _Frame) -> StreamEvent[T] | _End:
        """Return a dispatched event decoded, or the stream's end at its terminal event; an error event raises.

        A helper declaring resumption takes the cursor of the event before delivering it.
        """
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
                raise self._stamped(self._decode_error(frame.body, condition, location=selector))
            key = found
        if (error := self._errors.get(key)) is not None:
            data = self._decoded(error, frame, wire)
            raise self._stamped(
                StreamRemoteError(event_type=frame.event_type or None, data=data, sequence=self._sequence)
            )
        if (decoder := plan.event) is None and (decoder := self._routes.get(key)) is None:
            if (unknown := plan.unknown) is None:
                raise self._stamped(self._decode_error(frame.body, "value", location=selector))
            value = unknown(key, frame.data)
        else:
            if wire is None:
                wire = self._wire(frame)
            value = self._decoded(decoder, frame, wire)
        if (resume := self._resume) is not None:
            self._advance(resume, frame, wire)
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
        data = frame.body
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
            raise self._stamped(self._decode_error(frame.body, "value", cause=error)) from None

    def _decode_error(
        self,
        data: bytes,
        condition: Literal["missing", "null", "type", "value", "malformed"],
        *,
        location: BodySelector | None = None,
        cause: BaseException | None = None,
    ) -> StreamDecodeError:
        """Return the decode failure of an event, keeping at most the event limit or 64 KiB of its raw data."""
        limit = self._prefix
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
    or `close()` every step raises ProtocolStateError, and after its end every step stops. A stream of a helper
    declaring resumption reopens itself within a step after an interruption when its call enables reconnection.
    """

    __slots__ = ("_chunks", "_core", "_response")

    def __init__(  # noqa: PLR0913
        self,
        core: ClientCore,
        plan: EventPlan[T],
        limits: _Limits,
        session: OperationSession,
        response: RawResponse,
        *,
        native: bool,
        position: _Position = _START,
        reopened: bool = False,
    ) -> None:
        """Take the open response, whose body is read only as the events need it."""
        super().__init__(
            core, plan, limits, session, response.info, native=native, position=position, reopened=reopened
        )
        self._core = core
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
        """Return the next event, releasing the response once the stream ends or fails.

        After an interruption it reconnects after, it reopens the stream and reads on from the reopened response.
        """
        while True:
            try:
                event = self._read()
            except Exception as error:  # noqa: BLE001
                self._state = _State.FAILED
                failure = self._broken(error)
                self._chunks.close()
                finished(self._response, failure)
                if (reconnect := self._reconnection(failure)) is None:
                    raise failure from None
            except BaseException as error:
                self._state = _State.FAILED
                self._chunks.close()
                finished(self._response, error)
                raise
            else:
                if isinstance(event, _End):
                    self._state = _State.ENDED
                    self._chunks.close()
                    finished(self._response)
                    raise StopIteration
                return event
            self._reopen(reconnect)

    def _reopen(self, reconnect: _Reconnect) -> None:
        """Wait out the delay, then reopen the stream after its cursor as one more child call of its session."""
        if reconnect.delay > 0:
            waiter = self._waiter(reconnect)
            try:
                waiter.sleep_until(waiter.started + reconnect.delay)
            finally:
                waiter.finish()
        self._reconnects += 1
        resume = cast("StreamResumePlan", self._resume)
        request = _reopen_request(resume, self._position())
        try:
            response = _sent(self._core, resume.reopened, request, self._limits, self._session, resume.media)
        except BudgetExceededError as error:
            if error.budget_kind != "parent_network":
                raise
            raise self._exhausted(error.limit, reconnect.failure, error, kind="network_sends") from None
        except _ENCODING_ERRORS as error:
            refused = self._unencodable(error, reconnect.failure)
        else:
            _accepted(response, self._reopened)
            self._response = response
            self._chunks = held(response)
            self._switched(response.info)
            return
        raise refused

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
    `aclose()` every step raises ProtocolStateError, and after its end every step stops. A stream of a helper declaring
    resumption reopens itself within a step after an interruption when its call enables reconnection.
    """

    __slots__ = ("_chunks", "_core", "_response")

    def __init__(  # noqa: PLR0913
        self,
        core: AsyncClientCore,
        plan: EventPlan[T],
        limits: _Limits,
        session: OperationSession,
        response: AsyncRawResponse,
        *,
        native: bool,
        position: _Position = _START,
        reopened: bool = False,
    ) -> None:
        """Take the open response, whose body is read only as the events need it."""
        super().__init__(
            core, plan, limits, session, response.info, native=native, position=position, reopened=reopened
        )
        self._core = core
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
        """Return the next event, releasing the response once the stream ends or fails.

        After an interruption it reconnects after, it reopens the stream and reads on from the reopened response.
        """
        while True:
            try:
                event = await self._read()
            except Exception as error:  # noqa: BLE001
                self._state = _State.FAILED
                failure = self._broken(error)
                await self._chunks.aclose()
                await afinished(self._response, failure)
                if (reconnect := self._reconnection(failure)) is None:
                    raise failure from None
            except BaseException as error:
                self._state = _State.FAILED
                await self._chunks.aclose()
                await afinished(self._response, error)
                raise
            else:
                if isinstance(event, _End):
                    self._state = _State.ENDED
                    await self._chunks.aclose()
                    await afinished(self._response)
                    raise StopAsyncIteration
                return event
            await self._reopen(reconnect)

    async def _reopen(self, reconnect: _Reconnect) -> None:
        """Wait out the delay, then reopen the stream after its cursor as one more child call of its session."""
        if reconnect.delay > 0:
            waiter = self._waiter(reconnect)
            try:
                await waiter.asleep_until(waiter.started + reconnect.delay)
            finally:
                waiter.finish()
        self._reconnects += 1
        resume = cast("StreamResumePlan", self._resume)
        request = _reopen_request(resume, self._position())
        try:
            response = await _asent(self._core, resume.reopened, request, self._limits, self._session, resume.media)
        except BudgetExceededError as error:
            if error.budget_kind != "parent_network":
                raise
            raise self._exhausted(error.limit, reconnect.failure, error, kind="network_sends") from None
        except _ENCODING_ERRORS as error:
            refused = self._unencodable(error, reconnect.failure)
        else:
            await _aaccepted(response, self._reopened)
            self._response = response
            self._chunks = aheld(response)
            self._switched(response.info)
            return
        raise refused

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


def _sent(  # noqa: PLR0913, PLR0917
    core: ClientCore,
    call: OperationPlan[object, object],
    request: _Given,
    limits: _Limits,
    session: OperationSession,
    media: str,
) -> RawResponse:
    """Send an open or reopen of a stream as a child call of its session, returning its declared success response."""
    arguments, body, media_type = request
    return core.execute_raw(
        call,
        arguments,
        body=body,
        media_type=media_type,
        options=limits.options,
        response_media_type=media,
        stream=True,
        session=session,
    )


async def _asent(  # noqa: PLR0913, PLR0917
    core: AsyncClientCore,
    call: OperationPlan[object, object],
    request: _Given,
    limits: _Limits,
    session: OperationSession,
    media: str,
) -> AsyncRawResponse:
    """Send an open or reopen of an asyncio stream as a child call of its session, as `_sent` does."""
    arguments, body, media_type = request
    return await core.execute_raw(
        call,
        arguments,
        body=body,
        media_type=media_type,
        options=limits.options,
        response_media_type=media,
        stream=True,
        session=session,
    )


def _accepted(response: RawResponse, take: Callable[[ResponseInfo], V]) -> V:
    """Return what a stream's response gives before its events, ending the response as failed when that fails."""
    try:
        return take(response.info)
    except BaseException as error:
        finished(response, error)
        raise


async def _aaccepted(response: AsyncRawResponse, take: Callable[[ResponseInfo], V]) -> V:
    """Return what an asyncio stream's response gives before its events, as `_accepted` does."""
    try:
        return take(response.info)
    except BaseException as error:
        await afinished(response, error)
        raise


def _resumed(plan: EventPlan[T], resume: StreamResumePlan, position: _Position, info: ResponseInfo) -> _Position:
    """Return where a resumed stream starts after its reopen response: the bindings' values it gives."""
    return replace(position, bound=_bound(plan, resume, resume.operation, info, position.given, position.bound))


def _resume_error(
    plan: EventPlan[T], condition: Literal["fingerprint", "security", "expired", "malformed"]
) -> ResumeStateError:
    return ResumeStateError(condition=condition, helper_id=plan.helper_id, operation=plan.operation)


def _restored(
    core: ClientCore | AsyncClientCore, plan: EventPlan[T], state: object, limits: _Limits
) -> tuple[StreamResumePlan, _Position]:
    """Return where a checkpoint reopens the stream, refusing a checkpoint that does not fit the helper or the call.

    The checkpoint must be this helper's, made under the security the reopen runs with, and unexpired; a state that does
    not fit the helper, or whose reopen request cannot be encoded, is malformed.
    """
    resume = cast("StreamResumePlan", plan.resume)
    if not isinstance(state, ResumeState):
        raise _invalid(plan, ("state",), "invalid_value")
    helper, security, state_json, payload, expires_at = state_fields(state)
    if helper != plan.fingerprint:
        raise _resume_error(plan, "fingerprint")
    if security != _security(core, resume, limits.options)[0]:
        raise _resume_error(plan, "security")
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise _resume_error(plan, "expired")
    try:
        position = _restore(core, plan, resume, decode_json(state_json), payload, expires_at)
        media_type = None if (given := position.given) is None else given[2]
        try:
            core.checked_page(
                resume.reopened, lambda: (*_reopen_request(resume, position)[:2], None), media_type, limits.options
            )
        except (RequestEncodingError, ProtocolDataError, CodecError):
            raise MalformedStateError from None
    except MalformedStateError:
        raise _resume_error(plan, "malformed") from None
    return resume, position


def _restore(  # noqa: PLR0913, PLR0917
    core: ClientCore | AsyncClientCore,
    plan: EventPlan[T],
    resume: StreamResumePlan,
    state: WireValue,
    payload: bytes,
    expires_at: datetime | None,
) -> _Position:
    """Return the position a checkpoint's decoded state saved, refusing a state that does not fit the helper.

    Only a cursor that can be cleared may be saved as cleared, an event ID cursor is a nonempty string, and literal
    bindings take the plan's value whatever was saved. A saved dot segment a binding writes to a path parameter is
    refused as if a server had just given it, and the caller's first request is built again from its wire values.
    """
    require_state(isinstance(state, Mapping) and frozenset(state) == _STATE and not payload)
    fields = cast("Mapping[str, WireValue]", state)
    cursor, retry = fields["cursor"], fields["retry_ms"]
    if resume.cursor is None:
        require_state(cursor is None or (isinstance(cursor, str) and bool(cursor)))
    else:
        require_state(cursor is not None or resume.null == "clear")
    require_state(retry is None or plan.kind == "sse")
    saved = state_array(fields["bound"])
    require_state(len(saved) == len(resume.bindings))
    bound = tuple(
        value if binding.selector is not None else binding.literal
        for binding, value in zip(resume.bindings, saved, strict=True)
    )
    given: _Given | None = None
    if resume.own:
        from .pagination import resent  # noqa: PLC0415 - Only a resumed stream rebuilds a saved request.

        request = resent(core, plan.call, fields["arguments"], fields["body"])
        given = request.arguments, request.body, request.media_type
    else:
        require_state(fields["arguments"] == () and fields["body"] == ())
    try:
        _fitting(core, resume, fields, bound, cursor)
    except RequestEncodingError:
        raise MalformedStateError from None
    if (selector := _dotted(resume, bound, given)) is not None:
        raise ProtocolDataError(
            condition="value", location=selector, helper_id=plan.helper_id, operation=resume.operation
        )
    return _Position(
        given=given,
        cursor=cursor,
        cursored=True,
        sequence=state_count(fields["sequence"]),
        reconnects=state_count(fields["reconnects"]),
        retry_ms=None if retry is None else state_count(retry, _MAX_RETRY),
        bound=bound,
        expires_at=expires_at,
    )


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
    """Open a helper's stream in a session of its own, returning once its response is a declared success.

    A helper declaring resumption first reads the bindings' values and the server's expiry from the response.
    """
    limits = _limits(core, plan, stream_options, options, session_options)
    native = core.native_responses(limits.options, plan.call.operation_id)
    session = _session(plan, limits)
    given = (arguments, body, media_type)
    try:
        response = _sent(core, plan.call, given, limits, session, plan.media)
    except BudgetExceededError as error:
        if error.budget_kind != "parent_network":
            raise
        raise _refused(plan, plan.operation, _progress(session), error) from None
    position = _START if (resume := plan.resume) is None else _accepted(response, partial(_opened, plan, resume, given))
    return EventStream(core, plan, limits, session, response, native=native, position=position)


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
    """Open a helper's stream with asyncio, returning once its response is a declared success, as `open_events` does."""
    limits = _limits(core, plan, stream_options, options, session_options)
    native = core.native_responses(limits.options, plan.call.operation_id)
    session = _session(plan, limits)
    given = (arguments, body, media_type)
    try:
        response = await _asent(core, plan.call, given, limits, session, plan.media)
    except BudgetExceededError as error:
        if error.budget_kind != "parent_network":
            raise
        raise _refused(plan, plan.operation, _progress(session), error) from None
    position = (
        _START if (resume := plan.resume) is None else await _aaccepted(response, partial(_opened, plan, resume, given))
    )
    return AsyncEventStream(core, plan, limits, session, response, native=native, position=position)


def resume_events(  # noqa: PLR0913
    core: ClientCore,
    plan: EventPlan[T],
    state: object,
    *,
    stream_options: object = None,
    options: object = None,
    session_options: object = None,
) -> EventStream[T]:
    """Reopen a helper's stream after a checkpoint's cursor in a session of its own, checking the checkpoint first.

    It sends the reopen at once, which counts no reconnection, and returns once its response is a declared success;
    sequences and reconnections count on from the checkpoint's.
    """
    limits = _limits(core, plan, stream_options, options, session_options)
    resume, position = _restored(core, plan, state, limits)
    native = core.native_responses(limits.options, plan.call.operation_id)
    session = _session(plan, limits)
    try:
        response = _sent(core, resume.reopened, _reopen_request(resume, position), limits, session, resume.media)
    except BudgetExceededError as error:
        if error.budget_kind != "parent_network":
            raise
        raise _refused(plan, resume.operation, _progress(session, position.reconnects), error) from None
    position = _accepted(response, partial(_resumed, plan, resume, position))
    return EventStream(core, plan, limits, session, response, native=native, position=position, reopened=True)


async def aresume_events(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: EventPlan[T],
    state: object,
    *,
    stream_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncEventStream[T]:
    """Reopen a helper's asyncio stream after a checkpoint's cursor, as `resume_events` does."""
    limits = _limits(core, plan, stream_options, options, session_options)
    resume, position = _restored(core, plan, state, limits)
    native = core.native_responses(limits.options, plan.call.operation_id)
    session = _session(plan, limits)
    request = _reopen_request(resume, position)
    try:
        response = await _asent(core, resume.reopened, request, limits, session, resume.media)
    except BudgetExceededError as error:
        if error.budget_kind != "parent_network":
            raise
        raise _refused(plan, resume.operation, _progress(session, position.reconnects), error) from None
    position = await _aaccepted(response, partial(_resumed, plan, resume, position))
    return AsyncEventStream(core, plan, limits, session, response, native=native, position=position, reopened=True)
