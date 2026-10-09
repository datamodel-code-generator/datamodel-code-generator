"""Server-sent event and NDJSON streams: typed events read through HTTPX2, and the handles that read them.

A stream helper opens its operation as one child call of a session of its own and hands the response to a handle, which
reads only the bytes the next event or record needs: HTTPX2's `EventSource` parses server-sent events, and NDJSON bodies
split at each LF. Native read timeouts govern waits for bytes, and an optional helper session budget limits later reads
and reconnects. A helper whose metadata declares resumption tracks the cursor of the last event it delivered: its
`checkpoint` returns it as plain JSON, its `resume` reopens the stream after it in a session of its own, and an
interruption reopens it as one more child call of the same session when the call enables reconnection.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, Literal, cast, final

import httpx2
from typing_extensions import Self, TypeVar

from ..client.errors import (
    APIConnectionError,
    ConfigurationError,
    DecodeError,
    ProtocolError,
    is_phase_timeout,
    is_transport,
)
from ..client.native import _AsyncHeld, _Held  # pyright: ignore[reportPrivateUsage]
from ..client.options import RequestOptions, TimeoutOptions
from ..client.raw import afinished, aheld, checked, finished, held, native_request
from ..client.timing import SYSTEM_CLOCK, SessionOptions
from ..model_codecs.errors import ParameterEncodingError
from ..model_codecs.media import json_value
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
from .records import plain_copy
from .resume import MalformedStateError, require_state, saved_expiry, state_array, state_expiry
from .values import MISSING, Missing, Patch, RepeatedValueError, resolve, selected, server_expiry, written

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Generator, Iterator
    from datetime import datetime
    from types import TracebackType
    from typing import TypeAlias

    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import InboundModelCodec, OperationPlan
    from ..client.raw import AsyncRawResponse, RawResponse
    from ..client.responses import ResponseInfo
    from ..client.timing import Clock
    from ..model_codecs.media import JSONValue
    from .client import AsyncClientCore, ClientCore
    from .errors import _DataCondition  # pyright: ignore[reportPrivateUsage]
    from .pagination import PageBinding
    from .records import BodySelector, HeaderSelector, ProtocolProgress, RequestTarget, Selector
    from .references import OperationRef
    from .writes import ReadPaths, Writes

    _Given: TypeAlias = tuple[tuple[object, ...], object, str | None]

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

_EVENT_STREAM: Final = "text/event-stream"
_NO_TYPE: Final = ""
_MAX_RETRY: Final = 10**18
_STATE: Final = frozenset({"cursor", "bound", "expires_at"})
_ENCODING_ERRORS: Final = (DecodeError, ParameterEncodingError)


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
    segments of the reopen a binding's read value is written to, which must not encode to a dot segment.
    """

    operation: OperationRef
    call: OperationPlan[object]
    media: str
    write: RequestTarget
    own: bool = False
    cursor: BodySelector | None = None
    missing: Literal["inherit", "error"] = "inherit"
    null: Literal["clear", "error"] = "clear"
    bindings: tuple[PageBinding, ...] = ()
    reconnect_on: tuple[Literal["transport_interruption", "incomplete_eof"], ...] = ("transport_interruption",)
    expires_at: HeaderSelector | None = None
    reopened: OperationPlan[object] = field(init=False)
    writes: Writes = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)
    dotted: ReadPaths = field(init=False)

    def __post_init__(self) -> None:
        """Derive the operation of a reopen taking the bindings' values and then the cursor as wire values.

        The header and query parameters it writes are those a call's options must not patch.
        """
        from .writes import targeted_writes  # noqa: PLC0415 - Only a plan loads the operation runtime.

        sources = tuple((binding.target, binding.selector) for binding in self.bindings)
        targeted = targeted_writes(self.call, sources, (self.write,))
        reopened, writes, headers, queries = targeted.call, targeted.writes, targeted.headers, targeted.queries
        object.__setattr__(self, "reopened", reopened)
        object.__setattr__(self, "writes", writes)
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "queries", queries)
        object.__setattr__(self, "dotted", targeted.dotted)


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
    call: OperationPlan[object]
    media: str
    event: InboundModelCodec[T] | None = None
    routes: tuple[tuple[str, InboundModelCodec[T]], ...] = ()
    discriminator: BodySelector | None = None
    unknown: Callable[[str, str], T] | None = None
    errors: tuple[tuple[str, InboundModelCodec[object]], ...] = ()
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


