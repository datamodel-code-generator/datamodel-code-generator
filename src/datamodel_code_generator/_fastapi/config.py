"""FastAPI server target settings."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Final, Literal, TypeAlias

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import OperationRef
from datamodel_code_generator._fastapi.naming import explicit
from datamodel_code_generator._target_config import (
    TargetConfig,
    _diagnostic,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from datamodel_code_generator._api_types import Diagnostic, OperationSelector

Layout: TypeAlias = Literal["routers", "single"]
HandlerMode: TypeAlias = Literal["sync", "async"]
BodyMode: TypeAlias = Literal["typed", "request"]

_PARAMETER_LOCATIONS: Final = frozenset({"path", "query", "querystring", "header", "cookie"})
_LAYOUTS: Final = frozenset({"routers", "single"})
_HANDLER_MODES: Final = frozenset({"sync", "async"})
_BODY_MODES: Final = frozenset({"typed", "request"})
_MIN_STATUS: Final = 100
_MAX_STATUS: Final = 599


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponseChoice:
    """Name the declared response a bare handler return value takes."""

    status_code: int
    media_type: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class FastAPIConfig(TargetConfig):
    """Settings of one FastAPI server target; model settings stay in the model configuration."""

    _option_prefix: ClassVar[str] = "server"

    layout: Layout = "routers"
    handler_mode: HandlerMode = "sync"
    handler_modes: Mapping[OperationSelector, HandlerMode] = field(default_factory=lambda: MappingProxyType({}))
    include_request: bool = False
    body_mode: BodyMode = "typed"
    body_modes: Mapping[OperationSelector, BodyMode] = field(default_factory=lambda: MappingProxyType({}))
    primary_responses: Mapping[OperationSelector, ResponseChoice] = field(default_factory=lambda: MappingProxyType({}))
    operation_names: Mapping[OperationSelector, str] = field(default_factory=lambda: MappingProxyType({}))
    router_names: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    parameter_names: Mapping[OperationSelector, Mapping[str, str]] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        """Freeze the given mappings, then reject values the server settings do not allow."""
        for name in ("handler_modes", "body_modes", "primary_responses", "operation_names", "router_names"):
            object.__setattr__(self, name, _frozen(getattr(self, name)))
        names: object = self.parameter_names
        if _is_mapping(names):
            object.__setattr__(
                self, "parameter_names", MappingProxyType({key: _frozen(value) for key, value in names.items()})
            )
        TargetConfig.__post_init__(self)

    def _problems(self) -> Iterator[Diagnostic]:
        yield from TargetConfig._problems(self)  # noqa: SLF001
        if self.layout not in _LAYOUTS:
            yield _diagnostic("E_CONFIG_VALUE", "layout", "layout must be 'routers' or 'single'")
        if self.handler_mode not in _HANDLER_MODES:
            yield _diagnostic("E_CONFIG_VALUE", "handler_mode", "handler_mode must be 'sync' or 'async'")
        yield from _selector_problems(self.handler_modes, "handler_modes", lambda value: value in _HANDLER_MODES)
        if not _is_bool(self.include_request):
            yield _diagnostic("E_CONFIG_VALUE", "include_request", "include_request must be a boolean")
        if self.body_mode not in _BODY_MODES:
            yield _diagnostic("E_CONFIG_VALUE", "body_mode", "body_mode must be 'typed' or 'request'")
        yield from _selector_problems(self.body_modes, "body_modes", lambda value: value in _BODY_MODES)
        yield from _selector_problems(self.primary_responses, "primary_responses", _response_choice)
        yield from _selector_problems(self.operation_names, "operation_names", _identifier)
        yield from _selector_problems(self.parameter_names, "parameter_names", _parameter_names)
        if not _router_names_valid(self.router_names):
            yield _diagnostic("E_CONFIG_VALUE", "router_names", "router_names must map group keys to identifiers")


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _is_bool(value: object) -> TypeIs[bool]:
    return isinstance(value, bool)


def _frozen(value: object) -> object:
    return MappingProxyType(dict(value)) if _is_mapping(value) else value


def _identifier(value: object) -> bool:
    return isinstance(value, str) and explicit(value)


def _router_names_valid(value: object) -> bool:
    return _is_mapping(value) and all(isinstance(key, str) and _identifier(name) for key, name in value.items())


def _response_choice(value: object) -> bool:
    return (
        isinstance(value, ResponseChoice)
        and type(value.status_code) is int
        and _MIN_STATUS <= value.status_code <= _MAX_STATUS
        and _optional_text(value.media_type)
    )


def _optional_text(value: object) -> bool:
    return value is None or isinstance(value, str)


def _parameter_names(value: object) -> bool:
    return _is_mapping(value) and all(
        isinstance(key, str)
        and key.partition(":")[0] in _PARAMETER_LOCATIONS
        and key.partition(":")[2]
        and _identifier(name)
        for key, name in value.items()
    )


def _selector_problems(value: object, name: str, valid: Callable[[object], bool]) -> Iterator[Diagnostic]:
    if not _is_mapping(value):
        yield _diagnostic("E_CONFIG_VALUE", name, f"{name} must map operation selectors to values")
        return
    for key, item in value.items():
        if not isinstance(key, (str, OperationRef)) or not valid(item):
            yield _diagnostic("E_CONFIG_VALUE", name, f"{name} has an invalid entry")
            return
