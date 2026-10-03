"""Client target settings and their flat TOML entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, TypeAlias, TypeVar

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import OperationRef
from datamodel_code_generator._client.naming import identifier, namespace_problem, token
from datamodel_code_generator._runtime.client.options import is_base_url
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type
from datamodel_code_generator._target_config import (
    Converter,
    TargetConfig,
    _array,  # pyright: ignore[reportPrivateUsage]
    _boolean,  # pyright: ignore[reportPrivateUsage]
    _ConfigValueError,  # pyright: ignore[reportPrivateUsage]
    _diagnostic,  # pyright: ignore[reportPrivateUsage]
    _operation,  # pyright: ignore[reportPrivateUsage]
    _path,  # pyright: ignore[reportPrivateUsage]
    _records,  # pyright: ignore[reportPrivateUsage]
    _string,  # pyright: ignore[reportPrivateUsage]
    _strings,  # pyright: ignore[reportPrivateUsage]
    _table,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._api_types import Diagnostic, OperationSelector
    from datamodel_code_generator._client.protocols import ProtocolConfiguration
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation

Transport: TypeAlias = Literal["httpx2"]
SignatureStyle: TypeAlias = Literal["explicit", "unpack"]
BodyArguments: TypeAlias = Literal["body", "both"]
RecordT = TypeVar("RecordT")

_LOCATIONS: Final = frozenset({"path", "query", "querystring", "header", "cookie"})
_SIGNATURE_STYLES: Final = frozenset({"explicit", "unpack"})
_BODY_ARGUMENTS: Final = frozenset({"body", "both"})
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
class BodyFieldName:
    """Name the keyword argument of one field of a body media type, selected by the media and its wire property."""

    media_type: str
    name: str
    python_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class IdempotencyMetadata:
    """An API's explicit key header, replay guarantee, retention, and nonsecret scope."""

    header_name: str
    replay_safe_with_key: bool
    retention_seconds: float
    scope: str

    def __post_init__(self) -> None:
        if (value := _positive_seconds(self.retention_seconds)) is not None:
            object.__setattr__(self, "retention_seconds", value)


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeOperationMetadata:
    """Runtime facts of one operation that the source cannot declare."""

    request_id_header: str | None = None
    success_statuses: tuple[int, ...] = ()
    retry_safety: Literal["method_default", "idempotent", "never"] = "method_default"
    idempotency: IdempotencyMetadata | None = None
    retry_after_ms_header: str | None = None
    should_retry_header: str | None = None
    auth_challenge_less_401: bool = False
    circuit_group: str | None = None
    accepted_content_encodings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _tuple_of(self.accepted_content_encodings, str):
            object.__setattr__(
                self, "accepted_content_encodings", tuple(item.lower() for item in self.accepted_content_encodings)
            )


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
    body_arguments: BodyArguments | None = None
    body_field_names: tuple[BodyFieldName, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientGenerationConfig(TargetConfig):
    """Settings of one client target; model settings stay in the model configuration."""

    schema_version: Literal[1] = 1
    transport: Transport = "httpx2"
    resource_names: tuple[ResourceName, ...] = ()
    operations: tuple[ClientOperationConfig, ...] = ()
    signature_style: SignatureStyle = "explicit"
    body_arguments: BodyArguments = "body"
    default_base_url: str | None = None
    server_base_url: str | None = None
    protocols: Path | ProtocolConfiguration | None = None

    toml_converters: ClassVar[Mapping[str, Converter]]
    manifest_exclusions: ClassVar[frozenset[str]] = frozenset({"selection", "protocols"})

    def _problems(self) -> Iterator[Diagnostic]:
        yield from TargetConfig._problems(self)  # noqa: SLF001
        if type(self.schema_version) is not int or self.schema_version != 1:
            yield _diagnostic("E_CONFIG_VALUE", "schema_version", "schema_version must be 1")
        if self.transport != "httpx2":
            yield _diagnostic("E_CONFIG_VALUE", "transport", "transport must be 'httpx2'")
        yield from _resource_name_problems(self.resource_names)
        yield from _operation_problems(self.operations, self.body_arguments)
        if self.signature_style not in _SIGNATURE_STYLES:
            yield _diagnostic("E_CONFIG_VALUE", "signature_style", "signature_style must be 'explicit' or 'unpack'")
        if self.body_arguments not in _BODY_ARGUMENTS:
            yield _diagnostic("E_CONFIG_VALUE", "body_arguments", "body_arguments must be 'body' or 'both'")
        for name in ("default_base_url", "server_base_url"):
            if (value := getattr(self, name)) is not None and not absolute(value):
                yield _diagnostic(
                    "E_CONFIG_VALUE",
                    name,
                    f"{name} must be an absolute http or https URL without userinfo, query, or fragment",
                )
        if self.protocols is not None and not isinstance(self.protocols, Path):
            yield from _protocol_problems(self.protocols)


def _protocol_problems(value: object) -> Iterator[Diagnostic]:
    """Validate Python helper records, loading their definitions only for a configuration that has them."""
    from datamodel_code_generator._client.protocols import ProtocolConfiguration, protocol_problems  # noqa: PLC0415

    if isinstance(value, ProtocolConfiguration):
        yield from protocol_problems(value)
    else:
        yield _diagnostic("E_CONFIG_VALUE", "protocols", "protocols must be a Path, a ProtocolConfiguration, or None")


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


def _positive_seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except OverflowError:
        return None
    return seconds if isfinite(seconds) and seconds > 0 else None


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


def _operation_problems(value: object, default: object) -> Iterator[Diagnostic]:
    if not _tuple_of(value, ClientOperationConfig):
        yield _diagnostic("E_CONFIG_VALUE", "operations", "operations must be a tuple of ClientOperationConfig records")
        return
    for index, item in enumerate(value):
        yield from _operation_config_problems(item, f"operations[{index}]")
        yield from _field_name_problems(item, default, f"operations[{index}]")


def _field_name_problems(item: ClientOperationConfig, default: object, at: str) -> Iterator[Diagnostic]:
    """Refuse an invalid body mode, and field names that are malformed, repeated, or for a body-only operation."""
    if item.body_arguments is not None and item.body_arguments not in _BODY_ARGUMENTS:
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.body_arguments", "body_arguments must be 'body', 'both', or None")
    names = item.body_field_names
    if not _tuple_of(names, BodyFieldName):
        yield _diagnostic(
            "E_CONFIG_VALUE", f"{at}.body_field_names", "body_field_names must be a tuple of BodyFieldName"
        )
        return
    if names and (item.body_arguments or default) != "both":
        yield _diagnostic(
            "E_CONFIG_VALUE", f"{at}.body_field_names", "body_field_names need body_arguments 'both' for the operation"
        )
    seen: set[tuple[str, str]] = set()
    for index, name in enumerate(names):
        if not (_media(name.media_type) and _text(name.name) and identifier(name.python_name)):
            yield _diagnostic(
                "E_CONFIG_VALUE",
                f"{at}.body_field_names[{index}]",
                "A body field name needs a media type, a wire property, and an identifier",
            )
        elif (key := (normalize_media_type(name.media_type), name.name)) in seen:
            yield _diagnostic(
                "E_CONFIG_VALUE",
                f"{at}.body_field_names[{index}]",
                f"The {name.media_type} body field {name.name!r} is named twice",
            )
        else:
            seen.add(key)


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
    for name in ("request_id_header", "retry_after_ms_header", "should_retry_header"):
        if (value := getattr(runtime, name)) is not None and not token(value):
            yield _diagnostic("E_CONFIG_VALUE", f"{at}.{name}", f"{name} must be a header name")
    if not isinstance(runtime.retry_safety, str) or runtime.retry_safety not in {
        "method_default",
        "idempotent",
        "never",
    }:
        yield _diagnostic(
            "E_CONFIG_VALUE", f"{at}.retry_safety", "retry_safety must be 'method_default', 'idempotent', or 'never'"
        )
    if runtime.idempotency is not None:
        yield from _idempotency_problems(runtime.idempotency, f"{at}.idempotency")
    if type(runtime.auth_challenge_less_401) is not bool:
        yield _diagnostic(
            "E_CONFIG_VALUE", f"{at}.auth_challenge_less_401", "auth_challenge_less_401 must be a boolean"
        )
    yield from _encoding_problems(runtime.accepted_content_encodings, f"{at}.accepted_content_encodings")
    if runtime.circuit_group is not None and not _group(runtime.circuit_group):
        yield _diagnostic(
            "E_CONFIG_VALUE",
            f"{at}.circuit_group",
            "circuit_group must be a string with non-whitespace text and no control characters",
        )
    if (
        isinstance(runtime.retry_after_ms_header, str)
        and isinstance(runtime.should_retry_header, str)
        and runtime.retry_after_ms_header.lower() == runtime.should_retry_header.lower()
    ):
        yield _diagnostic(
            "E_CONFIG_CONFLICT", f"{at}.should_retry_header", "Retry control response headers must have distinct names"
        )
    statuses = runtime.success_statuses
    if not (
        _tuple_of(statuses, int)
        and all(type(status) is int and _MIN_REDIRECT <= status <= _MAX_REDIRECT for status in statuses)
        and len(set(statuses)) == len(statuses)
    ):
        yield _diagnostic(
            "E_CONFIG_VALUE", f"{at}.success_statuses", "success_statuses must be distinct statuses 300 to 399"
        )


def _encoding_problems(value: object, at: str) -> Iterator[Diagnostic]:
    if not _tuple_of(value, str) or not all(token(item) for item in value):
        yield _diagnostic("E_CONFIG_VALUE", at, "accepted_content_encodings must be a tuple of HTTP tokens")
    elif len(set(value)) != len(value):
        yield _diagnostic("E_CONFIG_CONFLICT", at, "accepted_content_encodings names a coding twice")
    elif any(item != "gzip" for item in value):
        yield _diagnostic("E_CONFIG_VALUE", at, "accepted_content_encodings may name only gzip, the builtin encoder")


def _group(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.isprintable()


def _idempotency_problems(value: object, at: str) -> Iterator[Diagnostic]:
    if not isinstance(value, IdempotencyMetadata):
        yield _diagnostic("E_CONFIG_VALUE", at, "idempotency must be an IdempotencyMetadata record")
        return
    if not token(value.header_name):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.header_name", "header_name must be a header name")
    if type(value.replay_safe_with_key) is not bool:
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.replay_safe_with_key", "replay_safe_with_key must be a boolean")
    if _positive_seconds(value.retention_seconds) is None:
        yield _diagnostic(
            "E_CONFIG_VALUE", f"{at}.retention_seconds", "retention_seconds must be positive finite seconds"
        )
    if not isinstance(value.scope, str) or not value.scope.strip():
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.scope", "scope must be a string containing non-whitespace text")


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
    table = _table(
        value,
        option_path,
        frozenset({
            "request_id_header",
            "success_statuses",
            "retry_safety",
            "idempotency",
            "retry_after_ms_header",
            "should_retry_header",
            "auth_challenge_less_401",
            "circuit_group",
            "accepted_content_encodings",
        }),
    )
    statuses = _array(table.get("success_statuses", []), f"{option_path}.success_statuses")
    if not all(type(status) is int for status in statuses):
        option = f"{option_path}.success_statuses"
        raise _ConfigValueError(option, "success_statuses must be integers")
    retry_safety = _string(table.get("retry_safety", "method_default"), base, f"{option_path}.retry_safety")
    if not _is_retry_safety(retry_safety):
        option = f"{option_path}.retry_safety"
        raise _ConfigValueError(option, "retry_safety must be 'method_default', 'idempotent', or 'never'")
    return RuntimeOperationMetadata(
        request_id_header=_optional(table, "request_id_header", base, option_path),
        success_statuses=tuple(status for status in statuses if type(status) is int),
        retry_safety=retry_safety,
        idempotency=_idempotency(table["idempotency"], base, f"{option_path}.idempotency")
        if "idempotency" in table
        else None,
        retry_after_ms_header=_optional(table, "retry_after_ms_header", base, option_path),
        should_retry_header=_optional(table, "should_retry_header", base, option_path),
        auth_challenge_less_401=_boolean(
            table.get("auth_challenge_less_401", False), base, f"{option_path}.auth_challenge_less_401"
        ),
        circuit_group=_optional(table, "circuit_group", base, option_path),
        accepted_content_encodings=_strings(
            table.get("accepted_content_encodings", []), base, f"{option_path}.accepted_content_encodings"
        ),
    )


def _is_retry_safety(value: str) -> TypeIs[Literal["method_default", "idempotent", "never"]]:
    return value in {"method_default", "idempotent", "never"}


def _idempotency(value: object, base: Path, option_path: str) -> IdempotencyMetadata:
    table = _table(value, option_path, frozenset({"header_name", "replay_safe_with_key", "retention_seconds", "scope"}))
    retention = _positive_seconds(table.get("retention_seconds"))
    if retention is None:
        option = f"{option_path}.retention_seconds"
        raise _ConfigValueError(option, "retention_seconds must be positive finite seconds")
    return IdempotencyMetadata(
        header_name=_string(table.get("header_name"), base, f"{option_path}.header_name"),
        replay_safe_with_key=_boolean(table.get("replay_safe_with_key"), base, f"{option_path}.replay_safe_with_key"),
        retention_seconds=retention,
        scope=_string(table.get("scope"), base, f"{option_path}.scope"),
    )


def _body_field_names(value: object, base: Path, option_path: str) -> tuple[BodyFieldName, ...]:
    names: list[BodyFieldName] = []
    for index, item in enumerate(_array(value, option_path)):
        at = f"{option_path}[{index}]"
        table = _table(item, at, frozenset({"media_type", "name", "python_name"}))
        names.append(
            BodyFieldName(
                media_type=_string(table.get("media_type"), base, f"{at}.media_type"),
                name=_string(table.get("name"), base, f"{at}.name"),
                python_name=_string(table.get("python_name"), base, f"{at}.python_name"),
            )
        )
    return tuple(names)


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
            "body_arguments",
            "body_field_names",
        }),
    )
    values: dict[str, Any] = {
        "body_arguments": _optional(table, "body_arguments", base, option_path),
        "body_field_names": _body_field_names(
            table.get("body_field_names", []), base, f"{option_path}.body_field_names"
        ),
    }
    return ClientOperationConfig(
        ref=_operation(table.get("ref"), base, f"{option_path}.ref"),
        resource=_optional(table, "resource", base, option_path),
        name=_optional(table, "name", base, option_path),
        parameter_names=_parameter_names(table.get("parameter_names", []), base, f"{option_path}.parameter_names"),
        request_media_type=_optional(table, "request_media_type", base, option_path),
        response_media_type=_optional(table, "response_media_type", base, option_path),
        runtime=_runtime(table.get("runtime", {}), base, f"{option_path}.runtime"),
        description=_optional(table, "description", base, option_path),
        **values,
    )


ClientGenerationConfig.toml_converters = MappingProxyType({
    "transport": _string,
    "resource_names": _resource_names,
    "operations": _records(_operation_config),
    "signature_style": _string,
    "body_arguments": _string,
    "default_base_url": _string,
    "server_base_url": _string,
    "protocols": _path,
})
