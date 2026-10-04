"""Helper limits, origins, the security context, and client protocol settings; UNSET takes the merged default."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from inspect import iscoroutinefunction
from keyword import iskeyword
from sys import float_info
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

from typing_extensions import TypeIs, TypeVar

from ..client.errors import ProtocolConfigurationError, is_sequence
from ..client.timing import SessionOptions
from ..model_codecs.unset import UNSET, Unset
from .caches import AsyncCacheStore, CacheStore  # noqa: TC001 - Public annotations support get_type_hints().
from .circuit_records import (  # noqa: TC001 - Public annotations support get_type_hints().
    CircuitKey,
    CircuitOutcome,
    CircuitPermit,
    CircuitSnapshot,
)
from .origins import Origin
from .queues import AsyncQueueStore, QueueStore, ResolvedQueueOptions
from .websocket_types import (
    AsyncWebSocketConnector,
    ResolvedWebSocketTransportOptions,
    WebSocketConnector,
)

if TYPE_CHECKING:
    from ssl import SSLContext

V = TypeVar("V")

_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_PAGINATION: Final = (
    ("max_pages", False, True, False),
    ("max_items", False, True, True),
    ("max_page_bytes", False, False, False),
    ("max_cursor_bytes", False, False, False),
)
_POLL: Final = (
    ("max_polls", False, True, False),
    ("interval", True, False, False),
    ("max_wait", True, True, False),
)
_STREAM: Final = (
    ("idle_timeout", True, True, False),
    ("max_line_bytes", False, False, False),
    ("max_event_bytes", False, False, False),
    ("max_reconnects", False, True, True),
    ("max_reconnect_wait", True, True, False),
)
_UPLOAD: Final = (
    ("chunk_bytes", False, False, False),
    ("max_parts", False, True, False),
    ("max_uncertain_probes", False, False, True),
)
_WS: Final = (
    ("open_timeout", True, True, False),
    ("idle_timeout", True, True, False),
    ("max_message_bytes", False, False, False),
    ("max_queue", False, False, False),
    ("send_timeout", True, True, False),
    ("ping_interval", True, True, False),
    ("pong_timeout", True, True, False),
    ("close_timeout", True, False, False),
    ("resume_ack_timeout", True, False, False),
    ("max_ack_buffer_messages", False, False, False),
    ("max_ack_buffer_bytes", False, False, False),
    ("max_unacked", False, False, False),
    ("max_reconnects", False, True, True),
)
_PROXY_SCHEMES: Final = ("http", "https")
_CACHE: Final = (("max_entry_bytes", False, False, False), ("max_ttl", True, False, False))
_CACHE_METHODS: Final = ("lookup", "fingerprint_vary", "compare_exchange", "delete", "invalidate")
QUEUE_FIELDS: Final = (
    ("max_entries", False, False, False),
    ("parallelism", False, False, False),
    ("max_entry_body_bytes", False, False, False),
    ("max_deliveries", False, False, False),
    ("entry_ttl", True, False, False),
    ("retry_initial_delay", True, False, False),
    ("retry_max_delay", True, False, False),
    ("lease_min", True, False, False),
    ("lease_grace", True, False, False),
    ("max_delivery_timeout", True, False, False),
)
QUEUE_DEFAULTS: Final = ResolvedQueueOptions(
    max_entries=100,
    parallelism=1,
    max_entry_body_bytes=8388608,
    max_deliveries=5,
    entry_ttl=86400.0,
    retry_initial_delay=5.0,
    retry_max_delay=600.0,
    lease_min=90.0,
    lease_grace=30.0,
    max_delivery_timeout=300.0,
)
_QUEUE_METHODS: Final = ("put", "get", "claim", "compare_exchange", "purge_terminal")
_STORE_METHODS: Final = {"cache": _CACHE_METHODS, "queue": _QUEUE_METHODS}
_MAX_DELIVERY_TIMEOUT: Final = 300.0
WEBHOOK_LIMITS: Final = (
    ("max_body_bytes", False, False, False),
    ("max_header_bytes", False, False, False),
    ("max_keys", False, False, False),
    ("max_signatures", False, False, False),
    ("past_tolerance", True, False, True),
    ("future_tolerance", True, False, True),
    ("replay_ttl", True, False, False),
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
        raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")


def checked_seconds(value: object, name: str, *, allow_zero: bool = False) -> None:
    """Validate a finite duration in seconds, excluding booleans, that is positive or, if allowed, zero."""
    match value:
        case bool():
            pass
        case int() | float() if 0 <= value <= float_info.max and (allow_zero or value > 0):
            return
        case _:
            pass
    raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")


def check_limits(options: object, rules: tuple[tuple[str, bool, bool, bool], ...]) -> None:
    """Validate each set limit by its (name, duration, nullable, allow_zero) rule; None only where nullable."""
    for name, duration, nullable, allow_zero in rules:
        if isinstance(value := getattr(options, name), Unset) or (nullable and value is None):
            continue
        (checked_seconds if duration else positive_count)(value, name, allow_zero=allow_zero)


def _instance(value: object, kinds: tuple[type, ...], name: str) -> None:
    if not isinstance(value, kinds):
        raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")


def _partition(value: object) -> None:
    if not isinstance(value, str) or not value or _CONTROL.search(value):
        raise ProtocolConfigurationError(field_path=("credential_partition",), condition="invalid_value")


def _origins(value: object) -> tuple[Origin, ...]:
    if is_sequence(value):
        origins = tuple(item for item in value if isinstance(item, Origin))
        if len(origins) == len(value) == len(set(origins)):
            return origins
    raise ProtocolConfigurationError(field_path=("allowed_origins",), condition="invalid_value")


def _is_helper_name(value: object) -> TypeIs[str]:
    return isinstance(value, str) and all(part.isidentifier() and not iskeyword(part) for part in value.split("."))


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


@dataclass(frozen=True, slots=True, kw_only=True)
class PaginationOptions:
    """Pagination limits; only max_pages and max_items take None, and only max_items takes 0."""

    max_pages: int | Unset | None = UNSET
    max_items: int | Unset | None = UNSET
    max_page_bytes: int | Unset = UNSET
    max_cursor_bytes: int | Unset = UNSET

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
    max_line_bytes: int | Unset = UNSET
    max_event_bytes: int | Unset = UNSET
    reconnect: bool | Unset = UNSET
    max_reconnects: int | Unset | None = UNSET
    max_reconnect_wait: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans as limits, a nonboolean reconnect switch, and every forbidden None or zero."""
        check_limits(self, _STREAM)
        _instance(self.reconnect, (bool, Unset), "reconnect")


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadOptions:
    """Upload limits; only max_parts takes None, and only max_uncertain_probes takes 0."""

    chunk_bytes: int | Unset = UNSET
    max_parts: int | Unset | None = UNSET
    max_uncertain_probes: int | Unset = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, and every forbidden None or zero."""
        check_limits(self, _UPLOAD)


@dataclass(frozen=True, slots=True, kw_only=True)
class WSOptions:
    """WebSocket limits; durations are finite positive seconds, None removes a limit where it is allowed.

    `idle_timeout` bounds a receive waiting for a message and inherits the call's stream idle timeout. Compression is
    `deflate` only where the helper permits it. Reconnection stays off unless enabled, and only max_reconnects takes 0.
    """

    open_timeout: float | Unset | None = UNSET
    idle_timeout: float | Unset | None = UNSET
    max_message_bytes: int | Unset = UNSET
    max_queue: int | Unset = UNSET
    send_timeout: float | Unset | None = UNSET
    ping_interval: float | Unset | None = UNSET
    pong_timeout: float | Unset | None = UNSET
    close_timeout: float | Unset = UNSET
    resume_ack_timeout: float | Unset = UNSET
    max_ack_buffer_messages: int | Unset = UNSET
    max_ack_buffer_bytes: int | Unset = UNSET
    max_unacked: int | Unset = UNSET
    compression: Literal["deflate"] | Unset | None = UNSET
    reconnect: bool | Unset = UNSET
    max_reconnects: int | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Reject booleans as limits, other compressions, a nonboolean reconnect switch, and forbidden None or zero."""
        check_limits(self, _WS)
        _instance(self.reconnect, (bool, Unset), "reconnect")
        if not isinstance(self.compression, Unset) and self.compression not in {None, "deflate"}:
            raise ProtocolConfigurationError(field_path=("compression",), condition="invalid_value")


