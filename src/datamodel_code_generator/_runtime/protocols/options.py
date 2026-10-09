"""Helper limits, origins, the security context, and client protocol settings; UNSET takes the merged default."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from inspect import iscoroutinefunction
from keyword import iskeyword
from sys import float_info
from types import MappingProxyType
from typing import Final, cast

from typing_extensions import TypeIs, TypeVar

from ..client.errors import ConfigurationError, is_sequence
from ..client.timing import SessionOptions
from ..model_codecs.unset import UNSET, Unset
from .caches import AsyncCacheStore, CacheStore  # ruff: ignore[typing-only-first-party-import] - Public annotations support get_type_hints().
from .origins import Origin

V = TypeVar("V")

_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_PAGINATION: Final = (
    ("max_pages", False, True, False),
    ("max_items", False, True, True),
)
_POLL: Final = (
    ("max_polls", False, True, False),
    ("interval", True, False, False),
    ("max_wait", True, True, False),
)
_STREAM: Final = (
    ("idle_timeout", True, True, False),
    ("max_reconnects", False, True, True),
    ("max_reconnect_wait", True, True, False),
)
_UPLOAD: Final = (("chunk_bytes", False, False, False),)
_WS: Final = (
    ("open_timeout", True, True, False),
    ("idle_timeout", True, True, False),
    ("max_message_bytes", False, False, False),
    ("ping_interval", True, True, False),
    ("pong_timeout", True, True, False),
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


def _partition(value: object) -> None:
    if not isinstance(value, str) or not value or _CONTROL.search(value):
        raise ConfigurationError(field_path=("credential_partition",), reason="invalid_value")


def _origins(value: object) -> tuple[Origin, ...]:
    if is_sequence(value):
        origins = tuple(item for item in value if isinstance(item, Origin))
        if len(origins) == len(value) == len(set(origins)):
            return origins
    raise ConfigurationError(field_path=("allowed_origins",), reason="invalid_value")


def _is_helper_name(value: object) -> TypeIs[str]:
    return isinstance(value, str) and all(part.isidentifier() and not iskeyword(part) for part in value.split("."))


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationOptions:
    """Pagination limits; only max_pages and max_items take None, and only max_items takes 0."""

    max_pages: int | Unset | None = UNSET
    max_items: int | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, and every forbidden None or zero."""
        check_limits(self, _PAGINATION)


