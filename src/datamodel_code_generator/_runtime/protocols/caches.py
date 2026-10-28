"""Cache entries, cache results, and the contracts of the borrowed stores cache helpers keep their entries in."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Generic, Literal, Protocol, TypeAlias

from typing_extensions import TypeIs, TypeVar

from ..client.responses import HeadersView, ResponseInfo

__all__ = ("AsyncCacheStore", "CacheEntry", "CacheResult", "CacheSource", "CacheStore", "string_tuple")

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


def _range(valid: bool, message: str) -> None:  # noqa: FBT001
    if not valid:
        raise ValueError(message)


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheEntry:
    """One stored representation, its timing, plain request Vary values, and schema fingerprint.

    The body is content-decoded and the headers are effective representation headers. Header values remain private
    process memory and are excluded from the representation.
    """

    vary: tuple[str, ...] = field(repr=False)
    vary_values: tuple[tuple[str, ...], ...] = field(repr=False)
    status_code: int
    headers: HeadersView = field(repr=False)
    body: bytes = field(repr=False)
    request_time: datetime = field(repr=False)
    response_time: datetime = field(repr=False)
    stored_at: datetime = field(repr=False)
    freshness_seconds: float = field(repr=False)
    initial_age_seconds: float = field(repr=False)
    schema_fingerprint: str = field(repr=False)

    def __post_init__(self) -> None:
        """Refuse wrong types with TypeError, and a status, time, duration, or Vary value count out of range."""
        _typed(self.schema_fingerprint, str, "schema_fingerprint")
        _kind(string_tuple(self.vary), "vary")
        _kind(_tuple(self.vary_values) and all(string_tuple(item) for item in self.vary_values), "vary_values")
        _typed(self.status_code, int, "status_code")
        _typed(self.headers, HeadersView, "headers")
        _typed(self.body, bytes, "body")
        _range(_MIN_STATUS <= self.status_code <= _MAX_STATUS, "status_code must be from 100 to 599")
        _range(len(self.vary_values) == len(self.vary), "vary_values must match vary one to one")
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
    """A borrowed store with one representation per key and last-write replacement."""

    def get(self, key: bytes) -> CacheEntry | None:
        """Return the key's representation, or None."""
        ...

    def set(self, key: bytes, entry: CacheEntry) -> None:
        """Replace the key's representation."""

    def delete(self, key: bytes) -> None:
        """Remove the key's representation, if present."""


class AsyncCacheStore(Protocol):
    """A borrowed asynchronous store with the same contract as CacheStore."""

    async def get(self, key: bytes) -> CacheEntry | None:
        """Return the key's representation, or None."""
        ...

    async def set(self, key: bytes, entry: CacheEntry) -> None:
        """Replace the key's representation."""

    async def delete(self, key: bytes) -> None:
        """Remove the key's representation, if present."""