def _context(value: object, name: str) -> None:
    if value is None or isinstance(value, Unset):
        return
    from ssl import SSLContext  # noqa: PLC0415 - Only a given context loads the TLS module.

    _instance(value, (SSLContext,), name)


def valid_proxy(value: object) -> bool:
    """Return whether a value is a proxy URL the WebSocket library accepts, of the HTTP or HTTPS scheme.

    It has a host and a nonzero port, a path of at most a slash, no query or fragment, and a password with any user.
    """
    from urllib.parse import urlparse  # noqa: PLC0415 - Only a given proxy is parsed.

    try:
        parts = urlparse(value) if isinstance(value, str) else None
        return (
            parts is not None
            and parts.scheme in _PROXY_SCHEMES
            and bool(parts.hostname)
            and parts.port != 0
            and parts.path in {"", "/"}
            and not parts.query
            and not parts.fragment
            and (parts.username is None or parts.password is not None)
        )
    except ValueError:
        return False


def _proxy(value: object) -> None:
    if value is not None and not isinstance(value, Unset) and not valid_proxy(value):
        raise ProtocolConfigurationError(field_path=("proxy",), condition="invalid_value")


@dataclass(frozen=True, slots=True, kw_only=True)
class WebSocketTransportOptions:
    """How WebSocket connections reach their servers: TLS contexts, an HTTP or HTTPS proxy, and environment proxies.

    None as a TLS context is the standard verifying one. An explicit proxy wins; environment proxies apply only with
    `trust_env`. The proxy URL, which may hold credentials, never appears in the representation.
    """

    ssl_context: SSLContext | Unset | None = UNSET
    proxy: str | Unset | None = field(default=UNSET, repr=False)
    proxy_ssl_context: SSLContext | Unset | None = UNSET
    trust_env: bool | Unset = UNSET

    def __post_init__(self) -> None:
        """Refuse other TLS context types, a proxy that is not an http or https URL, and a nonboolean switch.

        A proxy TLS context needs an explicit HTTPS proxy.
        """
        _context(self.ssl_context, "ssl_context")
        _context(self.proxy_ssl_context, "proxy_ssl_context")
        _proxy(self.proxy)
        _instance(self.trust_env, (bool, Unset), "trust_env")
        context, proxy = self.proxy_ssl_context, self.proxy
        if (
            context is not None
            and not isinstance(context, Unset)
            and not (isinstance(proxy, str) and proxy.lower().startswith("https:"))
        ):
            raise ProtocolConfigurationError(field_path=("proxy_ssl_context",), condition="invalid_value")