@dataclass(frozen=True, slots=True, kw_only=True)
class PollOptions:
    """Polling limits; only max_polls and max_wait take None, and durations are finite positive seconds."""

    max_polls: int | Unset | None = UNSET
    interval: float | Unset = UNSET
    max_wait: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, zero, and nonfinite durations."""
        check_limits(self, _POLL)


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamOptions:
    """Stream limits; reconnection stays off unless enabled, and only max_reconnects takes 0."""

    idle_timeout: float | Unset | None = UNSET
    reconnect: bool | Unset = UNSET
    max_reconnects: int | Unset | None = UNSET
    max_reconnect_wait: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans as limits, a nonboolean reconnect switch, and every forbidden None or zero."""
        check_limits(self, _STREAM)
        _instance(self.reconnect, (bool, Unset), "reconnect")


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadOptions:
    """Upload limits: the positive size of the chunk one append holds in memory."""

    chunk_bytes: int | Unset = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, and every forbidden None or zero."""
        check_limits(self, _UPLOAD)


@dataclass(frozen=True, slots=True, kw_only=True)
class WSOptions:
    """WebSocket limits; durations are finite positive seconds, None removes a limit where it is allowed.

    `idle_timeout` bounds a receive waiting for a message and inherits the native read timeout.
    """

    open_timeout: float | Unset | None = UNSET
    idle_timeout: float | Unset | None = UNSET
    max_message_bytes: int | Unset = UNSET
    ping_interval: float | Unset | None = UNSET
    pong_timeout: float | Unset | None = UNSET

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


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolSecurityContext:
    """The nonsecret credential partition of helper state and the origins permitted beyond the same origin."""

    credential_partition: str = field(repr=False)
    allowed_origins: tuple[Origin, ...] = ()

    def __post_init__(self) -> None:
        """Require a nonempty partition without control characters and copy the origins into a tuple."""
        _partition(self.credential_partition)
        object.__setattr__(self, "allowed_origins", _origins(self.allowed_origins))


def _helper_defaults(value: object) -> Mapping[str, ProtocolDefaults]:
    if not _is_mapping(value):
        raise ConfigurationError(field_path=("defaults",), reason="invalid_value")
    defaults: dict[str, ProtocolDefaults] = {}
    for name, item in value.items():
        if not _is_helper_name(name):
            raise ConfigurationError(field_path=("defaults",), reason="invalid_value")
        if not isinstance(item, ProtocolDefaults):
            raise ConfigurationError(field_path=("defaults", name), reason="invalid_value")
        defaults[name] = item
    return MappingProxyType(defaults)


def _stores(value: object, field: str) -> Mapping[str, object]:
    if not _is_mapping(value):
        raise ConfigurationError(field_path=(field,), reason="invalid_value")
    stores: dict[str, object] = {}
    for name, store in value.items():
        if not _is_helper_name(name):
            raise ConfigurationError(field_path=(field,), reason="invalid_value")
        stores[name] = store
    return MappingProxyType(stores)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolDefaults:
    """Defaults of one helper, below its call arguments and above the kind's effective defaults."""

    session: SessionOptions | Unset = UNSET
    options: PaginationOptions | PollOptions | StreamOptions | CacheOptions | WSOptions | UploadOptions | Unset = UNSET

    def __post_init__(self) -> None:
        """Refuse values other than session options and one kind's options."""
        _instance(self.session, (SessionOptions, Unset), "session")
        _instance(
            self.options,
            (
                PaginationOptions,
                PollOptions,
                StreamOptions,
                CacheOptions,
                WSOptions,
                UploadOptions,
                Unset,
            ),
            "options",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolClientOptions:
    """Protocol helper settings of one client: its security context, helper defaults, and stores.

    None as the security context means anonymous use. The defaults and the borrowed cache stores are keyed by
    dotted helper names; each mapping is copied into a read-only one that keeps each value's identity.
    """

    security: ProtocolSecurityContext | Unset | None = UNSET
    defaults: Mapping[str, ProtocolDefaults] | Unset = UNSET
    cache_stores: Mapping[str, CacheStore | AsyncCacheStore] | Unset = UNSET

    def __post_init__(self) -> None:
        """Refuse another security value, helper names that are not dotted identifiers, and other default values."""
        _instance(self.security, (ProtocolSecurityContext, Unset, type(None)), "security")
        if not isinstance(self.defaults, Unset):
            object.__setattr__(self, "defaults", _helper_defaults(self.defaults))
        if not isinstance(self.cache_stores, Unset):
            object.__setattr__(self, "cache_stores", _stores(self.cache_stores, "cache_stores"))


_KIND_OPTIONS: Final[Mapping[str, type]] = MappingProxyType({
    "pagination": PaginationOptions,
    "polling": PollOptions,
    "sse": StreamOptions,
    "ndjson": StreamOptions,
    "resumable_upload": UploadOptions,
    "cache": CacheOptions,
    "websocket": WSOptions,
})


def checked_defaults(defaults: Mapping[str, ProtocolDefaults], helpers: tuple[tuple[str, str], ...]) -> None:
    """Refuse helper defaults for a helper the package lacks, or whose options belong to another kind.

    A cache helper runs no session, so its defaults take no session options.
    """
    kinds = dict(helpers)
    for name, item in defaults.items():
        if (kind := kinds.get(name)) is None:
            raise ConfigurationError(field_path=("protocols", "defaults", name), reason="unknown_field")
        if not isinstance(item.options, (Unset, _KIND_OPTIONS[kind])):
            raise ConfigurationError(field_path=("protocols", "defaults", name, "options"), reason="invalid_value")
        if kind == "cache" and not isinstance(item.session, Unset):
            raise ConfigurationError(field_path=("protocols", "defaults", name, "session"), reason="invalid_value")


def checked_stores(
    stores: Mapping[str, object],
    helpers: tuple[tuple[str, str], ...],
    *,
    asynchronous: bool,
) -> None:
    """Refuse a cache store under a name that is no cache helper, or one the client cannot call.

    A store must have every method of the cache store contract, coroutine functions for an asyncio client and plain
    functions for a synchronous one.
    """
    kinds = dict(helpers)
    for name, store in stores.items():
        if kinds.get(name) != "cache":
            raise ConfigurationError(field_path=("protocols", "cache_stores", name), reason="unknown_field")
        methods = [getattr(store, method, None) for method in _CACHE_METHODS]
        if not all(callable(method) and iscoroutinefunction(method) == asynchronous for method in methods):
            raise ConfigurationError(field_path=("protocols", "cache_stores", name), reason="wrong_capability")


def check_helpers(
    protocols: ProtocolClientOptions, helpers: tuple[tuple[str, str], ...], *, asynchronous: bool
) -> None:
    """Check helper names and stores before creating the native client."""
    if not isinstance(defaults := protocols.defaults, Unset) and defaults:
        checked_defaults(defaults, helpers)
    if not isinstance(stores := protocols.cache_stores, Unset) and stores:
        checked_stores(stores, helpers, asynchronous=asynchronous)
