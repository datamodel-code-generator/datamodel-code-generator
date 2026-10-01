"""Resumable uploads of the offset profile: their creation, the handles that append chunks, checkpoints, and resume.

A helper's `start` scans the source, creates the upload in one child call, and returns a handle. Each `advance`
appends the chunk holding the confirmed offset, read again and checked against the digest the scan recorded, and an
append whose outcome is unknown is settled by probing the server's offset instead of sending it again blindly.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from functools import partial
from hashlib import sha256
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, cast, final

from typing_extensions import Self, TypeVar

from ..client.errors import (
    BudgetExceededError,
    DeliveryState,
    HTTPStatusError,
    ProtocolConfigurationError,
    ProtocolSizeError,
    SDKError,
    TransportError,
    UnexpectedStatusError,
)
from ..client.options import RequestOptions
from ..client.timing import SessionOptions
from ..model_codecs.errors import CodecError
from ..model_codecs.media import decode_json
from ..model_codecs.unset import UNSET
from .errors import (
    NonResumableSourceError,
    ProtocolDataError,
    ProtocolStateError,
    SessionLimitError,
    UploadDeliveryUnknownError,
    UploadExpiredError,
    UploadOffsetError,
    UploadSourceChangedError,
)
from .options import UploadOptions, layered
from .records import HeaderSelector, canonical_json
from .resume import (
    MAX_STATE_BYTES,
    MalformedStateError,
    ResumeState,
    ResumeStateError,
    helper_state,
    require_state,
    state_array,
    state_count,
    state_fields,
    state_text,
)
from .sources import UploadIdentity, UploadProgress
from .values import MISSING, RepeatedValueError, selected, server_expiry

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import OperationPlan
    from ..client.responses import ResponseInfo
    from ..client.timing import Deadline
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .pagination import PageBinding
    from .polling import _Targeted  # pyright: ignore[reportPrivateUsage]
    from .records import ParameterTarget, ProtocolProgress, RequestTarget, Selector
    from .references import OperationRef
    from .sources import AsyncRangeReader, AsyncUploadSource, RangeReader, UploadSource

__all__ = (
    "AsyncUploadHandle",
    "UploadHandle",
    "UploadPlan",
    "aresume_upload",
    "astart_upload",
    "resume_upload",
    "start_upload",
)

T = TypeVar("T")
C = TypeVar("C")

_READ: Final = 65536
_DIGEST: Final = 32
_UNKNOWN: Final = frozenset({DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED})
_STATE: Final = frozenset({"size", "sha256", "chunk", "confirmed", "phase", "delivery", "bound", "result"})
_RESULT_FIELDS: Final = 2
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299
_BOUND_FIELDS: Final = 3


class _Phase(Enum):
    UPLOADING = "uploading"
    UNKNOWN = "unknown"
    COMPLETE = "complete"


_PHASES: Final = MappingProxyType({phase.value: phase for phase in _Phase})


def _position(call: OperationPlan[Any, object], target: ParameterTarget) -> int:
    """Return the argument position of the parameter a target names, matching a header's name without case."""
    from .writes import _position as position  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    return position(call, target.location, target.name)


def _targeted(
    call: OperationPlan[T, object], bindings: tuple[PageBinding, ...], extra: tuple[RequestTarget, ...] = ()
) -> _Targeted[T]:
    """Return an operation taking the bindings' values and then a value for each extra target, as wire values."""
    from .polling import _Targeted  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]
    from .writes import read_paths, targeted  # noqa: PLC0415 - Only a plan loads the operation runtime.

    sources = (*((binding.target, binding.selector) for binding in bindings), *((target, None) for target in extra))
    return _Targeted(*targeted(call, (target for target, _ in sources)), read_paths(call, sources))


