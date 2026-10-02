"""Explicit offline queues: enqueue saves a call in a borrowed store, and only an explicit drain sends what it holds.

Each operation of a queue helper is either side-effect free or written with a stable idempotency key. Saved send
intent without a recorded outcome recovers under the same identity on the next explicit drain. Every store and
network step is a value one shared plan yields, run by the synchronous or asyncio driver.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, cast, final
from uuid import uuid4

from typing_extensions import TypeVar

from ..client.errors import (
    ClientClosedError,
    ConfigurationError,
    DeliveryState,
    HTTPStatusError,
    ProtocolConfigurationError,
    ProtocolSizeError,
    RequestCancelledError,
    RequestEncodingError,
    SDKError,
    add_secondary,
)
from ..client.options import IdempotencyKey, RequestOptions
from ..client.retry import retry_after, status_retry_reason
from ..client.timing import Deadline, SessionOptions, absolute_deadline
from ..model_codecs.errors import CodecError
from ..model_codecs.media import decode_json, encode_json
from ..model_codecs.unset import UNSET, Unset
from .errors import ProtocolStateError, QueueBindingError, QueuePolicyConflictError, QueueStoreError
from .options import QueueOptions, layered, resolved_queue
from .queues import TERMINAL_STATES, DrainReport, QueueEntry, QueueLease, QueueOutcome, QueueReceipt
from .records import canonical_json

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator

    from ..client.client import AsyncClientCore, ClientCore
    from ..client.logical import OperationSession
    from ..client.operations import OperationPlan
    from ..client.options import ResolvedRetryOptions
    from ..client.responses import ResponseInfo
    from ..client.timing import Clock
    from ..model_codecs.selectors import MediaSelector
    from ..model_codecs.wire import WireValue
    from .queues import AsyncQueueStore, QueueState, QueueStore, ResolvedQueueOptions
    from .references import OperationRef

R = TypeVar("R")

__all__ = (
    "QueuePlan",
    "QueuedPlan",
    "acancel_entry",
    "adrain_queue",
    "aenqueue_entry",
    "ainspect_entry",
    "apurge_entries",
    "aretry_entry",
    "cancel_entry",
    "drain_queue",
    "enqueue_entry",
    "inspect_entry",
    "purge_entries",
    "retry_entry",
)

_VERSION: Final = 1
_BODY_FIELDS: Final = 3
_ROUNDS: Final = 16
_MAX_DOUBLINGS: Final = 64
_SUCCESS_MIN: Final = 200
_SUCCESS_MAX: Final = 299
_DRAIN_FIELDS: Final = ("max_entries", "parallelism")
_POLICY_FIELDS: Final = (
    "max_entry_body_bytes",
    "max_deliveries",
    "entry_ttl",
    "retry_initial_delay",
    "retry_max_delay",
    "lease_min",
    "lease_grace",
    "max_delivery_timeout",
)
_PERMANENT_STOPS: Final = frozenset({"status_not_retryable", "server_forbids_retry"})
_AUTH_STATUSES: Final = frozenset({401, 403, 407})
_UNSENT: Final = frozenset({DeliveryState.NOT_SENT})
_STOPS: Final = (RequestCancelledError, ClientClosedError)
_TOTAL_TIMEOUT: Final = 300.0
_MAX_NETWORK_SENDS: Final = 1000
_PAYLOAD: Final = frozenset({"version", "arguments", "body", "request", "key", "created_at"})


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class QueuedPlan:
    """One operation a queue helper may enqueue: its alias, reference, plan, contract fingerprint, and key period.

    `dedupe_ttl` is the server's deduplication period of the stable idempotency key the queue sends; None for a
    side-effect-free operation sent without one.
    """

    alias: str
    operation: OperationRef
    call: OperationPlan[object, object]
    fingerprint: str
    dedupe_ttl: float | None = None


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class QueuePlan:
    """Everything fixed about one generated queue helper: its name and the operations it may enqueue."""

    helper_id: str
    operations: tuple[QueuedPlan, ...]
    aliases: Mapping[str, QueuedPlan] = field(init=False)

    def __post_init__(self) -> None:
        """Index the operations by alias."""
        object.__setattr__(self, "aliases", MappingProxyType({queued.alias: queued for queued in self.operations}))


@dataclass(frozen=True, slots=True)
class _Scope:
    """The helper and operation a saved request's errors name."""

    helper_id: str
    operation: OperationRef


@dataclass(frozen=True, slots=True)
class _Store:
    """One store call: the method, its arguments, and the check its result must pass."""

    action: Literal["put", "get", "claim", "compare_exchange", "purge_terminal"]
    entry_id: str | None
    arguments: tuple[object, ...]
    keywords: Mapping[str, object]
    valid: Callable[[object], bool]


@dataclass(frozen=True, slots=True)
class _Send:
    """One delivery: the operation, the request a payload restores, the call's options, and its own deadline."""

    call: OperationPlan[object, object]
    arguments: tuple[object, ...]
    body: object
    media_type: str | MediaSelector | None
    options: RequestOptions | None
    deadline: Deadline
    lease: QueueLease
    identity: WireValue


_Step = _Store | _Send


def _now(clock: Clock) -> datetime:
    """Return the client clock's wall-clock instant in UTC."""
    return datetime.fromtimestamp(clock.time(), timezone.utc)


def _expiry(entry: QueueEntry, queued: QueuedPlan | None) -> datetime:
    """Return when an entry expires: its saved expiry, never later than its key's deduplication period allows.

    The store is trusted with entries, but a keyed entry's key is never sent past what the plan declares the server
    retains.
    """
    if queued is None or queued.dedupe_ttl is None:
        return entry.expires_at
    return min(entry.expires_at, entry.created_at + timedelta(seconds=queued.dedupe_ttl))


def _entry_or_none(entry_id: str) -> Callable[[object], bool]:
    return lambda value: value is None or (isinstance(value, QueueEntry) and value.entry_id == entry_id)


def _is_bool(value: object) -> bool:
    return type(value) is bool


def _items(value: object) -> tuple[object, ...] | None:
    return cast("tuple[object, ...]", value) if isinstance(value, tuple) else None


def _entries(value: object) -> bool:
    return (items := _items(value)) is not None and all(isinstance(item, QueueEntry) for item in items)


def _anything(_: object) -> bool:
    return True


def _put(entry: QueueEntry) -> _Store:
    return _Store("put", entry.entry_id, (entry,), {}, _anything)


def _get(entry_id: str) -> _Store:
    return _Store("get", entry_id, (entry_id,), {}, _entry_or_none(entry_id))


def _exchange(expected: QueueEntry, entry: QueueEntry) -> _Store:
    return _Store("compare_exchange", entry.entry_id, (entry.entry_id, expected.version, entry), {}, _is_bool)


