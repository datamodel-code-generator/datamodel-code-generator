"""Helper limits and the helper settings of a client; UNSET takes the merged default."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import iscoroutinefunction
from sys import float_info
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, TypeAlias, cast

from typing_extensions import TypeVar

from ..client.errors import ConfigurationError
from ..model_codecs.unset import UNSET, Unset

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .origins import Origin

V = TypeVar("V")

_SESSION: Final = ("total_timeout", True, True, True)
_PAGINATION: Final = (
    ("max_pages", False, True, False),
    ("max_items", False, True, True),
    _SESSION,
)
_POLL: Final = (
    ("max_polls", False, True, False),
    ("interval", True, False, False),
    ("max_wait", True, True, False),
    _SESSION,
)
_STREAM: Final = (
    ("idle_timeout", True, True, False),
    ("max_reconnects", False, True, True),
    ("max_reconnect_wait", True, True, False),
    _SESSION,
)
_UPLOAD: Final = (("chunk_bytes", False, False, False), _SESSION)
_WS: Final = (
    ("open_timeout", True, True, False),
    ("idle_timeout", True, True, False),
    ("max_message_bytes", False, False, False),
    ("ping_interval", True, True, False),
    ("pong_timeout", True, True, False),
    _SESSION,
)
_CACHE: Final = (("max_entry_bytes", False, False, False), ("max_ttl", True, False, False))
_CACHE_METHODS: Final = ("get", "set", "delete")
WEBHOOK_LIMITS: Final = (
    ("max_body_bytes", False, False, False),
    ("max_header_bytes", False, False, False),
    ("max_keys", False, False, False),
    ("max_signatures", False, False, False),
    ("past_tolerance", True, False, True),
    ("future_tolerance", True, False, True),
)


def layered(layers: tuple[object, ...], name: str, default: V) -> V:
    """Return an option from the first layer that sets it, skipping None and UNSET layers, or its default."""
    for layer in layers:
        if layer is not None and not isinstance(layer, Unset) and not isinstance(value := getattr(layer, name), Unset):
            return cast("V", value)
    return default


def positive_count(value: object, name: str, *, allow_zero: bool = False) -> None:
    """Validate a positive integer, or a nonnegative one, at a public configuration boundary."""
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ConfigurationError(field_path=(name,), reason="invalid_value")


def checked_seconds(value: object, name: str, *, allow_zero: bool = False) -> None:
    """Validate a finite duration in seconds, excluding booleans, that is positive or, if allowed, zero."""
    match value:
        case bool():
            pass
        case int() | float() if 0 <= value <= float_info.max and (allow_zero or value > 0):
            return
        case _:
            pass
    raise ConfigurationError(field_path=(name,), reason="invalid_value")


def check_limits(options: object, rules: tuple[tuple[str, bool, bool, bool], ...]) -> None:
    """Validate each set limit by its (name, duration, nullable, allow_zero) rule; None only where nullable."""
    for name, duration, nullable, allow_zero in rules:
        if isinstance(value := getattr(options, name), Unset) or (nullable and value is None):
            continue
        (checked_seconds if duration else positive_count)(value, name, allow_zero=allow_zero)


def _instance(value: object, kinds: tuple[type, ...], name: str) -> None:
    if not isinstance(value, kinds):
        raise ConfigurationError(field_path=(name,), reason="invalid_value")


@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationOptions:
    """Pagination limits; max_pages, max_items, and total_timeout take None, and max_items and total_timeout take 0.

    `total_timeout` bounds the whole session in seconds, counted from its start.
    """

    max_pages: int | Unset | None = UNSET
    max_items: int | Unset | None = UNSET
    total_timeout: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, and every forbidden None or zero."""
        check_limits(self, _PAGINATION)