def _unreplayed(call: OperationPlan[T, object]) -> OperationPlan[T, object]:
    """Return an append that shared retries never send again once it may have been delivered.

    An operation retried only while it was proven unsent stays as declared; any other is never retried, so an unknown
    outcome is settled by probing the server's offset.
    """
    from dataclasses import replace  # noqa: PLC0415

    from ..client.retry import replay_safe  # noqa: PLC0415

    if call.idempotency is None and not replay_safe(call.method, call.retry_safety, None, None, now=0.0):
        return call
    return replace(call, retry_safety="never")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class UploadPlan(Generic[T, C]):
    """Everything fixed about one generated upload helper of the offset profile.

    `create` starts the upload, writing the content's size into `size` when declared, and its response gives the
    `initial` values of every binding and the server's expiry at `expires_at`. `probe` reads the server's offset at
    `remote_offset`; `append` sends one chunk as its binary body, writing its offset into `offset` and its length into
    `length`. Chunks are at most `max_chunk_bytes`; `partial_commit` allows a server to keep part of one. A helper
    without `completion` completes when the server holds every byte; one with it sends that operation once.
    """

    helper_id: str
    operation: OperationRef
    create: OperationPlan[C, object]
    probe_operation: OperationRef
    probe: OperationPlan[object, object]
    remote_offset: Selector
    append_operation: OperationRef
    append: OperationPlan[object, object]
    offset: ParameterTarget
    max_chunk_bytes: int
    partial_commit: bool
    fingerprint: str
    size: ParameterTarget | None = None
    expires_at: Selector | None = None
    probe_bindings: tuple[PageBinding, ...] = ()
    append_bindings: tuple[PageBinding, ...] = ()
    length: ParameterTarget | None = None
    completion_operation: OperationRef | None = None
    completion: OperationPlan[T, object] | None = None
    completion_bindings: tuple[PageBinding, ...] = ()
    size_position: int | None = field(init=False)
    probed: _Targeted[object] = field(init=False)
    appended: _Targeted[object] = field(init=False)
    completed: _Targeted[T] | None = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        """Derive the operations writing the bindings, the chunk's offset and length, and the size's position."""
        from dataclasses import replace  # noqa: PLC0415

        probed = _targeted(self.probe, self.probe_bindings)
        extra = (self.offset,) if self.length is None else (self.offset, self.length)
        appended = _targeted(self.append, self.append_bindings, extra)
        appended = replace(appended, call=_unreplayed(appended.call))
        completed = None if self.completion is None else _targeted(self.completion, self.completion_bindings)
        children = (probed, appended, *(() if completed is None else (completed,)))
        headers, queries = set[str](), set[str]()
        if (size := self.size) is not None:
            (headers if size.location == "header" else queries).add(
                size.name.lower() if size.location == "header" else size.name
            )
        object.__setattr__(self, "size_position", None if size is None else _position(self.create, size))
        object.__setattr__(self, "probed", probed)
        object.__setattr__(self, "appended", appended)
        object.__setattr__(self, "completed", completed)
        object.__setattr__(self, "headers", frozenset(headers.union(*(item.headers for item in children))))
        object.__setattr__(self, "queries", frozenset(queries.union(*(item.queries for item in children))))


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective limits of one helper call, each from the first layer that sets it; None removes a limit."""

    chunk_bytes: int = 8 * 1024 * 1024
    max_parts: int | None = 10000
    parallelism: int = 4
    max_uncertain_probes: int = 3
    total_timeout: float | None = 600.0
    deadline: Deadline | None = None
    max_network_sends: int | None = 10000
    options: RequestOptions | None = None


_DEFAULTS: Final = _Limits()


def _invalid(
    plan: UploadPlan[Any, Any],
    path: tuple[str, ...],
    condition: Literal["invalid_value", "wrong_capability"] = "invalid_value",
) -> ProtocolConfigurationError:
    return ProtocolConfigurationError(
        field_path=path, condition=condition, helper_id=plan.helper_id, operation=plan.operation
    )


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: UploadPlan[T, C],
    upload_options: object,
    options: object,
    session_options: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    Effective options fixing an idempotency key are refused, since the create call and each append need keys of their
    own, and so are header or query patches of a parameter the helper writes.
    """
    for name, value, kind in (
        ("upload_options", upload_options, UploadOptions),
        ("options", options, RequestOptions),
        ("session_options", session_options, SessionOptions),
    ):
        if value is not None and not isinstance(value, kind):
            raise _invalid(plan, (name,))
    request = options if isinstance(options, RequestOptions) else None
    if core.fixes_key(request):
        raise _invalid(plan, ("options", "idempotency_key"))
    if request is not None:
        for name, _ in request.headers:
            if name.lower() in plan.headers:
                raise _invalid(plan, ("options", "headers", name))
        for name, _ in request.query:
            if name in plan.queries:
                raise _invalid(plan, ("options", "query", name))
    defaults = core.protocol_defaults(plan.helper_id)
    kinds = (upload_options, UNSET if defaults is None else defaults.options)
    sessions = (session_options, UNSET if defaults is None else defaults.session)
    return _Limits(
        chunk_bytes=layered(kinds, "chunk_bytes", _DEFAULTS.chunk_bytes),
        max_parts=layered(kinds, "max_parts", _DEFAULTS.max_parts),
        parallelism=layered(kinds, "parallelism", _DEFAULTS.parallelism),
        max_uncertain_probes=layered(kinds, "max_uncertain_probes", _DEFAULTS.max_uncertain_probes),
        total_timeout=layered(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=layered(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=layered(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
    )


def _source_kind(value: object) -> Literal["iterable", "iterator", "stream", "reader"] | None:
    """Return the kind of a one-shot input that has no resumable identity, or None for anything else."""
    kind: Literal["iterable", "iterator", "stream", "reader"] | None = None
    if hasattr(value, "read"):
        kind = "reader"
    elif hasattr(value, "__aiter__"):
        kind = "stream"
    elif hasattr(value, "__next__"):
        kind = "iterator"
    elif hasattr(value, "__iter__"):
        kind = "iterable"
    return kind


def _identity(plan: UploadPlan[T, C], source: object) -> UploadIdentity:
    """Return a source's identity, refusing a one-shot input, another value, and an invalid range capability."""
    if not all(hasattr(source, name) for name in ("identity", "max_parallel_ranges", "open_range")):
        if (kind := _source_kind(source)) is None:
            raise _invalid(plan, ("source",))
        raise NonResumableSourceError(source_kind=kind, helper_id=plan.helper_id, operation=plan.operation)
    source_any: Any = source
    if not isinstance(identity := source_any.identity, UploadIdentity):
        raise _invalid(plan, ("source", "identity"))
    if type(ranges := source_any.max_parallel_ranges) is not int or ranges < 1:
        raise _invalid(plan, ("source", "max_parallel_ranges"))
    return identity


def _layout(plan: UploadPlan[T, C], limits: _Limits, size: int) -> int:
    """Return the chunk size a new upload uses, refusing more chunks than allowed or a digest list too large to keep."""
    chunk = min(limits.chunk_bytes, plan.max_chunk_bytes)
    count = -(-size // chunk)
    if (limit := limits.max_parts) is not None and count > limit:
        raise _invalid(plan, ("upload_options", "max_parts"))
    if (encoded := 4 * -(-_DIGEST * count // 3)) > MAX_STATE_BYTES:
        raise ProtocolSizeError(
            kind="part_manifest",
            limit=MAX_STATE_BYTES,
            observed=encoded,
            unit="bytes",
            helper_id=plan.helper_id,
            operation=plan.operation,
        )
    return chunk


class _Scan:
    """The digests of content read in order: of each chunk, and of the whole."""

    __slots__ = ("chunk", "digests", "part", "read", "whole")

    def __init__(self, chunk: int) -> None:
        self.chunk = chunk
        self.whole = sha256()
        self.part = sha256()
        self.digests = bytearray()
        self.read = 0

    def add(self, data: bytes) -> None:
        """Hash bytes read after those before, closing each chunk at its end."""
        view = memoryview(data)
        while view:
            room = self.chunk - self.read % self.chunk
            piece, view = view[:room], view[room:]
            self.part.update(piece)
            self.whole.update(piece)
            self.read += len(piece)
            if not self.read % self.chunk:
                self.digests += self.part.digest()
                self.part = sha256()

    def finish(self) -> tuple[UploadIdentity, bytes]:
        """Return the identity of what was read and the digest of each chunk."""
        if self.read % self.chunk:
            self.digests += self.part.digest()
        return UploadIdentity(size=self.read, sha256=self.whole.digest()), bytes(self.digests)


def _wanted(size: int, read: int) -> int:
    """Return how much to read next: up to a read buffer, and one byte past the end to find bytes beyond it."""
    return min(_READ, size - read + 1)


class _Upload(Generic[T]):
    """What the synchronous and asyncio handles share: the plan, limits, session, source, and confirmed offset.

    Only a settled child call changes the confirmed offset. A failed append leaves it and makes the next step probe the
    server first; a source that changed keeps its checkpoint but sends nothing more.
    """

    __slots__ = (
        "_bound",
        "_changed",
        "_chunk",
        "_closed",
        "_confirmed",
        "_delivery",
        "_digests",
        "_expires_at",
        "_guard",
        "_high",
        "_identity",
        "_limits",
        "_lock",
        "_phase",
        "_plan",
        "_result",
        "_saved",
        "_session",
        "_verify",
    )

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        plan: UploadPlan[T, Any],
        limits: _Limits,
        session: OperationSession,
        identity: UploadIdentity,
        chunk: int,
        digests: bytes,
    ) -> None:
        """Keep the plan, limits, session, the content's identity, its chunk size, and each chunk's digest."""
        self._plan = plan
        self._limits = limits
        self._session = session
        self._identity = identity
        self._chunk = chunk
        self._digests = digests
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self._phase = _Phase.UPLOADING
        self._delivery: DeliveryState | None = None
        self._confirmed = 0
        self._verify = False
        self._high: int | None = 0
        self._closed = False
        self._changed = False
        self._bound: tuple[tuple[WireValue, ...], tuple[WireValue, ...], tuple[WireValue, ...]] = ((), (), ())
        self._expires_at: datetime | None = None
        self._result: T | None = None
        self._saved: tuple[bytes, int, str | None] | None = None

    def __repr__(self) -> str:
        """Name the handle's phase and its confirmed and total bytes only, never its data."""
        return (
            f"{type(self).__name__}(phase={self._phase.value!r}, confirmed_bytes={self._confirmed}, "
            f"total_bytes={self._identity.size})"
        )

    def _progress(self) -> UploadProgress:
        return UploadProgress(
            confirmed_bytes=self._confirmed,
            total_bytes=self._identity.size,
            complete=self._phase is _Phase.COMPLETE,
        )

    def _counters(self) -> ProtocolProgress:
        session = self._session
        return MappingProxyType({
            "confirmed_bytes": self._confirmed,
            "network_send_count": session.network_send_count,
            "network_send_budget_used": session.network_send_budget_used,
        })

    def _state_error(self, action: str, state: str) -> ProtocolStateError:
        plan = self._plan
        return ProtocolStateError(
            state=state,
            action=action,
            helper_id=plan.helper_id,
            operation=plan.operation,
            parent_session_id=self._session.session_id,
        )

    @contextmanager
    def _mapped(self, *, created: bool = True) -> Generator[None, None, None]:
        """Raise a child call's refusal for want of a session send slot as the session's limit error."""
        try:
            yield
        except BudgetExceededError as error:
            if error.budget_kind != "parent_network":
                raise
            plan = self._plan
            raise SessionLimitError(
                kind="network_sends",
                limit=error.limit,
                progress=self._counters(),
                resume_state=self._checkpoint() if created else None,
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
            ) from None

    def _enter(self, action: str) -> None:
        """Take the handle for one step, refusing a concurrent step, a closed handle, and a changed source."""
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, "uploading")
        if self._closed or self._changed:
            self._lock.release()
            raise self._state_error(action, "closed" if self._closed else "source_changed")

    def _close(self, action: str, *, quiet: bool = False) -> None:
        """Close the handle, refusing while a step runs, or leaving it open then when `quiet`."""
        if not self._lock.acquire(blocking=False):
            if quiet:
                return
            raise self._state_error(action, "uploading")
        self._closed = True
        self._lock.release()

    def _sending(self, end: int) -> None:
        """Probe before the next append unless this one settles, and raise the end this handle ever sent to.

        A handle a checkpoint restored never knows what an earlier session sent, so it keeps no such end.
        """
        with self._guard:
            self._verify = True
            self._high = None if self._high is None else max(self._high, end)

    def _settle(self, confirmed: int, *, verify: bool = False) -> None:
        """Confirm the server holds every byte before an offset; one completing by length completes with the last."""
        with self._guard:
            self._confirmed, self._verify = confirmed, verify
            if confirmed == self._identity.size and self._plan.completion is None:
                self._phase = _Phase.COMPLETE

    def _unknown(
        self,
        *,
        phase: Literal["append", "complete"],
        delivery: DeliveryState,
        error: BaseException | None = None,
        failures: tuple[BaseException, ...] = (),
    ) -> UploadDeliveryUnknownError:
        plan = self._plan
        return UploadDeliveryUnknownError(
            phase=phase,
            progress=self._progress(),
            delivery_state=delivery,
            resume_state=self._checkpoint(),
            helper_id=plan.helper_id,
            operation=plan.operation,
            parent_session_id=self._session.session_id,
            cause=error,
            secondary_errors=failures,
        )

    def _settled(self) -> T | None:
        """Return nothing while the upload is due, its result once complete, or raise its unknown completion again."""
        if self._phase is _Phase.UPLOADING:
            return None
        if (delivery := self._delivery) is not None:
            raise self._unknown(phase="complete", delivery=delivery)
        return self._result

    def _offset_error(self, expected: int, remote: int, info: ResponseInfo | None) -> UploadOffsetError:
        plan = self._plan
        return UploadOffsetError(
            confirmed_offset=self._confirmed,
            expected_offset=expected,
            remote_offset=remote,
            size=self._identity.size,
            resume_state=self._checkpoint(),
            location=plan.remote_offset,
            helper_id=plan.helper_id,
            operation=plan.probe_operation,
            parent_session_id=self._session.session_id,
            info=info,
        )

    def _data_error(
        self,
        info: ResponseInfo | None,
        condition: Literal["missing", "null", "type", "value", "malformed"],
        at: Selector,
    ) -> ProtocolDataError:
        plan = self._plan
        return ProtocolDataError(
            condition=condition, location=at, helper_id=plan.helper_id, operation=plan.operation, info=info
        )

    def _read(self, read: Selector, wire: WireValue, info: ResponseInfo) -> WireValue:
        """Return what a selector reads from a response, refusing a missing value and a header repeated once."""
        try:
            value = selected(read, wire, info)
        except RepeatedValueError:
            raise self._data_error(info, "malformed", read) from None
        if value is MISSING:
            raise self._data_error(info, "missing", read)
        return value

    def _values(
        self, targeted: _Targeted[Any], bindings: tuple[PageBinding, ...], wire: WireValue, info: ResponseInfo
    ) -> tuple[WireValue, ...]:
        """Return what bindings write: their literals and what the create response gives, refusing dot segments."""
        written = tuple(
            binding.literal if binding.selector is None else self._read(binding.selector, wire, info)
            for binding in bindings
        )
        self._dotted(targeted, written, info)
        return written

    def _dotted(self, targeted: _Targeted[Any], written: tuple[WireValue, ...], info: ResponseInfo | None) -> None:
        """Refuse read values that make a path segment a dot segment once encoded."""
        from .writes import dotted_read  # noqa: PLC0415 - A plan loaded the operation runtime.

        parameters = targeted.call.parameters
        for segment, parts in targeted.dotted:
            if (read := dotted_read(parameters, segment, parts, written, dict)) is not None:
                raise self._data_error(info, "value", read)

    def _created(
        self, _data: object, wire: WireValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> tuple[tuple[tuple[WireValue, ...], tuple[WireValue, ...], tuple[WireValue, ...]], datetime | None]:
        """Read what each later call writes from the create response, and the server's expiry."""
        plan = self._plan
        bound = (
            self._values(plan.probed, plan.probe_bindings, wire, info),
            self._values(plan.appended, plan.append_bindings, wire, info),
            () if plan.completed is None else self._values(plan.completed, plan.completion_bindings, wire, info),
        )
        expires_at = None
        if (read := plan.expires_at) is not None:
            if not isinstance(value := self._read(read, wire, info), str):
                raise self._data_error(info, "null" if value is None else "type", read)
            if (expires_at := server_expiry(value)) is None:
                raise self._data_error(info, "value", read)
        return bound, expires_at

    def _offered(
        self, _data: object, wire: WireValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> tuple[int, ResponseInfo]:
        """Return the offset a probe response gives: a JSON integer, or a header of ASCII digits."""
        read = self._plan.remote_offset
        value = self._read(read, wire, info)
        if isinstance(read, HeaderSelector) and isinstance(value, str):
            try:
                if not (value.isascii() and value.isdigit()):
                    raise ValueError(value)  # noqa: TRY301 - One refusal covers digits int() cannot convert.
                return int(value), info
            except ValueError:
                raise self._data_error(info, "value", read) from None
        if not isinstance(value, int) or isinstance(value, bool):
            raise self._data_error(info, "null" if value is None else "type", read)
        if value < 0:
            raise self._data_error(info, "value", read)
        return value, info

    def _verified(self, remote: int, info: ResponseInfo) -> None:
        """Take the offset a probe gives as confirmed: never below the confirmed one, past the content, or mid-chunk.

        A server may hold part of a chunk only when the helper allows partial commits, and never more than this handle
        sent unless a checkpoint restored it.
        """
        confirmed, size = self._confirmed, self._identity.size
        limit = size if (high := self._high) is None else min(high, size)
        partial = remote % self._chunk and remote != size
        if remote < confirmed or remote > limit or (partial and not self._plan.partial_commit):
            raise self._offset_error(confirmed, remote, info)
        self._settle(remote)

    def _reconciled(self, remote: int, end: int, info: ResponseInfo) -> bool:
        """Settle an append of unknown outcome by the offset a probe gives; return whether its chunk is confirmed.

        An unchanged offset sends the range again, the chunk's end confirms it, and an offset inside it confirms the
        bytes before it where partial commits are allowed.
        """
        confirmed = self._confirmed
        if remote == confirmed:
            return False
        if remote == end:
            self._settle(end)
            return True
        if confirmed < remote < end and self._plan.partial_commit:
            self._settle(remote)
            return False
        raise self._offset_error(end, remote, info)

    def _chunked(self, buffer: bytes, index: int, size: int) -> None:
        """Refuse a chunk whose bytes are not those the scan read, stopping every later send."""
        expected = self._digests[index * _DIGEST : (index + 1) * _DIGEST]
        if len(buffer) == size and sha256(buffer).digest() == expected:
            return
        with self._guard:
            self._changed = True
        plan = self._plan
        raise UploadSourceChangedError(
            expected=UploadIdentity(size=size, sha256=expected),
            actual=UploadIdentity(size=len(buffer), sha256=sha256(buffer).digest()),
            offset=index * self._chunk,
            helper_id=plan.helper_id,
            operation=plan.operation,
            parent_session_id=self._session.session_id,
        )

    def _span(self) -> tuple[int, int, int]:
        """Return the index of the chunk holding the confirmed offset, its start, and its end."""
        index = self._confirmed // self._chunk
        start = index * self._chunk
        return index, start, min(start + self._chunk, self._identity.size)

    def _append_request(self, payload: bytes) -> Callable[[], tuple[tuple[object, ...], object, None]]:
        plan, offset = self._plan, self._confirmed
        values = (offset,) if plan.length is None else (offset, len(payload))
        arguments = plan.appended.request((*self._bound[1], *values))[0]
        return lambda: (arguments, payload, None)

    def _probe_request(self) -> tuple[tuple[object, ...], object, None]:
        return (*self._plan.probed.request(self._bound[0]), None)

    def _completion_request(self) -> tuple[tuple[object, ...], object, None]:
        completed = self._plan.completed
        assert completed is not None
        return (*completed.request(self._bound[2]), None)

    def _completed(
        self, data: T, _wire: WireValue, content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> T:
        """Keep the completion's result and its body, which a checkpoint saves."""
        with self._guard:
            self._result, self._phase, self._delivery = data, _Phase.COMPLETE, None
            self._saved = content, info.status_code, info.content_type
        return data

    def _completing(self) -> int:
        """Mark the completion unknown before it is sent, returning the session's sends so far.

        A checkpoint taken while it is in flight, or after any interruption, never lets it be sent again.
        """
        with self._guard:
            self._phase, self._delivery = _Phase.UNKNOWN, DeliveryState.MAYBE_SENT
        return self._session.network_send_count

    def _completion_failed(self, error: BaseException, sends: int) -> None:
        """Let a completion that certainly did not apply be sent again; keep every other one unknown.

        It did not apply when the server answered with an error status, when nothing reached the server, or when the
        session sent nothing. A response the server started, such as a success whose body does not decode, keeps it
        unknown with RESPONSE_STARTED.
        """
        info = getattr(error, "info", None)
        status = getattr(info, "status_code", 0)
        unapplied = (
            (isinstance(error, (HTTPStatusError, UnexpectedStatusError)) and not _MIN_SUCCESS <= status <= _MAX_SUCCESS)
            or getattr(error, "delivery_state", None) is DeliveryState.NOT_SENT
            or self._session.network_send_count == sends
        )
        started = info is not None or getattr(error, "delivery_state", None) is DeliveryState.RESPONSE_STARTED
        with self._guard:
            if unapplied:
                self._phase, self._delivery = _Phase.UPLOADING, None
            elif started and self._phase is _Phase.UNKNOWN:
                self._delivery = DeliveryState.RESPONSE_STARTED

    def _completion_error(self, error: Exception) -> Exception:
        """Return the error of a failed completion: its own once it may be sent again, or else an unknown outcome."""
        if (delivery := self._delivery) is None or self._phase is not _Phase.UNKNOWN:
            return error
        return self._unknown(phase="complete", delivery=delivery, error=error)

    def checkpoint(self) -> ResumeState:
        """Return the state a later `resume` continues from, sending nothing; it works in every phase.

        It keeps the upload's identity, chunk size and digests, the confirmed offset, the values later calls write, the
        server's expiry, and a completion's result, bound to the helper and the security the call runs under.
        """
        with self._guard:
            return self._checkpoint()

    def _checkpoint(self) -> ResumeState:
        plan, identity = self._plan, self._identity
        options = self._limits.options
        facts: list[WireValue] = []
        exportable = True
        for call in _children(plan):
            fact, allowed = self._core_security(call, options)
            facts.append(fact)
            exportable = exportable and allowed
        saved = self._saved
        delivery = self._delivery
        state: WireValue = {
            "size": identity.size,
            "sha256": identity.sha256.hex(),
            "chunk": self._chunk,
            "confirmed": self._confirmed,
            "phase": self._phase.value,
            "delivery": None if delivery is None else delivery.value,
            "bound": self._bound,
            "result": None if saved is None else (saved[1], saved[2]),
        }
        return helper_state(
            helper_fingerprint=plan.fingerprint,
            security_fingerprint=sha256(canonical_json(tuple(facts))).hexdigest(),
            state=state,
            payload=self._digests + (b"" if saved is None else saved[0]),
            exportable=exportable,
            expires_at=self._expires_at,
        )

    def _core_security(
        self, call: OperationPlan[Any, object], options: RequestOptions | None
    ) -> tuple[WireValue, bool]:
        raise NotImplementedError


def _probed_again(error: Exception) -> bool:
    """Return whether a probe failed in a way another probe may settle: a transport error or an error status."""
    return isinstance(error, (TransportError, HTTPStatusError, UnexpectedStatusError))


def _children(plan: UploadPlan[Any, Any]) -> tuple[OperationPlan[Any, object], ...]:
    """Return the operations a resumed upload may send: the probe, the append, and any completion."""
    return (plan.probe, plan.append, *(() if plan.completion is None else (plan.completion,)))


def _session(limits: _Limits) -> OperationSession:
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only a started helper loads the call runtime.

    return OperationSession(
        total_timeout=limits.total_timeout, deadline=limits.deadline, max_network_sends=limits.max_network_sends
    )


def _changed(
    plan: UploadPlan[Any, Any], expected: UploadIdentity, actual: UploadIdentity | None, offset: int | None = None
) -> UploadSourceChangedError:
    return UploadSourceChangedError(
        expected=expected, actual=actual, offset=offset, helper_id=plan.helper_id, operation=plan.operation
    )


def _opened(plan: UploadPlan[Any, Any], source: Any, start: int, length: int, method: str) -> Any:
    """Open a range of a source, refusing one whose context is not of this client's kind."""
    opened = source.open_range(start, length)
    if not hasattr(opened, method):
        raise _invalid(plan, ("source",), "wrong_capability")
    return opened


def _bytes(plan: UploadPlan[Any, Any], data: object) -> bytes:
    """Return what a reader read, refusing anything but bytes."""
    if not isinstance(data, bytes):
        raise _invalid(plan, ("source",), "wrong_capability")
    return data


def _scanned(plan: UploadPlan[Any, Any], identity: UploadIdentity, scan: _Scan, digests: bytes | None) -> bytes:
    """Return each chunk's digest once the scan read exactly the identity's content, and any saved digests too."""
    read, found = scan.finish()
    if read != identity:
        raise _changed(plan, identity, read)
    if digests is not None and found != digests:
        index = next(
            index
            for index in range(len(found) // _DIGEST)
            if found[index * _DIGEST : (index + 1) * _DIGEST] != digests[index * _DIGEST : (index + 1) * _DIGEST]
        )
        start = index * scan.chunk
        size = min(scan.chunk, identity.size - start)
        expected = UploadIdentity(size=size, sha256=digests[index * _DIGEST : (index + 1) * _DIGEST])
        actual = UploadIdentity(size=size, sha256=found[index * _DIGEST : (index + 1) * _DIGEST])
        raise _changed(plan, expected, actual, start)
    return found


@final
class UploadHandle(_Upload[T]):
    """A resumable upload a helper created or resumed: `advance` appends one chunk, and `run` uploads the rest.

    `close` only stops local uploading; the remote upload stays. Uploading from two threads at once raises
    ProtocolStateError.
    """

    __slots__ = ("_core", "_source")

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        core: ClientCore,
        plan: UploadPlan[T, Any],
        limits: _Limits,
        session: OperationSession,
        source: UploadSource,
        identity: UploadIdentity,
        chunk: int,
        digests: bytes,
    ) -> None:
        """Keep the client core the handle's child calls are sent through and the borrowed source."""
        super().__init__(plan, limits, session, identity, chunk, digests)
        self._core = core
        self._source = source

    def _core_security(
        self, call: OperationPlan[Any, object], options: RequestOptions | None
    ) -> tuple[WireValue, bool]:
        return self._core.checkpoint_security(call, options)

    def _waiter(self) -> LogicalCallContext:
        return self._core.waiting(self._limits.options, self._session, self._plan.append.operation_id)

    def _read_all(self, start: int, size: int, add: Callable[[bytes], object]) -> None:
        """Read a range through one reader, until its end or one byte past its size, checking the session first."""
        plan = self._plan
        waiter = self._waiter()
        got = 0
        try:
            with _opened(plan, self._source, start, size, "__enter__") as reader:
                opened: RangeReader = reader
                while got <= size:
                    waiter.check()
                    if not (data := _bytes(plan, opened.read(_wanted(size, got)))):
                        break
                    got += len(data)
                    add(data)
        finally:
            waiter.finish()

    def _scan(self, digests: bytes | None = None) -> bytes:
        """Read the whole source once before any send, returning each chunk's digest; a change raises."""
        scan = _Scan(self._chunk)
        self._read_all(0, self._identity.size, scan.add)
        return _scanned(self._plan, self._identity, scan, digests)

    def _buffer(self, index: int, start: int, size: int) -> bytes:
        """Read one chunk into its own buffer and check it against the digest the scan recorded."""
        parts: list[bytes] = []
        self._read_all(start, size, parts.append)
        buffer = b"".join(parts)
        self._chunked(buffer, index, size)
        return buffer

    def _create(self, arguments: tuple[object, ...], body: object, media_type: str | MediaSelector | None) -> None:
        """Send the create request as the session's first child call and keep what it gives."""
        core, plan = self._core, self._plan
        with self._mapped(created=False):
            self._bound, self._expires_at = core.execute_page(
                plan,
                plan.create,
                lambda: (arguments, body, None),
                self._created,
                body=body,
                media_type=media_type,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )
        self._settle(0)

    def _probe(self) -> tuple[int, ResponseInfo]:
        """Probe the server's offset once."""
        plan = self._plan
        with self._mapped():
            return self._core.execute_page(
                plan,
                plan.probed.call,
                self._probe_request,
                self._offered,
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )

    def _append(self) -> None:
        """Append the chunk holding the confirmed offset until the server confirms all of it."""
        index, start, end = self._span()
        buffer = self._buffer(index, start, end - start)
        while self._confirmed < end:
            payload = buffer[self._confirmed - start :]
            self._sending(end)
            try:
                with self._mapped():
                    self._core.execute_page(
                        self._plan,
                        self._plan.appended.call,
                        self._append_request(payload),
                        _ignored,
                        body=payload,
                        media_type=None,
                        options=self._limits.options,
                        session=self._session,
                        max_page_bytes=None,
                    )
            except Exception as error:
                if not isinstance(error, TransportError) or (delivery := error.delivery_state) not in _UNKNOWN:
                    raise
                if self._uncertain(error, delivery, end):
                    return
                continue
            self._settle(end)

    def _uncertain(self, error: TransportError, delivery: DeliveryState, end: int) -> bool:
        """Probe after an append of unknown outcome; return whether its chunk is confirmed, raising when unsettled."""
        failures: list[BaseException] = []
        for _ in range(self._limits.max_uncertain_probes):
            try:
                remote, info = self._probe()
            except Exception as failure:
                if not _probed_again(failure):
                    raise
                failures.append(failure)
                continue
            return self._reconciled(remote, end, info)
        raise self._unknown(phase="append", delivery=delivery, error=error, failures=tuple(failures))

    def _complete(self) -> None:
        """Send the completion once and keep its result."""
        plan = self._plan
        completed = plan.completed
        assert completed is not None
        sends = self._completing()
        try:
            with self._mapped():
                try:
                    self._core.execute_page(
                        plan,
                        completed.call,
                        self._completion_request,
                        self._completed,
                        body=UNSET,
                        media_type=None,
                        options=self._limits.options,
                        session=self._session,
                        max_page_bytes=None,
                    )
                except BaseException as error:
                    self._completion_failed(error, sends)
                    raise
        except Exception as error:
            if (failure := self._completion_error(error)) is error:
                raise
            raise failure from None

    def _step(self) -> None:
        """Probe when due, then append one chunk, or complete once every byte is confirmed."""
        if self._verify:
            self._verified(*self._probe())
            if self._phase is _Phase.COMPLETE:
                return
        if self._confirmed < self._identity.size:
            self._append()
        else:
            self._complete()

    def advance(self) -> UploadProgress:
        """Append one chunk, or complete the upload once every byte is confirmed, and return the progress.

        A complete upload sends nothing.
        """
        self._enter("advance")
        try:
            self._settled()
            if self._phase is _Phase.UPLOADING:
                self._step()
            return self._progress()
        finally:
            self._lock.release()

    def run(self) -> T:
        """Upload every remaining chunk, complete the upload, and return its result; a complete upload sends nothing."""
        self._enter("run")
        try:
            while self._phase is _Phase.UPLOADING:
                self._step()
            return cast("T", self._settled())
        finally:
            self._lock.release()

    def close(self) -> None:
        """Stop uploading locally; the remote upload stays, later steps raise ProtocolStateError."""
        self._close("close")

    def __enter__(self) -> Self:
        """Return this handle, which leaving the block closes."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the handle; while another step runs, leave it open rather than mask the block's error."""
        self._close("close", quiet=exc is not None)


@final
class AsyncUploadHandle(_Upload[T]):
    """A resumable upload an asyncio helper created or resumed: `advance` appends one chunk, `run` uploads the rest.

    `aclose` only stops local uploading; the remote upload stays. Uploading from two tasks at once raises
    ProtocolStateError.
    """

    __slots__ = ("_core", "_source")

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        core: AsyncClientCore,
        plan: UploadPlan[T, Any],
        limits: _Limits,
        session: OperationSession,
        source: AsyncUploadSource,
        identity: UploadIdentity,
        chunk: int,
        digests: bytes,
    ) -> None:
        """Keep the asyncio client core the handle's child calls are sent through and the borrowed source."""
        super().__init__(plan, limits, session, identity, chunk, digests)
        self._core = core
        self._source = source

    def _core_security(
        self, call: OperationPlan[Any, object], options: RequestOptions | None
    ) -> tuple[WireValue, bool]:
        return self._core.checkpoint_security(call, options)

    def _waiter(self) -> LogicalCallContext:
        return self._core.waiting(self._limits.options, self._session, self._plan.append.operation_id)

    async def _read_all(self, start: int, size: int, add: Callable[[bytes], object]) -> None:
        """Read a range through one reader, each read bounded by the session's deadline and the client's close."""
        plan = self._plan
        waiter = self._waiter()
        got = 0
        try:
            async with _opened(plan, self._source, start, size, "__aenter__") as reader:
                opened: AsyncRangeReader = reader
                while got <= size:
                    read = partial(opened.read, _wanted(size, got))
                    if not (data := _bytes(plan, await waiter.bounded(read, idle=False))):
                        break
                    got += len(data)
                    add(data)
        finally:
            waiter.finish()

    async def _scan(self, digests: bytes | None = None) -> bytes:
        """Read the whole source once before any send, returning each chunk's digest; a change raises."""
        scan = _Scan(self._chunk)
        await self._read_all(0, self._identity.size, scan.add)
        return _scanned(self._plan, self._identity, scan, digests)

    async def _buffer(self, index: int, start: int, size: int) -> bytes:
        """Read one chunk into its own buffer and check it against the digest the scan recorded."""
        parts: list[bytes] = []
        await self._read_all(start, size, parts.append)
        buffer = b"".join(parts)
        self._chunked(buffer, index, size)
        return buffer

    async def _create(
        self, arguments: tuple[object, ...], body: object, media_type: str | MediaSelector | None
    ) -> None:
        """Send the create request as the session's first child call and keep what it gives."""
        core, plan = self._core, self._plan
        with self._mapped(created=False):
            self._bound, self._expires_at = await core.execute_page(
                plan,
                plan.create,
                lambda: (arguments, body, None),
                self._created,
                body=body,
                media_type=media_type,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )
        self._settle(0)

    async def _probe(self) -> tuple[int, ResponseInfo]:
        """Probe the server's offset once."""
        plan = self._plan
        with self._mapped():
            return await self._core.execute_page(
                plan,
                plan.probed.call,
                self._probe_request,
                self._offered,
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )

    async def _append(self) -> None:
        """Append the chunk holding the confirmed offset until the server confirms all of it."""
        index, start, end = self._span()
        buffer = await self._buffer(index, start, end - start)
        while self._confirmed < end:
            payload = buffer[self._confirmed - start :]
            self._sending(end)
            try:
                with self._mapped():
                    await self._core.execute_page(
                        self._plan,
                        self._plan.appended.call,
                        self._append_request(payload),
                        _ignored,
                        body=payload,
                        media_type=None,
                        options=self._limits.options,
                        session=self._session,
                        max_page_bytes=None,
                    )
            except Exception as error:
                if not isinstance(error, TransportError) or (delivery := error.delivery_state) not in _UNKNOWN:
                    raise
                if await self._uncertain(error, delivery, end):
                    return
                continue
            self._settle(end)

    async def _uncertain(self, error: TransportError, delivery: DeliveryState, end: int) -> bool:
        """Probe after an append of unknown outcome; return whether its chunk is confirmed, raising when unsettled."""
        failures: list[BaseException] = []
        for _ in range(self._limits.max_uncertain_probes):
            try:
                remote, info = await self._probe()
            except Exception as failure:
                if not _probed_again(failure):
                    raise
                failures.append(failure)
                continue
            return self._reconciled(remote, end, info)
        raise self._unknown(phase="append", delivery=delivery, error=error, failures=tuple(failures))

    async def _complete(self) -> None:
        """Send the completion once and keep its result."""
        plan = self._plan
        completed = plan.completed
        assert completed is not None
        sends = self._completing()
        try:
            with self._mapped():
                try:
                    await self._core.execute_page(
                        plan,
                        completed.call,
                        self._completion_request,
                        self._completed,
                        body=UNSET,
                        media_type=None,
                        options=self._limits.options,
                        session=self._session,
                        max_page_bytes=None,
                    )
                except BaseException as error:
                    self._completion_failed(error, sends)
                    raise
        except Exception as error:
            if (failure := self._completion_error(error)) is error:
                raise
            raise failure from None

    async def _step(self) -> None:
        """Probe when due, then append one chunk, or complete once every byte is confirmed."""
        if self._verify:
            self._verified(*await self._probe())
            if self._phase is _Phase.COMPLETE:
                return
        if self._confirmed < self._identity.size:
            await self._append()
        else:
            await self._complete()

    async def advance(self) -> UploadProgress:
        """Append one chunk, or complete the upload once every byte is confirmed, and return the progress.

        A complete upload sends nothing.
        """
        self._enter("advance")
        try:
            self._settled()
            if self._phase is _Phase.UPLOADING:
                await self._step()
            return self._progress()
        finally:
            self._lock.release()

    async def run(self) -> T:
        """Upload every remaining chunk, complete the upload, and return its result; a complete upload sends nothing."""
        self._enter("run")
        try:
            while self._phase is _Phase.UPLOADING:
                await self._step()
            return cast("T", self._settled())
        finally:
            self._lock.release()

    async def aclose(self) -> None:
        """Stop uploading locally; the remote upload stays, later steps raise ProtocolStateError."""
        self._close("aclose")

    async def __aenter__(self) -> Self:
        """Return this handle, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the handle; while another step runs, leave it open rather than mask the block's error."""
        self._close("aclose", quiet=exc is not None)


def _ignored(
    _data: object, _wire: WireValue, _content: bytes, _info: ResponseInfo, _url: str, _managed: frozenset[str]
) -> None:
    """Confirm an append by its success response alone."""


def _sized(
    core: ClientCore | AsyncClientCore, plan: UploadPlan[T, C], arguments: tuple[object, ...], size: int
) -> tuple[object, ...]:
    """Return the create arguments with the content's size where the helper declares it, built as a caller's value."""
    if (position := plan.size_position) is None:
        return arguments
    create = plan.create
    wire = tuple(size if index == position else UNSET for index in range(len(create.parameters)))
    value = core.restored_request(create, wire, None)[0][position]
    return (*arguments[:position], value, *arguments[position:])


def start_upload(  # noqa: PLR0913
    core: ClientCore,
    plan: UploadPlan[T, C],
    source: object,
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> UploadHandle[T]:
    """Scan the source, create the helper's upload in a session of its own, and return the handle that appends to it."""
    limits = _limits(core, plan, upload_options, options, session_options)
    identity = _identity(plan, source)
    chunk = _layout(plan, limits, identity.size)
    handle = UploadHandle(core, plan, limits, _session(limits), cast("UploadSource", source), identity, chunk, b"")
    handle._digests = handle._scan()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._create(_sized(core, plan, arguments, identity.size), body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle


async def astart_upload(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncUploadHandle[T]:
    """Scan the source, create the helper's upload with asyncio in a session of its own, and return its handle."""
    limits = _limits(core, plan, upload_options, options, session_options)
    identity = _identity(plan, source)
    chunk = _layout(plan, limits, identity.size)
    handle = AsyncUploadHandle(
        core, plan, limits, _session(limits), cast("AsyncUploadSource", source), identity, chunk, b""
    )
    handle._digests = await handle._scan()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    await handle._create(_sized(core, plan, arguments, identity.size), body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle


@dataclass(frozen=True, slots=True)
class _Saved:
    """A checkpoint's decoded state, checked against the helper resuming it."""

    identity: UploadIdentity
    chunk: int
    confirmed: int
    phase: _Phase
    delivery: DeliveryState | None
    bound: tuple[tuple[WireValue, ...], tuple[WireValue, ...], tuple[WireValue, ...]]
    digests: bytes
    result: tuple[bytes, int, str | None] | None
    expires_at: datetime | None


def _resume_error(
    plan: UploadPlan[Any, Any], condition: Literal["fingerprint", "security", "malformed"]
) -> ResumeStateError:
    return ResumeStateError(condition=condition, helper_id=plan.helper_id, operation=plan.operation)


def _bound(plan: UploadPlan[Any, Any], saved: WireValue) -> tuple[tuple[WireValue, ...], ...]:
    """Return the values each later call writes, as saved, a literal binding taking the plan's value."""
    groups = tuple(map(state_array, state_array(saved)))
    bindings = (plan.probe_bindings, plan.append_bindings, plan.completion_bindings)
    require_state(len(groups) == _BOUND_FIELDS)
    require_state(all(len(group) == len(items) for group, items in zip(groups, bindings, strict=True)))
    return tuple(
        tuple(
            value if binding.selector is not None else binding.literal
            for binding, value in zip(items, group, strict=True)
        )
        for group, items in zip(groups, bindings, strict=True)
    )


def _decoded(plan: UploadPlan[Any, Any], state: WireValue, payload: bytes, expires_at: datetime | None) -> _Saved:
    """Return a checkpoint's state, refusing one whose form does not fit the helper."""
    from collections.abc import Mapping  # noqa: PLC0415

    require_state(isinstance(state, Mapping) and frozenset(state) == _STATE)
    fields = cast("Mapping[str, WireValue]", state)
    size, chunk, confirmed = (state_count(fields[name]) for name in ("size", "chunk", "confirmed"))
    digest, phase, delivery = (state_text(fields[name]) for name in ("sha256", "phase", "delivery"))
    require_state(
        isinstance(digest, str)
        and len(digest) == 2 * _DIGEST
        and digest == digest.lower()
        and all(character in "0123456789abcdef" for character in digest)
        and 0 < chunk <= plan.max_chunk_bytes
        and confirmed <= size
        and phase in _PHASES
    )
    resolved = _PHASES[cast("str", phase)]
    require_state(
        (delivery is None) == (resolved is not _Phase.UNKNOWN)
        and (delivery is None or delivery in {state.value for state in _UNKNOWN})
        and (resolved is _Phase.UPLOADING or confirmed == size)
        and (resolved is not _Phase.UNKNOWN or plan.completion is not None)
    )
    count = -(-size // chunk) * _DIGEST
    require_state(len(payload) >= count)
    saved_result = fields["result"]
    result: tuple[bytes, int, str | None] | None = None
    complete_by_operation = resolved is _Phase.COMPLETE and plan.completion is not None
    if saved_result is None:
        require_state(len(payload) == count and not complete_by_operation)
    else:
        parts = state_array(saved_result)
        require_state(complete_by_operation and len(parts) == _RESULT_FIELDS)
        result = payload[count:], state_count(parts[0]), state_text(parts[1])
    bound = cast(
        "tuple[tuple[WireValue, ...], tuple[WireValue, ...], tuple[WireValue, ...]]", _bound(plan, fields["bound"])
    )
    return _Saved(
        identity=UploadIdentity(size=size, sha256=bytes.fromhex(cast("str", digest))),
        chunk=chunk,
        confirmed=confirmed,
        phase=resolved,
        delivery=None if delivery is None else DeliveryState(delivery),
        bound=bound,
        digests=payload[:count],
        result=result,
        expires_at=expires_at,
    )


def _restored(core: ClientCore | AsyncClientCore, plan: UploadPlan[T, C], state: object, limits: _Limits) -> _Saved:
    """Return what a checkpoint saved: this helper's, made under the security the call runs with, and unexpired."""
    if not isinstance(state, ResumeState):
        raise _invalid(plan, ("state",))
    helper, security, state_json, payload, expires_at = state_fields(state)
    if helper != plan.fingerprint:
        raise _resume_error(plan, "fingerprint")
    facts = tuple(core.checkpoint_security(call, limits.options)[0] for call in _children(plan))
    if security != sha256(canonical_json(facts)).hexdigest():
        raise _resume_error(plan, "security")
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise UploadExpiredError(expires_at=expires_at, helper_id=plan.helper_id, operation=plan.operation)
    try:
        return _decoded(plan, decode_json(state_json), payload, expires_at)
    except MalformedStateError:
        raise _resume_error(plan, "malformed") from None


def _checked(core: ClientCore | AsyncClientCore, handle: _Upload[T], saved: _Saved) -> None:
    """Prepare each request the saved values write as its call would, refusing values that cannot be sent.

    Read values that make a path segment a dot segment raise ProtocolDataError, as if a server gave them; any other
    refusal of a saved value makes the checkpoint malformed.
    """
    plan = handle._plan  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    options = handle._limits.options  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    from ..client.errors import RequestEncodingError  # noqa: PLC0415 - Only a resume checks saved values.

    probe, append, completion = saved.bound
    offsets = (0,) if plan.length is None else (0, 0)
    requests: list[tuple[_Targeted[Any], tuple[WireValue, ...], tuple[WireValue, ...], object]] = [
        (plan.probed, probe, (), None),
        (plan.appended, append, offsets, b""),
    ]
    if (completed := plan.completed) is not None:
        requests.append((completed, completion, (), None))
    for targeted, values, extra, payload in requests:
        handle._dotted(targeted, values, None)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        arguments, body = targeted.request((*values, *extra))
        try:
            core.checked_page(targeted.call, _fixed(arguments, body if payload is None else payload), None, options)
        except (RequestEncodingError, ProtocolDataError, CodecError):
            raise _resume_error(plan, "malformed") from None


def _fixed(arguments: tuple[object, ...], body: object) -> Callable[[], tuple[tuple[object, ...], object, None]]:
    """Return the request of a check, built once."""
    return lambda: (arguments, body, None)


def _resumed(handle: _Upload[T], saved: _Saved, core: ClientCore | AsyncClientCore) -> None:
    """Restore a handle to a checkpoint's phase, offset, values, expiry, and any completion's result."""
    plan = handle._plan  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._bound = saved.bound  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._confirmed = saved.confirmed  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._verify = True  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._high = None  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._expires_at = saved.expires_at  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._phase, handle._delivery = saved.phase, saved.delivery  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    if (result := saved.result) is None:
        return
    completion = plan.completion
    assert completion is not None
    content, status, content_type = result
    try:
        data = core.saved_page(completion, content, status, content_type, handle._limits.options)[0]  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    except SDKError:
        raise _resume_error(plan, "malformed") from None
    handle._result, handle._saved = data, result  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def _resume_start(
    core: ClientCore | AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    limits: _Limits,
) -> tuple[_Saved, UploadIdentity]:
    """Check the checkpoint, then the source, before any read or send; the source must be the checkpoint's content."""
    saved = _restored(core, plan, state, limits)
    identity = _identity(plan, source)
    if identity != saved.identity:
        raise _changed(plan, saved.identity, identity)
    return saved, identity


def resume_upload(  # noqa: PLR0913
    core: ClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    *,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> UploadHandle[T]:
    """Continue a checkpoint in a new session: check the source against it, then probe the server's offset once.

    A complete upload resumes with its result and an unknown completion raises again, both without reading or sending.
    """
    limits = _limits(core, plan, upload_options, options, session_options)
    saved, identity = _resume_start(core, plan, source, state, limits)
    handle = UploadHandle(
        core, plan, limits, _session(limits), cast("UploadSource", source), identity, saved.chunk, saved.digests
    )
    _checked(core, handle, saved)
    _resumed(handle, saved, core)
    if handle._phase is not _Phase.UPLOADING:  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        handle._settled()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        return handle
    handle._scan(saved.digests)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._verified(*handle._probe())  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle


async def aresume_upload(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    *,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncUploadHandle[T]:
    """Continue a checkpoint with asyncio in a new session: check the source against it, then probe once.

    A complete upload resumes with its result and an unknown completion raises again, both without reading or sending.
    """
    limits = _limits(core, plan, upload_options, options, session_options)
    saved, identity = _resume_start(core, plan, source, state, limits)
    handle = AsyncUploadHandle(
        core, plan, limits, _session(limits), cast("AsyncUploadSource", source), identity, saved.chunk, saved.digests
    )
    _checked(core, handle, saved)
    _resumed(handle, saved, core)
    if handle._phase is not _Phase.UPLOADING:  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        handle._settled()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        return handle
    await handle._scan(saved.digests)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._verified(*await handle._probe())  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle
