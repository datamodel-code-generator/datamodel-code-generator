"""Shared single-target configuration and its flat TOML loader."""

from __future__ import annotations

import codecs
import keyword
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, fields
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, TypeAlias, TypeVar

from typing_extensions import TypeIs

from datamodel_code_generator._api_manifest import document_identity
from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationSelection
from datamodel_code_generator._codec_declarations import (
    BuiltinCodecCompatibility,
    ModelExportBinding,
    OperationRef,
    SchemaRef,
)
from datamodel_code_generator._format_types import Formatter
from datamodel_code_generator.enums import DataModelType

if TYPE_CHECKING:
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue

ModelMode: TypeAlias = Literal["generate", "verify"]
Direction: TypeAlias = Literal["request", "response", "neutral"]
PackageMode: TypeAlias = Literal["embedded", "standalone"]
Converter: TypeAlias = Callable[[object, Path, str], object]
ConfigT = TypeVar("ConfigT", bound="TargetConfig")

_MODEL_MODES: Final = frozenset({"generate", "verify"})
_STANDALONE_FIELDS: Final = ("package_version", "distribution_name", "model_dependency")
_SELECTION_COLLECTIONS: Final = ("include_operations", "include_tags", "exclude_operations", "exclude_tags")


class _ConfigValueError(Exception):
    def __init__(self, option_path: str, message: str) -> None:
        self.option_path = option_path
        self.message = message
        super().__init__(message)


def _diagnostic(code: str, option_path: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity="error", stage="config", message=message, option_path=option_path)


def _dotted(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(part.isidentifier() and not keyword.iskeyword(part) for part in value.split("."))
    )


def _json(value: object) -> bool:
    match value:
        case None | bool() | int() | str():
            return True
        case float():
            return isfinite(value)
        case tuple() | list():
            return all(_json(item) for item in value)
        case Mapping():
            return all(type(key) is str and _json(item) for key, item in value.items())
    return False


def _selection_problems(selection: object) -> Iterator[Diagnostic]:
    if not isinstance(selection, OperationSelection):
        yield _diagnostic("E_CONFIG_VALUE", "selection", "selection must be an OperationSelection")
        return
    for name in _SELECTION_COLLECTIONS:
        values = getattr(selection, name)
        if not isinstance(values, tuple) or not all(isinstance(value, (str, OperationRef)) for value in values):
            yield _diagnostic("E_CONFIG_VALUE", f"selection.{name}", f"selection.{name} must be a tuple")
        elif len(set(values)) != len(values):
            yield _diagnostic("E_SELECTION", f"selection.{name}", f"selection.{name} repeats an entry")
    if any(getattr(selection, name) for name in _SELECTION_COLLECTIONS) and not (
        isinstance(selection.reason, str) and selection.reason.strip()
    ):
        yield _diagnostic("E_SELECTION", "selection.reason", "A selection needs a reason with non-whitespace text")


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetConfig:
    """Hold the settings every single target shares; concrete targets add their own fields."""

    output: Path
    package: str
    model_package: str
    model_mode: ModelMode = "generate"
    selection: OperationSelection = field(default_factory=OperationSelection)
    formatters: tuple[Formatter, ...] = (Formatter.BUILTIN,)
    formatter_settings: Path | None = None
    custom_formatters: tuple[str, ...] = ()
    custom_formatter_kwargs: Mapping[str, JSONValue] = field(default_factory=lambda: MappingProxyType({}))
    encoding: str = "utf-8"
    header: str | None = None
    include_timestamp: bool = False
    package_mode: PackageMode = "embedded"
    package_version: str | None = None
    distribution_name: str | None = None
    model_dependency: str | None = None
    builtin_codec_compatibility: tuple[BuiltinCodecCompatibility, ...] = ()
    export_bindings: tuple[ModelExportBinding, ...] = ()

    toml_converters: ClassVar[Mapping[str, Converter]] = MappingProxyType({})
    manifest_exclusions: ClassVar[frozenset[str]] = frozenset({"selection"})

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
        yield from _selection_problems(self.selection)
        if not isinstance(self.formatters, tuple) or not all(isinstance(item, Formatter) for item in self.formatters):
            yield _diagnostic("E_CONFIG_VALUE", "formatters", "formatters must be a tuple of Formatter values")
        if self.formatter_settings is not None and not isinstance(self.formatter_settings, Path):
            yield _diagnostic("E_CONFIG_VALUE", "formatter_settings", "formatter_settings must be a Path or None")
        if not isinstance(self.custom_formatters, tuple) or not all(_dotted(item) for item in self.custom_formatters):
            yield _diagnostic("E_CONFIG_VALUE", "custom_formatters", "custom_formatters must be module paths")
        if not isinstance(self.custom_formatter_kwargs, Mapping) or not _json(self.custom_formatter_kwargs):
            yield _diagnostic(
                "E_CONFIG_VALUE", "custom_formatter_kwargs", "custom_formatter_kwargs must be a JSON object"
            )
        yield from self._encoding_problems()
        if self.header is not None and not isinstance(self.header, str):
            yield _diagnostic("E_CONFIG_VALUE", "header", "header must be a string or None")
        if not isinstance(self.include_timestamp, bool):
            yield _diagnostic("E_CONFIG_VALUE", "include_timestamp", "include_timestamp must be a boolean")
        yield from self._package_problems()

    def _encoding_problems(self) -> Iterator[Diagnostic]:
        try:
            codecs.lookup(self.encoding)
        except (LookupError, TypeError):
            yield _diagnostic("E_CONFIG_VALUE", "encoding", "encoding must name a Python codec")

    def _package_problems(self) -> Iterator[Diagnostic]:
        match self.package_mode:
            case "standalone":
                for name in ("package_version", "distribution_name"):
                    if not (isinstance(value := getattr(self, name), str) and value.strip()):
                        yield _diagnostic("E_CONFIG_VALUE", name, f"A standalone package needs {name}")
            case "embedded":
                for name in _STANDALONE_FIELDS:
                    if getattr(self, name) is not None:
                        yield _diagnostic(
                            "E_CONFIG_CONFLICT", name, f"{name} applies only to package_mode='standalone'"
                        )
            case _:
                yield _diagnostic("E_CONFIG_VALUE", "package_mode", "package_mode must be 'embedded' or 'standalone'")


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
        raise _ConfigValueError(at, f"{option_path} has no key {unknown[0]!r}")
    return value