def resolved_transport(options: WebSocketTransportOptions | Unset) -> ResolvedWebSocketTransportOptions:
    """Return the effective transport settings of a client's WebSocket options."""
    if isinstance(options, Unset):
        return _NO_TRANSPORT
    return ResolvedWebSocketTransportOptions(
        ssl_context=None if isinstance(context := options.ssl_context, Unset) else context,
        proxy=None if isinstance(proxy := options.proxy, Unset) else proxy,
        proxy_ssl_context=None if isinstance(context := options.proxy_ssl_context, Unset) else context,
        trust_env=options.trust_env is True,
    )


_NO_TRANSPORT: Final = ResolvedWebSocketTransportOptions(
    ssl_context=None, proxy=None, proxy_ssl_context=None, trust_env=False
)


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheOptions:
    """Cache limits of one fetch: the largest body it stores and the cap on any entry's freshness."""

    max_entry_bytes: int | Unset = UNSET
    max_ttl: float | Unset = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, zero, and nonfinite durations."""
        check_limits(self, _CACHE)


@dataclass(frozen=True, slots=True, kw_only=True)
class QueueOptions:
    """Queue settings: `max_entries` and `parallelism` bound one drain, every other field an entry's fixed policy.

    Durations are finite positive seconds and counts positive integers; `max_delivery_timeout` is at most 300.
    """

    max_entries: int | Unset = UNSET
    parallelism: int | Unset = UNSET
    max_entry_body_bytes: int | Unset = UNSET
    max_deliveries: int | Unset = UNSET
    entry_ttl: float | Unset = UNSET
    retry_initial_delay: float | Unset = UNSET
    retry_max_delay: float | Unset = UNSET
    lease_min: float | Unset = UNSET
    lease_grace: float | Unset = UNSET
    max_delivery_timeout: float | Unset = UNSET

    def __post_init__(self) -> None:
        """Reject booleans, other types, zero, nonfinite durations, and a delivery timeout over 300 seconds."""
        check_limits(self, QUEUE_FIELDS)
        if not isinstance(timeout := self.max_delivery_timeout, Unset) and timeout > _MAX_DELIVERY_TIMEOUT:
            raise ProtocolConfigurationError(field_path=("max_delivery_timeout",), condition="invalid_value")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolSecurityContext:
    """The nonsecret credential partition of helper state and the origins permitted beyond the same origin."""

    credential_partition: str = field(repr=False)
    allowed_origins: tuple[Origin, ...] = ()

    def __post_init__(self) -> None:
        """Require a nonempty partition without control characters and copy the origins into a tuple."""
        _partition(self.credential_partition)
        object.__setattr__(self, "allowed_origins", _origins(self.allowed_origins))


@dataclass(frozen=True, slots=True, kw_only=True)
class CircuitBreakerOptions:
    """Circuit breaking of operations that declare a circuit group; off unless enabled.

    failure_threshold consecutive failed calls open a group's circuit for cooldown seconds, after which one probe call
    may close it again. Only one probe runs at a time, and no option changes that.
    """

    enabled: bool = False
    failure_threshold: int = 5
    cooldown: float = 30.0

    def __post_init__(self) -> None:
        """Require a boolean switch, a positive threshold, and a positive finite cooldown."""
        _instance(self.enabled, (bool,), "enabled")
        positive_count(self.failure_threshold, "failure_threshold")
        checked_seconds(self.cooldown, "cooldown")

    def resolved(self) -> ResolvedCircuitBreakerOptions:
        """Return the limits a circuit store applies."""
        return ResolvedCircuitBreakerOptions(failure_threshold=self.failure_threshold, cooldown=float(self.cooldown))


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedCircuitBreakerOptions:
    """The limits a circuit store applies to one admission: the opening threshold and the cooldown in seconds."""

    failure_threshold: int
    cooldown: float


class CircuitStore(Protocol):
    """A borrowed store of circuit states; each method is atomic, and `now` is the caller's monotonic time."""

    def admit(self, key: CircuitKey, *, now: float, options: ResolvedCircuitBreakerOptions) -> CircuitPermit:
        """Admit one call, taking the single half-open probe slot when due, or raise CircuitOpenError."""
        ...

    def record(self, permit: CircuitPermit, outcome: CircuitOutcome, *, now: float) -> None:
        """Apply a call's outcome once, ignoring a permit of another generation."""

    def reset(self, key: CircuitKey) -> None:
        """Close a circuit and advance its generation, so permits admitted before cannot change it."""

    def snapshot(self, key: CircuitKey) -> CircuitSnapshot:
        """Return the circuit's current state."""
        ...


