"""End-to-end tests for comparing generated output from two local inputs."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import jsonschema
import pytest

from datamodel_code_generator import DataModelType, chdir
from datamodel_code_generator.__main__ import (
    Exit,
    OutputComparisonOptions,
    _compare_generated_outputs,
    _output_comparison_policy,
    _write_comparison_output,
)
from tests.conftest import assert_output, create_assert_file_content
from tests.main.conftest import DATA_PATH, InputFileTypeLiteral, run_main_and_assert, run_main_with_args

if TYPE_CHECKING:
    from pathlib import Path


INPUT_DIFF_EXPECTED_PATH = DATA_PATH / "expected" / "main" / "input_diff"
JSON_SCHEMA_INPUT_DIFF_PATH = DATA_PATH / "jsonschema" / "input_diff"
OPENAPI_INPUT_DIFF_PATH = DATA_PATH / "openapi" / "input_diff"
LOCAL_INPUT_TYPE_CASES: tuple[tuple[InputFileTypeLiteral, Path, bool], ...] = (
    ("jsonschema", JSON_SCHEMA_INPUT_DIFF_PATH / "same_old.json", False),
    ("openapi", OPENAPI_INPUT_DIFF_PATH / "new.yaml", True),
    ("asyncapi", DATA_PATH / "asyncapi" / "user-events.yaml", False),
    ("mcp-tools", DATA_PATH / "mcp_tools" / "direct_tool.json", False),
    ("xmlschema", DATA_PATH / "xmlschema" / "inline_root.xsd", False),
    ("protobuf", DATA_PATH / "protobuf" / "common.proto", False),
    ("avro", DATA_PATH / "avro" / "root_type_object.avsc", False),
    ("json", DATA_PATH / "json" / "simple.json", False),
    ("yaml", DATA_PATH / "yaml" / "pet.yaml", False),
    ("dict", DATA_PATH / "python" / "space_and_special_characters_dict.py", False),
    ("csv", DATA_PATH / "csv" / "simple.csv", False),
    ("graphql", DATA_PATH / "graphql" / "simple-star-wars.graphql", False),
)
VIRTUAL_FILE_SENTINEL = INPUT_DIFF_EXPECTED_PATH / "virtual_file_sentinel.py"
VIRTUAL_DIRECTORY_SENTINEL = INPUT_DIFF_EXPECTED_PATH / "virtual_directory_sentinel"
assert_file_content = create_assert_file_content(INPUT_DIFF_EXPECTED_PATH)


@pytest.mark.parametrize(
    ("input_diff", "expected_policy"),
    [
        (
            False,
            (
                "missing",
                "should be generated",
                "file does not exist but should be generated",
                "extra",
                "no longer generated",
            ),
        ),
        (
            True,
            (
                "added",
                "generated only from current input",
                "generated only from current input",
                "removed",
                "generated only from baseline input",
            ),
        ),
    ],
)
@pytest.mark.allow_direct_assert
def test_output_comparison_policy(input_diff: bool, expected_policy: tuple[str, str, str, str, str]) -> None:
    """Return an explicit policy for both normal and two-input comparisons."""
    assert _output_comparison_policy(input_diff=input_diff) == expected_policy


def test_diff_against_identical_single_file_does_not_write_virtual_output(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Equivalent files with different names do not make generated headers differ."""
    new_input = JSON_SCHEMA_INPUT_DIFF_PATH / "same_new.json"
    run_main_and_assert(
        input_path=new_input,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--diff-against", str(JSON_SCHEMA_INPUT_DIFF_PATH / "same_old.json"), "--disable-timestamp"],
        expected_exit=Exit.OK,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical.txt",
        assert_no_stderr=True,
    )


@pytest.mark.parametrize("output_model_type", tuple(model_type.value for model_type in DataModelType))
def test_diff_against_supports_every_output_model_type(
    output_model_type: str, output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two-input pipeline compares every supported data-model backend."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / "same_new.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--diff-against",
            str(JSON_SCHEMA_INPUT_DIFF_PATH / "same_old.json"),
            "--disable-timestamp",
            "--output-model-type",
            output_model_type,
        ],
        expected_exit=Exit.OK,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical.txt",
        assert_no_stderr=True,
    )


def test_diff_against_infers_each_input_type_independently(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Automatic input-type inference runs for both the old and new local paths."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / "same_new.yaml",
        output_path=output_file,
        extra_args=["--diff-against", str(JSON_SCHEMA_INPUT_DIFF_PATH / "same_old.json"), "--disable-timestamp"],
        expected_exit=Exit.OK,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical.txt",
        expected_stderr=(
            "The input file type was determined to be: jsonschema\n"
            "This can be specified explicitly with the `--input-file-type` option.\n"
        )
        * 2,
    )