def _strings(value: object, base: Path, option_path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, base, f"{option_path}[{index}]") for index, item in enumerate(_array(value, option_path))
    )


def _formatters(value: object, base: Path, option_path: str) -> tuple[Formatter, ...]:
    try:
        return tuple(Formatter(item) for item in _strings(value, base, option_path))
    except ValueError:
        raise _ConfigValueError(option_path, f"{option_path} names an unknown formatter") from None


def _json_table(value: object, _: Path, option_path: str) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping) or not _json(value):
        raise _ConfigValueError(option_path, f"{option_path} must be a table of JSON values")
    return MappingProxyType(dict(value))


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


def _schema(value: object, base: Path, option_path: str) -> SchemaRef:
    pointer, document = _reference(value, base, option_path)
    return SchemaRef(pointer=pointer, document=document)


def _is_contract(value: str) -> TypeIs[Literal["builtin-v1"]]:
    return value == "builtin-v1"


def _is_direction(value: str) -> TypeIs[Direction]:
    return value in {"request", "response", "neutral"}


def _selection(value: object, base: Path, option_path: str) -> OperationSelection:
    table = _table(value, option_path, frozenset({*_SELECTION_COLLECTIONS, "reason"}))
    reason = table.get("reason")
    return OperationSelection(
        include_operations=tuple(
            _operation(item, base, f"{option_path}.include_operations[{index}]")
            for index, item in enumerate(
                _array(table.get("include_operations", []), f"{option_path}.include_operations")
            )
        ),
        include_tags=_strings(table.get("include_tags", []), base, f"{option_path}.include_tags"),
        exclude_operations=tuple(
            _operation(item, base, f"{option_path}.exclude_operations[{index}]")
            for index, item in enumerate(
                _array(table.get("exclude_operations", []), f"{option_path}.exclude_operations")
            )
        ),
        exclude_tags=_strings(table.get("exclude_tags", []), base, f"{option_path}.exclude_tags"),
        reason=None if reason is None else _string(reason, base, f"{option_path}.reason"),
    )


def _compatibility(value: object, base: Path, option_path: str) -> BuiltinCodecCompatibility:
    table = _table(
        value,
        option_path,
        frozenset({"name", "backend", "schemas", "contract", "dependencies", "python_requires"}),
    )
    at = f"{option_path}.contract"
    if not _is_contract(contract := _string(table.get("contract", "builtin-v1"), base, at)):
        raise _ConfigValueError(at, "The only compatibility contract is 'builtin-v1'")
    try:
        return BuiltinCodecCompatibility(
            name=_string(table.get("name"), base, f"{option_path}.name"),
            backend=DataModelType(_string(table.get("backend"), base, f"{option_path}.backend")),
            schemas=tuple(
                _schema(item, base, f"{option_path}.schemas[{index}]")
                for index, item in enumerate(_array(table.get("schemas", []), f"{option_path}.schemas"))
            ),
            contract=contract,
            dependencies=_strings(table.get("dependencies", []), base, f"{option_path}.dependencies"),
            python_requires=_string(table.get("python_requires", ">=3.10"), base, f"{option_path}.python_requires"),
        )
    except ValueError as error:
        raise _ConfigValueError(option_path, str(error)) from None


def _export(value: object, base: Path, option_path: str) -> ModelExportBinding:
    table = _table(value, option_path, frozenset({"schema", "module", "symbol", "direction"}))
    at = f"{option_path}.direction"
    if not _is_direction(direction := _string(table.get("direction", "neutral"), base, at)):
        raise _ConfigValueError(at, "direction must be 'request', 'response', or 'neutral'")
    try:
        return ModelExportBinding(
            schema=_schema(table.get("schema"), base, f"{option_path}.schema"),
            module=_string(table.get("module"), base, f"{option_path}.module"),
            symbol=_string(table.get("symbol"), base, f"{option_path}.symbol"),
            direction=direction,
        )
    except ValueError as error:
        raise _ConfigValueError(option_path, str(error)) from None


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
    "selection": _selection,
    "formatters": _formatters,
    "formatter_settings": _path,
    "custom_formatters": _strings,
    "custom_formatter_kwargs": _json_table,
    "encoding": _string,
    "header": _string,
    "include_timestamp": _boolean,
    "package_mode": _string,
    "package_version": _string,
    "distribution_name": _string,
    "model_dependency": _string,
    "builtin_codec_compatibility": _records(_compatibility),
    "export_bindings": _records(_export),
})


def _convert(converter: Converter, value: object, base: Path, key: str) -> object:
    try:
        return converter(value, base, key)
    except _ConfigValueError as error:
        return _diagnostic("E_CONFIG_VALUE", error.option_path, error.message)


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
    if "formatter_settings" not in data:
        values["formatter_settings"] = path.parent
    reported = {item.option_path for item in diagnostics}
    diagnostics.extend(
        _diagnostic("E_CONFIG_VALUE", name, f"The target file needs {name}")
        for name in ("output", "package", "model_package")
        if name not in values and name not in reported
    )
    if diagnostics:
        raise APIGenerationError(tuple(diagnostics))
    return config_type(**values)