class AsyncCircuitStore(Protocol):
    """A borrowed asynchronous store of circuit states with the same atomic contract."""

    async def admit(self, key: CircuitKey, *, now: float, options: ResolvedCircuitBreakerOptions) -> CircuitPermit:
        """Admit one call, taking the single half-open probe slot when due, or raise CircuitOpenError."""
        ...

    async def record(self, permit: CircuitPermit, outcome: CircuitOutcome, *, now: float) -> None:
        """Apply a call's outcome once, ignoring a permit of another generation."""

    async def reset(self, key: CircuitKey) -> None:
        """Close a circuit and advance its generation, so permits admitted before cannot change it."""

    async def snapshot(self, key: CircuitKey) -> CircuitSnapshot:
        """Return the circuit's current state."""
        ...


def _helper_defaults(value: object) -> Mapping[str, ProtocolDefaults]:
    if not _is_mapping(value):
        raise ProtocolConfigurationError(field_path=("defaults",), condition="invalid_value")
    defaults: dict[str, ProtocolDefaults] = {}
    for name, item in value.items():
        if not _is_helper_name(name):
            raise ProtocolConfigurationError(field_path=("defaults",), condition="invalid_value")
        if not isinstance(item, ProtocolDefaults):
            raise ProtocolConfigurationError(field_path=("defaults", name), condition="invalid_value")
        defaults[name] = item
    return MappingProxyType(defaults)