def _view(
    response: RawResponse | AsyncRawResponse, stream: httpx2.SyncByteStream | httpx2.AsyncByteStream
) -> httpx2.Response:
    """Return a native response over a stream's decoded body, which HTTPX2's event source reads as UTF-8 text.

    Its leading byte order mark is skipped, and invalid UTF-8 decodes to replacement characters, whatever charset the
    response declares.
    """
    view = httpx2.Response(
        response.info.status_code,
        headers={"content-type": _EVENT_STREAM},
        stream=stream,
        request=native_request(response),
    )
    view.encoding = "utf-8-sig"
    return view


class _Lines:
    """Split an NDJSON body at each LF, keeping the bytes after the last one until a later chunk ends their line."""

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        """Start before the first byte."""
        self._parts: list[bytes] = []

    def split(self, chunk: bytes) -> list[bytes]:
        """Return the lines a chunk ends, each without its LF."""
        if b"\n" not in chunk:
            self._parts.append(chunk)
            return []
        ended = chunk.split(b"\n")
        parts, self._parts = self._parts, [rest] if (rest := ended.pop()) else []
        if parts:
            ended[0] = b"".join((*parts, ended[0]))
        return ended

    def rest(self) -> bytes:
        """Return the bytes after the last LF."""
        return b"".join(self._parts)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective limits of one stream, each from the first layer that sets it; None removes a limit."""

    idle_timeout: float | Unset | None = UNSET
    reconnect: bool = False
    max_reconnects: int | None = 5
    max_reconnect_wait: float | None = 60.0
    total_timeout: float | None = None
    options: RequestOptions | None = None
    clock: Clock = SYSTEM_CLOCK


_DEFAULTS: Final = _Limits()


def _invalid(
    plan: EventPlan[T], path: tuple[str, ...], condition: Literal["invalid_value", "missing_metadata"]
) -> ConfigurationError:
    return ConfigurationError(field_path=path, reason=condition, helper_id=plan.helper_id, operation=plan.operation)


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
        request = request or RequestOptions()
        timeout = request.timeout
        if not isinstance(timeout, TimeoutOptions):
            timeout = TimeoutOptions(connect=None, write=None, pool=None) if timeout is None else TimeoutOptions()
        request = replace(request, timeout=replace(timeout, read=idle))
    return _Limits(
        reconnect=reconnect,
        max_reconnects=layered(kinds, "max_reconnects", _DEFAULTS.max_reconnects),
        max_reconnect_wait=layered(kinds, "max_reconnect_wait", _DEFAULTS.max_reconnect_wait),
        total_timeout=layered(sessions, "total_timeout", _DEFAULTS.total_timeout),
        options=request,
        clock=core.clock,
    )


def _unpatched(
    core: ClientCore | AsyncClientCore, plan: EventPlan[T], resume: StreamResumePlan, request: RequestOptions | None
) -> None:
    """Refuse effective options fixing an idempotency key or patching a header or query parameter a reopen writes.

    The effective options are the client's, a view's, and the call's own, each of which patches every reopen.
    """
    from .writes import query_written  # noqa: PLC0415 - Only resume metadata needs the write inventory.

    if core.fixes_key(request):
        raise _invalid(plan, ("options", "idempotency_key"), "invalid_value")
    headers, queries = core.patches(request)
    for patch in headers:
        for name, _ in patch:
            if name.lower() in resume.headers:
                raise _invalid(plan, ("options", "headers", name), "invalid_value")
    for patch in queries:
        for name, _ in patch:
            if query_written(resume.call, resume.writes, name):
                raise _invalid(plan, ("options", "query", name), "invalid_value")


def _progress(reconnects: int = 0) -> ProtocolProgress:
    return MappingProxyType({
        "reconnects": reconnects,
    })


def _session(limits: _Limits) -> OperationSession:
    """Start the stream's session."""
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only an open loads the call runtime.

    return OperationSession(total_timeout=limits.total_timeout, clock=limits.clock)


@dataclass(frozen=True, slots=True)
class _Position:
    """Where a stream of a helper declaring resumption starts: its request, cursor, counts, and the values it keeps.

    `given` is the caller's first request a reopen of the helper's own operation repeats. `cursored` tells that some
    delivered event gave a cursor, `cursor` is the last one, None once cleared, and `bound` the bindings' values.
    """

    given: _Given | None = None
    cursor: JSONValue = None
    cursored: bool = False
    sequence: int = 0
    reconnects: int = 0
    bound: tuple[JSONValue, ...] = ()
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


