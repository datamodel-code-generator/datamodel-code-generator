"""Client and request options: frozen values where UNSET inherits and each field's None has its own meaning."""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from ssl import SSLContext
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeVar
from urllib.parse import urlsplit
from uuid import uuid4

from typing_extensions import TypeIs

from ..model_codecs.media import encode_json
from ..model_codecs.unset import UNSET, Unset
from ..model_codecs.wire import JSONScalar  # noqa: TC001 - Public annotations support get_type_hints().
from .errors import ConfigurationError
from .hooks import AsyncHook, AsyncLimiter, Hook, Limiter  # noqa: TC001 - Public annotations support get_type_hints().
from .timing import CancelToken, Deadline, ResolvedTimeoutOptions, seconds

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = (
    "UNSET",
    "CancelToken",
    "ClientOptions",
    "Deadline",
    "HeaderPatch",
    "IdempotencyKey",
    "QueryPatch",
    "RedirectOptions",
    "RequestOptions",
    "RetryOptions",
    "ServerSelection",
    "TimeoutOptions",
    "TransportOptions",
    "Unset",
    "ValidationOptions",
)

HeaderPatch: TypeAlias = tuple[tuple[str, str | None], ...]
QueryPatch: TypeAlias = tuple[tuple[str, str | None], ...]
RequestValidation: TypeAlias = Literal["none", "native", "schema"]
ResponseValidation: TypeAlias = Literal["native", "schema"]
ArgumentValidation: TypeAlias = Literal["none", "pydantic"]

MAX_ERROR_BODY_LIMIT: Final = 1024 * 1024
MAX_CONTEXT_BYTES: Final = 8 * 1024
NO_CONTEXT: Final[Mapping[str, JSONScalar]] = MappingProxyType({})
_SCHEMES: Final = frozenset({"http", "https"})
_ORIGIN: Final = re.compile(r"https?://[^\s/?#\\\x00-\x1f\x7f]+", re.IGNORECASE)
_NAME: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_VALUE: Final = re.compile(r"[^\x00-\x08\x0a-\x1f\x7f]*")
_RESERVED: Final = frozenset({"host", "content-length", "transfer-encoding"})
_CODINGS: Final = frozenset({"identity", "gzip", "x-gzip", "deflate"})
_WEIGHT: Final = re.compile(r"[qQ]=(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)")
_PAIR: Final = 2
_MODES: Final = (
    ("request", frozenset({"none", "native", "schema"})),
    ("response", frozenset({"native", "schema"})),
    ("arguments", frozenset({"none", "pydantic"})),
)
_JITTER: Final = frozenset({"full", "none"})
_RETRY_OWNERS: Final = frozenset({"sdk", "transport"})
_RETRY_STATUS_MIN: Final = 400
_RETRY_STATUS_MAX: Final = 599
_RETRY_STATUS_EXCLUDED: Final = frozenset({401, 403, 407})
_OptionT = TypeVar("_OptionT")


def _count(value: object, path: tuple[str, ...], *, minimum: int = 0, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ConfigurationError(field_path=path, condition="out_of_range")


def _positive_seconds(value: object, path: tuple[str, ...]) -> None:
    match value:
        case bool():
            pass
        case int() | float() if 0 < value < math.inf:
            return
        case _:
            pass
    raise ConfigurationError(field_path=path, condition="out_of_range")


def _header_patch(value: object) -> HeaderPatch:
    """Return a copy of a header patch, refusing reserved or malformed headers and a name both set and removed."""
    patch = _patch(value, "headers")
    for name, text in patch:
        match name.lower(), text:
            case _ if not _NAME.fullmatch(name) or (text is not None and not _VALUE.fullmatch(text)):
                raise ConfigurationError(field_path=("headers", name), condition="invalid_value")
            case key, _ if key in _RESERVED:
                raise ConfigurationError(field_path=("headers", name), condition="reserved")
            case "accept-encoding", str() if not _accepted(text):
                raise ConfigurationError(field_path=("headers", name), condition="unsupported_coding")
            case _:
                pass
    _settled(patch, "headers", str.lower)
    return patch


def _query_patch(value: object) -> QueryPatch:
    """Return a copy of a query patch, refusing an empty name and a name both set and removed."""
    patch = _patch(value, "query")
    if not all(name for name, _ in patch):
        raise ConfigurationError(field_path=("query",), condition="invalid_value")
    _settled(patch, "query", str)
    return patch


def _patch(value: object, field: str) -> tuple[tuple[str, str | None], ...]:
    if not _is_sequence(value):
        raise ConfigurationError(field_path=(field,), condition="invalid_type")
    return tuple(_pair(item, field) for item in value)


def _is_sequence(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    return isinstance(value, (tuple, list))


def _pair(item: object, field: str) -> tuple[str, str | None]:
    if _is_sequence(item) and len(item) == _PAIR:
        match item[0], item[1]:
            case str() as name, (str() | None) as text:
                return name, text
            case _:
                pass
    raise ConfigurationError(field_path=(field,), condition="invalid_type")


def _hooks(value: object) -> tuple[Hook | AsyncHook, ...]:
    """Return a copy of a tuple of hooks, refusing anything that has no on_event method."""
    if not _is_sequence(value) or len(hooks := tuple(hook for hook in value if _is_hook(hook))) != len(value):
        raise ConfigurationError(field_path=("hooks",), condition="invalid_type")
    return hooks


def _is_hook(value: object) -> TypeIs[Hook | AsyncHook]:
    return callable(getattr(value, "on_event", None))


def awaited(hooks: tuple[Hook | AsyncHook, ...]) -> bool:
    """Return whether any of the hooks is asynchronous, which only an asyncio client can await."""
    return any(inspect.iscoroutinefunction(hook.on_event) for hook in hooks)


def context(value: object) -> Mapping[str, JSONScalar]:
    """Return a copy of a call context, refusing a context of other names or values, or over 8 KiB of JSON.

    Its names are strings and its values JSON scalars.
    """
    if not _is_context(value):
        raise ConfigurationError(field_path=("context",), condition="invalid_type")
    copied: Mapping[str, JSONScalar] = MappingProxyType(dict(value))
    if len(encode_json(copied)) > MAX_CONTEXT_BYTES:
        raise ConfigurationError(field_path=("context",), condition="out_of_range")
    return copied


def _is_context(value: object) -> TypeIs[Mapping[str, JSONScalar]]:
    return _is_mapping(value) and all(type(key) is str and _scalar(item) for key, item in value.items())


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _scalar(value: object) -> bool:
    match value:
        case None | bool() | str() | Decimal():
            return True
        case int():
            return True
        case float():
            return math.isfinite(value)
        case _:
            return False


def _settled(patch: tuple[tuple[str, str | None], ...], field: str, fold: Callable[[str], str]) -> None:
    """Refuse a patch that both sets and removes one name."""
    removed = {fold(name) for name, text in patch if text is None}
    if any(fold(name) in removed for name, text in patch if text is not None):
        raise ConfigurationError(field_path=(field,), condition="set_and_removed")


def _accepted(value: str) -> bool:
    """Return whether an Accept-Encoding value names each coding a client decodes once, with a valid weight."""
    seen: set[str] = set()
    for element in value.split(","):
        coding, *weights = (part.strip() for part in element.split(";"))
        if (key := coding.lower()) not in _CODINGS or key in seen or len(weights) > 1:
            return False
        if weights and not _WEIGHT.fullmatch(weights[0]):
            return False
        seen.add(key)
    return True


def _typed(value: object, kinds: tuple[type, ...], path: tuple[str, ...]) -> None:
    """Refuse an option value of another type."""
    if not isinstance(value, kinds):
        raise ConfigurationError(field_path=path, condition="invalid_type")


def is_base_url(value: str) -> bool:
    """Return whether a URL is absolute http or https, without userinfo, query, or fragment, and not on port 0.

    Raises ValueError when the URL or its port does not parse.
    """
    parts = urlsplit(value)
    absolute = parts.scheme in _SCHEMES and bool(parts.hostname) and parts.port != 0
    return absolute and parts.username is None and not parts.query and not parts.fragment


def checked_base_url(value: str, path: tuple[str, ...]) -> str:
    """Return a URL that is_base_url accepts, or raise ConfigurationError."""
    try:
        valid = is_base_url(value)
    except ValueError as error:
        raise ConfigurationError(field_path=path, condition="invalid_url", cause=error) from None
    if not valid:
        raise ConfigurationError(field_path=path, condition="invalid_url")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerSelection:
    """Choose one of the operation's declared servers by position and supply its variables."""

    index: int = 0
    variables: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        """Freeze the variables and reject a negative position or non-string values."""
        _count(self.index, ("server", "index"))
        variables = dict(self.variables)
        if not all(type(name) is str and type(value) is str for name, value in variables.items()):
            raise ConfigurationError(field_path=("server", "variables"), condition="invalid_value")
        object.__setattr__(self, "variables", MappingProxyType(variables))


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationOptions:
    """How calls validate what they send, what they receive, and their arguments; UNSET inherits the lower layer's.

    `request` is `none` to send native values as they serialize, `native` to pass them through their model backend's
    validation first, and `schema` to validate the serialized value against its schema. `response` is `native` to
    construct the declared type through its backend's converter and `schema` to validate the received value first.
    A package allows the modes it was generated with; selecting another raises ConfigurationError.
    """

    request: RequestValidation | Unset = UNSET
    response: ResponseValidation | Unset = UNSET
    arguments: ArgumentValidation | Unset = UNSET

    def __post_init__(self) -> None:
        """Refuse None and any mode that no package defines."""
        for name, modes in _MODES:
            if not isinstance(value := getattr(self, name), Unset) and not (type(value) is str and value in modes):
                raise ConfigurationError(field_path=("validation", name), condition="invalid_value")


@dataclass(frozen=True, slots=True)
class Validation:
    """The validation modes a call runs with."""

    request: RequestValidation
    response: ResponseValidation
    arguments: ArgumentValidation


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationModes:
    """The modes a package allows on each axis, its generated default first."""

    request: tuple[RequestValidation, ...] = ("none",)
    response: tuple[ResponseValidation, ...] = ("native",)
    arguments: tuple[ArgumentValidation, ...] = ("none",)

    def default(self) -> Validation:
        """Return the generated default of every axis."""
        return Validation(self.request[0], self.response[0], self.arguments[0])

    def layered(self, current: Validation, layer: ValidationOptions, operation_id: str | None = None) -> Validation:
        """Return the modes with a layer's set fields applied, refusing a mode the package does not allow."""
        for name, allowed in (("request", self.request), ("response", self.response), ("arguments", self.arguments)):
            if not isinstance(value := getattr(layer, name), Unset) and value not in allowed:
                raise ConfigurationError(
                    field_path=("validation", name), condition="not_allowed", operation_id=operation_id
                )
        return Validation(
            current.request if isinstance(layer.request, Unset) else layer.request,
            current.response if isinstance(layer.response, Unset) else layer.response,
            current.arguments if isinstance(layer.arguments, Unset) else layer.arguments,
        )


DEFAULT_VALIDATION: Final = ValidationModes()
DEFAULT_TIMEOUT: Final = ResolvedTimeoutOptions(connect=5.0, read=30.0, write=30.0, pool=5.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeoutOptions:
    """Phase timeout overrides in seconds; UNSET inherits and None disables only that phase."""

    connect: float | Unset | None = UNSET
    read: float | Unset | None = UNSET
    write: float | Unset | None = UNSET
    pool: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Refuse booleans, negative durations, and nonfinite durations."""
        for name in ("connect", "read", "write", "pool"):
            if (value := getattr(self, name)) is not None and not isinstance(value, Unset):
                object.__setattr__(self, name, seconds(value, ("timeout", name)))


def _choice(value: object, choices: frozenset[str], path: tuple[str, ...]) -> None:
    if type(value) is not str or value not in choices:
        raise ConfigurationError(field_path=path, condition="invalid_value")


def _is_set(value: object) -> TypeIs[AbstractSet[object]]:
    return isinstance(value, AbstractSet)


def _statuses(value: object) -> frozenset[int]:
    path = ("retry", "statuses")
    if not _is_set(value):
        raise ConfigurationError(field_path=path, condition="invalid_type")
    checked: set[int] = set()
    for item in value:
        if (
            type(item) is not int
            or not _RETRY_STATUS_MIN <= item <= _RETRY_STATUS_MAX
            or item in _RETRY_STATUS_EXCLUDED
        ):
            raise ConfigurationError(field_path=path, condition="out_of_range")
        checked.add(item)
    return frozenset(checked)


def _retry_header(value: object, name: str) -> None:
    if value is None or isinstance(value, Unset):
        return
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ConfigurationError(field_path=("retry", name), condition="invalid_value")


def _ordered_delays(initial: float | Unset, maximum: float | Unset, operation_id: str | None = None) -> None:
    if not isinstance(initial, Unset) and not isinstance(maximum, Unset) and maximum < initial:
        raise ConfigurationError(field_path=("retry", "max_delay"), condition="out_of_range", operation_id=operation_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryOptions:
    """Override retry limits, status selection, and delays; omitted fields inherit independently."""

    max_retries: int | Unset = UNSET
    initial_delay: float | Unset = UNSET
    max_delay: float | Unset = UNSET
    jitter: Literal["full", "none"] | Unset = UNSET
    statuses: AbstractSet[int] | Unset = UNSET
    max_retry_after: float | Unset | None = UNSET
    respect_retry_after: bool | Unset = UNSET
    retry_after_ms_header: str | Unset | None = UNSET
    should_retry_header: str | Unset | None = UNSET
    retry_on_pool_timeout: bool | Unset = UNSET

    def __post_init__(self) -> None:
        """Validate explicit overrides without resolving values inherited from another layer."""
        if not isinstance(self.max_retries, Unset):
            _count(self.max_retries, ("retry", "max_retries"))
        for name, value in (("initial_delay", self.initial_delay), ("max_delay", self.max_delay)):
            if not isinstance(value, Unset):
                object.__setattr__(self, name, seconds(value, ("retry", name)))
        _ordered_delays(self.initial_delay, self.max_delay)
        if not isinstance(self.jitter, Unset):
            _choice(self.jitter, _JITTER, ("retry", "jitter"))
        if not isinstance(self.statuses, Unset):
            object.__setattr__(self, "statuses", _statuses(self.statuses))
        if self.max_retry_after is not None and not isinstance(self.max_retry_after, Unset):
            path = ("retry", "max_retry_after")
            duration = seconds(self.max_retry_after, path)
            _positive_seconds(duration, path)
            object.__setattr__(self, "max_retry_after", duration)
        for name, value in (
            ("respect_retry_after", self.respect_retry_after),
            ("retry_on_pool_timeout", self.retry_on_pool_timeout),
        ):
            if not isinstance(value, Unset):
                _typed(value, (bool,), ("retry", name))
        _retry_header(self.retry_after_ms_header, "retry_after_ms_header")
        _retry_header(self.should_retry_header, "should_retry_header")


def _origin(value: object) -> str:
    path = ("redirects", "allowed_origins")
    if not isinstance(value, str):
        raise ConfigurationError(field_path=path, condition="invalid_type")
    if not _ORIGIN.fullmatch(value):
        raise ConfigurationError(field_path=path, condition="invalid_url")
    return checked_base_url(value, path)


def _origins(value: object) -> tuple[str, ...]:
    if not _is_sequence(value):
        raise ConfigurationError(field_path=("redirects", "allowed_origins"), condition="invalid_type")
    return tuple(_origin(item) for item in value)


@dataclass(frozen=True, slots=True, kw_only=True)
class RedirectOptions:
    """Override redirect admission; an empty origin allowlist permits only the initial origin."""

    enabled: bool | Unset = UNSET
    max_redirects: int | Unset = UNSET
    allow_303_to_get: bool | Unset = UNSET
    allowed_origins: tuple[str, ...] | Unset = UNSET
    allow_https_downgrade: bool | Unset = UNSET

    def __post_init__(self) -> None:
        """Freeze configured origins and reject invalid counts or nonboolean policy switches."""
        for name, value in (
            ("enabled", self.enabled),
            ("allow_303_to_get", self.allow_303_to_get),
            ("allow_https_downgrade", self.allow_https_downgrade),
        ):
            if not isinstance(value, Unset):
                _typed(value, (bool,), ("redirects", name))
        if not isinstance(self.max_redirects, Unset):
            _count(self.max_redirects, ("redirects", "max_redirects"))
        if not isinstance(self.allowed_origins, Unset):
            object.__setattr__(self, "allowed_origins", _origins(self.allowed_origins))


def _key_value(value: object) -> None:
    if not isinstance(value, str) or not value or any(char in value for char in ("\r", "\n", "\0")):
        raise ConfigurationError(field_path=("idempotency_key",), condition="invalid_value")


def _key_time(value: object) -> None:
    if value is None:
        return
    path = ("idempotency_key", "first_used_at")
    if not isinstance(value, datetime):
        raise ConfigurationError(field_path=path, condition="invalid_type")
    try:
        aware = value.utcoffset() is not None
    except (TypeError, ValueError, OverflowError) as error:
        raise ConfigurationError(field_path=path, condition="invalid_value", cause=error) from None
    if not aware:
        raise ConfigurationError(field_path=path, condition="invalid_value")


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """A stable key and its known first-use time; an unknown time cannot establish retry safety."""

    value: str = field(repr=False)
    first_used_at: datetime | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        """Reject empty or unsafe header values and timestamps without a timezone offset."""
        _key_value(self.value)
        _key_time(self.first_used_at)

    @staticmethod
    def new() -> IdempotencyKey:
        """Create a UUID4 key whose first-use time is now in UTC."""
        return IdempotencyKey(str(uuid4()), first_used_at=datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True, kw_only=True)
class TransportOptions:
    """Configure an SDK-created native transport; injected transports retain their construction settings."""

    verify: bool | Unset = UNSET
    ssl_context: SSLContext | None = None
    proxy: str | None = None
    trust_env: bool = False
    http2: bool = False
    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
    retry_owner: Literal["sdk", "transport"] = "sdk"

    def __post_init__(self) -> None:
        """Validate construction fields and reject any explicit verify alongside an SSLContext."""
        if not isinstance(self.verify, Unset):
            _typed(self.verify, (bool,), ("transport", "verify"))
        _typed(self.ssl_context, (SSLContext, type(None)), ("transport", "ssl_context"))
        if self.ssl_context is not None and not isinstance(self.verify, Unset):
            raise ConfigurationError(field_path=("transport", "verify"), condition="conflicts_with_ssl_context")
        _typed(self.proxy, (str, type(None)), ("transport", "proxy"))
        for name, enabled in (("trust_env", self.trust_env), ("http2", self.http2)):
            _typed(enabled, (bool,), ("transport", name))
        for name, count in (
            ("max_connections", self.max_connections),
            ("max_keepalive_connections", self.max_keepalive_connections),
        ):
            _count(count, ("transport", name))
        object.__setattr__(self, "keepalive_expiry", seconds(self.keepalive_expiry, ("transport", "keepalive_expiry")))
        _choice(self.retry_owner, _RETRY_OWNERS, ("transport", "retry_owner"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedRetryOptions:
    """Merged retry values, retaining vendor header inheritance until an operation is selected."""

    max_retries: int = 2
    initial_delay: float = 0.5
    max_delay: float = 8.0
    jitter: Literal["full", "none"] = "full"
    statuses: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
    max_retry_after: float | None = 60.0
    respect_retry_after: bool = True
    retry_after_ms_header: str | Unset | None = UNSET
    should_retry_header: str | Unset | None = UNSET
    retry_on_pool_timeout: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedRedirectOptions:
    """Merged redirect limits and permitted destinations."""

    enabled: bool = False
    max_redirects: int = 5
    allow_303_to_get: bool = False
    allowed_origins: tuple[str, ...] = ()
    allow_https_downgrade: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedTransportOptions:
    """Client construction settings after verify omission has been resolved."""

    verify: bool = True
    ssl_context: SSLContext | None = None
    proxy: str | None = None
    trust_env: bool = False
    http2: bool = False
    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 5.0
    retry_owner: Literal["sdk", "transport"] = "sdk"


DEFAULT_RETRY: Final = ResolvedRetryOptions()
DEFAULT_REDIRECTS: Final = ResolvedRedirectOptions()
DEFAULT_TRANSPORT: Final = ResolvedTransportOptions()


def _inherited(current: _OptionT, layer: _OptionT | Unset) -> _OptionT:
    return current if isinstance(layer, Unset) else layer


def layered_retry(
    current: ResolvedRetryOptions, layer: RetryOptions | Unset, operation_id: str | None = None
) -> ResolvedRetryOptions:
    """Apply each explicit retry field, then validate the resulting delay pair."""
    if isinstance(layer, Unset):
        return current
    initial_delay = _inherited(current.initial_delay, layer.initial_delay)
    max_delay = _inherited(current.max_delay, layer.max_delay)
    _ordered_delays(initial_delay, max_delay, operation_id)
    return ResolvedRetryOptions(
        max_retries=_inherited(current.max_retries, layer.max_retries),
        initial_delay=initial_delay,
        max_delay=max_delay,
        jitter=_inherited(current.jitter, layer.jitter),
        statuses=frozenset(_inherited(current.statuses, layer.statuses)),
        max_retry_after=_inherited(current.max_retry_after, layer.max_retry_after),
        respect_retry_after=_inherited(current.respect_retry_after, layer.respect_retry_after),
        retry_after_ms_header=_inherited(current.retry_after_ms_header, layer.retry_after_ms_header),
        should_retry_header=_inherited(current.should_retry_header, layer.should_retry_header),
        retry_on_pool_timeout=_inherited(current.retry_on_pool_timeout, layer.retry_on_pool_timeout),
    )


def layered_redirects(current: ResolvedRedirectOptions, layer: RedirectOptions | Unset) -> ResolvedRedirectOptions:
    """Apply each explicit redirect field without resetting the remaining policy."""
    if isinstance(layer, Unset):
        return current
    return ResolvedRedirectOptions(
        enabled=_inherited(current.enabled, layer.enabled),
        max_redirects=_inherited(current.max_redirects, layer.max_redirects),
        allow_303_to_get=_inherited(current.allow_303_to_get, layer.allow_303_to_get),
        allowed_origins=_inherited(current.allowed_origins, layer.allowed_origins),
        allow_https_downgrade=_inherited(current.allow_https_downgrade, layer.allow_https_downgrade),
    )


def resolve_transport_options(options: TransportOptions | Unset) -> ResolvedTransportOptions:
    """Resolve client-only construction options without creating an HTTP client or SSLContext."""
    if isinstance(options, Unset):
        return DEFAULT_TRANSPORT
    return ResolvedTransportOptions(
        verify=_inherited(DEFAULT_TRANSPORT.verify, options.verify),
        ssl_context=options.ssl_context,
        proxy=options.proxy,
        trust_env=options.trust_env,
        http2=options.http2,
        max_connections=options.max_connections,
        max_keepalive_connections=options.max_keepalive_connections,
        keepalive_expiry=options.keepalive_expiry,
        retry_owner=options.retry_owner,
    )


def _is_limiter(value: object) -> TypeIs[Limiter | AsyncLimiter]:
    return callable(getattr(value, "acquire", None))


@dataclass(frozen=True, slots=True, kw_only=True)
class _Options:
    server: ServerSelection | Unset = UNSET
    base_url: str | Unset = UNSET
    headers: HeaderPatch = ()
    query: QueryPatch = ()
    max_response_bytes: int | Unset | None = UNSET
    max_error_body_bytes: int | Unset = UNSET
    cleanup_timeout: float | Unset = UNSET
    max_stream_bytes: int | Unset | None = UNSET
    hooks: tuple[Hook | AsyncHook, ...] | Unset = UNSET
    context: Mapping[str, JSONScalar] | Unset = UNSET
    validation: ValidationOptions | Unset = UNSET
    timeout: TimeoutOptions | Unset | None = UNSET
    total_timeout: float | Unset | None = UNSET
    deadline: Deadline | Unset | None = UNSET
    cancel_token: CancelToken | Unset | None = UNSET
    limiter: Limiter | AsyncLimiter | Unset | None = field(default=UNSET, repr=False)
    max_network_sends: int | Unset | None = UNSET
    stream_idle_timeout: float | Unset | None = UNSET
    stream_total_timeout: float | Unset | None = UNSET
    retry: RetryOptions | Unset = UNSET
    redirects: RedirectOptions | Unset = UNSET
    idempotency_key: IdempotencyKey | Unset | None = UNSET

    def _check_timing(self) -> None:
        _typed(self.timeout, (TimeoutOptions, Unset, type(None)), ("timeout",))
        _typed(self.deadline, (Deadline, Unset, type(None)), ("deadline",))
        _typed(self.cancel_token, (CancelToken, Unset, type(None)), ("cancel_token",))
        if self.limiter is not None and not isinstance(self.limiter, Unset) and not _is_limiter(self.limiter):
            raise ConfigurationError(field_path=("limiter",), condition="invalid_type")
        for name in ("total_timeout", "stream_idle_timeout", "stream_total_timeout"):
            if (value := getattr(self, name)) is not None and not isinstance(value, Unset):
                object.__setattr__(self, name, seconds(value, (name,)))  # noqa: PLC2801 - Normalize frozen options.
        if self.max_network_sends is not None and not isinstance(self.max_network_sends, Unset):
            _count(self.max_network_sends, ("max_network_sends",))

    def __post_init__(self) -> None:
        self._check_timing()
        _typed(self.validation, (ValidationOptions, Unset), ("validation",))
        _typed(self.retry, (RetryOptions, Unset), ("retry",))
        _typed(self.redirects, (RedirectOptions, Unset), ("redirects",))
        _typed(self.idempotency_key, (IdempotencyKey, Unset, type(None)), ("idempotency_key",))
        if not isinstance(self.hooks, Unset):
            object.__setattr__(self, "hooks", _hooks(self.hooks))
        if not isinstance(self.context, Unset):
            object.__setattr__(self, "context", context(self.context))
        if self.headers != ():
            object.__setattr__(self, "headers", _header_patch(self.headers))
        if self.query != ():
            object.__setattr__(self, "query", _query_patch(self.query))
        if not isinstance(self.server, Unset) and not isinstance(self.base_url, Unset):
            raise ConfigurationError(field_path=("base_url",), condition="conflicts_with_server")
        _typed(self.server, (ServerSelection, Unset), ("server",))
        if not isinstance(self.base_url, Unset):
            if type(self.base_url) is not str:
                raise ConfigurationError(field_path=("base_url",), condition="invalid_type")
            checked_base_url(self.base_url, ("base_url",))
        if self.max_response_bytes is not None and not isinstance(self.max_response_bytes, Unset):
            _count(self.max_response_bytes, ("max_response_bytes",))
        if not isinstance(self.max_error_body_bytes, Unset):
            _count(self.max_error_body_bytes, ("max_error_body_bytes",), minimum=1, maximum=MAX_ERROR_BODY_LIMIT)
        if not isinstance(self.cleanup_timeout, Unset):
            _positive_seconds(self.cleanup_timeout, ("cleanup_timeout",))
        if self.max_stream_bytes is not None and not isinstance(self.max_stream_bytes, Unset):
            _count(self.max_stream_bytes, ("max_stream_bytes",))


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientOptions(_Options):
    """Settings of one client; every field left UNSET takes the generated default.

    Its headers and query patch the generated ones: each name it gives replaces their values of that name, and None
    removes them. Its hooks observe every call's events, with its context. Its validation replaces the generated mode
    of each axis it sets.
    """

    transport: TransportOptions | Unset = UNSET

    def __post_init__(self) -> None:
        """Validate ordinary options and the client-only construction settings."""
        _Options.__post_init__(self)
        _typed(self.transport, (TransportOptions, Unset), ("transport",))


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestOptions(_Options):
    """Settings of one call; every field left UNSET inherits the client's.

    Its headers and query patch the lower layers' last: the client's, a view's, and those the call's parameters give.
    Its hooks replace the lower layers' rather than adding to them, and its context replaces their values of the names
    it gives. Its validation replaces their mode of each axis it sets.
    """


@dataclass(frozen=True, slots=True)
class Settings:
    """The settings a call runs with: its client's or view's, with the call's options layered on them."""

    base_url: str | None
    server: ServerSelection
    max_response_bytes: int | None
    max_error_body_bytes: int
    cleanup_timeout: float
    max_stream_bytes: int | None
    headers: tuple[HeaderPatch, ...] = ()
    query: tuple[QueryPatch, ...] = ()
    hooks: tuple[Hook | AsyncHook, ...] = ()
    context: Mapping[str, JSONScalar] = field(default_factory=lambda: NO_CONTEXT)
    async_hooks: bool = False
    validation: Validation = field(default_factory=DEFAULT_VALIDATION.default)
    timeout: ResolvedTimeoutOptions = DEFAULT_TIMEOUT
    stream_read_timeout: float | None = None
    total_timeout: float | None = 60.0
    deadline: Deadline | None = None
    cancel_token: CancelToken | None = None
    limiter: Limiter | AsyncLimiter | None = field(default=None, repr=False)
    max_network_sends: int | Unset | None = UNSET
    stream_idle_timeout: float | None = 60.0
    stream_total_timeout: float | None = None
    retry: ResolvedRetryOptions = DEFAULT_RETRY
    redirects: ResolvedRedirectOptions = DEFAULT_REDIRECTS
    idempotency_key: IdempotencyKey | Unset | None = UNSET


def network_send_limit(settings: Settings) -> int | None:
    """Derive only an omitted send cap, after all retry and redirect fields have been merged."""
    if not isinstance(settings.max_network_sends, Unset):
        return settings.max_network_sends
    return 1 + settings.retry.max_retries + (settings.redirects.max_redirects if settings.redirects.enabled else 0)
