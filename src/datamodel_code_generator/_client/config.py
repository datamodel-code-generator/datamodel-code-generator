"""Client target settings and their flat TOML entries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Final, Literal, TypeAlias, TypeVar

from typing_extensions import TypeIs

from datamodel_code_generator._client.naming import identifier, namespace_problem
from datamodel_code_generator._codec_declarations import CodecAdapterRegistration, OperationRef
from datamodel_code_generator._runtime.client.options import is_base_url
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type
from datamodel_code_generator._target_config import (
    Converter,
    TargetConfig,
    _array,  # pyright: ignore[reportPrivateUsage]
    _ConfigValueError,  # pyright: ignore[reportPrivateUsage]
    _diagnostic,  # pyright: ignore[reportPrivateUsage]
    _operation,  # pyright: ignore[reportPrivateUsage]
    _records,  # pyright: ignore[reportPrivateUsage]
    _string,  # pyright: ignore[reportPrivateUsage]
    _table,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from datamodel_code_generator._api_types import Diagnostic, OperationSelector
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation

Transport: TypeAlias = Literal["httpx2"]
RecordT = TypeVar("RecordT")

_LOCATIONS: Final = frozenset({"path", "query", "querystring", "header", "cookie"})
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_MIN_REDIRECT: Final = 300
_MAX_REDIRECT: Final = 399


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceName:
    """Map one source tag to the resource namespace its operations join."""

    tag: str
    namespace: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterName:
    """Name the Python argument of one effective parameter, selected by its declared location and name."""

    in_: ParameterLocation
    name: str
    python_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeOperationMetadata:
    """Runtime facts of one operation that the source cannot declare."""

    request_id_header: str | None = None
    success_statuses: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientOperationConfig:
    """Settings of one operation, selected by its root use-site reference."""

    ref: OperationSelector
    resource: str | None = None
    name: str | None = None
    parameter_names: tuple[ParameterName, ...] = ()
    request_media_type: str | None = None
    response_media_type: str | None = None
    runtime: RuntimeOperationMetadata = field(default_factory=RuntimeOperationMetadata)
    description: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientGenerationConfig(TargetConfig):
    """Settings of one client target; model settings stay in the model configuration."""

    schema_version: Literal[1] = 1
    transport: Transport = "httpx2"
    resource_names: tuple[ResourceName, ...] = ()
    operations: tuple[ClientOperationConfig, ...] = ()
    default_base_url: str | None = None
    server_base_url: str | None = None
    codec_adapters: tuple[CodecAdapterRegistration, ...] = ()

    toml_converters: ClassVar[Mapping[str, Converter]]

    def _problems(self) -> Iterator[Diagnostic]:
        yield from TargetConfig._problems(self)  # noqa: SLF001
        if type(self.schema_version) is not int or self.schema_version != 1:
            yield _diagnostic("E_CONFIG_VALUE", "schema_version", "schema_version must be 1")
        if self.transport != "httpx2":
            yield _diagnostic("E_CONFIG_VALUE", "transport", "transport must be 'httpx2'")
        yield from _resource_name_problems(self.resource_names)
        yield from _operation_problems(self.operations)
        for name in ("default_base_url", "server_base_url"):
            if (value := getattr(self, name)) is not None and not absolute(value):
                yield _diagnostic(
                    "E_CONFIG_VALUE",
                    name,
                    f"{name} must be an absolute http or https URL without userinfo, query, or fragment",
                )
        if not _tuple_of(self.codec_adapters, CodecAdapterRegistration):
            yield _diagnostic("E_CONFIG_VALUE", "codec_adapters", "codec_adapters must be a tuple of registrations")


def absolute(value: object) -> bool:
    """Return whether a value is a base URL that generated clients accept, as their runtime checks it."""
    try:
        return isinstance(value, str) and is_base_url(value)
    except ValueError:
        return False


def _media(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        normalize_media_type(value)
    except ValueError:
        return False
    return True


def token(value: object) -> bool:
    """Return whether a value is an HTTP token, such as a header name."""
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _tuple_of(value: object, kind: type[RecordT]) -> TypeIs[tuple[RecordT, ...]]:
    return _is_tuple(value) and all(isinstance(item, kind) for item in value)


def _text(value: object) -> bool:
    return isinstance(value, str)


def _resource_name_problems(value: object) -> Iterator[Diagnostic]:
    if not _tuple_of(value, ResourceName):
        yield _diagnostic("E_CONFIG_VALUE", "resource_names", "resource_names must be a tuple of ResourceName records")
        return
    seen: set[str] = set()
    for index, item in enumerate(value):
        at = f"resource_names[{index}]"
        if not _text(item.tag):
            yield _diagnostic("E_CONFIG_VALUE", f"{at}.tag", "A resource name needs its source tag")
        elif item.tag in seen:
            yield _diagnostic("E_CONFIG_CONFLICT", f"{at}.tag", f"The tag {item.tag!r} is named twice")
        else:
            seen.add(item.tag)
        if (problem := namespace_problem(item.namespace)) is not None:
            yield _diagnostic("E_CONFIG_VALUE", f"{at}.namespace", problem)


def _operation_problems(value: object) -> Iterator[Diagnostic]:
    if not _tuple_of(value, ClientOperationConfig):
        yield _diagnostic("E_CONFIG_VALUE", "operations", "operations must be a tuple of ClientOperationConfig records")
        return
    for index, item in enumerate(value):
        yield from _operation_config_problems(item, f"operations[{index}]")


def _operation_config_problems(item: ClientOperationConfig, at: str) -> Iterator[Diagnostic]:
    if not _selector(item.ref):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.ref", "ref must be an OperationRef or a pointer string")
    if item.resource is not None and (problem := namespace_problem(item.resource)) is not None:
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.resource", problem)
    if item.name is not None and not identifier(item.name):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.name", "name must be a lowercase ASCII identifier")
    yield from _parameter_name_problems(item.parameter_names, f"{at}.parameter_names")
    for name in ("request_media_type", "response_media_type"):
        if (media := getattr(item, name)) is not None and not _media(media):
            yield _diagnostic("E_CONFIG_VALUE", f"{at}.{name}", f"{name} must be a media type")
    yield from _runtime_problems(item.runtime, f"{at}.runtime")
    if item.description is not None and not _text(item.description):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.description", "description must be a string")


def _selector(value: object) -> bool:
    return isinstance(value, (str, OperationRef))


def _runtime_problems(runtime: object, at: str) -> Iterator[Diagnostic]:
    if not isinstance(runtime, RuntimeOperationMetadata):
        yield _diagnostic("E_CONFIG_VALUE", at, "runtime must be a RuntimeOperationMetadata record")
        return
    if runtime.request_id_header is not None and not token(runtime.request_id_header):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.request_id_header", "request_id_header must be a header name")
    statuses = runtime.success_statuses
    if not (
        _tuple_of(statuses, int)
        and all(type(status) is int and _MIN_REDIRECT <= status <= _MAX_REDIRECT for status in statuses)
        and len(set(statuses)) == len(statuses)
    ):
        yield _diagnostic(
            "E_CONFIG_VALUE", f"{at}.success_statuses", "success_statuses must be distinct statuses 300 to 399"
        )


def _parameter_name_problems(value: object, at: str) -> Iterator[Diagnostic]:
    if not _tuple_of(value, ParameterName):
        yield _diagnostic("E_CONFIG_VALUE", at, "parameter_names must be a tuple of ParameterName records")
        return
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        if item.in_ not in _LOCATIONS or not _text(item.name) or not identifier(item.python_name):
            yield _diagnostic(
                "E_CONFIG_VALUE", f"{at}[{index}]", "A parameter name needs a location, a wire name, and an identifier"
            )
        elif (key := (item.in_, item.name)) in seen:
            yield _diagnostic(
                "E_CONFIG_CONFLICT", f"{at}[{index}]", f"The {item.in_} parameter {item.name!r} is named twice"
            )
        else:
            seen.add(key)


def _resource_names(value: object, base: Path, option_path: str) -> tuple[ResourceName, ...]:
    names: list[ResourceName] = []
    for index, item in enumerate(_array(value, option_path)):
        at = f"{option_path}[{index}]"
        table = _table(item, at, frozenset({"tag", "namespace"}))
        names.append(
            ResourceName(
                tag=_string(table.get("tag"), base, f"{at}.tag"),
                namespace=_string(table.get("namespace"), base, f"{at}.namespace"),
            )
        )
    return tuple(names)


def _optional(table: Mapping[str, object], key: str, base: Path, at: str) -> str | None:
    return None if (value := table.get(key)) is None else _string(value, base, f"{at}.{key}")


def _parameter_names(value: object, base: Path, option_path: str) -> tuple[ParameterName, ...]:
    names: list[ParameterName] = []
    for index, item in enumerate(_array(value, option_path)):
        at = f"{option_path}[{index}]"
        table = _table(item, at, frozenset({"in", "name", "python_name"}))
        location = _string(table.get("in"), base, f"{at}.in")
        if not _is_location(location):
            option = f"{at}.in"
            raise _ConfigValueError(option, "in must be path, query, querystring, header, or cookie")
        names.append(
            ParameterName(
                in_=location,
                name=_string(table.get("name"), base, f"{at}.name"),
                python_name=_string(table.get("python_name"), base, f"{at}.python_name"),
            )
        )
    return tuple(names)


def _is_location(value: str) -> TypeIs[ParameterLocation]:
    return value in _LOCATIONS


def _runtime(value: object, base: Path, option_path: str) -> RuntimeOperationMetadata:
    table = _table(value, option_path, frozenset({"request_id_header", "success_statuses"}))
    statuses = _array(table.get("success_statuses", []), f"{option_path}.success_statuses")
    if not all(type(status) is int for status in statuses):
        option = f"{option_path}.success_statuses"
        raise _ConfigValueError(option, "success_statuses must be integers")
    return RuntimeOperationMetadata(
        request_id_header=_optional(table, "request_id_header", base, option_path),
        success_statuses=tuple(status for status in statuses if type(status) is int),
    )


def _operation_config(value: object, base: Path, option_path: str) -> ClientOperationConfig:
    table = _table(
        value,
        option_path,
        frozenset({
            "ref",
            "resource",
            "name",
            "parameter_names",
            "request_media_type",
            "response_media_type",
            "runtime",
            "description",
        }),
    )
    return ClientOperationConfig(
        ref=_operation(table.get("ref"), base, f"{option_path}.ref"),
        resource=_optional(table, "resource", base, option_path),
        name=_optional(table, "name", base, option_path),
        parameter_names=_parameter_names(table.get("parameter_names", []), base, f"{option_path}.parameter_names"),
        request_media_type=_optional(table, "request_media_type", base, option_path),
        response_media_type=_optional(table, "response_media_type", base, option_path),
        runtime=_runtime(table.get("runtime", {}), base, f"{option_path}.runtime"),
        description=_optional(table, "description", base, option_path),
    )


ClientGenerationConfig.toml_converters = MappingProxyType({
    "transport": _string,
    "resource_names": _resource_names,
    "operations": _records(_operation_config),
    "default_base_url": _string,
    "server_base_url": _string,
})
