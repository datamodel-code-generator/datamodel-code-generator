"""Long-running operations: their creation, the handles that poll them, and the session their child calls share.

A helper's `start` creates the operation in one child call and returns a handle. The handle polls only when `status` or
`wait` asks, first waiting out the interval and any server delay the helper declares, and fetches a result at most once.
Its `checkpoint` saves what continuing takes, which the helper's `resume` continues in a session of its own without
creating the operation again.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from math import ceil
from time import monotonic, time
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, TypeAlias, cast, final, overload

from typing_extensions import Self, TypeVar

from ..client.errors import (
    BudgetExceededError,
    ProtocolConfigurationError,
    ProtocolSizeError,
    RequestEncodingError,
    SDKError,
)
from ..client.options import RequestOptions
from ..client.responses import ResponseInfo
from ..client.timing import SessionOptions
from ..model_codecs.errors import CodecError
from ..model_codecs.media import decode_json
from ..model_codecs.unset import UNSET
from .errors import (
    OperationCancelledError,
    OperationFailedError,
    PollingStateError,
    PollWaitLimitError,
    ProtocolDataError,
    ProtocolStateError,
    ResumeStateError,
    SessionLimitError,
)
from .options import PollOptions, layered
from .records import (
    BodySelector,
    CancelReceipt,
    PollSnapshot,
    StatusSelector,
    canonical_json,
)
from .resume import (
    MalformedStateError,
    ResumeState,
    helper_state,
    require_state,
    server_expiry,
    state_array,
    state_count,
    state_fields,
    state_text,
)
from .values import MISSING, Missing, RepeatedValueError, resolve, selected, written

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import OperationPlan
    from ..client.timing import Deadline
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .errors import _DataCondition  # pyright: ignore[reportPrivateUsage]
    from .pagination import PageBinding
    from .records import ProtocolProgress, Selector
    from .references import OperationRef
    from .writes import ReadPaths

__all__ = (
    "AsyncLroHandle",
    "CancelPlan",
    "LroHandle",
    "PollingPlan",
    "aresume_operation",
    "astart_operation",
    "resume_operation",
    "start_operation",
)

T = TypeVar("T")
P = TypeVar("P")
C = TypeVar("C")
K = TypeVar("K")
V = TypeVar("V")
H = TypeVar("H", bound="LroHandle[Any, Any]")
AH = TypeVar("AH", bound="AsyncLroHandle[Any, Any]")
OperationT = TypeVar("OperationT", bound="_Operation[Any, Any]")

_Saved: TypeAlias = tuple[bytes, int, str | None]


class _Phase(Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_PHASES: Final[Mapping[str, _Phase]] = MappingProxyType({phase.value: phase for phase in _Phase})
_STATE: Final = frozenset({"phase", "polls", "wait_ms", "bound", "seed", "cancel", "poll", "result"})
_POLL_FIELDS: Final = 4
_UNREAD: Final = "unread"
_RESULT_FIELDS: Final = 3
_MOST_WAIT_MS: Final = 2**53 - 1
_KINDS: Final[Mapping[type, str]] = MappingProxyType({
    type(None): "null",
    bool: "boolean",
    str: "string",
    tuple: "array",
})


def _kind(value: WireValue) -> str:
    """Return the JSON type of a wire value, every number being one type."""
    return _KINDS.get(type(value)) or ("object" if isinstance(value, Mapping) else "number")


@dataclass(frozen=True, slots=True)
class _Targeted(Generic[T]):
    """An operation that sends only the values a helper writes into its targets, and where each value goes.

    A write is a parameter's argument position without a pointer, a querystring's position with a pointer into its
    value, or no position with a pointer into the JSON body. `headers` and `queries` name the header and query
    parameters it writes, which a call's options must not patch, and `dotted` the path segments a read value is
    written to, which must not encode to a dot segment.
    """

    call: OperationPlan[T, object]
    writes: tuple[tuple[int | None, str | None], ...]
    headers: frozenset[str]
    queries: frozenset[str]
    dotted: ReadPaths

    def request(self, values: tuple[WireValue, ...]) -> tuple[tuple[object, ...], object]:
        """Return the arguments and body of a request writing each value in order, every other argument omitted.

        A parameter's value replaces its argument, and the values for a querystring or the body are patched into an
        empty object.
        """
        return written(self.writes, (UNSET,) * len(self.call.parameters), UNSET, values)


def _targeted(call: OperationPlan[T, object], bindings: tuple[PageBinding, ...]) -> _Targeted[T]:
    """Return an operation that takes the values the bindings write to their targets, in order, as wire values."""
    from .writes import read_paths, targeted  # noqa: PLC0415 - Only a plan loads the operation runtime.

    sources = tuple((binding.target, binding.selector) for binding in bindings)
    return _Targeted(*targeted(call, (target for target, _ in sources)), read_paths(call, sources))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CancelPlan(Generic[K]):
    """The remote cancellation a polling helper declares: its operation, and the bindings it writes.

    Its bindings read the create response for `initial` and the latest response, the create response and then each
    pending poll, for `previous`; `targeted` sends only what they write.
    """

    operation: OperationRef
    call: OperationPlan[K, object]
    bindings: tuple[PageBinding, ...] = ()
    targeted: _Targeted[K] = field(init=False)

    def __post_init__(self) -> None:
        """Derive the cancel operation writing the bindings' values."""
        object.__setattr__(self, "targeted", _targeted(self.call, self.bindings))


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PollingPlan(Generic[T, P, C]):
    """Everything fixed about one generated polling helper: its operations, states, result, bindings, and waits.

    `create` starts the operation: a status in `accepted` leaves it pending, and one in `immediate_statuses` already
    carries the result `immediate` reads from the decoded response. `poll` writes each binding's value, and its
    response's `state` must equal a value of `pending`, `succeeded`, `failed`, or `cancelled` by canonical JSON. A
    success reads its result from the final poll with `inline`, fetches it with `fetch` writing `fetch_bindings`, or
    has none. After each response, `interval` seconds pass before the next poll, or the longer delay the
    `retry_after_header` gives. `cancel` is the declared remote cancellation, and `expires_at` reads the server's
    expiry of the operation from an accepted create response. `children` are the operations a resumed handle may send.
    """

    helper_id: str
    operation: OperationRef
    create: OperationPlan[C, object]
    accepted: tuple[int, ...]
    poll_operation: OperationRef
    poll: OperationPlan[P, object]
    state: Selector
    pending: tuple[WireValue, ...]
    succeeded: tuple[WireValue, ...]
    fingerprint: str
    bindings: tuple[PageBinding, ...] = ()
    failed: tuple[WireValue, ...] = ()
    cancelled: tuple[WireValue, ...] = ()
    inline: Callable[[P], T | None] | None = None
    inline_selector: BodySelector | None = None
    fetch_operation: OperationRef | None = None
    fetch: OperationPlan[T, object] | None = None
    fetch_bindings: tuple[PageBinding, ...] = ()
    immediate: Callable[[C], T | None] | None = None
    immediate_statuses: tuple[int, ...] = ()
    immediate_selector: BodySelector | None = None
    interval: float = 1.0
    retry_after_header: str | None = None
    cancel: CancelPlan[Any] | None = None
    expires_at: Selector | None = None
    phases: Mapping[bytes, _Phase] = field(init=False)
    kinds: frozenset[str] = field(init=False)
    polled: _Targeted[P] = field(init=False)
    fetched: _Targeted[T] | None = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)
    children: tuple[OperationPlan[Any, object], ...] = field(init=False)

    def __post_init__(self) -> None:
        """Index the states by canonical JSON, and derive the poll, fetch, and cancel operations writing values."""
        phases = {
            canonical_json(value): phase
            for values, phase in (
                (self.pending, _Phase.PENDING),
                (self.succeeded, _Phase.SUCCEEDED),
                (self.failed, _Phase.FAILED),
                (self.cancelled, _Phase.CANCELLED),
            )
            for value in values
        }
        polled = _targeted(self.poll, self.bindings)
        fetched = None if self.fetch is None else _targeted(self.fetch, self.fetch_bindings)
        others = (*(() if fetched is None else (fetched,)), *(() if self.cancel is None else (self.cancel.targeted,)))
        object.__setattr__(self, "phases", MappingProxyType(phases))
        object.__setattr__(
            self,
            "kinds",
            frozenset(map(_kind, (*self.pending, *self.succeeded, *self.failed, *self.cancelled))),
        )
        object.__setattr__(self, "polled", polled)
        object.__setattr__(self, "fetched", fetched)
        object.__setattr__(self, "headers", polled.headers.union(*(item.headers for item in others)))
        object.__setattr__(self, "queries", polled.queries.union(*(item.queries for item in others)))
        object.__setattr__(self, "children", (polled.call, *(item.call for item in others)))


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits:
    """The effective limits of one helper call, each from the first layer that sets it; None removes a limit."""

    interval: float
    max_polls: int | None = 1000
    max_wait: float | None = 60.0
    total_timeout: float | None = 600.0
    deadline: Deadline | None = None
    max_network_sends: int | None = 2000
    options: RequestOptions | None = None


