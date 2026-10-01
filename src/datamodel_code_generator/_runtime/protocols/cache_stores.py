"""Explicitly constructed bounded memory cache stores, with keyed Vary fingerprints and least-recently-used eviction."""

from __future__ import annotations

import hmac
from collections import OrderedDict
from hashlib import sha256
from heapq import heapify, heappop, heappush
from math import inf
from secrets import token_bytes
from threading import Lock
from time import time
from typing import TYPE_CHECKING, Final, TypeAlias

from ..client.errors import ProtocolConfigurationError
from .caches import CacheEntry, string_tuple
from .options import positive_count

if TYPE_CHECKING:
    from ..client.responses import HeadersView

_Slot: TypeAlias = tuple[bytes, tuple[str, ...], tuple[bytes, ...]]
_ABSENT: Final = b"\x00"
_PRESENT: Final = b"\x01"


def _argument(valid: bool, name: str) -> None:  # noqa: FBT001
    if not valid:
        raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")


def _key(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise ProtocolConfigurationError(field_path=("base_key",), condition="invalid_value")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not string_tuple(value):
        raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")
    return value


def _size(entry: CacheEntry) -> int:
    return len(entry.body) + sum(len(name) + len(value) for name, value in entry.headers)


def _expiry(entry: CacheEntry) -> float:
    """Return the instant an entry's age reaches its freshness lifetime, minus infinity for one stored stale."""
    remaining = entry.freshness_seconds - entry.initial_age_seconds
    return entry.response_time.timestamp() + remaining if remaining > 0 else -inf


class _Entries:
    """The entries of one store by slot, in least-recently-used order, with the secret of their fingerprints.

    Each base key's slots are grouped by their Vary names, and a heap orders the slots by when their entries expire;
    a heap item whose entry was replaced or removed is skipped when it surfaces, and the heap is rebuilt once such
    items outnumber the entries.
    """

    __slots__ = ("_bytes", "_entries", "_expiries", "_keys", "_lock", "_max_bytes", "_max_entries", "_secret")

    def __init__(self, max_entries: int, max_bytes: int) -> None:
        positive_count(max_entries, "max_entries")
        positive_count(max_bytes, "max_bytes")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[_Slot, CacheEntry] = OrderedDict()
        self._keys: dict[bytes, dict[tuple[str, ...], set[_Slot]]] = {}
        self._expiries: list[tuple[float, _Slot]] = []
        self._bytes = 0
        self._secret = token_bytes(32)
        self._lock = Lock()

    def fingerprint_vary(self, names: object, request_headers: HeadersView) -> tuple[bytes, ...]:
        """Return an HMAC of each named header's values, telling an absent header from an empty one."""
        return tuple(
            hmac.new(
                self._secret,
                _PRESENT + ",".join(value.strip() for value in values).encode("utf-8", "surrogatepass")
                if (values := request_headers.get_all(name))
                else _ABSENT,
                sha256,
            ).digest()
            for name in _strings(names, "names")
        )

    def lookup(self, base_key: object, request_headers: HeadersView) -> CacheEntry | None:
        """Return the most recently stored entry of the key whose fingerprints match, marking it recently used.

        The request is fingerprinted once for each set of Vary names the key's entries use, however many entries
        share it.
        """
        base_key = _key(base_key)
        entries = self._entries
        with self._lock:
            found: tuple[_Slot, CacheEntry] | None = None
            for names in self._keys.get(base_key, ()):
                slot = (base_key, names, self.fingerprint_vary(names, request_headers))
                if (entry := entries.get(slot)) is not None and (found is None or entry.stored_at > found[1].stored_at):
                    found = slot, entry
            if found is None:
                return None
            entries.move_to_end(found[0])
            return found[1]

    def compare_exchange(self, base_key: object, expected_version: object, entry: object) -> bool:
        """Store the entry in its slot when the slot holds the expected version, evicting what it needs room for.

        Expired entries are evicted before the least recently used ones; an entry larger than the store is refused.
        """
        base_key = _key(base_key)
        _argument(expected_version is None or isinstance(expected_version, str), "expected_version")
        if not isinstance(entry, CacheEntry):
            raise ProtocolConfigurationError(field_path=("entry",), condition="invalid_value")
        slot: _Slot = (base_key, entry.vary, entry.vary_fingerprints)
        size = _size(entry)
        with self._lock:
            current = self._entries.get(slot)
            if (None if current is None else current.version) != expected_version or size > self._max_bytes:
                return False
            if current is not None:
                self._remove(slot)
            self._evict(size)
            self._entries[slot] = entry
            self._keys.setdefault(base_key, {}).setdefault(entry.vary, set()).add(slot)
            self._bytes += size
            expiries = self._expiries
            heappush(expiries, (_expiry(entry), slot))
            if len(expiries) > 2 * len(self._entries):
                expiries[:] = [(_expiry(stored), stored_slot) for stored_slot, stored in self._entries.items()]
                heapify(expiries)
            return True

    def delete(self, base_key: object, version: object) -> bool:
        """Remove the key's entry of that version."""
        base_key = _key(base_key)
        _argument(isinstance(version, str), "version")
        with self._lock:
            slot = next(
                (
                    slot
                    for slots in self._keys.get(base_key, {}).values()
                    for slot in slots
                    if self._entries[slot].version == version
                ),
                None,
            )
            if slot is None:
                return False
            self._remove(slot)
            return True

    def invalidate(self, tags: object) -> int:
        """Remove every entry carrying any of the tags."""
        wanted = frozenset(_strings(tags, "tags"))
        with self._lock:
            removed = [slot for slot, entry in self._entries.items() if not wanted.isdisjoint(entry.tags)]
            for slot in removed:
                self._remove(slot)
            return len(removed)

    def _fits(self, size: int) -> bool:
        return len(self._entries) < self._max_entries and self._bytes + size <= self._max_bytes

    def _evict(self, size: int) -> None:
        """Remove expired entries, earliest expired first, then the least recently used ones, until the size fits."""
        entries, expiries, now = self._entries, self._expiries, time()
        while not self._fits(size) and expiries and expiries[0][0] <= now:
            slot = heappop(expiries)[1]
            if (stored := entries.get(slot)) is not None and _expiry(stored) <= now:
                self._remove(slot)
        while not self._fits(size):
            self._remove(next(iter(entries)))

    def _remove(self, slot: _Slot) -> None:
        self._bytes -= _size(self._entries.pop(slot))
        groups = self._keys[slot[0]]
        slots = groups[slot[1]]
        slots.discard(slot)
        if not slots:
            del groups[slot[1]]
            if not groups:
                del self._keys[slot[0]]


class MemoryCacheStore:
    """Keep at most max_entries entries of at most max_bytes body and header characters in this process.

    Vary fingerprints are HMACs keyed by a secret of this instance, so header values never become keys.
    """

    __slots__ = ("_entries",)

    def __init__(self, max_entries: int = 128, max_bytes: int = 16 * 1024 * 1024) -> None:
        """Create an independent store; no clients, workers, or network resources are created."""
        self._entries = _Entries(max_entries, max_bytes)

    def lookup(self, base_key: bytes, request_headers: HeadersView) -> CacheEntry | None:
        """Return the key's entry whose Vary fingerprints match the request's headers, or None."""
        return self._entries.lookup(base_key, request_headers)

    def fingerprint_vary(self, names: tuple[str, ...], request_headers: HeadersView) -> tuple[bytes, ...]:
        """Return this store's keyed fingerprint of each named request header's values."""
        return self._entries.fingerprint_vary(names, request_headers)

    def compare_exchange(self, base_key: bytes, expected_version: str | None, entry: CacheEntry) -> bool:
        """Store the entry when its slot holds the expected version, else return False."""
        return self._entries.compare_exchange(base_key, expected_version, entry)

    def delete(self, base_key: bytes, version: str) -> bool:
        """Remove the key's entry of that version, returning whether one was removed."""
        return self._entries.delete(base_key, version)

    def invalidate(self, tags: tuple[str, ...]) -> int:
        """Remove every entry carrying any of the tags, returning how many were removed."""
        return self._entries.invalidate(tags)


class AsyncMemoryCacheStore:
    """Apply the same bounded in-process entries through asynchronous methods without background I/O."""

    __slots__ = ("_entries",)

    def __init__(self, max_entries: int = 128, max_bytes: int = 16 * 1024 * 1024) -> None:
        """Create an independent store whose operations perform only memory work."""
        self._entries = _Entries(max_entries, max_bytes)

    async def lookup(self, base_key: bytes, request_headers: HeadersView) -> CacheEntry | None:
        """Return the key's entry whose Vary fingerprints match the request's headers, or None."""
        return self._entries.lookup(base_key, request_headers)

    async def fingerprint_vary(self, names: tuple[str, ...], request_headers: HeadersView) -> tuple[bytes, ...]:
        """Return this store's keyed fingerprint of each named request header's values."""
        return self._entries.fingerprint_vary(names, request_headers)

    async def compare_exchange(self, base_key: bytes, expected_version: str | None, entry: CacheEntry) -> bool:
        """Store the entry when its slot holds the expected version, else return False."""
        return self._entries.compare_exchange(base_key, expected_version, entry)

    async def delete(self, base_key: bytes, version: str) -> bool:
        """Remove the key's entry of that version, returning whether one was removed."""
        return self._entries.delete(base_key, version)

    async def invalidate(self, tags: tuple[str, ...]) -> int:
        """Remove every entry carrying any of the tags, returning how many were removed."""
        return self._entries.invalidate(tags)
