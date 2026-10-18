"""E2E recipe tests for the datamodel-code-generator agent skill."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_exact_directory_content, assert_output, create_assert_file_content

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = Path(__file__).parents[2] / "data" / "expected" / "skills" / "datamodel-code-generator"
TARGET_PYTHON = "3.10"
COMMON_ARGS = (
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--target-python-version",
    TARGET_PYTHON,
    "--formatters",
    "black",
    "isort",
    "--disable-timestamp",
)
assert_file_content = create_assert_file_content(EXPECTED)


def _run_codegen(args: Sequence[str], *, cwd: Path | None = None) -> str:
    """Run datamodel-codegen, fail with captured output on errors, and return its stdout."""
    result = subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    if result.returncode != 0:
        pytest.fail(f"datamodel-codegen failed\nargs: {args!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def test_run_codegen_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codegen helper reports captured output when the CLI fails."""

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["datamodel-codegen"], 2, stdout="out", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(pytest.fail.Exception, match="datamodel-codegen failed"):
        _run_codegen(["--bad-option"])


@pytest.mark.parametrize(
    ("fixture_name", "input_type", "expected_file", "extra_args"),
    [
        ("openapi.yaml", "openapi", "single_file/openapi.py", ()),
        ("asyncapi.yaml", "asyncapi", "single_file/asyncapi.py", ()),
        ("jsonschema.json", "jsonschema", "single_file/jsonschema.py", ()),
        ("sample.json", "json", "single_file/json.py", ("--class-name", "SampleJson")),
        ("sample.yaml", "yaml", "single_file/yaml.py", ("--class-name", "SampleYaml")),
        ("sample.csv", "csv", "single_file/csv.py", ("--class-name", "SampleCsv")),
        ("sample.avsc", "avro", "single_file/avro.py", ()),
        ("sample.xsd", "xmlschema", "single_file/xmlschema.py", ()),
        ("sample.proto", "protobuf", "single_file/protobuf.py", ()),
        ("mcp_tools.json", "mcp-tools", "single_file/mcp_tools.py", ()),
    ],
)
def test_skill_single_file_recipes_match_expected_output(
    tmp_path: Path,
    fixture_name: str,
    input_type: str,
    expected_file: str,
    extra_args: tuple[str, ...],
) -> None:
    """Representative single-file recipes match checked-in expected output."""
    output = tmp_path / "models.py"
    _run_codegen([
        "--input",
        str(FIXTURES / fixture_name),
        "--input-file-type",
        input_type,
        "--output",
        str(output),
        *COMMON_ARGS,
        *extra_args,
    ])
    assert_file_content(output, expected_file)


def test_skill_graphql_recipe_matches_expected_output(tmp_path: Path) -> None:
    """GraphQL recipe matches checked-in expected output."""
    pytest.importorskip("graphql", reason="GraphQL optional dependency is not installed")

    output = tmp_path / "graphql_models.py"
    _run_codegen([
        "--input",
        str(FIXTURES / "schema.graphql"),
        "--input-file-type",
        "graphql",
        "--output",
        str(output),
        *COMMON_ARGS,
    ])
    assert_file_content(output, "graphql.py")


def test_skill_input_model_recipe_matches_expected_output(tmp_path: Path) -> None:
    """Python model input retargeting matches checked-in expected output."""
    output = tmp_path / "input_model.py"
    _run_codegen([
        "--input-model",
        f"{FIXTURES / 'python_models.py'}:SkillInputUser",
        "--output",
        str(output),
        *COMMON_ARGS,
    ])
    assert_file_content(output, "input_model.py")


def test_skill_directory_output_recipe_matches_expected_package(tmp_path: Path) -> None:
    """Directory output matches checked-in expected package files."""
    output = tmp_path / "skill_models"
    _run_codegen([
        "--input",
        str(FIXTURES / "schemas"),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(output),
        *COMMON_ARGS,
        "--all-exports-scope",
        "recursive",
    ])
    assert_exact_directory_content(output, EXPECTED / "directory_output")


def test_skill_recipe_check_mode_matches_expected_output(tmp_path: Path) -> None:
    """The documented --check workflow matches checked-in expected output."""
    output = tmp_path / "checked_models.py"
    base_args = [
        "--input",
        str(FIXTURES / "jsonschema.json"),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(output),
        *COMMON_ARGS,
    ]
    _run_codegen(base_args)
    assert_file_content(output, "check_mode.py")
    _run_codegen([*base_args, "--check"])


def test_skill_recommend_options_recipe(tmp_path: Path) -> None:
    """The option discovery recipe prints a recommendation whose arguments are the command that generates the models."""
    shutil.copy(FIXTURES / "openapi.yaml", tmp_path / "openapi.yaml")
    payload = _run_codegen(
        [
            "--input",
            "openapi.yaml",
            "--input-file-type",
            "openapi",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            TARGET_PYTHON,
            "--recommend-options",
            "--output-format",
            "json",
        ],
        cwd=tmp_path,
    )
    assert_output(payload)
    _run_codegen([*json.loads(payload)["arguments"], "--output", "models.py", "--disable-timestamp"], cwd=tmp_path)
    assert_output((tmp_path / "models.py").read_text(encoding="utf-8"), name="models")
