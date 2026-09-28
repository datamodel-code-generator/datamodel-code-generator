"""Client and request options: frozen values where UNSET inherits and each field's None has its own meaning."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping  # noqa: TC003 - Public annotations support get_type_hints().
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, TypeAlias
from urllib.parse import urlsplit

from typing_extensions import TypeIs

from ..model_codecs.unset import UNSET, Unset
from .errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ("UNSET", "ClientOptions", "HeaderPatch", "QueryPatch", "RequestOptions", "ServerSelection", "Unset")

HeaderPatch: TypeAlias = tuple[tuple[str, str | None], ...]
QueryPatch: TypeAlias = tuple[tuple[str, str | None], ...]

MAX_ERROR_BODY_LIMIT: Final = 1024 * 1024
_SCHEMES: Final = frozenset({"http", "https"})
_NAME: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_VALUE: Final = re.compile(r"[^\x00-\x08\x0a-\x1f\x7f]*")
_RESERVED: Final = frozenset({"host", "content-length", "transfer-encoding"})
_CODINGS: Final = frozenset({"identity", "gzip", "x-gzip", "deflate"})
_WEIGHT: Final = re.compile(r"[qQ]=(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)")
_PAIR: Final = 2


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


def _server(value: object) -> bool:
    return isinstance(value, (ServerSelection, Unset))


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
class _Options:
    server: ServerSelection | Unset = UNSET
    base_url: str | Unset = UNSET
    headers: HeaderPatch = ()
    query: QueryPatch = ()
    max_response_bytes: int | Unset | None = UNSET
    max_error_body_bytes: int | Unset = UNSET
    cleanup_timeout: float | Unset = UNSET
    max_stream_bytes: int | Unset | None = UNSET

    def __post_init__(self) -> None:
        if self.headers != ():
            object.__setattr__(self, "headers", _header_patch(self.headers))
        if self.query != ():
            object.__setattr__(self, "query", _query_patch(self.query))
        if not isinstance(self.server, Unset) and not isinstance(self.base_url, Unset):
            raise ConfigurationError(field_path=("base_url",), condition="conflicts_with_server")
        if not _server(self.server):
            raise ConfigurationError(field_path=("server",), condition="invalid_type")
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
    removes them.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestOptions(_Options):
    """Settings of one call; every field left UNSET inherits the client's.

    Its headers and query patch the lower layers' last: the client's, a view's, and those the call's parameters give.
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