def _dotted(resume: StreamResumePlan, bound: tuple[JSONValue, ...], given: _Given | None) -> Selector | None:
    """Return the selector of a read value that makes a path segment of the reopen a dot segment once encoded, or None.

    The caller's own path arguments in the segment, which only a reopen of the helper's own operation repeats, are
    encoded again by their parameters' codecs, without validation.
    """
    from .writes import dotted_read  # noqa: PLC0415 - A plan loaded the operation runtime.

    parameters = resume.call.parameters

    def callers() -> dict[str, str]:
        arguments = () if given is None else given[0]
        return {
            name: parameters[position].path_text(parameters[position].dump(arguments[position]))
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
    kept: tuple[JSONValue, ...] = (),
) -> tuple[JSONValue, ...]:
    """Return the values the bindings write: their literals, kept `initial` values, and what a response gives.

    A stream response's header or status gives a value; a missing one, a header repeated where one is read, and read
    values that make a path segment of the reopen a dot segment once encoded are refused.
    """
    values: list[JSONValue] = []
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


def _json(frame: _Frame) -> JSONValue | Missing:
    """Return an event's data parsed as JSON, or MISSING when it is not JSON.

    `int` refuses the NaN and infinity constants, which a saved cursor could not hold.
    """
    try:
        return cast("JSONValue", json.loads(frame.body, parse_constant=int))
    except (ValueError, RecursionError):
        return MISSING


def _absence(value: JSONValue | Missing) -> Literal["missing", "null"]:
    return "missing" if value is MISSING else "null"


def _expiry(plan: EventPlan[T], read: HeaderSelector, info: ResponseInfo, clock: Clock) -> datetime:
    """Return the server's expiry an open response's header gives, an RFC 3339 date-time with offset or an HTTP date.

    A value that is no string, which only a selector of every occurrence would read, is refused like an unreadable one.
    """
    try:
        value = selected(read, None, info)
    except RepeatedValueError:
        raise _data_error(plan, plan.operation, info, "malformed", read) from None
    if value is MISSING:
        raise _data_error(plan, plan.operation, info, "missing", read)
    if (expires_at := server_expiry(value, clock.time()) if isinstance(value, str) else None) is None:
        raise _data_error(plan, plan.operation, info, "value", read)
    return expires_at


def _opened(plan: EventPlan[T], resume: StreamResumePlan, given: _Given, clock: Clock, info: ResponseInfo) -> _Position:
    """Return where a stream starts after its open response: the bindings' values and the server's expiry it gives."""
    kept = given if resume.own else None
    return _Position(
        given=kept,
        bound=_bound(plan, resume, plan.operation, info, kept),
        expires_at=None if (read := resume.expires_at) is None else _expiry(plan, read, info, clock),
    )


def _reopen_request(
    core: ClientCore | AsyncClientCore, plan: EventPlan[T], resume: StreamResumePlan, position: _Position
) -> _Given:
    """Return the arguments, body, and media type of a reopen: each binding's value and then the cursor written.

    A reopen of the helper's own operation writes them into the caller's first request, and another operation's
    request takes nothing else. A cleared cursor's parameter is omitted.
    """
    sent = _written(resume, (UNSET,) * len(resume.call.parameters), UNSET, position.bound, position.cursor)[0]
    if (unsaved := core.unsaved_argument(resume.call, _wires(sent))) is not None:
        raise ConfigurationError(
            field_path=("arguments", *unsaved),
            reason="wrong_capability",
            helper_id=plan.helper_id,
            operation=resume.operation,
            operation_id=resume.call.operation_id,
        )
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
    bound: tuple[JSONValue, ...],
    cursor: JSONValue,
) -> tuple[tuple[object, ...], object]:
    """Return a request's arguments and body with each binding's value and then the cursor written.

    A cleared cursor's parameter is omitted.
    """
    writes = resume.writes
    if cursor is not None:
        return written(writes, arguments, body, (*bound, cursor))
    cleared = cast("int", writes[-1][0])
    return written(writes[:-1], (*arguments[:cleared], UNSET, *arguments[cleared + 1 :]), body, bound)


def _wire(value: object) -> JSONValue:
    """Return a value written by wire value with its writes applied, or the value itself."""
    return value.applied(plain_copy) if isinstance(value, Patch) else cast("JSONValue", value)


def _wires(arguments: tuple[object, ...]) -> tuple[JSONValue | Unset, ...]:
    """Return arguments written by wire value with their writes applied."""
    return cast("tuple[JSONValue | Unset, ...]", tuple(map(_wire, arguments)))


