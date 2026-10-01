"""Cache entries, cache results, and the contracts of the borrowed stores cache helpers keep their entries in."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Generic, Literal, Protocol, TypeAlias

from typing_extensions import TypeIs, TypeVar

from ..client.responses import HeadersView, ResponseInfo

__all__ = ("AsyncCacheStore", "CacheEntry", "CacheResult", "CacheSource", "CacheStore", "bytes_tuple", "string_tuple")

T_co = TypeVar("T_co", covariant=True, default=object)

CacheSource: TypeAlias = Literal["network", "fresh_cache", "revalidated"]

_SOURCES: tuple[CacheSource, ...] = ("network", "fresh_cache", "revalidated")
_MIN_STATUS = 100
_MAX_STATUS = 599


def _kind(valid: bool, name: str) -> None:  # noqa: FBT001
    if not valid:
        msg = f"{name} has the wrong type"
        raise TypeError(msg)


def _typed(value: object, kinds: type | tuple[type, ...], name: str) -> None:
    _kind(isinstance(value, kinds) and not isinstance(value, bool), name)


def _tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def string_tuple(value: object) -> TypeIs[tuple[str, ...]]:
    """Return whether a value is a tuple of strings."""
    return _tuple(value) and all(isinstance(item, str) for item in value)


def bytes_tuple(value: object) -> TypeIs[tuple[bytes, ...]]:
    """Return whether a value is a tuple of bytes."""
    return _tuple(value) and all(isinstance(item, bytes) for item in value)


def _range(valid: bool, message: str) -> None:  # noqa: FBT001
    if not valid:
        raise ValueError(message)


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheEntry:
    """One stored representation: its validated response, timing, Vary fingerprints, tags, and schema fingerprint.

    The body is the representation after content decoding and the headers are its effective headers. The version is
    opaque; a cache helper gives every entry it writes a new one. The representation names only the status.
    """

    version: str = field(repr=False)
    vary: tuple[str, ...] = field(repr=False)
    vary_fingerprints: tuple[bytes, ...] = field(repr=False)
    status_code: int
    headers: HeadersView = field(repr=False)
    body: bytes = field(repr=False)
    request_time: datetime = field(repr=False)
    response_time: datetime = field(repr=False)
    stored_at: datetime = field(repr=False)
    freshness_seconds: float = field(repr=False)
    initial_age_seconds: float = field(repr=False)
    tags: tuple[str, ...] = field(repr=False)
    schema_fingerprint: str = field(repr=False)

    def __post_init__(self) -> None:
        """Refuse wrong types with TypeError, and a status, time, duration, or fingerprint count out of range."""
        for name in ("version", "schema_fingerprint"):
            _typed(getattr(self, name), str, name)
        _kind(string_tuple(self.vary), "vary")
        _kind(bytes_tuple(self.vary_fingerprints), "vary_fingerprints")
        _kind(string_tuple(self.tags), "tags")
        _typed(self.status_code, int, "status_code")
        _typed(self.headers, HeadersView, "headers")
        _typed(self.body, bytes, "body")
        _range(_MIN_STATUS <= self.status_code <= _MAX_STATUS, "status_code must be from 100 to 599")
        _range(len(self.vary_fingerprints) == len(self.vary), "vary_fingerprints must match vary one to one")
        for name in ("request_time", "response_time", "stored_at"):
            _typed(instant := getattr(self, name), datetime, name)
            _range(instant.utcoffset() is not None, f"{name} must be timezone-aware")
        for name in ("freshness_seconds", "initial_age_seconds"):
            _typed(seconds := getattr(self, name), (int, float), name)
            _range(isfinite(seconds) and seconds >= 0, f"{name} must be finite and not negative")
            object.__setattr__(self, name, float(seconds))


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheResult(Generic[T_co]):
    """The decoded value of a cache fetch, where it came from, and the metadata of its response.

    `network_status` is the status the network returned, 304 for a revalidation, and None for a fresh stored entry.
    The representation never names the data.
    """

    data: T_co = field(repr=False)
    source: CacheSource
    response: ResponseInfo
    network_status: int | None

    def __post_init__(self) -> None:
        """Refuse an unknown source, response metadata of another type, and a status that is not an integer."""
        if self.source not in _SOURCES:
            msg = "source must be network, fresh_cache, or revalidated"
            raise ValueError(msg)
        _typed(self.response, ResponseInfo, "response")
        if self.network_status is not None:
            _typed(self.network_status, int, "network_status")


class CacheStore(Protocol):
    """A borrowed store of cache entries by base key, evaluating Vary and replacing entries by compare-and-exchange."""

    def lookup(self, base_key: bytes, request_headers: HeadersView) -> CacheEntry | None:
        """Return the entry of the base key whose Vary fingerprints match the request's headers, or None."""
        ...

    def fingerprint_vary(self, names: tuple[str, ...], request_headers: HeadersView) -> tuple[bytes, ...]:
        """Return the store's keyed fingerprint of each named request header's values, in order."""
        ...

    def compare_exchange(self, base_key: bytes, expected_version: str | None, entry: CacheEntry) -> bool:
        """Store the entry when its slot holds the expected version, None meaning an empty slot, else return False."""
        ...

    def delete(self, base_key: bytes, version: str) -> bool:
        """Remove the base key's entry of that version, returning whether one was removed."""
        ...

    def invalidate(self, tags: tuple[str, ...]) -> int:
        """Remove every entry carrying any of the tags, returning how many were removed."""
        ...


class AsyncCacheStore(Protocol):
    """A borrowed asynchronous store with the same contract as CacheStore."""

    async def lookup(self, base_key: bytes, request_headers: HeadersView) -> CacheEntry | None:
        """Return the entry of the base key whose Vary fingerprints match the request's headers, or None."""
        ...

    async def fingerprint_vary(self, names: tuple[str, ...], request_headers: HeadersView) -> tuple[bytes, ...]:
        """Return the store's keyed fingerprint of each named request header's values, in order."""
        ...

    async def compare_exchange(self, base_key: bytes, expected_version: str | None, entry: CacheEntry) -> bool:
        """Store the entry when its slot holds the expected version, None meaning an empty slot, else return False."""
        ...

    async def delete(self, base_key: bytes, version: str) -> bool:
        """Remove the base key's entry of that version, returning whether one was removed."""
        ...

    async def invalidate(self, tags: tuple[str, ...]) -> int:
        """Remove every entry carrying any of the tags, returning how many were removed."""
        ...
