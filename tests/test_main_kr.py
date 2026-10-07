"""Tests for main CLI functionality with Korean locale settings."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from argparse import Namespace
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

import black
import jsonschema
import pydantic
import pytest
from packaging import version

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from datamodel_code_generator import MIN_VERSION, Error, chdir, inferred_message
from datamodel_code_generator import __main__ as main_module
from datamodel_code_generator import _publication as publication_module
from datamodel_code_generator.__main__ import (
    Exit,
    JobPlan,
    _generated_files_from_result,
    _generation_output_json,
    _json_ready,
    _plan_jobs,
    _publish_staged_files,
    _selected_jobs,
    _StagedJobPlan,
    _write_generated_result,
    generate_pyproject_config,
)
from datamodel_code_generator.arguments import arg_parser
from tests.conftest import (
    HttpxGetMockFactory,
    MockHttpxResponse,
    assert_directory_content,
    assert_error_message,
    assert_httpx_get_kwargs,
    assert_output,
    create_assert_file_content,
    freeze_time,
    validate_generated_code,
)

if TYPE_CHECKING:
    import tempfile
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    LEGACY_BLACK_SKIP,
    OPEN_API_DATA_PATH,
    TIMESTAMP,
    _assert_captured_output,
    _assert_file_does_not_exist,
    run_main_and_assert,
    run_main_url_and_assert,
    run_main_with_args,
)

EXPECTED_MAIN_KR_PATH = DATA_PATH / "expected" / "main_kr"
EXPECTED_OUTPUT_FORMAT_JSON_PATH = EXPECTED_MAIN_KR_PATH / "output_format_json"
EXPECTED_EMPTY_OUTPUT_PATH = DATA_PATH / "expected" / "__init__.py"
JOBS_PYPROJECT_TEMPLATE = DATA_PATH / "config" / "pyproject_jobs.toml"
FALSE_BOOLEAN_OPTIONAL_ACTIONS_PYPROJECT = DATA_PATH / "config" / "false_boolean_optional_actions.toml"
GENERATE_PROMPT_JSON_ARGS = [
    "--input",
    "tests/data/jsonschema/person.json",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--no-use-annotated",
    "--strict-types",
    "str",
    "int",
    "--generate-prompt",
    "Which strict Pydantic v2 options should I use?",
    "--output-format",
    "json",
]
GENERATE_PROMPT_JSON_SCHEMA_ARGS = ["--output-format-json-schema", "generate-prompt"]

assert_file_content = create_assert_file_content(EXPECTED_MAIN_KR_PATH)


OUTPUT_FILE_PLACEHOLDER = "<OUTPUT_FILE>"
OUTPUT_DIR_PLACEHOLDER = "<OUTPUT_DIR>"


def _normalize_generation_json_output_path(output: str, output_path: Path, placeholder: str) -> str:
    payload = json.loads(output)
    if payload.get("output") == output_path.as_posix():
        payload["output"] = placeholder
    for file_payload in payload["files"]:
        if file_payload["path"] == output_path.as_posix():
            file_payload["path"] = placeholder
    return f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n"


def _batch_json_summary(output: str) -> str:
    """Normalize batch JSON to the stable fields covered by the job contracts."""
    payload = json.loads(output)
    jobs = [
        {
            "kind": job["result"]["kind"],
            "name": job["name"],
            **({"output": Path(job["result"]["output"]).name} if "output" in job["result"] else {}),
            **({"success": job["result"]["success"]} if "success" in job["result"] else {}),
        }
        for job in payload["jobs"]
    ]
    return f"{json.dumps({'kind': payload['kind'], 'jobs': jobs}, indent=2)}\n"


@pytest.fixture(autouse=True)
def reset_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset argument namespace before each test."""
    namespace_ = Namespace(no_color=False)
    monkeypatch.setattr("datamodel_code_generator.__main__.namespace", namespace_)
    monkeypatch.setattr("datamodel_code_generator.arguments.namespace", namespace_)


@pytest.fixture
def output_file(tmp_path: Path) -> Path:
    """Return standard output file path."""
    return tmp_path / "output.py"


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """Return standard output directory path."""
    return tmp_path / "model"


@pytest.fixture
def jobs_project(tmp_path: Path) -> dict[str, Path]:
    """Create a two-job project backed by the shared JSON Schema fixture."""
    plain_output = tmp_path / "plain.py"
    strict_output = tmp_path / "strict.py"
    pyproject = JOBS_PYPROJECT_TEMPLATE.read_text(encoding="utf-8")
    pyproject = pyproject.replace("$PERSON_SCHEMA", (JSON_SCHEMA_DATA_PATH / "person.json").as_posix())
    pyproject = pyproject.replace("$PLAIN_OUTPUT", plain_output.as_posix())
    pyproject = pyproject.replace("$STRICT_OUTPUT", strict_output.as_posix())
    (tmp_path / "pyproject.toml").write_text(
        pyproject,
        encoding="utf-8",
    )
    return {"plain": plain_output, "strict": strict_output}


@freeze_time("2019-07-26")
def test_main(output_file: Path) -> None:
    """Test basic main function with OpenAPI input."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="main/output.py",
    )


@freeze_time("2019-07-26")
def test_main_base_class(output_file: Path, tmp_path: Path) -> None:
    """Test main function with custom base class."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_base_class" / "output.py",
        extra_args=["--base-class", "custom_module.Base"],
        copy_files=[(DATA_PATH / "pyproject.toml", tmp_path / "pyproject.toml")],
    )


@freeze_time("2019-07-26")
def test_target_python_version(output_file: Path) -> None:
    """Test main function with target Python version."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "target_python_version" / "output.py",
        extra_args=["--target-python-version", f"3.{MIN_VERSION}"],
    )


def test_main_modular(output_dir: Path) -> None:
    """Test main function on modular file."""
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "modular.yaml",
            output_path=output_dir,
            expected_directory=EXPECTED_MAIN_KR_PATH / "main_modular",
            extra_args=["--fail-on-multi-module-stdout"],
        )


@pytest.mark.isolate_builtin_formatter_config
@freeze_time(TIMESTAMP)
def test_main_modular_no_file(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve legacy concatenated modular text stdout by default."""
    run_main_with_args(
        ["--input", str(OPEN_API_DATA_PATH / "modular.yaml")],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_KR_PATH / "main_modular_no_file" / "output.py",
    )


@pytest.mark.parametrize(
    "input_path",
    [
        pytest.param(OPEN_API_DATA_PATH / "modular.yaml", id="modular"),
        pytest.param(OPEN_API_DATA_PATH / "invalid_dotted_schema_name.yaml", id="invalid-dotted-repair"),
    ],
)
def test_main_fail_on_multi_module_stdout(input_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject concatenated modular text only when explicitly requested."""
    run_main_with_args(
        [
            "--input",
            str(input_path),
            "--input-file-type",
            "openapi",
            "--fail-on-multi-module-stdout",
        ],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stdout_path=EXPECTED_EMPTY_OUTPUT_PATH,
        expected_stderr=(EXPECTED_MAIN_KR_PATH / "multi_module_stdout" / "error.txt").read_text(),
    )


@pytest.mark.isolate_builtin_formatter_config
@pytest.mark.cli_doc(
    options=["--fail-on-multi-module-stdout"],
    option_description="""Fail instead of concatenating multiple modules in text stdout.

The `--fail-on-multi-module-stdout` flag detects modular results while preserving
the legacy default, single-module output, JSON output, and file output. It takes
precedence over automatic unusable-stdout repair, rejecting the modular result
instead of coalescing it.""",
    input_schema="jsonschema/person.json",
    cli_args=["--fail-on-multi-module-stdout", "--disable-timestamp"],
    golden_output="main/person.py",
)
def test_fail_on_multi_module_stdout_single_file(capsys: pytest.CaptureFixture[str]) -> None:
    """Fail instead of concatenating multiple modules in text stdout.

    The `--fail-on-multi-module-stdout` flag detects modular results while preserving
    the legacy default, single-module output, JSON output, and file output.
    """
    run_main_with_args(
        [
            "--input",
            str(JSON_SCHEMA_DATA_PATH / "person.json"),
            "--input-file-type",
            "jsonschema",
            "--fail-on-multi-module-stdout",
            "--disable-timestamp",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=DATA_PATH / "expected" / "main" / "person.py",
        assert_no_stderr=True,
    )


@pytest.mark.isolate_builtin_formatter_config
@freeze_time(TIMESTAMP)
def test_main_modular_json_no_file(capsys: pytest.CaptureFixture[str]) -> None:
    """Test modular JSON output preserves each generated module on stdout."""
    run_main_with_args(
        [
            "--input",
            str(OPEN_API_DATA_PATH / "modular.yaml"),
            "--input-file-type",
            "openapi",
            "--output-format",
            "json",
            "--disable-timestamp",
            "--fail-on-multi-module-stdout",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_modular_stdout.txt",
        assert_no_stderr=True,
    )


def test_single_module_mapping_text_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Preserve valid text stdout for a generated mapping with one module."""
    expected_path = EXPECTED_MAIN_KR_PATH / "single_module_mapping" / "output.py"
    _write_generated_result(
        {("model.py",): expected_path.read_text().removesuffix("\n")},
        None,
        fail_on_multi_module_stdout=True,
    )

    _assert_captured_output(capsys, expected_stdout_path=expected_path, assert_no_stderr=True)


def test_main_modular_filename(output_file: Path) -> None:
    """Test main function on modular file with filename."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "modular.yaml",
        output_path=output_file,
        expected_exit=Exit.ERROR,
    )


@pytest.mark.isolate_builtin_formatter_config
def test_main_no_file(capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test main function on non-modular file with no output name."""
    monkeypatch.chdir(tmp_path)

    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_MAIN_KR_PATH / "main_no_file" / "output.py",
            capsys=capsys,
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.isolate_builtin_formatter_config
def test_main_custom_template_dir(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test main function with custom template directory."""
    monkeypatch.chdir(tmp_path)

    custom_template_dir = DATA_PATH / "templates"
    extra_template_data = OPEN_API_DATA_PATH / "extra_data.json"

    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "api.yaml",
            output_path=None,
            expected_stdout_path=EXPECTED_MAIN_KR_PATH / "main_custom_template_dir" / "output.py",
            capsys=capsys,
            extra_args=[
                "--custom-template-dir",
                str(custom_template_dir),
                "--extra-template-data",
                str(extra_template_data),
            ],
            expected_stderr=inferred_message.format("openapi") + "\n",
        )


@pytest.mark.skipif(
    black.__version__.split(".")[0] >= "24",
    reason="Installed black doesn't support the old style",
)
@freeze_time("2019-07-26")
def test_pyproject(output_file: Path, tmp_path: Path) -> None:
    """Test main function with pyproject.toml configuration."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file="pyproject/output.py",
        copy_files=[(DATA_PATH / "project" / "pyproject.toml", tmp_path / "pyproject.toml")],
    )


@pytest.mark.parametrize("language", ["UK", "US"])
def test_pyproject_respects_both_spellings_of_capitalize_enum_members_flag(language: str, tmp_path: Path) -> None:
    """Test that both UK and US spellings of capitalise are accepted."""
    pyproject_toml_data = f"""
[tool.datamodel-codegen]
capitali{"s" if language == "UK" else "z"}e-enum-members = true
enable-version-header = false
input-file-type = "jsonschema"
"""
    with (tmp_path / "pyproject.toml").open("w") as f:
        f.write(pyproject_toml_data)

        input_data = """
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "MyEnum": {
      "enum": [
        "MEMBER_1",
        "member_2"
      ]
    }
  }
}
"""
    input_file = tmp_path / "schema.json"
    with input_file.open("w") as f:
        f.write(input_data)

    expected_output = """# generated by datamodel-codegen:
#   filename:  schema.json

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import RootModel


class Model(RootModel[Any]):
    root: Any


class MyEnum(Enum):
    MEMBER_1 = 'MEMBER_1'
    member_2 = 'member_2'
"""

    output_file: Path = tmp_path / "output.py"
    run_main_and_assert(
        input_path=input_file,
        output_path=output_file,
        expected_output=expected_output,
        extra_args=["--disable-timestamp"],
    )


@pytest.mark.skipif(
    black.__version__.split(".")[0] == "19",
    reason="Installed black doesn't support the old style",
)
@freeze_time("2019-07-26")
def test_pyproject_with_tool_section(output_file: Path, tmp_path: Path) -> None:
    """Test that a pyproject.toml with [tool.datamodel-codegen] section is found and applied."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
strict-types = ["str"]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=(OPEN_API_DATA_PATH / "api.yaml").resolve(),
            output_path=output_file.resolve(),
            input_file_type=None,
            assert_func=assert_file_content,
            expected_file=EXPECTED_MAIN_KR_PATH / "pyproject" / "output.strictstr.py",
        )


@pytest.mark.cli_doc(
    options=["--use-schema-description"],
    option_description="""Use schema description as class docstring.

The `--use-schema-description` flag extracts the `description` property from
schema definitions and adds it as a docstring to the generated class. This is
useful for preserving documentation from your schema in the generated code.""",
    input_schema="openapi/api_multiline_docstrings.yaml",
    cli_args=["--use-schema-description"],
    golden_output="main_kr/main_use_schema_description/output.py",
    related_options=["--use-field-description", "--use-inline-field-description"],
)
@freeze_time("2019-07-26")
def test_main_use_schema_description(output_file: Path) -> None:
    """Use schema description as class docstring.

    The `--use-schema-description` flag extracts the `description` property from
    schema definitions and adds it as a docstring to the generated class. This is
    useful for preserving documentation from your schema in the generated code.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_multiline_docstrings.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_schema_description" / "output.py",
        extra_args=["--use-schema-description"],
    )


@freeze_time("2019-07-26")
def test_main_docstring_special_chars(output_file: Path) -> None:
    """Escape special characters in docstrings.

    Backslashes and triple quotes in schema descriptions must be escaped
    to prevent Python syntax errors and type checker warnings. See GitHub
    issue #1808.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "docstring_special_chars.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_docstring_special_chars" / "output.py",
        extra_args=["--use-schema-description", "--use-field-description"],
    )


@pytest.mark.cli_doc(
    options=["--use-field-description"],
    option_description="""Add field descriptions using Pydantic Field().

The `--use-field-description` flag adds the `description` property from
schema fields as the `description` parameter in Pydantic Field(). This
provides documentation that is accessible via model schema and OpenAPI docs.""",
    input_schema="openapi/api_multiline_docstrings.yaml",
    cli_args=["--use-field-description"],
    golden_output="main_kr/main_use_field_description/output.py",
    related_options=["--use-schema-description", "--use-inline-field-description"],
)
@freeze_time("2022-11-11")
def test_main_use_field_description(output_file: Path) -> None:
    """Add field descriptions using Pydantic Field().

    The `--use-field-description` flag adds the `description` property from
    schema fields as the `description` parameter in Pydantic Field(). This
    provides documentation that is accessible via model schema and OpenAPI docs.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_multiline_docstrings.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_field_description" / "output.py",
        extra_args=["--use-field-description"],
    )


@pytest.mark.cli_doc(
    options=["--use-inline-field-description"],
    option_description="""Add field descriptions as inline comments.

The `--use-inline-field-description` flag adds the `description` property from
schema fields as inline comments after each field definition. This provides
documentation without using Field() wrappers.""",
    input_schema="openapi/api_multiline_docstrings.yaml",
    cli_args=["--use-inline-field-description"],
    golden_output="main_kr/main_use_inline_field_description/output.py",
    related_options=["--use-field-description", "--use-schema-description"],
)
@freeze_time("2022-11-11")
def test_main_use_inline_field_description(output_file: Path) -> None:
    """Add field descriptions as inline comments.

    The `--use-inline-field-description` flag adds the `description` property from
    schema fields as inline comments after each field definition. This provides
    documentation without using Field() wrappers.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "api_multiline_docstrings.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_inline_field_description" / "output.py",
        extra_args=["--use-inline-field-description"],
    )


@pytest.mark.cli_doc(
    options=["--use-field-description-example"],
    option_description="""Add field examples to docstrings.

The `--use-field-description-example` flag adds the `example` or `examples`
property from schema fields as docstrings. This provides documentation that
is visible in IDE intellisense.""",
    input_schema="jsonschema/extras.json",
    cli_args=["--use-field-description-example"],
    golden_output="main_kr/main_use_field_description_example/output.py",
    related_options=["--use-field-description", "--use-inline-field-description"],
)
@freeze_time("2022-11-11")
def test_main_use_field_description_example(output_file: Path) -> None:
    """Add field examples to docstrings.

    The `--use-field-description-example` flag adds the `example` or `examples`
    property from schema fields as docstrings. This provides documentation that
    is visible in IDE intellisense.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "extras.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_field_description_example" / "output.py",
        extra_args=["--use-field-description-example"],
    )


@freeze_time("2022-11-11")
def test_main_use_field_description_example_dataclass(output_file: Path) -> None:
    """Test single example docstrings with dataclass output."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "single_line_description_with_example.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_field_description_example_dataclass" / "output.py",
        extra_args=["--use-field-description-example", "--output-model-type", "dataclasses.dataclass"],
    )


@pytest.mark.cli_doc(
    options=["--use-field-description", "--use-field-description-example"],
    option_description="""Add field descriptions and examples to docstrings.

When both `--use-field-description` and `--use-field-description-example` are used,
the docstring includes both the description and example(s).""",
    input_schema="jsonschema/extras.json",
    cli_args=["--use-field-description", "--use-field-description-example"],
    golden_output="main_kr/main_use_field_description_with_example/output.py",
    related_options=["--use-inline-field-description"],
)
@freeze_time("2022-11-11")
def test_main_use_field_description_with_example(output_file: Path) -> None:
    """Add field descriptions and examples to docstrings.

    When both `--use-field-description` and `--use-field-description-example` are used,
    the docstring includes both the description and example(s).
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "extras.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_field_description_with_example" / "output.py",
        extra_args=["--use-field-description", "--use-field-description-example"],
    )


@pytest.mark.cli_doc(
    options=["--use-inline-field-description", "--use-field-description-example"],
    option_description="""Add field descriptions and examples to docstrings with inline description.

When both `--use-inline-field-description` and `--use-field-description-example` are used,
multi-line descriptions and examples are included in the docstring.""",
    input_schema="jsonschema/multiline_description_with_example.json",
    cli_args=["--use-inline-field-description", "--use-field-description-example"],
    golden_output="main_kr/main_use_inline_field_description_with_example/output.py",
    related_options=["--use-field-description"],
)
@freeze_time("2022-11-11")
def test_main_use_inline_field_description_with_example(output_file: Path) -> None:
    """Add field descriptions and examples to docstrings with inline description.

    When both `--use-inline-field-description` and `--use-field-description-example` are used,
    multi-line descriptions and examples are included in the docstring.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "multiline_description_with_example.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_inline_field_description_with_example" / "output.py",
        extra_args=["--use-inline-field-description", "--use-field-description-example"],
    )


@freeze_time("2022-11-11")
def test_main_use_inline_field_description_example_only(output_file: Path) -> None:
    """Test single-line description with use_inline_field_description and use_field_description_example.

    When both flags are used with a single-line description, only the example
    appears in the docstring (the single-line description stays in Field()).
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "single_line_description_with_example.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_inline_field_description_example_only" / "output.py",
        extra_args=["--use-inline-field-description", "--use-field-description-example"],
    )