class _State(Enum):
    OPEN = "open"
    ENDED = "ended"
    FAILED = "failed"
    CLOSED = "closed"


class _End(Enum):
    END = "end"


_ENDED: Final = _End.END


class _Events(Generic[T]):
    """What the synchronous and asyncio streams share: the plan's decoding, the state, and the counts.

    The plan's kind chooses the reader: HTTPX2's event source for SSE, or the line splitter for NDJSON. An SSE stream
    keeps the last valid reconnection time, and a stream of a helper declaring resumption also keeps the cursor of the
    last event it delivered, the bindings' values, and the caller's first request a reopen repeats; others keep none of
    them, and each event costs them nothing more.
    """

    __slots__ = (
        "_bound",
        "_cap",
        "_carried",
        "_client",
        "_cursor",
        "_cursored",
        "_delivered",
        "_errors",
        "_expires_at",
        "_given",
        "_identified",
        "_info",
        "_limits",
        "_lock",
        "_operation",
        "_operation_id",
        "_plan",
        "_reconnects",
        "_resume",
        "_retry",
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
        self._routes: Mapping[str, InboundModelCodec[T]] = dict(plan.routes)
        self._errors: Mapping[str, InboundModelCodec[object]] = dict(plan.errors)
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
        self._retry: int | None = None
        source = resume if reopened and resume is not None else plan
        self._operation, self._operation_id = source.operation, source.call.operation_id
        self._carried: str | None = None
        self._identified = False
        self._carry()

    @property
    def response(self) -> ResponseInfo:
        """Return the metadata of the stream's response, the latest reopen's once it reconnected."""
        return self._info

    @property
    def progress(self) -> ProtocolProgress:
        """Return the reconnections so far, none without resume metadata, and the session's sends."""
        return _progress(self._reconnects)

    def checkpoint(self) -> JSONValue:
        """Return the cursor `resume` reopens the stream after, as plain JSON, sending nothing.

        It holds the cursor of the last event delivered, the bindings' values, and the server's expiry, but never
        events, counts, the caller's arguments, the response, the session, or the call's options. A stream that ended,
        failed, or closed checkpoints as it stood; one running a step refuses with ProtocolStateError, and so does one
        that delivered no cursor yet.
        """
        action = "checkpoint"
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, "receiving")
        try:
            if self._resume is None:
                raise _invalid(self._plan, ("resume",), "missing_metadata")
            if not self._cursored:
                raise self._state_error(action, "uncursored")
            return plain_copy({
                "cursor": self._cursor,
                "bound": self._bound,
                "expires_at": saved_expiry(self._expires_at),
            })
        finally:
            self._lock.release()

    def _carry(self) -> None:
        """Start a response's events at the stream's event ID cursor, the last event ID a reopen sends."""
        resume, cursor = self._resume, self._cursor
        self._carried = cursor if resume is not None and resume.cursor is None and isinstance(cursor, str) else None
        self._identified = False

    def _dispatched(self, event: httpx2.ServerSentEvent) -> _Frame | None:
        """Return the frame of an event HTTPX2 dispatched, or None for one without data.

        An event without data still sets the reconnection time and the last event ID. A `retry` that is negative or of
        more than 18 digits is ignored. Until a response sets an event ID, its events keep the one the stream reopened
        after.
        """
        if (retry := event.retry) is not None and 0 <= retry < _MAX_RETRY:
            self._retry = retry
        if event.id:
            self._identified = True
        if not event.data:
            return None
        event_id = event.id or (None if self._identified else self._carried)
        return _Frame(event.data, event.event, event_id, self._retry)

    def _record(self, line: bytes) -> _Frame:
        """Return the record of an NDJSON line, without the CR of a CRLF, refusing one that is not UTF-8.

        The refusal has neither a cause nor a context, either of which would hold the whole record.
        """
        if line.endswith(b"\r"):
            line = line[:-1]
        try:
            return _Frame(line.decode(), _NO_TYPE, None, None, line)
        except UnicodeDecodeError:
            pass
        self._sequence += 1
        raise self._stamped(self._decode_error(line, "malformed"))

    def _last(self, lines: _Lines) -> _Frame | None:
        """Return the record of the bytes after the last line end, when the helper allows one, or None for none.

        Bytes there that the helper does not allow raise IncompleteFrameError.
        """
        if not (rest := lines.rest()):
            return None
        if self._plan.final_line != "allow_eof":
            raise self._stamped(IncompleteFrameError(buffered_bytes=len(rest), sequence=self._delivered))
        return self._record(rest)

    def _position(self) -> _Position:
        """Return where the stream stands, which its next reopen continues from."""
        return _Position(given=self._given, cursor=self._cursor, bound=self._bound)

    def _advance(self, resume: StreamResumePlan, frame: _Frame, wire: JSONValue | None) -> None:
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
        whose longest wait, the retry backoff's cap and at least the last reconnection time, is longer than allowed
        does not; the wait itself draws its jitter below that cap. A stream out of reconnects raises
        StreamResumeExhaustedError.
        """
        if not isinstance(failure, StreamInterruptedError) or (resume := self._resume) is None:
            return None
        limits, client = self._limits, self._client
        reason: Literal["transport_interruption", "incomplete_eof"] | None = "incomplete_eof"
        if failure.condition == "transport":
            cause = failure.cause
            reason = "transport_interruption" if is_transport(cause) and self._retryable(cause) else None
        if not (limits.reconnect and self._cursored and reason in resume.reconnect_on):
            return None
        if (cap := limits.max_reconnects) is not None and self._reconnects >= cap:
            raise self._exhausted(cap, failure)
        backoff_cap, backoff = client.reconnect_backoff(limits.options, resume.call.operation_id, self._cap)
        retry = (self._retry or 0) / 1000
        if (allowed := limits.max_reconnect_wait) is not None and max(backoff_cap, retry) > allowed:
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
            raise reconnect.failure
        return waiter

    def _unencodable(self, error: Exception, failure: StreamInterruptedError) -> ProtocolDataError:
        """Return the failure of a reopen request that cannot encode the cursor.

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

    def _exhausted(self, limit: int, failure: StreamInterruptedError) -> SessionLimitError:
        """Return the error of a reconnection out of reconnects, keeping the interruption."""
        return self._stamped(
            StreamResumeExhaustedError(kind="reconnects", limit=limit, progress=self.progress, cause=failure)
        )

    def _reopened(self, info: ResponseInfo) -> None:
        """Take a reopen's response: the reopen operation's identity and the bindings' values it gives."""
        resume = cast("StreamResumePlan", self._resume)
        self._bound = _bound(self._plan, resume, resume.operation, info, self._given, self._bound)
        self._operation, self._operation_id = resume.operation, resume.call.operation_id

    def _switched(self, info: ResponseInfo) -> None:
        """Read on from a reopened response, its events starting at the event ID cursor, and the state open."""
        self._info = info
        self._carry()
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
        """Give a failure of the stream the helper's context, the stream call's identity, and its measurements."""
        info = self._info
        error.helper_id = self._plan.helper_id
        error.operation = self._operation
        error.info = info
        error.operation_id = self._operation_id
        error.call_id = info.call_id
        error.parent_session_id = self._session.session_id
        error.attempt_count, error.elapsed = info.attempt_count, info.elapsed
        return error

    def _oversized(self, error: httpx2.SSEError) -> ProtocolDataError:
        """Return the failure of an event over HTTPX2's event size limit, which keeps the native error as its cause."""
        return self._stamped(ProtocolDataError(condition="malformed", cause=error))

    def _ended(self) -> _End:
        """Return the end of a response that ended the stream as declared, raising the failure of one that did not."""
        if self._plan.completion != "eof":
            raise self._stamped(StreamInterruptedError(condition="eof", sequence=self._delivered))
        return _ENDED

    def _broken(self, error: BaseException) -> BaseException:
        """Return how a failure ends the stream: a transport failure reading it, but no phase timeout, interrupts it.

        A stream of a helper declaring resumption is also interrupted by a read timeout the call's own read timeout
        set, which it may reconnect after, but never by its idle limit.
        """
        if isinstance(error, StreamDecodeError):
            return error.with_traceback(None)
        if not is_transport(error) or (
            is_phase_timeout(error) and (self._resume is None or not self._retryable(error))
        ):
            return error
        return self._stamped(StreamInterruptedError(condition="transport", sequence=self._delivered, cause=error))

    def _retryable(self, error: APIConnectionError) -> bool:
        """Return whether a transport failure reading the body is one an automatic reconnection may follow."""
        return self._client.reconnects_after(error)

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
        wire: JSONValue | None = None
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
            data = self._decoded(error, frame)
            raise self._stamped(
                StreamRemoteError(event_type=frame.event_type or None, data=data, sequence=self._sequence)
            )
        if (decoder := plan.event) is None and (decoder := self._routes.get(key)) is None:
            if (unknown := plan.unknown) is None:
                raise self._stamped(self._decode_error(frame.body, "value", location=selector))
            value = unknown(key, frame.data)
        else:
            value = self._decoded(decoder, frame)
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

    def _wire(self, frame: _Frame) -> JSONValue:
        """Return an event's data parsed as JSON, raising StreamDecodeError for data that does not parse."""
        data = frame.body
        try:
            return json_value(data)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            raise self._stamped(self._decode_error(data, "malformed", cause=error)) from None

    def _decoded(self, codec: InboundModelCodec[U], frame: _Frame) -> U:
        """Return an event's data decoded by its codec, raising StreamDecodeError for data it refuses."""
        try:
            return codec.decode(frame.body)
        except codec.errors as error:
            condition: Literal["value", "malformed"] = "malformed" if codec.malformed(error) else "value"
            raise self._stamped(self._decode_error(frame.body, condition, cause=error)) from None

    def _decode_error(
        self,
        data: bytes,
        condition: Literal["missing", "null", "type", "value", "malformed"],
        *,
        location: BodySelector | None = None,
        cause: BaseException | None = None,
    ) -> StreamDecodeError:
        """Return the decode failure of an event, keeping at most 64 KiB of its raw data."""
        limit = MAX_RAW_PREFIX
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

    __slots__ = ("_chunks", "_core", "_frames", "_response")

    def __init__(  # noqa: PLR0913
        self,
        core: ClientCore,
        plan: EventPlan[T],
        limits: _Limits,
        session: OperationSession,
        response: RawResponse,
        *,
        position: _Position = _START,
        reopened: bool = False,
    ) -> None:
        """Take the open response, whose body is read only as the events need it."""
        super().__init__(core, plan, limits, session, response.info, position=position, reopened=reopened)
        self._core = core
        self._held(response)

    def _held(self, response: RawResponse) -> None:
        """Read the events of a response from now on."""
        self._response = response
        self._chunks = chunks = held(response)
        self._frames = self._sse(response, chunks) if self._plan.kind == "sse" else self._ndjson(chunks)

    def _sse(self, response: RawResponse, chunks: Iterator[bytes]) -> Generator[_Frame, None, None]:
        for event in httpx2.EventSource(_view(response, _Held(chunks))):
            if (frame := self._dispatched(event)) is not None:
                yield frame

    def _ndjson(self, chunks: Iterator[bytes]) -> Generator[_Frame, None, None]:
        lines = _Lines()
        for chunk in chunks:
            for line in lines.split(chunk):
                yield self._record(line)
        if (frame := self._last(lines)) is not None:
            yield frame

    def _release(self, error: BaseException | None = None) -> None:
        """Stop reading the response and end it, as read or as failed with an error."""
        self._frames.close()
        self._chunks.close()
        finished(self._response, error)

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
                self._release(failure)
                if (reconnect := self._reconnection(failure)) is None:
                    raise failure from None
            except BaseException as error:
                self._state = _State.FAILED
                self._release(error)
                raise
            else:
                if isinstance(event, _End):
                    self._state = _State.ENDED
                    self._release()
                    raise StopIteration
                return event
            self._reopen(reconnect)

    def _reopen(self, reconnect: _Reconnect) -> None:
        """Wait out the delay, then reopen the stream after its cursor as one more child call of its session."""
        if reconnect.delay > 0:
            waiter = self._waiter(reconnect)
            waiter.sleep_until(waiter.started + reconnect.delay)
        self._reconnects += 1
        resume = cast("StreamResumePlan", self._resume)
        request = _reopen_request(self._client, self._plan, resume, self._position())
        try:
            response = _sent(self._core, resume.reopened, request, self._limits, self._session, resume.media)
        except _ENCODING_ERRORS as error:
            if isinstance(error, DecodeError) and error.direction != "request":
                raise
            refused = self._unencodable(error, reconnect.failure)
        else:
            _accepted(response, self._reopened)
            self._held(response)
            self._switched(response.info)
            return
        raise refused

    def _read(self) -> StreamEvent[T] | _End:
        checked(self._response)
        try:
            frame = next(self._frames, None)
        except httpx2.SSEError as error:
            raise self._oversized(error) from None
        return self._ended() if frame is None else self._event(frame)

    def close(self) -> None:
        """Release the response; later steps raise ProtocolStateError, and closing again does nothing."""
        if self._closing("close"):
            self._frames.close()
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

    __slots__ = ("_chunks", "_core", "_frames", "_response")

    def __init__(  # noqa: PLR0913
        self,
        core: AsyncClientCore,
        plan: EventPlan[T],
        limits: _Limits,
        session: OperationSession,
        response: AsyncRawResponse,
        *,
        position: _Position = _START,
        reopened: bool = False,
    ) -> None:
        """Take the open response, whose body is read only as the events need it."""
        super().__init__(core, plan, limits, session, response.info, position=position, reopened=reopened)
        self._core = core
        self._held(response)

    def _held(self, response: AsyncRawResponse) -> None:
        """Read the events of a response from now on."""
        self._response = response
        self._chunks = chunks = aheld(response)
        self._frames = self._sse(response, chunks) if self._plan.kind == "sse" else self._ndjson(chunks)

    async def _sse(self, response: AsyncRawResponse, chunks: AsyncIterator[bytes]) -> AsyncGenerator[_Frame, None]:
        async for event in httpx2.EventSource(_view(response, _AsyncHeld(chunks))):
            if (frame := self._dispatched(event)) is not None:
                yield frame

    async def _ndjson(self, chunks: AsyncIterator[bytes]) -> AsyncGenerator[_Frame, None]:
        lines = _Lines()
        async for chunk in chunks:
            for line in lines.split(chunk):
                yield self._record(line)
        if (frame := self._last(lines)) is not None:
            yield frame

    async def _release(self, error: BaseException | None = None) -> None:
        """Stop reading the response and end it, as read or as failed with an error."""
        await self._frames.aclose()
        await self._chunks.aclose()
        await afinished(self._response, error)

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
                await self._release(failure)
                if (reconnect := self._reconnection(failure)) is None:
                    raise failure from None
            except BaseException as error:
                self._state = _State.FAILED
                await self._release(error)
                raise
            else:
                if isinstance(event, _End):
                    self._state = _State.ENDED
                    await self._release()
                    raise StopAsyncIteration
                return event
            await self._reopen(reconnect)

    async def _reopen(self, reconnect: _Reconnect) -> None:
        """Wait out the delay, then reopen the stream after its cursor as one more child call of its session."""
        if reconnect.delay > 0:
            waiter = self._waiter(reconnect)
            await waiter.asleep_until(waiter.started + reconnect.delay)
        self._reconnects += 1
        resume = cast("StreamResumePlan", self._resume)
        request = _reopen_request(self._client, self._plan, resume, self._position())
        try:
            response = await _asent(self._core, resume.reopened, request, self._limits, self._session, resume.media)
        except _ENCODING_ERRORS as error:
            if isinstance(error, DecodeError) and error.direction != "request":
                raise
            refused = self._unencodable(error, reconnect.failure)
        else:
            await _aaccepted(response, self._reopened)
            self._held(response)
            self._switched(response.info)
            return
        raise refused

    async def _read(self) -> StreamEvent[T] | _End:
        checked(self._response)
        try:
            frame = await anext(self._frames, None)
        except httpx2.SSEError as error:
            raise self._oversized(error) from None
        return self._ended() if frame is None else self._event(frame)

    async def aclose(self) -> None:
        """Release the response; later steps raise ProtocolStateError, and closing again does nothing."""
        if self._closing("aclose"):
            await self._frames.aclose()
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
    call: OperationPlan[object],
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
        _call=core.stream_call(call, limits.options, session),
    )


