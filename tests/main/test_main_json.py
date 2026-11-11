"""Tests for JSON input file code generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import black
import pytest
from packaging import version

from datamodel_code_generator import chdir
from datamodel_code_generator.__main__ import Exit
from tests.conftest import (
    HttpxGetMockFactory,
    MockHttpxResponse,
    assert_httpx_get_kwargs,
    create_assert_file_content,
)
from tests.main.conftest import (
    EXPECTED_JSON_PATH,
    JSON_DATA_PATH,
    run_main_and_assert,
    run_main_url_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


assert_file_content = create_assert_file_content(EXPECTED_JSON_PATH)


@pytest.mark.cli_doc(
    options=["--input-file-type"],
    option_description="""Specify the input file type for code generation.

The `--input-file-type` flag explicitly sets the input format.

**Important distinction:**

- Use `jsonschema`, `openapi`, `asyncapi`, `graphql`, `mcp-tools`, `xmlschema`,
  `protobuf`, or `avro` for **schema definition files**
- Use `json`, `yaml`, or `csv` for **raw sample data** to automatically infer a schema

For example, if you have a JSON Schema written in YAML format, use `--input-file-type jsonschema`,
not `--input-file-type yaml`. The `yaml` type treats the file as raw data and infers a schema from it.""",
    input_schema="json/pet.json",
    cli_args=["--input-file-type", "json"],
    golden_output="json/general.py",
    primary=True,
)
def test_main_json(output_file: Path) -> None:
    """Specify the input file type for code generation.

    The `--input-file-type` flag explicitly sets the input format.

    **Important distinction:**

    - Use `jsonschema`, `openapi`, `asyncapi`, `graphql`, `mcp-tools`, `xmlschema`,
      `protobuf`, or `avro` for **schema definition files**
    - Use `json`, `yaml`, or `csv` for **raw sample data** to automatically infer a schema

    For example, if you have a JSON Schema written in YAML format, use `--input-file-type jsonschema`,
    not `--input-file-type yaml`. The `yaml` type treats the file as raw data and infers a schema from it.
    """
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "pet.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
        expected_file="general.py",
    )


def test_space_and_special_characters_json(output_file: Path) -> None:
    """Test JSON code generation with space and special characters."""
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "space_and_special_characters.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
        expected_file="space_and_special_characters.py",
    )


def test_main_json_failed(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test JSON code generation with broken input file."""
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "broken.json",
        output_path=output_file,
        input_file_type="json",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid file format",
    )


def test_main_json_array_include_null(output_file: Path) -> None:
    """Test JSON code generation with arrays including null values."""
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "array_include_null.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
    )


def test_main_json_reuse_model(output_file: Path) -> None:
    """Test JSON code generation with model reuse."""
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "duplicate_models.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
        extra_args=["--reuse-model"],
    )


def test_main_json_reuse_model_pydantic2(output_file: Path) -> None:
    """Test JSON code generation with model reuse and Pydantic v2."""
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "duplicate_models.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--reuse-model"],
    )


@pytest.mark.cli_doc(
    options=["--collapse-reuse-models"],
    option_description="""Collapse duplicate models by replacing references instead of inheritance.

The `--collapse-reuse-models` flag, when used with `--reuse-model`,
eliminates redundant empty subclasses (e.g., `class Foo(Bar): pass`)
by replacing all references to duplicate models with the canonical model.""",
    input_schema="json/duplicate_models.json",
    cli_args=["--reuse-model", "--collapse-reuse-models"],
    golden_output="json/json_collapse_reuse_model.py",
    related_options=["--reuse-model"],
)
def test_main_json_collapse_reuse_model(output_file: Path) -> None:
    """Collapse duplicate models by replacing references instead of inheritance.

    The `--collapse-reuse-models` flag, when used with `--reuse-model`,
    eliminates redundant empty subclasses (e.g., `class Foo(Bar): pass`)
    by replacing all references to duplicate models with the canonical model.
    """
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "duplicate_models.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
        extra_args=["--reuse-model", "--collapse-reuse-models"],
    )


def test_simple_json_snake_case_field(output_file: Path) -> None:
    """Test JSON code generation with snake case field naming."""
    with chdir(JSON_DATA_PATH):
        run_main_and_assert(
            input_path=JSON_DATA_PATH / "simple.json",
            output_path=output_file,
            input_file_type="json",
            assert_func=assert_file_content,
            extra_args=["--snake-case-field"],
        )


def test_main_http_json(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Test JSON code generation from HTTP URL."""
    httpx_get_mock = mock_httpx_get(MockHttpxResponse("https://example.com/pet.json", JSON_DATA_PATH / "pet.json"))
    run_main_url_and_assert(
        url="https://example.com/pet.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
        expected_file="general.py",
        transform=lambda s: s.replace(
            "#   filename:  https://example.com/pet.json",
            "#   filename:  pet.json",
        ),
    )
    assert_httpx_get_kwargs(httpx_get_mock, expected_url="https://example.com/pet.json")


@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.3.0"),
    reason="Require Black version 23.3.0 or later ",
)
def test_main_typed_dict_space_and_special_characters(output_file: Path) -> None:
    """Test TypedDict generation with space and special characters."""
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "space_and_special_characters.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
        extra_args=["--output-model-type", "typing.TypedDict", "--target-python-version", "3.11"],
    )


def test_main_json_snake_case_field(output_file: Path) -> None:
    """Test JSON code generation with snake case field naming."""
    run_main_and_assert(
        input_path=JSON_DATA_PATH / "snake_case.json",
        output_path=output_file,
        input_file_type="json",
        assert_func=assert_file_content,
        extra_args=["--snake-case-field"],
    )