@freeze_time("2022-11-11")
def test_main_use_field_description_example_multiple(output_file: Path) -> None:
    """Test multiple examples in docstring.

    When a field has multiple examples, they are formatted as a bulleted list.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "multiple_examples.json",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_use_field_description_example_multiple" / "output.py",
        extra_args=["--use-field-description-example"],
    )


def test_capitalise_enum_members(tmp_path: Path) -> None:
    """Test capitalise-enum-members option (issue #2370)."""
    input_data = """
openapi: 3.0.3
info:
  version: X.Y.Z
  title: example schema
servers:
  - url: "https://acme.org"
paths: {}
components:
  schemas:
    EnumSystems:
      type: enum
      enum:
        - linux
        - osx
        - windows
"""
    input_file = tmp_path / "myschema.yaml"
    input_file.write_text(input_data, encoding="utf_8")

    expected_output = """# generated by datamodel-codegen:
#   filename:  myschema.yaml

from __future__ import annotations

from enum import Enum


class EnumSystems(Enum):
    LINUX = 'linux'
    OSX = 'osx'
    WINDOWS = 'windows'
"""

    output_file: Path = tmp_path / "output.py"
    run_main_and_assert(
        input_path=input_file,
        output_path=output_file,
        expected_output=expected_output,
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--capitalise-enum-members",
            "--snake-case-field",
        ],
    )


def test_capitalise_enum_members_and_use_subclass_enum(tmp_path: Path) -> None:
    """Test combination of capitalise-enum-members and use-subclass-enum (issue #2395)."""
    input_data = """
openapi: 3.0.3
info:
  version: X.Y.Z
  title: example schema
servers:
  - url: "https://acme.org"
paths: {}
components:
  schemas:
    EnumSystems:
      type: string
      enum:
        - linux
        - osx
        - windows
"""
    input_file = tmp_path / "myschema.yaml"
    input_file.write_text(input_data, encoding="utf_8")

    expected_output = """# generated by datamodel-codegen:
#   filename:  myschema.yaml

from __future__ import annotations

from enum import Enum


class EnumSystems(str, Enum):
    LINUX = 'linux'
    OSX = 'osx'
    WINDOWS = 'windows'
"""

    output_file: Path = tmp_path / "output.py"
    run_main_and_assert(
        input_path=input_file,
        output_path=output_file,
        expected_output=expected_output,
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--capitalise-enum-members",
            "--snake-case-field",
            "--use-subclass-enum",
        ],
    )


def test_capitalise_enum_members_builtin_conflict(output_file: Path) -> None:
    """Test capitalise-enum-members does not add underscore to builtin names (#2970)."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "enum_builtin_conflict.json",
        output_path=output_file,
        assert_func=assert_file_content,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--capitalise-enum-members",
        ],
    )


def test_capitalise_enum_members_and_use_subclass_enum_builtin_conflict(output_file: Path) -> None:
    """Test capitalise-enum-members + use-subclass-enum with builtin names (#2970)."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "enum_builtin_conflict_two.json",
        output_path=output_file,
        assert_func=assert_file_content,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--capitalise-enum-members",
            "--use-subclass-enum",
        ],
    )


def test_use_subclass_enum_builtin_conflict_no_capitalise(output_file: Path) -> None:
    """Test use-subclass-enum without capitalise adds underscore for builtin conflicts."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "enum_builtin_conflict_two.json",
        output_path=output_file,
        assert_func=assert_file_content,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
            "--use-subclass-enum",
        ],
    )


def test_no_subclass_enum_no_capitalise_builtin_names(output_file: Path) -> None:
    """Test default behavior with builtin names has no underscore suffix."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "enum_builtin_conflict_two.json",
        output_path=output_file,
        assert_func=assert_file_content,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--disable-timestamp",
        ],
    )


EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH = EXPECTED_MAIN_KR_PATH / "generate_pyproject_config"


@pytest.mark.cli_doc(
    options=["--generate-pyproject-config"],
    option_description="""Generate pyproject.toml configuration from CLI arguments.

The `--generate-pyproject-config` flag outputs a pyproject.toml configuration
snippet based on the provided CLI arguments. This is useful for converting
a working CLI command into a reusable configuration file.""",
    cli_args=["--generate-pyproject-config", "--input", "schema.yaml", "--output", "model.py"],
    expected_stdout="main_kr/generate_pyproject_config/basic.txt",
)
def test_generate_pyproject_config_basic(capsys: pytest.CaptureFixture[str]) -> None:
    """Generate pyproject.toml configuration from CLI arguments.

    The `--generate-pyproject-config` flag outputs a pyproject.toml configuration
    snippet based on the provided CLI arguments. This is useful for converting
    a working CLI command into a reusable configuration file.
    """
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--input",
            "schema.yaml",
            "--output",
            "model.py",
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "basic.txt",
    )


def test_generate_pyproject_config_with_boolean_options(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-pyproject-config with boolean options."""
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--snake-case-field",
            "--use-annotated",
            "--collapse-root-models",
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "boolean_options.txt",
    )


def test_generate_pyproject_config_with_list_options(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-pyproject-config with list options."""
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--strict-types",
            "str",
            "int",
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "list_options.txt",
    )


def test_generate_pyproject_config_with_float_option(capsys: pytest.CaptureFixture[str]) -> None:
    """Serialize finite floating-point CLI options as TOML values."""
    run_main_with_args(
        ["--generate-pyproject-config", "--http-timeout", "1.5"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "float_option.txt",
        assert_no_stderr=True,
    )


def test_generate_pyproject_config_float_round_trip(tmp_path: Path, output_file: Path) -> None:
    """Preserve normal output when generated floating-point config is used."""
    config_output = generate_pyproject_config(Namespace(http_timeout=1.5))
    assert_output(config_output, EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "float_option_helper.txt")
    tomllib.loads(config_output)
    expected_output_path = DATA_PATH / "expected" / "main" / "person.py"

    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--http-timeout", "1.5"],
    )
    assert_output(output_file.read_text(encoding="utf-8"), expected_output_path)

    config_output_file = tmp_path / "from_config.py"
    (tmp_path / "pyproject.toml").write_text(config_output, encoding="utf-8")
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "person.json",
            output_path=config_output_file,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp"],
        )

    assert_output(config_output_file.read_text(encoding="utf-8"), expected_output_path)


def test_generate_pyproject_config_with_multiple_options(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-pyproject-config with various option types."""
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--input",
            "schema.yaml",
            "--output",
            "model.py",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.11",
            "--snake-case-field",
            "--strict-types",
            "str",
            "bytes",
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "multiple_options.txt",
    )


def test_generate_pyproject_config_excludes_meta_options(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that meta options are excluded from generated config."""
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--input",
            "schema.yaml",
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "excludes_meta_options.txt",
    )


def test_generate_pyproject_config_with_enum_option(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-pyproject-config with Enum option."""
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--input",
            "schema.yaml",
            "--read-only-write-only-model-type",
            "all",
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "enum_option.txt",
    )


def test_generate_pyproject_config_escapes_toml_strings(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-pyproject-config escapes special characters in TOML basic strings."""
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--input",
            "schema.yaml",
            "--custom-file-header",
            'say "hi" on C:\\tmp\nnext',
            "--http-headers",
            'Authorization: Bearer "abc"',
            "X-Path: C:\\tmp",
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "escapes_toml_strings.txt",
    )


EXPECTED_GENERATE_CLI_COMMAND_PATH = EXPECTED_MAIN_KR_PATH / "generate_cli_command"


@pytest.mark.cli_doc(
    options=["--generate-cli-command"],
    option_description="""Generate CLI command from pyproject.toml configuration.

The `--generate-cli-command` flag reads your pyproject.toml configuration
and outputs the equivalent CLI command. This is useful for debugging
configuration issues or sharing commands with others.""",
    cli_args=["--generate-cli-command"],
    config_content="""[tool.datamodel-codegen]
input = "schema.yaml"
output = "model.py"
""",
    expected_stdout="main_kr/generate_cli_command/basic.txt",
)
def test_generate_cli_command_basic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Generate CLI command from pyproject.toml configuration.

    The `--generate-cli-command` flag reads your pyproject.toml configuration
    and outputs the equivalent CLI command. This is useful for debugging
    configuration issues or sharing commands with others.
    """
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
output = "model.py"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "basic.txt",
        )


def test_generate_cli_command_with_boolean_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with boolean options."""
    pyproject_toml = """
[tool.datamodel-codegen]
snake-case-field = true
use-annotated = true
collapse-root-models = true
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "boolean_options.txt",
        )


def test_generate_cli_command_with_list_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with list options."""
    pyproject_toml = """
[tool.datamodel-codegen]
strict-types = ["str", "int"]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "list_options.txt",
        )


def test_generate_cli_command_with_multiple_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with various option types."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
output = "model.py"
output-model-type = "pydantic_v2.BaseModel"
target-python-version = "3.11"
snake-case-field = true
strict-types = ["str", "bytes"]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "multiple_options.txt",
        )


def test_generate_cli_command_no_config(tmp_path: Path) -> None:
    """Test --generate-cli-command when no config found."""
    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            expected_exit=Exit.ERROR,
        )


def test_generate_cli_command_with_no_use_specialized_enum(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with use-specialized-enum = false."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
use-specialized-enum = false
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "no_use_specialized_enum.txt",
        )


def test_generate_cli_command_with_spaces_in_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command with spaces in values."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "my schema.yaml"
output = "my model.py"
http-headers = ["Authorization: Bearer token", "X-Custom: value"]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "spaces_in_values.txt",
        )


def test_generate_cli_command_with_false_boolean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command preserves the existing negative snake-case flag."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
snake-case-field = false
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "false_boolean.txt",
        )


def test_generate_cli_command_with_false_boolean_optional_actions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test --generate-cli-command preserves false BooleanOptionalAction values."""
    shutil.copy(FALSE_BOOLEAN_OPTIONAL_ACTIONS_PYPROJECT, tmp_path / "pyproject.toml")

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "false_boolean_optional_actions.txt",
        )


def test_generate_cli_command_json_with_false_boolean_optional_actions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test JSON CLI command output preserves false BooleanOptionalAction values."""
    shutil.copy(FALSE_BOOLEAN_OPTIONAL_ACTIONS_PYPROJECT, tmp_path / "pyproject.toml")

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command", "--output-format", "json"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "cli_command_false_boolean_optional_actions.txt",
            assert_no_stderr=True,
        )


@pytest.mark.parametrize(
    ("input_name", "output_model_type", "expected_file"),
    [
        ("person.json", "pydantic_v2.BaseModel", "generate_cli_command/no_use_union_operator.py"),
        ("typed_dict_closed.json", "typing.TypedDict", "generate_cli_command/no_use_closed_typed_dict.py"),
    ],
)
def test_generate_cli_command_reconstructs_false_boolean_optional_actions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    input_name: str,
    output_model_type: str,
    expected_file: str,
) -> None:
    """Test reconstructed CLI commands retain false BooleanOptionalAction behavior."""
    shutil.copy(JSON_SCHEMA_DATA_PATH / input_name, tmp_path / "schema.json")
    (tmp_path / "pyproject.toml").write_text(
        (DATA_PATH / "config" / "false_boolean_round_trip.toml")
        .read_text(encoding="utf-8")
        .format(output_model_type=output_model_type),
        encoding="utf-8",
    )
    config_output = tmp_path / "from-config.py"
    command_output = tmp_path / "from-command.py"

    with chdir(tmp_path):
        run_main_with_args(["--input", "schema.json", "--output", str(config_output)])
        run_main_with_args(["--generate-cli-command", "--output-format", "json"], capsys=capsys)
        generated_command = json.loads(capsys.readouterr().out)
        run_main_with_args([
            *generated_command["arguments"][1:],
            "--ignore-pyproject",
            "--output",
            str(command_output),
        ])

    assert_file_content(config_output, expected_file)
    assert_file_content(command_output, expected_file)
    validate_generated_code(config_output.read_text(), str(config_output), do_exec=True)
    validate_generated_code(command_output.read_text(), str(command_output), do_exec=True)


def test_generate_cli_command_with_mapping_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command prints table options in the form their CLI options accept."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
aliases = {"first name" = "first_name", id = ["id_", "identifier"]}
extra-template-data = {Person = {comment = "it's ü $HOME"}}
dataclass-arguments = {frozen = true, kw_only = false}
external-ref-mapping = {"../common/a b.yaml" = "pkg.models", "other.yaml" = "pkg.other"}
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml, encoding="utf-8")

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "mapping_options.txt",
        )


def test_generate_cli_command_reconstructs_mapping_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test reconstructed CLI commands apply table options like the pyproject configuration."""
    shutil.copy(JSON_SCHEMA_DATA_PATH / "person.json", tmp_path / "schema.json")
    shutil.copy(DATA_PATH / "config" / "mapping_round_trip.toml", tmp_path / "pyproject.toml")
    config_output = tmp_path / "from-config.py"
    command_output = tmp_path / "from-command.py"

    with chdir(tmp_path):
        run_main_with_args(["--output", str(config_output)])
        run_main_with_args(["--generate-cli-command", "--output-format", "json"], capsys=capsys)
        generated_command = json.loads(capsys.readouterr().out)
        run_main_with_args([
            *generated_command["arguments"][1:],
            "--ignore-pyproject",
            "--output",
            str(command_output),
        ])

    assert_file_content(config_output, "generate_cli_command/mapping_round_trip.py")
    assert_file_content(command_output, "generate_cli_command/mapping_round_trip.py")
    validate_generated_code(command_output.read_text(), str(command_output), do_exec=True)


def test_generate_cli_command_excludes_excluded_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command excludes options like debug, version, etc."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
debug = true
version = true
no-color = true
disable-warnings = true
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "excluded_options.txt",
        )


EXPECTED_PYPROJECT_PROFILE_PATH = EXPECTED_MAIN_KR_PATH / "pyproject_profile"


@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.0.0"),
    reason="black 22.x doesn't support Python 3.11 target version",
)
@freeze_time("2019-07-26")
def test_pyproject_with_profile(output_file: Path, tmp_path: Path) -> None:
    """Test loading a named profile from pyproject.toml."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
enable-version-header = false

[tool.datamodel-codegen.profiles.api]
target-python-version = "3.11"
snake-case-field = true
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = """
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "firstName": {"type": "string"},
    "lastName": {"type": "string"}
  }
}
"""
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "with_profile.py",
            extra_args=["--profile", "api", "--disable-timestamp"],
        )


def test_pyproject_profile_not_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when profile is not found."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_file = tmp_path / "schema.json"
    input_file.write_text('{"type": "object"}')

    output_file = tmp_path / "output.py"

    with chdir(tmp_path):
        run_main_with_args(
            ["--input", str(input_file), "--output", str(output_file), "--profile", "nonexistent"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
        )
        assert_error_message(capsys, "Profile 'nonexistent' not found in pyproject.toml")


@freeze_time("2019-07-26")
def test_ignore_pyproject_option(output_file: Path, tmp_path: Path) -> None:
    """Test --ignore-pyproject ignores pyproject.toml configuration."""
    pyproject_toml = """
[tool.datamodel-codegen]
snake-case-field = true
enable-version-header = true
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = """
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "firstName": {"type": "string"},
    "lastName": {"type": "string"}
  }
}
"""
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "ignore_pyproject.py",
            extra_args=["--ignore-pyproject", "--disable-timestamp"],
        )


def test_pyproject_profile_rejects_non_table_profiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Report a clean configuration error when the profiles value is not a TOML table."""
    input_file = JSON_SCHEMA_DATA_PATH / "person.json"
    output_file = tmp_path / "output.py"
    (tmp_path / "pyproject.toml").write_text(
        '[tool.datamodel-codegen]\nprofiles = "invalid"\n',
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--input", str(input_file), "--output", str(output_file), "--profile", "api"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="profiles] must be a table",
        )

    _assert_file_does_not_exist(output_file)


@freeze_time("2019-07-26")
def test_profile_overrides_base_config_shallow_merge(output_file: Path, tmp_path: Path) -> None:
    """Test that profile settings shallow-merge (replace) base settings for lists."""
    pyproject_toml = """
[tool.datamodel-codegen]
strict-types = ["str", "int"]
target-python-version = "3.10"
enable-version-header = false

[tool.datamodel-codegen.profiles.api]
strict-types = ["bytes"]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = """
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "data": {"type": "string", "format": "binary"}
  }
}
"""
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "shallow_merge.py",
            extra_args=["--profile", "api", "--disable-timestamp"],
        )


def test_generate_cli_command_with_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command reflects merged profile settings."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
snake-case-field = true

[tool.datamodel-codegen.profiles.api]
input = "api.yaml"
target-python-version = "3.11"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            ["--profile", "api", "--generate-cli-command"],
            capsys=capsys,
            expected_stdout_path=EXPECTED_GENERATE_CLI_COMMAND_PATH / "with_profile.txt",
        )


def test_pyproject_job_runs_selected_profile(jobs_project: dict[str, Path], tmp_path: Path) -> None:
    """Run one named job with its reusable profile settings."""
    with chdir(tmp_path):
        run_main_with_args(["--job", "strict", "--formatters", "builtin"])

    _assert_file_does_not_exist(jobs_project["plain"])
    assert_file_content(jobs_project["strict"], "jobs/strict.py")


def test_pyproject_jobs_run_in_toml_declaration_order(jobs_project: dict[str, Path], tmp_path: Path) -> None:
    """Run selected jobs in TOML order even when CLI selection order differs."""
    with chdir(tmp_path):
        run_main_with_args(["--job", "strict", "--job", "plain", "--output-format", "json", "--formatters", "builtin"])

    assert_output(jobs_project["plain"].read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert_file_content(jobs_project["strict"], "jobs/strict.py")


def test_pyproject_all_jobs_runs_every_job(jobs_project: dict[str, Path], tmp_path: Path) -> None:
    """Run every named job from the project configuration."""
    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    assert_output(jobs_project["plain"].read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert_file_content(jobs_project["strict"], "jobs/strict.py")


def test_selected_jobs_defensively_rejects_all_jobs_with_named_job() -> None:
    """Reject an invalid namespace even if selection bypasses argument parsing."""
    args = Namespace(all_jobs=True, job=["plain"])

    with pytest.raises(Error, match="--all-jobs cannot be used with --job"):
        _selected_jobs(args, {"plain": {}})


def test_pyproject_job_command_header_uses_batch_invocation(jobs_project: dict[str, Path], tmp_path: Path) -> None:
    """Keep the reproducible job-selection command in generated file headers."""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf-8").replace(
            "enable-version-header = false", "enable-version-header = false\nenable-command-header = true"
        ),
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(["--job", "strict", "--formatters", "builtin"])

    assert_file_content(jobs_project["strict"], "jobs/strict_command_header.py")


def test_pyproject_jobs_check_does_not_write_output(jobs_project: dict[str, Path], tmp_path: Path) -> None:
    """Check every job without replacing a stale generated file."""
    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])
        run_main_with_args(["--all-jobs", "--check", "--formatters", "builtin"])
        jobs_project["strict"].write_text("stale\n", encoding="utf-8")
        run_main_with_args(["--all-jobs", "--check", "--formatters", "builtin"], expected_exit=Exit.DIFF)

    assert_file_content(jobs_project["strict"], "jobs/stale.py")


@pytest.mark.usefixtures("jobs_project")
def test_pyproject_jobs_json_is_one_ordered_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Emit one structured JSON document whose results follow TOML declaration order."""
    with chdir(tmp_path):
        run_main_with_args(["--job", "strict", "--job", "plain", "--output-format", "json", "--formatters", "builtin"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    relative_outputs = [Path(job["result"]["output"]).relative_to(tmp_path).as_posix() for job in payload["jobs"]]
    assert_output("\n".join(relative_outputs) + "\n", EXPECTED_MAIN_KR_PATH / "jobs" / "ordered_outputs.txt")
    assert_output(_batch_json_summary(output), EXPECTED_MAIN_KR_PATH / "jobs" / "json_generation.txt")


def test_pyproject_jobs_json_reports_check_results(
    jobs_project: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Preserve each job's check payload and the batch difference exit code."""
    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])
        jobs_project["strict"].write_text("stale\n", encoding="utf-8")
        run_main_with_args(
            ["--all-jobs", "--check", "--output-format", "json", "--formatters", "builtin"], expected_exit=Exit.DIFF
        )

    assert_output(_batch_json_summary(capsys.readouterr().out), EXPECTED_MAIN_KR_PATH / "jobs" / "json_check.txt")


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ("not JSON", "returned invalid JSON batch output"),
        ('{"kind":"unexpected"}', "kind 'unexpected'"),
        ("[]", "raw JSON '[]'"),
    ],
)
def test_pyproject_jobs_json_rejects_invalid_inner_payload_without_stdout(
    jobs_project: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    error: str,
) -> None:
    """Return a clean CLI error rather than partial batch JSON for malformed inner job output."""
    monkeypatch.setattr(main_module, "_generation_output_json", lambda *_args, **_kwargs: payload)

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--output-format", "json", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stdout_path=EXPECTED_EMPTY_OUTPUT_PATH,
            expected_stderr_contains=error,
        )

    _assert_file_does_not_exist(jobs_project["plain"])
    _assert_file_does_not_exist(jobs_project["strict"])


def test_pyproject_jobs_json_publish_failure_has_no_batch_payload(
    jobs_project: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not emit a successful-looking batch JSON document when transactional publication fails."""
    monkeypatch.setattr(
        main_module,
        "_publish_staged_job_plans",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated publish failure")),
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--output-format", "json", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stdout_path=EXPECTED_EMPTY_OUTPUT_PATH,
            expected_stderr_contains="could not publish batch output",
        )

    _assert_file_does_not_exist(jobs_project["plain"])
    _assert_file_does_not_exist(jobs_project["strict"])


def test_pyproject_jobs_json_spool_failure_has_no_batch_payload(
    jobs_project: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a temporary spool failure without publishing code or emitting partial JSON."""
    monkeypatch.setattr(
        main_module.tempfile,
        "TemporaryFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated JSON spool failure")),
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--output-format", "json", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stdout_path=EXPECTED_EMPTY_OUTPUT_PATH,
            expected_stderr_contains="could not spool batch JSON output",
        )

    _assert_file_does_not_exist(jobs_project["plain"])
    _assert_file_does_not_exist(jobs_project["strict"])


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--job", "plain", "--generate-cli-command"], "--generate-cli-command cannot be used"),
        (["--job", "plain", "--list-deprecations"], "--list-deprecations cannot be used"),
        (["--all-jobs", "--list-experimental"], "--list-experimental cannot be used"),
    ],
)
def test_pyproject_jobs_reject_cli_command_only_modes(
    jobs_project: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str
) -> None:
    """Reject command-only modes before a selected job can generate output."""
    with chdir(tmp_path):
        run_main_with_args(args, expected_exit=Exit.ERROR, capsys=capsys, expected_stderr_contains=message)

    _assert_file_does_not_exist(jobs_project["plain"])
    _assert_file_does_not_exist(jobs_project["strict"])


@pytest.mark.parametrize("provenance", ["cli", "base", "profile", "job"])
def test_pyproject_jobs_reject_input_diff_from_every_configuration_layer_without_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], provenance: str
) -> None:
    """Reject inherited or explicit input comparison before staging, publication, or watch startup."""
    old_input = (JSON_SCHEMA_DATA_PATH / "person.json").resolve()
    new_input = (JSON_SCHEMA_DATA_PATH / "person.json").resolve()
    output_paths = (tmp_path / "api.py", tmp_path / "events.py")
    metadata_paths = (tmp_path / "api.metadata.json", tmp_path / "events.metadata.json")
    configurations = {
        "cli": (["--job", "api", "--job", "events"], ["--diff-against", old_input.as_posix()], "", "", "", ""),
        "base": (["--all-jobs"], [], f'diff-against = "{old_input.as_posix()}"', "", "", "watch = true"),
        "profile": (["--job", "api"], [], "", f'diff-against = "{old_input.as_posix()}"', "", ""),
        "job": (["--job", "api"], [], "", "", f'diff-against = "{old_input.as_posix()}"', ""),
    }
    selection, cli_diff, base_diff, profile_diff, job_diff, base_watch = configurations[provenance]

    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"
{base_diff}
{base_watch}

[tool.datamodel-codegen.profiles.compare]
{profile_diff}

[tool.datamodel-codegen.jobs.api]
profile = "compare"
input = "{new_input.as_posix()}"
output = "{output_paths[0].as_posix()}"
emit-model-metadata = "{metadata_paths[0].as_posix()}"
{job_diff}

[tool.datamodel-codegen.jobs.events]
input = "{new_input.as_posix()}"
output = "{output_paths[1].as_posix()}"
emit-model-metadata = "{metadata_paths[1].as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            [*selection, *cli_diff, "--output-format", "json", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stdout_path=EXPECTED_EMPTY_OUTPUT_PATH,
            expected_stderr_contains="--diff-against cannot be used with --job or --all-jobs",
        )

    for path in (*output_paths, *metadata_paths):
        _assert_file_does_not_exist(path)


def test_pyproject_job_custom_formatter_resolves_relative_resources_from_original_output(
    tmp_path: Path,
) -> None:
    """Keep formatter resource lookup at the logical output while publishing from staging."""
    output_path = tmp_path / "generated" / "models.py"
    output_path.parent.mkdir()
    shutil.copyfile(
        DATA_PATH / "python" / "custom_formatters" / "license_example.txt",
        output_path.parent / "license.txt",
    )
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.api]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").resolve().as_posix()}"
output = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args([
            "--job",
            "api",
            "--formatters",
            "builtin",
            "--custom-formatters",
            "tests.data.python.custom_formatters.add_license",
            "--custom-formatters-kwargs",
            (DATA_PATH / "config" / "input_diff_relative_license.json").resolve().as_posix(),
        ])

    assert_file_content(output_path, "jobs/custom_formatter.py")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--all-jobs", "--watch", "--check"], "--watch and --check cannot be used together"),
        (
            ["--all-jobs", "--watch", "--output-format", "json"],
            "--output-format json cannot be used with --watch",
        ),
    ],
)
def test_pyproject_jobs_reject_incompatible_watch_modes(
    jobs_project: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    """Validate the outer batch scheduler before generation or watcher startup."""
    with chdir(tmp_path):
        run_main_with_args(args, expected_exit=Exit.ERROR, capsys=capsys, expected_stderr_contains=message)

    _assert_file_does_not_exist(jobs_project["plain"])
    _assert_file_does_not_exist(jobs_project["strict"])


def test_pyproject_jobs_report_batch_watch_startup_error(
    jobs_project: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report a watcher startup failure after the initial transaction publishes."""
    from datamodel_code_generator import watch

    def fail_watch(*_args: object, **_kwargs: object) -> Exit:
        msg = "batch watcher startup failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(watch, "watch_and_regenerate", fail_watch)
    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--watch", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="batch watcher startup failed",
        )

    assert_output(jobs_project["plain"].read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    assert_file_content(jobs_project["strict"], "jobs/strict.py")


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_watch_retains_failed_initial_generation_dependencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain a full batch graph when its initial watched generation cannot be staged."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    input_path = tmp_path / "schema.json"
    output_path = tmp_path / "output.py"
    input_path.write_text((JSON_SCHEMA_DATA_PATH / "person.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"
watch = true

[tool.datamodel-codegen.jobs.models]
input = "{input_path.as_posix()}"
output = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    def fail_staging(_plans: object) -> tuple[()]:
        msg = "simulated watched staging failure"
        raise OSError(msg)

    monkeypatch.setattr(main_module, "_stage_job_plans", fail_staging)
    dependencies = WatchDependencies()

    with chdir(tmp_path):
        assert main_module._main(["--all-jobs"], start_watch=False, dependencies=dependencies) is Exit.ERROR

    assert_error_message(capsys, "could not prepare batch output staging: simulated watched staging failure")
    assert dependencies.includes(input_path)
    assert dependencies.includes(tmp_path / "pyproject.toml")
    assert output_path in dependencies.outputs


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_watch_records_raw_dependencies_after_invalid_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep raw profile, JSON, and input paths observable when a batch plan is invalid."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """
[tool.datamodel-codegen]
input-file-type = "jsonschema"
aliases = "base-aliases.json"

[tool.datamodel-codegen.profiles]
invalid = "not a profile table"

[tool.datamodel-codegen.profiles.base]
extends = "root"
aliases = "base-profile-aliases.json"

[tool.datamodel-codegen.profiles.root]
aliases = "root-profile-aliases.json"

[tool.datamodel-codegen.profiles.child]
extends = ["base", "child", "invalid"]
default-values = "child-defaults.json"

[tool.datamodel-codegen.jobs.models]
profile = "child"
input = "missing-schema.json"
output = "output.py"
aliases = []
default-values = "job-defaults.json"

[tool.datamodel-codegen.jobs.unprofiled]
input = "unprofiled-missing-schema.json"
output = "unprofiled-output.py"
aliases = "unprofiled-aliases.json"
""",
        encoding="utf-8",
    )
    dependencies = WatchDependencies()

    with chdir(tmp_path):
        assert main_module._main(["--all-jobs"], start_watch=False, dependencies=dependencies) is Exit.ERROR

    assert_error_message(capsys, "Profile 'child' cannot extend itself")
    for dependency in (
        pyproject_path,
        tmp_path / "base-aliases.json",
        tmp_path / "base-profile-aliases.json",
        tmp_path / "root-profile-aliases.json",
        tmp_path / "child-defaults.json",
        tmp_path / "job-defaults.json",
        tmp_path / "missing-schema.json",
        tmp_path / "unprofiled-aliases.json",
        tmp_path / "unprofiled-missing-schema.json",
    ):
        assert dependencies.includes(dependency)

    find_project_config = main_module._find_datamodel_codegen_project_config_with_path

    def fail_project_lookup(_path: Path) -> tuple[Path, dict[str, object]]:
        msg = "simulated project lookup failure"
        raise OSError(msg)

    monkeypatch.setattr(main_module, "_find_datamodel_codegen_project_config_with_path", fail_project_lookup)
    main_module._record_raw_batch_watch_dependencies(Namespace(all_jobs=True, job=None), dependencies)
    monkeypatch.setattr(main_module, "_find_datamodel_codegen_project_config_with_path", find_project_config)

    pyproject_path.write_text('[tool.datamodel-codegen]\njobs = "invalid"\n', encoding="utf-8")
    invalid_jobs_dependencies = WatchDependencies()
    with chdir(tmp_path):
        assert (
            main_module._main(["--all-jobs"], start_watch=False, dependencies=invalid_jobs_dependencies) is Exit.ERROR
        )
    assert_error_message(capsys, "No jobs found in [tool.datamodel-codegen.jobs]")
    assert invalid_jobs_dependencies.includes(pyproject_path)

    pyproject_path.write_text('[tool.datamodel-codegen.jobs]\nmodels = "invalid"\n', encoding="utf-8")
    invalid_job_dependencies = WatchDependencies()
    with chdir(tmp_path):
        assert main_module._main(["--all-jobs"], start_watch=False, dependencies=invalid_job_dependencies) is Exit.ERROR
    assert_error_message(capsys, "Job 'models' must be a table")
    assert invalid_job_dependencies.includes(pyproject_path)


@pytest.mark.parametrize(
    "command_option",
    ["list-deprecations", "list-experimental"],
)
def test_pyproject_jobs_reject_configured_command_only_modes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command_option: str
) -> None:
    """Reject job command-only modes that would otherwise skip generation."""
    output_path = tmp_path / "output.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.api]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{output_path.as_posix()}"
{command_option} = "table"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--job", "api"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="jobs must generate code",
        )

    _assert_file_does_not_exist(output_path)


@pytest.mark.parametrize(
    ("base_config", "profile_config", "job_profile"),
    [
        ('url = "https://example.invalid/schema.json"', "", ""),
        ("", 'input-model = ["missing.py"]', 'profile = "inherited"'),
    ],
)
def test_pyproject_job_input_clears_inherited_alternate_sources(
    tmp_path: Path,
    base_config: str,
    profile_config: str,
    job_profile: str,
) -> None:
    """Use the job file input instead of an inherited URL or input-model source."""
    output_path = tmp_path / "output.py"
    profile = f"\n[tool.datamodel-codegen.profiles.inherited]\n{profile_config}\n" if profile_config else ""
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"
{base_config}
{profile}
[tool.datamodel-codegen.jobs.api]
{job_profile}
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(["--job", "api", "--formatters", "builtin"])

    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")


def test_pyproject_job_rejects_hyphenated_input_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject the TOML spelling of an alternate input source before generation starts."""
    output_path = tmp_path / "output.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.api]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
input-model = ["example.Model"]
output = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--job", "api"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="only supports an 'input' file",
        )

    _assert_file_does_not_exist(output_path)


@pytest.mark.allow_direct_assert
def test_pyproject_job_plan_preserves_watch_provenance(jobs_project: dict[str, Path], tmp_path: Path) -> None:
    """Keep the resolved raw config, CLI settings, and project path for batch watch planning."""
    with chdir(tmp_path):
        args = arg_parser.parse_args([
            "--job",
            "plain",
            "--formatters",
            "builtin",
            "--watch",
            "--watch-delay",
            "0.25",
        ])
        batch_plan = _plan_jobs(args)
        plan = batch_plan.jobs[0]

    assert batch_plan.watch
    assert batch_plan.watch_delay == pytest.approx(0.25)
    assert not plan.config.watch
    assert plan.config.watch_delay == pytest.approx(0.5)
    assert plan.raw_config["input"] == (JSON_SCHEMA_DATA_PATH / "person.json").as_posix()
    assert plan.raw_config["output"] == jobs_project["plain"].as_posix()
    assert plan.cli_config_args["formatters"] == ["builtin"]
    assert plan.pyproject_path == tmp_path / "pyproject.toml"


@pytest.mark.allow_direct_assert
def test_pyproject_job_plan_uses_base_watch_scheduler(jobs_project: dict[str, Path], tmp_path: Path) -> None:
    """Apply base watch settings only to the outer batch scheduler."""
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf-8").replace(
            "disable-timestamp = true",
            "disable-timestamp = true\nwatch = true\nwatch-delay = 0.75",
        ),
        encoding="utf-8",
    )
    with chdir(tmp_path):
        batch_plan = _plan_jobs(arg_parser.parse_args(["--all-jobs"]))

    assert batch_plan.watch
    assert batch_plan.watch_delay == pytest.approx(0.75)
    assert all(not plan.config.watch and plan.config.watch_delay == pytest.approx(0.5) for plan in batch_plan.jobs)
    _assert_file_does_not_exist(jobs_project["plain"])
    _assert_file_does_not_exist(jobs_project["strict"])


@pytest.mark.parametrize(
    ("payload", "context"),
    [
        ('{"kind":"unexpected"}', "kind 'unexpected'"),
        ("[]", "raw JSON '[]'"),
    ],
)
@pytest.mark.usefixtures("jobs_project")
def test_pyproject_jobs_json_handles_unsupported_inner_payload(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    context: str,
) -> None:
    """Return a CLI error if a future inner job emits an unsupported JSON shape."""
    monkeypatch.setattr(
        "datamodel_code_generator.__main__._generation_output_json",
        lambda *_args, **_kwargs: payload,
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--job", "plain", "--output-format", "json", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains=context,
        )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--job", "missing"], "Job 'missing' not found"),
        (["--job", "plain", "--input", "schema.json"], "--input cannot be used"),
        (["--job", "plain", "--profile", "strict"], "--profile cannot be used"),
        (["--job", "plain", "--ignore-pyproject"], "--ignore-pyproject cannot be used"),
    ],
)
def test_pyproject_jobs_reject_job_specific_cli_options(
    jobs_project: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str
) -> None:
    """Reject ambiguous selection and per-job CLI settings before generation starts."""
    with chdir(tmp_path):
        run_main_with_args(args, expected_exit=Exit.ERROR, capsys=capsys, expected_stderr_contains=message)

    _assert_file_does_not_exist(jobs_project["plain"])
    _assert_file_does_not_exist(jobs_project["strict"])


def test_pyproject_jobs_preflight_rejects_overlapping_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject output overlap before the first job writes generated code."""
    output_path = tmp_path / "generated.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.first]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{(tmp_path / "generated" / ".." / "generated.py").as_posix()}"

[tool.datamodel-codegen.jobs.second]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="overlapping output paths",
        )

    _assert_file_does_not_exist(output_path)


@pytest.mark.parametrize(
    "writer_artifact",
    [
        'output = "$PROTECTED_INPUT"',
        'output = "$WRITER_OUTPUT"\nemit-model-metadata = "$PROTECTED_INPUT"',
        'output = "$PROTECTED_PARENT"',
    ],
)
def test_pyproject_jobs_preflight_protects_other_job_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], writer_artifact: str
) -> None:
    """Reject output or metadata paths that could overwrite another job's input."""
    source_input = JSON_SCHEMA_DATA_PATH / "person.json"
    protected_parent = tmp_path / "schemas"
    protected_parent.mkdir()
    protected_input = protected_parent / "schema.json"
    protected_input.write_text(source_input.read_text(encoding="utf-8"), encoding="utf-8")
    reader_output = tmp_path / "reader.py"
    pyproject = f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.writer]
input = "{source_input.as_posix()}"
{writer_artifact}

[tool.datamodel-codegen.jobs.reader]
input = "{protected_input.as_posix()}"
output = "{reader_output.as_posix()}"
"""
    pyproject = pyproject.replace("$PROTECTED_INPUT", protected_input.as_posix())
    pyproject = pyproject.replace("$PROTECTED_PARENT", protected_parent.as_posix())
    pyproject = pyproject.replace("$WRITER_OUTPUT", (tmp_path / "writer.py").as_posix())
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="overlaps input for job",
        )

    assert_output(
        protected_input.read_text(encoding="utf-8") + "\n", EXPECTED_MAIN_KR_PATH / "jobs" / "protected_schema.txt"
    )
    _assert_file_does_not_exist(reader_output)


def test_pyproject_jobs_reject_non_table_definition(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject malformed TOML job entries before any output can be written."""
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.datamodel-codegen]
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs]
invalid = "not a table"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="must be a table",
        )


