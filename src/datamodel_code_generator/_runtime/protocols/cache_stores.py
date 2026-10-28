"""Explicitly constructed memory caches bounded by entry count, in least-recently-used order."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from ..client.errors import ConfigurationError
from ..client.timing import positive_count
from .caches import CacheEntry


def _key(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise ConfigurationError(field_path=("key",), reason="invalid_value")
    return value


class _Entries:
    """One representation per key, with a lock held only for each memory operation."""

    __slots__ = ("_entries", "_lock", "_max_entries")

    def __init__(self, max_entries: int) -> None:
        positive_count(max_entries, "max_entries")
        self._max_entries = max_entries
        self._entries: OrderedDict[bytes, CacheEntry] = OrderedDict()
        self._lock = Lock()

    def get(self, key: object) -> CacheEntry | None:
        key = _key(key)
        with self._lock:
            if (entry := self._entries.get(key)) is not None:
                self._entries.move_to_end(key)
            return entry

    def set(self, key: object, entry: object) -> None:
        key = _key(key)
        if not isinstance(entry, CacheEntry):
            raise ConfigurationError(field_path=("entry",), reason="invalid_value")
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def delete(self, key: object) -> None:
        key = _key(key)
        with self._lock:
            self._entries.pop(key, None)


class MemoryCacheStore:
    """Keep at most max_entries representations in this process; reads and writes mark a key recently used."""

    __slots__ = ("_entries",)

    def __init__(self, max_entries: int = 128) -> None:
        """Create an independent memory store."""
        self._entries = _Entries(max_entries)

    def get(self, key: bytes) -> CacheEntry | None:
        """Return the key's representation, or None."""
        return self._entries.get(key)

    def set(self, key: bytes, entry: CacheEntry) -> None:
        """Replace the key's representation and evict the oldest key if necessary."""
        self._entries.set(key, entry)

    def delete(self, key: bytes) -> None:
        """Remove the key's representation, if present."""
        self._entries.delete(key)


class AsyncMemoryCacheStore:
    """Use the same bounded memory operations asynchronously, without background I/O."""

    __slots__ = ("_entries",)

    def __init__(self, max_entries: int = 128) -> None:
        """Create an independent memory store."""
        self._entries = _Entries(max_entries)

    async def get(self, key: bytes) -> CacheEntry | None:
        """Return the key's representation, or None."""
        return self._entries.get(key)

    async def set(self, key: bytes, entry: CacheEntry) -> None:
        """Replace the key's representation and evict the oldest key if necessary."""
        self._entries.set(key, entry)

    async def delete(self, key: bytes) -> None:
        """Remove the key's representation, if present."""
        self._entries.delete(key)