@pytest.mark.parametrize(("input_file_type", "source_input", "is_directory"), LOCAL_INPUT_TYPE_CASES)
def test_diff_against_supports_every_local_input_type(
    input_file_type: InputFileTypeLiteral,
    source_input: Path,
    is_directory: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each local parser can compare two equivalent inputs through the CLI."""
    copied_input = tmp_path / f"candidate-{source_input.name}"
    virtual_output = tmp_path / ("models" if is_directory else "models.py")
    run_main_and_assert(
        input_path=copied_input,
        output_path=virtual_output,
        input_file_type=input_file_type,
        extra_args=["--diff-against", str(source_input), "--disable-timestamp"],
        expected_exit=Exit.OK,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical.txt",
        assert_no_stderr=True,
        copy_files=[(source_input, copied_input)],
    )


def test_diff_against_reports_changed_single_file_without_writing_virtual_output(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Compare two local JSON Schema revisions through the normal generation pipeline."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / "new.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--diff-against",
            str(JSON_SCHEMA_INPUT_DIFF_PATH / "old.json"),
            "--disable-timestamp",
        ],
        expected_exit=Exit.DIFF,
        output_should_not_exist=True,
        capsys=capsys,
        assert_no_stderr=True,
    )


@pytest.mark.parametrize(
    ("baseline", "current", "extra_args", "expected_exit", "expected_stdout"),
    [
        pytest.param("same_old.json", "same_new.json", (), Exit.OK, "identical.txt", id="identical"),
        pytest.param("old.json", "new.json", (), Exit.DIFF, "different_suffixless_file.txt", id="different"),
        pytest.param(
            "old.json",
            "new.json",
            ("--output-format", "json"),
            Exit.DIFF,
            "different_suffixless_file_json.txt",
            id="different-json",
        ),
    ],
)
def test_diff_against_compares_single_file_results_at_a_suffixless_output(
    baseline: str,
    current: str,
    extra_args: tuple[str, ...],
    expected_exit: Exit,
    expected_stdout: str,
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Single-module results at a virtual output without a suffix are compared as that file."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / current,
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=["--diff-against", str(JSON_SCHEMA_INPUT_DIFF_PATH / baseline), "--disable-timestamp", *extra_args],
        expected_exit=expected_exit,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / expected_stdout,
        assert_no_stderr=True,
    )


@pytest.mark.parametrize(
    ("baseline", "current", "expected_stdout"),
    [
        pytest.param("api.yaml", "input_diff/new.yaml", "single_file_to_directory.txt", id="single-file-to-directory"),
        pytest.param("input_diff/new.yaml", "api.yaml", "directory_to_single_file.txt", id="directory-to-single-file"),
    ],
)
def test_diff_against_reports_single_file_and_directory_results_by_path(
    baseline: str, current: str, expected_stdout: str, output_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single-module file and the modules of a directory at the same virtual output are different paths."""
    run_main_and_assert(
        input_path=DATA_PATH / "openapi" / current,
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--diff-against", str(DATA_PATH / "openapi" / baseline), "--disable-timestamp"],
        expected_exit=Exit.DIFF,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / expected_stdout,
        assert_no_stderr=True,
    )


def test_diff_against_single_file_comparison_reports_added_when_old_output_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report a new-only file consistently in text and structured input-diff output."""
    generated_output = tmp_path / "new.py"
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / "new.json",
        output_path=generated_output,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp"],
        capsys=capsys,
        assert_no_stderr=True,
    )

    comparison = _compare_generated_outputs(
        generated_output,
        tmp_path / "old.py",
        "utf-8",
        OutputComparisonOptions(is_directory_output=False, input_diff=True, single_file_display_path="models.py"),
    )
    _write_comparison_output(comparison, None, kind="input-diff")
    assert_output(capsys.readouterr().out, INPUT_DIFF_EXPECTED_PATH / "added_single_file.txt")

    _write_comparison_output(comparison, "json", kind="input-diff")
    assert_output(capsys.readouterr().out, INPUT_DIFF_EXPECTED_PATH / "added_single_file_json.txt")


@pytest.mark.cli_doc(
    options=["--diff-against"],
    option_description=(
        "Compare generated code from a baseline input with the current schema without writing files.\n\n"
        "Pass the baseline input with `--diff-against` and the current schema with `--input`.\n"
        "The command generates both, then shows the generated-code diff from baseline to current.\n"
        "The required `--output` value is a virtual destination: it selects single-file or\n"
        "directory layout and formatter settings, but datamodel-code-generator never writes it.\n"
        "The command exits non-zero when the formatted generated outputs differ, making it useful\n"
        "for reviewing schema migrations in CI."
    ),
    input_schema="openapi/input_diff/new.yaml",
    cli_args=[
        "--input",
        "tests/data/openapi/input_diff/new.yaml",
        "--diff-against",
        "tests/data/openapi/input_diff/old.yaml",
        "--output",
        "models",
        "--input-file-type",
        "openapi",
        "--disable-timestamp",
    ],
    expected_stdout="main/input_diff/different_directory.txt",
    related_options=["--input", "--output", "--check", "--output-format"],
)
def test_diff_against_reports_added_and_removed_directory_files_without_writing_virtual_output(
    output_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Directory comparison uses the existing generated-file manifest comparison."""
    run_main_and_assert(
        input_path=OPENAPI_INPUT_DIFF_PATH / "new.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--diff-against",
            str(OPENAPI_INPUT_DIFF_PATH / "old.yaml"),
            "--disable-timestamp",
        ],
        expected_exit=Exit.DIFF,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "different_directory.txt",
        assert_no_stderr=True,
    )