@pytest.mark.parametrize(
    ("output_format", "existing_content"),
    [
        ([], None),
        (["--output-format", "json"], "stale generated output\n"),
    ],
)
def test_pyproject_jobs_abort_runtime_errors_without_publishing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    output_format: list[str],
    existing_content: str | None,
) -> None:
    """Retain existing output and metadata when a later external input cannot generate."""
    good_output = tmp_path / "good.py"
    metadata_output = tmp_path / "good.metadata.json"
    bad_input = DATA_PATH / "json" / "broken.json"
    if existing_content is not None:
        good_output.write_text(existing_content, encoding="utf-8")
        metadata_output.write_text("stale metadata\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.good]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{good_output.as_posix()}"
emit-model-metadata = "{metadata_output.as_posix()}"

[tool.datamodel-codegen.jobs.invalid]
input = "{bad_input.as_posix()}"
output = "{(tmp_path / "invalid.py").as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin", *output_format],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="Invalid file format for jsonschema",
        )

    if existing_content is None:
        _assert_file_does_not_exist(good_output)
        _assert_file_does_not_exist(metadata_output)
    else:
        assert_output(good_output.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale_output.txt")
        assert_output(
            metadata_output.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale_metadata.txt"
        )


def test_pyproject_jobs_publish_model_metadata(tmp_path: Path) -> None:
    """Publish generated code and model metadata only after their job succeeds."""
    output_path = tmp_path / "generated" / "generated.py"
    metadata_path = tmp_path / "generated" / "generated.metadata.json"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.generated]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{output_path.as_posix()}"
emit-model-metadata = "{metadata_path.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    metadata_version = json.loads(metadata_path.read_text(encoding="utf-8"))["version"]
    assert_output(f"{metadata_version}\n", EXPECTED_MAIN_KR_PATH / "jobs" / "metadata_version.txt")


def test_pyproject_jobs_directory_output_overlays_generated_files(tmp_path: Path) -> None:
    """Replace only generated files in a directory job and retain unrelated existing files."""
    output_path = tmp_path / "models"
    extra_file = output_path / "keep.py"
    output_path.mkdir()
    extra_file.write_text("stale\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "openapi"

[tool.datamodel-codegen.jobs.models]
input = "{(OPEN_API_DATA_PATH / "modular.yaml").as_posix()}"
output = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    assert_output(extra_file.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py")
    assert_output(
        (output_path / "__init__.py").read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "modular_init.py"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="hardlink creation requires elevated privileges")
def test_pyproject_jobs_preflight_rejects_hardlinked_other_job_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject a batch output hardlinked to another job's external input without mutating it."""
    external_input = tmp_path / "external-person.json"
    external_input.write_text((JSON_SCHEMA_DATA_PATH / "person.json").read_text(encoding="utf-8"), encoding="utf-8")
    hardlinked_output = tmp_path / "hardlinked.py"
    hardlinked_output.hardlink_to(external_input)
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.writer]
input = "{(JSON_SCHEMA_DATA_PATH / "pet_simple.json").as_posix()}"
output = "{hardlinked_output.as_posix()}"

[tool.datamodel-codegen.jobs.reader]
input = "{external_input.as_posix()}"
output = "{(tmp_path / "reader.py").as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="overlaps input for job 'reader'",
        )

    assert_output(
        hardlinked_output.read_text(encoding="utf-8") + "\n", EXPECTED_MAIN_KR_PATH / "jobs" / "protected_schema.txt"
    )
    assert_output(
        external_input.read_text(encoding="utf-8") + "\n", EXPECTED_MAIN_KR_PATH / "jobs" / "protected_schema.txt"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="directory symlink creation requires elevated privileges")
def test_pyproject_jobs_reject_nested_output_symlink_escape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject a generated directory file that would escape through an existing nested symlink."""
    output_path = tmp_path / "models"
    protected_directory = tmp_path / "schemas"
    protected_input = protected_directory / "bar.py"
    output_path.mkdir()
    protected_directory.mkdir()
    protected_input.write_text((JSON_SCHEMA_DATA_PATH / "person.json").read_text(encoding="utf-8"), encoding="utf-8")
    (output_path / "foo").symlink_to(protected_directory, target_is_directory=True)
    reader_output = tmp_path / "reader.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true

[tool.datamodel-codegen.jobs.models]
input = "{(OPEN_API_DATA_PATH / "modular.yaml").as_posix()}"
output = "{output_path.as_posix()}"
input-file-type = "openapi"

[tool.datamodel-codegen.jobs.reader]
input = "{protected_input.as_posix()}"
output = "{reader_output.as_posix()}"
input-file-type = "jsonschema"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="generated file escapes its output path",
        )

    assert_output(
        protected_input.read_text(encoding="utf-8") + "\n", EXPECTED_MAIN_KR_PATH / "jobs" / "protected_schema.txt"
    )
    _assert_file_does_not_exist(reader_output)


@pytest.mark.skipif(sys.platform == "win32", reason="directory symlink creation requires elevated privileges")
def test_pyproject_jobs_rejects_output_root_swapped_after_preflight(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the output root resolved during planning if an output ancestor is swapped before staging."""
    output_root = tmp_path / "output"
    original_root = tmp_path / "original-output"
    attacker_root = tmp_path / "attacker-output"
    output_path = output_root / "generated.py"
    output_root.mkdir()
    attacker_root.mkdir()
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.api]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    original_stage_job_plan = main_module._stage_job_plan

    def stage_after_output_root_swap(plan: JobPlan) -> _StagedJobPlan:
        output_root.rename(original_root)
        output_root.symlink_to(attacker_root, target_is_directory=True)
        return original_stage_job_plan(plan)

    monkeypatch.setattr(main_module, "_stage_job_plan", stage_after_output_root_swap)

    with chdir(tmp_path):
        run_main_with_args(
            ["--job", "api", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="could not prepare batch output staging",
        )

    _assert_file_does_not_exist(attacker_root / "generated.py")
    _assert_file_does_not_exist(original_root / "generated.py")


@pytest.mark.skipif(sys.platform == "win32", reason="directory symlink creation requires elevated privileges")
@pytest.mark.allow_direct_assert
def test_pyproject_jobs_rejects_output_ancestor_swap_between_validation_and_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bind replacement to the planned root if an output directory is swapped immediately after validation."""
    output_root = tmp_path / "models"
    original_root = tmp_path / "original-models"
    attacker_root = tmp_path / "attacker-models"
    input_path = tmp_path / "schema.yaml"
    input_path.write_text((OPEN_API_DATA_PATH / "modular.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    output_root.mkdir()
    attacker_root.mkdir()
    stale_output = (EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py").read_text(encoding="utf-8")
    (output_root / "keep.py").write_text(stale_output, encoding="utf-8")
    (attacker_root / "models.py").write_text(stale_output, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "openapi"

[tool.datamodel-codegen.jobs.models]
input = "{input_path.as_posix()}"
output = "{output_root.as_posix()}"
""",
        encoding="utf-8",
    )
    original_publish = main_module._publish_staged_job_plans

    def publish_after_output_swap(staged_plans: tuple[_StagedJobPlan, ...]) -> None:
        output_root.rename(original_root)
        output_root.symlink_to(attacker_root, target_is_directory=True)
        original_publish(staged_plans)

    monkeypatch.setattr(main_module, "_publish_staged_job_plans", publish_after_output_swap)

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="destination anchor changed before publication",
        )

    assert input_path.read_text(encoding="utf-8") == (OPEN_API_DATA_PATH / "modular.yaml").read_text(encoding="utf-8")
    assert_output((original_root / "keep.py").read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py")
    assert_output(
        (attacker_root / "models.py").read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py"
    )
    assert list(original_root.glob(".*.bak")) == []
    assert list(attacker_root.glob(".*.bak")) == []
    assert list(tmp_path.glob(".datamodel-codegen-*")) == []


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_publish_rollback_restores_prior_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore every earlier replacement if publication fails after its backup was journaled."""
    first_staged = tmp_path / "first.staged.py"
    new_staged = tmp_path / "new.staged.py"
    second_staged = tmp_path / "second.staged.py"
    first_target = tmp_path / "first.py"
    new_target = tmp_path / "generated" / "new.py"
    second_target = tmp_path / "second.py"
    first_staged.write_text("first generated\n", encoding="utf-8")
    new_staged.write_text("new generated\n", encoding="utf-8")
    second_staged.write_text("second generated\n", encoding="utf-8")
    first_target.write_text("first stale\n", encoding="utf-8")
    second_target.write_text("second stale\n", encoding="utf-8")
    original_replace_source = publication_module._replace_source

    def fail_second_publication(
        source: publication_module.StagedFile, destination: str | Path, destination_fd: int | None
    ) -> None:
        if source.staged_file == second_staged:
            msg = "simulated publish failure"
            raise OSError(msg)
        original_replace_source(source, destination, destination_fd)

    monkeypatch.setattr(publication_module, "_replace_source", fail_second_publication)

    with pytest.raises(OSError, match="simulated publish failure"):
        _publish_staged_files([(first_staged, first_target), (new_staged, new_target), (second_staged, second_target)])

    assert first_target.read_text(encoding="utf-8") == "first stale\n"
    assert second_target.read_text(encoding="utf-8") == "second stale\n"
    _assert_file_does_not_exist(new_target)
    assert not new_target.parent.exists()


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_reject_duplicate_publication_targets_before_mutating_files(tmp_path: Path) -> None:
    """A common publication journal refuses duplicate immutable destinations before its first write."""
    first_staged = tmp_path / "first.staged.py"
    second_staged = tmp_path / "second.staged.py"
    target = tmp_path / "target.py"
    first_staged.write_text("first generated\n", encoding="utf-8")
    second_staged.write_text("second generated\n", encoding="utf-8")

    with pytest.raises(OSError, match="duplicate staged publication target"):
        _publish_staged_files(((first_staged, target), (second_staged, target)))

    _assert_file_does_not_exist(target)
    assert first_staged.is_file()
    assert second_staged.is_file()


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_publish_first_replacement_failure_removes_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave the original target and no hidden backup when its first replacement cannot start."""
    staged_file = tmp_path / "generated.py"
    target = tmp_path / "target.py"
    staged_file.write_text("generated\n", encoding="utf-8")
    target.write_text("stale\n", encoding="utf-8")

    def fail_first_replacement(
        _source: publication_module.StagedFile,
        _destination: str | Path,
        _destination_fd: int | None,
    ) -> None:
        msg = "simulated first replacement failure"
        raise OSError(msg)

    monkeypatch.setattr(publication_module, "_replace_source", fail_first_replacement)

    with pytest.raises(OSError, match="simulated first replacement failure"):
        _publish_staged_files([(staged_file, target)])

    assert_output(target.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py")
    assert list(tmp_path.glob(".target.py.*.bak")) == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
@pytest.mark.allow_direct_assert
def test_pyproject_jobs_publish_replaces_symlink_without_mutating_its_target(tmp_path: Path) -> None:
    """Back up a symlink entry and atomically replace it without changing its original referent."""
    staged_file = tmp_path / "generated.py"
    original_target = tmp_path / "original.py"
    output_link = tmp_path / "output.py"
    staged_file.write_text("generated\n", encoding="utf-8")
    original_target.write_text("stale\n", encoding="utf-8")
    output_link.symlink_to(original_target)

    _publish_staged_files([(staged_file, output_link)])

    assert output_link.read_text(encoding="utf-8") == "generated\n"
    assert original_target.read_text(encoding="utf-8") == "stale\n"


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
@pytest.mark.allow_direct_assert
def test_pyproject_jobs_failed_replacement_discards_unchanged_symlink_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain the original symlink and remove its backup when the atomic replacement fails."""
    staged_file = tmp_path / "generated.py"
    original_target = tmp_path / "original.py"
    output_link = tmp_path / "output.py"
    staged_file.write_text("generated\n", encoding="utf-8")
    original_target.write_text(
        (EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    output_link.symlink_to(original_target)

    def fail_staged_replace(
        _source: publication_module.StagedFile,
        _destination: str | Path,
        _destination_fd: int | None,
    ) -> None:
        msg = "simulated symlink replacement failure"
        raise OSError(msg)

    monkeypatch.setattr(publication_module, "_replace_source", fail_staged_replace)

    with pytest.raises(OSError, match="simulated symlink replacement failure"):
        _publish_staged_files([(staged_file, output_link)])

    assert output_link.is_symlink()
    assert_output(output_link.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py")
    assert_output(original_target.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py")
    assert list(tmp_path.glob(".output.py.*.bak")) == []


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_publish_falls_back_to_copy_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a copy backup when the filesystem cannot make a hardlink for an existing target."""
    staged_file = tmp_path / "generated.py"
    target = tmp_path / "target.py"
    staged_file.write_text("generated\n", encoding="utf-8")
    target.write_text("stale\n", encoding="utf-8")

    def fail_hardlink(*_args: object, **_kwargs: object) -> None:
        msg = "simulated hardlink failure"
        raise OSError(msg)

    monkeypatch.setattr(os, "link", fail_hardlink)

    _publish_staged_files([(staged_file, target)])

    assert target.read_text(encoding="utf-8") == "generated\n"


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize("copy_backup", [False, True])
def test_backup_existing_target_retries_collisions_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, copy_backup: bool
) -> None:
    """Claim backup names atomically for both hardlink and exclusive-copy paths."""
    target = tmp_path / "target.py"
    colliding_backup = tmp_path / ".target.py.collision.bak"
    expected_backup = tmp_path / ".target.py.available.bak"
    target.write_text("stale\n", encoding="utf-8")
    target.chmod(0o640)
    timestamp_ns = 1_600_000_000_123_456_789
    os.utime(target, ns=(timestamp_ns, timestamp_ns))
    colliding_backup.write_text("unrelated\n", encoding="utf-8")
    candidate_names = iter((colliding_backup.name, expected_backup.name))
    monkeypatch.setattr(publication_module, "_backup_name", lambda _target_name: next(candidate_names))

    if copy_backup:
        monkeypatch.setattr(
            os,
            "link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated hardlink failure")),
        )

    backup = publication_module._backup_existing_target(target)
    backup_stat = backup.stat()

    assert backup == expected_backup
    assert colliding_backup.read_text(encoding="utf-8") == "unrelated\n"
    assert backup.read_text(encoding="utf-8") == "stale\n"
    assert backup_stat.st_mtime_ns == target.stat().st_mtime_ns
    if copy_backup and os.name != "nt":
        assert stat.S_IMODE(backup_stat.st_mode) == 0o640


