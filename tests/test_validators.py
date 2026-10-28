"""Tests for validator configuration helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from datamodel_code_generator.validators import ValidatorMode, format_validation_error, normalize_validators
from tests.conftest import assert_output

EXPECTED_PATH = Path(__file__).parent / "data" / "expected" / "validators"


def assert_validation_error_message(error: ValidationError, expected_file: Path) -> None:
    """Assert the formatted validation error message matches expected output."""
    assert_output(f"{format_validation_error(error)}\n", expected_file)


def test_format_validation_error_without_location_returns_message() -> None:
    """Validation errors without a location are formatted as a message only."""
    error = ValidationError.from_exception_data(
        "ValidatorDefinition",
        [
            {
                "type": "value_error",
                "loc": (),
                "input": "x",
                "ctx": {"error": ValueError("plain message")},
            }
        ],
    )

    assert_validation_error_message(error, EXPECTED_PATH / "format_validation_error_without_location.txt")


@pytest.mark.allow_direct_assert
def test_normalize_validators_accepts_supported_shapes() -> None:
    """Normalize supported validator config shapes for template rendering."""
    validators = [
        {"field": "name", "function": "myapp.validators.validate_name"},
        {"fields": ["email", "phone"], "function": "myapp.validators.validate_contact", "mode": "before"},
        {"function": "myapp.validators.validate_something"},
        {
            "field": None,
            "fields": ["status"],
            "function": "myapp.validators.validate_status",
            "mode": ValidatorMode.WRAP,
        },
    ]

    assert normalize_validators(validators) == [
        {"field": "name", "function": "myapp.validators.validate_name", "mode": "after"},
        {"fields": ["email", "phone"], "function": "myapp.validators.validate_contact", "mode": "before"},
        {"function": "myapp.validators.validate_something", "mode": "after"},
        {"fields": ["status"], "function": "myapp.validators.validate_status", "mode": "wrap"},
    ]


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize(
    ("validators", "expected_message"),
    [
        ("not-a-list", "validators: Input should be a valid list"),
        ([{"field": "name"}], "validators.0.function: Field required"),
        (
            [{"field": "name", "function": "validate_name"}],
            "validators.0.function: must be a dotted Python identifier path: 'validate_name'",
        ),
        (
            [{"fields": "name", "function": "myapp.validators.validate_name"}],
            "validators.0.fields: Input should be a valid list",
        ),
        (
            [{"field": "name", "function": "myapp.validators.validate_name", "mode": "during"}],
            "validators.0.mode: must be one of: 'before', 'after', 'wrap', 'plain'",
        ),
        (
            [{"field": "name", "function": "myapp.validators.validate_name", "mode": None}],
            "validators.0.mode: must be one of: 'before', 'after', 'wrap', 'plain'",
        ),
    ],
)
def test_normalize_validators_rejects_invalid_shapes(validators: object, expected_message: str) -> None:
    """Report the first invalid validator config error with concise formatting."""
    with pytest.raises(ValidationError) as exc_info:
        normalize_validators(validators)

    assert format_validation_error(exc_info.value) == expected_message
