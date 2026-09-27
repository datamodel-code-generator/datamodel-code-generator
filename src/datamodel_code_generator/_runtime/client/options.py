"""Client and request options: frozen values where UNSET inherits and each field's None has its own meaning."""

from __future__ import annotations

import math
from collections.abc import Mapping  # noqa: TC003 - Public annotations support get_type_hints().
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit

from ..model_codecs.unset import UNSET, Unset
from .errors import ConfigurationError

__all__ = ("UNSET", "ClientOptions", "RequestOptions", "ServerSelection", "Unset")

MAX_ERROR_BODY_LIMIT: Final = 1024 * 1024
_SCHEMES: Final = frozenset({"http", "https"})


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
    max_response_bytes: int | Unset | None = UNSET
    max_error_body_bytes: int | Unset = UNSET
    cleanup_timeout: float | Unset = UNSET

    def __post_init__(self) -> None:
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


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientOptions(_Options):
    """Settings of one client; every field left UNSET takes the generated default."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestOptions(_Options):
    """Settings of one call; every field left UNSET inherits the client's."""