@pytest.mark.allow_direct_assert
def test_backup_existing_symlink_retries_collision_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claim a symlink backup name directly and preserve the original link target."""
    referent = tmp_path / "referent.py"
    target = tmp_path / "target.py"
    colliding_backup = tmp_path / ".target.py.collision.bak"
    expected_backup = tmp_path / ".target.py.available.bak"
    referent.write_text("stale\n", encoding="utf-8")
    try:
        target.symlink_to(referent.name)
    except (NotImplementedError, OSError):  # pragma: no cover - platform capability
        pytest.skip("this platform cannot create symlinks")
    colliding_backup.write_text("unrelated\n", encoding="utf-8")
    candidate_names = iter((colliding_backup.name, expected_backup.name))
    monkeypatch.setattr(publication_module, "_backup_name", lambda _target_name: next(candidate_names))

    backup = publication_module._backup_existing_target(target)

    assert backup == expected_backup
    assert backup.is_symlink()
    assert backup.readlink() == target.readlink()
    assert colliding_backup.read_text(encoding="utf-8") == "unrelated\n"


def test_copy_backup_without_fchmod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the platform chmod fallback when ``fchmod`` is unavailable."""
    target = tmp_path / "target.py"
    target.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(os, "link", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no hardlink")))
    monkeypatch.delattr(os, "fchmod", raising=False)

    backup = publication_module._backup_existing_target(target)

    assert_output(backup.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py")


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_partial_copy_backup_failure_removes_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove a partially copied hidden backup if the hardlink and copy fallback both fail."""
    target = tmp_path / "target.py"
    target.write_text("stale\n", encoding="utf-8")

    monkeypatch.setattr(os, "link", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no hardlink")))

    def fail_partial_copy(_source: BinaryIO, destination: BinaryIO, _length: int = 0) -> None:
        destination.write(b"partial\n")
        msg = "simulated partial copy failure"
        raise OSError(msg)

    monkeypatch.setattr(main_module.shutil, "copyfileobj", fail_partial_copy)

    with pytest.raises(OSError, match="simulated partial copy failure"):
        publication_module._backup_existing_target(target)

    assert_output(target.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py")
    assert list(tmp_path.glob(".target.py.*.bak")) == []

    staged_file = tmp_path / "generated.py"
    staged_file.write_text("generated\n", encoding="utf-8")

    with pytest.raises(OSError, match="simulated partial copy failure"):
        _publish_staged_files([(staged_file, target)])

    assert_output(target.read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py")
    assert list(tmp_path.glob(".target.py.*.bak")) == []


@pytest.mark.skipif(sys.platform == "win32", reason="batch publication anchors use directory descriptors on POSIX")
@pytest.mark.allow_direct_assert
def test_pyproject_jobs_post_publish_check_does_not_recreate_missing_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Roll back through the pinned directory without recreating a detached destination path."""
    target_parent = tmp_path / "output"
    detached_parent = tmp_path / "detached-output"
    staged_file = tmp_path / "generated.py"
    target = target_parent / "target.py"
    target_parent.mkdir()
    staged_file.write_text("generated\n", encoding="utf-8")
    target.write_text("stale\n", encoding="utf-8")
    original_matches = publication_module._directory_fd_matches_path

    def detach_before_postcheck(directory_fd: int, path: Path) -> bool:
        path.rename(detached_parent)
        return original_matches(directory_fd, path)

    monkeypatch.setattr(publication_module, "_directory_fd_matches_path", detach_before_postcheck)

    with pytest.raises(OSError, match="destination changed during publication"):
        _publish_staged_files([(staged_file, target)])

    assert not target_parent.exists()
    assert_output(
        (detached_parent / "target.py").read_text(encoding="utf-8"), EXPECTED_MAIN_KR_PATH / "jobs" / "stale.py"
    )
    assert list(detached_parent.glob(".target.py.*.bak")) == []


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_rollback_helpers_report_unrecoverable_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return targets, backups, and directories that cannot be rolled back cleanly."""
    target = tmp_path / "target.py"
    missing_backup = tmp_path / "missing.bak"
    target.write_text("generated\n", encoding="utf-8")
    assert publication_module._rollback_published_file(publication_module._PublishedFile(target, missing_backup)) == [
        target,
        missing_backup,
    ]
    assert (
        publication_module._rollback_published_file(publication_module._PublishedFile(tmp_path / "missing.py", None))
        == []
    )

    backup = tmp_path / "backup.py"
    backup.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(
        publication_module, "_restore_backup", lambda *_args: (_ for _ in ()).throw(OSError("restore failed"))
    )
    assert publication_module._rollback_published_file(publication_module._PublishedFile(target, backup)) == [
        target,
        backup,
    ]

    nonempty_directory = tmp_path / "generated"
    nonempty_directory.mkdir()
    (nonempty_directory / "file.py").write_text("generated\n", encoding="utf-8")
    assert publication_module._remove_created_directory(nonempty_directory) == [nonempty_directory]


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_parent_and_rollback_helpers_handle_races(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep transaction journals accurate when competing filesystem changes win a race."""
    created_directories: list[Path] = []
    monkeypatch.setattr(publication_module, "_create_directory", lambda _directory: False)
    publication_module._create_target_parent(tmp_path / "generated" / "model.py", created_directories)
    assert created_directories == []

    target = tmp_path / "target.py"
    target.write_text("generated\n", encoding="utf-8")
    monkeypatch.setattr(publication_module, "_unlink", lambda *_args: (_ for _ in ()).throw(OSError("unlink failed")))
    assert publication_module._rollback_published_file(publication_module._PublishedFile(target, None)) == [target]


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_restore_backup_replaces_when_samefile_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore a backup if samefile cannot inspect a raced target path."""
    backup = tmp_path / "backup.py"
    target = tmp_path / "target.py"
    backup.write_text("stale\n", encoding="utf-8")
    target.write_text("generated\n", encoding="utf-8")

    def fail_samefile(_first: Path, _second: Path) -> bool:
        msg = "simulated samefile failure"
        raise OSError(msg)

    monkeypatch.setattr(Path, "samefile", fail_samefile)

    publication_module._restore_backup(backup, target)

    assert target.read_text(encoding="utf-8") == "stale\n"

    monkeypatch.undo()
    backup.write_text("stale again\n", encoding="utf-8")
    target.write_text("generated again\n", encoding="utf-8")
    publication_module._restore_backup(backup, target)

    assert target.read_text(encoding="utf-8") == "stale again\n"


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
@pytest.mark.allow_direct_assert
def test_pyproject_jobs_restore_backup_discards_unchanged_symlink_backup(tmp_path: Path) -> None:
    """Remove a symlink backup when a failed replacement left its target unchanged."""
    original = tmp_path / "original.py"
    backup = tmp_path / "backup.py"
    target = tmp_path / "target.py"
    original.write_text("stale\n", encoding="utf-8")
    backup.symlink_to(original)
    target.symlink_to(original)

    publication_module._restore_backup(backup, target)

    _assert_file_does_not_exist(backup)
    assert target.is_symlink()


@pytest.mark.skipif(sys.platform == "win32", reason="batch publication anchors use directory descriptors on POSIX")
@pytest.mark.allow_direct_assert
def test_pyproject_jobs_publish_reports_failed_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Include unrecovered paths when both publication and rollback fail."""
    staged_file = tmp_path / "generated.py"
    target = tmp_path / "target.py"
    staged_file.write_text("generated\n", encoding="utf-8")
    target.write_text("stale\n", encoding="utf-8")

    def fail_publication(
        _source: publication_module.StagedFile,
        _destination: str | Path,
        _destination_fd: int | None,
    ) -> None:
        msg = "simulated publication failure"
        raise OSError(msg)

    monkeypatch.setattr(publication_module, "_replace_source", fail_publication)
    monkeypatch.setattr(
        publication_module,
        "_restore_backup_at",
        lambda *_args: (_ for _ in ()).throw(OSError("rollback failed")),
    )

    with pytest.raises(OSError, match="failed to roll back batch output"):
        _publish_staged_files([(staged_file, target)])

    assert target.read_text(encoding="utf-8") == "stale\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes are unsupported on Windows")
@pytest.mark.allow_direct_assert
def test_pyproject_jobs_publish_preserves_existing_file_mode(tmp_path: Path) -> None:
    """Keep an existing output's permission bits when atomically replacing its contents."""
    staged_file = tmp_path / "generated.py"
    target = tmp_path / "target.py"
    staged_file.write_text("generated\n", encoding="utf-8")
    target.write_text("stale\n", encoding="utf-8")
    target.chmod(0o640)

    _publish_staged_files([(staged_file, target)])

    assert target.read_text(encoding="utf-8") == "generated\n"
    assert target.stat().st_mode & 0o777 == 0o640


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_publish_rejects_directory_file_target(tmp_path: Path) -> None:
    """Do not replace an existing directory when a staged generated file collides with it."""
    staged_file = tmp_path / "generated.py"
    target = tmp_path / "target.py"
    staged_file.write_text("generated\n", encoding="utf-8")
    target.mkdir()

    with pytest.raises(IsADirectoryError):
        _publish_staged_files([(staged_file, target)])

    assert staged_file.read_text(encoding="utf-8") == "generated\n"


@pytest.mark.allow_direct_assert
@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes are unsupported on Windows")
def test_pyproject_jobs_publish_nested_output_and_preserve_mode(tmp_path: Path) -> None:
    """Publish into new parent directories and preserve the mode on a later replacement."""
    output_path = tmp_path / "created" / "nested" / "model.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.models]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    assert_output(output_path.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")
    output_path.chmod(0o640)

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    assert stat.S_IMODE(output_path.stat().st_mode) == 0o640


def test_pyproject_jobs_staging_failure_removes_earlier_staging(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove already prepared staging when a later job's metadata staging cannot be made."""
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    metadata_output = tmp_path / "second.metadata.json"
    lockfile = tmp_path / "remote.lock"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"
update-lock = true
lockfile = "{lockfile.as_posix()}"

[tool.datamodel-codegen.jobs.first]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{first_output.as_posix()}"

[tool.datamodel-codegen.jobs.second]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{second_output.as_posix()}"
emit-model-metadata = "{metadata_output.as_posix()}"
""",
        encoding="utf-8",
    )
    staging_directory_for = main_module._staging_directory_for
    calls = 0

    def fail_metadata_staging(target: Path) -> tempfile.TemporaryDirectory[str]:
        nonlocal calls
        calls += 1
        if calls == 3:
            msg = "simulated staging failure"
            raise OSError(msg)
        return staging_directory_for(target)

    monkeypatch.setattr(main_module, "_staging_directory_for", fail_metadata_staging)

    with chdir(tmp_path):
        run_main_with_args(
            ["--all-jobs", "--formatters", "builtin"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="could not prepare batch output staging",
        )

    _assert_file_does_not_exist(first_output)
    _assert_file_does_not_exist(second_output)
    _assert_file_does_not_exist(metadata_output)
    _assert_file_does_not_exist(lockfile)


@pytest.mark.skipif(sys.platform == "win32", reason="batch publication anchors use directory descriptors on POSIX")
def test_pyproject_jobs_staging_failure_keeps_primary_error_when_anchor_cleanup_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report both failed anchor cleanups without masking the original staging failure."""
    first_output = tmp_path / "first.py"
    second_output = tmp_path / "second.py"
    metadata_output = tmp_path / "second.metadata.json"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.first]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{first_output.as_posix()}"

[tool.datamodel-codegen.jobs.second]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{second_output.as_posix()}"
emit-model-metadata = "{metadata_output.as_posix()}"
""",
        encoding="utf-8",
    )
    staging_directory_for = main_module._staging_directory_for
    staging_calls = 0

    def fail_metadata_staging(target: Path) -> tempfile.TemporaryDirectory[str]:
        nonlocal staging_calls
        staging_calls += 1
        if staging_calls == 3:
            msg = "simulated staging failure"
            raise OSError(msg)
        return staging_directory_for(target)

    publication_anchor = publication_module.publication_anchor
    anchored_descriptors: set[int] = set()

    def record_anchor(path: Path) -> main_module._PublicationAnchor:
        anchor = publication_anchor(path)
        anchored_descriptors.add(cast("int", anchor.directory_fd))
        return anchor

    close = os.close

    def fail_anchor_cleanup(descriptor: int) -> None:
        if descriptor in anchored_descriptors:
            anchored_descriptors.remove(descriptor)
            close(descriptor)
            msg = "simulated anchor cleanup failure"
            raise OSError(msg)
        close(descriptor)

    monkeypatch.setattr(main_module, "_staging_directory_for", fail_metadata_staging)
    monkeypatch.setattr(publication_module, "publication_anchor", record_anchor)
    monkeypatch.setattr(main_module.os, "close", fail_anchor_cleanup)

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"], expected_exit=Exit.ERROR)

    assert_output(
        capsys.readouterr().err,
        EXPECTED_MAIN_KR_PATH / "jobs" / "staging_anchor_cleanup_error.txt",
    )
    _assert_file_does_not_exist(first_output)
    _assert_file_does_not_exist(second_output)
    _assert_file_does_not_exist(metadata_output)


def test_pyproject_jobs_cleanup_failure_after_success_is_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return a normal diagnostic if final staging cleanup fails after publishing output."""
    output = tmp_path / "person.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.person]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{output.as_posix()}"
""",
        encoding="utf-8",
    )
    staging_directory_for = main_module._staging_directory_for

    def staging_with_failed_cleanup(target: Path) -> tempfile.TemporaryDirectory[str]:
        context = staging_directory_for(target)
        cleanup = context.cleanup

        def fail_cleanup() -> None:
            cleanup()
            msg = "simulated post-generation cleanup failure"
            raise OSError(msg)

        monkeypatch.setattr(context, "cleanup", fail_cleanup)
        return context

    monkeypatch.setattr(main_module, "_staging_directory_for", staging_with_failed_cleanup)

    with chdir(tmp_path):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"], expected_exit=Exit.ERROR)

    assert_output(capsys.readouterr().err, EXPECTED_MAIN_KR_PATH / "jobs" / "staging_cleanup_error.txt")
    assert_output(output.read_text(encoding="utf-8"), DATA_PATH / "expected" / "main" / "person.py")


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_reraises_generation_failure_after_attempting_staging_cleanup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep a primary runner failure while reporting a cleanup failure from every staged resource."""
    output = tmp_path / "person.py"
    (tmp_path / "pyproject.toml").write_text(
        f"""
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.person]
input = "{(JSON_SCHEMA_DATA_PATH / "person.json").as_posix()}"
output = "{output.as_posix()}"
""",
        encoding="utf-8",
    )
    staging_directory_for = main_module._staging_directory_for

    def staging_with_failed_cleanup(target: Path) -> tempfile.TemporaryDirectory[str]:
        context = staging_directory_for(target)
        cleanup = context.cleanup

        def fail_cleanup() -> None:
            cleanup()
            msg = "simulated post-generation cleanup failure"
            raise OSError(msg)

        monkeypatch.setattr(context, "cleanup", fail_cleanup)
        return context

    def fail_runner(*_args: object, **_kwargs: object) -> Exit:
        msg = "simulated runner failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(main_module, "_staging_directory_for", staging_with_failed_cleanup)
    monkeypatch.setattr(main_module, "_run_jobs_text", fail_runner)

    with chdir(tmp_path), pytest.raises(RuntimeError, match="simulated runner failure"):
        run_main_with_args(["--all-jobs", "--formatters", "builtin"])

    assert_output(capsys.readouterr().err, EXPECTED_MAIN_KR_PATH / "jobs" / "staging_cleanup_error.txt")
    _assert_file_does_not_exist(output)


@pytest.mark.parametrize(
    ("config", "args", "message"),
    [
        ("", ["--all-jobs"], "No jobs found"),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
output = "$OUTPUT"
""",
            ["--all-jobs"],
            "must define both 'input' and 'output'",
        ),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
input = "$INPUT"
output = "$OUTPUT"
url = "https://example.com/schema.json"
""",
            ["--all-jobs"],
            "only supports an 'input' file",
        ),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
profile = 1
input = "$INPUT"
output = "$OUTPUT"
""",
            ["--all-jobs"],
            "profile must be a string",
        ),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
profile = "missing"
input = "$INPUT"
output = "$OUTPUT"
""",
            ["--all-jobs"],
            "Profile 'missing' not found",
        ),
        (
            """
profiles = "invalid"

[tool.datamodel-codegen.jobs.invalid]
input = "$INPUT"
output = "$OUTPUT"
""",
            ["--all-jobs"],
            "profiles] must be a table",
        ),
        (
            """
[tool.datamodel-codegen.profiles]
invalid = "not a table"

[tool.datamodel-codegen.jobs.invalid]
profile = "invalid"
input = "$INPUT"
output = "$OUTPUT"
""",
            ["--all-jobs"],
            "Profile 'invalid' must be a table",
        ),
        (
            """
[tool.datamodel-codegen.profiles.invalid]
extends = 1

[tool.datamodel-codegen.jobs.invalid]
profile = "invalid"
input = "$INPUT"
output = "$OUTPUT"
""",
            ["--all-jobs"],
            "extends must be a string or list of strings",
        ),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
input = "$INPUT"
output = "$OUTPUT"
output-model-type = "not-a-model-type"
""",
            ["--all-jobs"],
            "Invalid batch job configuration",
        ),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
input = "$INPUT"
output = "\\u0000"
""",
            ["--all-jobs"],
            "Invalid batch job configuration",
        ),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
input = "$INPUT"
output = "$OUTPUT"
watch = true
""",
            ["--all-jobs"],
            "--watch cannot be used",
        ),
        (
            """
[tool.datamodel-codegen.profiles.watched]
watch-delay = 0.1

[tool.datamodel-codegen.jobs.invalid]
profile = "watched"
input = "$INPUT"
output = "$OUTPUT"
""",
            ["--all-jobs"],
            "--watch-delay cannot be used",
        ),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
input = "missing.json"
output = "$OUTPUT"
""",
            ["--all-jobs"],
            "input does not exist",
        ),
        (
            """
[tool.datamodel-codegen.jobs.invalid]
input = "$INPUT"
output = "$OUTPUT"
emit-model-metadata = "generated/metadata.json"
""",
            ["--all-jobs"],
            "overlapping output paths",
        ),
    ],
)
def test_pyproject_jobs_reject_invalid_definitions_before_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    config: str,
    args: list[str],
    message: str,
) -> None:
    """Reject invalid job definitions and paths before a generation can write output."""
    output_path = tmp_path / "generated"
    pyproject = '[tool.datamodel-codegen]\ninput-file-type = "jsonschema"\n' + config
    pyproject = pyproject.replace("$INPUT", (JSON_SCHEMA_DATA_PATH / "person.json").as_posix())
    pyproject = pyproject.replace("$OUTPUT", output_path.as_posix())
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    with chdir(tmp_path):
        run_main_with_args(args, expected_exit=Exit.ERROR, capsys=capsys, expected_stderr_contains=message)

    _assert_file_does_not_exist(output_path)


@pytest.mark.allow_direct_assert
def test_pyproject_jobs_require_project_configuration(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Require a project configuration rather than silently falling back to stdin."""
    from datamodel_code_generator.watch_dependencies import WatchDependencies

    dependencies = WatchDependencies()
    with chdir(tmp_path):
        assert main_module._main(["--job", "api"], start_watch=False, dependencies=dependencies) is Exit.ERROR

    assert_error_message(capsys, "No [tool.datamodel-codegen] section found")


def test_pyproject_jobs_normalize_project_path_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Report unreadable project configuration files as planning errors instead of tracebacks."""
    pyproject_path = tmp_path / "pyproject.toml"
    output_path = tmp_path / "output.py"
    pyproject_path.write_text(
        """
[tool.datamodel-codegen]
input-file-type = "jsonschema"

[tool.datamodel-codegen.jobs.api]
input = "tests/data/jsonschema/person.json"
output = "output.py"
""",
        encoding="utf-8",
    )
    try:
        pyproject_path.chmod(0)
    except OSError:  # pragma: no cover - platform capability
        _assert_file_does_not_exist(output_path)
        pytest.skip("this platform cannot remove read permission from the project file")
    try:
        try:
            pyproject_path.read_text(encoding="utf-8")
        except PermissionError:
            pass
        else:  # pragma: no cover - root/privileged user or Windows permission semantics
            _assert_file_does_not_exist(output_path)
            pytest.skip("the current user can read files with mode 000")
        with chdir(tmp_path):
            run_main_with_args(
                ["--job", "api"],
                expected_exit=Exit.ERROR,
                capsys=capsys,
                expected_stderr_contains="Invalid batch job configuration",
            )
    finally:
        pyproject_path.chmod(0o600)

    _assert_file_does_not_exist(output_path)


def test_pyproject_invalid_config_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Report configuration validation errors without attempting generation."""
    output_path = tmp_path / "output.py"
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.datamodel-codegen]
original-field-name-delimiter = "_"
""",
        encoding="utf-8",
    )

    with chdir(tmp_path):
        run_main_with_args(
            [
                "--input",
                str(JSON_SCHEMA_DATA_PATH / "person.json"),
                "--output",
                str(output_path),
            ],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="original-field-name-delimiter",
        )

    _assert_file_does_not_exist(output_path)


def test_help_shows_new_options() -> None:
    """Test that profile and job selection options appear in help."""
    assert_output(arg_parser.format_help(), EXPECTED_MAIN_KR_PATH / "help_shows_new_options.txt")


@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.0.0"),
    reason="black 22.x doesn't support Python 3.11 target version",
)
def test_pyproject_profile_inherits_base_settings(output_file: Path, tmp_path: Path) -> None:
    """Test that profile inherits settings from base config."""
    pyproject_toml = """
[tool.datamodel-codegen]
snake-case-field = true
enable-version-header = false

[tool.datamodel-codegen.profiles.api]
target-python-version = "3.11"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = """
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "firstName": {"type": "string"}
  }
}
"""
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "inherits_base.py",
            extra_args=["--profile", "api", "--disable-timestamp"],
        )


@pytest.mark.skipif(
    version.parse(black.__version__) < version.parse("23.0.0"),
    reason="black 22.x doesn't support Python 3.11 target version",
)
@freeze_time("2019-07-26")
def test_cli_args_override_profile_and_base(output_file: Path, tmp_path: Path) -> None:
    """Test that CLI arguments take precedence over profile and base settings."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
enable-version-header = false

[tool.datamodel-codegen.profiles.api]
target-python-version = "3.11"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = """
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "firstName": {"type": "string"}
  }
}
"""
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "cli_override.py",
            extra_args=[
                "--profile",
                "api",
                "--disable-timestamp",
                "--target-python-version",
                "3.11",
                "--use-union-operator",
            ],
        )


