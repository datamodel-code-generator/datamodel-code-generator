"""Explicitly constructed bounded memory replay stores, with atomic claims and exact expiry."""

from __future__ import annotations

from datetime import datetime, timezone
from heapq import heappop, heappush
from threading import Lock
from time import time_ns
from typing import Final

from ..client.errors import ProtocolConfigurationError, ReplayStoreFullError
from .webhooks import positive_count

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _expiry(expires_at: object) -> int:
    if not isinstance(expires_at, datetime) or expires_at.utcoffset() is None:
        raise ProtocolConfigurationError(field_path=("expires_at",), condition="invalid_value")
    offset = expires_at - _EPOCH
    return (offset.days * 86400 + offset.seconds) * 1_000_000 + offset.microseconds


def _key(namespace: object, delivery_id: object) -> tuple[str, str]:
    if isinstance(namespace, str) and isinstance(delivery_id, str):
        return namespace, delivery_id
    name = "namespace" if not isinstance(namespace, str) else "delivery_id"
    raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")


class _Claims:
    __slots__ = ("_entries", "_expirations", "_lock", "_max_entries")

    def __init__(self, max_entries: int) -> None:
        positive_count(max_entries, "max_entries")
        self._max_entries = max_entries
        self._entries: set[tuple[str, str]] = set()
        self._expirations: list[tuple[int, tuple[str, str]]] = []
        self._lock = Lock()

    def claim(self, namespace: str, delivery_id: str, expires_at: datetime) -> bool:
        key = _key(namespace, delivery_id)
        expires = _expiry(expires_at)
        with self._lock:
            now = time_ns() // 1000
            while self._expirations and self._expirations[0][0] <= now:
                _, expired = heappop(self._expirations)
                self._entries.remove(expired)
            if key in self._entries:
                return False
            if expires <= now:
                return True
            if len(self._entries) >= self._max_entries:
                raise ReplayStoreFullError(action="claim", max_entries=self._max_entries)
            self._entries.add(key)
            heappush(self._expirations, (expires, key))
            return True


class MemoryReplayStore:
    """Keep at most max_entries live claims in this process without evicting any before expiry."""

    __slots__ = ("_claims",)

    def __init__(self, max_entries: int = 10000) -> None:
        """Create an independent store; no clients, workers, or network resources are created."""
        self._claims = _Claims(max_entries)

    def claim(self, namespace: str, delivery_id: str, expires_at: datetime) -> bool:
        """Atomically claim a delivery until expiry, or return False for an existing live claim."""
        return self._claims.claim(namespace, delivery_id, expires_at)


class AsyncMemoryReplayStore:
    """Apply the same bounded in-process claims through an asynchronous method without background I/O."""

    __slots__ = ("_claims",)

    def __init__(self, max_entries: int = 10000) -> None:
        """Create an independent store whose operations perform only atomic memory work."""
        self._claims = _Claims(max_entries)

    async def claim(self, namespace: str, delivery_id: str, expires_at: datetime) -> bool:
        """Atomically claim a delivery until expiry, or return False for an existing live claim."""
        return self._claims.claim(namespace, delivery_id, expires_at)
