"""Resumable upload creation, offset and parts handles, checkpoints, and resume.

A helper's `start` scans the source, creates the upload in one child call, and returns a handle. Each `advance`
appends the chunk holding the confirmed offset, read again and checked against the digest the scan recorded, and an
append whose outcome is unknown is settled by probing the server's offset instead of sending it again blindly.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import partial
from hashlib import sha256
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, cast, final

from typing_extensions import Self, TypeVar, overload

from ..client.bodies import AsyncBodyFactory, BodyFactory
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
from ..client.timing import SYSTEM_CLOCK, CancelToken, Clock, SessionOptions
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
from .sources import PartReceipt, UploadIdentity, UploadProgress
from .values import MISSING, RepeatedValueError, selected, server_expiry

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator, Callable, Generator, Iterator
    from concurrent.futures import Future, ThreadPoolExecutor
    from datetime import datetime
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import OperationPlan
    from ..client.responses import ResponseInfo
    from ..client.timing import Deadline
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .pagination import PageBinding
    from .records import ParameterTarget, ProtocolProgress, RequestTarget, Selector
    from .references import OperationRef
    from .sources import AsyncRangeReader, AsyncUploadSource, RangeReader, UploadSource
    from .writes import Targeted

__all__ = (
    "AsyncPartsUploadHandle",
    "AsyncUploadHandle",
    "PartsUploadHandle",
    "UploadAbortPlan",
    "UploadCompletionProbePlan",
    "UploadHandle",
    "UploadPartsPlan",
    "UploadPlan",
    "aresume_upload",
    "astart_upload",
    "resume_upload",
    "start_upload",
)

T = TypeVar("T")
C = TypeVar("C")
H = TypeVar("H", bound="UploadHandle[Any]")
A = TypeVar("A", bound="AsyncUploadHandle[Any]")
K = TypeVar("K")

_READ: Final = 65536
_DIGEST: Final = 32
_UNKNOWN: Final = frozenset({DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED})
_STATE: Final = frozenset({"size", "sha256", "chunk", "confirmed", "phase", "delivery", "bound", "result"})
_MANIFEST_BYTES: Final = MAX_STATE_BYTES - 1024 * 1024
_RESULT_FIELDS: Final = 2
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299
_BOUND_FIELDS: Final = 3


class _Phase(Enum):
    UPLOADING = "uploading"
    UNKNOWN = "unknown"
    COMPLETE = "complete"
    ABORTED = "aborted"


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


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadAbortPlan(Generic[K]):
    """The typed remote abort operation and its create-response bindings."""

    operation: OperationRef
    call: OperationPlan[K, object]
    bindings: tuple[PageBinding, ...] = ()
    targeted: Targeted[K] = field(init=False)

    def __post_init__(self) -> None:
        """Prepare the abort's bound request."""
        from .writes import targeted_writes  # ruff: ignore[import-outside-top-level]

        object.__setattr__(self, "targeted", targeted_writes(self.call, (item.written for item in self.bindings)))


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadCompletionProbePlan:
    """A read operation confirming a completed upload's result."""

    call: OperationPlan[object, object]
    state: Selector
    completed_values: tuple[WireValue, ...]
    result: Selector
    result_status: int
    result_media: str
    bindings: tuple[PageBinding, ...] = ()
    targeted: Targeted[object] = field(init=False)

    def __post_init__(self) -> None:
        """Prepare the probe's bound request."""
        from .writes import targeted_writes  # ruff: ignore[import-outside-top-level]

        object.__setattr__(self, "targeted", targeted_writes(self.call, (item.written for item in self.bindings)))


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadPartsPlan:
    """Indexed parts, their receipts, explicit digests, and the server's layout limits."""

    items: Selector
    index: Selector
    receipt: Selector
    parts: RequestTarget
    index_field: str
    receipt_field: str
    digest: Selector | None = None
    digest_target: ParameterTarget | None = None
    digest_encoding: Literal["hex", "base64"] = "hex"
    max_parts: int | None = None
    min_part_bytes: int = 1
    last_part_may_be_smaller: bool = False


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class UploadPlan(Generic[T, C]):
    """Everything fixed about one generated upload helper.

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
    session_url: Selector | None = None
    abort: UploadAbortPlan[Any] | None = None
    parts: UploadPartsPlan | None = None
    completion_probe: UploadCompletionProbePlan | None = None
    probe_bindings: tuple[PageBinding, ...] = ()
    append_bindings: tuple[PageBinding, ...] = ()
    length: ParameterTarget | None = None
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
        """Derive the operations writing the bindings, the chunk's offset and length, and the size's position."""
        from dataclasses import replace  # noqa: PLC0415

        from .writes import position, targeted_writes  # noqa: PLC0415 - Only a plan loads the operation runtime.

        probed = targeted_writes(self.probe, (binding.written for binding in self.probe_bindings))
        extra = (self.offset,) if self.length is None else (self.offset, self.length)
        if self.parts is not None and self.parts.digest_target is not None:
            extra = (*extra, self.parts.digest_target)
        appended = targeted_writes(self.append, (binding.written for binding in self.append_bindings), extra)
        appended = replace(appended, call=_unreplayed(appended.call))
        completion = self.completion
        completed = (
            None
            if completion is None
            else targeted_writes(
                completion,
                (binding.written for binding in self.completion_bindings),
                () if self.parts is None else (self.parts.parts,),
            )
        )
        if completed is not None:
            completed = replace(completed, call=_unreplayed(completed.call))
        children = (
            probed,
            appended,
            *(() if completed is None else (completed,)),
            *(item.targeted for item in (self.abort, self.completion_probe) if item is not None),
        )
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
    parallelism: int = 4
    max_uncertain_probes: int = 3
    total_timeout: float | None = 600.0
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
        parallelism=layered(kinds, "parallelism", _DEFAULTS.parallelism),
        max_uncertain_probes=layered(kinds, "max_uncertain_probes", _DEFAULTS.max_uncertain_probes),
        total_timeout=layered(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=layered(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=layered(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
        clock=core.clock,
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


def _layout(plan: UploadPlan[T, C], limits: _Limits, size: int, chunk: int) -> int:
    """Return the chunk size of an upload, refusing one the options do not allow or a digest list too large to keep.

    A resumed checkpoint keeps its chunk size, which must not exceed the call's `chunk_bytes`, since one chunk is what
    an append holds in memory. The digests must leave room in a checkpoint for its other members.
    """
    if chunk > limits.chunk_bytes:
        raise _invalid(plan, ("upload_options", "chunk_bytes"))
    count = -(-size // chunk)
    if (parts := plan.parts) is not None:
        if chunk < parts.min_part_bytes:
            raise _invalid(plan, ("upload_options", "chunk_bytes"))
        if size and not parts.last_part_may_be_smaller and (size - (count - 1) * chunk) < parts.min_part_bytes:
            raise _invalid(plan, ("source", "size"))
        if parts.max_parts is not None and count > parts.max_parts:
            raise _invalid(plan, ("limits", "max_parts"))
    if (limit := limits.max_parts) is not None and count > limit:
        raise _invalid(plan, ("upload_options", "max_parts"))
    if (encoded := 4 * -(-_DIGEST * count // 3)) > _MANIFEST_BYTES:
        raise ProtocolSizeError(
            kind="part_manifest",
            limit=_MANIFEST_BYTES,
            observed=encoded,
            unit="bytes",
            helper_id=plan.helper_id,
            operation=plan.operation,
        )
    return chunk


class _Chunk:
    """One chunk read into a buffer of its own, hashed as it is read; one byte more shows content past its end."""

    __slots__ = ("digest", "read", "view")

    def __init__(self, size: int) -> None:
        self.view = memoryview(bytearray(size))
        self.digest = sha256()
        self.read = 0

    def add(self, data: bytes) -> None:
        """Copy bytes read after those before into the buffer and hash them."""
        end = self.read + len(data)
        available = min(len(data), len(self.view) - self.read)
        if available > 0:
            self.view[self.read : self.read + available] = data[:available]
        self.digest.update(data)
        self.read = end


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
        "_controls",
        "_core",
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
        "_source",
        "_url",
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
        self._controls: tuple[tuple[WireValue, ...], ...] = ((), ())
        self._url: str | None = None
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
        if self._closed or self._changed or self._phase is _Phase.ABORTED:
            self._lock.release()
            raise self._state_error(
                action, "closed" if self._closed else "source_changed" if self._changed else "aborted"
            )

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
        phase: Literal["append", "part", "complete"],
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
        self._controls = tuple(
            () if item is None else self._values(item.targeted, item.bindings, wire, info)
            for item in (plan.abort, plan.completion_probe)
        )
        if (read := plan.session_url) is not None:
            value = self._read(read, wire, info)
            if not isinstance(value, str):
                raise self._data_error(info, "type", read)
            self._url = self._follow(value, _url, info, _managed)
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

    @property
    def _client(self) -> ClientCore | AsyncClientCore:
        """The client core shared URL and result checks use."""
        raise NotImplementedError

    def _follow(self, value: str, url: str, info: ResponseInfo | None, stripped: frozenset[str] = frozenset()) -> str:
        """Apply the shared next-URL origin and credential policy to a session URL."""
        from .pagination import _followed  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        plan = self._plan
        read = plan.session_url
        assert read is not None
        origins = self._client.follow_origins(plan.create, self._limits.options)
        return _followed(plan, read, value, url, info, 8192, origins, stripped)

    def _abort_request(self, abort: UploadAbortPlan[K]) -> tuple[tuple[object, ...], object, str | None]:
        return (*abort.targeted.request(self._controls[0]), self._url)

    def _aborted(
        self, data: K, _wire: WireValue, _content: bytes, _info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> K:
        with self._guard:
            self._phase, self._delivery = _Phase.ABORTED, None
        return data

    def _abort_enter(self) -> None:
        self._enter("abort_remote")
        if self._phase is _Phase.COMPLETE:
            self._lock.release()
            error = self._state_error("abort_remote", "complete")
            raise error

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

    def _chunked(self, chunk: _Chunk, index: int, size: int) -> memoryview:
        """Return a chunk's bytes once they are those the scan read; any others stop every later send."""
        expected = self._digests[index * _DIGEST : (index + 1) * _DIGEST]
        if chunk.read == size and chunk.digest.digest() == expected:
            return chunk.view[:size]
        with self._guard:
            self._changed = True
        plan = self._plan
        raise UploadSourceChangedError(
            expected=UploadIdentity(size=size, sha256=expected),
            actual=UploadIdentity(size=chunk.read, sha256=chunk.digest.digest()),
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

    def _append_request(
        self, payload: object, size: int
    ) -> Callable[[], tuple[tuple[object, ...], object, str | None]]:
        plan, offset = self._plan, self._confirmed
        values = (offset,) if plan.length is None else (offset, size)
        arguments = plan.appended.request((*self._bound[1], *values))[0]
        return lambda: (arguments, payload, self._url)

    def _probe_request(self) -> tuple[tuple[object, ...], object, str | None]:
        return (*self._plan.probed.request(self._bound[0]), self._url)

    def _completion_request(self) -> tuple[tuple[object, ...], object, str | None]:
        completed = self._plan.completed
        assert completed is not None
        return (*completed.request(self._bound[2]), self._url)

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
        delivery = _delivery_of(error)
        unapplied = delivery not in _UNKNOWN and (
            _refused(error) or delivery is DeliveryState.NOT_SENT or self._session.network_send_count == sends
        )
        started = (isinstance(error, SDKError) and error.info is not None) or delivery is DeliveryState.RESPONSE_STARTED
        with self._guard:
            if unapplied:
                self._phase, self._delivery = _Phase.UPLOADING, None
            else:
                self._phase = _Phase.UNKNOWN
                self._delivery = DeliveryState.RESPONSE_STARTED if started else DeliveryState.MAYBE_SENT
                self._result, self._saved = None, None

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
        state: dict[str, WireValue] = {
            "size": identity.size,
            "sha256": identity.sha256.hex(),
            "chunk": self._chunk,
            "confirmed": self._confirmed,
            "phase": self._phase.value,
            "delivery": None if delivery is None else delivery.value,
            "bound": self._bound,
            "result": None if saved is None else (saved[1], saved[2]),
        }
        if plan.abort is not None or plan.completion_probe is not None:
            state["controls"] = self._controls
        if plan.session_url is not None:
            state["url"] = self._url
        self._checkpoint_extra(state)
        return helper_state(
            helper_fingerprint=plan.fingerprint,
            security_fingerprint=sha256(canonical_json(tuple(facts))).hexdigest(),
            state=state,
            payload=self._digests + (b"" if saved is None else saved[0]),
            exportable=exportable,
            expires_at=self._expires_at,
        )

    def _checkpoint_extra(self, state: WireValue) -> None:  # noqa: PLR6301
        """Keep no additional offset-profile state."""
        del state

    def _core_security(
        self, call: OperationPlan[Any, object], options: RequestOptions | None
    ) -> tuple[WireValue, bool]:
        raise NotImplementedError


def _dotted(
    plan: UploadPlan[Any, Any], targeted: Targeted[Any], written: tuple[WireValue, ...], info: ResponseInfo | None
) -> None:
    """Refuse read values that make a path segment a dot segment once encoded, as data a server gave."""
    from .writes import dotted_write  # noqa: PLC0415 - A plan loaded the operation runtime.

    if (read := dotted_write(targeted, written)) is not None:
        raise ProtocolDataError(
            condition="value", location=read, helper_id=plan.helper_id, operation=plan.operation, info=info
        )


def _refused(error: BaseException) -> bool:
    """Return whether the server answered a call with an error status, so it did not apply the call."""
    return (
        isinstance(error, (HTTPStatusError, UnexpectedStatusError))
        and not _MIN_SUCCESS <= error.info.status_code <= _MAX_SUCCESS
        and error.info.status_code not in {502, 504}
    )


def _delivery_of(error: BaseException) -> DeliveryState | None:
    """Return how far a failed call got, when its error says."""
    delivery: object = getattr(error, "delivery_state", None)
    return delivery if isinstance(delivery, DeliveryState) else None


def _probed_again(error: Exception) -> bool:
    """Return whether a probe failed in a way another probe may settle: a transport error or an error status."""
    return isinstance(error, (TransportError, HTTPStatusError, UnexpectedStatusError))


def _children(plan: UploadPlan[Any, Any]) -> tuple[OperationPlan[Any, object], ...]:
    """Return the operations a resumed upload may send: the probe, the append, and any completion."""
    return (
        plan.probe,
        plan.append,
        *(
            item
            for item in (
                plan.completion,
                None if plan.abort is None else plan.abort.call,
                None if plan.completion_probe is None else plan.completion_probe.call,
            )
            if item is not None
        ),
    )


def _session(limits: _Limits) -> OperationSession:
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only a started helper loads the call runtime.

    return OperationSession(
        total_timeout=limits.total_timeout,
        deadline=limits.deadline,
        max_network_sends=limits.max_network_sends,
        clock=limits.clock,
    )


def _changed(
    plan: UploadPlan[Any, Any], expected: UploadIdentity, actual: UploadIdentity | None, offset: int | None = None
) -> UploadSourceChangedError:
    return UploadSourceChangedError(
        expected=expected, actual=actual, offset=offset, helper_id=plan.helper_id, operation=plan.operation
    )


def _opened(plan: UploadPlan[Any, Any], source: Any, start: int, length: int, method: str) -> Any:
    """Open a range of a source, refusing one whose context is not of this client's kind.

    A refused coroutine, such as an asyncio `open_range` a synchronous client called, is closed unawaited.
    """
    opened = source.open_range(start, length)
    if not hasattr(opened, method):
        if (close := getattr(opened, "close", None)) is not None:
            close()
        raise _invalid(plan, ("source",), "wrong_capability")
    return opened


def _bytes(plan: UploadPlan[Any, Any], data: object, limit: int) -> bytes:
    """Return what a reader read, refusing anything but bytes and more bytes than it was asked for."""
    if not isinstance(data, bytes) or len(data) > limit:
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


class UploadHandle(_Upload[T]):
    """A resumable upload a helper created or resumed: `advance` appends one chunk, and `run` uploads the rest.

    `close` only stops local uploading; the remote upload stays. Uploading from two threads at once raises
    ProtocolStateError.
    """

    _core: ClientCore

    __slots__ = ()

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

    @property
    def _client(self) -> ClientCore:
        """The client core that sends this handle's child calls."""
        return self._core

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
                    wanted = _wanted(size, got)
                    if not (data := _bytes(plan, opened.read(wanted), wanted)):
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

    def _buffer(self, index: int, start: int, size: int) -> memoryview:
        """Read one chunk into its own buffer and check it against the digest the scan recorded."""
        chunk = _Chunk(size)
        self._read_all(start, size, chunk.add)
        return self._chunked(chunk, index, size)

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
            unconfirmed = buffer[self._confirmed - start :]
            payload = BodyFactory(_Factory(unconfirmed), content_length=len(unconfirmed))
            self._sending(end)
            try:
                with self._mapped():
                    self._core.execute_page(
                        self._plan,
                        self._plan.appended.call,
                        self._append_request(payload, len(unconfirmed)),
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

    def _abort_remote(self, abort: UploadAbortPlan[K]) -> K:
        """Abort remotely when declared, retaining the aborted checkpoint."""
        self._abort_enter()
        try:
            with self._mapped():
                return self._core.execute_page(
                    self._plan,
                    abort.targeted.call,
                    partial(self._abort_request, abort),
                    self._aborted,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=self._session,
                    max_page_bytes=None,
                )
        finally:
            self._lock.release()

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


class AsyncUploadHandle(_Upload[T]):
    """A resumable upload an asyncio helper created or resumed: `advance` appends one chunk, `run` uploads the rest.

    `aclose` only stops local uploading; the remote upload stays. Uploading from two tasks at once raises
    ProtocolStateError.
    """

    _core: AsyncClientCore

    __slots__ = ()

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

    @property
    def _client(self) -> AsyncClientCore:
        """The client core that sends this handle's child calls."""
        return self._core

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
                    wanted = _wanted(size, got)
                    read = partial(opened.read, wanted)
                    if not (data := _bytes(plan, await waiter.bounded(read, idle=False), wanted)):
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

    async def _buffer(self, index: int, start: int, size: int) -> memoryview:
        """Read one chunk into its own buffer and check it against the digest the scan recorded."""
        chunk = _Chunk(size)
        await self._read_all(start, size, chunk.add)
        return self._chunked(chunk, index, size)

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
            unconfirmed = buffer[self._confirmed - start :]
            payload = AsyncBodyFactory(_AsyncFactory(unconfirmed), content_length=len(unconfirmed))
            self._sending(end)
            try:
                with self._mapped():
                    await self._core.execute_page(
                        self._plan,
                        self._plan.appended.call,
                        self._append_request(payload, len(unconfirmed)),
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

    async def _abort_remote(self, abort: UploadAbortPlan[K]) -> K:
        """Abort remotely when declared, retaining the aborted checkpoint."""
        self._abort_enter()
        try:
            with self._mapped():
                return await self._core.execute_page(
                    self._plan,
                    abort.targeted.call,
                    partial(self._abort_request, abort),
                    self._aborted,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=self._session,
                    max_page_bytes=None,
                )
        finally:
            self._lock.release()

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


class _WaveCancel(CancelToken):
    """Cancel siblings while retaining the caller's effective cancellation signal."""

    __slots__ = ("_parent",)

    def __init__(self, parent: CancelToken | None) -> None:
        super().__init__()
        self._parent = parent

    @property
    def cancelled(self) -> bool:
        return super().cancelled or (self._parent is not None and self._parent.cancelled)


class _Parts(_Upload[T]):
    """Shared parts layout, progress, receipt checks, and completion request."""

    __slots__ = ("_parallelism", "_receipts", "_uncertain_parts")

    _receipts: dict[int, str]
    _uncertain_parts: set[int]
    _parallelism: int

    def _parts_init(self, capacity: int) -> None:
        self._receipts = {}
        self._uncertain_parts = set()
        self._parallelism = min(self._limits.parallelism, capacity)

    def _parts(self) -> UploadPartsPlan:
        parts = self._plan.parts
        assert parts is not None
        return parts

    def _progress(self) -> UploadProgress:
        return UploadProgress(
            confirmed_bytes=self._confirmed,
            total_bytes=self._identity.size,
            confirmed_parts=tuple(
                PartReceipt(index=index, receipt=receipt) for index, receipt in sorted(self._receipts.items())
            ),
            complete=self._phase is _Phase.COMPLETE,
        )

    def _checkpoint_extra(self, state: WireValue) -> None:
        assert isinstance(state, dict)
        state["parts"] = tuple(sorted(self._receipts.items()))
        state["uncertain"] = tuple(sorted(self._uncertain_parts))

    def _digest_text(self, index: int) -> str:
        digest = self._digests[(index - 1) * _DIGEST : index * _DIGEST]
        if self._parts().digest_encoding == "hex":
            return digest.hex()
        from base64 import b64encode  # ruff: ignore[import-outside-top-level]

        return b64encode(digest).decode("ascii")

    def _listed(
        self, _data: object, wire: WireValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> None:
        """Check indexed digests and receipts before confirming the remote parts."""
        parts = self._parts()
        values = cast("tuple[WireValue, ...]", self._read(parts.items, wire, info))
        found: dict[int, str] = {}
        count = len(self._digests) // _DIGEST
        for value in values:
            index = self._read(parts.index, value, info)
            receipt = self._read(parts.receipt, value, info)
            if type(index) is not int or not 0 < index <= count or index in found:
                raise self._data_error(info, "value", parts.index)
            if not isinstance(receipt, str):
                raise self._data_error(info, "type", parts.receipt)
            if parts.digest is not None and self._read(parts.digest, value, info) != self._digest_text(index):
                raise self._data_error(info, "value", parts.digest)
            if index in self._receipts and self._receipts[index] != receipt:
                raise self._data_error(info, "value", parts.receipt)
            found[index] = receipt
        if not self._receipts.keys() <= found.keys():
            raise self._data_error(info, "value", parts.items)
        with self._guard:
            self._receipts = found
            self._confirmed = sum(min(self._chunk, self._identity.size - (index - 1) * self._chunk) for index in found)
            self._uncertain_parts.clear()
            self._verify = False

    def _wave(self) -> tuple[int, ...]:
        count = len(self._digests) // _DIGEST
        indices: list[int] = []
        for index in range(1, count + 1):
            if index not in self._receipts:
                indices.append(index)
                if len(indices) == self._parallelism:
                    break
        with self._guard:
            self._uncertain_parts.update(indices)
            self._verify = bool(indices)
        return tuple(indices)

    def _part_request(self, index: int, payload: object) -> Callable[[], tuple[tuple[object, ...], object, str | None]]:
        parts = self._parts()
        written: tuple[WireValue, ...] = (
            *self._bound[1],
            index,
            *((self._digest_text(index),) if parts.digest_target is not None else ()),
        )
        arguments = self._plan.appended.request(written)[0]
        return lambda: (arguments, payload, self._url)

    def _completion_request(self) -> tuple[tuple[object, ...], object, str | None]:
        parts, completed = self._parts(), self._plan.completed
        assert completed is not None
        values: WireValue = tuple(
            {parts.index_field: index, parts.receipt_field: receipt}
            for index, receipt in sorted(self._receipts.items())
        )
        return (*completed.request((*self._bound[2], values)), self._url)

    def _completion_probe_request(self) -> tuple[tuple[object, ...], object, str | None]:
        probe = self._plan.completion_probe
        assert probe is not None
        return (*probe.targeted.request(self._controls[1]), self._url)

    def _completion_probed(
        self, _data: object, wire: WireValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> None:
        """Retain only a result the declared completion state confirms."""
        probe, completion = self._plan.completion_probe, self._plan.completion
        assert probe is not None
        assert completion is not None
        state = self._read(probe.state, wire, info)
        if not any(canonical_json(state) == canonical_json(value) for value in probe.completed_values):
            return
        content = canonical_json(self._read(probe.result, wire, info))
        data, _, result_info = self._client.saved_page(
            completion, content, probe.result_status, probe.result_media, self._limits.options
        )
        self._completed(data, wire, content, result_info, _url, _managed)


class PartsUploadHandle(_Parts[T], UploadHandle[T]):
    """A sync parts upload using a private executor bounded by source capability."""

    __slots__ = ("_executor", "_wave_cancel")

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._parts_init(self._source.max_parallel_ranges)
        self._executor: ThreadPoolExecutor | None = None
        self._wave_cancel: _WaveCancel | None = None

    def _options(self) -> RequestOptions | None:
        if self._wave_cancel is None:
            return self._limits.options
        return replace(self._limits.options or RequestOptions(), cancel_token=self._wave_cancel)

    def _waiter(self) -> LogicalCallContext:
        return self._core.waiting(self._options(), self._session, self._plan.append.operation_id)

    def _list_parts(self) -> None:
        with self._mapped():
            self._core.execute_page(
                self._plan,
                self._plan.probed.call,
                self._probe_request,
                self._listed,
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )

    def _reconcile_parts(self, error: BaseException | None = None, *, acknowledged: bool = False) -> None:
        failures: list[BaseException] = []
        for _ in range(max(int(acknowledged), self._limits.max_uncertain_probes)):
            try:
                self._list_parts()
            except Exception as failure:
                if not _probed_again(failure):
                    raise
                failures.append(failure)
                continue
            return
        raise self._unknown(phase="part", delivery=DeliveryState.MAYBE_SENT, error=error, failures=tuple(failures))

    def _part_buffer(self, index: int) -> memoryview:
        start = (index - 1) * self._chunk
        return self._buffer(index - 1, start, min(self._chunk, self._identity.size - start))

    def _part(self, index: int, buffer: memoryview) -> None:
        waiter = self._waiter()
        try:
            waiter.check()
            payload = BodyFactory(_Factory(buffer), content_length=len(buffer))
            with self._mapped():
                self._core.execute_page(
                    self._plan,
                    self._plan.appended.call,
                    self._part_request(index, payload),
                    _ignored,
                    body=payload,
                    media_type=None,
                    options=self._options(),
                    session=self._session,
                    max_page_bytes=None,
                )
        finally:
            waiter.finish()

    def _upload_wave(self) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed, wait  # ruff: ignore[import-outside-top-level]

        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._parallelism, thread_name_prefix="dcg-upload")
        waiter = super()._waiter()
        self._wave_cancel = _WaveCancel(waiter.settings.cancel_token)
        waiter.finish()
        indices = self._wave()
        readers = [self._executor.submit(self._part_buffer, index) for index in indices]
        futures: list[Future[Any]] = list(readers)
        failure: BaseException | None = None
        try:
            for reader in as_completed(readers):
                reader.result()
            sends = [
                self._executor.submit(self._part, index, reader.result())
                for index, reader in zip(indices, readers, strict=True)
            ]
            futures.extend(sends)
            for sent in as_completed(sends):
                sent.result()
        except BaseException as error:
            failure = error
            self._wave_cancel.cancel()
            for pending in futures:
                pending.cancel()
            raise
        finally:
            wait(futures)
            self._wave_cancel = None
            if failure is not None:
                self._verify = True

    def _step(self) -> None:
        if self._verify:
            self._reconcile_parts()
        if self._confirmed < self._identity.size:
            try:
                self._upload_wave()
            except SDKError as error:
                if getattr(error, "delivery_state", None) in {DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED}:
                    self._reconcile_parts(error)
                    return
                raise
            self._reconcile_parts(acknowledged=True)
        else:
            self._complete()

    def _recover_completion(self) -> None:
        probe = self._plan.completion_probe
        if probe is not None and self._phase is _Phase.UNKNOWN:
            with self._mapped():
                self._core.execute_page(
                    self._plan,
                    probe.targeted.call,
                    self._completion_probe_request,
                    self._completion_probed,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=self._session,
                    max_page_bytes=None,
                )
        self._settled()

    def close(self) -> None:
        super().close()
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        super().__exit__(exc_type, exc, traceback)
        if self._closed and self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None


class AsyncPartsUploadHandle(_Parts[T], AsyncUploadHandle[T]):
    """An asyncio parts upload with bounded native tasks and observed exceptions."""

    __slots__ = ()

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._parts_init(self._source.max_parallel_ranges)

    async def _list_parts(self) -> None:
        with self._mapped():
            await self._core.execute_page(
                self._plan,
                self._plan.probed.call,
                self._probe_request,
                self._listed,
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )

    async def _reconcile_parts(self, error: BaseException | None = None, *, acknowledged: bool = False) -> None:
        failures: list[BaseException] = []
        for _ in range(max(int(acknowledged), self._limits.max_uncertain_probes)):
            try:
                await self._list_parts()
            except Exception as failure:
                if not _probed_again(failure):
                    raise
                failures.append(failure)
                continue
            return
        raise self._unknown(phase="part", delivery=DeliveryState.MAYBE_SENT, error=error, failures=tuple(failures))

    async def _part_buffer(self, index: int) -> memoryview:
        start = (index - 1) * self._chunk
        return await self._buffer(index - 1, start, min(self._chunk, self._identity.size - start))

    async def _part(self, index: int, buffer: memoryview) -> None:
        payload = AsyncBodyFactory(_AsyncFactory(buffer), content_length=len(buffer))
        with self._mapped():
            await self._core.execute_page(
                self._plan,
                self._plan.appended.call,
                self._part_request(index, payload),
                _ignored,
                body=payload,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )

    async def _upload_wave(self) -> None:
        import asyncio  # ruff: ignore[import-outside-top-level]

        indices = self._wave()
        readers = [asyncio.create_task(self._part_buffer(index)) for index in indices]
        tasks: list[asyncio.Task[Any]] = list(readers)
        for task in tasks:
            task.add_done_callback(_observed)
        try:
            buffers = await asyncio.gather(*readers)
            sends = [
                asyncio.create_task(self._part(index, buffer)) for index, buffer in zip(indices, buffers, strict=True)
            ]
            for task in sends:
                task.add_done_callback(_observed)
            tasks.extend(sends)
            await asyncio.gather(*sends)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _step(self) -> None:
        if self._verify:
            await self._reconcile_parts()
        if self._confirmed < self._identity.size:
            try:
                await self._upload_wave()
            except SDKError as error:
                if getattr(error, "delivery_state", None) in {DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED}:
                    await self._reconcile_parts(error)
                    return
                raise
            await self._reconcile_parts(acknowledged=True)
        else:
            await self._complete()

    async def _recover_completion(self) -> None:
        probe = self._plan.completion_probe
        if probe is not None and self._phase is _Phase.UNKNOWN:
            with self._mapped():
                await self._core.execute_page(
                    self._plan,
                    probe.targeted.call,
                    self._completion_probe_request,
                    self._completion_probed,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=self._session,
                    max_page_bytes=None,
                )
        self._settled()


def _observed(task: asyncio.Task[Any]) -> None:
    """Retrieve failures even when the wave's caller was cancelled."""
    if not task.cancelled():
        task.exception()


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


@overload
def start_upload(  # ruff: ignore[overload-with-docstring]
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
    """Return the profile's base upload handle."""


@overload
def start_upload(  # ruff: ignore[overload-with-docstring]
    core: ClientCore,
    plan: UploadPlan[T, C],
    source: object,
    arguments: tuple[object, ...],
    *,
    handle: type[H],
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> H:
    """Return the generated concrete upload handle."""


def start_upload(  # noqa: PLR0913
    core: ClientCore,
    plan: UploadPlan[T, C],
    source: object,
    arguments: tuple[object, ...],
    *,
    handle: type[UploadHandle[Any]] | None = None,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> UploadHandle[Any]:
    """Scan the source, create the helper's upload in a session of its own, and return the handle that appends to it."""
    limits = _limits(core, plan, upload_options, options, session_options)
    kind: type[UploadHandle[Any]] = handle or (PartsUploadHandle[Any] if plan.parts is not None else UploadHandle[Any])
    identity = _identity(plan, source)
    chunk = _layout(plan, limits, identity.size, min(limits.chunk_bytes, plan.max_chunk_bytes))
    created = kind(core, plan, limits, _session(limits), cast("UploadSource", source), identity, chunk, b"")
    created._digests = created._scan()  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    created._create(_sized(core, plan, arguments, identity.size), body, media_type)  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    return created


@overload
async def astart_upload(  # ruff: ignore[overload-with-docstring]
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
    """Return the profile's base upload handle."""


@overload
async def astart_upload(  # ruff: ignore[overload-with-docstring]
    core: AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    arguments: tuple[object, ...],
    *,
    handle: type[A],
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> A:
    """Return the generated concrete upload handle."""


async def astart_upload(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    arguments: tuple[object, ...],
    *,
    handle: type[AsyncUploadHandle[Any]] | None = None,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncUploadHandle[Any]:
    """Scan the source, create the helper's upload with asyncio in a session of its own, and return its handle."""
    limits = _limits(core, plan, upload_options, options, session_options)
    kind: type[AsyncUploadHandle[Any]] = handle or (
        AsyncPartsUploadHandle[Any] if plan.parts is not None else AsyncUploadHandle[Any]
    )
    identity = _identity(plan, source)
    chunk = _layout(plan, limits, identity.size, min(limits.chunk_bytes, plan.max_chunk_bytes))
    created = kind(core, plan, limits, _session(limits), cast("AsyncUploadSource", source), identity, chunk, b"")
    created._digests = await created._scan()  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    await created._create(_sized(core, plan, arguments, identity.size), body, media_type)  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    return created


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
    controls: tuple[tuple[WireValue, ...], ...] = ((), ())
    url: str | None = None
    parts: tuple[tuple[int, str], ...] = ()
    uncertain: tuple[int, ...] = ()


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


def _decoded(plan: UploadPlan[Any, Any], state: WireValue, payload: bytes, expires_at: datetime | None) -> _Saved:  # noqa: PLR0914
    """Return a checkpoint's state, refusing one whose form does not fit the helper."""
    from collections.abc import Mapping  # noqa: PLC0415

    extra = set[str]()
    if plan.abort is not None or plan.completion_probe is not None:
        extra.add("controls")
    if plan.session_url is not None:
        extra.add("url")
    if plan.parts is not None:
        extra.update(("parts", "uncertain"))
    require_state(isinstance(state, Mapping) and frozenset(state) == _STATE | extra)
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
        and (resolved in {_Phase.UPLOADING, _Phase.ABORTED} or confirmed == size)
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
        result_fields = state_array(saved_result)
        require_state(complete_by_operation and len(result_fields) == _RESULT_FIELDS)
        result = payload[count:], state_count(result_fields[0]), state_text(result_fields[1])
    bound = cast(
        "tuple[tuple[WireValue, ...], tuple[WireValue, ...], tuple[WireValue, ...]]", _bound(plan, fields["bound"])
    )
    controls: tuple[tuple[WireValue, ...], ...] = ((), ())
    if "controls" in extra:
        groups = tuple(map(state_array, state_array(fields["controls"])))
        children = (plan.abort, plan.completion_probe)
        require_state(
            len(groups) == len(children)
            and all(
                len(group) == (0 if item is None else len(item.bindings))
                for group, item in zip(groups, children, strict=True)
            )
        )
        controls = tuple(
            tuple(
                value if binding.selector is not None else binding.literal
                for value, binding in zip(group, () if item is None else item.bindings, strict=True)
            )
            for group, item in zip(groups, children, strict=True)
        )
    url = None if "url" not in extra else state_text(fields["url"])
    require_state("url" not in extra or isinstance(url, str))
    parts: list[tuple[int, str]] = []
    uncertain: tuple[int, ...] = ()
    if plan.parts is not None:
        for value in state_array(fields["parts"]):
            pair = state_array(value)
            require_state(len(pair) == _RESULT_FIELDS)
            index, receipt = state_count(pair[0]), state_text(pair[1])
            require_state(0 < index <= count // _DIGEST and isinstance(receipt, str))
            parts.append((index, cast("str", receipt)))
        require_state([index for index, _ in parts] == sorted({index for index, _ in parts}))
        require_state(confirmed == sum(min(chunk, size - (index - 1) * chunk) for index, _ in parts))
        uncertain = tuple(state_count(value) for value in state_array(fields["uncertain"]))
        require_state(
            list(uncertain) == sorted(set(uncertain)) and all(0 < index <= count // _DIGEST for index in uncertain)
        )
    return _Saved(
        controls=controls,
        url=url,
        parts=tuple(parts),
        uncertain=uncertain,
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
    if expires_at is not None and expires_at.timestamp() <= limits.clock.time():
        raise UploadExpiredError(expires_at=expires_at, helper_id=plan.helper_id, operation=plan.operation)
    try:
        return _decoded(plan, decode_json(state_json), payload, expires_at)
    except MalformedStateError:
        raise _resume_error(plan, "malformed") from None


def _checked(
    core: ClientCore | AsyncClientCore, plan: UploadPlan[Any, Any], options: RequestOptions | None, saved: _Saved
) -> None:
    """Prepare each request the saved values write as its call would, refusing values that cannot be sent.

    Read values that make a path segment a dot segment raise ProtocolDataError, as if a server gave them; any other
    refusal of a saved value makes the checkpoint malformed.
    """
    from ..client.errors import RequestEncodingError  # noqa: PLC0415 - Only a resume checks saved values.

    probe, append, completion = saved.bound
    offsets: tuple[WireValue, ...] = (0,) if plan.length is None else (0, 0)
    if plan.parts is not None:
        offsets = (1, *(("0" * 64,) if plan.parts.digest_target is not None else ()))
    requests: list[tuple[Targeted[Any], tuple[WireValue, ...], tuple[WireValue, ...], object]] = [
        (plan.probed, probe, (), None),
        (plan.appended, append, offsets, b""),
    ]
    if (completed := plan.completed) is not None:
        requests.append((completed, completion, () if plan.parts is None else ((),), None))
    requests.extend(
        (item.targeted, group, (), None)
        for item, group in zip((plan.abort, plan.completion_probe), saved.controls, strict=True)
        if item is not None
    )
    for targeted, values, extra, payload in requests:
        _dotted(plan, targeted, values, None)
        arguments, body = targeted.request((*values, *extra))
        try:
            core.checked_page(targeted.call, _fixed(arguments, body if payload is None else payload), None, options)
        except (RequestEncodingError, ProtocolDataError, CodecError):
            raise _resume_error(plan, "malformed") from None


def _fixed(arguments: tuple[object, ...], body: object) -> Callable[[], tuple[tuple[object, ...], object, str | None]]:
    """Return the request of a check, built once."""
    return lambda: (arguments, body, None)


def _resumed(handle: _Upload[T], saved: _Saved, core: ClientCore | AsyncClientCore) -> None:
    """Restore a handle to a checkpoint's phase, offset, values, expiry, and any completion's result."""
    plan = handle._plan  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    handle._url = saved.url  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    handle._controls = saved.controls  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    if saved.url is not None:
        handle._follow(saved.url, saved.url, None)  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    if saved.phase is _Phase.ABORTED:
        error = handle._state_error("resume", "aborted")  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
        raise error
    if isinstance(handle, (PartsUploadHandle, AsyncPartsUploadHandle)):
        handle._receipts = dict(saved.parts)  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
        handle._uncertain_parts = set(saved.uncertain)  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
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
    """Check the checkpoint, its layout and saved values, then the source, before any read or send.

    The source must be the checkpoint's content.
    """
    saved = _restored(core, plan, state, limits)
    _layout(plan, limits, saved.identity.size, saved.chunk)
    _checked(core, plan, limits.options, saved)
    identity = _identity(plan, source)
    if identity != saved.identity:
        raise _changed(plan, saved.identity, identity)
    return saved, identity


@overload
def resume_upload(  # ruff: ignore[overload-with-docstring]
    core: ClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    *,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> UploadHandle[T]:
    """Return the profile's base upload handle."""


@overload
def resume_upload(  # ruff: ignore[overload-with-docstring]
    core: ClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    *,
    handle: type[H],
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> H:
    """Return the generated concrete upload handle."""


def resume_upload(  # noqa: PLR0913
    core: ClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    *,
    handle: type[UploadHandle[Any]] | None = None,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> UploadHandle[Any]:
    """Continue a checkpoint in a new session: check the source against it, then probe the server's offset once.

    A complete upload resumes with its result and an unknown completion raises again, both without reading or sending.
    """
    limits = _limits(core, plan, upload_options, options, session_options)
    kind: type[UploadHandle[Any]] = handle or (PartsUploadHandle[Any] if plan.parts is not None else UploadHandle[Any])
    saved, identity = _resume_start(core, plan, source, state, limits)
    created = kind(
        core, plan, limits, _session(limits), cast("UploadSource", source), identity, saved.chunk, saved.digests
    )
    _resumed(created, saved, core)
    if created._phase is not _Phase.UPLOADING:  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
        created._recover_completion() if isinstance(created, PartsUploadHandle) else created._settled()  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
        return created
    created._scan(saved.digests)  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    if isinstance(created, PartsUploadHandle):
        created._list_parts()  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    else:
        created._verified(*created._probe())  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    return created


@overload
async def aresume_upload(  # ruff: ignore[overload-with-docstring]
    core: AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    *,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncUploadHandle[T]:
    """Return the profile's base upload handle."""


@overload
async def aresume_upload(  # ruff: ignore[overload-with-docstring]
    core: AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    *,
    handle: type[A],
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> A:
    """Return the generated concrete upload handle."""


async def aresume_upload(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: UploadPlan[T, C],
    source: object,
    state: object,
    *,
    handle: type[AsyncUploadHandle[Any]] | None = None,
    upload_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncUploadHandle[Any]:
    """Continue a checkpoint with asyncio in a new session: check the source against it, then probe once.

    A complete upload resumes with its result and an unknown completion raises again, both without reading or sending.
    """
    limits = _limits(core, plan, upload_options, options, session_options)
    kind: type[AsyncUploadHandle[Any]] = handle or (
        AsyncPartsUploadHandle[Any] if plan.parts is not None else AsyncUploadHandle[Any]
    )
    saved, identity = _resume_start(core, plan, source, state, limits)
    created = kind(
        core, plan, limits, _session(limits), cast("AsyncUploadSource", source), identity, saved.chunk, saved.digests
    )
    _resumed(created, saved, core)
    if created._phase is not _Phase.UPLOADING:  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
        await created._recover_completion() if isinstance(created, AsyncPartsUploadHandle) else created._settled()  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
        return created
    await created._scan(saved.digests)  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    if isinstance(created, AsyncPartsUploadHandle):
        await created._list_parts()  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    else:
        created._verified(*await created._probe())  # pyright: ignore[reportPrivateUsage]  # ruff: ignore[private-member-access]
    return created