def test_ignore_pyproject_with_profile(tmp_path: Path) -> None:
    """Test that --ignore-pyproject ignores --profile as well."""
    pyproject_toml = """
[tool.datamodel-codegen]
snake-case-field = true

[tool.datamodel-codegen.profiles.api]
target-python-version = "3.11"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = """
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "firstName": {"type": "string"}
  }
}
"""
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)
    output_file = tmp_path / "output.py"

    with chdir(tmp_path):
        run_main_with_args(
            [
                "--input",
                str(input_file),
                "--output",
                str(output_file),
                "--ignore-pyproject",
                "--profile",
                "api",
                "--disable-timestamp",
            ],
        )
        assert_file_content(output_file, EXPECTED_PYPROJECT_PROFILE_PATH / "ignore_pyproject_with_profile.py")


def test_profile_without_pyproject_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test that --profile without pyproject.toml raises an error."""
    input_file = tmp_path / "schema.json"
    input_file.write_text('{"type": "object"}')
    output_file = tmp_path / "output.py"

    with chdir(tmp_path):
        run_main_with_args(
            ["--input", str(input_file), "--output", str(output_file), "--profile", "api"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
        )
        assert_error_message(capsys, "no [tool.datamodel-codegen] section found")


@freeze_time("2019-07-26")
def test_allof_with_description_generates_class_not_alias(output_file: Path) -> None:
    """Test that allOf with description generates class definition, not alias."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "allof_with_description_only.yaml",
        output_path=output_file,
        input_file_type=None,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "main_allof_with_description_only" / "output.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-schema-description",
        ],
    )


@pytest.mark.cli_doc(
    options=["--use-decimal-for-multiple-of"],
    option_description="""Generate Decimal types for fields with multipleOf constraint.

The `--use-decimal-for-multiple-of` flag generates `condecimal` or `Decimal`
types for numeric fields that have a `multipleOf` constraint. This ensures
precise decimal arithmetic when validating values against the constraint.""",
    input_schema="jsonschema/use_decimal_for_multiple_of.json",
    cli_args=["--use-decimal-for-multiple-of"],
    golden_output="main_kr/use_decimal_for_multiple_of/output.py",
)
@LEGACY_BLACK_SKIP
@freeze_time("2019-07-26")
def test_use_decimal_for_multiple_of(output_file: Path) -> None:
    """Generate Decimal types for fields with multipleOf constraint.

    The `--use-decimal-for-multiple-of` flag generates `condecimal` or `Decimal`
    types for numeric fields that have a `multipleOf` constraint. This ensures
    precise decimal arithmetic when validating values against the constraint.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "use_decimal_for_multiple_of.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "use_decimal_for_multiple_of" / "output.py",
        extra_args=["--use-decimal-for-multiple-of"],
    )


@pytest.mark.cli_doc(
    options=["--use-pendulum"],
    option_description="""Use pendulum types for date, time, and duration fields.

The `--use-pendulum` flag maps schema `date`, `time`, and `duration` values to
Pendulum types such as `pendulum.Date`, `pendulum.Time`, and `pendulum.Duration`.
`date-time` fields continue to use `pydantic.AwareDatetime`.

If you need a different datetime class for `date-time` fields, use
[`--output-datetime-class`](#output-datetime-class).""",
    input_schema="jsonschema/use_pendulum.json",
    cli_args=["--use-pendulum"],
    golden_output="main_kr/use_pendulum/output.py",
)
@freeze_time("2019-07-26")
def test_use_pendulum(output_file: Path) -> None:
    """Use pendulum types for date, time, and duration fields.

    The `--use-pendulum` flag maps schema `date`, `time`, and `duration` values
    to Pendulum types such as `pendulum.Date`, `pendulum.Time`, and
    `pendulum.Duration`. `date-time` fields continue to use
    `pydantic.AwareDatetime`.

    If you need a different datetime class for `date-time` fields, use
    `--output-datetime-class`.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "use_pendulum.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "use_pendulum" / "output.py",
        extra_args=["--use-pendulum"],
    )


@pytest.mark.cli_doc(
    options=["--use-non-positive-negative-number-constrained-types"],
    option_description="""Use NonPositive/NonNegative types for number constraints.

The `--use-non-positive-negative-number-constrained-types` flag generates
Pydantic's NonPositiveInt, NonNegativeInt, NonPositiveFloat, and NonNegativeFloat
types for fields with minimum: 0 or maximum: 0 constraints, instead of using
conint/confloat with ge/le parameters.""",
    input_schema="jsonschema/use_non_positive_negative.json",
    cli_args=["--use-non-positive-negative-number-constrained-types"],
    golden_output="main_kr/use_non_positive_negative/output.py",
)
@pytest.mark.skipif(pydantic.VERSION < "2.0.0", reason="Require Pydantic version 2.0.0 or later")
@freeze_time("2019-07-26")
def test_use_non_positive_negative_number_constrained_types(output_file: Path) -> None:
    """Use NonPositive/NonNegative types for number constraints."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "use_non_positive_negative.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "use_non_positive_negative" / "output.py",
        extra_args=["--use-non-positive-negative-number-constrained-types"],
    )


@pytest.mark.skipif(pydantic.VERSION < "2.0.0", reason="Require Pydantic version 2.0.0 or later")
@freeze_time("2019-07-26")
def test_use_non_positive_negative_number_constrained_types_with_use_annotated(output_file: Path) -> None:
    """Use NonPositive/NonNegative types combined with --use-annotated."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "use_non_positive_negative.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "use_non_positive_negative_with_use_annotated" / "output.py",
        extra_args=["--use-non-positive-negative-number-constrained-types", "--use-annotated"],
    )


@pytest.mark.cli_doc(
    options=["--include-path-parameters"],
    option_description="""Include OpenAPI path parameters in generated parameter models.

The `--include-path-parameters` flag adds path parameters (like /users/{userId})
to the generated request parameter models. By default, only query parameters
are included. Use this with `--openapi-scopes parameters` to generate parameter
models that include both path and query parameters.""",
    input_schema="openapi/include_path_parameters.yaml",
    cli_args=["--include-path-parameters", "--openapi-scopes", "schemas", "paths", "parameters"],
    golden_output="main_kr/include_path_parameters/output.py",
)
@freeze_time("2019-07-26")
def test_include_path_parameters(output_file: Path) -> None:
    """Include OpenAPI path parameters in generated parameter models.

    The `--include-path-parameters` flag adds path parameters (like /users/{userId})
    to the generated request parameter models. By default, only query parameters
    are included. Use this with `--openapi-scopes parameters` to generate parameter
    models that include both path and query parameters.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "include_path_parameters.yaml",
        output_path=output_file,
        input_file_type="openapi",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "include_path_parameters" / "output.py",
        extra_args=["--include-path-parameters", "--openapi-scopes", "schemas", "paths", "parameters"],
    )


@pytest.mark.cli_doc(
    options=["--no-alias"],
    option_description="""Disable Field alias generation for non-Python-safe property names.

The `--no-alias` flag disables automatic alias generation when JSON property
names contain characters invalid in Python (like hyphens). Without this flag,
fields are renamed to Python-safe names with `Field(alias='original-name')`.
With this flag, only Python-safe names are used without aliases.""",
    input_schema="jsonschema/no_alias.json",
    cli_args=["--no-alias"],
    golden_output="main_kr/no_alias/with_option.py",
    comparison_output="main_kr/no_alias/without_option.py",
)
@freeze_time("2019-07-26")
def test_no_alias(output_file: Path) -> None:
    """Disable Field alias generation for non-Python-safe property names.

    The `--no-alias` flag disables automatic alias generation when JSON property
    names contain characters invalid in Python (like hyphens). Without this flag,
    fields are renamed to Python-safe names with `Field(alias='original-name')`.
    With this flag, only Python-safe names are used without aliases.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "no_alias.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "no_alias" / "with_option.py",
        extra_args=["--no-alias"],
    )


@pytest.mark.cli_doc(
    options=["--use-serialization-alias"],
    option_description="""Use serialization_alias instead of alias for field aliasing (Pydantic v2 only).

The `--use-serialization-alias` flag changes field aliasing to use `serialization_alias`
instead of `alias`. This allows setting values using the Pythonic field name while
serializing to the original JSON property name.""",
    input_schema="jsonschema/no_alias.json",
    cli_args=["--use-serialization-alias", "--output-model-type", "pydantic_v2.BaseModel"],
    golden_output="main_kr/use_serialization_alias/output.py",
    comparison_output="main_kr/no_alias/without_option.py",
)
@freeze_time("2019-07-26")
def test_use_serialization_alias(output_file: Path) -> None:
    """Use serialization_alias instead of alias for field aliasing (Pydantic v2 only).

    The `--use-serialization-alias` flag changes field aliasing to use `serialization_alias`
    instead of `alias`. This allows setting values using the Pythonic field name while
    serializing to the original JSON property name.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "no_alias.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "use_serialization_alias" / "output.py",
        extra_args=["--use-serialization-alias", "--output-model-type", "pydantic_v2.BaseModel"],
    )


@pytest.mark.cli_doc(
    options=["--custom-file-header"],
    option_description="""Add custom header text to the generated file.

The `--custom-file-header` flag replaces the default "generated by datamodel-codegen"
header with custom text. This is useful for adding copyright notices, license
headers, or other metadata to generated files.""",
    input_schema="jsonschema/no_alias.json",
    cli_args=["--custom-file-header", "# Copyright 2024 MyCompany"],
    golden_output="main_kr/custom_file_header/with_option.py",
    comparison_output="main_kr/custom_file_header/without_option.py",
)
@freeze_time("2019-07-26")
def test_custom_file_header(output_file: Path) -> None:
    """Add custom header text to the generated file.

    The `--custom-file-header` flag replaces the default "generated by datamodel-codegen"
    header with custom text. This is useful for adding copyright notices, license
    headers, or other metadata to generated files.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "no_alias.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "custom_file_header" / "with_option.py",
        extra_args=["--custom-file-header", "# Copyright 2024 MyCompany"],
    )


@pytest.mark.cli_doc(
    options=["--url", "--http-headers"],
    option_description="""Fetch a schema from a URL with custom HTTP headers.

The `--url` flag specifies a remote URL to fetch the schema from instead of
a local file. The `--http-headers` flag adds request headers in
`HeaderName:HeaderValue` format.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=[
        "--url",
        "https://api.example.com/schema.json",
        "--http-headers",
        "Authorization:Bearer token",
    ],
    golden_output="main_kr/url_with_headers/output.py",
)
@freeze_time("2019-07-26")
def test_url_with_http_headers(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Fetch a schema from a URL with custom HTTP headers.

    The `--url` flag specifies a remote URL to fetch the schema from instead of
    a local file. The `--http-headers` flag adds request headers in
    `HeaderName:HeaderValue` format.
    """
    mock_get = mock_httpx_get(
        MockHttpxResponse("https://api.example.com/schema.json", JSON_SCHEMA_DATA_PATH / "pet_simple.json")
    )

    run_main_url_and_assert(
        url="https://api.example.com/schema.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "url_with_headers" / "output.py",
        extra_args=["--http-headers", "Authorization:Bearer token"],
    )
    assert_httpx_get_kwargs(mock_get, headers=[("Authorization", "Bearer token")])


@pytest.mark.cli_doc(
    options=["--http-local-ref-path"],
    option_description="""Resolve HTTP references from local schema files.

The `--http-local-ref-path` flag maps HTTP(S) `$ref` URLs to files under
a local schema store instead of fetching them from the network. The host and
URL path are used as the relative path under the schema store. For example,
`https://api.example.com/schemas/pet.json` is read from
`schemas/api.example.com/schemas/pet.json`.""",
    input_schema="jsonschema/http_local_ref_path_root.json",
    cli_args=[
        "--url",
        "https://api.example.com/schema.json",
        "--http-local-ref-path",
        "schemas",
    ],
    golden_output="main_kr/http_local_ref_path/output.py",
)
@freeze_time("2019-07-26")
def test_http_local_ref_path_cli_doc(mock_httpx_get: HttpxGetMockFactory, output_file: Path, tmp_path: Path) -> None:
    """Resolve HTTP references from local schema files.

    The `--http-local-ref-path` flag maps HTTP(S) `$ref` URLs to files under
    a local schema store instead of fetching them from the network. The host and
    URL path are used as the relative path under the schema store.
    """
    schema_store = tmp_path / "schemas"
    local_schema = schema_store / "api.example.com" / "schemas" / "pet.json"
    local_schema.parent.mkdir(parents=True)
    local_schema.write_text((JSON_SCHEMA_DATA_PATH / "pet_simple.json").read_text(), encoding="utf-8")
    mock_get = mock_httpx_get(
        MockHttpxResponse(
            "https://api.example.com/schema.json",
            JSON_SCHEMA_DATA_PATH / "http_local_ref_path_root.json",
        )
    )

    run_main_url_and_assert(
        url="https://api.example.com/schema.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "http_local_ref_path" / "output.py",
        extra_args=["--http-local-ref-path", str(schema_store)],
    )
    assert_httpx_get_kwargs(mock_get)


@pytest.mark.cli_doc(
    options=["--allow-private-network"],
    option_description="""Allow HTTP requests to private network schema endpoints.

The `--allow-private-network` flag permits trusted HTTP(S) schema requests to
private, loopback, link-local, or otherwise non-public network hosts. Without
this flag, those targets are blocked by default to reduce server-side request
forgery (SSRF) risk. If a trusted internal schema endpoint is blocked, verify
the URL and pass `--allow-private-network`; otherwise use a local schema file
or public endpoint.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--url", "http://127.0.0.1/schema.json", "--allow-private-network"],
    golden_output="main_kr/allow_private_network/output.py",
)
@freeze_time("2019-07-26")
def test_allow_private_network_cli_doc(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Allow trusted private network schema endpoints."""
    mock_get = mock_httpx_get(
        MockHttpxResponse("http://127.0.0.1/schema.json", JSON_SCHEMA_DATA_PATH / "pet_simple.json")
    )
    run_main_url_and_assert(
        url="http://127.0.0.1/schema.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="allow_private_network/output.py",
        extra_args=["--allow-private-network"],
    )
    assert_httpx_get_kwargs(mock_get, expected_url="http://127.0.0.1/schema.json")


@pytest.mark.cli_doc(
    options=["--input"],
    option_description="""Specify the input schema file path.

The `--input` flag specifies the path to the schema file (JSON Schema,
OpenAPI, GraphQL, etc.). Multiple input files can be specified to merge
schemas. Required unless using `--url` to fetch schema from a URL.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--input", "pet_simple.json", "--output", "output.py"],
    golden_output="main_kr/input_output/output.py",
)
@freeze_time("2019-07-26")
def test_input_option(output_file: Path) -> None:
    """Specify the input schema file path.

    The `--input` flag specifies the path to the schema file (JSON Schema,
    OpenAPI, GraphQL, etc.). Multiple input files can be specified to merge
    schemas. Required unless using `--url` to fetch schema from a URL.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "input_output" / "output.py",
    )


@pytest.mark.cli_doc(
    options=["--output"],
    option_description="""Specify the destination path for generated Python code.

The `--output` flag specifies where to write the generated Python code.
It can be either a file path (single-file output) or a directory path
(multi-file output for modular schemas). If omitted, the generated code
is written to stdout.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--input", "pet_simple.json", "--output", "output.py"],
    golden_output="main_kr/input_output/output.py",
)
@freeze_time("2019-07-26")
def test_output_option(output_file: Path) -> None:
    """Specify the destination path for generated Python code.

    The `--output` flag specifies where to write the generated Python code.
    It can be either a file path (single-file output) or a directory path
    (multi-file output for modular schemas). If omitted, the generated code
    is written to stdout.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "input_output" / "output.py",
    )


@pytest.mark.cli_doc(
    options=["--encoding"],
    option_description="""Specify character encoding for input and output files.

The `--encoding` flag sets the character encoding used when reading
the schema file and writing the generated Python code. This is useful
for schemas containing non-ASCII characters (e.g., Japanese, Chinese).
Default is UTF-8, which is the standard encoding for JSON and most modern text files.""",
    input_schema="jsonschema/encoding_test.json",
    cli_args=["--encoding", "utf-8"],
    golden_output="main_kr/encoding/output.py",
)
@freeze_time("2019-07-26")
def test_encoding_option(output_file: Path) -> None:
    """Specify character encoding for input and output files.

    The `--encoding` flag sets the character encoding used when reading
    the schema file and writing the generated Python code. This is useful
    for schemas containing non-ASCII characters (e.g., Japanese, Chinese).
    Default is UTF-8, which is the standard encoding for JSON and most modern text files.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "encoding_test.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "encoding" / "output.py",
        extra_args=["--encoding", "utf-8"],
    )


@pytest.mark.cli_doc(
    options=["--formatters"],
    option_description="""Specify code formatters to apply to generated output.

The `--formatters` flag specifies which code formatters to apply to
the generated Python code. Available formatters are: builtin, black,
isort, ruff-check, ruff-format. The current default is [black, isort].
For projects using Ruff, select --formatters ruff-check ruff-format to match project lint and formatting settings.
Use --formatters builtin when you use no external formatter or prioritize generation speed.
Keep --formatters black isort to preserve existing formatting. Explicit formatters override presets.
The future builtin default reduces installation dependencies and version constraints; Black/isort remain required today.
See the formatter guides for installation and custom-template limitations.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--formatters", "isort"],
    golden_output="main_kr/formatters/output.py",
)
@freeze_time("2019-07-26")
def test_formatters_option(output_file: Path) -> None:
    """Specify code formatters to apply to generated output.

    The `--formatters` flag specifies which code formatters to apply to
    the generated Python code. Available formatters are: builtin, black,
    isort, ruff-check, ruff-format. Default is [black, isort].
    Use this to customize formatting or disable formatters entirely.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "formatters" / "output.py",
        extra_args=["--formatters", "isort"],
    )


@pytest.mark.cli_doc(
    options=["--custom-formatters-kwargs"],
    option_description="""Pass custom arguments to custom formatters via inline JSON or a JSON file path.

The `--custom-formatters-kwargs` flag accepts an inline JSON object or a path to a JSON file containing
custom configuration for custom formatters (used with --custom-formatters).
The file should contain a JSON object mapping formatter names to their kwargs.

Note: This option is primarily used with --custom-formatters to pass
configuration to user-defined formatter modules.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--custom-formatters-kwargs", "formatter_kwargs.json"],
    golden_output="main_kr/input_output/output.py",
)
@freeze_time("2019-07-26")
def test_custom_formatters_kwargs_option(output_file: Path) -> None:
    """Pass custom arguments to custom formatters via inline JSON or a JSON file path.

    The `--custom-formatters-kwargs` flag accepts an inline JSON object or a path to a JSON file containing
    custom configuration for custom formatters (used with --custom-formatters).
    The file should contain a JSON object mapping formatter names to their kwargs.

    Note: This option is primarily used with --custom-formatters to pass
    configuration to user-defined formatter modules.
    """
    # Simple test - the option is accepted. Full usage requires custom formatter module.
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "input_output" / "output.py",
        extra_args=["--custom-formatters-kwargs", str(DATA_PATH / "config" / "formatter_kwargs.json")],
    )


@freeze_time("2019-07-26")
def test_custom_formatters_kwargs_inline_json_option(output_file: Path) -> None:
    """Pass custom formatter kwargs via inline JSON."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "input_output" / "output.py",
        extra_args=[
            "--custom-formatters-kwargs",
            (DATA_PATH / "config" / "formatter_kwargs.json").read_text(encoding="utf-8"),
        ],
    )


