"""FastAPI server target settings and their flat TOML entries."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Final, Literal, TypeAlias

from datamodel_code_generator._codec_declarations import CodecAdapterRegistration, OperationRef
from datamodel_code_generator._fastapi.naming import explicit
from datamodel_code_generator._target_config import (
    Converter,
    TargetConfig,
    _array,  # pyright: ignore[reportPrivateUsage]
    _boolean,  # pyright: ignore[reportPrivateUsage]
    _ConfigValueError,  # pyright: ignore[reportPrivateUsage]
    _diagnostic,  # pyright: ignore[reportPrivateUsage]
    _operation,  # pyright: ignore[reportPrivateUsage]
    _string,  # pyright: ignore[reportPrivateUsage]
    _table,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator._api_types import Diagnostic, OperationSelector

Layout: TypeAlias = Literal["routers", "single"]
BodyMode: TypeAlias = Literal["typed", "request"]

_PARAMETER_LOCATIONS: Final = frozenset({"path", "query", "querystring", "header", "cookie", "form", "file"})
_LAYOUTS: Final = frozenset({"routers", "single"})
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

    layout: Layout = "routers"
    include_request: bool = False
    body_mode: BodyMode = "typed"
    body_modes: Mapping[OperationSelector, BodyMode] = field(default_factory=lambda: MappingProxyType({}))
    primary_responses: Mapping[OperationSelector, ResponseChoice] = field(default_factory=lambda: MappingProxyType({}))
    operation_names: Mapping[OperationSelector, str] = field(default_factory=lambda: MappingProxyType({}))
    router_names: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    parameter_names: Mapping[OperationSelector, Mapping[str, str]] = field(default_factory=lambda: MappingProxyType({}))
    codec_adapters: tuple[CodecAdapterRegistration, ...] = ()

    toml_converters: ClassVar[Mapping[str, Converter]]

    def __post_init__(self) -> None:
        """Freeze the given mappings, then reject values the server settings do not allow."""
        for name in ("body_modes", "primary_responses", "operation_names", "router_names"):
            if isinstance(value := getattr(self, name), Mapping):
                object.__setattr__(self, name, MappingProxyType(dict(value)))
        if isinstance(self.parameter_names, Mapping):
            object.__setattr__(
                self,
                "parameter_names",
                MappingProxyType({
                    key: MappingProxyType(dict(names)) if isinstance(names, Mapping) else names
                    for key, names in self.parameter_names.items()
                }),
            )
        TargetConfig.__post_init__(self)

    def _problems(self) -> Iterator[Diagnostic]:
        yield from TargetConfig._problems(self)  # noqa: SLF001
        if self.layout not in _LAYOUTS:
            yield _diagnostic("E_CONFIG_VALUE", "layout", "layout must be 'routers' or 'single'")
        if not isinstance(self.include_request, bool):
            yield _diagnostic("E_CONFIG_VALUE", "include_request", "include_request must be a boolean")
        if self.body_mode not in _BODY_MODES:
            yield _diagnostic("E_CONFIG_VALUE", "body_mode", "body_mode must be 'typed' or 'request'")
        yield from _selector_problems(self.body_modes, "body_modes", lambda value: value in _BODY_MODES)
        yield from _selector_problems(self.primary_responses, "primary_responses", _response_choice)
        yield from _selector_problems(self.operation_names, "operation_names", _identifier)
        yield from _selector_problems(self.parameter_names, "parameter_names", _parameter_names)
        if not isinstance(self.router_names, Mapping) or not all(
            isinstance(key, str) and _identifier(value) for key, value in self.router_names.items()
        ):
            yield _diagnostic("E_CONFIG_VALUE", "router_names", "router_names must map group keys to identifiers")
        if not isinstance(self.codec_adapters, tuple) or not all(
            isinstance(item, CodecAdapterRegistration) for item in self.codec_adapters
        ):
            yield _diagnostic("E_CONFIG_VALUE", "codec_adapters", "codec_adapters must be a tuple of registrations")


def _identifier(value: object) -> bool:
    return isinstance(value, str) and explicit(value)


def _response_choice(value: object) -> bool:
    return (
        isinstance(value, ResponseChoice)
        and type(value.status_code) is int
        and _MIN_STATUS <= value.status_code <= _MAX_STATUS
        and (value.media_type is None or isinstance(value.media_type, str))
    )


def _parameter_names(value: object) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(key, str)
        and key.partition(":")[0] in _PARAMETER_LOCATIONS
        and key.partition(":")[2]
        and _identifier(name)
        for key, name in value.items()
    )


def _selector_problems(value: object, name: str, valid: Callable[[object], bool]) -> Iterator[Diagnostic]:
    if not isinstance(value, Mapping):
        yield _diagnostic("E_CONFIG_VALUE", name, f"{name} must map operation selectors to values")
        return
    for key, item in value.items():
        if not isinstance(key, (str, OperationRef)) or not valid(item):
            yield _diagnostic("E_CONFIG_VALUE", name, f"{name} has an invalid entry")
            return


def _selector_entries(
    value: object, base: Path, option_path: str, keys: frozenset[str]
) -> Iterator[tuple[OperationSelector, Mapping[str, object], str]]:
    for index, item in enumerate(_array(value, option_path)):
        at = f"{option_path}[{index}]"
        table = _table(item, at, frozenset({"operation", *keys}))
        yield _operation(table.get("operation"), base, f"{at}.operation"), table, at


def _body_modes(value: object, base: Path, option_path: str) -> Mapping[OperationSelector, str]:
    return {
        selector: _string(table.get("mode"), base, f"{at}.mode")
        for selector, table, at in _selector_entries(value, base, option_path, frozenset({"mode"}))
    }


def _primary_responses(value: object, base: Path, option_path: str) -> Mapping[OperationSelector, ResponseChoice]:
    choices: dict[OperationSelector, ResponseChoice] = {}
    for selector, table, at in _selector_entries(value, base, option_path, frozenset({"status_code", "media_type"})):
        status = table.get("status_code")
        if type(status) is not int:
            option = f"{at}.status_code"
            msg = f"{option} must be an integer"
            raise _ConfigValueError(option, msg)
        media = table.get("media_type")
        choices[selector] = ResponseChoice(
            status_code=status, media_type=None if media is None else _string(media, base, f"{at}.media_type")
        )
    return choices


def _operation_names(value: object, base: Path, option_path: str) -> Mapping[OperationSelector, str]:
    return {
        selector: _string(table.get("name"), base, f"{at}.name")
        for selector, table, at in _selector_entries(value, base, option_path, frozenset({"name"}))
    }


def _router_names(value: object, base: Path, option_path: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise _ConfigValueError(option_path, f"{option_path} must be a table")
    return {str(key): _string(name, base, f"{option_path}.{key}") for key, name in value.items()}


def _parameter_name_entries(
    value: object, base: Path, option_path: str
) -> Mapping[OperationSelector, Mapping[str, str]]:
    names: dict[OperationSelector, dict[str, str]] = {}
    for selector, table, at in _selector_entries(value, base, option_path, frozenset({"parameter", "name"})):
        names.setdefault(selector, {})[_string(table.get("parameter"), base, f"{at}.parameter")] = _string(
            table.get("name"), base, f"{at}.name"
        )
    return names


FastAPIConfig.toml_converters = MappingProxyType({
    "layout": _string,
    "include_request": _boolean,
    "body_mode": _string,
    "body_modes": _body_modes,
    "primary_responses": _primary_responses,
    "operation_names": _operation_names,
    "router_names": _router_names,
    "parameter_names": _parameter_name_entries,
})