_DEFAULTS: Final = _Limits(interval=1.0)


def _invalid(plan: PollingPlan[T, P, C], path: tuple[str, ...]) -> ProtocolConfigurationError:
    return ProtocolConfigurationError(
        field_path=path, condition="invalid_value", helper_id=plan.helper_id, operation=plan.operation
    )


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: PollingPlan[T, P, C],
    poll_options: object,
    options: object,
    session_options: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    The interval defaults to the helper's; one longer than the allowed wait, or not shorter than the session, could
    never be waited out, so it is refused before anything is sent. Effective options fixing an idempotency key are
    refused, since the create call and each poll need keys of their own, and so are header or query patches of a
    parameter the helper writes.
    """
    for name, value, kind in (
        ("poll_options", poll_options, PollOptions),
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
    kinds = (poll_options, UNSET if defaults is None else defaults.options)
    sessions = (session_options, UNSET if defaults is None else defaults.session)
    limits = _Limits(
        interval=layered(kinds, "interval", plan.interval),
        max_polls=layered(kinds, "max_polls", _DEFAULTS.max_polls),
        max_wait=layered(kinds, "max_wait", _DEFAULTS.max_wait),
        total_timeout=layered(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=layered(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=layered(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
    )
    interval, deadline = limits.interval, limits.deadline
    session = (limits.total_timeout, None if deadline is None else deadline.remaining())
    if ((allowed := limits.max_wait) is not None and interval > allowed) or any(
        bound is not None and interval >= bound for bound in session
    ):
        raise _invalid(plan, ("poll_options", "interval"))
    return limits


def _security(
    core: ClientCore | AsyncClientCore, plan: PollingPlan[T, P, C], options: RequestOptions | None
) -> tuple[str, bool]:
    """Return the digest of the security a checkpoint is bound to, and whether it may leave the process.

    It covers every operation a resumed handle may send, but never the create operation, which it does not send.
    """
    checked = [core.checkpoint_security(call, options) for call in plan.children]
    facts = tuple(item for item, _ in checked)
    return sha256(canonical_json(facts)).hexdigest(), all(exportable for _, exportable in checked)


def _absence(value: WireValue | Missing) -> Literal["missing", "null"]:
    return "missing" if value is MISSING else "null"


def _literals(bindings: tuple[PageBinding, ...], values: tuple[WireValue, ...]) -> tuple[WireValue, ...]:
    """Return saved binding values with each literal binding's value the plan's, whatever was saved."""
    return tuple(
        value if binding.selector is not None else binding.literal
        for binding, value in zip(bindings, values, strict=True)
    )


def _wait_ms(not_before: float) -> int:
    """Return the milliseconds left before a time, rounded up, and at most 2**53 - 1, which any longer wait saves."""
    return min(ceil(min(max(0.0, not_before - monotonic()), _MOST_WAIT_MS) * 1000), _MOST_WAIT_MS)


def _sent(
    targeted: _Targeted[Any], values: tuple[WireValue, ...]
) -> Callable[[], tuple[tuple[object, ...], object, None]]:
    """Return the request a targeted operation sends writing the values, whatever the handle holds later."""
    return lambda: (*targeted.request(values), None)


@dataclass(frozen=True, slots=True)
class _Step(Generic[T, P]):
    """What one child call settled: the phase, when the next poll may be sent, the poll, and what comes next.

    `bound` holds the values the next poll writes while pending, and those the result fetch writes after a success;
    `seed` holds what the create response gives the fetch's `initial` bindings, and `cancel` what a remote cancel
    writes while pending. `body` is a terminal poll's body and `kept` an immediate create response's, which
    checkpoints save, and `expires_at` the server's expiry an accepted create response gives.
    """

    phase: _Phase
    not_before: float
    snapshot: PollSnapshot[P] | None = None
    bound: tuple[WireValue, ...] = ()
    result: T | Missing = MISSING
    seed: tuple[WireValue, ...] | None = None
    cancel: tuple[WireValue, ...] = ()
    body: _Saved | None = None
    kept: _Saved | None = None
    expires_at: datetime | None = None


class _Operation(Generic[T, P]):
    """What the synchronous and asyncio handles share: the plan, limits, session, phase, and the last poll.

    Only a settled child call changes the phase, so a failure that settles none, such as a transport error, a
    deadline, a cancellation, or a limit, leaves the handle as it was, and a later step polls again. A settled handle
    keeps the bodies its checkpoint saves: the terminal poll's and that of a result read from another response.

    `_lock` is held by the one step that polls or fetches, across its waits and sends; `_guard` only while what a
    step settles is written or read together, so a checkpoint or a remote cancel never waits for a step.
    """

    __slots__ = (
        "_bound",
        "_cancel",
        "_client",
        "_closed",
        "_expires_at",
        "_guard",
        "_limits",
        "_lock",
        "_not_before",
        "_phase",
        "_plan",
        "_poll_body",
        "_polls",
        "_result",
        "_result_body",
        "_seed",
        "_session",
        "_snapshot",
    )

    def __init__(
        self,
        client: ClientCore | AsyncClientCore,
        plan: PollingPlan[T, P, Any],
        limits: _Limits,
        session: OperationSession,
    ) -> None:
        """Keep the plan, limits, and session; the create call or a checkpoint settles the handle's first phase."""
        self._client = client
        self._plan = plan
        self._limits = limits
        self._session = session
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self._phase = _Phase.PENDING
        self._closed = False
        self._snapshot: PollSnapshot[P] | None = None
        self._bound: tuple[WireValue, ...] = ()
        self._seed: tuple[WireValue, ...] = ()
        self._cancel: tuple[WireValue, ...] = ()
        self._result: T | Missing = MISSING
        self._poll_body: _Saved | None = None
        self._result_body: _Saved | None = None
        self._expires_at: datetime | None = None
        self._not_before = 0.0
        self._polls = 0

    def __repr__(self) -> str:
        """Name the handle's phase and its poll count only, never its data."""
        phase = "closed" if self._closed else self._phase.value
        return f"{type(self).__name__}(phase={phase!r}, polls={self._polls})"

    @property
    def progress(self) -> ProtocolProgress:
        """Return the polls sent so far and the session's sends."""
        session = self._session
        return MappingProxyType({
            "polls": self._polls,
            "network_send_count": session.network_send_count,
            "network_send_budget_used": session.network_send_budget_used,
        })

    def checkpoint(self) -> ResumeState:
        """Return a checkpoint the helper's `resume` continues from, sending nothing.

        It saves the phase, the polls so far, the wait left before the next poll or result fetch, the values the
        next requests write, and the bodies a settled handle decodes again, but neither the session nor the call's
        options. A closed handle is checkpointed as it stood, and a handle another thread or task is polling as its
        last settled step left it.
        """
        return self._saved()

    def _saved(self) -> ResumeState:
        """Return a checkpoint of the handle as it stands, bound to the helper and the security its calls run under."""
        plan = self._plan
        with self._guard:
            state, payload, expires_at = self._state()
        security, exportable = _security(self._client, plan, self._limits.options)
        return helper_state(
            helper_fingerprint=plan.fingerprint,
            security_fingerprint=security,
            state=state,
            payload=payload,
            exportable=exportable,
            expires_at=expires_at,
        )

    def _state(self) -> tuple[WireValue, bytes, datetime | None]:
        """Return the protocol state and payload of a checkpoint, with its expiry; the guard is held."""
        phase = self._phase
        payload = b""
        poll: WireValue = None
        result: WireValue = None
        if (body := self._poll_body) is not None:
            content, status, content_type = body
            snapshot = self._snapshot
            assert snapshot is not None
            poll, payload = (snapshot.state, status, content_type, len(content)), content
        if (kept := self._result_body) is not None:
            content, status, content_type = kept
            result, payload = (status, content_type, len(content)), payload + content
        state: WireValue = {
            "phase": phase.value,
            "polls": self._polls,
            "wait_ms": _wait_ms(self._not_before),
            "bound": self._bound,
            "seed": self._seed if phase is _Phase.PENDING else (),
            "cancel": self._cancel,
            "poll": poll,
            "result": result,
        }
        return state, payload, self._expires_at

    def _state_error(self, action: str, state: str) -> ProtocolStateError:
        plan = self._plan
        return ProtocolStateError(
            state=state,
            action=action,
            helper_id=plan.helper_id,
            operation=plan.operation,
            parent_session_id=self._session.session_id,
        )

    def _limit(
        self,
        limit: int,
        kind: Literal["polls", "network_sends"],
        refused: BudgetExceededError | None = None,
        *,
        created: bool = True,
    ) -> SessionLimitError:
        """Return the error of a session limit reached while the operation is unsettled, with a refused child call.

        It keeps a checkpoint of the handle once the operation was created.
        """
        plan = self._plan
        saved = self._saved() if created else None
        if refused is None:
            return SessionLimitError(
                kind=kind,
                limit=limit,
                progress=self.progress,
                resume_state=saved,
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self._session.session_id,
            )
        return SessionLimitError(
            kind=kind,
            limit=limit,
            progress=self.progress,
            resume_state=saved,
            helper_id=plan.helper_id,
            operation=plan.operation,
            operation_id=refused.operation_id,
            call_id=refused.call_id,
            parent_session_id=refused.parent_session_id,
            info=refused.info,
            cause=refused,
            resource_attempt_count=refused.resource_attempt_count,
            redirect_count=refused.redirect_count,
            auth_exchange_count=refused.auth_exchange_count,
            network_send_count=refused.network_send_count,
            network_send_budget_used=refused.network_send_budget_used,
            auth_exchange_budget_used=refused.auth_exchange_budget_used,
            auth_refresh_ids=refused.auth_refresh_ids,
            auth_refresh_pending=refused.auth_refresh_pending,
            wire_send_count=refused.wire_send_count,
        )

    @contextmanager
    def _mapped(self, *, created: bool = True) -> Generator[None, None, None]:
        """Raise a child call's refusal for want of a session send slot as the session's limit error."""
        try:
            yield
        except BudgetExceededError as error:
            if error.budget_kind != "parent_network":
                raise
            raise self._limit(error.limit, "network_sends", error, created=created) from None

    def _enter(self, action: str) -> None:
        """Take the handle for one step, refusing a concurrent step and a closed handle."""
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, "polling")
        if self._closed:
            self._lock.release()
            raise self._state_error(action, "closed")

    def _close(self, action: str, *, quiet: bool = False) -> None:
        """Close the handle, refusing while a step runs, or leaving it open then when `quiet`."""
        if not self._lock.acquire(blocking=False):
            if quiet:
                return
            raise self._state_error(action, "polling")
        self._closed = True
        self._lock.release()

    def _sendable(self) -> None:
        """Refuse a child call the session has no send slot left for."""
        session = self._session
        if (limit := session.send_limit) is not None and session.network_send_budget_used >= limit:
            raise self._limit(limit, "network_sends")

    def _cancelling(self, cancel: CancelPlan[K]) -> Callable[[], tuple[tuple[object, ...], object, None]]:
        """Return the remote cancel request of a pending operation, without waiting for a step that polls it.

        A closed handle and a settled operation refuse it, and so does a session without a send slot.
        """
        action = "cancel_remote"
        with self._guard:
            if self._closed:
                raise self._state_error(action, "closed")
            if (phase := self._phase) is not _Phase.PENDING:
                raise self._state_error(action, phase.value)
            values = self._cancel
        self._sendable()
        return _sent(cancel.targeted, values)

    def _due(self, core: ClientCore | AsyncClientCore, call: OperationPlan[Any, object]) -> LogicalCallContext | None:
        """Return the context to wait in before the next poll or result fetch, or None when it may be sent now.

        The poll limit, for a poll, and the session's send slots are checked first. A wait longer than the allowed
        wait, or not shorter than what remains of the deadline, raises PollWaitLimitError instead of sending early.
        """
        if call is self._plan.polled.call and (limit := self._limits.max_polls) is not None and self._polls >= limit:
            raise self._limit(limit, "polls")
        self._sendable()
        if (required := self._not_before - monotonic()) <= 0:
            return None
        limits = self._limits
        if (allowed := limits.max_wait) is not None and required > allowed:
            raise self._waited(required, allowed, "wait")
        waiter = core.waiting(limits.options, self._session, call.operation_id)
        if (remaining := waiter.remaining()) is not None and required >= remaining:
            waiter.finish()
            raise self._waited(required, remaining, "deadline")
        return waiter

    def _waited(self, required: float, limit: float, kind: Literal["wait", "deadline"]) -> PollWaitLimitError:
        plan = self._plan
        return PollWaitLimitError(
            kind=kind,
            required_wait=required,
            limit=limit,
            resume_state=self._saved(),
            helper_id=plan.helper_id,
            operation=plan.poll_operation,
            parent_session_id=self._session.session_id,
        )

    def _error(
        self,
        info: ResponseInfo | None,
        condition: _DataCondition,
        location: Selector,
        operation: OperationRef | None,
    ) -> ProtocolDataError:
        return ProtocolDataError(
            condition=condition, location=location, helper_id=self._plan.helper_id, operation=operation, info=info
        )

    def _selected(
        self, read: Selector, wire: WireValue, info: ResponseInfo, operation: OperationRef | None
    ) -> WireValue | Missing:
        """Return what a selector reads from a response, or MISSING, refusing a header selected once it repeats."""
        try:
            return selected(read, wire, info)
        except RepeatedValueError:
            raise self._error(info, "malformed", read, operation) from None

    def _value(
        self, binding: PageBinding, wire: WireValue, info: ResponseInfo, operation: OperationRef | None
    ) -> WireValue:
        """Return what a binding writes: its literal or what the response gives, refusing a missing value."""
        if (selector := binding.selector) is None:
            return binding.literal
        if (value := self._selected(selector, wire, info, operation)) is MISSING:
            raise self._error(info, "missing", selector, operation)
        return value

    def _values(  # noqa: PLR0913, PLR0917
        self,
        targeted: _Targeted[Any],
        bindings: tuple[PageBinding, ...],
        wire: WireValue,
        info: ResponseInfo,
        operation: OperationRef | None,
        kept: tuple[WireValue, ...] = (),
    ) -> tuple[WireValue, ...]:
        """Return the values bindings write: their literals, kept `initial` values, and what the response gives.

        A missing value is refused, and so are read values that make a path segment a dot segment once encoded.
        """
        written = tuple(
            kept[index]
            if kept and binding.selector is not None and binding.source == "initial"
            else self._value(binding, wire, info, operation)
            for index, binding in enumerate(bindings)
        )
        if (read := _dotted(targeted, written)) is not None:
            raise self._error(info, "value", read, operation)
        return written

    def _expiry(self, wire: WireValue, info: ResponseInfo) -> datetime:
        """Return the server's expiry an accepted create response gives at the helper's declared selector.

        It must be a string giving an RFC 3339 date-time with an offset or an HTTP date.
        """
        plan = self._plan
        read = plan.expires_at
        assert read is not None
        value = self._selected(read, wire, info, plan.operation)
        if value is MISSING or value is None:
            raise self._error(info, _absence(value), read, plan.operation)
        if not isinstance(value, str):
            raise self._error(info, "type", read, plan.operation)
        if (expires_at := server_expiry(value)) is None:
            raise self._error(info, "value", read, plan.operation)
        return expires_at

    def _after(self, info: ResponseInfo) -> float:
        """Return when the next poll may be sent after a response: its receipt plus the interval or a longer delay.

        Only the header the helper declares gives a server delay; one that is not a valid delay is ignored.
        """
        received = monotonic()
        delay = self._limits.interval
        if (name := self._plan.retry_after_header) is not None:
            from ..client.retry import header_delay  # noqa: PLC0415 - Only a helper with a delay header reads one.

            if (server := header_delay(info.headers, name, time())) is not None:
                delay = max(delay, server)
        return received + delay

    def _result_of(  # noqa: PLR0913, PLR0917
        self,
        read: Callable[[V], T | None],
        selector: BodySelector | None,
        data: V,
        wire: WireValue,
        info: ResponseInfo,
        operation: OperationRef | None,
    ) -> T:
        """Return the result a response carries, refusing a missing or null one before its accessor reads it."""
        assert selector is not None
        if (found := resolve(wire, selector.pointer)) is MISSING or found is None or (value := read(data)) is None:
            raise self._error(info, _absence(found), selector, operation)
        return value

    def _created(
        self, data: object, wire: WireValue, content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> _Step[T, P]:
        """Settle the create response: an accepted status pends, an immediate one carries the result.

        Any other success status is refused, since it neither starts nor completes the operation as the helper
        declares. The values the first poll, the result fetch, and a remote cancel take from the response are read
        now, with the server's expiry; an immediate response's body is kept for checkpoints.
        """
        plan = self._plan
        status, not_before, operation = info.status_code, self._after(info), plan.operation
        if status in plan.accepted:
            seed = tuple(
                self._value(binding, wire, info, operation) if binding.source == "initial" else None
                for binding in plan.fetch_bindings
            )
            bound = self._values(plan.polled, plan.bindings, wire, info, operation)
            cancel = (
                ()
                if (cancels := plan.cancel) is None
                else self._values(cancels.targeted, cancels.bindings, wire, info, operation)
            )
            expires_at = None if plan.expires_at is None else self._expiry(wire, info)
            return _Step(_Phase.PENDING, not_before, bound=bound, seed=seed, cancel=cancel, expires_at=expires_at)
        if status in plan.immediate_statuses:
            assert plan.immediate is not None
            result = self._result_of(plan.immediate, plan.immediate_selector, data, wire, info, plan.operation)
            kept = (content, status, info.content_type)
            return _Step(_Phase.SUCCEEDED, not_before, result=result, kept=kept)
        raise self._error(info, "value", StatusSelector(), plan.operation)

    def _polled(
        self, data: P, wire: WireValue, content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> _Step[T, P]:
        """Settle one poll by its state; an unknown state raises PollingStateError and success is never inferred.

        A pending poll reads the values of the next poll's and a remote cancel's bindings, and a success its result or
        what its result fetch writes. A terminal poll's body is kept for checkpoints.
        """
        plan = self._plan
        operation, read = plan.poll_operation, plan.state
        if (value := self._selected(read, wire, info, operation)) is MISSING:
            raise self._error(info, "missing", read, operation)
        if (phase := plan.phases.get(canonical_json(value))) is None:
            raise PollingStateError(
                condition="value" if _kind(value) in plan.kinds else "type",
                location=read,
                helper_id=plan.helper_id,
                operation=operation,
                info=info,
            )
        snapshot = PollSnapshot(state=value, terminal=phase is not _Phase.PENDING, data=data, response=info)
        if phase is _Phase.PENDING:
            polled = self._values(plan.polled, plan.bindings, wire, info, operation, self._bound)
            if (cancel := plan.cancel) is None:
                return _Step(phase, self._after(info), snapshot, polled)
            cancels = self._values(cancel.targeted, cancel.bindings, wire, info, operation, self._cancel)
            return _Step(phase, self._after(info), snapshot, polled, cancel=cancels)
        bound: tuple[WireValue, ...] = ()
        result: T | Missing = MISSING
        if phase is _Phase.SUCCEEDED:
            bound, result = self._succeeded(data, wire, info)
        return _Step(phase, monotonic(), snapshot, bound, result, body=(content, info.status_code, info.content_type))

    def _succeeded(self, data: P, wire: WireValue, info: ResponseInfo) -> tuple[tuple[WireValue, ...], T | Missing]:
        """Return what the result fetch writes after a successful poll, or the result the poll carries itself."""
        plan = self._plan
        operation = plan.poll_operation
        if (inline := plan.inline) is not None:
            return (), self._result_of(inline, plan.inline_selector, data, wire, info, operation)
        if (fetched := plan.fetched) is not None:
            return self._values(fetched, plan.fetch_bindings, wire, info, operation, self._seed), MISSING
        return (), cast("T", None)

    def _counted(self, admitted: int) -> None:
        """Count a poll once its call was admitted to send, never one refused before sending."""
        if self._session.network_send_budget_used > admitted:
            self._polls += 1

    def _failed(self, error: Exception) -> None:
        """Wait after a final error response as after any response, so its server delay is never cut short.

        A poll or a result fetch that failed with a response is sent again only after it.
        """
        if isinstance(info := getattr(error, "info", None), ResponseInfo):
            self._not_before = self._after(info)

    def _settle(self, step: _Step[T, P]) -> None:
        with self._guard:
            self._phase, self._not_before = step.phase, step.not_before
            self._snapshot = step.snapshot or self._snapshot
            self._bound, self._result, self._cancel = step.bound, step.result, step.cancel
            self._poll_body, self._result_body = step.body, step.kept
            if step.seed is not None:
                self._seed = step.seed
            if step.expires_at is not None:
                self._expires_at = step.expires_at

    def _fetched(self, result: T, kept: _Saved) -> T:
        """Keep a fetched result and its body; the fetch's values are no longer needed."""
        with self._guard:
            self._result, self._result_body, self._bound = result, kept, ()
        return result

    def _status(self, action: str) -> PollSnapshot[P] | None:
        """Return the saved terminal poll, or None while one more poll is due.

        A handle created with an immediate result has no poll to return.
        """
        if self._phase is _Phase.PENDING:
            return None
        if (snapshot := self._snapshot) is None:
            raise self._state_error(action, _Phase.SUCCEEDED.value)
        return snapshot

    def _outcome(self) -> T | Missing:
        """Return the settled operation's result, MISSING while its fetch is due; a failure or cancellation raises.

        The terminal snapshot stays on the error, which every later `wait` raises again without sending.
        """
        if (phase := self._phase) in {_Phase.FAILED, _Phase.CANCELLED}:
            plan, snapshot = self._plan, self._snapshot
            assert snapshot is not None
            kind = OperationFailedError if phase is _Phase.FAILED else OperationCancelledError
            raise kind(
                snapshot=snapshot,
                helper_id=plan.helper_id,
                operation=plan.poll_operation,
                parent_session_id=self._session.session_id,
                info=snapshot.response,
            )
        return self._result

    def _fetch_request(self) -> tuple[tuple[object, ...], object, None]:
        fetched = self._plan.fetched
        assert fetched is not None
        return (*fetched.request(self._bound), None)

    def _poll_request(self) -> tuple[tuple[object, ...], object, None]:
        return (*self._plan.polled.request(self._bound), None)

    def _decoded(self, operation: OperationPlan[V, object], saved: _Saved) -> tuple[V, WireValue, ResponseInfo]:
        """Return a saved body decoded as its response was, refusing one that does not decode as malformed.

        A body over the resumed call's response size limit raises ProtocolSizeError, as receiving it would.
        """
        options, size = self._limits.options, len(saved[0])
        if (limit := self._client.response_limit(operation, options)) is not None and size > limit:
            plan = self._plan
            raise ProtocolSizeError(
                kind="body",
                limit=limit,
                observed=size,
                unit="bytes",
                helper_id=plan.helper_id,
                operation=plan.operation,
            )
        try:
            return self._client.saved_page(operation, *saved, options)
        except SDKError:
            raise MalformedStateError from None

    def _checked(
        self, call: OperationPlan[Any, object], request: Callable[[], tuple[tuple[object, ...], object, None]]
    ) -> None:
        """Prepare a request the restored handle sends next as its call would, refusing saved values it cannot send."""
        try:
            self._client.checked_page(call, request, None, self._limits.options)
        except (RequestEncodingError, ProtocolDataError, CodecError):
            raise MalformedStateError from None

    def _seeded(self) -> None:
        """Refuse a saved value a result fetch's `initial` binding writes to a parameter that cannot send it.

        Each is encoded as the fetch encodes its parameter, before the final poll gives the fetch's other values; a
        value written into the fetch's querystring or body is checked when the fetch request is built. Saved values
        that alone make a path segment a dot segment are refused as a server's; a segment that also takes a value the
        final poll gives is checked when the fetch is built.
        """
        plan = self._plan
        if (fetched := plan.fetched) is None:
            return
        bindings = plan.fetch_bindings
        given = {
            cast("int", position): value
            for binding, (position, pointer), value in zip(bindings, fetched.writes, self._seed, strict=True)
            if binding.source == "initial" and pointer is None
        }
        try:
            self._client.checked_arguments(fetched.call, given, self._limits.options)
        except RequestEncodingError:
            raise MalformedStateError from None
        self._dots(
            fetched,
            tuple(
                value if binding.source == "initial" else binding.literal if binding.selector is None else _UNREAD
                for binding, value in zip(bindings, self._seed, strict=True)
            ),
        )

    def _dots(self, targeted: _Targeted[Any], written: tuple[WireValue, ...]) -> None:
        """Refuse saved values making a path segment a dot segment once encoded, as if a server had just given them."""
        if (read := _dotted(targeted, written)) is not None:
            raise self._error(None, "value", read, self._plan.operation)

    def _restore(self, state: WireValue, payload: bytes, expires_at: datetime | None) -> None:  # noqa: PLR0914
        """Restore the handle from a checkpoint's decoded state and payload, refusing what does not fit the helper.

        A settled handle decodes its saved bodies again under the call's response validation; a pending one, or one
        whose result fetch is due, prepares the requests it sends next without sending.
        """
        plan = self._plan
        require_state(isinstance(state, Mapping) and frozenset(state) == _STATE)
        fields = cast("Mapping[str, WireValue]", state)
        phase = _PHASES.get(name) if isinstance(name := fields["phase"], str) else None
        require_state(phase is not None)
        assert phase is not None
        polls, wait = state_count(fields["polls"]), state_count(fields["wait_ms"], _MOST_WAIT_MS)
        bound, seed, cancel = state_array(fields["bound"]), state_array(fields["seed"]), state_array(fields["cancel"])
        poll, kept = fields["poll"], fields["result"]
        offset, data, wire, info = 0, None, None, None
        if poll is not None:
            body, (value,) = _saved_body(poll, payload, 0, _POLL_FIELDS)
            require_state(phase is not _Phase.PENDING and plan.phases.get(canonical_json(value)) is phase)
            data, wire, info = self._decoded(plan.poll, body)
            self._snapshot = PollSnapshot(state=value, terminal=True, data=data, response=info)
            self._poll_body, offset = body, len(body[0])
        if kept is not None:
            self._result_body, _ = _saved_body(kept, payload, offset, _RESULT_FIELDS)
            offset += len(self._result_body[0])
        require_state(offset == len(payload))
        cancels = () if plan.cancel is None else plan.cancel.bindings
        match phase:
            case _Phase.PENDING:
                require_state(
                    kept is None
                    and len(bound) == len(plan.bindings)
                    and len(seed) == len(plan.fetch_bindings)
                    and len(cancel) == len(cancels)
                )
                self._bound, self._cancel = _literals(plan.bindings, bound), _literals(cancels, cancel)
                self._seed = tuple(
                    value if binding.source == "initial" else None
                    for binding, value in zip(plan.fetch_bindings, seed, strict=True)
                )
                self._dots(plan.polled, self._bound)
                self._checked(plan.polled.call, self._poll_request)
                self._seeded()
                if (remote := plan.cancel) is not None:
                    self._dots(remote.targeted, self._cancel)
                    self._checked(remote.targeted.call, _sent(remote.targeted, self._cancel))
            case _Phase.SUCCEEDED:
                require_state(not seed and not cancel)
                self._result = self._restored_result(bound, (data, wire, info))
            case _:
                require_state(poll is not None and kept is None and not bound and not seed and not cancel)
        self._phase, self._polls, self._expires_at = phase, polls, expires_at
        self._not_before = monotonic() + wait / 1000

    def _restored_result(
        self, bound: tuple[WireValue, ...], polled: tuple[object, WireValue, ResponseInfo | None]
    ) -> T | Missing:
        """Return the result a restored success holds, or MISSING with the fetch's values while its fetch is due.

        An immediate result is read again from the saved create response, a fetched one decoded from its body, and an
        inline one read again from the saved poll.
        """
        plan, kept = self._plan, self._result_body
        data, wire, info = polled
        if info is None:
            require_state(
                kept is not None and plan.immediate is not None and kept[1] in plan.immediate_statuses and not bound
            )
            assert kept is not None
            assert plan.immediate is not None
            created, created_wire, created_info = self._decoded(plan.create, kept)
            return self._read(plan.immediate, plan.immediate_selector, created, created_wire, created_info)
        if kept is not None:
            require_state(plan.fetched is not None and not bound)
            assert plan.fetched is not None
            return self._decoded(plan.fetched.call, kept)[0]
        if (inline := plan.inline) is not None:
            require_state(not bound)
            return self._read(inline, plan.inline_selector, cast("P", data), wire, info)
        if (fetched := plan.fetched) is not None:
            require_state(len(bound) == len(plan.fetch_bindings))
            self._bound = _literals(plan.fetch_bindings, bound)
            self._dots(fetched, self._bound)
            self._checked(fetched.call, self._fetch_request)
            return MISSING
        require_state(not bound)
        return cast("T", None)

    def _read(
        self, read: Callable[[V], T | None], selector: BodySelector | None, data: V, wire: WireValue, info: ResponseInfo
    ) -> T:
        """Return a result read again from a saved body, refusing one it does not carry as malformed."""
        try:
            return self._result_of(read, selector, data, wire, info, self._plan.operation)
        except ProtocolDataError:
            raise MalformedStateError from None


def _saved_body(saved: WireValue, payload: bytes, offset: int, fields: int) -> tuple[_Saved, tuple[WireValue, ...]]:
    """Return the body a saved entry describes, read from the payload at an offset, and the entry's other fields."""
    entry = state_array(saved)
    require_state(len(entry) == fields)
    status, content_type, size = state_count(entry[-3]), state_text(entry[-2]), state_count(entry[-1])
    content = payload[offset : offset + size]
    require_state(len(content) == size)
    return (content, status, content_type), entry[:-3]


def _dotted(targeted: _Targeted[Any], written: tuple[WireValue, ...]) -> Selector | None:
    """Return the selector of a read value that makes a path segment a dot segment once encoded, or None."""
    from .writes import dotted_read  # noqa: PLC0415 - A plan loaded the operation runtime.

    parameters = targeted.call.parameters
    return next(
        (
            read
            for segment, parts in targeted.dotted
            if (read := dotted_read(parameters, segment, parts, written, dict)) is not None
        ),
        None,
    )


def _receipt(
    data: K, _wire: WireValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
) -> CancelReceipt[K]:
    """Return the receipt of a remote cancel request."""
    return CancelReceipt(data=data, response=info)


def _kept(
    data: T, _wire: WireValue, content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
) -> tuple[T, _Saved]:
    """Return a fetched result as it was decoded, with its body for checkpoints."""
    return data, (content, info.status_code, info.content_type)


class LroHandle(_Operation[T, P]):
    """A long-running operation a helper created: `status` polls it once, and `wait` polls until it settles.

    `close` only stops local polling; the remote operation goes on. Polling from two threads at once raises
    ProtocolStateError, while `checkpoint` and a remote cancel run alongside a poll. A helper that declares a remote
    cancellation returns a subclass of its own with `cancel_remote`.
    """

    __slots__ = ("_core",)

    def __init__(
        self, core: ClientCore, plan: PollingPlan[T, P, Any], limits: _Limits, session: OperationSession
    ) -> None:
        """Keep the client core the handle's child calls are sent through."""
        super().__init__(core, plan, limits, session)
        self._core = core

    def _create(self, arguments: tuple[object, ...], body: object, media_type: str | MediaSelector | None) -> None:
        """Send the create request as the session's first child call and settle what it gives."""
        core, plan = self._core, self._plan
        with self._mapped(created=False):
            step = core.execute_page(
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
        self._settle(step)

    def _pause(self, call: OperationPlan[Any, object]) -> None:
        """Wait until the next poll or result fetch is due."""
        if (waiter := self._due(self._core, call)) is not None:
            try:
                waiter.sleep_until(self._not_before)
            finally:
                waiter.finish()

    def _poll(self) -> None:
        """Wait until the next poll is due, then poll once and settle what it gives; a sent poll is counted."""
        core, plan, session = self._core, self._plan, self._session
        self._pause(plan.polled.call)
        admitted = session.network_send_budget_used
        try:
            with self._mapped():
                step = core.execute_page(
                    plan,
                    plan.polled.call,
                    self._poll_request,
                    self._polled,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=session,
                    max_page_bytes=None,
                )
        except Exception as error:
            self._failed(error)
            raise
        finally:
            self._counted(admitted)
        self._settle(step)

    def _fetch(self) -> T:
        """Wait until the result fetch is due, then fetch the result once and keep it."""
        plan = self._plan
        fetched = plan.fetched
        assert fetched is not None
        self._pause(fetched.call)
        try:
            with self._mapped():
                result, kept = self._core.execute_page(
                    plan,
                    fetched.call,
                    self._fetch_request,
                    _kept,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=self._session,
                    max_page_bytes=None,
                )
        except Exception as error:
            self._failed(error)
            raise
        return self._fetched(result, kept)

    def _cancel_remote(self, cancel: CancelPlan[K]) -> CancelReceipt[K]:
        """Send the remote cancel request of a pending operation once, keeping the phase and the last poll.

        It runs alongside a step another thread is running, such as a `wait` sleeping until its next poll.
        """
        request = self._cancelling(cancel)
        with self._mapped():
            return self._core.execute_page(
                self._plan,
                cancel.targeted.call,
                request,
                _receipt,
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )

    def status(self) -> PollSnapshot[P]:
        """Poll once, after the wait the last response requires, and return the poll; a settled handle sends nothing."""
        self._enter("status")
        try:
            if (snapshot := self._status("status")) is not None:
                return snapshot
            self._poll()
            assert self._snapshot is not None
            return self._snapshot
        finally:
            self._lock.release()

    def wait(self) -> T:
        """Poll until the operation settles and return its result, fetching it at most once.

        A failed or cancelled operation raises OperationFailedError or OperationCancelledError with its last poll.
        """
        self._enter("wait")
        try:
            while self._phase is _Phase.PENDING:
                self._poll()
            if not isinstance(result := self._outcome(), Missing):
                return result
            return self._fetch()
        finally:
            self._lock.release()

    def close(self) -> None:
        """Stop polling locally; later steps raise ProtocolStateError, and closing again does nothing."""
        self._close("close")

    def __enter__(self) -> Self:
        """Return this handle, which leaving the block closes."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the handle; while another step runs, leave it open rather than mask the block's error."""
        self._close("close", quiet=exc is not None)


class AsyncLroHandle(_Operation[T, P]):
    """A long-running operation an asyncio helper created: `status` polls it once, and `wait` until it settles.

    `aclose` only stops local polling; the remote operation goes on. Polling from two tasks at once raises
    ProtocolStateError, while `checkpoint` and a remote cancel run alongside a poll. A helper that declares a remote
    cancellation returns a subclass of its own with `cancel_remote`.
    """

    __slots__ = ("_core",)

    def __init__(
        self, core: AsyncClientCore, plan: PollingPlan[T, P, Any], limits: _Limits, session: OperationSession
    ) -> None:
        """Keep the asyncio client core the handle's child calls are sent through."""
        super().__init__(core, plan, limits, session)
        self._core = core

    async def _create(
        self, arguments: tuple[object, ...], body: object, media_type: str | MediaSelector | None
    ) -> None:
        """Send the create request as the session's first child call and settle what it gives."""
        core, plan = self._core, self._plan
        with self._mapped(created=False):
            step = await core.execute_page(
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
        self._settle(step)

    async def _pause(self, call: OperationPlan[Any, object]) -> None:
        """Wait until the next poll or result fetch is due."""
        if (waiter := self._due(self._core, call)) is not None:
            try:
                await waiter.asleep_until(self._not_before)
            finally:
                waiter.finish()

    async def _poll(self) -> None:
        """Wait until the next poll is due, then poll once and settle what it gives; a sent poll is counted."""
        core, plan, session = self._core, self._plan, self._session
        await self._pause(plan.polled.call)
        admitted = session.network_send_budget_used
        try:
            with self._mapped():
                step = await core.execute_page(
                    plan,
                    plan.polled.call,
                    self._poll_request,
                    self._polled,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=session,
                    max_page_bytes=None,
                )
        except Exception as error:
            self._failed(error)
            raise
        finally:
            self._counted(admitted)
        self._settle(step)

    async def _fetch(self) -> T:
        """Wait until the result fetch is due, then fetch the result once and keep it."""
        plan = self._plan
        fetched = plan.fetched
        assert fetched is not None
        await self._pause(fetched.call)
        try:
            with self._mapped():
                result, kept = await self._core.execute_page(
                    plan,
                    fetched.call,
                    self._fetch_request,
                    _kept,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=self._session,
                    max_page_bytes=None,
                )
        except Exception as error:
            self._failed(error)
            raise
        return self._fetched(result, kept)

    async def _cancel_remote(self, cancel: CancelPlan[K]) -> CancelReceipt[K]:
        """Send the remote cancel request of a pending operation once, keeping the phase and the last poll.

        It runs alongside a step another task is running, such as a `wait` sleeping until its next poll.
        """
        request = self._cancelling(cancel)
        with self._mapped():
            return await self._core.execute_page(
                self._plan,
                cancel.targeted.call,
                request,
                _receipt,
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )

    async def status(self) -> PollSnapshot[P]:
        """Poll once, after the wait the last response requires, and return the poll; a settled handle sends nothing."""
        self._enter("status")
        try:
            if (snapshot := self._status("status")) is not None:
                return snapshot
            await self._poll()
            assert self._snapshot is not None
            return self._snapshot
        finally:
            self._lock.release()

    async def wait(self) -> T:
        """Poll until the operation settles and return its result, fetching it at most once.

        A failed or cancelled operation raises OperationFailedError or OperationCancelledError with its last poll.
        """
        self._enter("wait")
        try:
            while self._phase is _Phase.PENDING:
                await self._poll()
            if not isinstance(result := self._outcome(), Missing):
                return result
            return await self._fetch()
        finally:
            self._lock.release()

    async def aclose(self) -> None:
        """Stop polling locally; later steps raise ProtocolStateError, and closing again does nothing."""
        self._close("aclose")

    async def __aenter__(self) -> Self:
        """Return this handle, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the handle; while another step runs, leave it open rather than mask the block's error."""
        self._close("aclose", quiet=exc is not None)


def _session(limits: _Limits) -> OperationSession:
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only a started helper loads the call runtime.

    return OperationSession(
        total_timeout=limits.total_timeout, deadline=limits.deadline, max_network_sends=limits.max_network_sends
    )


def _resume_error(
    plan: PollingPlan[T, P, C], condition: Literal["fingerprint", "security", "expired", "malformed"]
) -> ResumeStateError:
    return ResumeStateError(condition=condition, helper_id=plan.helper_id, operation=plan.operation)


def _restored(
    core: ClientCore | AsyncClientCore,
    plan: PollingPlan[T, P, C],
    state: object,
    limits: _Limits,
    make: Callable[[OperationSession], OperationT],
) -> OperationT:
    """Return the handle a checkpoint continues in a session of its own, without sending.

    The checkpoint must be this helper's, made under the security the call runs with, and unexpired; a state or saved
    body that does not fit the helper is malformed.
    """
    if not isinstance(state, ResumeState):
        raise _invalid(plan, ("state",))
    helper, security, state_json, payload, expires_at = state_fields(state)
    if helper != plan.fingerprint:
        raise _resume_error(plan, "fingerprint")
    if security != _security(core, plan, limits.options)[0]:
        raise _resume_error(plan, "security")
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise _resume_error(plan, "expired")
    handle = make(_session(limits))
    try:
        handle._restore(decode_json(state_json), payload, expires_at)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    except MalformedStateError:
        raise _resume_error(plan, "malformed") from None
    return handle


@overload
def start_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    body: object = ...,
    media_type: str | MediaSelector | None = ...,
    poll_options: object = ...,
    options: object = ...,
    session_options: object = ...,
) -> LroHandle[T, P]:
    """Create a helper's operation and return a handle of the base class."""


@overload
def start_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    handle: type[H],
    body: object = ...,
    media_type: str | MediaSelector | None = ...,
    poll_options: object = ...,
    options: object = ...,
    session_options: object = ...,
) -> H:
    """Create a helper's operation and return a handle of the helper's own class."""


def start_operation(  # noqa: PLR0913
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    handle: type[LroHandle[Any, Any]] = LroHandle,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    poll_options: object = None,
    options: object = None,
    session_options: object = None,
) -> LroHandle[Any, Any]:
    """Create a helper's operation in a session of its own and return the handle that polls it.

    A helper that declares a remote cancellation passes its own handle class.
    """
    limits = _limits(core, plan, poll_options, options, session_options)
    created = handle(core, plan, limits, _session(limits))
    created._create(arguments, body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return created


@overload
async def astart_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    body: object = ...,
    media_type: str | MediaSelector | None = ...,
    poll_options: object = ...,
    options: object = ...,
    session_options: object = ...,
) -> AsyncLroHandle[T, P]:
    """Create a helper's operation with asyncio and return a handle of the base class."""


@overload
async def astart_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    handle: type[AH],
    body: object = ...,
    media_type: str | MediaSelector | None = ...,
    poll_options: object = ...,
    options: object = ...,
    session_options: object = ...,
) -> AH:
    """Create a helper's operation with asyncio and return a handle of the helper's own class."""


async def astart_operation(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    handle: type[AsyncLroHandle[Any, Any]] = AsyncLroHandle,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    poll_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncLroHandle[Any, Any]:
    """Create a helper's operation with asyncio in a session of its own and return the handle that polls it.

    A helper that declares a remote cancellation passes its own handle class.
    """
    limits = _limits(core, plan, poll_options, options, session_options)
    created = handle(core, plan, limits, _session(limits))
    await created._create(arguments, body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return created


@overload
def resume_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    state: object,
    *,
    poll_options: object = ...,
    options: object = ...,
    session_options: object = ...,
) -> LroHandle[T, P]:
    """Return a handle of the base class continuing a helper's checkpoint."""


@overload
def resume_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    state: object,
    *,
    handle: type[H],
    poll_options: object = ...,
    options: object = ...,
    session_options: object = ...,
) -> H:
    """Return a handle of the helper's own class continuing a helper's checkpoint."""


def resume_operation(  # noqa: PLR0913
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    state: object,
    *,
    handle: type[LroHandle[Any, Any]] = LroHandle,
    poll_options: object = None,
    options: object = None,
    session_options: object = None,
) -> LroHandle[Any, Any]:
    """Return a handle continuing a helper's checkpoint in a session of its own, checking the checkpoint now.

    It sends nothing, and never creates the operation again; `status` or `wait` sends its first poll.
    """
    limits = _limits(core, plan, poll_options, options, session_options)
    return _restored(core, plan, state, limits, lambda session: handle(core, plan, limits, session))


@overload
def aresume_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    state: object,
    *,
    poll_options: object = ...,
    options: object = ...,
    session_options: object = ...,
) -> AsyncLroHandle[T, P]:
    """Return an asyncio handle of the base class continuing a helper's checkpoint."""


@overload
def aresume_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    state: object,
    *,
    handle: type[AH],
    poll_options: object = ...,
    options: object = ...,
    session_options: object = ...,
) -> AH:
    """Return an asyncio handle of the helper's own class continuing a helper's checkpoint."""


def aresume_operation(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    state: object,
    *,
    handle: type[AsyncLroHandle[Any, Any]] = AsyncLroHandle,
    poll_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncLroHandle[Any, Any]:
    """Return an asyncio handle continuing a helper's checkpoint in a session of its own, checking it now.

    It is not awaited and sends nothing, and never creates the operation again; `status` or `wait` sends its first
    poll.
    """
    limits = _limits(core, plan, poll_options, options, session_options)
    return _restored(core, plan, state, limits, lambda session: handle(core, plan, limits, session))