@pytest.mark.cli_doc(
    options=["--http-ignore-tls"],
    option_description="""Disable TLS certificate verification for HTTPS requests.

The `--http-ignore-tls` flag disables SSL/TLS certificate verification
when fetching schemas from HTTPS URLs. This is useful for development
environments with self-signed certificates. Not recommended for production.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--url", "https://api.example.com/schema.json", "--http-ignore-tls"],
    golden_output="main_kr/url_with_headers/output.py",
)
@freeze_time("2019-07-26")
def test_http_ignore_tls(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Disable TLS certificate verification for HTTPS requests.

    The `--http-ignore-tls` flag disables SSL/TLS certificate verification
    when fetching schemas from HTTPS URLs. This is useful for development
    environments with self-signed certificates. Not recommended for production.
    """
    mock_get = mock_httpx_get(
        MockHttpxResponse("https://api.example.com/schema.json", JSON_SCHEMA_DATA_PATH / "pet_simple.json")
    )
    run_main_url_and_assert(
        url="https://api.example.com/schema.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="url_with_headers/output.py",
        extra_args=["--http-ignore-tls"],
    )
    assert_httpx_get_kwargs(mock_get, verify=False)


@pytest.mark.cli_doc(
    options=["--http-query-parameters"],
    option_description="""Add query parameters to HTTP requests for remote schemas.

The `--http-query-parameters` flag adds query parameters to HTTP requests
when fetching schemas from URLs. Useful for APIs that require version
or format parameters. Format: `key=value`. Multiple parameters can be
specified: `--http-query-parameters version=v2 format=json`.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--url", "https://api.example.com/schema.json", "--http-query-parameters", "version=v2", "format=json"],
    golden_output="main_kr/url_with_headers/output.py",
)
@freeze_time("2019-07-26")
def test_http_query_parameters(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Add query parameters to HTTP requests for remote schemas.

    The `--http-query-parameters` flag adds query parameters to HTTP requests
    when fetching schemas from URLs. Useful for APIs that require version
    or format parameters. Format: `key=value`. Multiple parameters can be
    specified: `--http-query-parameters version=v2 format=json`.
    """
    mock_get = mock_httpx_get(
        MockHttpxResponse("https://api.example.com/schema.json", JSON_SCHEMA_DATA_PATH / "pet_simple.json")
    )
    run_main_url_and_assert(
        url="https://api.example.com/schema.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="url_with_headers/output.py",
        extra_args=["--http-query-parameters", "version=v2", "format=json"],
    )
    assert_httpx_get_kwargs(mock_get, params_contains={"version": "v2", "format": "json"})


@pytest.mark.cli_doc(
    options=["--http-timeout"],
    option_description="""Set timeout for HTTP requests to remote hosts.

The `--http-timeout` flag sets the timeout in seconds for HTTP requests
when fetching schemas from URLs. Useful for slow servers or large schemas.
Default is 30 seconds.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--url", "https://api.example.com/schema.json", "--http-timeout", "60"],
    golden_output="main_kr/url_with_headers/output.py",
)
@freeze_time("2019-07-26")
def test_http_timeout(mock_httpx_get: HttpxGetMockFactory, output_file: Path) -> None:
    """Set timeout for HTTP requests to remote hosts.

    The `--http-timeout` flag sets the timeout in seconds for HTTP requests
    when fetching schemas from URLs. Useful for slow servers or large schemas.
    Default is 30 seconds.
    """
    mock_get = mock_httpx_get(
        MockHttpxResponse("https://api.example.com/schema.json", JSON_SCHEMA_DATA_PATH / "pet_simple.json")
    )
    run_main_url_and_assert(
        url="https://api.example.com/schema.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="url_with_headers/output.py",
        extra_args=["--http-timeout", "60"],
    )
    assert_httpx_get_kwargs(mock_get, timeout=60.0)


REMOTE_LOCK_EXPERIMENTAL_NOTE = (
    "Remote reference lock support is experimental: its lock document schema and request-identity compatibility may "
    "evolve. It remains fail-closed on integrity mismatches, and it never persists credentials.\n\n"
)

REMOTE_LOCK_OPTION_DESCRIPTION = """The lock stores opaque SHA-256 request-identity digests and SHA-256 body digests,
never response bodies or request values directly. Each saved display origin contains only the scheme, host, and
explicit port—never a path, query, or request headers. A request identity includes its scheme, host, explicit port,
path, header names, and ordered query parameter names only. If one generation receives different bodies for the same
path and query-name identity, it fails closed rather than sharing a lock entry."""

REMOTE_LOCKFILE_OPTION_DESCRIPTION = (
    """Select the remote reference integrity lock file (experimental).

An existing selected lock is verified automatically; a missing selected lock is ignored unless `--locked` requires it.
Without `--lockfile`, the CLI uses `datamodel-codegen.lock` beside the discovered `pyproject.toml`, or in the invocation
working directory when no project is found. Explicit relative `--lockfile` paths resolve from the invocation working
directory, not the project root or output directory. The public API uses the caller's working directory for both its
default lock and relative `lockfile` paths.

"""
    + REMOTE_LOCK_EXPERIMENTAL_NOTE
    + REMOTE_LOCK_OPTION_DESCRIPTION
)

REMOTE_LOCK_UPDATE_OPTION_DESCRIPTION = (
    """Create or atomically update the selected remote lock after generation (experimental).

`--update-lock` creates or refreshes the selected `--lockfile` from every remote resource reached during this run.
It conflicts with `--locked`.

"""
    + REMOTE_LOCK_EXPERIMENTAL_NOTE
    + REMOTE_LOCK_OPTION_DESCRIPTION
)

REMOTE_LOCKED_OPTION_DESCRIPTION = (
    """Require an existing remote lock and validate each fetched resource against it (experimental).

`--locked` fails if the selected lock is missing, a resource is unrecorded, or its body differs. It conflicts with
`--update-lock`.

"""
    + REMOTE_LOCK_EXPERIMENTAL_NOTE
    + REMOTE_LOCK_OPTION_DESCRIPTION
)


@pytest.mark.cli_doc(
    options=["--lockfile"],
    option_description=REMOTE_LOCKFILE_OPTION_DESCRIPTION,
    input_schema="jsonschema/pet_simple.json",
    cli_args=[
        "--url",
        "https://api.example.com/schema.json",
        "--update-lock",
        "--lockfile",
        "datamodel-codegen.lock",
    ],
    golden_output="main_kr/url_with_headers/output.py",
    related_options=["--url", "--http-local-ref-path", "--update-lock", "--locked"],
)
@freeze_time("2019-07-26")
@pytest.mark.allow_direct_assert
def test_lockfile_remote_lock_cli_doc(
    mock_httpx_get: HttpxGetMockFactory,
    output_file: Path,
    tmp_path: Path,
) -> None:
    """Create a usable lock for the remote URL shown in the generated docs."""
    schema_url = "https://api.example.com/schema.json"
    mock_httpx_get(MockHttpxResponse(schema_url, JSON_SCHEMA_DATA_PATH / "pet_simple.json"))
    lockfile = tmp_path / "datamodel-codegen.lock"

    run_main_url_and_assert(
        url=schema_url,
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "url_with_headers" / "output.py",
        extra_args=["--update-lock", "--lockfile", str(lockfile)],
    )
    assert len(json.loads(lockfile.read_text(encoding="utf-8"))["resources"]) == 1


@pytest.mark.cli_doc(
    options=["--update-lock"],
    option_description=REMOTE_LOCK_UPDATE_OPTION_DESCRIPTION,
    input_schema="jsonschema/pet_simple.json",
    cli_args=[
        "--url",
        "https://api.example.com/schema.json",
        "--update-lock",
        "--lockfile",
        "datamodel-codegen.lock",
    ],
    golden_output="main_kr/url_with_headers/output.py",
    related_options=["--url", "--http-local-ref-path", "--lockfile"],
)
@freeze_time("2019-07-26")
@pytest.mark.allow_direct_assert
def test_update_remote_lock_cli_doc(
    mock_httpx_get: HttpxGetMockFactory,
    output_file: Path,
    tmp_path: Path,
) -> None:
    """Create a usable lock for the remote URL shown in the generated docs."""
    schema_url = "https://api.example.com/schema.json"
    mock_httpx_get(MockHttpxResponse(schema_url, JSON_SCHEMA_DATA_PATH / "pet_simple.json"))
    lockfile = tmp_path / "datamodel-codegen.lock"

    run_main_url_and_assert(
        url=schema_url,
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "url_with_headers" / "output.py",
        extra_args=["--update-lock", "--lockfile", str(lockfile)],
    )
    assert len(json.loads(lockfile.read_text(encoding="utf-8"))["resources"]) == 1


@pytest.mark.cli_doc(
    options=["--locked"],
    option_description=REMOTE_LOCKED_OPTION_DESCRIPTION,
    input_schema="jsonschema/pet_simple.json",
    cli_args=[
        "--url",
        "https://api.example.com/schema.json",
        "--locked",
        "--lockfile",
        "datamodel-codegen.lock",
    ],
    golden_output="main_kr/url_with_headers/output.py",
    related_options=["--url", "--http-local-ref-path", "--lockfile"],
)
@freeze_time("2019-07-26")
def test_locked_remote_lock_cli_doc(
    mock_httpx_get: HttpxGetMockFactory,
    output_file: Path,
    tmp_path: Path,
) -> None:
    """Verify the remote URL shown in the docs against an existing lock."""
    schema_url = "https://api.example.com/schema.json"
    response = MockHttpxResponse(schema_url, JSON_SCHEMA_DATA_PATH / "pet_simple.json")
    mock_httpx_get(response, response)
    lockfile = tmp_path / "datamodel-codegen.lock"
    common_args = ["--lockfile", str(lockfile)]

    run_main_url_and_assert(
        url=schema_url,
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "url_with_headers" / "output.py",
        extra_args=[*common_args, "--update-lock"],
    )
    run_main_url_and_assert(
        url=schema_url,
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "url_with_headers" / "output.py",
        extra_args=[*common_args, "--locked"],
    )


@pytest.mark.cli_doc(
    options=["--ignore-pyproject"],
    option_description="""Ignore pyproject.toml configuration file.

The `--ignore-pyproject` flag tells datamodel-codegen to ignore any
[tool.datamodel-codegen] configuration in pyproject.toml. This is useful
when you want to override project defaults with CLI arguments, or when
testing without project configuration.""",
    input_schema="jsonschema/ignore_pyproject_example.json",
    cli_args=["--ignore-pyproject"],
    golden_output="main_kr/ignore_pyproject/output.py",
    comparison_output="main_kr/ignore_pyproject/without_option.py",
)
@freeze_time("2019-07-26")
def test_ignore_pyproject_cli_doc(output_file: Path, tmp_path: Path) -> None:
    """Ignore pyproject.toml configuration file.

    The `--ignore-pyproject` flag tells datamodel-codegen to ignore any
    [tool.datamodel-codegen] configuration in pyproject.toml. This is useful
    when you want to override project defaults with CLI arguments, or when
    testing without project configuration.
    """
    # Create a pyproject.toml with snake-case-field to demonstrate ignoring
    pyproject_toml = """
[tool.datamodel-codegen]
snake-case-field = true
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = """
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "firstName": {"type": "string"},
    "lastName": {"type": "string"}
  }
}
"""
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_MAIN_KR_PATH / "ignore_pyproject" / "output.py",
            extra_args=["--ignore-pyproject", "--disable-timestamp"],
        )


@pytest.mark.cli_doc(
    options=["--shared-module-name"],
    option_description="""Customize the name of the shared module for deduplicated models.

The `--shared-module-name` flag sets the name of the shared module created
when using `--reuse-model` with `--reuse-scope=tree`. This module contains
deduplicated models that are referenced from multiple files. Default is
`shared`. Use this if your schema already has a file named `shared`.

Note: This option only affects modular output with tree-level model reuse.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--shared-module-name", "my_shared"],
    golden_output="main_kr/input_output/output.py",
)
@freeze_time("2019-07-26")
def test_shared_module_name(output_file: Path) -> None:
    """Customize the name of the shared module for deduplicated models.

    The `--shared-module-name` flag sets the name of the shared module created
    when using `--reuse-model` with `--reuse-scope=tree`. This module contains
    deduplicated models that are referenced from multiple files. Default is
    `shared`. Use this if your schema already has a file named `shared`.

    Note: This option only affects modular output with tree-level model reuse.
    """
    # Simple test - the option is accepted but only affects modular output with reuse
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "input_output" / "output.py",
        extra_args=["--shared-module-name", "my_shared"],
    )


@pytest.mark.cli_doc(
    options=["--use-exact-imports"],
    option_description="""Import exact types instead of modules.

The `--use-exact-imports` flag changes import style from module imports
to exact type imports. For example, instead of `from . import foo` then
`foo.Bar`, it generates `from .foo import Bar`. This can make the generated
code more explicit and easier to read.

Note: This option primarily affects modular output where imports between
modules are generated. For single-file output, the difference is minimal.""",
    input_schema="jsonschema/pet_simple.json",
    cli_args=["--use-exact-imports"],
    golden_output="main_kr/input_output/output.py",
)
@freeze_time("2019-07-26")
def test_use_exact_imports(output_file: Path) -> None:
    """Import exact types instead of modules.

    The `--use-exact-imports` flag changes import style from module imports
    to exact type imports. For example, instead of `from . import foo` then
    `foo.Bar`, it generates `from .foo import Bar`. This can make the generated
    code more explicit and easier to read.

    Note: This option primarily affects modular output where imports between
    modules are generated. For single-file output, the difference is minimal.
    """
    # Simple test - the option is accepted and works for single file output
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "pet_simple.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "input_output" / "output.py",
        extra_args=["--use-exact-imports"],
    )


@pytest.mark.cli_doc(
    options=["--target-python-version"],
    option_description="""Target Python version for generated code syntax and imports.

The `--target-python-version` flag controls Python version-specific syntax:

- **Python 3.10-3.11**: Uses `X | None` union operator, `TypeAlias` annotation
- **Python 3.12+**: Uses `type` statement for type aliases

This affects import statements and type annotation syntax in generated code.""",
    input_schema="jsonschema/person.json",
    cli_args=["--target-python-version", "3.10", "--use-standard-collections"],
    version_outputs={
        "3.10": "main_kr/target_python_version/py310.py",
    },
    primary=True,
)
@freeze_time("2019-07-26")
def test_target_python_version_outputs(output_file: Path) -> None:
    """Target Python version for generated code syntax and imports.

    The `--target-python-version` flag controls Python version-specific syntax:

    - **Python 3.10-3.11**: Uses `X | None` union operator, `TypeAlias` annotation
    - **Python 3.12+**: Uses `type` statement for type aliases

    This affects import statements and type annotation syntax in generated code.
    """
    # Test with Python 3.10 style
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "target_python_version" / "py310.py",
        extra_args=["--target-python-version", "3.10", "--use-standard-collections"],
    )


@pytest.mark.cli_doc(
    options=["--target-pydantic-version"],
    option_description="""Target Pydantic version for generated code compatibility.

The `--target-pydantic-version` flag controls Pydantic version-specific config:

- **2**: Uses `populate_by_name=True` (compatible with Pydantic 2.0-2.10)
- **2.11**: Uses `validate_by_name=True` (for Pydantic 2.11+)
- **2.12**: Uses `validate_by_name=True` and allows features that require Pydantic 2.12+

This prevents breaking changes when generated code is used on older Pydantic versions.""",
    input_schema="jsonschema/person.json",
    cli_args=[
        "--target-pydantic-version",
        "2.11",
        "--allow-population-by-field-name",
        "--output-model-type",
        "pydantic_v2.BaseModel",
    ],
    golden_output="main_kr/target_pydantic_version/v2_11.py",
    primary=True,
)
@freeze_time("2019-07-26")
def test_target_pydantic_version(output_file: Path) -> None:
    """Target Pydantic version for generated code compatibility.

    The `--target-pydantic-version` flag controls Pydantic version-specific config:

    - **2**: Uses `populate_by_name=True` (compatible with Pydantic 2.0-2.10)
    - **2.11**: Uses `validate_by_name=True` (for Pydantic 2.11+)
    - **2.12**: Uses `validate_by_name=True` and allows features that require Pydantic 2.12+

    This prevents breaking changes when generated code is used on older Pydantic versions.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "person.json",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file=EXPECTED_MAIN_KR_PATH / "target_pydantic_version" / "v2_11.py",
        extra_args=[
            "--target-pydantic-version",
            "2.11",
            "--allow-population-by-field-name",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
    )


def test_generate_prompt_basic(capsys: pytest.CaptureFixture[str]) -> None:
    """Generate a prompt for consulting LLMs about CLI options.

    The `--generate-prompt` flag outputs a formatted prompt containing:
    - Current CLI options
    - Options organized by category with descriptions
    - Full help text

    This prompt can be copied to ChatGPT, Claude, or other LLMs to get
    recommendations for appropriate CLI options.
    """
    run_main_with_args(
        ["--generate-prompt"],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_KR_PATH / "generate_prompt" / "basic.txt",
    )


def test_generate_prompt_with_question(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-prompt with a question argument."""
    question = "How do I convert enums to Literal types?"
    run_main_with_args(
        ["--generate-prompt", question],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_KR_PATH / "generate_prompt" / "with_question.txt",
    )


def test_generate_prompt_with_options(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-prompt with other CLI options set."""
    run_main_with_args(
        [
            "--input",
            "schema.json",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--snake-case-field",
            "--generate-prompt",
            "What other options should I use?",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_KR_PATH / "generate_prompt" / "with_options.txt",
    )


def test_generate_prompt_with_list_options(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-prompt with list options (e.g., --strict-types)."""
    run_main_with_args(
        [
            "--strict-types",
            "str",
            "int",
            "--generate-prompt",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_KR_PATH / "generate_prompt" / "with_list_options.txt",
    )


def test_generate_prompt_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-prompt --output-format json emits structured option metadata."""
    run_main_with_args(
        GENERATE_PROMPT_JSON_ARGS,
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_KR_PATH / "generate_prompt" / "json_output.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_schema_generate_prompt(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --output-format-json-schema generate-prompt emits the prompt JSON Schema."""
    run_main_with_args(
        GENERATE_PROMPT_JSON_SCHEMA_ARGS,
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_MAIN_KR_PATH / "generate_prompt" / "json_schema.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_schema_validates_generate_prompt_json() -> None:
    """Test prompt JSON output conforms to its emitted JSON Schema."""
    schema = json.loads((EXPECTED_MAIN_KR_PATH / "generate_prompt" / "json_schema.txt").read_text())
    payload = json.loads((EXPECTED_MAIN_KR_PATH / "generate_prompt" / "json_output.txt").read_text())

    jsonschema.validate(instance=payload, schema=schema)


def test_output_format_json_schema_generation(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --output-format-json-schema generation emits the generation JSON Schema."""
    run_main_with_args(
        [
            "--output-format-json-schema",
            "generation",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_schema.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_schema_model_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --output-format-json-schema model-metadata emits the metadata JSON Schema."""
    run_main_with_args(
        [
            "--output-format-json-schema",
            "model-metadata",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "model_metadata_schema.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_schema_structured_output(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --output-format-json-schema structured-output emits the JSON Schema for all structured outputs."""
    run_main_with_args(
        [
            "--output-format-json-schema",
            "structured-output",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "structured_output_schema.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_schema_config(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --output-format-json-schema config emits the JSON config schema."""
    run_main_with_args(
        [
            "--output-format-json-schema",
            "config",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "config_schema.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_schema_validates_generation_json() -> None:
    """Test generation JSON output conforms to its emitted JSON Schema."""
    schema = json.loads((EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_schema.txt").read_text())
    payload = json.loads((EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_stdout.txt").read_text())

    jsonschema.validate(instance=payload, schema=schema)


def test_output_format_json_schema_validates_model_metadata_json(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test generated model metadata conforms to the emitted JSON Schema."""
    metadata_path = output_file.parent / "metadata" / "model-map.json"
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "model_metadata_base_model_name.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=DATA_PATH / "expected" / "main" / "jsonschema" / "model_metadata_base_model_name.py",
        extra_args=[
            "--emit-model-metadata",
            str(metadata_path),
            "--disable-timestamp",
        ],
    )
    run_main_with_args(
        [
            "--output-format-json-schema",
            "model-metadata",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
    )
    schema = json.loads(capsys.readouterr().out)
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))

    jsonschema.validate(instance=payload, schema=schema)


def test_output_format_json_json_ready_values() -> None:
    """Test JSON output normalization handles CLI values used in configs."""

    class Payload(pydantic.BaseModel):
        name: str
        skipped: str | None = None

    payload = _json_ready({
        "exit": Exit.OK,
        "path": Path("schema/person.json"),
        "items": [Exit.ERROR, Path("model/output.py")],
        "model": Payload(name="value"),
    })
    assert_output(
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n",
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "json_ready_values.txt",
    )


def test_output_format_json_generation_mapping_payload() -> None:
    """Test mapping generation results become per-module JSON files."""
    output = _generation_output_json(
        _generated_files_from_result({
            ("models", "user.py"): "class User:\n    pass\n",
        })
    )
    assert_output(f"{output}\n", EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_mapping_payload.txt")


def test_output_format_json_normalizes_multiple_generation_output_paths(output_file: Path) -> None:
    """Test generated output path normalization only replaces matching file paths."""
    disk_output = json.dumps({
        "output": output_file.as_posix(),
        "files": [
            {"path": output_file.as_posix(), "content": "generated\n"},
            {"path": "pkg/model.py", "content": "keep\n"},
        ],
    })
    assert_output(
        _normalize_generation_json_output_path(disk_output, output_file, OUTPUT_FILE_PLACEHOLDER),
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_output_path_normalized.txt",
    )
    stdout_output = json.dumps({
        "output": None,
        "files": [
            {"path": output_file.as_posix(), "content": "generated\n"},
            {"path": "pkg/model.py", "content": "keep\n"},
        ],
    })
    assert_output(
        _normalize_generation_json_output_path(stdout_output, output_file, OUTPUT_FILE_PLACEHOLDER),
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_stdout_path_normalized.txt",
    )


@pytest.mark.parametrize(
    "payload_name",
    [
        "generation_stdout.txt",
        "generation_output_file.txt",
        "generation_output_directory.txt",
        "pyproject_config.txt",
        "cli_command.txt",
        "list_deprecations.txt",
        "list_experimental.txt",
        "check_success.txt",
        "check_missing_output.txt",
        "check_output_file_difference_json.txt",
        "check_output_directory_differences_json.txt",
    ],
)
def test_output_format_json_schema_validates_structured_json(payload_name: str) -> None:
    """Test structured JSON outputs conform to their emitted JSON Schema."""
    schema = json.loads((EXPECTED_OUTPUT_FORMAT_JSON_PATH / "structured_output_schema.txt").read_text())
    payload = json.loads((EXPECTED_OUTPUT_FORMAT_JSON_PATH / payload_name).read_text())

    jsonschema.validate(instance=payload, schema=schema)


def test_output_format_json_schema_validates_prompt_json_as_structured_output() -> None:
    """Test prompt JSON output also conforms to the structured-output JSON Schema."""
    schema = json.loads((EXPECTED_OUTPUT_FORMAT_JSON_PATH / "structured_output_schema.txt").read_text())
    payload = json.loads((EXPECTED_MAIN_KR_PATH / "generate_prompt" / "json_output.txt").read_text())

    jsonschema.validate(instance=payload, schema=schema)


def test_output_format_json_generation_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Test normal generation can emit generated content as JSON."""
    run_main_with_args(
        [
            "--input",
            str(OPEN_API_DATA_PATH / "api.yaml"),
            "--input-file-type",
            "openapi",
            "--output-format",
            "json",
            "--disable-timestamp",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_stdout.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_generate_pyproject_config(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-pyproject-config can emit structured JSON."""
    run_main_with_args(
        [
            "--generate-pyproject-config",
            "--input",
            "schema.yaml",
            "--output",
            "model.py",
            "--output-format",
            "json",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "pyproject_config.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_generate_pyproject_config_with_float_option(capsys: pytest.CaptureFixture[str]) -> None:
    """Emit floating-point pyproject options in structured JSON."""
    run_main_with_args(
        ["--generate-pyproject-config", "--http-timeout", "1.5", "--output-format", "json"],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "pyproject_config_float.txt",
        assert_no_stderr=True,
    )


def test_generate_pyproject_config_helper_uses_default_formatter() -> None:
    """Test the pyproject config helper returns text output by default."""
    assert_output(
        generate_pyproject_config(
            Namespace(
                input="schema.yaml",
                output="model.py",
                strict_types=["str", "bytes"],
                output_format="json",
                output_format_json_schema="structured-output",
            )
        ),
        EXPECTED_GENERATE_PYPROJECT_CONFIG_PATH / "helper_default_formatter.txt",
    )


def test_output_format_json_generate_cli_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --generate-cli-command can emit structured JSON."""
    pyproject_toml = """
[tool.datamodel-codegen]
input = "schema.yaml"
output = "model.py"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    with chdir(tmp_path):
        run_main_with_args(
            [
                "--generate-cli-command",
                "--output-format",
                "json",
            ],
            expected_exit=Exit.OK,
            capsys=capsys,
            expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "cli_command.txt",
            assert_no_stderr=True,
        )


def test_output_format_json_list_deprecations(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --list-deprecations can emit structured JSON."""
    run_main_with_args(
        [
            "--list-deprecations",
            "--output-format",
            "json",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "list_deprecations.txt",
        assert_no_stderr=True,
    )


def test_output_format_json_list_experimental(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --list-experimental can emit structured JSON."""
    run_main_with_args(
        [
            "--list-experimental",
            "json",
            "--output-format",
            "json",
        ],
        expected_exit=Exit.OK,
        capsys=capsys,
        expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "list_experimental.txt",
        assert_no_stderr=True,
    )


@freeze_time(TIMESTAMP)
def test_output_format_json_generation_output_file(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Test --output-format json can mirror a generated single output file."""
    run_main_with_args(
        [
            "--input",
            str(OPEN_API_DATA_PATH / "api.yaml"),
            "--input-file-type",
            "openapi",
            "--output",
            str(output_file),
            "--output-format",
            "json",
        ],
        expected_exit=Exit.OK,
    )
    captured = capsys.readouterr()
    assert_output(captured.err, EXPECTED_EMPTY_OUTPUT_PATH)
    assert_output(
        _normalize_generation_json_output_path(captured.out, output_file, OUTPUT_FILE_PLACEHOLDER),
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_output_file.txt",
    )
    assert_file_content(output_file, EXPECTED_MAIN_KR_PATH / "main_no_file" / "output.py")


@freeze_time(TIMESTAMP)
def test_output_format_json_generation_output_directory(capsys: pytest.CaptureFixture[str], output_dir: Path) -> None:
    """Test --output-format json reports generated multi-file modules."""
    run_main_with_args(
        [
            "--input",
            str(OPEN_API_DATA_PATH / "modular.yaml"),
            "--input-file-type",
            "openapi",
            "--output",
            str(output_dir),
            "--output-format",
            "json",
        ],
        expected_exit=Exit.OK,
    )
    captured = capsys.readouterr()
    assert_output(captured.err, EXPECTED_EMPTY_OUTPUT_PATH)
    assert_output(
        _normalize_generation_json_output_path(captured.out, output_dir, OUTPUT_DIR_PLACEHOLDER),
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "generation_output_directory.txt",
    )
    assert_directory_content(output_dir, EXPECTED_MAIN_KR_PATH / "main_modular")


def test_output_format_json_check_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --check can emit structured JSON when files are up-to-date."""
    with chdir(tmp_path):
        run_main_with_args(
            [
                "--input",
                str(OPEN_API_DATA_PATH / "api.yaml"),
                "--input-file-type",
                "openapi",
                "--output",
                "output.py",
                "--disable-timestamp",
            ],
            expected_exit=Exit.OK,
        )
        capsys.readouterr()

        run_main_with_args(
            [
                "--input",
                str(OPEN_API_DATA_PATH / "api.yaml"),
                "--input-file-type",
                "openapi",
                "--output",
                "output.py",
                "--check",
                "--output-format",
                "json",
                "--disable-timestamp",
            ],
            expected_exit=Exit.OK,
            capsys=capsys,
            expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "check_success.txt",
            assert_no_stderr=True,
        )


def test_output_format_json_check_missing_output_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --check can emit structured JSON when files differ."""
    with chdir(tmp_path):
        run_main_with_args(
            [
                "--input",
                str(OPEN_API_DATA_PATH / "modular.yaml"),
                "--input-file-type",
                "openapi",
                "--output",
                "model",
                "--check",
                "--output-format",
                "json",
                "--disable-timestamp",
            ],
            expected_exit=Exit.DIFF,
            capsys=capsys,
            expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "check_missing_output.txt",
            assert_no_stderr=True,
        )


def test_output_format_json_check_missing_output_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test --check reports a missing single-file output through structured JSON."""
    with chdir(tmp_path):
        output_path = Path("output.py")
        run_main_with_args(
            [
                "--input",
                str(JSON_SCHEMA_DATA_PATH / "person.json"),
                "--input-file-type",
                "jsonschema",
                "--output",
                output_path.as_posix(),
                "--check",
                "--output-format",
                "json",
                "--disable-timestamp",
                "--formatters",
                "builtin",
            ],
            expected_exit=Exit.DIFF,
        )
    captured = capsys.readouterr()
    assert_output(
        captured.out.replace((tmp_path / output_path).resolve().as_posix(), OUTPUT_FILE_PLACEHOLDER),
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "check_missing_output_file.txt",
    )
    assert_output(captured.err, EXPECTED_EMPTY_OUTPUT_PATH)


def test_output_format_json_check_output_file_difference(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --check can emit structured JSON for single-file differences."""
    with chdir(tmp_path):
        output_path = Path("output.py")
        output_path.write_text("outdated\n", encoding="utf-8")
        run_main_with_args(
            [
                "--input",
                str(OPEN_API_DATA_PATH / "api.yaml"),
                "--input-file-type",
                "openapi",
                "--output",
                output_path.as_posix(),
                "--check",
                "--output-format",
                "json",
                "--disable-timestamp",
            ],
            expected_exit=Exit.DIFF,
        )
    captured = capsys.readouterr()
    assert_output(
        captured.out.replace((tmp_path / output_path).as_posix(), OUTPUT_FILE_PLACEHOLDER),
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "check_output_file_difference_json.txt",
    )
    assert_output(captured.err, EXPECTED_EMPTY_OUTPUT_PATH)


def test_output_format_json_check_output_directory_differences(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --check can emit structured JSON for directory differences."""
    with chdir(tmp_path):
        output_dir = Path("model")
        output_dir.mkdir()
        (output_dir / "models.py").write_text("outdated\n", encoding="utf-8")
        (output_dir / "extra.py").write_text("extra\n", encoding="utf-8")
        run_main_with_args(
            [
                "--input",
                str(OPEN_API_DATA_PATH / "modular.yaml"),
                "--input-file-type",
                "openapi",
                "--output",
                output_dir.as_posix(),
                "--check",
                "--output-format",
                "json",
                "--disable-timestamp",
            ],
            expected_exit=Exit.DIFF,
        )
    captured = capsys.readouterr()
    assert_output(
        captured.out.replace((tmp_path / output_dir).as_posix(), OUTPUT_DIR_PLACEHOLDER),
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "check_output_directory_differences_json.txt",
    )
    assert_output(captured.err, EXPECTED_EMPTY_OUTPUT_PATH)


def test_output_format_json_check_text_reports_output_file_difference(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --check reports single-file differences in text output."""
    with chdir(tmp_path):
        output_path = Path("output.py")
        output_path.write_text("outdated\n", encoding="utf-8")
        run_main_with_args(
            [
                "--input",
                str(OPEN_API_DATA_PATH / "api.yaml"),
                "--input-file-type",
                "openapi",
                "--output",
                output_path.as_posix(),
                "--check",
                "--disable-timestamp",
            ],
            expected_exit=Exit.DIFF,
        )
    captured = capsys.readouterr()
    assert_output(
        captured.out.replace((tmp_path / output_path).as_posix(), OUTPUT_FILE_PLACEHOLDER),
        EXPECTED_OUTPUT_FORMAT_JSON_PATH / "check_output_file_difference_text.txt",
    )
    assert_output(captured.err, EXPECTED_EMPTY_OUTPUT_PATH)


def test_output_format_json_check_text_reports_output_directory_differences(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test --check reports directory differences in text output."""
    with chdir(tmp_path):
        output_dir = Path("model")
        output_dir.mkdir()
        (output_dir / "models.py").write_text("outdated\n", encoding="utf-8")
        (output_dir / "extra.py").write_text("extra\n", encoding="utf-8")
        run_main_with_args(
            [
                "--input",
                str(OPEN_API_DATA_PATH / "modular.yaml"),
                "--input-file-type",
                "openapi",
                "--output",
                "model",
                "--check",
                "--disable-timestamp",
            ],
            expected_exit=Exit.DIFF,
            capsys=capsys,
            expected_stdout_path=EXPECTED_OUTPUT_FORMAT_JSON_PATH / "check_output_directory_differences_text.txt",
            assert_no_stderr=True,
        )


def test_output_format_json_rejects_watch(capsys: pytest.CaptureFixture[str]) -> None:
    """Test --watch cannot be combined with --output-format json."""
    run_main_with_args(
        [
            "--input",
            str(OPEN_API_DATA_PATH / "api.yaml"),
            "--input-file-type",
            "openapi",
            "--output-format",
            "json",
            "--watch",
        ],
        expected_exit=Exit.ERROR,
    )
    captured = capsys.readouterr()
    assert_output(captured.out, EXPECTED_EMPTY_OUTPUT_PATH)
    assert_output(captured.err, EXPECTED_OUTPUT_FORMAT_JSON_PATH / "watch_incompatible.txt")


@freeze_time("2019-07-26")
def test_profile_extends_single_parent(output_file: Path, tmp_path: Path) -> None:
    """Test profile inheritance with single extends."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
enable-version-header = false

[tool.datamodel-codegen.profiles._base]
snake-case-field = true

[tool.datamodel-codegen.profiles.api]
extends = "_base"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = '{"type": "object", "properties": {"firstName": {"type": "string"}}}'
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "extends_single.py",
            extra_args=["--profile", "api", "--disable-timestamp"],
        )


@freeze_time("2019-07-26")
def test_profile_extends_multiple_parents(output_file: Path, tmp_path: Path) -> None:
    """Test profile inheritance with multiple extends (list)."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
enable-version-header = false

[tool.datamodel-codegen.profiles._snake]
snake-case-field = true

[tool.datamodel-codegen.profiles._constraints]
field-constraints = true

[tool.datamodel-codegen.profiles.api]
extends = ["_snake", "_constraints"]
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = '{"type": "object", "properties": {"firstName": {"type": "string", "minLength": 1}}}'
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "extends_multiple.py",
            extra_args=["--profile", "api", "--disable-timestamp"],
        )


@freeze_time("2019-07-26")
def test_profile_extends_chain(output_file: Path, tmp_path: Path) -> None:
    """Test profile inheritance chain (a extends b extends c)."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
enable-version-header = false

[tool.datamodel-codegen.profiles._base]
snake-case-field = true

[tool.datamodel-codegen.profiles._middle]
extends = "_base"
field-constraints = true

[tool.datamodel-codegen.profiles.api]
extends = "_middle"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = '{"type": "object", "properties": {"firstName": {"type": "string", "minLength": 1}}}'
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "extends_chain.py",
            extra_args=["--profile", "api", "--disable-timestamp"],
        )


def test_profile_extends_circular_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when circular extends is detected."""
    pyproject_toml = """
[tool.datamodel-codegen]

[tool.datamodel-codegen.profiles.a]
extends = "b"

[tool.datamodel-codegen.profiles.b]
extends = "a"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_file = tmp_path / "schema.json"
    input_file.write_text('{"type": "object"}')
    output_file = tmp_path / "output.py"

    with chdir(tmp_path):
        run_main_with_args(
            ["--input", str(input_file), "--output", str(output_file), "--profile", "a"],
            expected_exit=Exit.ERROR,
        )
        assert_error_message(capsys, "Circular extends detected")


def test_profile_extends_not_found_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when extended profile is not found."""
    pyproject_toml = """
[tool.datamodel-codegen]

[tool.datamodel-codegen.profiles.api]
extends = "nonexistent"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_file = tmp_path / "schema.json"
    input_file.write_text('{"type": "object"}')
    output_file = tmp_path / "output.py"

    with chdir(tmp_path):
        run_main_with_args(
            ["--input", str(input_file), "--output", str(output_file), "--profile", "api"],
            expected_exit=Exit.ERROR,
        )
        assert_error_message(capsys, "Extended profile 'nonexistent' not found")


def test_profile_extends_self_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test error when profile extends itself."""
    pyproject_toml = """
[tool.datamodel-codegen]

[tool.datamodel-codegen.profiles.api]
extends = "api"
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_file = tmp_path / "schema.json"
    input_file.write_text('{"type": "object"}')
    output_file = tmp_path / "output.py"

    with chdir(tmp_path):
        run_main_with_args(
            ["--input", str(input_file), "--output", str(output_file), "--profile", "api"],
            expected_exit=Exit.ERROR,
        )
        assert_error_message(capsys, "cannot extend itself")


@freeze_time("2019-07-26")
def test_profile_extends_child_overrides_parent(output_file: Path, tmp_path: Path) -> None:
    """Test that child profile settings override parent settings."""
    pyproject_toml = """
[tool.datamodel-codegen]
target-python-version = "3.10"
enable-version-header = false

[tool.datamodel-codegen.profiles._base]
snake-case-field = true

[tool.datamodel-codegen.profiles.api]
extends = "_base"
snake-case-field = false
"""
    (tmp_path / "pyproject.toml").write_text(pyproject_toml)

    input_data = '{"type": "object", "properties": {"firstName": {"type": "string"}}}'
    input_file = tmp_path / "schema.json"
    input_file.write_text(input_data)

    with chdir(tmp_path):
        run_main_and_assert(
            input_path=input_file,
            output_path=output_file.resolve(),
            assert_func=assert_file_content,
            expected_file=EXPECTED_PYPROJECT_PROFILE_PATH / "extends_override.py",
            extra_args=["--profile", "api", "--disable-timestamp"],
        )
