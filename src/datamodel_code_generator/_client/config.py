"""Client target settings and the JSON form of their per-operation entries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar, cast

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic, OperationRef
from datamodel_code_generator._client.naming import identifier, namespace_problem, token
from datamodel_code_generator._runtime.client.options import is_base_url
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type
from datamodel_code_generator._target_config import (
    TargetConfig,
    _diagnostic,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._api_types import OperationSelector
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
    """An API's explicit idempotency key header."""

    header_name: str


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

    transport: Transport = "httpx2"
    resource_names: tuple[ResourceName, ...] = ()
    operations: tuple[ClientOperationConfig, ...] = ()
    signature_style: SignatureStyle = "explicit"
    body_arguments: BodyArguments = "body"
    default_base_url: str | None = None
    server_base_url: str | None = None
    protocols: Path | ProtocolConfiguration | Mapping[str, object] | None = None

    def _problems(self) -> Iterator[Diagnostic]:
        yield from TargetConfig._problems(self)  # noqa: SLF001
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
    """Validate Python helper records or a JSON object, loading their definitions only for a configuration with them."""
    from datamodel_code_generator._client.protocols import (  # noqa: PLC0415
        ProtocolConfiguration,
        protocol_problems,
        validate,
    )

    match value:
        case ProtocolConfiguration():
            yield from protocol_problems(value)
        case Mapping():
            yield from validate(value)[1]
        case _:
            yield _diagnostic(
                "E_CONFIG_VALUE", "protocols", "protocols must be a Path, a ProtocolConfiguration, a Mapping, or None"
            )


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


def _idempotency_problems(value: object, at: str) -> Iterator[Diagnostic]:
    if not isinstance(value, IdempotencyMetadata):
        yield _diagnostic("E_CONFIG_VALUE", at, "idempotency must be an IdempotencyMetadata record")
        return
    if not token(value.header_name):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.header_name", "header_name must be a header name")


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


class _EntryError(Exception):
    def __init__(self, option_path: str, message: str, *, code: str = "E_CONFIG_VALUE") -> None:
        self.diagnostic = _diagnostic(code, option_path, message)
        super().__init__(message)


def _object(value: object, at: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        message = f"{at} must be an object"
        raise _EntryError(at, message)
    return cast("Mapping[str, Any]", value)


def _members(value: object, at: str, keys: frozenset[str]) -> Mapping[str, Any]:
    """Return a JSON object whose members are all known, or refuse it."""
    if unknown := sorted(set(members := _object(value, at)) - keys):
        option_path, message = f"{at}.{unknown[0]}", f"{at} has no key {unknown[0]!r}"
        raise _EntryError(option_path, message, code="E_CONFIG_UNKNOWN")
    return members


def operation_configs(entries: Mapping[OperationSelector, object]) -> tuple[ClientOperationConfig, ...]:
    """Read the JSON settings of each operation into its record; the client settings then validate their values.

    Parameters are keyed by location and name, such as `query:limit`, and body fields by media type, then property.
    """
    read = [_entry(ref, entry, f"operations[{index}]") for index, (ref, entry) in enumerate(entries.items())]
    if problems := tuple(item for item in read if isinstance(item, Diagnostic)):
        raise APIGenerationError(problems)
    return tuple(item for item in read if isinstance(item, ClientOperationConfig))


def _entry(ref: OperationSelector, value: object, at: str) -> ClientOperationConfig | Diagnostic:
    try:
        return _operation_config(ref, value, at)
    except _EntryError as error:
        return error.diagnostic


def _operation_config(ref: OperationSelector, value: object, at: str) -> ClientOperationConfig:
    values: dict[str, Any] = dict(_members(value, at, _OPERATION_KEYS))
    if (names := values.get("parameter_names")) is not None:
        values["parameter_names"] = tuple(
            _parameter_name(key, python_name, f"{at}.parameter_names")
            for key, python_name in _object(names, f"{at}.parameter_names").items()
        )
    if (fields_ := values.get("body_field_names")) is not None:
        values["body_field_names"] = tuple(
            BodyFieldName(media_type=media_type, name=name, python_name=python_name)
            for media_type, names in _object(fields_, f"{at}.body_field_names").items()
            for name, python_name in _object(names, f"{at}.body_field_names[{media_type!r}]").items()
        )
    if (runtime := values.get("runtime")) is not None:
        values["runtime"] = _runtime(runtime, f"{at}.runtime")
    return ClientOperationConfig(ref=ref, **values)


def _parameter_name(key: str, python_name: object, at: str) -> ParameterName:
    location, separator, name = key.partition(":")
    if not separator or not _is_location(location):
        message = f"{at} key {key!r} must be a location (path, query, querystring, header, or cookie), ':', and a name"
        raise _EntryError(at, message)
    return ParameterName(in_=location, name=name, python_name=cast("str", python_name))


def _is_location(value: str) -> TypeIs[ParameterLocation]:
    return value in _LOCATIONS


def _runtime(value: object, at: str) -> RuntimeOperationMetadata:
    values: dict[str, Any] = dict(_members(value, at, _RUNTIME_KEYS))
    for name in ("success_statuses", "accepted_content_encodings"):
        if isinstance(items := values.get(name), list):
            values[name] = tuple(items)
    if (idempotency := values.get("idempotency")) is not None:
        header = _members(idempotency, f"{at}.idempotency", _IDEMPOTENCY_KEYS).get("header_name")
        values["idempotency"] = IdempotencyMetadata(header_name=cast("str", header))
    return RuntimeOperationMetadata(**values)


_OPERATION_KEYS: Final = frozenset(item.name for item in fields(ClientOperationConfig)) - {"ref"}
_RUNTIME_KEYS: Final = frozenset(item.name for item in fields(RuntimeOperationMetadata))
_IDEMPOTENCY_KEYS: Final = frozenset(item.name for item in fields(IdempotencyMetadata))
