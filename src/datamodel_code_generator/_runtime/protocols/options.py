"""Helper limits, origins, the security context, and client protocol settings; UNSET takes the merged default."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from keyword import iskeyword
from sys import float_info
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, cast

from typing_extensions import TypeIs, TypeVar

from ..client.errors import ProtocolConfigurationError, is_sequence
from ..client.timing import SessionOptions
from ..model_codecs.unset import UNSET, Unset
from .records import record_string
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


def _origin(scheme: object, host: object, port: object) -> None:
    from ..client.urls import URLValidationError, canonical_origin, origin_text  # noqa: PLC0415 - Parse URLs only here.

    names = (record_string(scheme, "scheme"), record_string(host, "host"))
    if type(port) is not int:
        msg = "port must be an integer"
        raise TypeError(msg)
    origin = (*names, port)
    try:
        valid = canonical_origin.__wrapped__(origin_text.__wrapped__(origin)) == origin
    except URLValidationError:
        valid = False
    if not valid:
        msg = "origin must be the scheme, host, and port that the client's URL rules produce"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class Origin:
    """An effective HTTP origin exactly as the client's URL rules produce it; other spellings are rejected.

    The host is lowercase ASCII, with IDNA labels already encoded, and an IPv6 address has no brackets.
    """

    scheme: str
    host: str
    port: int

    def __post_init__(self) -> None:
        """Require the scheme, host, and effective port that interpreting this origin's URL gives back unchanged."""
        _origin(self.scheme, self.host, self.port)


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


def _proxy(value: object) -> None:
    if value is None or isinstance(value, Unset):
        return
    from urllib.parse import urlsplit  # noqa: PLC0415 - Only a given proxy is parsed.

    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        valid = parts is not None and parts.scheme in _PROXY_SCHEMES and bool(parts.hostname) and parts.port != 0
    except ValueError:
        valid = False
    if not valid:
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
        raise ProtocolConfigurationError(field_path=("defaults",), condition="invalid_value")
    defaults: dict[str, ProtocolDefaults] = {}
    for name, item in value.items():
        if not _is_helper_name(name):
            raise ProtocolConfigurationError(field_path=("defaults",), condition="invalid_value")
        if not isinstance(item, ProtocolDefaults):
            raise ProtocolConfigurationError(field_path=("defaults", name), condition="invalid_value")
        defaults[name] = item
    return MappingProxyType(defaults)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolDefaults:
    """Defaults of one helper, below its call arguments and above the kind's effective defaults."""

    session: SessionOptions | Unset = UNSET
    options: PaginationOptions | PollOptions | StreamOptions | WSOptions | Unset = UNSET

    def __post_init__(self) -> None:
        """Refuse values other than session options and one kind's options."""
        _instance(self.session, (SessionOptions, Unset), "session")
        _instance(self.options, (PaginationOptions, PollOptions, StreamOptions, WSOptions, Unset), "options")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtocolClientOptions:
    """Protocol helper settings of one client: its security context, each helper's defaults, and its WebSockets.

    None as the security context means anonymous use. The defaults are keyed by dotted helper names; the mapping is
    copied into a read-only one that keeps each value's identity. A WebSocket connector is borrowed and never closed;
    without one, the client opens its WebSocket connections itself.
    """

    security: ProtocolSecurityContext | Unset | None = UNSET
    defaults: Mapping[str, ProtocolDefaults] | Unset = UNSET
    websocket_connector: WebSocketConnector | AsyncWebSocketConnector | Unset | None = UNSET
    websocket_transport: WebSocketTransportOptions | Unset = UNSET

    def __post_init__(self) -> None:
        """Refuse another security value, helper names that are not dotted identifiers, and other default values.

        A connector needs an `open` method, and the transport settings their own type.
        """
        _instance(self.security, (ProtocolSecurityContext, Unset, type(None)), "security")
        if not isinstance(self.defaults, Unset):
            object.__setattr__(self, "defaults", _helper_defaults(self.defaults))
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
    "websocket": WSOptions,
})


def checked_defaults(defaults: Mapping[str, ProtocolDefaults], helpers: tuple[tuple[str, str], ...]) -> None:
    """Refuse helper defaults for a helper the package lacks, or whose options belong to another kind."""
    kinds = dict(helpers)
    for name, item in defaults.items():
        if (kind := kinds.get(name)) is None:
            raise ProtocolConfigurationError(field_path=("protocols", "defaults", name), condition="unknown_field")
        if not isinstance(item.options, (Unset, _KIND_OPTIONS[kind])):
            raise ProtocolConfigurationError(
                field_path=("protocols", "defaults", name, "options"), condition="invalid_value"
            )