def _invalid(
    plan: QueuePlan, path: tuple[str, ...], operation: OperationRef | None = None
) -> ProtocolConfigurationError:
    return ProtocolConfigurationError(
        field_path=path, condition="invalid_value", helper_id=plan.helper_id, operation=operation
    )


def _store(core: ClientCore | AsyncClientCore, plan: QueuePlan) -> Any:
    """Return the store the client lends the helper, refusing a client without one before anything else."""
    if (store := core.queue_store(plan.helper_id)) is None:
        raise ProtocolConfigurationError(
            field_path=("protocols", "queue_stores", plan.helper_id),
            condition="missing_adapter",
            helper_id=plan.helper_id,
        )
    return store


def _store_error(plan: QueuePlan, step: _Store, cause: Exception | None = None) -> QueueStoreError:
    return QueueStoreError(action=step.action, entry_id=step.entry_id, helper_id=plan.helper_id, cause=cause)


def _checked(plan: QueuePlan, step: _Store, value: object) -> object:
    if not step.valid(value):
        raise _store_error(plan, step)
    return value


def _ran(plan: QueuePlan, step: _Store, run: Callable[[], object]) -> object:
    """Run a store method, raising its failure as a queue store error that keeps the cause."""
    try:
        value = run()
    except QueueStoreError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _store_error(plan, step, error) from None
    return _checked(plan, step, value)


async def _aran(plan: QueuePlan, step: _Store, run: Callable[[], Awaitable[object]]) -> object:
    """Await a store method, raising its failure as a queue store error that keeps the cause."""
    try:
        value = await run()
    except QueueStoreError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _store_error(plan, step, error) from None
    return _checked(plan, step, value)


def _wire_options(plan: QueuePlan, value: object, name: str, kind: type) -> None:
    if value is not None and not isinstance(value, kind):
        raise _invalid(plan, (name,))


def _misplaced(plan: QueuePlan, options: object, names: tuple[str, ...]) -> None:
    """Refuse queue options that set a field the call cannot set."""
    if isinstance(options, QueueOptions) and (
        given := tuple(n for n in names if not isinstance(getattr(options, n), Unset))
    ):
        raise QueuePolicyConflictError(fields=given, helper_id=plan.helper_id)


def _resolved(
    core: ClientCore | AsyncClientCore, plan: QueuePlan, queue_options: object, names: tuple[str, ...]
) -> ResolvedQueueOptions:
    _wire_options(plan, queue_options, "queue_options", QueueOptions)
    _misplaced(plan, queue_options, names)
    defaults = core.protocol_defaults(plan.helper_id)
    return resolved_queue((queue_options, UNSET if defaults is None else defaults.options))


def _fingerprint(facts: WireValue) -> str:
    return sha256(canonical_json(facts)).hexdigest()


def _new_entry(  # noqa: PLR0913, PLR0917
    core: ClientCore | AsyncClientCore,
    plan: QueuePlan,
    index: int,
    arguments: tuple[object, ...],
    body: object,
    media_type: str | MediaSelector | None,
    queue_options: object,
) -> QueueEntry:
    """Return a new pending entry saving a call, encoded and checked as the call would be, without credentials.

    An operation that may authenticate needs the client's security context, which binds the entry to it.
    """
    queued = plan.operations[index]
    policy = _resolved(core, plan, queue_options, _DRAIN_FIELDS)
    facts, exportable = core.checkpoint_security(queued.call, None)
    if not exportable:
        raise ProtocolConfigurationError(
            field_path=("protocols", "security"),
            condition="security_partition",
            helper_id=plan.helper_id,
            operation=queued.operation,
        )
    scope = _Scope(plan.helper_id, queued.operation)
    saved, saved_body = core.saved_request(scope, queued.call, arguments, body, media_type, None)
    now = _now(core.clock)
    key = None if queued.dedupe_ttl is None else str(uuid4())
    identity = core.saved_queue_request(queued.call, arguments, body, media_type, None)
    payload = encode_json({
        "version": _VERSION,
        "request": identity,
        "key": key,
        "created_at": now.isoformat(),
        "arguments": tuple(() if isinstance(value, Unset) else (value,) for value in saved),
        "body": () if saved_body is None else saved_body,
    })
    if (size := len(payload)) > (limit := policy.max_entry_body_bytes):
        raise ProtocolSizeError(
            kind="body", limit=limit, observed=size, unit="bytes", helper_id=plan.helper_id, operation=queued.operation
        )
    ttl = policy.entry_ttl if queued.dedupe_ttl is None else min(policy.entry_ttl, queued.dedupe_ttl)
    return QueueEntry(
        entry_id=str(uuid4()),
        version=uuid4().hex,
        operation_alias=queued.alias,
        helper_fingerprint=queued.fingerprint,
        security_fingerprint=_fingerprint(facts),
        payload=payload,
        blob=None,
        blob_owned=False,
        idempotency_key=key,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
        not_before=now,
        saved_wait_seconds=0.0,
        state="pending",
        delivery_count=0,
        send_intent=False,
        cancel_requested=False,
        lease_id=None,
        lease_until=None,
        policy=policy,
        result=None,
    )


def _receipt(entry: QueueEntry) -> QueueReceipt:
    return QueueReceipt(
        entry_id=entry.entry_id, state=entry.state, created_at=entry.created_at, expires_at=entry.expires_at
    )


class _MalformedError(Exception):
    """A payload that does not restore the request of the operation it names."""


class _InterruptedError(Exception):
    """Raised into a plan whose delivery a native cancellation or interrupt stopped, after its intent was saved."""


def _require(condition: bool) -> None:  # noqa: FBT001
    if not condition:
        raise _MalformedError