async def _asent(  # noqa: PLR0913, PLR0917
    core: AsyncClientCore,
    call: OperationPlan[object],
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
        _call=core.stream_call(call, limits.options, session),
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


def _restored(plan: EventPlan[T], state: object, given: _Given, limits: _Limits) -> tuple[StreamResumePlan, _Position]:
    """Return where a checkpoint reopens the stream, without sending.

    A checkpoint that is not JSON or does not fit the helper raises ConfigurationError, and one past the server's
    expiry by the client's wall clock ResumeStateError. A reopen of the helper's own operation repeats the request the
    caller gives again.
    """
    resume = cast("StreamResumePlan", plan.resume)
    try:
        position = _restore(plan, resume, plain_copy(state), given if resume.own else None)
    except (MalformedStateError, TypeError, ValueError):
        raise _invalid(plan, ("state",), "invalid_value") from None
    if (expires_at := position.expires_at) is not None and expires_at.timestamp() <= limits.clock.time():
        raise ResumeStateError(condition="expired", helper_id=plan.helper_id, operation=plan.operation)
    return resume, position


def _restore(plan: EventPlan[T], resume: StreamResumePlan, state: JSONValue, given: _Given | None) -> _Position:
    """Return the position a checkpoint's state saved, refusing a state that does not fit the helper.

    Only a cursor that can be cleared may be saved as cleared, an event ID cursor is a nonempty string, and literal
    bindings take the plan's value whatever was saved. A saved dot segment a binding writes to a path parameter is
    refused as if a server had just given it.
    """
    require_state(isinstance(state, Mapping) and frozenset(state) == _STATE)
    fields = cast("Mapping[str, JSONValue]", state)
    cursor = fields["cursor"]
    if resume.cursor is None:
        require_state(cursor is None or (isinstance(cursor, str) and bool(cursor)))
    else:
        require_state(cursor is not None or resume.null == "clear")
    expires_at = state_expiry(fields["expires_at"])
    saved = state_array(fields["bound"])
    require_state(len(saved) == len(resume.bindings))
    bound = tuple(
        value if binding.selector is not None else binding.literal
        for binding, value in zip(resume.bindings, saved, strict=True)
    )
    if (selector := _dotted(resume, bound, given)) is not None:
        raise ProtocolDataError(
            condition="value", location=selector, helper_id=plan.helper_id, operation=resume.operation
        )
    return _Position(given=given, cursor=cursor, cursored=True, bound=bound, expires_at=expires_at)


def open_events(  # noqa: PLR0913
    core: ClientCore,
    plan: EventPlan[T],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | None = None,
    stream_options: object = None,
    options: object = None,
    session_options: object = None,
) -> EventStream[T]:
    """Open a helper's stream in a session of its own, returning once its response is a declared success.

    A helper declaring resumption first reads the bindings' values and the server's expiry from the response.
    """
    limits = _limits(core, plan, stream_options, options, session_options)
    session = _session(limits)
    given = (arguments, body, media_type)
    response = _sent(core, plan.call, given, limits, session, plan.media)
    position = (
        _START
        if (resume := plan.resume) is None
        else _accepted(response, partial(_opened, plan, resume, given, limits.clock))
    )
    return EventStream(core, plan, limits, session, response, position=position)


async def aopen_events(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: EventPlan[T],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | None = None,
    stream_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncEventStream[T]:
    """Open a helper's stream with asyncio, returning once its response is a declared success, as `open_events` does."""
    limits = _limits(core, plan, stream_options, options, session_options)
    session = _session(limits)
    given = (arguments, body, media_type)
    response = await _asent(core, plan.call, given, limits, session, plan.media)
    position = (
        _START
        if (resume := plan.resume) is None
        else await _aaccepted(response, partial(_opened, plan, resume, given, limits.clock))
    )
    return AsyncEventStream(core, plan, limits, session, response, position=position)


def resume_events(  # noqa: PLR0913
    core: ClientCore,
    plan: EventPlan[T],
    state: object,
    arguments: tuple[object, ...] = (),
    *,
    body: object = UNSET,
    media_type: str | None = None,
    stream_options: object = None,
    options: object = None,
    session_options: object = None,
) -> EventStream[T]:
    """Reopen a helper's stream after a checkpoint's cursor in a session of its own, checking the checkpoint first.

    It sends the reopen at once, which counts no reconnection, and returns once its response is a declared success;
    sequences and reconnections count afresh. A reopen of the helper's own operation sends the caller's arguments and
    body again.
    """
    limits = _limits(core, plan, stream_options, options, session_options)
    resume, position = _restored(plan, state, (arguments, body, media_type), limits)
    request = _reopen_request(core, plan, resume, position)
    session = _session(limits)
    response = _sent(core, resume.reopened, request, limits, session, resume.media)
    position = _accepted(response, partial(_resumed, plan, resume, position))
    return EventStream(core, plan, limits, session, response, position=position, reopened=True)


async def aresume_events(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: EventPlan[T],
    state: object,
    arguments: tuple[object, ...] = (),
    *,
    body: object = UNSET,
    media_type: str | None = None,
    stream_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncEventStream[T]:
    """Reopen a helper's asyncio stream after a checkpoint's cursor, as `resume_events` does."""
    limits = _limits(core, plan, stream_options, options, session_options)
    resume, position = _restored(plan, state, (arguments, body, media_type), limits)
    request = _reopen_request(core, plan, resume, position)
    session = _session(limits)
    response = await _asent(core, resume.reopened, request, limits, session, resume.media)
    position = await _aaccepted(response, partial(_resumed, plan, resume, position))
    return AsyncEventStream(core, plan, limits, session, response, position=position, reopened=True)