@dataclass(frozen=True, slots=True, kw_only=True)
class PollOptions:
    """Polling limits; max_polls, max_wait, and total_timeout take None, and durations are finite positive seconds.

    `total_timeout`, which also takes 0, bounds the whole session in seconds, counted from its start.
    """

    max_polls: int | Unset | None = UNSET
    interval: float | Unset = UNSET
    max_wait: float | Unset | None = UNSET
    total_timeout: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, zero, and nonfinite durations."""
        check_limits(self, _POLL)


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamOptions:
    """Stream limits; reconnection stays off unless enabled, and only max_reconnects and total_timeout take 0.

    `total_timeout` bounds the whole session, reconnections included, in seconds counted from its start.
    """

    idle_timeout: float | Unset | None = UNSET
    reconnect: bool | Unset = UNSET
    max_reconnects: int | Unset | None = UNSET
    max_reconnect_wait: float | Unset | None = UNSET
    total_timeout: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans as limits, a nonboolean reconnect switch, and every forbidden None or zero."""
        check_limits(self, _STREAM)
        _instance(self.reconnect, (bool, Unset), "reconnect")


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadOptions:
    """Upload limits: the positive size of the chunk one append holds in memory, and the session's total timeout.

    `total_timeout` bounds the whole session in seconds, counted from its start; None removes it.
    """

    chunk_bytes: int | Unset = UNSET
    total_timeout: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, and every forbidden None or zero."""
        check_limits(self, _UPLOAD)


@dataclass(frozen=True, slots=True, kw_only=True)
class WSOptions:
    """WebSocket limits; durations are finite positive seconds, None removes a limit where it is allowed.

    `idle_timeout` bounds a receive waiting for a message and inherits the native read timeout. `total_timeout`, which
    also takes 0, bounds the whole session in seconds, counted from its start.
    """

    open_timeout: float | Unset | None = UNSET
    idle_timeout: float | Unset | None = UNSET
    max_message_bytes: int | Unset = UNSET
    ping_interval: float | Unset | None = UNSET
    pong_timeout: float | Unset | None = UNSET
    total_timeout: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans as limits, and forbidden None or zero."""
        check_limits(self, _WS)


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheOptions:
    """Cache limits of one fetch: the largest body it stores and the cap on any entry's freshness."""

    max_entry_bytes: int | Unset = UNSET
    max_ttl: float | Unset = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, zero, and nonfinite durations."""
        check_limits(self, _CACHE)


HelperOptions: TypeAlias = PaginationOptions | PollOptions | StreamOptions | UploadOptions | CacheOptions | WSOptions
_KIND_OPTIONS: Final[Mapping[str, type]] = MappingProxyType({
    "pagination": PaginationOptions,
    "polling": PollOptions,
    "sse": StreamOptions,
    "ndjson": StreamOptions,
    "resumable_upload": UploadOptions,
    "cache": CacheOptions,
    "websocket": WSOptions,
})


class HelperSettings:
    """The helper settings of one client root, which its views share: helper defaults, lent stores, and origins.

    The defaults and the lent cache stores are keyed by helper name, and duplicate origins are dropped. `created`
    holds the memory store the root creates on first use for a cache helper no store is lent to.
    """

    __slots__ = ("allowed_origins", "cache_stores", "created", "defaults")

    def __init__(
        self,
        *,
        defaults: Mapping[str, HelperOptions] | None = None,
        cache_stores: Mapping[str, object] | None = None,
        allowed_origins: Sequence[Origin] | None = None,
    ) -> None:
        """Copy the mappings and the origins, so that later changes to the caller's leave the client's alone."""
        self.defaults: Mapping[str, HelperOptions] = MappingProxyType(dict(defaults or {}))
        self.cache_stores: Mapping[str, object] = MappingProxyType(dict(cache_stores or {}))
        self.allowed_origins: tuple[Origin, ...] = tuple(dict.fromkeys(allowed_origins or ()))
        self.created: dict[str, object] = {}

    def check_helpers(self, helpers: tuple[tuple[str, str], ...], *, asynchronous: bool) -> None:
        """Refuse defaults or stores for a helper the package lacks, another kind's options, and an unusable store.

        A store must have every method of the cache store contract, coroutine functions for an asyncio client and plain
        functions for a synchronous one.
        """
        kinds = dict(helpers)
        for name, options in self.defaults.items():
            if (kind := kinds.get(name)) is None:
                raise ConfigurationError(field_path=("helper_defaults", name), reason="unknown_field")
            if not isinstance(options, _KIND_OPTIONS[kind]):
                raise ConfigurationError(field_path=("helper_defaults", name), reason="invalid_value")
        for name, store in self.cache_stores.items():
            if kinds.get(name) != "cache":
                raise ConfigurationError(field_path=("cache_stores", name), reason="unknown_field")
            methods = [getattr(store, method, None) for method in _CACHE_METHODS]
            if not all(callable(method) and iscoroutinefunction(method) == asynchronous for method in methods):
                raise ConfigurationError(field_path=("cache_stores", name), reason="wrong_capability")
