"""Sparse, data-only generation defaults; effective values belong to the shared runtime."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Any, Literal, TypeAlias
from urllib.parse import urlsplit

from datamodel_code_generator._runtime.client.errors import ConfigurationError
from datamodel_code_generator._runtime.client.options import (
    GenerationDefaults,
    RedirectOptions,
    RetryOptions,
    TimeoutOptions,
)
from datamodel_code_generator._runtime.model_codecs.unset import UNSET, Unset
from datamodel_code_generator._target_config import _ConfigValueError

if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator._api_manifest import JSONObject
    from datamodel_code_generator._runtime.model_codecs.wire import JSONValue


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeTimeoutDefaults:
    """Phase durations; omission inherits and None disables the selected phase."""

    connect: float | Unset | None = UNSET
    read: float | Unset | None = UNSET
    write: float | Unset | None = UNSET
    pool: float | Unset | None = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeRetryDefaults:
    """Retry policy fields without operation-specific headers or replay guarantees."""

    max_retries: int | Unset = UNSET
    initial_delay: float | Unset = UNSET
    max_delay: float | Unset = UNSET
    jitter: Literal["full", "none"] | Unset = UNSET
    statuses: AbstractSet[int] | Unset = UNSET
    max_retry_after: float | Unset | None = UNSET
    respect_retry_after: bool | Unset = UNSET
    retry_on_pool_timeout: bool | Unset = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeRedirectDefaults:
    """Redirect policy fields independent of providers and credentials."""

    enabled: bool | Unset = UNSET
    max_redirects: int | Unset = UNSET
    allow_303_to_get: bool | Unset = UNSET
    allowed_origins: tuple[str, ...] | Unset = UNSET
    allow_https_downgrade: bool | Unset = UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeDefaultsConfig:
    """The complete closed inventory saved as generation defaults, with omission preserved."""

    timeout: RuntimeTimeoutDefaults | Unset | None = UNSET
    total_timeout: float | Unset | None = UNSET
    retry: RuntimeRetryDefaults | Unset = UNSET
    redirects: RuntimeRedirectDefaults | Unset = UNSET
    max_network_sends: int | Unset | None = UNSET
    max_response_bytes: int | Unset | None = UNSET
    max_error_body_bytes: int | Unset = UNSET
    max_stream_bytes: int | Unset | None = UNSET
    stream_idle_timeout: float | Unset | None = UNSET
    stream_total_timeout: float | Unset | None = UNSET
    cleanup_timeout: float | Unset = UNSET
    compression: str | Unset | None = UNSET


DefaultsRecord: TypeAlias = (
    RuntimeDefaultsConfig | RuntimeTimeoutDefaults | RuntimeRetryDefaults | RuntimeRedirectDefaults
)

_NESTED = {
    "timeout": (RuntimeTimeoutDefaults, TimeoutOptions),
    "retry": (RuntimeRetryDefaults, RetryOptions),
    "redirects": (RuntimeRedirectDefaults, RedirectOptions),
}
_DISABLED = frozenset({"timeout", "timeout.connect", "timeout.read", "timeout.write", "timeout.pool", "compression"})
_UNLIMITED = frozenset({
    "total_timeout",
    "max_network_sends",
    "max_response_bytes",
    "max_stream_bytes",
    "stream_idle_timeout",
    "stream_total_timeout",
    "retry.max_retry_after",
})
_DURATIONS = frozenset({
    "connect",
    "read",
    "write",
    "pool",
    "total_timeout",
    "stream_idle_timeout",
    "stream_total_timeout",
    "cleanup_timeout",
    "initial_delay",
    "max_delay",
    "max_retry_after",
})


def _record(value: object, kind: type[DefaultsRecord], path: tuple[str, ...]) -> dict[str, Any]:
    """Read only a declared record's fields, without opaque serialization or extra properties."""
    if type(value) is not kind:
        raise ConfigurationError(field_path=path, condition="invalid_type")
    return {item.name: field for item in fields(kind) if not isinstance(field := getattr(value, item.name), Unset)}


