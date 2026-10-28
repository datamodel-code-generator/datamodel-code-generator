"""Tests for CSV input file code generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import InputFileType, InvalidFileFormatError, generate
from datamodel_code_generator.__main__ import Exit
from tests.conftest import create_assert_file_content
from tests.main.conftest import (
    BACKEND_GOLDEN_CASES,
    BACKEND_GOLDEN_TARGET_ARGS,
    CSV_DATA_PATH,
    EXPECTED_CSV_PATH,
    JSON_DATA_PATH,
    YAML_DATA_PATH,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


assert_file_content = create_assert_file_content(EXPECTED_CSV_PATH)


def test_csv_file(output_file: Path) -> None:
    """Test CSV file input code generation."""
    run_main_and_assert(
        input_path=CSV_DATA_PATH / "simple.csv",
        output_path=output_file,
        input_file_type="csv",
        assert_func=assert_file_content,
        expected_file="csv_file_simple.py",
    )


@pytest.mark.parametrize(("output_model_type", "expected_name"), BACKEND_GOLDEN_CASES)
def test_main_csv_output_model_types(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
) -> None:
    """Generate representative CSV models across supported output backends."""
    run_main_and_assert(
        input_path=CSV_DATA_PATH / "backend_matrix.csv",
        output_path=output_file,
        input_file_type="csv",
        assert_func=assert_file_content,
        expected_file=f"output_model_types/backend_matrix_{expected_name}.py",
        extra_args=[*BACKEND_GOLDEN_TARGET_ARGS, "--output-model-type", output_model_type],
        force_exec_validation=True,
        importable_module_name=f"generated_csv_{expected_name}",
        importable_module_attribute="Model",
    )


def test_csv_file_auto_detection(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test CSV file input is detected by auto input type."""
    run_main_and_assert(
        input_path=CSV_DATA_PATH / "simple.csv",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file="csv_file_simple.py",
        capsys=capsys,
        expected_stderr_contains="The input file type was determined to be: csv",
    )


@pytest.mark.parametrize(
    "input_path",
    [
        JSON_DATA_PATH / "broken.json",
        YAML_DATA_PATH / "broken.yaml",
    ],
)
def test_auto_detection_rejects_malformed_non_csv_input(
    input_path: Path,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test auto input type does not treat malformed JSON/YAML as CSV data."""
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Please specify the input file type explicitly with --input-file-type option.",
        output_should_not_exist=True,
    )


def test_csv_stdin(monkeypatch: pytest.MonkeyPatch, output_file: Path) -> None:
    """Test CSV stdin input code generation."""
    run_main_and_assert(
        stdin_path=CSV_DATA_PATH / "simple.csv",
        output_path=output_file,
        monkeypatch=monkeypatch,
        input_file_type="csv",
        assert_func=assert_file_content,
        expected_file="csv_stdin_simple.py",
    )


def test_csv_file_missing_trailing_cell(output_file: Path) -> None:
    """Test CSV file input infers row values, not header names, for short rows."""
    run_main_and_assert(
        input_path=CSV_DATA_PATH / "missing_trailing_cell.csv",
        output_path=output_file,
        input_file_type="csv",
        assert_func=assert_file_content,
        expected_file="csv_file_missing_trailing_cell.py",
    )


def test_csv_file_extra_trailing_cell(output_file: Path) -> None:
    """Test CSV file input ignores cells beyond the header columns."""
    run_main_and_assert(
        input_path=CSV_DATA_PATH / "extra_trailing_cell.csv",
        output_path=output_file,
        input_file_type="csv",
        assert_func=assert_file_content,
        expected_file="csv_file_extra_trailing_cell.py",
    )


def test_csv_file_header_only(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test CSV file input reports header-only data as an invalid file."""
    run_main_and_assert(
        input_path=CSV_DATA_PATH / "header_only.csv",
        output_path=output_file,
        input_file_type="csv",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid file format for csv: ValueError: CSV file has no data rows",
        output_should_not_exist=True,
    )


def test_csv_empty_input_raises_invalid_file_format(output_file: Path) -> None:
    """Test CSV raw input reports missing headers as an invalid file."""
    with pytest.raises(InvalidFileFormatError, match="CSV file has no header row"):
        generate(
            input_="",
            output=output_file,
            input_file_type=InputFileType.CSV,
        )