def _stores(value: object, field: str) -> Mapping[str, object]:
    if not _is_mapping(value):
        raise ProtocolConfigurationError(field_path=(field,), condition="invalid_value")
    stores: dict[str, object] = {}
    for name, store in value.items():
        if not _is_helper_name(name):
            raise ProtocolConfigurationError(field_path=(field,), condition="invalid_value")
        stores[name] = store
    return MappingProxyType(stores)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolDefaults:
    """Defaults of one helper, below its call arguments and above the kind's effective defaults."""

    session: SessionOptions | Unset = UNSET
    options: (
        PaginationOptions
        | PollOptions
        | StreamOptions
        | CacheOptions
        | WSOptions
        | UploadOptions
        | QueueOptions
        | Unset
    ) = UNSET

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
                QueueOptions,
                Unset,
            ),
            "options",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolClientOptions:
    """Protocol helper settings of one client: its security context, helper defaults, stores, and WebSockets.

    None as the security context means anonymous use. The defaults and the borrowed cache and queue stores are keyed by
    dotted helper names; each mapping is copied into a read-only one that keeps each value's identity. A WebSocket
    connector is borrowed and never closed; without one, the client opens its WebSocket connections itself.
    """

    security: ProtocolSecurityContext | Unset | None = UNSET
    defaults: Mapping[str, ProtocolDefaults] | Unset = UNSET
    cache_stores: Mapping[str, CacheStore | AsyncCacheStore] | Unset = UNSET
    websocket_connector: WebSocketConnector | AsyncWebSocketConnector | Unset | None = UNSET
    websocket_transport: WebSocketTransportOptions | Unset = UNSET
    circuit: CircuitBreakerOptions | Unset = UNSET
    circuit_store: CircuitStore | AsyncCircuitStore | Unset | None = field(default=UNSET, repr=False)
    queue_stores: Mapping[str, QueueStore | AsyncQueueStore] | Unset = UNSET

    def __post_init__(self) -> None:
        """Refuse another security value, helper names that are not dotted identifiers, and other default values.

        A connector needs an `open` method, and the transport settings their own type. The circuit store is only
        borrowed here; whether its methods suit the client's mode is checked by the client.
        """
        _instance(self.security, (ProtocolSecurityContext, Unset, type(None)), "security")
        if not isinstance(self.defaults, Unset):
            object.__setattr__(self, "defaults", _helper_defaults(self.defaults))
        _instance(self.circuit, (CircuitBreakerOptions, Unset), "circuit")
        if not isinstance(self.cache_stores, Unset):
            object.__setattr__(self, "cache_stores", _stores(self.cache_stores, "cache_stores"))
        if not isinstance(self.queue_stores, Unset):
            object.__setattr__(self, "queue_stores", _stores(self.queue_stores, "queue_stores"))
        connector = self.websocket_connector
        if (
            connector is not None
            and not isinstance(connector, Unset)
            and not callable(getattr(connector, "open", None))
        ):
            raise ProtocolConfigurationError(field_path=("websocket_connector",), condition="wrong_capability")
        _instance(self.websocket_transport, (WebSocketTransportOptions, Unset), "websocket_transport")