def test_diff_against_json_reports_added_and_removed_directory_files_without_writing_virtual_output(
    output_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Structured diff output preserves added and removed directory entries."""
    run_main_and_assert(
        input_path=OPENAPI_INPUT_DIFF_PATH / "new.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--diff-against",
            str(OPENAPI_INPUT_DIFF_PATH / "old.yaml"),
            "--disable-timestamp",
            "--output-format",
            "json",
        ],
        expected_exit=Exit.DIFF,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "different_directory_json.txt",
        assert_no_stderr=True,
    )


def test_diff_against_json_reports_structured_differences_without_writing_virtual_output(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON output reuses the generated-output difference payload."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / "new.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--diff-against",
            str(JSON_SCHEMA_INPUT_DIFF_PATH / "old.json"),
            "--disable-timestamp",
            "--output-format",
            "json",
        ],
        expected_exit=Exit.DIFF,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "different_file_json.txt",
        assert_no_stderr=True,
    )


def test_diff_against_json_reports_identical_inputs_without_writing_virtual_output(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Structured diff output reports a successful comparison without a file write."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / "same_new.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--diff-against",
            str(JSON_SCHEMA_INPUT_DIFF_PATH / "same_old.json"),
            "--disable-timestamp",
            "--output-format",
            "json",
        ],
        expected_exit=Exit.OK,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical_json.txt",
        assert_no_stderr=True,
    )


def test_diff_against_preserves_a_preexisting_virtual_file(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Diff generation uses a physical temporary file, not the virtual file destination."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / "same_new.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--diff-against",
            str(JSON_SCHEMA_INPUT_DIFF_PATH / "same_old.json"),
            "--disable-timestamp",
        ],
        expected_exit=Exit.OK,
        assert_func=assert_file_content,
        expected_file=VIRTUAL_FILE_SENTINEL,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical.txt",
        assert_no_stderr=True,
        copy_files=[(VIRTUAL_FILE_SENTINEL, output_file)],
    )


def test_diff_against_preserves_a_preexisting_virtual_directory(
    output_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Diff generation leaves every pre-existing virtual directory file intact."""
    output_dir.mkdir()
    run_main_and_assert(
        input_path=OPENAPI_INPUT_DIFF_PATH / "new.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--diff-against",
            str(OPENAPI_INPUT_DIFF_PATH / "new.yaml"),
            "--disable-timestamp",
        ],
        expected_exit=Exit.OK,
        expected_directory=VIRTUAL_DIRECTORY_SENTINEL,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical.txt",
        assert_no_stderr=True,
        copy_files=[(VIRTUAL_DIRECTORY_SENTINEL / "sentinel.py", output_dir / "sentinel.py")],
    )


