"""The JSON output needs Pydantic 2.1 (SkipJsonSchema); --check, --diff-against and generation do not."""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

import pydantic.json_schema
import pytest

from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

    from tests.main.conftest import InputFileTypeLiteral

JSON_SCHEMA_PATH = DATA_PATH / "jsonschema"
INPUT_DIFF_PATH = JSON_SCHEMA_PATH / "input_diff"
OPENAPI_PATH = DATA_PATH / "openapi"
FLOOR_MESSAGE = "The JSON output of datamodel-codegen requires pydantic 2.1 or newer, but pydantic "


@pytest.mark.abnormal_path("Pydantic 2.0 cannot be installed beside the Pydantic of the test environment")
def pydantic_2_0(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide SkipJsonSchema and the payload models already imported, as Pydantic 2.0 lacks it."""
    monkeypatch.delattr(pydantic.json_schema, "SkipJsonSchema")
    monkeypatch.delitem(sys.modules, "datamodel_code_generator._structured_output", raising=False)


@pytest.mark.abnormal_path("Pydantic 2.0 cannot be installed beside the Pydantic of the test environment")
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--output-format", "json"], id="generation"),
        pytest.param(["--output-format", "json", "--check"], id="check"),
        pytest.param(["--output-format", "json", "--diff-against", str(INPUT_DIFF_PATH / "old.json")], id="diff"),
        pytest.param(["--output-format", "json", "--list-deprecations"], id="deprecations"),
        pytest.param(["--output-format", "json", "--generate-pyproject-config"], id="pyproject-config"),
        pytest.param(["--output-format-json-schema", "generation"], id="schema-generation"),
        pytest.param(["--output-format-json-schema", "structured-output"], id="schema-structured-output"),
        pytest.param(["--output-format-json-schema", "recommend-options"], id="schema-recommend-options"),
    ],
)
def test_json_output_reports_the_pydantic_it_needs(
    extra_args: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report the pydantic version the JSON output needs instead of failing to import SkipJsonSchema."""
    pydantic_2_0(monkeypatch)
    run_main_and_assert(
        input_path=JSON_SCHEMA_PATH / "person.json",
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", *extra_args],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=FLOOR_MESSAGE,
    )


@pytest.mark.abnormal_path("Pydantic 2.0 cannot be installed beside the Pydantic of the test environment")
def test_json_output_to_stdout_reports_the_pydantic_it_needs(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print nothing on stdout when the JSON output cannot be built."""
    pydantic_2_0(monkeypatch)
    run_main_and_assert(
        input_path=JSON_SCHEMA_PATH / "person.json",
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--output-format", "json"],
        expected_exit=Exit.ERROR,
    )
    captured = capsys.readouterr()
    assert_output(captured.out, name="stdout")
    assert_output(re.sub(r"but pydantic \S+ is", "but pydantic <VERSION> is", captured.err), name="stderr")


@pytest.mark.abnormal_path("Pydantic 2.0 cannot be installed beside the Pydantic of the test environment")
@pytest.mark.parametrize(
    ("input_path", "input_file_type", "output_name", "change"),
    [
        pytest.param(JSON_SCHEMA_PATH / "person.json", "jsonschema", "output.py", "none", id="file-same"),
        pytest.param(JSON_SCHEMA_PATH / "person.json", "jsonschema", "output.py", "modify", id="file-modified"),
        pytest.param(JSON_SCHEMA_PATH / "person.json", "jsonschema", "output.py", "remove", id="file-removed"),
        pytest.param(OPENAPI_PATH / "modular.yaml", "openapi", "model", "none", id="directory-same"),
        pytest.param(OPENAPI_PATH / "modular.yaml", "openapi", "model", "modify", id="directory-modified"),
        pytest.param(OPENAPI_PATH / "modular.yaml", "openapi", "model", "remove", id="directory-removed"),
        pytest.param(OPENAPI_PATH / "modular.yaml", "openapi", "model", "add", id="directory-extra"),
    ],
)
def test_check_needs_no_payload_models(
    input_path: Path,
    input_file_type: InputFileTypeLiteral,
    output_name: str,
    change: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare the generated output with the files without the payload models, which only the JSON report builds."""
    output = tmp_path / output_name
    run_main_and_assert(
        input_path=input_path,
        output_path=output,
        input_file_type=input_file_type,
        extra_args=["--disable-timestamp"],
    )
    files = sorted(output.rglob("*.py")) if output.is_dir() else [output]
    match change:
        case "modify":
            files[0].write_text("# Modified content\n", encoding="utf-8")
        case "remove":
            files[0].unlink()
        case "add":
            (output / "extra_model.py").write_text("# Extra file\n", encoding="utf-8")
    pydantic_2_0(monkeypatch)

    run_main_and_assert(
        input_path=input_path,
        output_path=output,
        input_file_type=input_file_type,
        extra_args=["--disable-timestamp", "--check"],
        expected_exit=Exit.OK if change == "none" else Exit.DIFF,
    )
    captured = capsys.readouterr()
    assert_output(captured.out.replace(tmp_path.as_posix(), "<TMP>"), name="check")
    assert_output(captured.err, name="stderr")


@pytest.mark.abnormal_path("Pydantic 2.0 cannot be installed beside the Pydantic of the test environment")
def test_diff_against_needs_no_payload_models(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Show the input diff as text without the payload models."""
    pydantic_2_0(monkeypatch)
    run_main_and_assert(
        input_path=INPUT_DIFF_PATH / "new.json",
        output_path=tmp_path / "output.py",
        input_file_type="jsonschema",
        extra_args=["--diff-against", str(INPUT_DIFF_PATH / "old.json"), "--disable-timestamp"],
        expected_exit=Exit.DIFF,
        output_should_not_exist=True,
    )
    captured = capsys.readouterr()
    assert_output(captured.out, name="diff")
    assert_output(captured.err, name="stderr")