def _restored(
    core: ClientCore | AsyncClientCore, call: OperationPlan[object, object], entry: QueueEntry
) -> tuple[tuple[object, ...], object, str | MediaSelector | None, WireValue]:
    """Return the arguments, body, and media type a payload saved, built and checked as a caller's are.

    A payload of another shape or version, an argument a payload never saves, a missing required argument or body, and
    a value its codec refuses are malformed.
    """
    try:
        saved = decode_json(entry.payload)
    except CodecError:
        raise _MalformedError from None
    _require(
        isinstance(saved, Mapping)
        and frozenset(saved) == _PAYLOAD
        and type(saved["version"]) is int
        and saved["version"] == _VERSION
    )
    fields = cast("Mapping[str, WireValue]", saved)
    _require(fields["key"] == entry.idempotency_key and fields["created_at"] == entry.created_at.isoformat())
    _require(isinstance(fields["request"], Mapping))
    arguments, body = fields["arguments"], fields["body"]
    _require(isinstance(arguments, tuple) and len(arguments) == len(call.parameters) and isinstance(body, tuple))
    items = cast("tuple[WireValue, ...]", arguments)
    kept = [item for item in items if isinstance(item, tuple) and len(item) <= 1]
    _require(len(kept) == len(items))
    wire = tuple(item[0] if item else UNSET for item in kept)
    sent = cast("tuple[WireValue, ...]", body)
    given: tuple[WireValue, str, str | None] | None = None
    if sent:
        declared, concrete = sent[1:] if len(sent) == _BODY_FIELDS else (None, None)
        _require(isinstance(declared, str) and (concrete is None or isinstance(concrete, str)))
        given = sent[0], cast("str", declared), cast("str | None", concrete)
    _require(core.unsaved_argument(call, wire) is None)
    _require(
        not any(
            spec.plan.required and isinstance(value, Unset) for spec, value in zip(call.parameters, wire, strict=True)
        )
        and (given is not None or (request := call.body) is None or not request.required)
    )
    try:
        return (*core.restored_request(call, wire, given), fields["request"])
    except (SDKError, CodecError):
        raise _MalformedError from None


@dataclass(frozen=True, slots=True)
class _Ended:
    """How one delivery ends: the entry's next state and outcome, its report, and any error raised once it is saved.

    A delivery that sent nothing is reverted: its delivery count and earlier send intent come back. A rescheduled one
    waits until `not_before`.
    """

    state: QueueState
    report: str
    outcome: QueueOutcome | None = None
    response: ResponseInfo | None = None
    reverted: bool = False
    not_before: datetime | None = None
    wait: float = 0.0
    failure: BaseException | None = None


def _dead(code: str, response: ResponseInfo | None = None, *, prior: bool = False) -> _Ended:
    return _Ended(
        "dead", "dead", QueueOutcome(category="unknown" if prior else "permanent", error_code=code), response=response
    )


def _cancelled(intent: bool, response: ResponseInfo | None = None) -> _Ended:  # noqa: FBT001
    """End a cancelled entry: cancelled when nothing may have been sent, else of unknown delivery."""
    state: QueueState = "delivery_unknown" if intent else "cancelled"
    return _Ended(state, "unknown" if intent else "cancelled", QueueOutcome(category="cancelled"), response=response)


def _released(entry: QueueEntry) -> QueueEntry:
    return replace(entry, state="pending", lease_id=None, lease_until=None)


def _saved_outcome(  # noqa: PLR0913, PLR0917
    core: ClientCore | AsyncClientCore,
    plan: QueuePlan,
    entry: QueueEntry,
    outcome: QueueOutcome | None,
    response: ResponseInfo | None,
    options: RequestOptions | None,
) -> QueueOutcome | None:
    """Save metadata only after stripping credential positions, including a reused store result."""
    if outcome is None or response is None:
        return outcome
    queued = plan.aliases.get(entry.operation_alias)
    return replace(outcome, response=core.saved_response(response, None if queued is None else queued.call, options))


def _applied(  # noqa: PLR0913
    core: ClientCore | AsyncClientCore,
    plan: QueuePlan,
    current: QueueEntry,
    ended: _Ended,
    *,
    prior: bool,
    options: RequestOptions | None = None,
) -> tuple[QueueEntry, _Ended]:
    """Return the entry an ending writes over the current one, and the ending a cancel requested meanwhile gives.

    A cancel requested while the delivery ran turns an entry that would wait for another delivery into a cancelled
    one, or into one of unknown delivery when a request may have reached the server.
    """
    if current.cancel_requested and ended.state == "pending":
        ended = replace(
            _cancelled(not ended.reverted or prior, ended.response), reverted=ended.reverted, failure=ended.failure
        )
    outcome = current.result if ended.outcome is None else ended.outcome
    response = (None if outcome is None else outcome.response) if ended.outcome is None else ended.response
    outcome = _saved_outcome(core, plan, current, outcome, response, options)
    count = current.delivery_count - ended.reverted
    entry = replace(
        current,
        state=ended.state,
        delivery_count=count,
        send_intent=prior if ended.state == "pending" else False,
        lease_id=None,
        lease_until=None,
        result=outcome,
    )
    if ended.not_before is not None:
        entry = replace(entry, not_before=ended.not_before, saved_wait_seconds=ended.wait)
    return entry, ended


def _ours(entry: QueueEntry | None, lease: QueueLease) -> bool:
    return entry is not None and entry.state == "leased" and entry.lease_id == lease.lease_id


def _write(  # noqa: PLR0913
    core: ClientCore | AsyncClientCore,
    plan: QueuePlan,
    expected: QueueEntry,
    entry: QueueEntry,
    *,
    options: RequestOptions | None = None,
    reload: bool = False,
) -> Generator[_Step, object, tuple[bool, QueueEntry | None]]:
    """Write an entry over the version expected, then read it back when asked or when another write came first."""
    outcome = entry.result
    if outcome is expected.result and outcome is not None and outcome.response is not None:
        entry = replace(entry, result=_saved_outcome(core, plan, entry, outcome, outcome.response, options))
    if (written := (yield _exchange(expected, entry))) and not reload:
        return True, entry
    current = cast("QueueEntry | None", (yield _get(entry.entry_id)))
    return bool(written), current  # noqa: B901 - Its runner receives the outcome.


def _retried(  # noqa: PLR0913
    entry: QueueEntry,
    queued: QueuedPlan,
    info: ResponseInfo | None,
    code: str,
    *,
    now: datetime,
    clock: Clock,
) -> _Ended:
    """End a delivery that may be sent again: after a full-jitter backoff, never before the server's Retry-After.

    One past its delivery limit, or one whose next attempt would come at or after its expiry, is dead instead.
    """
    policy = entry.policy
    if entry.delivery_count >= policy.max_deliveries:
        return _dead("max_deliveries", info)
    cap = min(policy.retry_max_delay, policy.retry_initial_delay * 2.0 ** min(entry.delivery_count - 1, _MAX_DOUBLINGS))
    wait = cap * clock.random()
    if (
        info is not None
        and (
            server := retry_after(
                info.headers,
                milliseconds_header=queued.call.retry_after_ms_header,
                received_at=0.0,
                received_wall_time=clock.time(),
            )
        )
        is not None
    ):
        wait = max(wait, server.seconds)
    if wait >= (_expiry(entry, queued) - now).total_seconds():
        return _dead("expired", info)
    at = now + timedelta(seconds=wait)
    outcome = QueueOutcome(category="retryable", retry_at=at, error_code=code)
    return _Ended("pending", "rescheduled", outcome, response=info, not_before=at, wait=wait)


