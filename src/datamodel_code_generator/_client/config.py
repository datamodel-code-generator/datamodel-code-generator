"""Client target settings and the JSON form of their per-operation entries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, TypeAlias, TypeVar, cast

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._client.naming import namespace_problem, token
from datamodel_code_generator._runtime.client.options import is_base_url
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type
from datamodel_code_generator._target_config import (
    TargetConfig,
    _diagnostic,  # pyright: ignore[reportPrivateUsage]
)
from datamodel_code_generator._target_naming import explicit_name

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from datamodel_code_generator._api_types import OperationSelector
    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation

Transport: TypeAlias = Literal["httpx2"]
SignatureStyle: TypeAlias = Literal["explicit", "unpack"]
BodyArguments: TypeAlias = Literal["body", "both"]
RecordT = TypeVar("RecordT")

OPTION_PREFIX: Final = "client"
_LOCATIONS: Final = frozenset({"path", "query", "querystring", "header", "cookie"})
_BODY_ARGUMENTS: Final = frozenset({"body", "both"})
_RETRY_SAFETIES: Final = frozenset({"method_default", "idempotent", "never"})
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

    _option_prefix: ClassVar[str | None] = OPTION_PREFIX

    transport: Transport = "httpx2"
    resource_names: tuple[ResourceName, ...] = ()
    operations: tuple[ClientOperationConfig, ...] = ()
    signature_style: SignatureStyle = "explicit"
    body_arguments: BodyArguments = "body"
    default_base_url: str | None = None
    server_base_url: str | None = None
    protocols: Mapping[str, object] | None = None

    def _problems(self, target_python_version: PythonVersion) -> Iterator[Diagnostic]:
        yield from TargetConfig._problems(self, target_python_version)  # noqa: SLF001
        yield from _resource_name_problems(self.resource_names, target_python_version)
        yield from _operation_problems(self.operations, self.body_arguments, target_python_version)
        for name in ("default_base_url", "server_base_url"):
            if (value := getattr(self, name)) is not None and not absolute(value):
                yield _diagnostic(
                    "E_CONFIG_VALUE",
                    name,
                    f"{name} must be an absolute http or https URL without userinfo, query, or fragment",
                )
        if self.protocols is not None:
            yield from _protocol_problems(self.protocols, target_python_version)


def _protocol_problems(value: Mapping[str, object], target_python_version: PythonVersion) -> Iterator[Diagnostic]:
    """Validate the helper JSON object, loading the helper definitions only for a configuration with helpers."""
    from datamodel_code_generator._client.protocols import validate  # noqa: PLC0415

    yield from validate(value, target_python_version)[1]


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


def _text(value: object) -> TypeIs[str]:
    """Return whether a value is text; a JSON object key is, but a Python mapping's key may be any object."""
    return isinstance(value, str)


def _key_order(key: object) -> tuple[bool, str]:
    """Order the keys of an object by their text, then any key that is no text by its repr."""
    return (False, key) if _text(key) else (True, repr(key))


def _one_of(value: object, choices: frozenset[str]) -> bool:
    return isinstance(value, str) and value in choices


def _resource_name_problems(
    value: tuple[ResourceName, ...], target_python_version: PythonVersion
) -> Iterator[Diagnostic]:
    """Refuse a namespace that is no dotted resource path; the tags are the distinct keys of a JSON object."""
    for index, item in enumerate(value):
        if (problem := namespace_problem(item.namespace, target_python_version)) is not None:
            yield _diagnostic("E_CONFIG_VALUE", f"resource_names[{index}].namespace", problem)


def _operation_problems(
    value: tuple[ClientOperationConfig, ...], default: object, target_python_version: PythonVersion
) -> Iterator[Diagnostic]:
    for index, item in enumerate(value):
        yield from _operation_config_problems(item, f"operations[{index}]", target_python_version)
        yield from _field_name_problems(item, default, f"operations[{index}]", target_python_version)


def _field_name_problems(
    item: ClientOperationConfig, default: object, at: str, target_python_version: PythonVersion
) -> Iterator[Diagnostic]:
    """Refuse an invalid body mode, and field names that are malformed, repeated, or for a body-only operation."""
    if item.body_arguments is not None and not _one_of(item.body_arguments, _BODY_ARGUMENTS):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.body_arguments", "body_arguments must be 'body', 'both', or None")
    names = item.body_field_names
    if names and (item.body_arguments or default) != "both":
        yield _diagnostic(
            "E_CONFIG_VALUE", f"{at}.body_field_names", "body_field_names need body_arguments 'both' for the operation"
        )
    seen: set[tuple[str, str]] = set()
    for index, name in enumerate(names):
        if not (
            _media(name.media_type) and _text(name.name) and explicit_name(name.python_name, target_python_version)
        ):
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


