"""Shared single-target configuration and its flat TOML loader."""

from __future__ import annotations

import keyword
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal, TypeAlias, TypeVar

from datamodel_code_generator._api_manifest import document_identity
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef

ModelMode: TypeAlias = Literal["generate", "verify"]
Converter: TypeAlias = Callable[[object, Path, str], object]
ConfigT = TypeVar("ConfigT", bound="TargetConfig")

_MODEL_MODES: Final = frozenset({"generate", "verify"})


class _ConfigValueError(Exception):
    def __init__(
        self,
        option_path: str,
        message: str,
        *,
        code: Literal["E_CONFIG_VALUE", "E_CONFIG_UNKNOWN"] = "E_CONFIG_VALUE",
    ) -> None:
        self.option_path = option_path
        self.message = message
        self.code = code
        super().__init__(message)


def _diagnostic(code: str, option_path: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity="error", stage="config", message=message, option_path=option_path)


def _dotted(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(part.isidentifier() and not keyword.iskeyword(part) for part in value.split("."))
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetConfig:
    """Hold the settings every single target shares; concrete targets add their own fields."""

    output: Path
    package: str
    model_package: str
    model_mode: ModelMode = "generate"

    toml_converters: ClassVar[Mapping[str, Converter]] = MappingProxyType({})

    def __post_init__(self) -> None:
        """Reject values and combinations the shared contract does not allow, in field order."""
        if diagnostics := tuple(self._problems()):
            raise APIGenerationError(diagnostics)

    def _problems(self) -> Iterator[Diagnostic]:
        if not isinstance(self.output, Path):
            yield _diagnostic("E_CONFIG_VALUE", "output", "output must be a Path")
        for name in ("package", "model_package"):
            if not _dotted(getattr(self, name)):
                yield _diagnostic("E_CONFIG_VALUE", name, f"{name} must be a dotted Python import path")
        if self.model_mode not in _MODEL_MODES:
            yield _diagnostic("E_CONFIG_VALUE", "model_mode", "model_mode must be 'generate' or 'verify'")


def _string(value: object, _: Path, option_path: str) -> str:
    if not isinstance(value, str):
        raise _ConfigValueError(option_path, f"{option_path} must be a string")
    return value


def _boolean(value: object, _: Path, option_path: str) -> bool:
    if not isinstance(value, bool):
        raise _ConfigValueError(option_path, f"{option_path} must be a boolean")
    return value


def _path(value: object, base: Path, option_path: str) -> Path:
    return base / _string(value, base, option_path)


def _array(value: object, option_path: str) -> list[object]:
    if not isinstance(value, list):
        raise _ConfigValueError(option_path, f"{option_path} must be an array")
    return value


def _table(value: object, option_path: str, keys: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _ConfigValueError(option_path, f"{option_path} must be a table")
    if unknown := sorted(set(value) - keys):
        at = f"{option_path}.{unknown[0]}"
        raise _ConfigValueError(at, f"{option_path} has no key {unknown[0]!r}", code="E_CONFIG_UNKNOWN")
    return value


def _strings(value: object, base: Path, option_path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, base, f"{option_path}[{index}]") for index, item in enumerate(_array(value, option_path))
    )


def _reference(value: object, base: Path, option_path: str) -> tuple[str, str | None]:
    table = _table(value, option_path, frozenset({"pointer", "document"}))
    pointer = _string(table.get("pointer"), base, f"{option_path}.pointer")
    if (document := table.get("document")) is None:
        return pointer, None
    return pointer, document_identity(_string(document, base, f"{option_path}.document"), base)


def _operation(value: object, base: Path, option_path: str) -> OperationRef | str:
    if isinstance(value, str):
        return value
    pointer, document = _reference(value, base, option_path)
    return OperationRef(pointer=pointer, document=document)


def _records(convert: Converter) -> Converter:
    def records(value: object, base: Path, option_path: str) -> tuple[object, ...]:
        return tuple(
            convert(item, base, f"{option_path}[{index}]") for index, item in enumerate(_array(value, option_path))
        )

    return records


SHARED_TOML_CONVERTERS: Final[Mapping[str, Converter]] = MappingProxyType({
    "output": _path,
    "package": _string,
    "model_package": _string,
    "model_mode": _string,
})


def _convert(converter: Converter, value: object, base: Path, key: str) -> object:
    try:
        return converter(value, base, key)
    except _ConfigValueError as error:
        return _diagnostic(error.code, error.option_path, error.message)


def load_target_config(path: Path, config_type: type[ConfigT], *, output: Path | None = None) -> ConfigT:
    """Read one flat target file, resolving its paths against the file's directory."""
    from datamodel_code_generator.util import load_toml  # noqa: PLC0415

    try:
        data = load_toml(path)
    except ValueError as error:
        raise APIGenerationError((
            Diagnostic(code="E_CONFIG_VALUE", severity="error", stage="config", message="The target file is not TOML"),
        )) from error
    version = data.pop("schema_version", None)
    if type(version) is not int or version != 1:
        raise APIGenerationError((_diagnostic("E_CONFIG_VALUE", "schema_version", "schema_version must be 1"),))
    names = {item.name for item in fields(config_type)}
    converters = {**SHARED_TOML_CONVERTERS, **config_type.toml_converters}
    if unknown := [key for key in data if key not in names or key not in converters]:
        raise APIGenerationError(
            tuple(_diagnostic("E_CONFIG_UNKNOWN", key, f"The target file has no setting {key!r}") for key in unknown)
        )
    values: dict[str, Any] = {}
    diagnostics: list[Diagnostic] = []
    for key, value in data.items():
        match _convert(converters[key], value, path.parent, key):
            case Diagnostic() as problem:
                diagnostics.append(problem)
            case converted:
                values[key] = converted
    if output is not None:
        values["output"] = output
    reported = {item.option_path for item in diagnostics}
    diagnostics.extend(
        _diagnostic("E_CONFIG_VALUE", name, f"The target file needs {name}")
        for name in ("output", "package", "model_package")
        if name not in values and name not in reported
    )
    if diagnostics:
        raise APIGenerationError(tuple(diagnostics))
    return config_type(**values)
