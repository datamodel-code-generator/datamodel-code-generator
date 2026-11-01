"""Long-running operations: their creation, the handles that poll them, and the session their child calls share.

A helper's `start` creates the operation in one child call and returns a handle. The handle polls only when `status` or
`wait` asks, first waiting out the interval and any server delay the helper declares, and fetches a result at most once.
Its `checkpoint` returns the values its next poll writes as plain JSON, which the helper's `resume` polls again in a
session of its own without creating the operation again.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, cast, final, overload

from typing_extensions import TypeVar

from ..client.responses import ResponseInfo
from ..model_codecs.unset import UNSET
from .errors import ProtocolDataError, SessionLimitError, configuration_error, data_refusal
from .options import AsyncStepHandle, HelperLimits, PollOptions, StepHandle, StepLock, layered, new_session
from .pagination import saved_values, targeted_values
from .records import (
    BodySelector,
    CancelReceipt,
    PollSnapshot,
    StatusSelector,
    canonical_json,
    plain_copy,
)
from .resume import require_state, restored, saved_expiry, state_array, state_expiry, state_fields
from .values import MISSING, accessed, read_expiry, required

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ..client.logical import LogicalCallContext, OperationSession
    from ..client.operations import OperationPlan
    from ..model_codecs.media import JSONValue
    from .client import AsyncClientCore, ClientCore
    from .errors import Refuse
    from .pagination import PageBinding
    from .records import ProtocolProgress, Selector
    from .references import OperationRef
    from .writes import Targeted

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
R = TypeVar("R")
H = TypeVar("H", bound="LroHandle[Any, Any]")
AH = TypeVar("AH", bound="AsyncLroHandle[Any, Any]")
OperationT = TypeVar("OperationT", bound="_Operation[Any, Any]")


class _Phase(Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_STATE: Final = frozenset({"phase", "bound", "seed", "cancel", "expires_at"})
_KINDS: Final[Mapping[type, str]] = MappingProxyType({
    type(None): "null",
    bool: "boolean",
    str: "string",
    list: "array",
})


def _kind(value: JSONValue) -> str:
    """Return the JSON type of a value, every number being one type."""
    return _KINDS.get(type(value)) or ("object" if isinstance(value, Mapping) else "number")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class CancelPlan(Generic[K]):
    """The remote cancellation a polling helper declares: its operation, and the bindings it writes.

    Its bindings read the create response for `initial` and the latest response, the create response and then each
    pending poll, for `previous`; `targeted` sends only what they write.
    """

    operation: OperationRef
    call: OperationPlan[K]
    bindings: tuple[PageBinding, ...] = ()
    targeted: Targeted[K] = field(init=False)

    def __post_init__(self) -> None:
        """Derive the cancel operation writing the bindings' values."""
        from .writes import targeted_writes  # noqa: PLC0415 - Only a plan loads the operation runtime.

        targeted = targeted_writes(self.call, (binding.written for binding in self.bindings))
        object.__setattr__(self, "targeted", targeted)


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
    expiry of the operation from an accepted create response.
    """

    helper_id: str
    operation: OperationRef
    create: OperationPlan[C]
    accepted: tuple[int, ...]
    poll_operation: OperationRef
    poll: OperationPlan[P]
    state: Selector
    pending: tuple[JSONValue, ...]
    succeeded: tuple[JSONValue, ...]
    bindings: tuple[PageBinding, ...] = ()
    failed: tuple[JSONValue, ...] = ()
    cancelled: tuple[JSONValue, ...] = ()
    inline: Callable[[P], T | None] | None = None
    inline_selector: BodySelector | None = None
    fetch_operation: OperationRef | None = None
    fetch: OperationPlan[T] | None = None
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
    polled: Targeted[P] = field(init=False)
    fetched: Targeted[T] | None = field(init=False)
    headers: frozenset[str] = field(init=False)
    queries: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        """Index the states by canonical JSON, and derive the poll, fetch, and cancel operations writing values."""
        from .writes import targeted_writes, written_names  # noqa: PLC0415 - Only a plan loads the operation runtime.

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
        polled = targeted_writes(self.poll, (binding.written for binding in self.bindings))
        fetch = self.fetch
        fetched = (
            None if fetch is None else targeted_writes(fetch, (binding.written for binding in self.fetch_bindings))
        )
        cancel = self.cancel
        headers, queries = written_names(children=(polled, fetched, None if cancel is None else cancel.targeted))
        object.__setattr__(self, "phases", MappingProxyType(phases))
        object.__setattr__(
            self,
            "kinds",
            frozenset(map(_kind, (*self.pending, *self.succeeded, *self.failed, *self.cancelled))),
        )
        object.__setattr__(self, "polled", polled)
        object.__setattr__(self, "fetched", fetched)
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "queries", queries)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Limits(HelperLimits):
    """The effective limits of one helper call, each from the first layer that sets it; None removes a limit."""

    interval: float
    max_polls: int | None = 1000
    max_wait: float | None = 60.0
    total_timeout: float | None = 600.0


_DEFAULTS: Final = _Limits(interval=1.0)


def _limits(
    core: ClientCore | AsyncClientCore,
    plan: PollingPlan[T, P, C],
    poll_options: object,
    options: object,
) -> _Limits:
    """Check the call's option types and merge each limit: the call's, the client's helper defaults, the kind's.

    The interval defaults to the helper's; one longer than the allowed wait, or not shorter than the session, could
    never be waited out, so it is refused before anything is sent. A fixed idempotency key, the call's own or a header
    of its name the call, the client, or a view sends, is refused, since the create call and each poll need keys of
    their own, and so are the headers or query names of a parameter the helper writes that any of them sends.
    """
    request, kinds = core.helper_layers(plan.helper_id, "poll_options", poll_options, PollOptions, options)
    operations = (plan.create, plan.poll, plan.fetch)
    core.refuse_patched(plan.helper_id, request, operations, plan.headers, plan.queries.__contains__)
    limits = _Limits(
        interval=layered(kinds, "interval", plan.interval),
        max_polls=layered(kinds, "max_polls", _DEFAULTS.max_polls),
        max_wait=layered(kinds, "max_wait", _DEFAULTS.max_wait),
        total_timeout=layered(kinds, "total_timeout", _DEFAULTS.total_timeout),
        options=request,
        clock=core.clock,
    )
    interval, total = limits.interval, limits.total_timeout
    if ((allowed := limits.max_wait) is not None and interval > allowed) or (total is not None and interval >= total):
        raise configuration_error(plan.helper_id, ("poll_options", "interval"))
    return limits


@dataclass(frozen=True, slots=True)
class _Step(Generic[T, P]):
    """What one child call settled: the phase, when the next poll may be sent, the poll, and what comes next.

    `bound` holds the values the next poll writes while pending, and those the result fetch writes after a success;
    `seed` holds what the create response gives the fetch's `initial` bindings, and `cancel` what a remote cancel
    writes while pending. `expires_at` is the server's expiry an accepted create response gives.
    """

    phase: _Phase
    not_before: float
    snapshot: PollSnapshot[P] | None = None
    bound: tuple[JSONValue, ...] = ()
    result: T | MISSING = MISSING
    seed: tuple[JSONValue, ...] | None = None
    cancel: tuple[JSONValue, ...] = ()
    expires_at: datetime | None = None


class _Operation(StepLock, Generic[T, P]):
    """What the synchronous and asyncio handles share: the plan, limits, session, phase, and the last poll.

    Only a settled child call changes the phase, so a failure that settles none, such as a transport error, a
    deadline, a cancellation, or a limit, leaves the handle as it was, and a later step polls again unless a session
    limit it hit stays spent. The handle keeps what its last pending poll and remote cancel write, which its checkpoint
    returns.

    `_lock` is held by the one step that polls or fetches, across its waits and sends; `_guard` only while what a
    step settles is written or read together, so a checkpoint or a remote cancel never waits for a step.
    """

    __slots__ = (
        "_bound",
        "_cancel",
        "_client",
        "_expires_at",
        "_guard",
        "_limits",
        "_not_before",
        "_phase",
        "_plan",
        "_polls",
        "_result",
        "_seed",
        "_session",
        "_snapshot",
    )

    _busy = "polling"

    def __init__(
        self,
        client: ClientCore | AsyncClientCore,
        plan: PollingPlan[T, P, Any],
        limits: _Limits,
        session: OperationSession,
    ) -> None:
        """Keep the plan, limits, and session; the create call or a checkpoint settles the handle's first phase."""
        super().__init__(plan.helper_id)
        self._client = client
        self._plan = plan
        self._limits = limits
        self._session = session
        self._guard = threading.Lock()
        self._phase = _Phase.PENDING
        self._snapshot: PollSnapshot[P] | None = None
        self._bound: tuple[JSONValue, ...] = ()
        self._seed: tuple[JSONValue, ...] = ()
        self._cancel: tuple[JSONValue, ...] = ()
        self._result: T | MISSING = MISSING
        self._expires_at: datetime | None = None
        self._not_before = 0.0
        self._polls = 0

    def __repr__(self) -> str:
        """Name the handle's phase and its poll count only, never its data."""
        phase = "closed" if self._closed else self._phase.value
        return f"{type(self).__name__}(phase={phase!r}, polls={self._polls})"

    @property
    def progress(self) -> ProtocolProgress:
        """Return the polls sent so far."""
        return MappingProxyType({
            "polls": self._polls,
        })

    def checkpoint(self) -> JSONValue:
        """Return the server values the helper's `resume` continues from, as plain JSON, sending nothing.

        A pending handle returns what the next poll and a remote cancel write and the values the create response gave
        the result fetch; a success whose result fetch is due returns what the fetch writes. Both keep the server's
        expiry, but neither polls, results, the session, nor the call's options. A closed handle is checkpointed as it
        stood, and a handle another thread or task is polling as its last settled step left it. A settled operation
        has nothing left to continue and refuses with ConfigurationError.
        """
        with self._guard:
            if (pending := self._phase is _Phase.PENDING) or (
                self._phase is _Phase.SUCCEEDED and self._result is MISSING
            ):
                return plain_copy({
                    "phase": "pending" if pending else "fetch",
                    "bound": self._bound,
                    "seed": self._seed if pending else (),
                    "cancel": self._cancel if pending else (),
                    "expires_at": saved_expiry(self._expires_at),
                })
        action = "checkpoint"
        raise self._state_error(action, self._phase.value)

    def _limit(self, limit: int, kind: Literal["polls"]) -> SessionLimitError:
        """Return the error of a session limit reached while the operation is unsettled; `checkpoint` continues it."""
        plan = self._plan
        return SessionLimitError(
            reason=kind, limit=limit, progress=self.progress, helper_id=plan.helper_id, operation=plan.operation
        )

    def _cancelling(self, cancel: CancelPlan[K]) -> Callable[[], tuple[tuple[object, ...], object, None]]:
        """Return the remote cancel request of a pending operation, without waiting for a step that polls it.

        A closed handle and a settled operation refuse it.
        """
        action = "cancel_remote"
        with self._guard:
            if self._closed:
                raise self._state_error(action, "closed")
            if (phase := self._phase) is not _Phase.PENDING:
                raise self._state_error(action, phase.value)
            values = self._cancel
        return cancel.targeted.sent(values)

    def _due(self, core: ClientCore | AsyncClientCore, call: OperationPlan[Any]) -> LogicalCallContext | None:
        """Return the context to wait in before the next poll or result fetch, or None when it may be sent now.

        The poll limit, for a poll, and the session's send slots are checked first. A wait longer than the allowed
        wait, or not shorter than what remains of the deadline, raises SessionLimitError instead of sending early.
        """
        if call is self._plan.polled.call and (limit := self._limits.max_polls) is not None and self._polls >= limit:
            raise self._limit(limit, "polls")
        if (required := self._not_before - self._limits.clock.monotonic()) <= 0:
            return None
        limits = self._limits
        if (allowed := limits.max_wait) is not None and required > allowed:
            raise self._waited(required, allowed, "wait")
        waiter = core.waiting(limits.options, self._session, call.operation_id)
        if (remaining := waiter.cut_short(required)) is not None:
            raise self._waited(required, remaining, "deadline")
        return waiter

    def _waited(self, required: float, limit: float, kind: Literal["wait", "deadline"]) -> SessionLimitError:
        plan = self._plan
        return SessionLimitError(
            reason=kind,
            limit=limit,
            progress=self.progress,
            required_wait=required,
            helper_id=plan.helper_id,
            operation=plan.poll_operation,
        )

    def _after(self, info: ResponseInfo) -> float:
        """Return when the next poll may be sent after a response: its receipt plus the interval or a longer delay.

        Only the header the helper declares gives a server delay; one that is not a valid delay is ignored.
        """
        limits = self._limits
        received, delay = limits.clock.monotonic(), limits.interval
        if (name := self._plan.retry_after_header) is not None:
            from ..client.retry import header_delay  # noqa: PLC0415 - Only a helper with a delay header reads one.

            if (server := header_delay(info.headers, name, limits.clock.time())) is not None:
                delay = max(delay, server)
        return received + delay

    def _created(
        self, data: object, wire: JSONValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> _Step[T, P]:
        """Settle the create response: an accepted status pends, an immediate one carries the result.

        Any other success status is refused, since it neither starts nor completes the operation as the helper
        declares. The values the first poll, the result fetch, and a remote cancel take from the response are read
        now, with the server's expiry: an RFC 3339 date-time with an offset or an HTTP date.
        """
        plan = self._plan
        status, not_before = info.status_code, self._after(info)
        refuse = data_refusal(plan.helper_id, plan.operation, info)
        if status in plan.accepted:
            seed = tuple(
                (binding.literal if (read := binding.selector) is None else required(read, wire, info, refuse))
                if binding.source == "initial"
                else None
                for binding in plan.fetch_bindings
            )
            bound = targeted_values(plan.polled, plan.bindings, wire, info, refuse)
            cancels = plan.cancel
            cancel = () if cancels is None else targeted_values(cancels.targeted, cancels.bindings, wire, info, refuse)
            expiry = plan.expires_at
            expires_at = None if expiry is None else read_expiry(expiry, wire, info, self._limits.clock, refuse)
            return _Step(_Phase.PENDING, not_before, bound=bound, seed=seed, cancel=cancel, expires_at=expires_at)
        if status in plan.immediate_statuses:
            assert plan.immediate is not None
            assert plan.immediate_selector is not None
            result = accessed(plan.immediate, plan.immediate_selector, data, wire, refuse)
            return _Step(_Phase.SUCCEEDED, not_before, result=result)
        raise refuse(StatusSelector(), "value")

    def _polled(
        self, data: P, wire: JSONValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
    ) -> _Step[T, P]:
        """Settle one poll by its state; an unknown state raises ProtocolDataError and success is never inferred.

        A pending poll reads the values of the next poll's and a remote cancel's bindings, and a success its result or
        what its result fetch writes.
        """
        plan = self._plan
        read = plan.state
        refuse = data_refusal(plan.helper_id, plan.poll_operation, info)
        value = required(read, wire, info, refuse)
        if (phase := plan.phases.get(canonical_json(value))) is None:
            raise refuse(read, "value" if _kind(value) in plan.kinds else "type")
        snapshot = PollSnapshot(state=value, terminal=phase is not _Phase.PENDING, data=data, response=info)
        if phase is _Phase.PENDING:
            polled = targeted_values(plan.polled, plan.bindings, wire, info, refuse, self._bound)
            if (cancel := plan.cancel) is None:
                return _Step(phase, self._after(info), snapshot, polled)
            cancels = targeted_values(cancel.targeted, cancel.bindings, wire, info, refuse, self._cancel)
            return _Step(phase, self._after(info), snapshot, polled, cancel=cancels)
        bound: tuple[JSONValue, ...] = ()
        result: T | MISSING = MISSING
        if phase is _Phase.SUCCEEDED:
            bound, result = self._succeeded(data, wire, info, refuse)
        return _Step(phase, self._limits.clock.monotonic(), snapshot, bound, result)

    def _succeeded(
        self, data: P, wire: JSONValue, info: ResponseInfo, refuse: Refuse
    ) -> tuple[tuple[JSONValue, ...], T | MISSING]:
        """Return what the result fetch writes after a successful poll, or the result the poll carries itself."""
        plan = self._plan
        if (inline := plan.inline) is not None:
            assert plan.inline_selector is not None
            return (), accessed(inline, plan.inline_selector, data, wire, refuse)
        if (fetched := plan.fetched) is not None:
            return targeted_values(fetched, plan.fetch_bindings, wire, info, refuse, self._seed), MISSING
        return (), cast("T", None)

    def _counted(self, admitted: int) -> None:
        """Count a poll once its call was admitted to send, never one refused before sending."""
        if self._session.sends > admitted:
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
            if step.seed is not None:
                self._seed = step.seed
            if step.expires_at is not None:
                self._expires_at = step.expires_at

    def _fetched(self, result: T) -> T:
        """Keep a fetched result; the fetch's values are no longer needed."""
        with self._guard:
            self._result, self._bound = result, ()
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

    def _outcome(self) -> T | MISSING:
        """Return the settled operation's result, MISSING while its fetch is due; a failure or cancellation raises.

        The terminal poll's data and response stay on the error, which every later `wait` raises again without sending.
        """
        if (phase := self._phase) in {_Phase.FAILED, _Phase.CANCELLED}:
            plan, snapshot = self._plan, self._snapshot
            assert snapshot is not None
            raise ProtocolDataError(
                reason="operation_failed" if phase is _Phase.FAILED else "operation_cancelled",
                data=snapshot.data,
                helper_id=plan.helper_id,
                operation=plan.poll_operation,
                info=snapshot.response,
            )
        return self._result

    def _restore(self, state: JSONValue) -> None:
        """Restore a handle from a checkpoint, refusing what does not fit the helper and saved dot segments.

        A saved dot segment is refused as if a server had just given it. Saved values are encoded as the server's are
        when the next poll, fetch, or remote cancel is built.
        """
        from .writes import refuse_dotted  # noqa: PLC0415 - A plan loaded the operation runtime.

        plan = self._plan
        refuse = data_refusal(plan.helper_id, plan.operation, None)
        fields = state_fields(state, _STATE)
        phase = fields["phase"]
        bound, seed, cancel = state_array(fields["bound"]), state_array(fields["seed"]), state_array(fields["cancel"])
        self._expires_at = state_expiry(fields["expires_at"])
        if phase == "fetch":
            fetched = plan.fetched
            require_state(fetched is not None and len(bound) == len(plan.fetch_bindings) and not seed and not cancel)
            assert fetched is not None
            self._phase, self._bound = _Phase.SUCCEEDED, saved_values(plan.fetch_bindings, bound)
            refuse_dotted(fetched, self._bound, refuse)
            return
        cancels = () if plan.cancel is None else plan.cancel.bindings
        require_state(
            phase == "pending"
            and len(bound) == len(plan.bindings)
            and len(seed) == len(plan.fetch_bindings)
            and len(cancel) == len(cancels)
        )
        self._bound, self._cancel = saved_values(plan.bindings, bound), saved_values(cancels, cancel)
        self._seed = tuple(
            value if binding.source == "initial" else None
            for binding, value in zip(plan.fetch_bindings, seed, strict=True)
        )
        refuse_dotted(plan.polled, self._bound, refuse)
        if (remote := plan.cancel) is not None:
            refuse_dotted(remote.targeted, self._cancel, refuse)


def _receipt(
    data: K, _wire: JSONValue, _content: bytes, info: ResponseInfo, _url: str, _managed: frozenset[str]
) -> CancelReceipt[K]:
    """Return the receipt of a remote cancel request."""
    return CancelReceipt(data=data, response=info)


def _kept(data: T, _wire: JSONValue, _content: bytes, _info: ResponseInfo, _url: str, _managed: frozenset[str]) -> T:
    """Return a fetched result as it was decoded."""
    return data


class LroHandle(_Operation[T, P], StepHandle):
    """A long-running operation a helper created: `status` polls it once, and `wait` polls until it settles.

    `close` only stops local polling; the remote operation goes on. Polling from two threads at once raises
    ConfigurationError, while `checkpoint` and a remote cancel run alongside a poll. A helper that declares a remote
    cancellation returns a subclass of its own with `cancel_remote`.
    """

    __slots__ = ("_core",)

    def __init__(
        self, core: ClientCore, plan: PollingPlan[T, P, Any], limits: _Limits, session: OperationSession
    ) -> None:
        """Keep the client core the handle's child calls are sent through."""
        super().__init__(core, plan, limits, session)
        self._core = core

    def _create(self, arguments: tuple[object, ...], body: object, media_type: str | None) -> None:
        """Send the create request as the session's first child call and settle what it gives."""
        create = (self._plan.create, lambda: (arguments, body, None), self._created)
        self._settle(self._send(*create, body=body, media_type=media_type))

    def _pause(self, call: OperationPlan[Any]) -> None:
        """Wait until the next poll or result fetch is due."""
        if (waiter := self._due(self._core, call)) is not None:
            waiter.sleep_until(self._not_before)

    def _send(
        self,
        call: OperationPlan[V],
        request: Callable[[], tuple[tuple[object, ...], object, None]],
        build: Callable[[V, JSONValue, bytes, ResponseInfo, str, frozenset[str]], R],
        *,
        body: object = UNSET,
        media_type: str | None = None,
    ) -> R:
        """Send a child call of the handle's session, with no body but the caller's of the create call."""
        options, session = self._limits.options, self._session
        return self._core.execute_page(
            call, request, build, body=body, media_type=media_type, options=options, session=session
        )

    def _poll(self) -> None:
        """Wait until the next poll is due, then poll once and settle what it gives; a sent poll is counted."""
        plan = self._plan
        polled = plan.polled
        self._pause(polled.call)
        admitted = self._session.sends
        try:
            step = self._send(polled.call, polled.sent(self._bound), self._polled)
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
            result = self._send(fetched.call, fetched.sent(self._bound), _kept)
        except Exception as error:
            self._failed(error)
            raise
        return self._fetched(result)

    def _cancel_remote(self, cancel: CancelPlan[K]) -> CancelReceipt[K]:
        """Send the remote cancel request of a pending operation once, keeping the phase and the last poll.

        It runs alongside a step another thread is running, such as a `wait` sleeping until its next poll.
        """
        request = self._cancelling(cancel)
        return self._send(cancel.targeted.call, request, _receipt)

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

        A failed or cancelled operation raises ProtocolDataError with its last poll's data and response.
        """
        self._enter("wait")
        try:
            while self._phase is _Phase.PENDING:
                self._poll()
            if (result := self._outcome()) is not MISSING:
                return result
            return self._fetch()
        finally:
            self._lock.release()

    def close(self) -> None:
        """Stop polling locally; later steps raise ConfigurationError, and closing again does nothing."""
        self._close("close")


class AsyncLroHandle(_Operation[T, P], AsyncStepHandle):
    """A long-running operation an asyncio helper created: `status` polls it once, and `wait` until it settles.

    `aclose` only stops local polling; the remote operation goes on. Polling from two tasks at once raises
    ConfigurationError, while `checkpoint` and a remote cancel run alongside a poll. A helper that declares a remote
    cancellation returns a subclass of its own with `cancel_remote`.
    """

    __slots__ = ("_core",)

    def __init__(
        self, core: AsyncClientCore, plan: PollingPlan[T, P, Any], limits: _Limits, session: OperationSession
    ) -> None:
        """Keep the asyncio client core the handle's child calls are sent through."""
        super().__init__(core, plan, limits, session)
        self._core = core

    async def _create(self, arguments: tuple[object, ...], body: object, media_type: str | None) -> None:
        """Send the create request as the session's first child call and settle what it gives."""
        create = (self._plan.create, lambda: (arguments, body, None), self._created)
        self._settle(await self._send(*create, body=body, media_type=media_type))

    async def _pause(self, call: OperationPlan[Any]) -> None:
        """Wait until the next poll or result fetch is due."""
        if (waiter := self._due(self._core, call)) is not None:
            await waiter.asleep_until(self._not_before)

    async def _send(
        self,
        call: OperationPlan[V],
        request: Callable[[], tuple[tuple[object, ...], object, None]],
        build: Callable[[V, JSONValue, bytes, ResponseInfo, str, frozenset[str]], R],
        *,
        body: object = UNSET,
        media_type: str | None = None,
    ) -> R:
        """Send a child call of the handle's session, with no body but the caller's of the create call."""
        options, session = self._limits.options, self._session
        return await self._core.execute_page(
            call, request, build, body=body, media_type=media_type, options=options, session=session
        )

    async def _poll(self) -> None:
        """Wait until the next poll is due, then poll once and settle what it gives; a sent poll is counted."""
        plan = self._plan
        polled = plan.polled
        await self._pause(polled.call)
        admitted = self._session.sends
        try:
            step = await self._send(polled.call, polled.sent(self._bound), self._polled)
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
            result = await self._send(fetched.call, fetched.sent(self._bound), _kept)
        except Exception as error:
            self._failed(error)
            raise
        return self._fetched(result)

    async def _cancel_remote(self, cancel: CancelPlan[K]) -> CancelReceipt[K]:
        """Send the remote cancel request of a pending operation once, keeping the phase and the last poll.

        It runs alongside a step another task is running, such as a `wait` sleeping until its next poll.
        """
        request = self._cancelling(cancel)
        return await self._send(cancel.targeted.call, request, _receipt)

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

        A failed or cancelled operation raises ProtocolDataError with its last poll's data and response.
        """
        self._enter("wait")
        try:
            while self._phase is _Phase.PENDING:
                await self._poll()
            if (result := self._outcome()) is not MISSING:
                return result
            return await self._fetch()
        finally:
            self._lock.release()

    async def aclose(self) -> None:
        """Stop polling locally; later steps raise ConfigurationError, and closing again does nothing."""
        self._close("aclose")


def _restored(
    plan: PollingPlan[T, P, C],
    state: object,
    limits: _Limits,
    make: Callable[[OperationSession], OperationT],
) -> OperationT:
    """Return the handle a checkpoint continues in a session of its own, without sending.

    A checkpoint that is not JSON or does not fit the helper raises ConfigurationError, with the reason expired once
    past the server's expiry by the client's wall clock.
    """
    handle = make(new_session(limits))
    restored(plan.helper_id, state, handle._restore, lambda _: handle._expires_at, limits.clock)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return handle


@overload
def start_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    body: object = ...,
    media_type: str | None = ...,
    poll_options: object = ...,
    options: object = ...,
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
    media_type: str | None = ...,
    poll_options: object = ...,
    options: object = ...,
) -> H:
    """Create a helper's operation and return a handle of the helper's own class."""


def start_operation(  # noqa: PLR0913
    core: ClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    handle: type[LroHandle[Any, Any]] = LroHandle,
    body: object = UNSET,
    media_type: str | None = None,
    poll_options: object = None,
    options: object = None,
) -> LroHandle[Any, Any]:
    """Create a helper's operation in a session of its own and return the handle that polls it.

    A helper that declares a remote cancellation passes its own handle class.
    """
    limits = _limits(core, plan, poll_options, options)
    created = handle(core, plan, limits, new_session(limits))
    created._create(arguments, body, media_type)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return created


@overload
async def astart_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    body: object = ...,
    media_type: str | None = ...,
    poll_options: object = ...,
    options: object = ...,
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
    media_type: str | None = ...,
    poll_options: object = ...,
    options: object = ...,
) -> AH:
    """Create a helper's operation with asyncio and return a handle of the helper's own class."""


async def astart_operation(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    arguments: tuple[object, ...],
    *,
    handle: type[AsyncLroHandle[Any, Any]] = AsyncLroHandle,
    body: object = UNSET,
    media_type: str | None = None,
    poll_options: object = None,
    options: object = None,
) -> AsyncLroHandle[Any, Any]:
    """Create a helper's operation with asyncio in a session of its own and return the handle that polls it.

    A helper that declares a remote cancellation passes its own handle class.
    """
    limits = _limits(core, plan, poll_options, options)
    created = handle(core, plan, limits, new_session(limits))
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
) -> LroHandle[Any, Any]:
    """Return a handle continuing a helper's checkpoint in a session of its own, checking the checkpoint now.

    It sends nothing, and never creates the operation again; `status` or `wait` sends its first poll.
    """
    limits = _limits(core, plan, poll_options, options)
    return _restored(plan, state, limits, lambda session: handle(core, plan, limits, session))


@overload
def aresume_operation(  # noqa: D418 - CodeQL flags an ellipsis body.
    core: AsyncClientCore,
    plan: PollingPlan[T, P, C],
    state: object,
    *,
    poll_options: object = ...,
    options: object = ...,
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
) -> AsyncLroHandle[Any, Any]:
    """Return an asyncio handle continuing a helper's checkpoint in a session of its own, checking it now.

    It is not awaited and sends nothing, and never creates the operation again; `status` or `wait` sends its first
    poll.
    """
    limits = _limits(core, plan, poll_options, options)
    return _restored(plan, state, limits, lambda session: handle(core, plan, limits, session))