def _operation_config_problems(
    item: ClientOperationConfig, at: str, target_python_version: PythonVersion
) -> Iterator[Diagnostic]:
    if item.resource is not None and (problem := namespace_problem(item.resource, target_python_version)) is not None:
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.resource", problem)
    if item.name is not None and not explicit_name(item.name, target_python_version):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.name", "name must be a Python identifier")
    yield from _parameter_name_problems(item.parameter_names, f"{at}.parameter_names", target_python_version)
    for name in ("request_media_type", "response_media_type"):
        if (media := getattr(item, name)) is not None and not _media(media):
            yield _diagnostic("E_CONFIG_VALUE", f"{at}.{name}", f"{name} must be a media type")
    yield from _runtime_problems(item.runtime, f"{at}.runtime")
    if item.description is not None and not _text(item.description):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.description", "description must be a string")


def _runtime_problems(runtime: RuntimeOperationMetadata, at: str) -> Iterator[Diagnostic]:
    for name in ("request_id_header", "retry_after_ms_header", "should_retry_header"):
        if (value := getattr(runtime, name)) is not None and not token(value):
            yield _diagnostic("E_CONFIG_VALUE", f"{at}.{name}", f"{name} must be a header name")
    if not _one_of(runtime.retry_safety, _RETRY_SAFETIES):
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


def _idempotency_problems(value: IdempotencyMetadata, at: str) -> Iterator[Diagnostic]:
    if not token(value.header_name):
        yield _diagnostic("E_CONFIG_VALUE", f"{at}.header_name", "header_name must be a header name")


def _parameter_name_problems(
    value: tuple[ParameterName, ...], at: str, target_python_version: PythonVersion
) -> Iterator[Diagnostic]:
    """Refuse an argument name that is no identifier; locations and names are the distinct keys of a JSON object."""
    for index, item in enumerate(value):
        if not explicit_name(item.python_name, target_python_version):
            yield _diagnostic(
                "E_CONFIG_VALUE", f"{at}[{index}]", "A parameter name needs a location, a wire name, and an identifier"
            )


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
    if unknown := sorted(set(members := _object(value, at)) - keys, key=_key_order):
        option_path, message = f"{at}.{unknown[0]}", f"{at} has no key {unknown[0]!r}"
        raise _EntryError(option_path, message, code="E_CONFIG_UNKNOWN")
    return members


def operation_configs(
    entries: Mapping[str, object], selector: Callable[[str], OperationSelector]
) -> tuple[ClientOperationConfig, ...]:
    """Read the JSON settings of each operation reference into its record; the client settings validate the values.

    Parameters are keyed by location and name, such as `query:limit`, and body fields by media type, then property.
    """
    read = [_entry(selector(key), entry, f"operations[{key!r}]") for key, entry in entries.items()]
    if problems := tuple(item for item in read if isinstance(item, Diagnostic)):
        raise APIGenerationError(problems, option_prefix=OPTION_PREFIX)
    return tuple(item for item in read if isinstance(item, ClientOperationConfig))


def _entry(ref: OperationSelector, value: object, at: str) -> ClientOperationConfig | Diagnostic:
    try:
        return _operation_config(ref, value, at)
    except _EntryError as error:
        return error.diagnostic


def _operation_config(ref: OperationSelector, value: object, at: str) -> ClientOperationConfig:
    values: dict[str, Any] = dict(_members(value, at, _OPERATION_KEYS))
    if "parameter_names" in values:
        values["parameter_names"] = tuple(
            _parameter_name(key, python_name, f"{at}.parameter_names")
            for key, python_name in _object(values["parameter_names"], f"{at}.parameter_names").items()
        )
    if "body_field_names" in values:
        values["body_field_names"] = tuple(
            BodyFieldName(media_type=media_type, name=name, python_name=python_name)
            for media_type, names in _object(values["body_field_names"], f"{at}.body_field_names").items()
            for name, python_name in _object(names, f"{at}.body_field_names[{media_type!r}]").items()
        )
    if "runtime" in values:
        values["runtime"] = _runtime(values["runtime"], f"{at}.runtime")
    return ClientOperationConfig(ref=ref, **values)


def _parameter_name(key: object, python_name: object, at: str) -> ParameterName:
    location, separator, name = key.partition(":") if _text(key) else ("", "", "")
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
