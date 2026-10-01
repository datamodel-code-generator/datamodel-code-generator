"""Long-running operations: their creation, the handles that poll them, and the session their child calls share.

A helper's `start` creates the operation in one child call and returns a handle. The handle polls only when `status` or
`wait` asks, first waiting out the interval and any server delay the helper declares, and fetches a result at most once.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from time import monotonic, time
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, cast, final

from typing_extensions import Self, TypeVar

from ..client.errors import BudgetExceededError, ProtocolConfigurationError
from ..client.options import RequestOptions
from ..client.timing import SessionOptions
from ..model_codecs.unset import UNSET, Unset
from .errors import (
    OperationCancelledError,
    OperationFailedError,
    PollingStateError,
    PollWaitLimitError,
    ProtocolDataError,
    ProtocolStateError,
    SessionLimitError,
)
from .options import PollOptions
from .records import (
    BodySelector,
    BodyTarget,
    ParameterTarget,
    PollSnapshot,
    QuerystringTarget,
    StatusSelector,
    canonical_json,
)
from .values import MISSING, Missing, Patch, RepeatedValueError, resolve, selected

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable
    from types import TracebackType

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import OperationPlan
    from ..client.responses import ResponseInfo
    from ..client.timing import Deadline
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .pagination import PageBinding
    from .records import ProtocolProgress, RequestTarget, Selector
    from .references import OperationRef

__all__ = ("AsyncLroHandle", "LroHandle", "PollingPlan", "astart_operation", "start_operation")

T = TypeVar("T")
P = TypeVar("P")
C = TypeVar("C")
V = TypeVar("V")

_DOT_SEGMENTS: Final = (".", "..")
_DataCondition = Literal["missing", "null", "type", "value", "malformed", "inconsistent"]


class _Phase(Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


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
    parameters it writes, which a call's options must not patch.
    """

    call: OperationPlan[T, object]
    writes: tuple[tuple[int | None, str | None], ...]
    headers: frozenset[str]
    queries: frozenset[str]

    def request(self, values: tuple[WireValue, ...]) -> tuple[tuple[object, ...], object]:
        """Return the arguments and body of a request writing each value in order, every other argument omitted.

        A parameter's value replaces its argument, and the values for a querystring or the body are patched into an
        empty object.
        """
        arguments: list[object] = [UNSET] * len(self.call.parameters)
        patches: dict[int | None, list[tuple[str, WireValue]]] = {}
        for (position, pointer), value in zip(self.writes, values, strict=True):
            if pointer is None:
                arguments[cast("int", position)] = value
            else:
                patches.setdefault(position, []).append((pointer, value))
        body: object = UNSET
        for position, writes in patches.items():
            if position is None:
                body = Patch(UNSET, tuple(writes))
            else:
                arguments[position] = Patch(UNSET, tuple(writes))
        return tuple(arguments), body


def _targeted(call: OperationPlan[T, object], targets: Iterable[RequestTarget]) -> _Targeted[T]:
    """Return an operation that takes the values written to the targets, in order, as wire values.

    Generation refuses cookie targets and requires every media of a body written to be JSON. The values skip the
    schema and argument checks, since the server chose them.
    """
    from .pagination import _position  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415
    from .writes import PatchedMedia, PatchedParameter  # noqa: PLC0415 - Only a plan loads the operation runtime.

    parameters = list(call.parameters)
    writes: list[tuple[int | None, str | None]] = []
    headers: set[str] = set()
    queries: set[str] = set()
    patched = False
    for target in targets:
        if isinstance(target, BodyTarget):
            writes.append((None, target.pointer))
            patched = True
            continue
        if isinstance(target, QuerystringTarget):
            position, pointer = _position(call, "querystring", target.name), target.pointer
        else:
            position, pointer = _position(call, target.location, target.name), None
            if (location := target.location) == "header":
                headers.add(target.name.lower())
            elif location == "query":
                queries.add(target.name)
        spec = parameters[position]
        parameters[position] = (
            replace(spec, encoder=None) if pointer is None else PatchedParameter(plan=spec.plan, encoder=spec.encoder)
        )
        writes.append((position, pointer))
    body = call.body
    if patched:
        assert body is not None
        body = replace(
            body,
            media=tuple(
                PatchedMedia(media_type=media.media_type, kind=media.kind, encoder=media.encoder)
                for media in body.media
            ),
        )
    return _Targeted(
        replace(call, parameters=tuple(parameters), body=body, checks=()),
        tuple(writes),
        frozenset(headers),
        frozenset(queries),
    )


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class PollingPlan(Generic[T, P, C]):
    """Everything fixed about one generated polling helper: its operations, states, result, bindings, and waits.

    `create` starts the operation: a status in `accepted` leaves it pending, and one in `immediate_statuses` already
    carries the result `immediate` reads from the decoded response. `poll` writes each binding's value, and its
    response's `state` must equal a value of `pending`, `succeeded`, `failed`, or `cancelled` by canonical JSON. A
    success reads its result from the final poll with `inline`, fetches it with `fetch` writing `fetch_bindings`, or
    has none. After each response, `interval` seconds pass before the next poll, or the longer delay the
    `retry_after_header` gives.
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
    phases: Mapping[bytes, _Phase] = field(init=False)
    kinds: frozenset[str] = field(init=False)
    polled: _Targeted[P] = field(init=False)
    fetched: _Targeted[T] | None = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        """Index the states by canonical JSON, and derive the poll and fetch operations writing the bindings' values."""
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
        polled = _targeted(self.poll, (binding.target for binding in self.bindings))
        fetched = None if self.fetch is None else _targeted(self.fetch, (item.target for item in self.fetch_bindings))
        others = () if fetched is None else (fetched,)
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