def test_diff_against_uses_the_virtual_output_context_for_relative_custom_formatter_resources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Custom formatter resources stay relative to the virtual output, not a temp directory."""
    virtual_output = tmp_path / "virtual-output" / "models.py"
    virtual_output.parent.mkdir()
    run_main_and_assert(
        input_path=JSON_SCHEMA_INPUT_DIFF_PATH / "same_new.json",
        output_path=virtual_output,
        input_file_type="jsonschema",
        extra_args=[
            "--diff-against",
            str(JSON_SCHEMA_INPUT_DIFF_PATH / "same_old.json"),
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--custom-formatters",
            "tests.data.python.custom_formatters.add_license",
            "--custom-formatters-kwargs",
            str(DATA_PATH / "config" / "input_diff_relative_license.json"),
        ],
        expected_exit=Exit.OK,
        output_should_not_exist=True,
        capsys=capsys,
        expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical.txt",
        assert_no_stderr=True,
        copy_files=[
            (
                DATA_PATH / "python" / "custom_formatters" / "license_example.txt",
                virtual_output.parent / "license.txt",
            )
        ],
    )


def test_diff_against_supports_one_selected_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A profile remains a valid configuration layer for one two-input comparison."""
    virtual_output = tmp_path / "profile-models.py"
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.datamodel-codegen.profiles.compare]
use-double-quotes = true
""",
        encoding="utf-8",
    )
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=(JSON_SCHEMA_INPUT_DIFF_PATH / "same_new.json").resolve(),
            output_path=virtual_output,
            input_file_type="jsonschema",
            extra_args=[
                "--profile",
                "compare",
                "--diff-against",
                str((JSON_SCHEMA_INPUT_DIFF_PATH / "same_old.json").resolve()),
                "--disable-timestamp",
                "--formatters",
                "builtin",
            ],
            expected_exit=Exit.OK,
            output_should_not_exist=True,
            capsys=capsys,
            expected_stdout_path=INPUT_DIFF_EXPECTED_PATH / "identical.txt",
            assert_no_stderr=True,
        )


def test_diff_against_json_fixture_matches_structured_output_schema() -> None:
    """The input-diff payload remains part of the public structured-output schema."""
    schema_path = DATA_PATH / "expected" / "main_kr" / "output_format_json" / "structured_output_schema.txt"
    jsonschema.validate(
        instance=json.loads((INPUT_DIFF_EXPECTED_PATH / "different_file_json.txt").read_text(encoding="utf-8")),
        schema=json.loads(schema_path.read_text(encoding="utf-8")),
    )


@pytest.mark.parametrize(
    ("args", "expected_stderr"),
    [
        (
            ["--diff-against", str(JSON_SCHEMA_INPUT_DIFF_PATH / "old.json"), "--output", "output.py"],
            "--diff-against requires --input",
        ),
        (
            ["--input", str(JSON_SCHEMA_INPUT_DIFF_PATH / "new.json"), "--diff-against", "old.json"],
            "--diff-against requires --output",
        ),
        (
            [
                "--input",
                str(JSON_SCHEMA_INPUT_DIFF_PATH / "new.json"),
                "--diff-against",
                "old.json",
                "--output",
                "output.py",
                "--check",
            ],
            "--diff-against and --check",
        ),
        (
            [
                "--input",
                str(JSON_SCHEMA_INPUT_DIFF_PATH / "new.json"),
                "--diff-against",
                "old.json",
                "--output",
                "output.py",
                "--watch",
            ],
            "--diff-against and --watch",
        ),
        (
            [
                "--input",
                str(JSON_SCHEMA_INPUT_DIFF_PATH / "new.json"),
                "--diff-against",
                "old.json",
                "--output",
                "output.py",
                "--emit-model-metadata",
                "metadata.json",
            ],
            "--diff-against cannot be used with --emit-model-metadata",
        ),
        (
            [
                "--input",
                str(JSON_SCHEMA_INPUT_DIFF_PATH / "new.json"),
                "--diff-against",
                "old.json",
                "--output",
                "output.py",
                "--input-model",
                "tests.data.python.input_model.pydantic_models:User",
            ],
            "--diff-against cannot be used with --input-model",
        ),
        (
            [
                "--input",
                str(JSON_SCHEMA_INPUT_DIFF_PATH / "new.json"),
                "--url",
                "https://example.com/schema.json",
                "--diff-against",
                "old.json",
                "--output",
                "output.py",
            ],
            "--diff-against cannot be used with --url",
        ),
        (
            [
                "--input",
                str(JSON_SCHEMA_INPUT_DIFF_PATH / "new.json"),
                "--diff-against",
                "old.json",
                "--output",
                "output.py",
                "--fail-on-multi-module-stdout",
            ],
            "--diff-against cannot be used with --fail-on-multi-module-stdout",
        ),
    ],
)
def test_diff_against_rejects_incompatible_inputs_and_outputs(
    args: list[str], expected_stderr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Diff mode rejects commands that cannot preserve its two-local-input contract."""
    run_main_with_args(
        args,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr,
    )