def checked_defaults(value: object) -> tuple[GenerationDefaults, JSONObject]:
    """Validate through runtime owners and produce the one sparse canonical data projection."""
    values = _record(value, RuntimeDefaultsConfig, ())
    for name, (record, runtime) in _NESTED.items():
        if name not in values or (name == "timeout" and values[name] is None):
            continue
        nested = _record(values[name], record, (name,))
        if nested:
            values[name] = runtime(**nested)
            if name == "redirects" and not isinstance(values[name].allowed_origins, Unset):
                values[name] = replace(
                    values[name], allowed_origins=tuple(_origin(origin) for origin in values[name].allowed_origins)
                )
        else:
            del values[name]
    layer = GenerationDefaults(**values)
    projected: JSONObject = {}
    for name in values:
        field = getattr(layer, name)
        if name in _NESTED and field is not None:
            record, _ = _NESTED[name]
            projected[name] = {
                item.name: _project(item.name, nested)
                for item in fields(record)
                if not isinstance(nested := getattr(field, item.name), Unset)
            }
        else:
            projected[name] = _project(name, field)
    return layer, projected


def _project(name: str, value: Any) -> JSONValue:
    if value is None:
        return None
    if name in _DURATIONS:
        return float(value)
    if name == "statuses":
        return sorted(value)
    if name == "allowed_origins":
        return list(value)
    return value


def _origin(value: str) -> str:
    """Normalize a validated absolute origin without importing a native transport."""
    parsed = urlsplit(value)
    host = parsed.hostname
    assert host is not None
    try:
        host = host.lower() if ":" in host else host.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ConfigurationError(
            field_path=("redirects", "allowed_origins"), condition="invalid_url", cause=error
        ) from None
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    suffix = "" if port is None or port == {"http": 80, "https": 443}[parsed.scheme] else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def _tag(value: object, path: str, fields_: frozenset[str] = frozenset()) -> object:
    if not isinstance(value, Mapping) or "mode" not in value:
        return value
    extras = set(value) - {"mode"}
    if extras:
        code = "E_CONFIG_CONFLICT" if extras.intersection({"value", *fields_}) else "E_CONFIG_UNKNOWN"
        raise _ConfigValueError(path, "A mode tag cannot also supply a value or extra fields", code=code)
    local = path.removeprefix("runtime_defaults.")
    expected = "disabled" if local in _DISABLED else "unlimited" if local in _UNLIMITED else None
    if expected is None or value["mode"] != expected:
        raise _ConfigValueError(path, "This field does not accept this mode tag")
    return None


def _table(value: object, kind: type[DefaultsRecord], path: str, *, tagged: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _ConfigValueError(path, "Defaults must be a table")
    allowed = frozenset(item.name for item in fields(kind))
    if unknown := set(value) - allowed:
        option_path = f"{path}.{next(iter(sorted(unknown)))}"
        raise _ConfigValueError(option_path, "Unknown runtime default", code="E_CONFIG_UNKNOWN")
    result: dict[str, Any] = {}
    for name, raw in value.items():
        key = f"{path}.{name}" if path else name
        fields_ = frozenset(item.name for item in fields(_NESTED[name][0])) if name in _NESTED else frozenset()
        field = _tag(raw, key, fields_) if tagged else raw
        if name in _NESTED and not (name == "timeout" and field is None):
            record, _ = _NESTED[name]
            field = record(**_table(field, record, key, tagged=tagged))
        elif name == "statuses":
            if (
                not isinstance(field, list)
                or any(type(item) is not int for item in field)
                or len(set(field)) != len(field)
            ):
                raise _ConfigValueError(key, "Retry statuses must be unique integer entries")
            field = frozenset(field)
        elif name == "allowed_origins":
            if not isinstance(field, list):
                raise _ConfigValueError(key, "Allowed origins must be an array")
            field = tuple(field)
        result[name] = field
    return result


def toml_defaults(value: object, _root: Path, option_path: str) -> RuntimeDefaultsConfig:
    """Read closed TOML records and exact field-specific None tags."""
    config = RuntimeDefaultsConfig(**_table(value, RuntimeDefaultsConfig, option_path, tagged=True))
    try:
        checked_defaults(config)
    except ConfigurationError as error:
        path = ".".join((option_path, *error.field_path))
        raise _ConfigValueError(path, error.condition) from None
    return config


def checked_projection(value: object) -> JSONObject:
    """Validate an existing manifest's data-only defaults, never accepting TOML tags."""
    try:
        config = RuntimeDefaultsConfig(**_table(value, RuntimeDefaultsConfig, "runtime_defaults", tagged=False))
        return checked_defaults(config)[1]
    except (_ConfigValueError, ConfigurationError) as error:
        message = "Invalid closed runtime defaults projection"
        raise ValueError(message) from error
