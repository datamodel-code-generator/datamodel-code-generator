"""Validator definitions for generated Pydantic models.

Provides types for defining custom field validators that can be added to generated models.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, ConfigDict, RootModel, ValidationError, ValidationInfo, field_validator

from datamodel_code_generator._format_types import NEWEST_TARGET
from datamodel_code_generator._free_names import first_free
from datamodel_code_generator.python_literal import (
    _normalize_string,
    _normalized_safe_python_name,
    message_repr,
    stable_repr,
)

if TYPE_CHECKING:
    from datamodel_code_generator._format_types import PythonVersion


class ValidatorMode(str, Enum):
    """Validator mode for Pydantic v2 field_validator."""

    BEFORE = "before"
    AFTER = "after"
    WRAP = "wrap"
    PLAIN = "plain"


_VALIDATOR_MODE_VALUES = frozenset(mode.value for mode in ValidatorMode)

_TARGET_CONTEXT_KEY: Final = "target_python_version"
"""The key of the validation context that names the target Python whose names the validator names must be.

A model passes it when it writes its validators (``normalize_validators``), and ``ValidatorDefinition`` validates an
instance again for it; without it, the names are names of the newest target.
"""


def _context_target(info: ValidationInfo) -> PythonVersion:
    """Return the target Python a validation context names, or the newest when it names none."""
    context = info.context
    return context.get(_TARGET_CONTEXT_KEY, NEWEST_TARGET) if isinstance(context, dict) else NEWEST_TARGET


def _validate_python_identifier(value: str, target: PythonVersion) -> str:
    if (normalized_value := _normalized_safe_python_name(_normalize_string(value), target)) is None:
        msg = f"must be a valid Python identifier: {message_repr(value)}"
        raise ValueError(msg)
    return normalized_value


def _validate_dotted_python_identifier_path(value: str, target: PythonVersion) -> str:
    if (
        normalized_value := _normalized_safe_python_name(value, target, dotted=True)
    ) is None or "." not in normalized_value:
        msg = f"must be a dotted Python identifier path: {message_repr(value)}"
        raise ValueError(msg)
    return normalized_value


def _validate_python_import_path(value: object, target: PythonVersion, *, allow_relative: bool = False) -> str:
    """Validate target Python names joined by dots, allowing module-qualified relative paths when requested."""
    if isinstance(value, str):
        normalized_value = _normalize_string(value).strip()
        names = normalized_value.lstrip(".") if allow_relative else normalized_value
        if (not normalized_value.startswith(".") or "." in names) and _normalized_safe_python_name(
            names, target, dotted=True
        ) is not None:
            return normalized_value
    msg = f"must be a Python import path composed of identifiers: {message_repr(value)}"
    raise ValueError(msg)


def _validate_schema_python_import_path(value: Any, field_name: str, target: PythonVersion) -> str:
    """Return the dotted import path a schema keyword names, refusing one whose names are no names of the target."""
    from datamodel_code_generator import Error  # noqa: PLC0415

    if not isinstance(value, str):
        msg = f"{field_name} must be a dotted Python identifier path: {message_repr(value)}"
        raise Error(msg)
    try:
        return _validate_dotted_python_identifier_path(value, target)
    except ValueError as exc:
        msg = f"{field_name} {exc}"
        raise Error(msg) from None


class ValidatorDefinition(BaseModel):
    """Definition of a single validator."""

    model_config = ConfigDict(defer_build=True, revalidate_instances="always")

    field: str | None = None
    fields: list[str] | None = None
    function: str
    mode: ValidatorMode = ValidatorMode.AFTER

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Validate a single field name."""
        if value is None:
            return value
        return _validate_python_identifier(value, _context_target(info))

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: list[str] | None, info: ValidationInfo) -> list[str] | None:
        """Validate multiple field names."""
        if value is None:
            return value
        target = _context_target(info)
        return [_validate_python_identifier(field_name, target) for field_name in value]

    @field_validator("function")
    @classmethod
    def validate_function(cls, value: str, info: ValidationInfo) -> str:
        """Validate the imported validator function path."""
        return _validate_dotted_python_identifier_path(value, _context_target(info))

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, value: Any) -> Any:
        """Validate the Pydantic field_validator mode."""
        if isinstance(value, ValidatorMode):
            return value
        if isinstance(value, str):
            normalized_value = _normalize_string(value)
            if normalized_value in _VALIDATOR_MODE_VALUES:
                return normalized_value
        allowed_values = ", ".join(stable_repr(mode.value) for mode in ValidatorMode)
        msg = f"must be one of: {allowed_values}"
        raise ValueError(msg)


class ModelValidators(BaseModel):
    """Validators configuration for a single model."""

    model_config = ConfigDict(defer_build=True)

    validators: list[ValidatorDefinition]


class ValidatorsConfig(RootModel[dict[str, ModelValidators]]):
    """Root model for validators configuration."""

    model_config = ConfigDict(defer_build=True)


def format_validation_error(error: ValidationError) -> str:
    """Format the first Pydantic validation error as a concise, stable message."""
    errors = error.errors()
    if not errors:
        return str(error)  # pragma: no cover

    first_error = errors[0]
    location = ".".join(str(part) for part in first_error.get("loc", ()))
    context = first_error.get("ctx") or {}
    context_error = context.get("error")
    message = str(context_error) if context_error else str(first_error["msg"])

    if location:
        return f"{location}: {message}"
    return message


def normalize_validators(validators: Any, *, target: PythonVersion | None = None) -> list[dict[str, Any]]:
    """Validate and normalize raw validators extra template data, its names as names of the target, else the newest."""
    model_validators = ModelValidators.model_validate(
        {"validators": validators}, context={_TARGET_CONTEXT_KEY: NEWEST_TARGET if target is None else target}
    )
    return [validator.model_dump(mode="json", exclude_none=True) for validator in model_validators.validators]


def _reserve_validator_name(name: str, reserved_names: set[str], next_suffixes: dict[str, int]) -> str:
    """Reserve a Python identifier without changing an available user spelling."""
    candidate, count = first_free(name, reserved_names.__contains__, "underscore", start=next_suffixes.get(name, 0))
    reserved_names.add(candidate)
    next_suffixes[name] = count + 1
    return candidate