def checked_connector(connector: WebSocketConnector | AsyncWebSocketConnector, *, asynchronous: bool) -> None:
    """Refuse a WebSocket connector whose `open` is a coroutine function for a synchronous client, or the reverse."""
    if inspect.iscoroutinefunction(connector.open) is not asynchronous:
        raise ProtocolConfigurationError(field_path=("protocols", "websocket_connector"), condition="wrong_capability")


_KIND_OPTIONS: Final[Mapping[str, type]] = MappingProxyType({
    "pagination": PaginationOptions,
    "polling": PollOptions,
    "sse": StreamOptions,
    "ndjson": StreamOptions,
    "resumable_upload": UploadOptions,
    "cache": CacheOptions,
    "websocket": WSOptions,
    "queue": QueueOptions,
})


def checked_defaults(defaults: Mapping[str, ProtocolDefaults], helpers: tuple[tuple[str, str], ...]) -> None:
    """Refuse helper defaults for a helper the package lacks, or whose options belong to another kind.

    A cache helper runs no session, so its defaults take no session options.
    """
    kinds = dict(helpers)
    for name, item in defaults.items():
        if (kind := kinds.get(name)) is None:
            raise ProtocolConfigurationError(field_path=("protocols", "defaults", name), condition="unknown_field")
        if not isinstance(item.options, (Unset, _KIND_OPTIONS[kind])):
            raise ProtocolConfigurationError(
                field_path=("protocols", "defaults", name, "options"), condition="invalid_value"
            )
        if kind == "cache" and not isinstance(item.session, Unset):
            raise ProtocolConfigurationError(
                field_path=("protocols", "defaults", name, "session"), condition="invalid_value"
            )


def checked_stores(
    stores: Mapping[str, object],
    helpers: tuple[tuple[str, str], ...],
    *,
    asynchronous: bool,
    kind: Literal["cache", "queue"] = "cache",
) -> None:
    """Refuse a cache or queue store under a name that is no helper of its kind, or one the client cannot call.

    A store must have every method of its kind's store contract, coroutine functions for an asyncio client and plain
    functions for a synchronous one.
    """
    kinds, field = dict(helpers), f"{kind}_stores"
    for name, store in stores.items():
        if kinds.get(name) != kind:
            raise ProtocolConfigurationError(field_path=("protocols", field, name), condition="unknown_field")
        methods = [getattr(store, method, None) for method in _STORE_METHODS[kind]]
        if not all(callable(method) and iscoroutinefunction(method) == asynchronous for method in methods):
            raise ProtocolConfigurationError(field_path=("protocols", field, name), condition="wrong_capability")


def resolved_queue(layers: tuple[object, ...]) -> ResolvedQueueOptions:
    """Return queue options with each field from the first layer that sets it, or the kind's default.

    Retry delays out of order are refused as the maximum delay.
    """
    values = {name: layered(layers, name, getattr(QUEUE_DEFAULTS, name)) for name, *_ in QUEUE_FIELDS}
    if values["retry_max_delay"] < values["retry_initial_delay"]:
        raise ProtocolConfigurationError(field_path=("queue_options", "retry_max_delay"), condition="invalid_value")
    return ResolvedQueueOptions(**values)
