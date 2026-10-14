"""Regression coverage for JSON Schema validation edge cases."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert
from tests.main.jsonschema.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_allof_type_list(output_file: Path) -> None:
    """Generate an allOf schema containing a multi-type branch."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "allof_type_list.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="allof_type_list.py",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10"],
        force_exec_validation=True,
    )


def test_main_jsonschema_required_only_type_list_with_schema_validator(output_file: Path) -> None:
    """Generate schema validators when a required-only branch uses a multi-type declaration."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "schema_validators_required_type_list.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="schema_validators_required_type_list.py",
        extra_args=[
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
            "--schema-validator-type",
            "pydantic-v2",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
        force_exec_validation=True,
    )


def test_main_jsonschema_required_only_object_type_list_with_schema_validator(output_file: Path) -> None:
    """Generate schema validators when a required-only branch has type [object]."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "schema_validators_required_object_type_list.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="schema_validators_required_object_type_list.py",
        extra_args=[
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
            "--schema-validator-type",
            "pydantic-v2",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
        force_exec_validation=True,
    )


def test_main_jsonschema_enum_names_allow_null_entries(output_file: Path) -> None:
    """Treat null x-enum-varnames and x-enumNames entries as missing metadata."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "enum_names_null.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="enum_names_null.py",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10"],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("output_model_type", "expected_file"),
    [
        ("pydantic_v2.BaseModel", "enum_names_beside_null.py"),
        ("msgspec.Struct", "enum_names_beside_null_msgspec.py"),
    ],
)
def test_main_jsonschema_enum_names_beside_null(output_model_type: str, expected_file: str, output_file: Path) -> None:
    """Give members beside a null value the x-enum-varnames, x-enumNames and x-enum-descriptions entries of 0.83.0."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "enum_names_beside_null.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=expected_file,
        extra_args=[
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
            "--output-model-type",
            output_model_type,
            "--use-field-description",
        ],
        force_exec_validation=True,
    )


def test_main_jsonschema_draft4_exclusive_bounds_without_bound(output_file: Path) -> None:
    """Ignore incomplete draft-4 exclusive-bound declarations."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "draft4_exclusive_without_bound.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="draft4_exclusive_without_bound.py",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10"],
        force_exec_validation=True,
    )
