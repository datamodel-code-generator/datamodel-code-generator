"""Tests for YAML input file code generation."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.util import get_yaml_backend, get_yaml_parse_errors
from tests.conftest import create_assert_file_content
from tests.main.conftest import (
    EXPECTED_MAIN_PATH,
    OPEN_API_DATA_PATH,
    YAML_DATA_PATH,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

assert_file_content = create_assert_file_content(EXPECTED_MAIN_PATH)


class InvalidYamlError(ValueError):
    """The error ryaml raises for a document it cannot read."""


def _read_nothing(_text: str) -> object:
    """Refuse a document as ryaml refuses one holding an integer outside 64 bits."""
    msg = "ryaml cannot read this document"
    raise InvalidYamlError(msg)


@pytest.fixture
def yaml_backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Read YAML with PyYAML alone, or with a ryaml that reads no document (the default)."""
    ryaml = SimpleNamespace(InvalidYamlError=InvalidYamlError, loads=_read_nothing)
    monkeypatch.setitem(sys.modules, "ryaml", ryaml if getattr(request, "param", "ryaml") == "ryaml" else None)
    get_yaml_backend.cache_clear()
    get_yaml_parse_errors.cache_clear()
    yield
    get_yaml_backend.cache_clear()
    get_yaml_parse_errors.cache_clear()


@pytest.mark.benchmark
@pytest.mark.cli_doc(
    options=["--input-file-type"],
    option_description="""Generate models from raw YAML sample data.

The `--input-file-type yaml` option treats the input file as **raw YAML data**
and automatically infers a JSON Schema from it.

**Note:** This is NOT for JSON Schema files written in YAML format.
For schema definition files (JSON Schema or OpenAPI), use `--input-file-type jsonschema`
or `--input-file-type openapi` instead, regardless of whether the file is in JSON or YAML format.""",
    input_schema="yaml/pet.yaml",
    cli_args=["--input-file-type", "yaml"],
    golden_output="yaml.py",
)
def test_main_yaml(output_file: Path) -> None:
    """Generate models from raw YAML sample data.

    The `--input-file-type yaml` option treats the input file as **raw YAML data**
    and automatically infers a JSON Schema from it.

    **Note:** This is NOT for JSON Schema files written in YAML format.
    For schema definition files (JSON Schema or OpenAPI), use `--input-file-type jsonschema`
    or `--input-file-type openapi` instead, regardless of whether the file is in JSON or YAML format.
    """
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "pet.yaml",
        output_path=output_file,
        input_file_type="yaml",
        assert_func=assert_file_content,
    )


def test_main_yaml_root_list(output_file: Path) -> None:
    """Test YAML file with list as root element generates a list root model."""
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "root_list.yaml",
        output_path=output_file,
        input_file_type="yaml",
        assert_func=assert_file_content,
        expected_file="yaml/root_list.py",
    )


def test_main_yaml_unsupported_set_tag(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test YAML file with a set tag fails with invalid file format error."""
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "unsupported_set_tag.yaml",
        output_path=output_file,
        input_file_type="yaml",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid file format",
    )


def test_main_yaml_deprecated_bool(output_file: Path) -> None:
    """Test YAML file with deprecated bool syntax emits deprecation warning."""
    with pytest.warns(DeprecationWarning, match=r"YAML bool 'True' is deprecated"):
        run_main_and_assert(
            input_path=YAML_DATA_PATH / "deprecated_bool.yaml",
            output_path=output_file,
            input_file_type="openapi",
        )


@pytest.mark.parametrize("yaml_backend", ["pyyaml", "ryaml"], indirect=True)
@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_integer_beyond_int64(output_file: Path) -> None:
    """Integer bounds just outside 64 bits generate the same models with either YAML backend (#4910).

    ryaml cannot read such a document, so PyYAML reads it, and the models keep the exact bounds.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "integer_bounds_beyond_int64.yaml",
        output_path=output_file,
        input_file_type="openapi",
    )
    for valid, invalid, error_type in (
        ("-9223372036854776000", "-9223372036854776001", "greater_than_equal"),
        ("9223372036854776000", "9223372036854776001", "less_than_equal"),
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name="integer_bounds_beyond_int64",
            model_name="Seed",
            valid_json=valid,
            invalid_json=invalid,
            expected_error_type=error_type,
        )


@pytest.mark.parametrize(
    "document",
    [
        pytest.param((YAML_DATA_PATH / "broken.yaml").read_text(), id="unclosed"),
        pytest.param(
            f"default: {'9' * (getattr(sys, 'get_int_max_str_digits', lambda: 4300)() + 1)}\n",
            id="integer_beyond_digit_limit",
            marks=pytest.mark.skipif(not hasattr(sys, "set_int_max_str_digits"), reason="no int digit limit"),
        ),
    ],
)
@pytest.mark.usefixtures("yaml_backend")
def test_main_yaml_ryaml_error_when_pyyaml_cannot_read(
    output_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], document: str
) -> None:
    """Report ryaml's error for a document PyYAML cannot read either, such as an integer beyond the digit limit."""
    input_path = tmp_path / "document.yaml"
    input_path.write_text(document)
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="document.yaml: InvalidYamlError: ryaml cannot read this document",
    )


def test_main_yaml_scientific_notation(output_file: Path) -> None:
    """Test YAML file with scientific notation default values (issue #1955).

    Scientific notation without decimal point (e.g., 1e-5) should be parsed as float,
    not as string.
    """
    run_main_and_assert(
        input_path=YAML_DATA_PATH / "scientific_notation.yaml",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
        assert_func=assert_file_content,
        expected_file="yaml/scientific_notation.py",
    )
