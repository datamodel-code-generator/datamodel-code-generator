"""Offline queue records: entries, leases, outcomes, receipts, drain reports, entry policies, and the store contracts.

An entry's payload is the opaque saved request of one queued call, and every record keeps payloads, keys, and digests
out of its repr.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias, final, get_args

from ..client.responses import ResponseInfo
from .records import record_instance, record_string

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = (
    "AsyncQueueStore",
    "BlobRef",
    "DrainReport",
    "OutcomeCategory",
    "QueueEntry",
    "QueueLease",
    "QueueOutcome",
    "QueueReceipt",
    "QueueState",
    "QueueStore",
    "ResolvedQueueOptions",
)

QueueState: TypeAlias = Literal["pending", "leased", "succeeded", "dead", "delivery_unknown", "cancelled"]
OutcomeCategory: TypeAlias = Literal["success", "retryable", "permanent", "unknown", "cancelled"]

QUEUE_STATES: Final = get_args(QueueState)
TERMINAL_STATES: Final = frozenset({"succeeded", "dead", "delivery_unknown", "cancelled"})
_CATEGORIES: Final = get_args(OutcomeCategory)
_DIGEST_BYTES: Final = 32
_MAX_DELIVERY_TIMEOUT: Final = 300.0
_COUNTS: Final = ("max_entries", "parallelism", "max_entry_body_bytes", "max_deliveries")
_SECONDS: Final = ("entry_ttl", "retry_initial_delay", "retry_max_delay", "lease_min", "lease_grace")
_REPORTED: Final = ("succeeded", "rescheduled", "dead", "unknown", "deferred", "cancelled")


def _count(value: object, name: str, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} must be an integer"
        raise TypeError(msg)
    if value < minimum:
        msg = f"{name} must be at least {minimum}"
        raise ValueError(msg)


def _seconds(value: object, name: str, *, allow_zero: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{name} must be a number of seconds"
        raise TypeError(msg)
    if not isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        msg = f"{name} must be a finite {'nonnegative' if allow_zero else 'positive'} number of seconds"
        raise ValueError(msg)


def _instant(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        msg = f"{name} must be a datetime"
        raise TypeError(msg)
    if value.utcoffset() is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)


def _optional_instant(value: object, name: str) -> None:
    if value is not None:
        _instant(value, name)


def _optional_string(value: object, name: str) -> None:
    if value is not None:
        record_string(value, name)


def _choice(value: object, name: str, choices: tuple[str, ...]) -> None:
    if record_string(value, name) not in choices:
        msg = f"{name} must be one of {', '.join(choices)}"
        raise ValueError(msg)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedQueueOptions:
    """Queue options with every default applied; an entry keeps the policy fields fixed when it was enqueued.

    `max_entries` and `parallelism` bound one drain; every other field is an entry's policy.
    """

    max_entries: int
    parallelism: int
    max_entry_body_bytes: int
    max_deliveries: int
    entry_ttl: float
    retry_initial_delay: float
    retry_max_delay: float
    lease_min: float
    lease_grace: float
    max_delivery_timeout: float

    def __post_init__(self) -> None:
        """Require positive counts and durations, retry delays in order, and a delivery timeout of at most 300 s."""
        for name in _COUNTS:
            _count(getattr(self, name), name, 1)
        for name in (*_SECONDS, "max_delivery_timeout"):
            _seconds(getattr(self, name), name)
        if self.max_delivery_timeout > _MAX_DELIVERY_TIMEOUT:
            msg = "max_delivery_timeout must be at most 300 seconds"
            raise ValueError(msg)
        if self.retry_max_delay < self.retry_initial_delay:
            msg = "retry_max_delay must not be less than retry_initial_delay"
            raise ValueError(msg)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class BlobRef:
    """One ownership lease of immutable content in a blob store: its size, SHA-256 digest, and opaque lease key."""

    size: int
    sha256: bytes = field(repr=False)
    key: str = field(repr=False)

    def __post_init__(self) -> None:
        """Require a nonnegative size, a 32-byte digest, and a nonempty key."""
        _count(self.size, "size")
        record_instance(self.sha256, bytes, "sha256 must be bytes")
        if len(self.sha256) != _DIGEST_BYTES:
            msg = "sha256 must be 32 bytes"
            raise ValueError(msg)
        if not record_string(self.key, "key"):
            msg = "key must not be empty"
            raise ValueError(msg)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class QueueOutcome:
    """How an entry's last delivery ended: its category, response metadata, next attempt, and a safe error code."""

    category: OutcomeCategory
    response: ResponseInfo | None = None
    retry_at: datetime | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Require a known category, response metadata or None, an aware retry instant or None, and a string code."""
        _choice(self.category, "category", _CATEGORIES)
        if self.response is not None:
            record_instance(self.response, ResponseInfo, "response must be a ResponseInfo")
        _optional_instant(self.retry_at, "retry_at")
        _optional_string(self.error_code, "error_code")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class QueueEntry:
    """One queued call as its store keeps it: the saved request, its bindings, policy, delivery state, and outcome.

    `payload` holds the saved request without credentials; `version` is the store's opaque CAS version, replaced on
    every write.
    """

    entry_id: str
    version: str = field(repr=False)
    operation_alias: str
    helper_fingerprint: str = field(repr=False)
    security_fingerprint: str = field(repr=False)
    payload: bytes = field(repr=False)
    blob: BlobRef | None = field(repr=False)
    blob_owned: bool
    idempotency_key: str | None = field(repr=False)
    created_at: datetime
    expires_at: datetime
    not_before: datetime
    saved_wait_seconds: float
    state: QueueState
    delivery_count: int
    send_intent: bool
    cancel_requested: bool
    lease_id: str | None = field(repr=False)
    lease_until: datetime | None
    policy: ResolvedQueueOptions
    result: QueueOutcome | None

    def __post_init__(self) -> None:
        """Require every field's type, aware instants, nonnegative counts and wait, a known state, and a lease pair."""
        for name in ("entry_id", "version", "operation_alias", "helper_fingerprint", "security_fingerprint"):
            record_string(getattr(self, name), name)
        record_instance(self.payload, bytes, "payload must be bytes")
        if self.blob is not None:
            record_instance(self.blob, BlobRef, "blob must be a BlobRef")
        for name in ("blob_owned", "send_intent", "cancel_requested"):
            record_instance(getattr(self, name), bool, f"{name} must be a bool")
        _optional_string(self.idempotency_key, "idempotency_key")
        for name in ("created_at", "expires_at", "not_before"):
            _instant(getattr(self, name), name)
        _seconds(self.saved_wait_seconds, "saved_wait_seconds", allow_zero=True)
        _choice(self.state, "state", QUEUE_STATES)
        _count(self.delivery_count, "delivery_count")
        _optional_string(self.lease_id, "lease_id")
        _optional_instant(self.lease_until, "lease_until")
        if (self.lease_id is None) != (self.lease_until is None):
            msg = "lease_id and lease_until must be given together"
            raise ValueError(msg)
        record_instance(self.policy, ResolvedQueueOptions, "policy must be a ResolvedQueueOptions")
        if self.result is not None:
            record_instance(self.result, QueueOutcome, "result must be a QueueOutcome")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class QueueLease:
    """An entry a claim moved to leased, with the lease that later writes of it must keep."""

    entry: QueueEntry
    lease_id: str = field(repr=False)

    def __post_init__(self) -> None:
        """Require a leased entry holding this lease."""
        record_instance(self.entry, QueueEntry, "entry must be a QueueEntry")
        record_string(self.lease_id, "lease_id")
        if self.entry.state != "leased" or self.entry.lease_id != self.lease_id:
            msg = "entry must be leased under lease_id"
            raise ValueError(msg)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class QueueReceipt:
    """The entry an enqueue persisted: its identifier, state, creation, and expiry."""

    entry_id: str
    state: QueueState
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        """Require a string identifier, a known state, and aware instants."""
        record_string(self.entry_id, "entry_id")
        _choice(self.state, "state", QUEUE_STATES)
        _instant(self.created_at, "created_at")
        _instant(self.expires_at, "expires_at")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DrainReport:
    """The entries one drain handled, by how each ended, in the order it claimed them; never a body or a secret.

    `deferred` entries were not sent, and kept their delivery count; `rescheduled` ones wait for a later drain.
    """

    succeeded: tuple[str, ...] = ()
    rescheduled: tuple[str, ...] = ()
    dead: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    cancelled: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require tuples of entry identifiers."""
        for name in _REPORTED:
            record_instance(value := getattr(self, name), tuple, f"{name} must be a tuple")
            for item in value:
                record_string(item, name)

    @property
    def counts(self) -> Mapping[str, int]:
        """Return how many entries ended each way, by the name of their field."""
        return MappingProxyType({name: len(getattr(self, name)) for name in _REPORTED})


class QueueStore(Protocol):
    """A borrowed store keeping queued entries with atomic claims, leases, and compare-and-exchange writes."""

    def put(self, entry: QueueEntry) -> None:
        """Persist a new entry atomically, raising QueueFullError rather than evicting another entry."""

    def get(self, entry_id: str) -> QueueEntry | None:
        """Return the entry with this identifier, or None."""
        ...

    def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[QueueLease, ...]:
        """Recover expired leases, then lease up to `limit` ready pending entries in creation-time and ID order.

        Recover entries with send intent to delivery_unknown, and only unsent entries to pending.
        """
        ...

    def compare_exchange(self, entry_id: str, expected_version: str, entry: QueueEntry) -> bool:
        """Replace the entry when its stored version is `expected_version`, giving it a new version; else False."""
        ...

    def purge_terminal(self, before: datetime) -> tuple[QueueEntry, ...]:
        """Remove and return the terminal entries created before an instant."""
        ...


class AsyncQueueStore(Protocol):
    """A borrowed asynchronous store with the same atomic entry, claim, and compare-and-exchange contract."""

    async def put(self, entry: QueueEntry) -> None:
        """Persist a new entry atomically, raising QueueFullError rather than evicting another entry."""

    async def get(self, entry_id: str) -> QueueEntry | None:
        """Return the entry with this identifier, or None."""
        ...

    async def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[QueueLease, ...]:
        """Recover expired leases, then lease up to `limit` ready pending entries in creation-time and ID order.

        Recover entries with send intent to delivery_unknown, and only unsent entries to pending.
        """
        ...

    async def compare_exchange(self, entry_id: str, expected_version: str, entry: QueueEntry) -> bool:
        """Replace the entry when its stored version is `expected_version`, giving it a new version; else False."""
        ...

    async def purge_terminal(self, before: datetime) -> tuple[QueueEntry, ...]:
        """Remove and return the terminal entries created before an instant."""
        ...