def _retryable(error: SDKError, status: int, retry: ResolvedRetryOptions) -> bool:
    """Return whether a response status would be sent again, as the shared retry policy decides for an HTTP error.

    An HTTP error the policy stopped for its status, or that the server forbids retrying, is not; an authentication
    status is, since current credentials may be accepted later. Any other response is judged by its status alone.
    """
    if isinstance(error, HTTPStatusError):
        return error.retry_stop_reason not in _PERMANENT_STOPS
    return status in _AUTH_STATUSES or status_retry_reason(status, retry, hint=None) is not None


def _ended(  # noqa: PLR0913, PLR0917
    error: SDKError, entry: QueueEntry, queued: QueuedPlan, now: datetime, retry: ResolvedRetryOptions, clock: Clock
) -> _Ended:
    """Classify a failed delivery by whether any request was sent and how it ended.

    A response with a success status succeeded, whatever failed after it. Nothing sent defers the entry, except a
    configuration or encoding error. A status the shared retry policy retries, or a request proven unsent, waits for
    another delivery; an HTTP error the policy does not retry is dead; any other failed response, such as one whose
    body failed, and a request that may have arrived are of unknown delivery. A cancellation or a closed client is
    raised once the entry is saved.
    """
    info, code = error.info, error.reason_code
    stop = error if isinstance(error, _STOPS) else None
    if info is not None and _SUCCESS_MIN <= info.status_code <= _SUCCESS_MAX:
        return _Ended("succeeded", "succeeded", QueueOutcome(category="success"), response=info, failure=stop)
    if not error.resource_attempt_count and not error.redirect_count:
        if isinstance(error, RequestEncodingError):
            return _dead(code)
        return _Ended(
            "pending", "deferred", reverted=True, failure=error if isinstance(error, ConfigurationError) else stop
        )
    if (info is not None and _retryable(error, info.status_code, retry)) or (
        info is None and getattr(error, "delivery_state", None) in _UNSENT
    ):
        return replace(_retried(entry, queued, info, code, now=now, clock=clock), failure=stop)
    if isinstance(error, HTTPStatusError):
        return replace(_dead(code, info), failure=stop)
    outcome = QueueOutcome(category="unknown", error_code=code)
    return _Ended("delivery_unknown", "unknown", outcome, response=info, failure=stop)


@dataclass(frozen=True, slots=True)
class _Limits:
    """The effective settings of one drain: its options, session limits, and call options."""

    policy: ResolvedQueueOptions
    total_timeout: float | None
    deadline: Deadline | None
    max_network_sends: int | None
    options: RequestOptions | None


def _limits(
    core: ClientCore | AsyncClientCore, plan: QueuePlan, queue_options: object, options: object, session_options: object
) -> _Limits:
    """Check a drain's option types and merge its limits, refusing entry policy fields and a fixed idempotency key."""
    _wire_options(plan, options, "options", RequestOptions)
    _wire_options(plan, session_options, "session_options", SessionOptions)
    policy = _resolved(core, plan, queue_options, _POLICY_FIELDS)
    request = options if isinstance(options, RequestOptions) else None
    if core.fixes_key(request):
        raise _invalid(plan, ("options", "idempotency_key"))
    defaults = core.protocol_defaults(plan.helper_id)
    sessions = (session_options, UNSET if defaults is None else defaults.session)
    return _Limits(
        policy,
        layered(sessions, "total_timeout", _TOTAL_TIMEOUT),
        layered(sessions, "deadline", None),
        layered(sessions, "max_network_sends", _MAX_NETWORK_SENDS),
        request,
    )


class _Drain:
    """One drain: its session, the entries it claimed and holds, and what became of each."""

    __slots__ = (
        "claimed",
        "clock",
        "coding",
        "core",
        "held",
        "limits",
        "plan",
        "reported",
        "retry",
        "security",
        "seen",
        "session",
    )

    def __init__(self, core: ClientCore | AsyncClientCore, plan: QueuePlan, limits: _Limits) -> None:
        """Start the drain's session and bind each operation to the security the drain sends under."""
        from ..client.logical import OperationSession  # noqa: PLC0415 - Only a drain loads the call runtime.

        self.core, self.plan, self.limits, self.clock = core, plan, limits, core.clock
        options = limits.options
        self.coding = options.compression if options is not None and isinstance(options.compression, str) else None
        self.session: OperationSession = OperationSession(
            total_timeout=limits.total_timeout,
            deadline=limits.deadline,
            max_network_sends=limits.max_network_sends,
            clock=core.clock,
        )
        self.security = {
            queued.alias: _fingerprint(core.checkpoint_security(queued.call, limits.options)[0])
            for queued in plan.operations
        }
        self.retry = {queued.alias: core.call_settings(limits.options, queued.call).retry for queued in plan.operations}
        self.seen: set[str] = set()
        self.held: list[QueueLease] = []
        self.claimed = 0
        self.reported: dict[str, list[str]] = {name: [] for name in DrainReport.__dataclass_fields__}

    def window(self, policy: ResolvedQueueOptions) -> float:
        """Return how long a delivery may take: its policy's timeout, within what remains of the session."""
        deadline = self.session.deadline
        return (
            policy.max_delivery_timeout if deadline is None else min(policy.max_delivery_timeout, deadline.remaining())
        )

    def left(self) -> int:
        """Return how many more entries the drain may claim: none once its session has no time or sends left."""
        session = self.session
        if ((deadline := session.deadline) is not None and deadline.remaining() <= 0) or not session.room():
            return 0
        return self.limits.policy.max_entries - self.claimed

    def claim(self, left: int) -> _Store:
        """Return the claim of the next wave, leased for a whole delivery and its grace."""
        policy = self.limits.policy
        limit = left if self.coding is not None else min(policy.parallelism, left)
        now = _now(self.clock)
        until = now + timedelta(seconds=max(policy.lease_min, self.window(policy) + policy.lease_grace))

        def valid(value: object) -> bool:
            items = _items(value)
            return items is not None and len(items) <= limit and all(isinstance(item, QueueLease) for item in items)

        return _Store("claim", None, (), {"now": now, "lease_until": until, "limit": limit}, valid)

    def admit(self, leases: list[QueueLease]) -> None:
        """Inspect the fixed claim's saved requests without reading bodies, returning all leases if admission fails."""
        from ..client.compression import helper_children  # noqa: PLC0415 - Only an explicit coding loads admission.

        self.held.extend(leases)
        children: list[tuple[OperationPlan[object, object], bool]] = []
        for lease in leases:
            entry = lease.entry
            queued = self.plan.aliases.get(entry.operation_alias)
            if (
                queued is None
                or entry.helper_fingerprint != queued.fingerprint
                or entry.security_fingerprint != self.security[queued.alias]
            ):
                raise QueueBindingError(
                    entry_id=entry.entry_id,
                    helper_id=self.plan.helper_id,
                    operation=None if queued is None else queued.operation,
                )
            try:
                payload = decode_json(entry.payload)
                _require(
                    isinstance(payload, Mapping) and frozenset(payload) == _PAYLOAD and payload["version"] == _VERSION
                )
                body = cast("Mapping[str, WireValue]", payload)["body"]
                _require(isinstance(body, tuple) and len(body) in {0, _BODY_FIELDS})
            except (CodecError, _MalformedError) as error:
                raise QueueStoreError(
                    action="claim", entry_id=entry.entry_id, helper_id=self.plan.helper_id, cause=error
                ) from None
            if (
                token := self.core.call_settings(self.limits.options, queued.call).cancel_token
            ) is not None and token.cancelled:
                raise RequestCancelledError(source="cancel_token", delivery_state=DeliveryState.NOT_SENT)
            if not entry.cancel_requested and _now(self.clock) < _expiry(entry, queued):
                children.append((queued.call, bool(body) or entry.blob is not None))
        helper_children(cast("str", self.coding), children)

    def accept(self, leases: tuple[QueueLease, ...]) -> list[QueueLease]:
        """Return the leases of entries this drain has not handled yet, holding the others until it ends."""
        fresh: list[QueueLease] = []
        for lease in leases:
            if (entry_id := lease.entry.entry_id) in self.seen:
                self.held.append(lease)
            else:
                self.seen.add(entry_id)
                fresh.append(lease)
        self.claimed += len(fresh)
        return fresh

    def record(self, lease: QueueLease, report: object) -> None:
        """Report how an entry ended; an entry whose lease another worker took is not reported."""
        if isinstance(report, str):
            self.reported[report].append(lease.entry.entry_id)

    def release(self) -> Generator[_Step, object, None]:
        """Return every held entry to pending, or end it cancelled when a cancel was requested meanwhile.

        A write another one beat is evaluated again against the entry it left, as long as the lease is this drain's.
        """
        for lease in self.held:
            current: QueueEntry | None = lease.entry
            for _ in range(2 if self.coding is not None else _ROUNDS):
                if current is None or not _ours(current, lease):
                    break
                if current.cancel_requested:
                    entry = _applied(self.core, self.plan, current, _cancelled(current.send_intent), prior=False)[0]
                else:
                    entry = _released(current)
                written, current = yield from _write(self.core, self.plan, current, entry, options=self.limits.options)
                if written:
                    break
            else:
                if self.coding is not None and _ours(current, lease):
                    raise QueueStoreError(
                        action="compare_exchange", entry_id=lease.entry.entry_id, helper_id=self.plan.helper_id
                    )

    def report(self) -> DrainReport:
        """Return the drain's report."""
        return DrainReport(**{name: tuple(ids) for name, ids in self.reported.items()})

    def options(self, queued: QueuedPlan, entry: QueueEntry) -> RequestOptions | None:
        """Return the call options of a delivery: the drain's, sending a keyed entry's stable idempotency key."""
        base = self.limits.options
        if queued.dedupe_ttl is None:
            if entry.idempotency_key is not None:
                raise _MalformedError
            return base
        if (value := entry.idempotency_key) is None:
            raise _MalformedError
        try:
            key = IdempotencyKey(value, first_used_at=entry.created_at)
        except ConfigurationError:
            raise _MalformedError from None
        return RequestOptions(idempotency_key=key) if base is None else replace(base, idempotency_key=key)

    def settle(
        self,
        lease: QueueLease,
        current: QueueEntry | None,
        ended: _Ended,
        *,
        prior: bool,
    ) -> Generator[_Step, object, str | None]:
        """Save how a delivery ended over the entry's latest version, then raise any error it keeps.

        A write another one beat is evaluated again against the entry it left, as long as the lease is this drain's.
        """
        for _ in range(_ROUNDS):
            if current is None or not _ours(current, lease):
                break
            entry, final = _applied(self.core, self.plan, current, ended, prior=prior, options=self.limits.options)
            written, current = yield from _write(self.core, self.plan, current, entry, options=self.limits.options)
            if written:
                if final.failure is not None:
                    raise final.failure
                return final.report
        else:
            raise QueueStoreError(
                action="compare_exchange", entry_id=lease.entry.entry_id, helper_id=self.plan.helper_id
            )
        if ended.failure is not None:
            raise ended.failure
        return None  # noqa: B901 - Its runner receives the outcome.

    def deliver(self, lease: QueueLease) -> Generator[_Step, object, str | None]:  # noqa: PLR0911, PLR0912, PLR0914, PLR0915
        """Deliver one claimed entry and save how it ended, returning its report.

        An entry of another contract or security is returned to pending and raises QueueBindingError. A cancelled,
        expired, exhausted, or malformed entry ends without a send. Otherwise the send intent and delivery count are
        saved first, so nothing is sent unless they are, and the one call's outcome is saved after it.
        """
        plan, entry = self.plan, lease.entry
        queued = plan.aliases.get(entry.operation_alias)
        if (
            queued is None
            or entry.helper_fingerprint != queued.fingerprint
            or entry.security_fingerprint != self.security[queued.alias]
        ):
            yield from _write(self.core, self.plan, entry, _released(entry), options=self.limits.options)
            raise QueueBindingError(
                entry_id=entry.entry_id,
                helper_id=plan.helper_id,
                operation=None if queued is None else queued.operation,
            )
        prior, now, clock = entry.send_intent, _now(self.clock), self.clock
        expires = min(_expiry(entry, queued), entry.created_at + timedelta(seconds=entry.policy.entry_ttl))
        if entry.cancel_requested:
            return (yield from self.settle(lease, entry, _cancelled(prior), prior=prior))
        if (
            now < entry.created_at
            or expires <= entry.created_at
            or entry.not_before - timedelta(seconds=entry.saved_wait_seconds) < entry.created_at
        ):
            return (yield from self.settle(lease, entry, _dead("malformed_entry", prior=prior), prior=prior))
        if now >= expires:
            return (yield from self.settle(lease, entry, _dead("expired", prior=prior), prior=prior))
        if entry.delivery_count >= entry.policy.max_deliveries:
            return (yield from self.settle(lease, entry, _dead("max_deliveries", prior=prior), prior=prior))
        if len(entry.payload) > entry.policy.max_entry_body_bytes or entry.blob is not None or entry.blob_owned:
            return (yield from self.settle(lease, entry, _dead("malformed_entry", prior=prior), prior=prior))
        saved_at = entry.not_before - timedelta(seconds=entry.saved_wait_seconds)
        if now < entry.not_before:
            wait = entry.saved_wait_seconds if now < saved_at else (entry.not_before - now).total_seconds()
            at = now + timedelta(seconds=wait)
            result = entry.result
            outcome = QueueOutcome(
                category="retryable",
                retry_at=at,
                error_code=None if result is None else result.error_code,
            )
            ended = _Ended(
                "pending",
                "rescheduled",
                outcome,
                response=None if result is None else result.response,
                not_before=at,
                wait=wait,
            )
            return (yield from self.settle(lease, entry, ended, prior=prior))
        policy = entry.policy
        started = clock.monotonic()
        expiry = started + (expires - now).total_seconds()
        deadline = absolute_deadline(min(started + self.window(policy), expiry), clock=clock)
        try:
            arguments, body, media_type, identity = _restored(self.core, queued.call, entry)
            options = self.options(queued, entry)
            actual = self.core.saved_queue_request(queued.call, arguments, body, media_type, options)
        except (_MalformedError, SDKError, CodecError):
            return (yield from self.settle(lease, entry, _dead("malformed_entry", prior=prior), prior=prior))
        if actual != identity:
            yield from _write(self.core, self.plan, entry, _released(entry), options=self.limits.options)
            raise QueueBindingError(entry_id=entry.entry_id, helper_id=plan.helper_id, operation=queued.operation)
        if deadline.remaining() <= 0:
            return (yield from self.settle(lease, entry, _dead("expired", prior=prior), prior=prior))
        now = _now(clock)
        until = now + timedelta(seconds=max(policy.lease_min, deadline.remaining() + policy.lease_grace))
        current: QueueEntry | None = entry
        for _ in range(_ROUNDS):
            if current is None or not _ours(current, lease):
                return None
            if self.coding is not None and current.lease_until is not None and current.lease_until <= _now(clock):
                return "deferred"
            if current.cancel_requested:
                return (yield from self.settle(lease, current, _cancelled(prior), prior=prior))
            intended = replace(
                current, send_intent=True, delivery_count=current.delivery_count + 1, lease_until=until, result=None
            )
            written, current = yield from _write(
                self.core, self.plan, current, intended, options=self.limits.options, reload=True
            )
            if written:
                break
        else:
            raise QueueStoreError(action="compare_exchange", entry_id=entry.entry_id, helper_id=plan.helper_id)
        if current is None or not _ours(current, lease):
            return None
        if current.cancel_requested:
            return (yield from self.settle(lease, current, replace(_cancelled(prior), reverted=True), prior=prior))
        if deadline.remaining() <= 0:
            return (
                yield from self.settle(
                    lease, current, replace(_dead("expired", prior=prior), reverted=True), prior=prior
                )
            )
        try:
            info = cast(
                "ResponseInfo",
                (yield _Send(queued.call, arguments, body, media_type, options, deadline, lease, identity)),
            )
        except SDKError as error:
            ended = _ended(error, current, queued, _now(clock), self.retry[queued.alias], clock)
            if not error.resource_attempt_count and not error.redirect_count and clock.monotonic() >= expiry:
                ended = replace(_dead("expired", prior=prior), reverted=True, failure=ended.failure)
            if prior and ended.state == "dead" and ended.outcome is not None:
                ended = replace(ended, outcome=replace(ended.outcome, category="unknown"))
        except _InterruptedError:
            ended = _cancelled(intent=True)
        else:
            ended = _Ended("succeeded", "succeeded", QueueOutcome(category="success"), response=info)
        return (yield from self.settle(lease, current, ended, prior=prior))  # noqa: B901 - Its runner receives the outcome.


def _entry_id(plan: QueuePlan, value: object) -> str:
    if not isinstance(value, str):
        raise _invalid(plan, ("entry_id",))
    return value


def _instant(plan: QueuePlan, value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise _invalid(plan, ("before",))
    return value


def _purge(before: datetime) -> _Store:
    return _Store("purge_terminal", None, (before,), {}, _entries)


def _cancel(
    core: ClientCore | AsyncClientCore, plan: QueuePlan, entry_id: str
) -> Generator[_Step, object, QueueEntry | None]:
    """Cancel an entry: a pending one ends at once, a leased one is asked to end at its drain's next boundary.

    A pending entry a request may have reached ends as of unknown delivery instead; a cancelled one stays as it is.
    """
    current = cast("QueueEntry | None", (yield _get(entry_id)))
    for _ in range(_ROUNDS):
        if current is None or current.state == "cancelled" or (current.state == "leased" and current.cancel_requested):
            return current
        if current.state in TERMINAL_STATES:
            raise ProtocolStateError(state=current.state, action="cancel", helper_id=plan.helper_id)
        if current.state == "leased":
            entry = replace(current, cancel_requested=True)
        else:
            entry = _applied(core, plan, current, _cancelled(current.send_intent), prior=False)[0]
        written, current = yield from _write(core, plan, current, entry, reload=True)
        if written:
            return current  # noqa: B901 - Its runner receives the outcome.
    raise QueueStoreError(action="compare_exchange", entry_id=entry_id, helper_id=plan.helper_id)


def _retry(
    core: ClientCore | AsyncClientCore, plan: QueuePlan, entry_id: str, clock: Clock
) -> Generator[_Step, object, QueueEntry | None]:
    """Return an entry of unknown delivery to pending with its key, or end it dead when expired or out of deliveries."""
    current = cast("QueueEntry | None", (yield _get(entry_id)))
    for _ in range(_ROUNDS):
        if current is None:
            return None
        if current.state != "delivery_unknown":
            raise ProtocolStateError(state=current.state, action="retry_unknown", helper_id=plan.helper_id)
        now = _now(clock)
        if now < current.created_at:
            entry = _applied(core, plan, current, _dead("malformed_entry", prior=True), prior=False)[0]
        elif now >= _expiry(current, plan.aliases.get(current.operation_alias)):
            entry = _applied(core, plan, current, _dead("expired", prior=True), prior=False)[0]
        elif current.delivery_count >= current.policy.max_deliveries:
            entry = _applied(core, plan, current, _dead("max_deliveries", prior=True), prior=False)[0]
        else:
            entry = replace(
                current,
                state="pending",
                not_before=now,
                saved_wait_seconds=0.0,
                cancel_requested=False,
                send_intent=True,
            )
        written, current = yield from _write(core, plan, current, entry, reload=True)
        if written:
            return current  # noqa: B901 - Its runner receives the outcome.
    raise QueueStoreError(action="compare_exchange", entry_id=entry_id, helper_id=plan.helper_id)


def _call(plan: QueuePlan, store: QueueStore, step: _Store) -> object:
    method = getattr(store, step.action)
    return _ran(plan, step, lambda: method(*step.arguments, **step.keywords))


async def _acall(plan: QueuePlan, store: AsyncQueueStore, step: _Store) -> object:
    method = getattr(store, step.action)
    return await _aran(plan, step, lambda: method(*step.arguments, **step.keywords))


def _interrupted(
    plan: QueuePlan, store: QueueStore, steps: Generator[_Step, object, object], error: BaseException
) -> None:
    """Let a plan whose delivery was interrupted save it as of unknown delivery, keeping failures beside the error."""
    try:
        step = steps.throw(_InterruptedError())
        while True:
            step = steps.send(_call(plan, store, cast("_Store", step)))
    except StopIteration:
        return
    except Exception as failure:  # noqa: BLE001
        add_secondary(error, failure)


async def _ainterrupted(
    plan: QueuePlan, store: AsyncQueueStore, steps: Generator[_Step, object, object], error: BaseException
) -> None:
    """Let a plan whose delivery was interrupted save it, as `_interrupted` does, awaiting the asynchronous store."""
    try:
        step = steps.throw(_InterruptedError())
        while True:
            step = steps.send(await _acall(plan, store, cast("_Store", step)))
    except StopIteration:
        return
    except Exception as failure:  # noqa: BLE001
        add_secondary(error, failure)


def _admission(step: _Send, current: QueueEntry | None, clock: Clock) -> None:
    """Refuse a lost, expired, or cancelled lease before child provider or resource admission."""
    if (
        not _ours(current, step.lease)
        or current is None
        or current.lease_until is None
        or current.lease_until <= _now(clock)
    ):
        raise ProtocolStateError(state="lease_lost", action="drain")
    if current.cancel_requested:
        raise ProtocolStateError(state="cancel_requested", action="drain")
    saved = step.lease.entry
    if any(
        getattr(current, name) != getattr(saved, name)
        for name in (
            "operation_alias",
            "helper_fingerprint",
            "security_fingerprint",
            "payload",
            "idempotency_key",
            "created_at",
            "expires_at",
            "policy",
            "blob",
            "blob_owned",
        )
    ):
        raise QueueBindingError(entry_id=current.entry_id)


def _driven(
    plan: QueuePlan,
    core: ClientCore,
    store: QueueStore,
    steps: Generator[_Step, object, R],
    session: OperationSession | None = None,
) -> R:
    """Run a plan's steps: each store call through the store, each delivery as a child call of the session.

    A step's SDK failure is raised into the plan, which handles a delivery's and lets every other one propagate. A
    native cancellation or interrupt of a delivery lets the plan save it as of unknown delivery, then propagates.
    """
    reply: object = None
    failure: SDKError | None = None
    while True:
        try:
            step = steps.send(reply) if failure is None else steps.throw(failure)
        except StopIteration as stop:
            return cast("R", stop.value)
        reply, failure = None, None
        try:
            if isinstance(step, _Send):

                def admission(step: _Send = step) -> None:
                    _admission(
                        step, cast("QueueEntry | None", _call(plan, store, _get(step.lease.entry.entry_id))), core.clock
                    )

                reply = core.execute(
                    step.call,
                    step.arguments,
                    body=step.body,
                    media_type=step.media_type,
                    options=step.options,
                    session=session,
                    deadline=step.deadline,
                    replay_identity=step.identity,
                    admission=admission,
                ).info
            else:
                reply = _call(plan, store, step)
        except SDKError as error:
            failure = error
        except BaseException as error:
            if isinstance(step, _Send):
                _interrupted(plan, store, steps, error)
            raise


async def _adriven(
    plan: QueuePlan,
    core: AsyncClientCore,
    store: AsyncQueueStore,
    steps: Generator[_Step, object, R],
    session: OperationSession | None = None,
) -> R:
    """Run a plan's steps as `_driven` does, awaiting the asynchronous store and the asyncio calls."""
    reply: object = None
    failure: SDKError | None = None
    while True:
        try:
            step = steps.send(reply) if failure is None else steps.throw(failure)
        except StopIteration as stop:
            return cast("R", stop.value)
        reply, failure = None, None
        try:
            if isinstance(step, _Send):

                async def admission(step: _Send = step) -> None:
                    _admission(
                        step,
                        cast("QueueEntry | None", await _acall(plan, store, _get(step.lease.entry.entry_id))),
                        core.clock,
                    )

                reply = (
                    await core.execute(
                        step.call,
                        step.arguments,
                        body=step.body,
                        media_type=step.media_type,
                        options=step.options,
                        session=session,
                        deadline=step.deadline,
                        replay_identity=step.identity,
                        admission=admission,
                    )
                ).info
            else:
                reply = await _acall(plan, store, step)
        except SDKError as error:
            failure = error
        except BaseException as error:
            if isinstance(step, _Send):
                await _ainterrupted(plan, store, steps, error)
            raise


def _failed(drain: _Drain, leases: list[QueueLease], results: list[object]) -> None:
    """Report each delivery of a wave in claim order, then raise the first failure, keeping the others beside it."""
    failures = [result for result in results if isinstance(result, BaseException)]
    for lease, result in zip(leases, results, strict=True):
        if not isinstance(result, BaseException):
            drain.record(lease, result)
    if failures:
        add_secondary(failures[0], *failures[1:])
        raise failures[0]


def _wave(drain: _Drain, store: QueueStore, leases: list[QueueLease]) -> None:
    """Deliver a wave of entries, several at once on private worker threads when the drain is parallel."""
    plan, core, session = drain.plan, cast("ClientCore", drain.core), drain.session
    if len(leases) == 1:
        drain.record(leases[0], _driven(plan, core, store, drain.deliver(leases[0]), session))
        return
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415 - Only a parallel drain starts threads.

    with ThreadPoolExecutor(max_workers=len(leases)) as pool:
        futures = [pool.submit(_driven, plan, core, store, drain.deliver(lease), session) for lease in leases]
    _failed(drain, leases, [future.exception() or future.result() for future in futures])


async def _awave(drain: _Drain, store: AsyncQueueStore, leases: list[QueueLease]) -> None:
    """Deliver a wave of entries, several at once in tasks of their own when the drain is parallel."""
    plan, core, session = drain.plan, cast("AsyncClientCore", drain.core), drain.session
    if len(leases) == 1:
        drain.record(leases[0], await _adriven(plan, core, store, drain.deliver(leases[0]), session))
        return
    results = await asyncio.gather(
        *(_adriven(plan, core, store, drain.deliver(lease), session) for lease in leases), return_exceptions=True
    )
    _failed(drain, leases, list(results))


def enqueue_entry(  # noqa: PLR0913
    core: ClientCore,
    plan: QueuePlan,
    index: int,
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    queue_options: object = None,
) -> QueueReceipt:
    """Save a call of one queued operation as a new pending entry, sending nothing."""
    store: QueueStore = _store(core, plan)
    entry = _new_entry(core, plan, index, arguments, body, media_type, queue_options)
    _call(plan, store, _put(entry))
    return _receipt(entry)


async def aenqueue_entry(  # noqa: PLR0913
    core: AsyncClientCore,
    plan: QueuePlan,
    index: int,
    arguments: tuple[object, ...],
    *,
    body: object = UNSET,
    media_type: str | MediaSelector | None = None,
    queue_options: object = None,
) -> QueueReceipt:
    """Save a call as `enqueue_entry` does, awaiting the asynchronous store."""
    store: AsyncQueueStore = _store(core, plan)
    entry = _new_entry(core, plan, index, arguments, body, media_type, queue_options)
    await _acall(plan, store, _put(entry))
    return _receipt(entry)


def drain_queue(
    core: ClientCore,
    plan: QueuePlan,
    *,
    queue_options: object = None,
    options: object = None,
    session_options: object = None,
) -> DrainReport:
    """Deliver ready entries in creation-time and ID order, in waves of the drain's parallelism, and report each.

    The drain ends once nothing is ready, its entry limit is reached, or its session has no time or sends left;
    entries it claimed again are held until it ends, so each is delivered at most once per drain.
    """
    store: QueueStore = _store(core, plan)
    drain = _Drain(core, plan, _limits(core, plan, queue_options, options, session_options))
    try:
        if drain.coding is not None:
            leases = (
                ()
                if not (left := drain.left())
                else cast("tuple[QueueLease, ...]", _call(plan, store, drain.claim(left)))
            )
            fresh = drain.accept(leases)
            drain.admit(fresh)
            parallelism = drain.limits.policy.parallelism
            for start in range(0, len(fresh), parallelism):
                if not drain.session.room() or (
                    drain.session.deadline is not None and drain.session.deadline.remaining() <= 0
                ):
                    break
                _wave(drain, store, fresh[start : start + parallelism])
        for _ in range(0 if drain.coding is not None else 2 * drain.limits.policy.max_entries + 1):
            if not (left := drain.left()) or not (
                leases := cast("tuple[QueueLease, ...]", _call(plan, store, drain.claim(left)))
            ):
                break
            if fresh := drain.accept(leases):
                _wave(drain, store, fresh)
    except BaseException as error:
        try:
            _driven(plan, core, store, drain.release())
        except Exception as failure:  # noqa: BLE001
            add_secondary(error, failure)
        raise
    _driven(plan, core, store, drain.release())
    return drain.report()


async def adrain_queue(
    core: AsyncClientCore,
    plan: QueuePlan,
    *,
    queue_options: object = None,
    options: object = None,
    session_options: object = None,
) -> DrainReport:
    """Deliver ready entries as `drain_queue` does, awaiting the asynchronous store and the asyncio calls."""
    store: AsyncQueueStore = _store(core, plan)
    drain = _Drain(core, plan, _limits(core, plan, queue_options, options, session_options))
    try:
        if drain.coding is not None:
            leases = (
                ()
                if not (left := drain.left())
                else cast("tuple[QueueLease, ...]", await _acall(plan, store, drain.claim(left)))
            )
            fresh = drain.accept(leases)
            drain.admit(fresh)
            parallelism = drain.limits.policy.parallelism
            for start in range(0, len(fresh), parallelism):
                if not drain.session.room() or (
                    drain.session.deadline is not None and drain.session.deadline.remaining() <= 0
                ):
                    break
                await _awave(drain, store, fresh[start : start + parallelism])
        for _ in range(0 if drain.coding is not None else 2 * drain.limits.policy.max_entries + 1):
            if not (left := drain.left()) or not (
                leases := cast("tuple[QueueLease, ...]", await _acall(plan, store, drain.claim(left)))
            ):
                break
            if fresh := drain.accept(leases):
                await _awave(drain, store, fresh)
    except BaseException as error:
        try:
            await _adriven(plan, core, store, drain.release())
        except Exception as failure:  # noqa: BLE001
            add_secondary(error, failure)
        raise
    await _adriven(plan, core, store, drain.release())
    return drain.report()


def inspect_entry(core: ClientCore, plan: QueuePlan, entry_id: object) -> QueueEntry | None:
    """Return a queued entry as the store holds it, or None."""
    store: QueueStore = _store(core, plan)
    return cast("QueueEntry | None", _call(plan, store, _get(_entry_id(plan, entry_id))))


async def ainspect_entry(core: AsyncClientCore, plan: QueuePlan, entry_id: object) -> QueueEntry | None:
    """Return a queued entry as `inspect_entry` does, awaiting the asynchronous store."""
    store: AsyncQueueStore = _store(core, plan)
    return cast("QueueEntry | None", await _acall(plan, store, _get(_entry_id(plan, entry_id))))


def cancel_entry(core: ClientCore, plan: QueuePlan, entry_id: object) -> QueueEntry | None:
    """Cancel an entry and return it as saved, or None for an unknown entry."""
    store: QueueStore = _store(core, plan)
    return _driven(plan, core, store, _cancel(core, plan, _entry_id(plan, entry_id)))


async def acancel_entry(core: AsyncClientCore, plan: QueuePlan, entry_id: object) -> QueueEntry | None:
    """Cancel an entry as `cancel_entry` does, awaiting the asynchronous store."""
    store: AsyncQueueStore = _store(core, plan)
    return await _adriven(plan, core, store, _cancel(core, plan, _entry_id(plan, entry_id)))


def retry_entry(core: ClientCore, plan: QueuePlan, entry_id: object) -> QueueEntry | None:
    """Return an entry of unknown delivery to pending, or end it dead, and return it as saved; None when unknown."""
    store: QueueStore = _store(core, plan)
    return _driven(plan, core, store, _retry(core, plan, _entry_id(plan, entry_id), core.clock))


async def aretry_entry(core: AsyncClientCore, plan: QueuePlan, entry_id: object) -> QueueEntry | None:
    """Retry an entry of unknown delivery as `retry_entry` does, awaiting the asynchronous store."""
    store: AsyncQueueStore = _store(core, plan)
    return await _adriven(plan, core, store, _retry(core, plan, _entry_id(plan, entry_id), core.clock))


def purge_entries(core: ClientCore, plan: QueuePlan, before: object) -> tuple[QueueEntry, ...]:
    """Remove and return the terminal entries created before an instant."""
    store: QueueStore = _store(core, plan)
    return cast("tuple[QueueEntry, ...]", _call(plan, store, _purge(_instant(plan, before))))


async def apurge_entries(core: AsyncClientCore, plan: QueuePlan, before: object) -> tuple[QueueEntry, ...]:
    """Remove and return terminal entries as `purge_entries` does, awaiting the asynchronous store."""
    store: AsyncQueueStore = _store(core, plan)
    return cast("tuple[QueueEntry, ...]", await _acall(plan, store, _purge(_instant(plan, before))))
