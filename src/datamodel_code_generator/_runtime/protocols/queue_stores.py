"""Explicitly constructed bounded memory queue stores, with atomic claims, expiring leases, and versioned writes."""

from __future__ import annotations

from dataclasses import replace
from threading import Lock
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from ..client.errors import ProtocolConfigurationError
from .errors import QueueFullError, QueueStoreError
from .options import positive_count
from .queues import TERMINAL_STATES, QueueEntry, QueueLease

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ("AsyncMemoryQueueStore", "MemoryQueueStore")

_MAX_ENTRIES: Final = 10000
_MAX_BYTES: Final = 268435456


def _entry(entry: object) -> QueueEntry:
    if not isinstance(entry, QueueEntry):
        raise ProtocolConfigurationError(field_path=("entry",), condition="invalid_value")
    return entry


class _Entries:
    """The entries of one memory store under one lock, with the payload bytes they hold."""

    __slots__ = ("_bytes", "_entries", "_lock", "_max_bytes", "_max_entries")

    def __init__(self, max_entries: int, max_bytes: int) -> None:
        positive_count(max_entries, "max_entries")
        positive_count(max_bytes, "max_bytes")
        self._max_entries, self._max_bytes = max_entries, max_bytes
        self._entries: dict[str, QueueEntry] = {}
        self._bytes = 0
        self._lock = Lock()

    def put(self, entry: QueueEntry) -> None:
        entry = _entry(entry)
        size = len(entry.payload)
        with self._lock:
            if entry.entry_id in self._entries:
                raise QueueStoreError(action="put", entry_id=entry.entry_id)
            if (count := len(self._entries)) >= self._max_entries:
                raise QueueFullError(kind="entries", limit=self._max_entries, observed=count + 1)
            if (total := self._bytes + size) > self._max_bytes:
                raise QueueFullError(kind="bytes", limit=self._max_bytes, observed=total)
            self._entries[entry.entry_id] = replace(entry, version=uuid4().hex)
            self._bytes = total

    def get(self, entry_id: str) -> QueueEntry | None:
        with self._lock:
            return self._entries.get(entry_id)

    def claim(self, now: datetime, lease_until: datetime, limit: int) -> tuple[QueueLease, ...]:
        positive_count(limit, "limit")
        entries = self._entries
        with self._lock:
            for key, entry in entries.items():
                if entry.state == "leased" and entry.lease_until is not None and entry.lease_until <= now:
                    entries[key] = replace(entry, state="pending", lease_id=None, lease_until=None, version=uuid4().hex)
            ready = sorted(
                (entry for entry in entries.values() if entry.state == "pending" and entry.not_before <= now),
                key=lambda entry: (entry.created_at, entry.entry_id),
            )[:limit]
            leases: list[QueueLease] = []
            for entry in ready:
                lease_id = uuid4().hex
                leased = entries[entry.entry_id] = replace(
                    entry, state="leased", lease_id=lease_id, lease_until=lease_until, version=uuid4().hex
                )
                leases.append(QueueLease(entry=leased, lease_id=lease_id))
            return tuple(leases)

    def compare_exchange(self, entry_id: str, expected_version: str, entry: QueueEntry) -> bool:
        entry = _entry(entry)
        if entry.entry_id != entry_id:
            raise QueueStoreError(action="compare_exchange", entry_id=entry_id)
        with self._lock:
            if (current := self._entries.get(entry_id)) is None or current.version != expected_version:
                return False
            if entry.state == "leased" and current.state == "leased" and entry.lease_id != current.lease_id:
                return False
            self._entries[entry_id] = replace(entry, version=uuid4().hex)
            self._bytes += len(entry.payload) - len(current.payload)
            return True

    def purge_terminal(self, before: datetime) -> tuple[QueueEntry, ...]:
        with self._lock:
            purged = sorted(
                (
                    entry
                    for entry in self._entries.values()
                    if entry.state in TERMINAL_STATES and entry.created_at < before
                ),
                key=lambda entry: (entry.created_at, entry.entry_id),
            )
            for entry in purged:
                del self._entries[entry.entry_id]
                self._bytes -= len(entry.payload)
            return tuple(purged)


class MemoryQueueStore:
    """A bounded memory queue store of one process; a full store refuses new entries instead of evicting any.

    Claims recover expired leases, then lease ready pending entries in creation-time and ID order; every write gives
    the entry a new version. Payload bytes count toward `max_bytes`.
    """

    __slots__ = ("_entries",)

    def __init__(self, *, max_entries: int = _MAX_ENTRIES, max_bytes: int = _MAX_BYTES) -> None:
        """Hold at most `max_entries` entries with `max_bytes` payload bytes in all."""
        self._entries = _Entries(max_entries, max_bytes)

    def put(self, entry: QueueEntry) -> None:
        """Store a new entry, raising QueueFullError when either limit would be exceeded."""
        self._entries.put(entry)

    def get(self, entry_id: str) -> QueueEntry | None:
        """Return the entry with this identifier, or None."""
        return self._entries.get(entry_id)

    def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[QueueLease, ...]:
        """Recover expired leases, then lease up to `limit` ready pending entries in creation-time and ID order."""
        return self._entries.claim(now, lease_until, limit)

    def compare_exchange(self, entry_id: str, expected_version: str, entry: QueueEntry) -> bool:
        """Replace the entry when its version is `expected_version` and a leased entry keeps its lease."""
        return self._entries.compare_exchange(entry_id, expected_version, entry)

    def purge_terminal(self, before: datetime) -> tuple[QueueEntry, ...]:
        """Remove and return the terminal entries created before an instant."""
        return self._entries.purge_terminal(before)


class AsyncMemoryQueueStore:
    """The asynchronous memory queue store: the same limits and atomic behavior, with coroutine methods."""

    __slots__ = ("_entries",)

    def __init__(self, *, max_entries: int = _MAX_ENTRIES, max_bytes: int = _MAX_BYTES) -> None:
        """Hold at most `max_entries` entries with `max_bytes` payload bytes in all."""
        self._entries = _Entries(max_entries, max_bytes)

    async def put(self, entry: QueueEntry) -> None:
        """Store a new entry, raising QueueFullError when either limit would be exceeded."""
        self._entries.put(entry)

    async def get(self, entry_id: str) -> QueueEntry | None:
        """Return the entry with this identifier, or None."""
        return self._entries.get(entry_id)

    async def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[QueueLease, ...]:
        """Recover expired leases, then lease up to `limit` ready pending entries in creation-time and ID order."""
        return self._entries.claim(now, lease_until, limit)

    async def compare_exchange(self, entry_id: str, expected_version: str, entry: QueueEntry) -> bool:
        """Replace the entry when its version is `expected_version` and a leased entry keeps its lease."""
        return self._entries.compare_exchange(entry_id, expected_version, entry)

    async def purge_terminal(self, before: datetime) -> tuple[QueueEntry, ...]:
        """Remove and return the terminal entries created before an instant."""
        return self._entries.purge_terminal(before)
