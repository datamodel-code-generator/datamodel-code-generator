"""Regression coverage for generated names and union annotations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_main_jsonschema_optional_literal_with_brackets(output_file: Path) -> None:
    """Render optional Literal unions containing bracket characters."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "optional_literal_brackets.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="optional_literal_brackets.py",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10", "--no-use-union-operator"],
        force_exec_validation=True,
    )


def test_main_jsonschema_invalid_special_field_prefix(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Reject invalid special field prefixes instead of repeatedly trying invalid names."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "invalid_special_field_prefix.json",
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        extra_args=["--special-field-name-prefix", "1"],
    )
    assert_output(capsys.readouterr().err, EXPECTED_JSON_SCHEMA_PATH / "invalid_special_field_prefix.txt")


def test_main_jsonschema_empty_original_field_delimiter(output_file: Path) -> None:
    """Treat an empty original field delimiter as an unset delimiter."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "empty_original_field_delimiter.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="empty_original_field_delimiter.py",
        extra_args=[
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
            "--snake-case-field",
            "--original-field-name-delimiter",
            "",
        ],
        force_exec_validation=True,
    )
