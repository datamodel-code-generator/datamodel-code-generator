"""Resumable uploads of the offset profile: their creation, the handles that append chunks, checkpoints, and resume.

A helper's `start` measures the source, creates the upload in one child call, and returns a handle. Each `advance`
reads the unconfirmed bytes of the chunk holding the confirmed offset from the source and appends them, and an append
whose outcome is unknown is settled by probing the server's offset instead of sending it again blindly.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from io import SEEK_END
from types import CoroutineType, MappingProxyType
from typing import TYPE_CHECKING, Any, BinaryIO, Final, Generic, Literal, cast, final

from typing_extensions import Self, TypeVar

from ..client.bodies import AsyncBodyFactory, BodyFactory
from ..client.errors import (
    BudgetExceededError,
    DeliveryState,
    HTTPStatusError,
    ProtocolConfigurationError,
    TransportError,
    UnexpectedStatusError,
)
from ..client.options import RequestOptions
from ..client.timing import SYSTEM_CLOCK, Clock, SessionOptions
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
from .records import HeaderSelector
from .resume import (
    MalformedStateError,
    ResumeState,
    ResumeStateError,
    require_state,
    saved_expiry,
    state_array,
    state_count,
    state_expiry,
    state_fields,
    state_text,
)
from .sources import UploadProgress
from .values import MISSING, RepeatedValueError, selected, server_expiry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Generator, Iterator
    from datetime import datetime
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import OperationSession
    from ..client.operations import OperationPlan
    from ..client.responses import ResponseInfo
    from ..client.timing import Deadline
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .pagination import PageBinding
    from .records import ParameterTarget, ProtocolProgress, Selector
    from .references import OperationRef
    from .writes import Targeted

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
_UNKNOWN: Final = frozenset({DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED})
_STATE: Final = frozenset({"size", "chunk", "confirmed", "phase", "delivery", "bound", "expires_at"})
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299
_GATEWAY_STATUSES: Final = frozenset({502, 504})
_BOUND_FIELDS: Final = 3


class _Phase(Enum):
    UPLOADING = "uploading"
    UNKNOWN = "unknown"
    COMPLETE = "complete"


_PHASES: Final = MappingProxyType({phase.value: phase for phase in _Phase})


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
    `remote_offset`; `append` sends one chunk as its binary body, writing its offset into `offset`, its length into
    `length`, and into `checksum` the `checksum_algorithm` digest of the bytes it sends, in `checksum_encoding` and
    after the algorithm's name and a space with `checksum_prefix`. Chunks are at most `max_chunk_bytes`;
    `partial_commit` allows a server to keep part of one. A helper without `completion` completes when the server holds
    every byte; one with it sends that operation once.
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
    checksum: ParameterTarget | None = None
    checksum_algorithm: Literal["md5", "sha1", "sha256", "sha512"] = "sha256"
    checksum_encoding: Literal["base64", "hex"] = "base64"
    checksum_prefix: bool = False
    completion_operation: OperationRef | None = None
    completion: OperationPlan[T, object] | None = None
    completion_bindings: tuple[PageBinding, ...] = ()
    size_position: int | None = field(init=False)
    probed: Targeted[object] = field(init=False)
    appended: Targeted[object] = field(init=False)
    completed: Targeted[T] | None = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        """Derive the operations writing the bindings and each chunk's offset, length, and checksum, and the size."""
        from dataclasses import replace  # noqa: PLC0415

        from .writes import position, targeted_writes  # noqa: PLC0415 - Only a plan loads the operation runtime.

        probed = targeted_writes(self.probe, (binding.written for binding in self.probe_bindings))
        extra = tuple(target for target in (self.offset, self.length, self.checksum) if target is not None)
        appended = targeted_writes(self.append, (binding.written for binding in self.append_bindings), extra)
        appended = replace(appended, call=_unreplayed(appended.call))
        completion = self.completion
        completed = (
            None
            if completion is None
            else targeted_writes(completion, (binding.written for binding in self.completion_bindings))
        )
        children = (probed, appended, *(() if completed is None else (completed,)))
        headers, queries = set[str](), set[str]()
        if (size := self.size) is not None:
            (headers if size.location == "header" else queries).add(
                size.name.lower() if size.location == "header" else size.name
            )
        size_position = None if size is None else position(self.create, size.location, size.name)
        object.__setattr__(self, "size_position", size_position)
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
    max_uncertain_probes: int = 3
    total_timeout: float | None = None
    deadline: Deadline | None = None
    max_network_sends: int | None = 10000
    options: RequestOptions | None = None
    clock: Clock = SYSTEM_CLOCK


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
        max_uncertain_probes=layered(kinds, "max_uncertain_probes", _DEFAULTS.max_uncertain_probes),
        total_timeout=layered(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=layered(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=layered(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
        clock=core.clock,
    )


def _source_kind(value: object) -> Literal["iterable", "iterator", "stream", "reader"] | None:
    """Return the kind of a one-shot input that cannot be read again, or None for anything else."""
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


def _discarded(*values: object) -> bool:
    """Close the coroutines among values an asyncio file returned, and return whether there were any."""
    found = False
    for value in values:
        if isinstance(value, CoroutineType):
            value.close()
            found = True
    return found


class _Content:
    """The content of an upload source: bytes, or a seekable binary file from where it was positioned, and its size."""

    __slots__ = ("base", "file", "size", "source")

    def __init__(self, source: object, file: BinaryIO | None, base: int, size: int) -> None:
        self.source = source
        self.file = file
        self.base = base
        self.size = size

    def measured(self) -> int:
        """Return the size the content has now."""
        if (file := self.file) is None:
            with memoryview(cast("bytes", self.source)) as view:
                return view.nbytes
        return max(file.seek(0, SEEK_END) - self.base, 0)

    def read(self, plan: UploadPlan[Any, Any], start: int, length: int) -> bytes:
        """Return up to `length` bytes from an offset of the content, fewer only at its end."""
        if (file := self.file) is None:
            with memoryview(cast("bytes", self.source)) as view, view.cast("B") as flat:
                return flat[start : start + length].tobytes()
        file.seek(self.base + start)
        parts: list[bytes] = []
        left = length
        while left:
            data = cast("object", file.read(min(left, _READ)))
            if not isinstance(data, bytes) or len(data) > left:
                raise _invalid(plan, ("source",), "wrong_capability")
            if not data:
                break
            parts.append(data)
            left -= len(data)
        return b"".join(parts)


def _content(plan: UploadPlan[T, C], source: object) -> _Content:
    """Return the content of a source, refusing a one-shot input, a file that cannot seek, and any other value.

    A file must read bytes and seek synchronously; an empty read checks that before anything is sent.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = cast("bytes", source)
        with memoryview(data) as view:
            return _Content(data, None, 0, view.nbytes)
    file = cast("BinaryIO", source)
    if all(hasattr(file, name) for name in ("read", "seek", "tell")) and (
        (seekable := getattr(file, "seekable", None)) is None or seekable()
    ):
        empty, base, end = cast("tuple[object, object, object]", (file.read(0), file.tell(), file.seek(0, SEEK_END)))
        if _discarded(empty, base, end) or type(empty) is not bytes or type(base) is not int or type(end) is not int:
            raise _invalid(plan, ("source",), "wrong_capability")
        return _Content(source, file, base, max(end - base, 0))
    if (kind := _source_kind(source)) is None:
        raise _invalid(plan, ("source",))
    raise NonResumableSourceError(source_kind=kind, helper_id=plan.helper_id, operation=plan.operation)


def _layout(plan: UploadPlan[T, C], limits: _Limits, size: int, chunk: int) -> int:
    """Return the chunk size of an upload, refusing one the options do not allow.

    A resumed checkpoint keeps its chunk size, which must not exceed the call's `chunk_bytes`, since one chunk is what
    an append holds in memory.
    """
    if chunk > limits.chunk_bytes:
        raise _invalid(plan, ("upload_options", "chunk_bytes"))
    if (limit := limits.max_parts) is not None and -(-size // chunk) > limit:
        raise _invalid(plan, ("upload_options", "max_parts"))
    return chunk


class _Slices:
    """One attempt of an append's body: the unconfirmed part of a chunk's buffer, sent in read-buffer slices."""

    __slots__ = ("_view",)

    def __init__(self, view: memoryview) -> None:
        self._view = view

    @property
    def content_length(self) -> int:
        """Return the number of body bytes."""
        return len(self._view)

    @property
    def content_type(self) -> None:
        """Name no media type; the operation's request media names it."""

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the body in slices of at most one read buffer."""
        view = self._view
        for start in range(0, len(view), _READ):
            yield bytes(view[start : start + _READ])

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the body in slices of at most one read buffer."""
        for data in self.iter_bytes():
            yield data

    def close(self) -> None:
        """Hold nothing to release."""

    async def aclose(self) -> None:
        """Hold nothing to release."""


class _Factory:
    """Build a new attempt of the same unconfirmed bytes for each send of an append."""

    __slots__ = ("_view",)

    def __init__(self, view: memoryview) -> None:
        self._view = view

    def __call__(self, context: object, /) -> _Slices:
        """Return a new attempt of the bytes."""
        del context
        return _Slices(self._view)


class _AsyncFactory:
    """Build a new asyncio attempt of the same unconfirmed bytes for each send of an append."""

    __slots__ = ("_view",)

    def __init__(self, view: memoryview) -> None:
        self._view = view

    async def __call__(self, context: object, /) -> _Slices:
        """Return a new attempt of the bytes."""
        del context
        return _Slices(self._view)


class _Upload(Generic[T]):
    """What the synchronous and asyncio handles share: the plan, limits, session, content, and confirmed offset.

    Only a settled child call changes the confirmed offset. A failed append leaves it and makes the next step probe the
    server first; a source whose size changed keeps its checkpoint but sends nothing more.
    """

    __slots__ = (
        "_bound",
        "_changed",
        "_chunk",
        "_closed",
        "_completion_delivery",
        "_confirmed",
        "_content",
        "_delivery",
        "_expires_at",
        "_guard",
        "_high",
        "_limits",
        "_lock",
        "_phase",
        "_plan",
        "_result",
        "_session",
        "_size",
        "_verify",
    )

    def __init__(
        self, plan: UploadPlan[T, Any], limits: _Limits, session: OperationSession, content: _Content, chunk: int
    ) -> None:
        """Keep the plan, limits, session, the borrowed source's content, and the chunk size."""
        self._plan = plan
        self._limits = limits
        self._session = session
        self._content = content
        self._size = content.size
        self._chunk = chunk
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self._phase = _Phase.UPLOADING
        self._delivery: DeliveryState | None = None
        self._completion_delivery = DeliveryState.NOT_SENT
        self._confirmed = 0
        self._verify = False
        self._high: int | None = 0
        self._closed = False
        self._changed = False
        self._bound: tuple[tuple[WireValue, ...], tuple[WireValue, ...], tuple[WireValue, ...]] = ((), (), ())
        self._expires_at: datetime | None = None
        self._result: T | None = None

    def __repr__(self) -> str:
        """Name the handle's phase and its confirmed and total bytes only, never its data."""
        return (
            f"{type(self).__name__}(phase={self._phase.value!r}, confirmed_bytes={self._confirmed}, "
            f"total_bytes={self._size})"
        )

    def _progress(self) -> UploadProgress:
        return UploadProgress(
            confirmed_bytes=self._confirmed,
            total_bytes=self._size,
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
            if confirmed == self._size and self._plan.completion is None:
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
            size=self._size,
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
        self, targeted: Targeted[Any], bindings: tuple[PageBinding, ...], wire: WireValue, info: ResponseInfo
    ) -> tuple[WireValue, ...]:
        """Return what bindings write: their literals and what the create response gives, refusing dot segments."""
        written = tuple(
            binding.literal if binding.selector is None else self._read(binding.selector, wire, info)
            for binding in bindings
        )
        _dotted(self._plan, targeted, written, info)
        return written

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
            if (expires_at := server_expiry(value, self._limits.clock.time())) is None:
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
        confirmed, size = self._confirmed, self._size
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

    def _buffer(self) -> tuple[memoryview, int]:
        """Read the unconfirmed bytes of the chunk holding the confirmed offset, and return them with the chunk's end.

        The last chunk is read with one byte more, to find content past its end. Content whose size is not the upload's
        stops every later send.
        """
        size, start = self._size, self._confirmed
        end = min((start // self._chunk + 1) * self._chunk, size)
        content = self._content
        data = content.read(self._plan, start, end - start + (end == size))
        if len(data) == end - start:
            return memoryview(data), end
        with self._guard:
            self._changed = True
        plan = self._plan
        raise UploadSourceChangedError(
            expected_size=size,
            actual_size=content.measured(),
            helper_id=plan.helper_id,
            operation=plan.operation,
            parent_session_id=self._session.session_id,
        )

    def _append_request(
        self, payload: object, data: memoryview
    ) -> Callable[[], tuple[tuple[object, ...], object, None]]:
        plan = self._plan
        arguments = plan.appended.request((*self._bound[1], *_written(plan, self._confirmed, data)))[0]
        return lambda: (arguments, payload, None)

    def _probe_request(self) -> tuple[tuple[object, ...], object, None]:
        return (*self._plan.probed.request(self._bound[0]), None)

    def _completion_request(self) -> tuple[tuple[object, ...], object, None]:
        completed = self._plan.completed
        assert completed is not None
        return (*completed.request(self._bound[2]), None)

    def _completed(
        self, data: T, _wire: WireValue, _content: bytes, _info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> T:
        """Keep the completion's result."""
        with self._guard:
            self._result, self._phase, self._delivery = data, _Phase.COMPLETE, None
        return data

    def _completing(self) -> None:
        """Keep a provisional unknown checkpoint while the completion child is active."""
        with self._guard:
            self._phase, self._delivery = _Phase.UNKNOWN, DeliveryState.MAYBE_SENT
            self._completion_delivery = DeliveryState.NOT_SENT

    def _completion_observed(self, _error: BaseException, delivery: DeliveryState) -> None:
        """Keep the completion child's resource evidence separately from OAuth traffic."""
        self._completion_delivery = delivery

    def _completion_failed(self, error: BaseException) -> None:
        """Restore uploading for an unapplied completion; keep actual resource uncertainty unknown."""
        delivery = self._completion_delivery
        with self._guard:
            if _refused(error) or delivery is DeliveryState.NOT_SENT:
                self._phase, self._delivery = _Phase.UPLOADING, None
            elif self._phase is _Phase.UNKNOWN:
                self._delivery = delivery

    def _completion_error(self, error: Exception) -> Exception:
        """Return the error of a failed completion: its own once it may be sent again, or else an unknown outcome."""
        if (delivery := self._delivery) is None or self._phase is not _Phase.UNKNOWN:
            return error
        return self._unknown(phase="complete", delivery=delivery, error=error)

    def checkpoint(self) -> ResumeState:
        """Return the state a later `resume` continues from, sending nothing; a complete upload has none.

        It keeps the upload's size and chunk size, the confirmed offset, a completion of unknown outcome, the values
        later calls write, and the server's expiry, under the helper's identity.
        """
        with self._guard:
            if (phase := self._phase) is _Phase.COMPLETE:
                action = "checkpoint"
                raise self._state_error(action, phase.value)
            return self._checkpoint()

    def _checkpoint(self) -> ResumeState:
        delivery = self._delivery
        state: WireValue = {
            "size": self._size,
            "chunk": self._chunk,
            "confirmed": self._confirmed,
            "phase": self._phase.value,
            "delivery": None if delivery is None else delivery.value,
            "bound": self._bound,
            "expires_at": saved_expiry(self._expires_at),
        }
        return ResumeState(helper=self._plan.fingerprint, state=state)


def _dotted(
    plan: UploadPlan[Any, Any], targeted: Targeted[Any], written: tuple[WireValue, ...], info: ResponseInfo | None
) -> None:
    """Refuse read values that make a path segment a dot segment once encoded, as data a server gave."""
    from .writes import dotted_write  # noqa: PLC0415 - A plan loaded the operation runtime.

    if (read := dotted_write(targeted, written)) is not None:
        raise ProtocolDataError(
            condition="value", location=read, helper_id=plan.helper_id, operation=plan.operation, info=info
        )


def _written(plan: UploadPlan[Any, Any], offset: int, data: memoryview | bytes) -> tuple[WireValue, ...]:
    """Return what an append writes after its bindings: its offset, and the length and checksum of what it sends."""
    values: list[WireValue] = [offset]
    if plan.length is not None:
        values.append(len(data))
    if plan.checksum is not None:
        values.append(_checksum(plan, data))
    return tuple(values)


def _checksum(plan: UploadPlan[Any, Any], data: memoryview | bytes) -> str:
    """Return the declared checksum of bytes: their digest in its encoding, after the algorithm's name when declared."""
    from hashlib import new  # noqa: PLC0415 - Only a declared checksum loads other digests.

    digest = new(plan.checksum_algorithm, data, usedforsecurity=False).digest()
    if plan.checksum_encoding == "hex":
        text = digest.hex()
    else:
        from base64 import b64encode  # noqa: PLC0415 - Only a base64 checksum loads the encoder.

        text = b64encode(digest).decode("ascii")
    return f"{plan.checksum_algorithm} {text}" if plan.checksum_prefix else text


def _refused(error: BaseException) -> bool:
    """Return whether the server answered a call with an error status, so it did not apply the call.

    A 502 or 504 proves nothing: a gateway answers with it when the server behind it may have applied the call.
    """
    return (
        isinstance(error, (HTTPStatusError, UnexpectedStatusError))
        and not _MIN_SUCCESS <= (status := error.info.status_code) <= _MAX_SUCCESS
        and status not in _GATEWAY_STATUSES
    )


def _probed_again(error: Exception) -> bool:
    """Return whether a probe failed in a way another probe may settle: a transport error or an error status."""
    return isinstance(error, (TransportError, HTTPStatusError, UnexpectedStatusError))


def _session(limits: _Limits) -> OperationSession:
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only a started helper loads the call runtime.

    return OperationSession(
        total_timeout=limits.total_timeout,
        deadline=limits.deadline,
        max_network_sends=limits.max_network_sends,
        clock=limits.clock,
    )


@final
class UploadHandle(_Upload[T]):
    """A resumable upload a helper created or resumed: `advance` appends one chunk, and `run` uploads the rest.

    `close` only stops local uploading; the remote upload stays. Uploading from two threads at once raises
    ProtocolStateError.
    """

    __slots__ = ("_core",)

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        core: ClientCore,
        plan: UploadPlan[T, Any],
        limits: _Limits,
        session: OperationSession,
        content: _Content,
        chunk: int,
    ) -> None:
        """Keep the client core the handle's child calls are sent through."""
        super().__init__(plan, limits, session, content, chunk)
        self._core = core

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
        start = self._confirmed
        buffer, end = self._buffer()
        while self._confirmed < end:
            unconfirmed = buffer[self._confirmed - start :]
            payload = BodyFactory(_Factory(unconfirmed), content_length=len(unconfirmed))
            self._sending(end)
            try:
                with self._mapped():
                    self._core.execute_page(
                        self._plan,
                        self._plan.appended.call,
                        self._append_request(payload, unconfirmed),
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
        self._completing()
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
                        failed=self._completion_observed,
                    )
                except BaseException as error:
                    self._completion_failed(error)
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
        if self._confirmed < self._size:
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

    __slots__ = ("_core",)

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        core: AsyncClientCore,
        plan: UploadPlan[T, Any],
        limits: _Limits,
        session: OperationSession,
        content: _Content,
        chunk: int,
    ) -> None:
        """Keep the asyncio client core the handle's child calls are sent through."""
        super().__init__(plan, limits, session, content, chunk)
        self._core = core

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
        start = self._confirmed
        buffer, end = self._buffer()
        while self._confirmed < end:
            unconfirmed = buffer[self._confirmed - start :]
            payload = AsyncBodyFactory(_AsyncFactory(unconfirmed), content_length=len(unconfirmed))
            self._sending(end)
            try:
                with self._mapped():
                    await self._core.execute_page(
                        self._plan,
                        self._plan.appended.call,
                        self._append_request(payload, unconfirmed),
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
        self._completing()
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
                        failed=self._completion_observed,
                    )
                except BaseException as error:
                    self._completion_failed(error)
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
        if self._confirmed < self._size:
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


def _coded(
    plan: UploadPlan[T, C],
    limits: _Limits,
    remaining: int,
    body: object = UNSET,
    *,
    saved: _Saved | None = None,
) -> None:
    """Admit explicit compression only for reachable requests with a declared coding and a provable body."""
    if (options := limits.options) is not None and isinstance(selected := options.compression, str):
        from ..client.compression import helper_children  # noqa: PLC0415 - Only a selected coding loads the encoder.

        children: list[tuple[OperationPlan[Any, object], bool]] = (
            [] if saved is not None else [(plan.create, body is not UNSET)]
        )
        if remaining:
            children.append((plan.append, True))
        if (saved is None or saved.phase is _Phase.UPLOADING) and (completed := plan.completed) is not None:
            children.append((completed.call, any(position is None for position, _ in completed.writes)))
        helper_children(selected, children)


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
    """Measure the source, create the helper's upload in a session of its own, and return the handle appending to it."""
    limits = _limits(core, plan, upload_options, options, session_options)
    content = _content(plan, source)
    chunk = _layout(plan, limits, content.size, min(limits.chunk_bytes, plan.max_chunk_bytes))
    _coded(plan, limits, content.size, body)
    handle = UploadHandle(core, plan, limits, _session(limits), content, chunk)
    handle._create(_sized(core, plan, arguments, content.size), body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
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
    """Measure the source, create the helper's upload with asyncio in a session of its own, and return its handle."""
    limits = _limits(core, plan, upload_options, options, session_options)
    content = _content(plan, source)
    chunk = _layout(plan, limits, content.size, min(limits.chunk_bytes, plan.max_chunk_bytes))
    _coded(plan, limits, content.size, body)
    handle = AsyncUploadHandle(core, plan, limits, _session(limits), content, chunk)
    await handle._create(_sized(core, plan, arguments, content.size), body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle


@dataclass(frozen=True, slots=True)
class _Saved:
    """A checkpoint's decoded state, checked against the helper resuming it."""

    size: int
    chunk: int
    confirmed: int
    phase: _Phase
    delivery: DeliveryState | None
    bound: tuple[tuple[WireValue, ...], tuple[WireValue, ...], tuple[WireValue, ...]]
    expires_at: datetime | None


def _resume_error(plan: UploadPlan[Any, Any], condition: Literal["fingerprint", "malformed"]) -> ResumeStateError:
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


def _decoded(plan: UploadPlan[Any, Any], state: WireValue) -> _Saved:
    """Return a checkpoint's state, refusing one whose form does not fit the helper.

    Only an upload in progress or one whose completion's outcome is unknown is saved.
    """
    from collections.abc import Mapping  # noqa: PLC0415

    require_state(isinstance(state, Mapping) and frozenset(state) == _STATE)
    fields = cast("Mapping[str, WireValue]", state)
    size, chunk, confirmed = (state_count(fields[name]) for name in ("size", "chunk", "confirmed"))
    phase, delivery = (state_text(fields[name]) for name in ("phase", "delivery"))
    require_state(0 < chunk <= plan.max_chunk_bytes and confirmed <= size and phase in _PHASES)
    resolved = _PHASES[cast("str", phase)]
    require_state(
        resolved is not _Phase.COMPLETE
        and (delivery is None) == (resolved is _Phase.UPLOADING)
        and (delivery is None or delivery in {state.value for state in _UNKNOWN})
        and (resolved is _Phase.UPLOADING or (confirmed == size and plan.completion is not None))
    )
    bound = cast(
        "tuple[tuple[WireValue, ...], tuple[WireValue, ...], tuple[WireValue, ...]]", _bound(plan, fields["bound"])
    )
    return _Saved(
        size=size,
        chunk=chunk,
        confirmed=confirmed,
        phase=resolved,
        delivery=None if delivery is None else DeliveryState(delivery),
        bound=bound,
        expires_at=state_expiry(fields["expires_at"]),
    )


def _restored(plan: UploadPlan[T, C], state: object, limits: _Limits) -> _Saved:
    """Return what a checkpoint saved: this helper's, fitting it, and unexpired by the client's clock."""
    if not isinstance(state, ResumeState):
        raise _invalid(plan, ("state",))
    helper, state_json = state_fields(state)
    if helper != plan.fingerprint:
        raise _resume_error(plan, "fingerprint")
    try:
        saved = _decoded(plan, decode_json(state_json))
    except MalformedStateError:
        raise _resume_error(plan, "malformed") from None
    if (expires_at := saved.expires_at) is not None and expires_at.timestamp() <= limits.clock.time():
        raise UploadExpiredError(expires_at=expires_at, helper_id=plan.helper_id, operation=plan.operation)
    return saved


def _checked(
    core: ClientCore | AsyncClientCore, plan: UploadPlan[Any, Any], options: RequestOptions | None, saved: _Saved
) -> None:
    """Prepare each request the saved values write as its call would, refusing values that cannot be sent.

    Read values that make a path segment a dot segment raise ProtocolDataError, as if a server gave them; any other
    refusal of a saved value makes the checkpoint malformed.
    """
    from ..client.errors import RequestEncodingError  # noqa: PLC0415 - Only a resume checks saved values.

    probe, append, completion = saved.bound
    offsets = _written(plan, 0, b"")
    requests: list[tuple[Targeted[Any], tuple[WireValue, ...], tuple[WireValue, ...], object]] = [
        (plan.probed, probe, (), None),
        (plan.appended, append, offsets, b""),
    ]
    if (completed := plan.completed) is not None:
        requests.append((completed, completion, (), None))
    for targeted, values, extra, payload in requests:
        _dotted(plan, targeted, values, None)
        arguments, body = targeted.request((*values, *extra))
        try:
            core.checked_page(targeted.call, _fixed(arguments, body if payload is None else payload), None, options)
        except (RequestEncodingError, ProtocolDataError, CodecError):
            raise _resume_error(plan, "malformed") from None


def _fixed(arguments: tuple[object, ...], body: object) -> Callable[[], tuple[tuple[object, ...], object, None]]:
    """Return the request of a check, built once."""
    return lambda: (arguments, body, None)


def _resume_start(
    core: ClientCore | AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    limits: _Limits,
) -> tuple[_Saved, _Content]:
    """Check the checkpoint, its layout and saved values, then the source's size, before any read or send."""
    saved = _restored(plan, state, limits)
    _layout(plan, limits, saved.size, saved.chunk)
    _checked(core, plan, limits.options, saved)
    content = _content(plan, source)
    if content.size != saved.size:
        raise UploadSourceChangedError(
            expected_size=saved.size, actual_size=content.size, helper_id=plan.helper_id, operation=plan.operation
        )
    _coded(plan, limits, saved.size - saved.confirmed, saved=saved)
    return saved, content


def _resumed(handle: _Upload[T], saved: _Saved) -> _Upload[T]:
    """Restore a handle to a checkpoint's phase, offset, values, and expiry; an unknown completion raises again."""
    handle._bound = saved.bound  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._confirmed = saved.confirmed  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._verify = True  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._high = None  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._expires_at = saved.expires_at  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._phase, handle._delivery = saved.phase, saved.delivery  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._settled()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle


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
    """Continue a checkpoint in a new session: check the source's size against it, then probe the server's offset once.

    A completion of unknown outcome raises again, without reading or sending.
    """
    limits = _limits(core, plan, upload_options, options, session_options)
    saved, content = _resume_start(core, plan, source, state, limits)
    handle = UploadHandle(core, plan, limits, _session(limits), content, saved.chunk)
    _resumed(handle, saved)
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
    """Continue a checkpoint with asyncio in a new session: check the source's size against it, then probe once.

    A completion of unknown outcome raises again, without reading or sending.
    """
    limits = _limits(core, plan, upload_options, options, session_options)
    saved, content = _resume_start(core, plan, source, state, limits)
    handle = AsyncUploadHandle(core, plan, limits, _session(limits), content, saved.chunk)
    _resumed(handle, saved)
    handle._verified(*await handle._probe())  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle
