"""Validator definitions for generated Pydantic models.

Provides types for defining custom field validators that can be added to generated models.
"""

from __future__ import annotations

import keyword
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, RootModel, ValidationError, field_validator

from datamodel_code_generator.python_literal import _normalize_string

_MIN_DOTTED_PATH_PARTS = 2


class ValidatorMode(str, Enum):
    """Validator mode for Pydantic v2 field_validator."""

    BEFORE = "before"
    AFTER = "after"
    WRAP = "wrap"
    PLAIN = "plain"


_VALIDATOR_MODE_VALUES = frozenset(mode.value for mode in ValidatorMode)


def _is_python_identifier(value: str) -> bool:
    normalized_value = _normalize_string(value)
    return normalized_value.isidentifier() and not keyword.iskeyword(normalized_value)


def _validate_python_identifier(value: str) -> str:
    normalized_value = _normalize_string(value)
    if not _is_python_identifier(normalized_value):
        msg = f"must be a valid Python identifier: {value!r}"
        raise ValueError(msg)
    return normalized_value


def _validate_dotted_python_identifier_path(value: str) -> str:
    normalized_value = _normalize_string(value)
    parts = normalized_value.split(".")
    if len(parts) < _MIN_DOTTED_PATH_PARTS or any(not _is_python_identifier(part) for part in parts):
        msg = f"must be a dotted Python identifier path: {value!r}"
        raise ValueError(msg)
    return normalized_value


def _validate_python_import_path(value: object) -> str:
    """Validate an import path with an optional dotted symbol suffix."""
    if (
        not isinstance(value, str)
        or not (normalized_value := _normalize_string(value).strip())
        or any(not _is_python_identifier(part) for part in normalized_value.split("."))
    ):
        msg = f"must be a Python import path composed of identifiers: {value!r}"
        raise ValueError(msg)
    return normalized_value


class ValidatorDefinition(BaseModel):
    """Definition of a single validator."""

    model_config = ConfigDict(defer_build=True)

    field: str | None = None
    fields: list[str] | None = None
    function: str
    mode: ValidatorMode = ValidatorMode.AFTER

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str | None) -> str | None:
        """Validate a single field name."""
        if value is None:
            return value
        return _validate_python_identifier(value)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: list[str] | None) -> list[str] | None:
        """Validate multiple field names."""
        if value is None:
            return value
        return [_validate_python_identifier(field_name) for field_name in value]

    @field_validator("function")
    @classmethod
    def validate_function(cls, value: str) -> str:
        """Validate the imported validator function path."""
        return _validate_dotted_python_identifier_path(value)

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
        allowed_values = ", ".join(repr(mode.value) for mode in ValidatorMode)
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


def normalize_validators(validators: Any) -> list[dict[str, Any]]:
    """Validate and normalize raw validators extra template data."""
    model_validators = ModelValidators.model_validate({"validators": validators})
    return [validator.model_dump(mode="json", exclude_none=True) for validator in model_validators.validators]


def _reserve_validator_name(name: str, reserved_names: set[str], next_suffixes: dict[str, int]) -> str:
    """Reserve a Python identifier without changing an available user spelling."""
    count = next_suffixes.get(name, 0)
    candidate = name if count == 0 else f"{name}_{count}"
    while candidate in reserved_names:
        count += 1
        candidate = f"{name}_{count}"
    reserved_names.add(candidate)
    next_suffixes[name] = count + 1
    return candidate