def _first(layers: tuple[object, ...], name: str, default: V) -> V:
    """Return a limit from the first options layer that sets it, or its default."""
    for layer in layers:
        if layer is not None and not isinstance(layer, Unset) and not isinstance(value := getattr(layer, name), Unset):
            return cast("V", value)
    return default


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: PollingPlan[T, P, C],
    poll_options: object,
    options: object,
    session_options: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    The interval defaults to the helper's. Effective options fixing an idempotency key are refused, since the create
    call and each poll need keys of their own, and so are header or query patches of a parameter the helper writes.
    """

    def invalid(path: tuple[str, ...]) -> ProtocolConfigurationError:
        return ProtocolConfigurationError(
            field_path=path, condition="invalid_value", helper_id=plan.helper_id, operation=plan.operation
        )

    for name, value, kind in (
        ("poll_options", poll_options, PollOptions),
        ("options", options, RequestOptions),
        ("session_options", session_options, SessionOptions),
    ):
        if value is not None and not isinstance(value, kind):
            raise invalid((name,))
    request = options if isinstance(options, RequestOptions) else None
    if core.fixes_key(request):
        raise invalid(("options", "idempotency_key"))
    if request is not None:
        for name, _ in request.headers:
            if name.lower() in plan.headers:
                raise invalid(("options", "headers", name))
        for name, _ in request.query:
            if name in plan.queries:
                raise invalid(("options", "query", name))
    defaults = core.protocol_defaults(plan.helper_id)
    kinds = (poll_options, UNSET if defaults is None else defaults.options)
    sessions = (session_options, UNSET if defaults is None else defaults.session)
    return _Limits(
        interval=_first(kinds, "interval", plan.interval),
        max_polls=_first(kinds, "max_polls", _DEFAULTS.max_polls),
        max_wait=_first(kinds, "max_wait", _DEFAULTS.max_wait),
        total_timeout=_first(sessions, "total_timeout", _DEFAULTS.total_timeout),
        deadline=_first(sessions, "deadline", _DEFAULTS.deadline),
        max_network_sends=_first(sessions, "max_network_sends", _DEFAULTS.max_network_sends),
        options=request,
    )


def _absence(value: WireValue | Missing) -> Literal["missing", "null"]:
    return "missing" if value is MISSING else "null"


@dataclass(frozen=True, slots=True)
class _Step(Generic[T, P]):
    """What one child call settled: the phase, when the next poll may be sent, the poll, and what comes next.

    `bound` holds the values the next poll writes while pending, and those the result fetch writes after a success;
    `seed` holds what the create response gives the fetch's `initial` bindings.
    """

    phase: _Phase
    not_before: float
    snapshot: PollSnapshot[P] | None = None
    bound: tuple[WireValue, ...] = ()
    result: T | Missing = MISSING
    seed: tuple[WireValue, ...] | None = None


class _Operation(Generic[T, P]):
    """What the synchronous and asyncio handles share: the plan, limits, session, phase, and the last poll.

    Only a settled child call changes the phase, so a failure that settles none, such as a transport error, a
    deadline, a cancellation, or a limit, leaves the handle as it was, and a later step polls again.
    """

    __slots__ = (
        "_bound",
        "_limits",
        "_lock",
        "_not_before",
        "_phase",
        "_plan",
        "_polls",
        "_result",
        "_seed",
        "_session",
        "_snapshot",
    )

    def __init__(self, plan: PollingPlan[T, P, Any], limits: _Limits, session: OperationSession) -> None:
        """Keep the plan, limits, and session; the create call settles the handle's first phase."""
        self._plan = plan
        self._limits = limits
        self._session = session
        self._lock = threading.Lock()
        self._phase = _Phase.PENDING
        self._snapshot: PollSnapshot[P] | None = None
        self._bound: tuple[WireValue, ...] = ()
        self._seed: tuple[WireValue, ...] = ()
        self._result: T | Missing = MISSING
        self._not_before = 0.0
        self._polls = 0

    def __repr__(self) -> str:
        """Name the handle's phase and its poll count only, never its data."""
        return f"{type(self).__name__}(phase={self._phase.value!r}, polls={self._polls})"

    @property
    def progress(self) -> ProtocolProgress:
        """Return the polls sent so far and the session's sends."""
        session = self._session
        return MappingProxyType({
            "polls": self._polls,
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

    def _limit(
        self, limit: int, kind: Literal["polls", "network_sends"], refused: BudgetExceededError | None = None
    ) -> SessionLimitError:
        """Return the error of a session limit reached while the operation is unsettled, with a refused child call."""
        plan = self._plan
        if refused is None:
            return SessionLimitError(
                kind=kind,
                limit=limit,
                progress=self.progress,
                helper_id=plan.helper_id,
                operation=plan.operation,
                parent_session_id=self._session.session_id,
            )
        return SessionLimitError(
            kind=kind,
            limit=limit,
            progress=self.progress,
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
    def _mapped(self) -> Generator[None, None, None]:
        """Raise a child call's refusal for want of a session send slot as the session's limit error."""
        try:
            yield
        except BudgetExceededError as error:
            if error.budget_kind != "parent_network":
                raise
            raise self._limit(error.limit, "network_sends", error) from None

    def _enter(self, action: str) -> None:
        """Take the handle for one step, refusing a concurrent step and a closed handle."""
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, "polling")
        if self._phase is _Phase.CLOSED:
            self._lock.release()
            raise self._state_error(action, _Phase.CLOSED.value)

    def _close(self, action: str) -> None:
        if not self._lock.acquire(blocking=False):
            raise self._state_error(action, "polling")
        self._phase = _Phase.CLOSED
        self._lock.release()

    def _sendable(self) -> None:
        """Refuse a child call the session has no send slot left for."""
        session = self._session
        if (limit := session.send_limit) is not None and session.network_send_budget_used >= limit:
            raise self._limit(limit, "network_sends")

    def _due(self, core: ClientCore | AsyncClientCore) -> LogicalCallContext | None:
        """Return the context to wait in before the next poll, or None when it may be sent now.

        The poll limit and the session's send slots are checked first. A wait longer than the allowed wait, or not
        shorter than what remains of the deadline, raises PollWaitLimitError instead of polling early.
        """
        if (limit := self._limits.max_polls) is not None and self._polls >= limit:
            raise self._limit(limit, "polls")
        self._sendable()
        if (required := self._not_before - monotonic()) <= 0:
            return None
        limits = self._limits
        if (allowed := limits.max_wait) is not None and required > allowed:
            raise self._waited(required, allowed, "wait")
        waiter = core.waiting(limits.options, self._session)
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
            helper_id=plan.helper_id,
            operation=plan.poll_operation,
            parent_session_id=self._session.session_id,
        )

    def _error(
        self, info: ResponseInfo, condition: _DataCondition, location: Selector, operation: OperationRef | None
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

    def _values(
        self,
        bindings: tuple[PageBinding, ...],
        wire: WireValue,
        info: ResponseInfo,
        operation: OperationRef | None,
        kept: tuple[WireValue, ...] = (),
    ) -> tuple[WireValue, ...]:
        """Return the values bindings write: their literals, kept `initial` values, and what the response gives.

        A missing value and a dot segment read for a path parameter are refused.
        """
        written: list[WireValue] = []
        for index, binding in enumerate(bindings):
            if (selector := binding.selector) is None:
                written.append(binding.literal)
            elif kept and binding.source == "initial":
                written.append(kept[index])
            elif (value := self._selected(selector, wire, info, operation)) is MISSING:
                raise self._error(info, "missing", selector, operation)
            elif (
                isinstance(target := binding.target, ParameterTarget)
                and target.location == "path"
                and (value in _DOT_SEGMENTS)
            ):
                raise self._error(info, "value", selector, operation)
            else:
                written.append(value)
        return tuple(written)

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
        if (
            (selected := resolve(wire, selector.pointer)) is MISSING
            or selected is None
            or (value := read(data)) is None
        ):
            raise self._error(info, _absence(selected), selector, operation)
        return value

    def _created(
        self, data: object, wire: WireValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> _Step[T, P]:
        """Settle the create response: an accepted status pends, an immediate one carries the result.

        Any other success status is refused, since it neither starts nor completes the operation as the helper
        declares. The values the first poll and the result fetch take from the response are read now.
        """
        plan = self._plan
        status, not_before, operation = info.status_code, self._after(info), plan.operation
        if status in plan.accepted:
            seed = tuple(
                self._values((binding,), wire, info, operation)[0] if binding.source == "initial" else None
                for binding in plan.fetch_bindings
            )
            bound = self._values(plan.bindings, wire, info, operation)
            return _Step(_Phase.PENDING, not_before, bound=bound, seed=seed)
        if status in plan.immediate_statuses:
            assert plan.immediate is not None
            result = self._result_of(plan.immediate, plan.immediate_selector, data, wire, info, plan.operation)
            return _Step(_Phase.SUCCEEDED, not_before, result=result)
        raise self._error(info, "value", StatusSelector(), plan.operation)

    def _polled(
        self, data: P, wire: WireValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> _Step[T, P]:
        """Settle one poll by its state; an unknown state raises PollingStateError and success is never inferred.

        A pending poll reads the values of the next poll's bindings, and a success its result or what its result
        fetch writes.
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
        bound: tuple[WireValue, ...] = ()
        result: T | Missing = MISSING
        if phase is _Phase.PENDING:
            bound = self._values(plan.bindings, wire, info, operation, self._bound)
        elif phase is _Phase.SUCCEEDED:
            bound, result = self._succeeded(data, wire, info)
        return _Step(phase, self._after(info), snapshot, bound, result)

    def _succeeded(self, data: P, wire: WireValue, info: ResponseInfo) -> tuple[tuple[WireValue, ...], T | Missing]:
        """Return what the result fetch writes after a successful poll, or the result the poll carries itself."""
        plan = self._plan
        operation = plan.poll_operation
        if (inline := plan.inline) is not None:
            return (), self._result_of(inline, plan.inline_selector, data, wire, info, operation)
        if plan.fetched is not None:
            return self._values(plan.fetch_bindings, wire, info, operation, self._seed), MISSING
        return (), cast("T", None)

    def _settle(self, step: _Step[T, P]) -> None:
        self._phase, self._not_before = step.phase, step.not_before
        self._snapshot = step.snapshot or self._snapshot
        self._bound, self._result = step.bound, step.result
        if step.seed is not None:
            self._seed = step.seed

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
        if isinstance(self._result, Missing):
            self._sendable()
        return self._result

    def _fetch_request(self) -> tuple[tuple[object, ...], object, None]:
        fetched = self._plan.fetched
        assert fetched is not None
        return (*fetched.request(self._bound), None)

    def _poll_request(self) -> tuple[tuple[object, ...], object, None]:
        return (*self._plan.polled.request(self._bound), None)


def _data(data: T, _wire: WireValue, _content: bytes, _info: ResponseInfo, _url: str, _managed: frozenset[str]) -> T:
    """Return a fetched result as it was decoded."""
    return data


@final
class LroHandle(_Operation[T, P]):
    """A long-running operation a helper created: `status` polls it once, and `wait` polls until it settles.

    `close` only stops local polling; the remote operation goes on. Polling from two threads at once raises
    ProtocolStateError.
    """

    __slots__ = ("_core",)

    def __init__(
        self, core: ClientCore, plan: PollingPlan[T, P, Any], limits: _Limits, session: OperationSession
    ) -> None:
        """Keep the client core the handle's child calls are sent through."""
        super().__init__(plan, limits, session)
        self._core = core

    def _create(self, arguments: tuple[object, ...], body: object, media_type: str | MediaSelector | None) -> None:
        """Send the create request as the session's first child call and settle what it gives."""
        core, plan = self._core, self._plan
        with self._mapped():
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

    def _poll(self) -> None:
        """Wait until the next poll is due, then poll once and settle what it gives."""
        core, plan = self._core, self._plan
        if (waiter := self._due(core)) is not None:
            try:
                waiter.sleep_until(self._not_before)
            finally:
                waiter.finish()
        self._polls += 1
        with self._mapped():
            step = core.execute_page(
                plan,
                plan.polled.call,
                self._poll_request,
                self._polled,
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )
        self._settle(step)

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
            plan = self._plan
            assert plan.fetched is not None
            with self._mapped():
                result = self._core.execute_page(
                    plan,
                    plan.fetched.call,
                    self._fetch_request,
                    _data,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=self._session,
                    max_page_bytes=None,
                )
            self._result = result
            return result
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
        """Close the handle."""
        self.close()


@final
class AsyncLroHandle(_Operation[T, P]):
    """A long-running operation an asyncio helper created: `status` polls it once, and `wait` until it settles.

    `aclose` only stops local polling; the remote operation goes on. Polling from two tasks at once raises
    ProtocolStateError.
    """

    __slots__ = ("_core",)

    def __init__(
        self, core: AsyncClientCore, plan: PollingPlan[T, P, Any], limits: _Limits, session: OperationSession
    ) -> None:
        """Keep the asyncio client core the handle's child calls are sent through."""
        super().__init__(plan, limits, session)
        self._core = core

    async def _create(
        self, arguments: tuple[object, ...], body: object, media_type: str | MediaSelector | None
    ) -> None:
        """Send the create request as the session's first child call and settle what it gives."""
        core, plan = self._core, self._plan
        with self._mapped():
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

    async def _poll(self) -> None:
        """Wait until the next poll is due, then poll once and settle what it gives."""
        core, plan = self._core, self._plan
        if (waiter := self._due(core)) is not None:
            try:
                await waiter.asleep_until(self._not_before)
            finally:
                waiter.finish()
        self._polls += 1
        with self._mapped():
            step = await core.execute_page(
                plan,
                plan.polled.call,
                self._poll_request,
                self._polled,
                body=UNSET,
                media_type=None,
                options=self._limits.options,
                session=self._session,
                max_page_bytes=None,
            )
        self._settle(step)

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
            plan = self._plan
            assert plan.fetched is not None
            with self._mapped():
                result = await self._core.execute_page(
                    plan,
                    plan.fetched.call,
                    self._fetch_request,
                    _data,
                    body=UNSET,
                    media_type=None,
                    options=self._limits.options,
                    session=self._session,
                    max_page_bytes=None,
                )
            self._result = result
            return result
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
        """Close the handle."""
        await self.aclose()


def _session(limits: _Limits) -> OperationSession:
    from ..client.logical import OperationSession  # noqa: PLC0415 - Only a started helper loads the call runtime.

    return OperationSession(
        total_timeout=limits.total_timeout, deadline=limits.deadline, max_network_sends=limits.max_network_sends
    )


def start_operation(  # noqa: PLR0913
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    poll_options: object = None,
    options: object = None,
    session_options: object = None,
) -> LroHandle[T, P]:
    """Create a helper's operation in a session of its own and return the handle that polls it."""
    limits = _limits(core, plan, poll_options, options, session_options)
    handle = LroHandle(core, plan, limits, _session(limits))
    handle._create(arguments, body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle


async def astart_operation(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    poll_options: object = None,
    options: object = None,
    session_options: object = None,
) -> AsyncLroHandle[T, P]:
    """Create a helper's operation with asyncio in a session of its own and return the handle that polls it."""
    limits = _limits(core, plan, poll_options, options, session_options)
    handle = AsyncLroHandle(core, plan, limits, _session(limits))
    await handle._create(arguments, body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle
