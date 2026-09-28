"""Client and request options: frozen values where UNSET inherits and each field's None has its own meaning."""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import urlsplit

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
    "QueryPatch",
    "RequestOptions",
    "ServerSelection",
    "TimeoutOptions",
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


def _typed(value: object, kinds: tuple[type, ...], name: str) -> None:
    """Refuse an option value of another type."""
    if not isinstance(value, kinds):
        raise ConfigurationError(field_path=(name,), condition="invalid_type")


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

    def _check_timing(self) -> None:
        _typed(self.timeout, (TimeoutOptions, Unset, type(None)), "timeout")
        _typed(self.deadline, (Deadline, Unset, type(None)), "deadline")
        _typed(self.cancel_token, (CancelToken, Unset, type(None)), "cancel_token")
        if self.limiter is not None and not isinstance(self.limiter, Unset) and not _is_limiter(self.limiter):
            raise ConfigurationError(field_path=("limiter",), condition="invalid_type")
        for name in ("total_timeout", "stream_idle_timeout", "stream_total_timeout"):
            if (value := getattr(self, name)) is not None and not isinstance(value, Unset):
                object.__setattr__(self, name, seconds(value, (name,)))  # noqa: PLC2801 - Normalize frozen options.
        if self.max_network_sends is not None and not isinstance(self.max_network_sends, Unset):
            _count(self.max_network_sends, ("max_network_sends",))

    def __post_init__(self) -> None:
        self._check_timing()
        _typed(self.validation, (ValidationOptions, Unset), "validation")
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
        _typed(self.server, (ServerSelection, Unset), "server")
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
    max_network_sends: int | None = 1
    stream_idle_timeout: float | None = 60.0
    stream_total_timeout: float | None = None
