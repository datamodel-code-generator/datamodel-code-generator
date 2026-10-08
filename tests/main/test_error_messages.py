"""Regression tests for malformed CLI input diagnostics."""

from __future__ import annotations

import json
import py_compile
import shutil
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from graphql import GraphQLSyntaxError
from graphql import Source as GraphQLSource

from datamodel_code_generator import (
    DanglingRefWarning,
    Error,
    InputFileType,
    InvalidFileFormatError,
    YamlValue,
    generate,
)
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.config import GenerateConfig
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.parser.base import Source, dump_templates
from datamodel_code_generator.parser.graphql import GraphQLParser
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.parser.openapi import OpenAPIParser
from tests.conftest import assert_output, assert_warnings_contain, create_assert_file_content
from tests.main.conftest import (
    DATA_PATH,
    InputFileTypeLiteral,
    run_generate_file_and_assert,
    run_main_and_assert,
    run_main_with_args,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Literal


MALFORMED_DATA_PATH = DATA_PATH / "malformed"
EXPECTED_MALFORMED_PATH = DATA_PATH / "expected" / "main" / "malformed"
assert_file_content = create_assert_file_content(EXPECTED_MALFORMED_PATH)
TRACEBACK_HEADER = "Traceback (most recent call last)"

ERROR_CASES: tuple[tuple[str, InputFileTypeLiteral, str], ...] = (
    (
        "truncated_jsonschema.json",
        "jsonschema",
        "Invalid file format for jsonschema at truncated_jsonschema.json",
    ),
    ("bad_openapi.yaml", "openapi", "Invalid file format for openapi at bad_openapi.yaml"),
    ("bad.graphql", "graphql", "Invalid file format for graphql at bad.graphql"),
    ("non_dict_root.yaml", "openapi", "Invalid file format for openapi at non_dict_root.yaml"),
    (
        "pointer_through_scalar_openapi.yaml",
        "openapi",
        "Error at schema path 'pointer_through_scalar_openapi.yaml/#/components/schemas/Name'",
    ),
    (
        "pointer_through_scalar.json",
        "jsonschema",
        "Error at schema path 'pointer_through_scalar.json/#/definitions/Name': ValidationError",
    ),
    ("wrong_type_properties.json", "jsonschema", "Error at schema path 'wrong_type_properties.json'"),
    ("required_as_string.json", "jsonschema", "Error at schema path 'required_as_string.json'"),
    ("enum_as_dict.json", "jsonschema", "Error at schema path 'enum_as_dict.json'"),
    ("missing_external_ref.json", "jsonschema", "$ref file not found:"),
    ("malformed_external_ref.json", "jsonschema", "truncated_external.json"),
    ("empty_jsonschema.json", "jsonschema", "Models not found in the input data"),
)

MISSING_INPUT_CASES: tuple[tuple[InputFileTypeLiteral | None, str], ...] = (
    (None, "missing.json"),
    ("jsonschema", "File not found"),
)


def test_output_path_does_not_overwrite_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject destructive output before the input schema is changed."""
    source = DATA_PATH / "jsonschema" / "person.json"
    input_path = tmp_path / source.name
    shutil.copyfile(source, input_path)

    run_main_and_assert(
        input_path=input_path,
        output_path=input_path,
        input_file_type="jsonschema",
        extra_args=["--output-format", "json"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Output path must not overwrite an input path",
        skip_code_validation=True,
    )
    assert_output(
        f"{input_path.read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


def test_model_metadata_path_does_not_overwrite_input(
    tmp_path: Path,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Apply the input protection to optional generated artifacts."""
    source = DATA_PATH / "jsonschema" / "person.json"
    input_path = tmp_path / source.name
    shutil.copyfile(source, input_path)

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--emit-model-metadata", str(input_path)],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Model metadata path must not overwrite an input path",
        output_should_not_exist=True,
    )
    assert_output(
        f"{input_path.read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
def test_symlinked_output_path_does_not_overwrite_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resolve a file symlink before checking for input overwrite."""
    source = DATA_PATH / "jsonschema" / "person.json"
    input_path = tmp_path / source.name
    shutil.copyfile(source, input_path)
    output_path = tmp_path / "schema-link.json"
    output_path.symlink_to(input_path)

    run_main_and_assert(
        input_path=input_path,
        output_path=output_path,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Output path must not overwrite an input path",
        skip_code_validation=True,
    )
    assert_output(
        f"{input_path.read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
def test_generate_symlinked_output_path_does_not_overwrite_input(tmp_path: Path) -> None:
    """Protect public API inputs when an output keeps its symlink spelling."""
    source = DATA_PATH / "jsonschema" / "person.json"
    input_path = tmp_path / source.name
    shutil.copyfile(source, input_path)
    output_path = tmp_path / "schema-link.json"
    output_path.symlink_to(input_path)

    with pytest.raises(Error, match="Output path must not overwrite an input path"):
        generate(input_path, input_file_type=InputFileType.JsonSchema, output=output_path)

    assert_output(
        f"{input_path.read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


@pytest.mark.skipif(sys.platform == "win32", reason="hardlink creation requires elevated privileges")
def test_generate_hardlinked_output_path_does_not_overwrite_input(tmp_path: Path) -> None:
    """Protect public API inputs when an output is a hardlink."""
    source = DATA_PATH / "jsonschema" / "person.json"
    input_path = tmp_path / source.name
    shutil.copyfile(source, input_path)
    output_path = tmp_path / "schema-hardlink.json"
    output_path.hardlink_to(input_path)

    with pytest.raises(Error, match="Output path must not overwrite an input path"):
        generate(input_path, input_file_type=InputFileType.JsonSchema, output=output_path)

    assert_output(
        f"{input_path.read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


def test_generate_list_input_does_not_overwrite_input(tmp_path: Path) -> None:
    """Protect every file supplied through the public list-input API."""
    source = DATA_PATH / "jsonschema" / "person.json"
    input_path = tmp_path / source.name
    shutil.copyfile(source, input_path)

    with pytest.raises(Error, match="Output path must not overwrite an input path"):
        generate([input_path], input_file_type=InputFileType.JsonSchema, output=input_path)

    assert_output(
        f"{input_path.read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


@pytest.mark.parametrize(("entrypoint", "update_lock"), [("api", False), ("cli", False), ("api", True), ("cli", True)])
@pytest.mark.parametrize("layout", ["sibling", "child", "same"])
@pytest.mark.parametrize("metadata_location", [None, "inside", "outside"])
@pytest.mark.parametrize("input_file_type", [InputFileType.JsonSchema, InputFileType.Auto])
def test_output_path_can_write_inside_input_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    entrypoint: Literal["api", "cli"],
    layout: str,
    metadata_location: str | None,
    input_file_type: InputFileType,
    update_lock: bool,
) -> None:
    """Repeated API and CLI generation retain inputs and ignore their own artifacts."""
    source = DATA_PATH / "jsonschema" / "person.json"
    input_path = tmp_path / "schemas"
    input_path.mkdir()
    shutil.copyfile(source, input_path / source.name)
    output_path = {"sibling": tmp_path / "generated", "child": input_path / "generated", "same": input_path}[layout]
    metadata = None
    if metadata_location is not None:
        metadata = (input_path if metadata_location == "inside" else tmp_path) / "model_map.json"
    expected_directory = tmp_path / "expected"
    expected_directory.mkdir()
    shutil.copyfile(DATA_PATH / "expected" / "main" / "person.py", expected_directory / "person.py")
    shutil.copyfile(EXPECTED_MALFORMED_PATH / "directory_input_init.py", expected_directory / "__init__.py")
    for run in range(2):
        match entrypoint:
            case "api":
                run_generate_file_and_assert(
                    input_path=input_path,
                    output_path=output_path,
                    input_file_type=input_file_type,
                    disable_timestamp=True,
                    formatters=[Formatter.BUILTIN],
                    emit_model_metadata=metadata,
                    update_lock=update_lock,
                    lockfile=tmp_path / "refs.lock",
                    expected_directory=expected_directory,
                )
            case _:
                extra_args = ["--disable-timestamp", "--formatters", "builtin"]
                if metadata is not None:
                    extra_args.extend(["--emit-model-metadata", str(metadata)])
                if update_lock:
                    extra_args.extend(["--update-lock", "--lockfile", str(tmp_path / "refs.lock")])
                run_main_and_assert(
                    input_path=input_path,
                    output_path=output_path,
                    input_file_type="jsonschema" if input_file_type == InputFileType.JsonSchema else None,
                    extra_args=extra_args,
                    expected_directory=expected_directory,
                    capsys=capsys,
                    assert_no_stderr=input_file_type == InputFileType.JsonSchema,
                )
        py_compile.compile(str(output_path / "person.py"), doraise=True)
        if run == 0:
            init_path = output_path / "__init__.py"
            init_path.write_text(init_path.read_text(encoding="utf-8").rstrip(), encoding="utf-8")
            module_path = output_path / "person.py"
            module_path.write_text(
                module_path.read_text(encoding="utf-8").replace(
                    "from __future__ import annotations", "from __future__ import annotations  # generated: model", 1
                ),
                encoding="utf-8",
            )
    assert_output(
        f"{(input_path / source.name).read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


def test_invalid_pyproject_configuration_is_a_clean_cli_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid pyproject values report a usage error instead of a traceback."""
    shutil.copyfile(DATA_PATH / "config" / "pyproject_invalid_target.toml", tmp_path / "pyproject.toml")
    monkeypatch.chdir(tmp_path)

    run_main_with_args(
        [],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid configuration: 1 validation error for Config",
    )


@pytest.mark.parametrize(
    ("suffix", "inside_output", "input_file_type", "header", "yaml_key"),
    [
        (".schema", False, "jsonschema", None, None),
        (".py", False, "jsonschema", None, None),
        (".py", True, "jsonschema", None, None),
        (".py", True, None, None, None),
        (".json", False, "jsonschema", "\n", None),
        (".py", True, "jsonschema", "# Shared copyright header", None),
        (".py", True, "jsonschema", "# Shared copyright header", "Alias = model"),
        (".py", True, "jsonschema", "# Shared copyright header", "Alias = lambda x"),
        (".py", True, "jsonschema", "# Shared copyright header", "from module import name"),
        (".py", True, "jsonschema", "# Shared copyright header", "class Model"),
    ],
)
def test_directory_input_preserves_nonstandard_schema_files(
    tmp_path: Path,
    suffix: str,
    inside_output: bool,
    input_file_type: InputFileTypeLiteral | None,
    header: str | None,
    yaml_key: str | None,
) -> None:
    """Output location and suffix alone cannot identify a generated artifact."""
    input_path = tmp_path / "schemas"
    output_path = input_path / "generated"
    output_path.mkdir(parents=True)
    source_path = (output_path if inside_output else input_path) / f"person{suffix}"
    shutil.copyfile(DATA_PATH / "jsonschema" / "person.json", source_path)
    source_text = source_path.read_text(encoding="utf-8")
    if yaml_key is not None:
        source_text = yaml.safe_dump({yaml_key: {}, **json.loads(source_text)}, sort_keys=False)
    if header is not None and header.strip():
        source_path.write_text(f"{header}\n{source_text}", encoding="utf-8")
    original_source = tmp_path / "original-schema.txt"
    shutil.copyfile(source_path, original_source)
    module_path = output_path / ("generated/person.py" if inside_output else "person.py")
    extra_args = ["--disable-timestamp", "--formatters", "builtin"]
    if header is not None:
        extra_args.extend(["--custom-file-header", header])
    for _ in range(2):
        run_main_and_assert(
            input_path=input_path,
            output_path=output_path,
            input_file_type=input_file_type,
            extra_args=extra_args,
            # The output tree also contains the intentionally non-Python .py input.
            skip_code_validation=yaml_key is not None,
        )
        generated = module_path.read_text(encoding="utf-8")
        if header is not None:
            if header.strip():
                generated = generated.removeprefix(header + "\n\n")
            generated = f"# generated by datamodel-codegen:\n#   filename:  person.json\n\n{generated}"
        else:
            generated = generated.replace(
                f"#   filename:  {source_path.relative_to(input_path).as_posix()}", "#   filename:  person.json"
            )
        assert_output(generated, DATA_PATH / "expected" / "main" / "person.py")
    assert_output(source_path.read_text(encoding="utf-8"), original_source)


@pytest.mark.parametrize(("mode", "trailing"), [("prepend", "\n\n"), ("prepend", "\r\n\r\n"), ("replace", "\n\n")])
@pytest.mark.parametrize("comment_header", [False, True])
def test_directory_input_custom_header_trailing_newlines(
    tmp_path: Path, mode: str, trailing: str, comment_header: bool
) -> None:
    """Recognize the emitted prepend/replace header without changing its output."""
    input_path = tmp_path / "schemas"
    input_path.mkdir()
    shutil.copyfile(DATA_PATH / "jsonschema" / "person.json", input_path / "person.json")
    output_path = input_path / "generated"
    header = (DATA_PATH / "custom_file_header.txt").read_text(encoding="utf-8").rstrip("\r\n") if comment_header else ""
    for _ in range(2):
        run_main_and_assert(
            input_path=input_path,
            output_path=output_path,
            input_file_type="jsonschema",
            extra_args=[
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--custom-file-header",
                header + trailing,
                "--custom-file-header-mode",
                mode,
            ],
        )
        generated = (output_path / "person.py").read_text(encoding="utf-8")
        if mode == "prepend":
            generated = generated.removeprefix(header + "\n#\n")
        else:
            generated = generated.removeprefix(header + "\n\n")
            generated = f"# generated by datamodel-codegen:\n#   filename:  person.json\n\n{generated}"
        assert_output(generated, DATA_PATH / "expected" / "main" / "person.py")


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
@pytest.mark.parametrize("target_inside", [False, True])
def test_directory_input_excludes_metadata_symlink_spellings(tmp_path: Path, target_inside: bool) -> None:
    """Exclude both a configured metadata symlink and its in-directory target."""
    input_path = tmp_path / "schemas"
    input_path.mkdir()
    shutil.copyfile(DATA_PATH / "jsonschema" / "person.json", input_path / "person.json")
    output_path = tmp_path / "generated"
    metadata = input_path / "model_map.json"
    metadata.symlink_to((input_path if target_inside else tmp_path) / "metadata.json")
    expected_directory = tmp_path / "expected"
    expected_directory.mkdir()
    shutil.copyfile(DATA_PATH / "expected" / "main" / "person.py", expected_directory / "person.py")
    shutil.copyfile(EXPECTED_MALFORMED_PATH / "directory_input_init.py", expected_directory / "__init__.py")
    for _ in range(2):
        run_main_and_assert(
            input_path=input_path,
            output_path=output_path,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp", "--formatters", "builtin", "--emit-model-metadata", str(metadata)],
            expected_directory=expected_directory,
        )


def test_directory_input_does_not_ignore_unrelated_bytecode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A Python cache is excluded only when its source is recognized as generated output."""
    input_path = tmp_path / "schemas"
    output_path = input_path / "generated"
    output_path.mkdir(parents=True)
    schema_path = output_path / "schema.py"
    shutil.copyfile(DATA_PATH / "jsonschema" / "simple_string.json", schema_path)
    py_compile.compile(str(schema_path), doraise=True)
    run_main_and_assert(
        input_path=input_path,
        output_path=output_path,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="codec can't decode",
        extra_args=["--formatters", "builtin"],
    )


def test_directory_input_retains_type_aliases_without_future_imports(tmp_path: Path) -> None:
    """Recognize generated assignment-only modules without parsing target-version syntax."""
    source_path = tmp_path / "schemas"
    source_path.mkdir()
    shutil.copyfile(DATA_PATH / "jsonschema" / "external_collapse" / "child.json", source_path / "child.json")
    for run in range(2):
        run_main_and_assert(
            input_path=source_path,
            output_path=source_path,
            input_file_type="jsonschema",
            extra_args=[
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--use-type-alias",
                "--target-python-version",
                "3.12",
                "--disable-future-imports",
            ],
            assert_func=assert_file_content,
            output_to_expected=[("child.py", "directory_input_alias.py")],
            skip_code_validation=sys.version_info < (3, 12),
        )
        if run == 0:
            module_path = source_path / "child.py"
            module_path.write_text(
                module_path.read_text(encoding="utf-8").rstrip() + "  # alias: generated\n", encoding="utf-8"
            )


def test_directory_auto_input_does_not_infer_from_generated_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A directory containing only previous output has no schema to infer."""
    source_path = tmp_path / "schemas"
    source_path.mkdir()
    shutil.copyfile(DATA_PATH / "expected" / "main" / "person.py", source_path / "person.py")
    run_main_and_assert(
        input_path=source_path,
        output_path=source_path,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="File not found:",
        extra_args=["--formatters", "builtin"],
    )


@pytest.mark.parametrize(
    ("directory_name", "extra_args"),
    [
        pytest.param("output", (), id="plain"),
        pytest.param("output", ("--check",), id="plain-check"),
        pytest.param("output.py.d", (), id="dotted"),
        pytest.param("output.py.d", ("--check",), id="dotted-check"),
    ],
)
def test_single_module_output_directory_is_a_clean_cli_error(
    directory_name: str,
    extra_args: tuple[str, ...],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Single-module output rejects existing plain and dotted directories."""
    output_path = tmp_path / directory_name
    output_path.mkdir()

    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "person.json",
        output_path=output_path,
        input_file_type="jsonschema",
        extra_args=extra_args,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Single-module output requires a file path, not a directory",
    )


def test_modular_output_file_is_a_clean_cli_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Modular output rejects an existing file at the output path and leaves it untouched."""
    existing_file = DATA_PATH / "expected" / "main" / "person.py"
    output_path = tmp_path / "output"

    run_main_and_assert(
        input_path=DATA_PATH / "openapi" / "modular.yaml",
        output_path=output_path,
        input_file_type="openapi",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr="Modular references require an output directory, not a file\n",
        copy_files=[(existing_file, output_path)],
    )
    assert_output(output_path.read_text(encoding="utf-8"), existing_file)


def test_modular_output_below_a_file_keeps_the_filesystem_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file above the output path is a filesystem error, not the modular output refusal."""
    occupied_path = tmp_path / "occupied"

    run_main_and_assert(
        input_path=DATA_PATH / "openapi" / "modular.yaml",
        output_path=occupied_path / "output",
        input_file_type="openapi",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=TRACEBACK_HEADER,
        copy_files=[(DATA_PATH / "expected" / "main" / "person.py", occupied_path)],
    )


def test_model_metadata_directory_is_a_clean_cli_error(
    output_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Model metadata rejects a directory destination before generation writes output."""
    metadata_path = tmp_path / "metadata.json"
    metadata_path.mkdir()

    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--emit-model-metadata", str(metadata_path)],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Model metadata output requires a file path, not a directory",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("input_path", "extra_args", "expected_stderr_contains"),
    [
        pytest.param(
            DATA_PATH / "jsonschema" / "person.json",
            ("--custom-formatters", "missing_custom_formatter"),
            "Unable to import custom formatter 'missing_custom_formatter'",
            id="custom-formatter-import",
        ),
        pytest.param(
            DATA_PATH / "jsonschema" / "encoding_test.json",
            ("--encoding", "shift_jis"),
            "Unable to decode input using encoding 'shift_jis'",
            id="input-encoding",
        ),
    ],
)
def test_cli_input_errors_are_clean(
    input_path: Path,
    extra_args: tuple[str, ...],
    expected_stderr_contains: str,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Custom formatter imports and input decoding report CLI errors without tracebacks."""
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=extra_args,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
        output_should_not_exist=True,
    )


@pytest.mark.parametrize("directory_input", [False, True])
def test_missing_custom_file_header_is_a_clean_cli_error(
    output_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    directory_input: bool,
) -> None:
    """Custom header I/O failures report a CLI error without a traceback."""
    header_path = tmp_path / "missing-header.txt"
    input_path = DATA_PATH / "jsonschema" / "person.json"
    if directory_input:
        input_path = tmp_path / "schemas"
        input_path.mkdir()
        shutil.copyfile(DATA_PATH / "jsonschema" / "person.json", input_path / "person.json")
        output_file = input_path / "generated"

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--custom-file-header-path", str(header_path)],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Unable to read custom file header",
        output_should_not_exist=True,
    )


def test_remote_lock_modes_are_mutually_exclusive_in_public_config() -> None:
    """The public config model preserves the parser's remote lock policy guard."""
    with pytest.raises(ValueError, match="--update-lock and --locked cannot be used together"):
        GenerateConfig(update_lock=True, locked=True)


def test_missing_input_conflict_preserves_missing_file_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Do not replace the established missing-input diagnostic with a conflict."""
    missing_path = tmp_path / "missing.json"
    run_main_and_assert(
        input_path=missing_path,
        output_path=missing_path,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="File not found",
        output_should_not_exist=True,
    )


def test_output_and_model_metadata_paths_must_differ(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject two generated artifacts targeting the same path."""
    run_main_and_assert(
        input_path=DATA_PATH / "jsonschema" / "person.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--emit-model-metadata", str(output_file)],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Output and model metadata paths must be different",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize("target", ["input", "output", "metadata"])
@pytest.mark.allow_direct_assert
def test_lockfile_path_conflicts_are_rejected_before_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    target: str,
) -> None:
    """The CLI refuses a lock path that aliases any generated or source artifact."""
    source = tmp_path / "schema.json"
    source.write_text('{"title":"Schema","type":"object"}', encoding="utf-8")
    lockfile = source if target == "input" else tmp_path / "remote.lock"
    output = lockfile if target == "output" else tmp_path / "output.py"
    extra_args = ["--update-lock", "--lockfile", str(lockfile)]
    if target == "metadata":
        extra_args.extend(["--emit-model-metadata", str(lockfile)])

    run_main_and_assert(
        input_path=source,
        output_path=output,
        input_file_type="jsonschema",
        extra_args=extra_args,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Remote lock",
        output_should_not_exist=target != "input",
    )
    assert source.read_text(encoding="utf-8") == '{"title":"Schema","type":"object"}'
    assert not (tmp_path / "remote.lock").exists()


@pytest.mark.allow_direct_assert
def test_public_api_rejects_lockfile_output_conflicts_before_writing(tmp_path: Path) -> None:
    """Public generation shares the CLI's lockfile preflight protection."""
    source = tmp_path / "schema.json"
    source.write_text('{"title":"Schema","type":"object"}', encoding="utf-8")
    lockfile = tmp_path / "remote.lock"

    with pytest.raises(Error, match="Output and Remote lock paths must be different"):
        generate(
            source,
            input_file_type=InputFileType.JsonSchema,
            output=lockfile,
            lockfile=lockfile,
            update_lock=True,
        )

    assert not lockfile.exists()


@pytest.mark.allow_direct_assert
def test_cli_rejects_lockfile_inside_directory_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A directory input cannot contain the lock that generation may replace."""
    input_directory = tmp_path / "schemas"
    input_directory.mkdir()
    (input_directory / "schema.json").write_text('{"title":"Schema","type":"object"}', encoding="utf-8")
    lockfile = input_directory / "remote.lock"

    run_main_and_assert(
        input_path=input_directory,
        output_path=tmp_path / "output",
        input_file_type="jsonschema",
        extra_args=["--update-lock", "--lockfile", str(lockfile)],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Remote lock path must not be inside an input directory",
        output_should_not_exist=True,
    )
    assert not lockfile.exists()


@pytest.mark.allow_direct_assert
def test_cli_rejects_default_project_lock_inside_root_directory_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The project-root default lock is unsafe when the project root is the input."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.datamodel-codegen]\n", encoding="utf-8")
    (project / "schema.json").write_text('{"title":"Schema","type":"object"}', encoding="utf-8")
    monkeypatch.chdir(project)

    run_main_and_assert(
        input_path=project,
        output_path=tmp_path / "output",
        input_file_type="jsonschema",
        extra_args=["--update-lock"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Remote lock path must not be inside an input directory",
        output_should_not_exist=True,
    )
    assert not (project / "datamodel-codegen.lock").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
@pytest.mark.allow_direct_assert
def test_cli_rejects_resolved_lockfile_alias_inside_directory_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Containment checks resolve an existing symlinked parent of the lock path."""
    input_directory = tmp_path / "schemas"
    input_directory.mkdir()
    (input_directory / "schema.json").write_text('{"title":"Schema","type":"object"}', encoding="utf-8")
    alias_directory = tmp_path / "schema-alias"
    alias_directory.symlink_to(input_directory, target_is_directory=True)
    lockfile = alias_directory / "remote.lock"

    run_main_and_assert(
        input_path=input_directory,
        output_path=tmp_path / "output",
        input_file_type="jsonschema",
        extra_args=["--update-lock", "--lockfile", str(lockfile)],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Remote lock path must not be inside an input directory",
        output_should_not_exist=True,
    )
    assert not lockfile.exists()


@pytest.mark.allow_direct_assert
def test_public_api_rejects_lockfile_inside_any_listed_directory_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List input preflight covers every directory, not only the first one."""
    input_directories = [tmp_path / "first", tmp_path / "second"]
    for index, directory in enumerate(input_directories):
        directory.mkdir()
        (directory / f"schema{index}.json").write_text(
            json.dumps({"title": f"Schema{index}", "type": "object"}),
            encoding="utf-8",
        )
    lockfile = input_directories[1] / "remote.lock"
    monkeypatch.chdir(tmp_path)

    with pytest.raises(Error, match="Remote lock path must not be inside an input directory"):
        generate(
            [directory.relative_to(tmp_path) for directory in input_directories],
            input_file_type=InputFileType.JsonSchema,
            lockfile=lockfile,
            update_lock=True,
        )

    assert not lockfile.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
@pytest.mark.allow_direct_assert
def test_public_api_rejects_lockfile_inside_resolved_directory_input_alias(tmp_path: Path) -> None:
    """A symlink-spelled directory input protects its resolved contents too."""
    input_directory = tmp_path / "schemas"
    input_directory.mkdir()
    (input_directory / "schema.json").write_text('{"title":"Schema","type":"object"}', encoding="utf-8")
    input_alias = tmp_path / "schema-alias"
    input_alias.symlink_to(input_directory, target_is_directory=True)
    lockfile = input_directory / "remote.lock"

    with pytest.raises(Error, match="Remote lock path must not be inside an input directory"):
        generate(
            input_alias,
            input_file_type=InputFileType.JsonSchema,
            lockfile=lockfile,
            update_lock=True,
        )

    assert not lockfile.exists()


@pytest.mark.allow_direct_assert
def test_public_api_rejects_default_lock_inside_root_directory_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public API default lock is relative to the caller's working directory."""
    (tmp_path / "schema.json").write_text('{"title":"Schema","type":"object"}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(Error, match="Remote lock path must not be inside an input directory"):
        generate(
            tmp_path,
            input_file_type=InputFileType.JsonSchema,
            update_lock=True,
        )

    assert not (tmp_path / "datamodel-codegen.lock").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation requires elevated privileges")
def test_generate_output_and_model_metadata_symlinks_must_differ(tmp_path: Path) -> None:
    """Reject public API artifact paths that alias the same existing file."""
    source = DATA_PATH / "jsonschema" / "person.json"
    output_path = tmp_path / source.name
    shutil.copyfile(source, output_path)
    metadata_path = tmp_path / "metadata-link.json"
    metadata_path.symlink_to(output_path)

    with pytest.raises(Error, match="Output and model metadata paths must be different"):
        generate(
            source,
            input_file_type=InputFileType.JsonSchema,
            output=output_path,
            emit_model_metadata=metadata_path,
        )

    assert_output(
        f"{output_path.read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


@pytest.mark.skipif(sys.platform == "win32", reason="hardlink creation requires elevated privileges")
def test_generate_output_and_model_metadata_hardlinks_must_differ(tmp_path: Path) -> None:
    """Reject public API artifact paths that are hardlinks to the same file."""
    source = DATA_PATH / "jsonschema" / "person.json"
    output_path = tmp_path / source.name
    shutil.copyfile(source, output_path)
    metadata_path = tmp_path / "metadata-hardlink.json"
    metadata_path.hardlink_to(output_path)

    with pytest.raises(Error, match="Output and model metadata paths must be different"):
        generate(
            source,
            input_file_type=InputFileType.JsonSchema,
            output=output_path,
            emit_model_metadata=metadata_path,
        )

    assert_output(
        f"{output_path.read_text(encoding='utf-8')}\n",
        EXPECTED_MALFORMED_PATH / "path_conflict_input.txt",
    )


@pytest.mark.parametrize(
    ("fixture_name", "input_file_type", "expected_stderr_contains"),
    ERROR_CASES,
    ids=[fixture_name for fixture_name, _, _ in ERROR_CASES],
)
def test_malformed_input_error_messages(
    fixture_name: str,
    input_file_type: InputFileTypeLiteral,
    expected_stderr_contains: str,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Known malformed inputs emit concise diagnostics and a non-zero exit code."""
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / fixture_name,
        output_path=output_file,
        input_file_type=input_file_type,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("parser", "source"),
    [
        (JsonSchemaParser(""), Source(path=MALFORMED_DATA_PATH / "truncated_jsonschema.json", text="{")),
        (OpenAPIParser(""), Source(path=MALFORMED_DATA_PATH / "non_dict_root.yaml", text="- item")),
    ],
    ids=("yaml-syntax", "non-dict-openapi"),
)
def test_uncached_source_parse_error_has_source_context(
    parser: JsonSchemaParser,
    source: Source,
) -> None:
    """Contextualize known failures when source text reaches the uncached loader boundary."""
    with pytest.raises(InvalidFileFormatError, match=source.path.name):
        parser._load_source_dict(source)


@pytest.mark.parametrize(
    ("input_file_type", "expected_stderr_contains"),
    MISSING_INPUT_CASES,
    ids=[input_file_type or "auto" for input_file_type, _ in MISSING_INPUT_CASES],
)
def test_missing_input_error_messages(
    input_file_type: InputFileTypeLiteral | None,
    expected_stderr_contains: str,
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing local input paths emit concise diagnostics regardless of explicit type."""
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "missing.json",
        output_path=output_file,
        input_file_type=input_file_type,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=expected_stderr_contains,
        output_should_not_exist=True,
    )


def test_dangling_local_ref_warns_and_preserves_generated_output(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default mode warns while retaining the legacy fallback model byte-for-byte."""
    with pytest.warns(DanglingRefWarning, match=r"Unresolved local \$ref.+dangling_local_ref\.json") as warning_records:
        run_main_and_assert(
            input_path=MALFORMED_DATA_PATH / "dangling_local_ref.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp"],
            assert_func=assert_file_content,
            expected_file=EXPECTED_MALFORMED_PATH / "dangling_local_ref.py",
            capsys=capsys,
            assert_no_stderr=True,
            importable_module_name="generated_dangling_local_ref",
        )
    dangling_warnings = [warning for warning in warning_records if warning.category is DanglingRefWarning]
    assert_warnings_contain(dangling_warnings, "Unresolved local $ref")
    assert_output(
        "".join(
            f"{filename}\n" for filename in dict.fromkeys(Path(warning.filename).name for warning in dangling_warnings)
        ),
        EXPECTED_MALFORMED_PATH / "dangling_ref_warning_location.txt",
    )


def test_auto_detected_dangling_ref_keeps_source_context_and_generated_output(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep auto-detected source context without changing generated output."""
    with pytest.warns(DanglingRefWarning, match=r"Unresolved local \$ref.+auto_dangling_local_ref\.json"):
        run_main_and_assert(
            input_path=MALFORMED_DATA_PATH / "auto_dangling_local_ref.json",
            output_path=output_file,
            input_file_type=None,
            extra_args=["--disable-timestamp"],
            assert_func=assert_file_content,
            expected_file=EXPECTED_MALFORMED_PATH / "auto_dangling_local_ref.py",
            capsys=capsys,
        )


def test_dangling_local_ref_strict_cli_error(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The opt-in CLI mode promotes only the unresolved local-ref diagnostic."""
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "dangling_local_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--strict-refs", "--disable-timestamp"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Unresolved local $ref",
        output_should_not_exist=True,
    )


def test_out_of_range_array_ref_warns_and_generates_importable_fallback(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Route an unresolved array index through the default dangling-ref fallback."""
    with pytest.warns(DanglingRefWarning, match=r"#/items/9.+out_of_range_array_ref\.json"):
        run_main_and_assert(
            input_path=MALFORMED_DATA_PATH / "out_of_range_array_ref.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp"],
            assert_func=assert_file_content,
            expected_file=EXPECTED_MALFORMED_PATH / "out_of_range_array_ref.py",
            capsys=capsys,
            assert_no_stderr=True,
            importable_module_name="generated_out_of_range_array_ref",
            importable_module_attribute="Field9",
        )


def test_out_of_range_array_ref_strict_cli_error(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Promote an unresolved array index through the existing strict-ref diagnostic."""
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "out_of_range_array_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--strict-refs", "--disable-timestamp"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="out_of_range_array_ref.json: #/items/9",
        output_should_not_exist=True,
    )


def test_multiple_dangling_refs_strict_error_is_aggregate_and_deduplicated(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """List every unique source/ref pair once in a deterministic strict-mode error."""
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "multiple_dangling_refs.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--strict-refs", "--disable-timestamp"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=(
            "Unresolved local $ref targets:\n"
            "- multiple_dangling_refs.json: #/$defs/MissingOne\n"
            "- multiple_dangling_refs.json: #/$defs/MissingTwo\n"
        ),
        output_should_not_exist=True,
    )


def test_dangling_ref_warning_is_not_duplicated_by_stdout_repair() -> None:
    """Emit one warning when invalid dotted-module output triggers an internal reparse."""
    schema: dict[str, YamlValue] = {
        "openapi": "3.0.0",
        "info": {"title": "Invalid dotted schema name", "version": "1.0.0"},
        "paths": {},
        "components": {
            "schemas": {
                "Shipment": {"type": "object"},
                "SaveTrifectaV2.1": {"type": "object"},
                "SaveRequest": {
                    "type": "object",
                    "properties": {
                        "shipment": {"$ref": "#/components/schemas/Shipment"},
                        "trifecta": {"$ref": "#/components/schemas/SaveTrifectaV2.1"},
                        "missing": {"$ref": "#/components/schemas/Missing"},
                    },
                },
            }
        },
    }
    config = GenerateConfig(
        input_file_type=InputFileType.OpenAPI,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    ).model_copy(update={"repair_invalid_dotted_stdout": True})

    with warnings.catch_warnings(record=True) as warning_records:
        warnings.simplefilter("always", DanglingRefWarning)
        generate(schema, config=config)

    dangling_warnings = [warning for warning in warning_records if warning.category is DanglingRefWarning]
    assert_warnings_contain(dangling_warnings, "#/components/schemas/Missing")
    if len(dangling_warnings) != 1:  # pragma: no cover
        pytest.fail(f"Expected one deduplicated dangling-ref warning, got {len(dangling_warnings)}")


def test_openapi_dangling_component_ref_warns(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Apply deferred dangling-ref diagnostics to OpenAPI component schemas."""
    with pytest.warns(DanglingRefWarning, match=r"#/components/schemas/Missing.+dangling_component_ref\.yaml"):
        run_main_and_assert(
            input_path=MALFORMED_DATA_PATH / "dangling_component_ref.yaml",
            output_path=output_file,
            input_file_type="openapi",
            extra_args=["--disable-timestamp"],
            assert_func=assert_file_content,
            expected_file=EXPECTED_MALFORMED_PATH / "dangling_component_ref.py",
            capsys=capsys,
            assert_no_stderr=True,
        )


@pytest.mark.cli_doc(
    options=["--strict-refs"],
    option_description="""Treat unresolved local `$ref` JSON pointers as errors.

By default, an unresolved local pointer emits a warning and retains the generated
fallback `Any` model. Enable this option in validation-sensitive workflows to stop
generation instead. Existing empty schemas remain valid references.""",
    input_schema="malformed/empty_local_ref.json",
    cli_args=["--strict-refs"],
    golden_output="main/malformed/empty_local_ref.py",
)
def test_empty_local_ref_is_valid_in_strict_mode(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Treat unresolved local `$ref` JSON pointers as errors.

    By default, an unresolved local pointer emits a warning and retains the generated
    fallback `Any` model. Enable this option in validation-sensitive workflows to stop
    generation instead. Existing empty schemas remain valid references.
    """
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "empty_local_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--strict-refs", "--disable-timestamp"],
        assert_func=assert_file_content,
        expected_file=EXPECTED_MALFORMED_PATH / "empty_local_ref.py",
        capsys=capsys,
        assert_no_stderr=True,
        importable_module_name="generated_empty_local_ref",
        importable_module_attribute="Empty",
    )


def test_cross_file_empty_ref_is_valid_in_strict_mode(
    output_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resolve a valid pointer after its target file was already parsed."""
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "cross_file_empty_ref",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=["--strict-refs", "--disable-timestamp"],
        expected_directory=EXPECTED_MALFORMED_PATH / "cross_file_empty_ref",
        capsys=capsys,
        assert_no_stderr=True,
    )


@pytest.mark.parametrize(
    ("raw", "ref", "expected_file", "expected_warning"),
    [
        ({"$defs": {"Empty": {}}}, "#/$defs/Empty", "parsed_empty_pointer.py", None),
        ({"$defs": {}}, "#/$defs/Missing", "parsed_missing_pointer.py", r"Unresolved local \$ref"),
        ({"items": [{}]}, "#/items/0", "parsed_array_pointer.py", None),
    ],
    ids=("empty", "missing", "array"),
)
def test_parse_deferred_json_pointer(
    raw: dict[str, YamlValue],
    ref: str,
    expected_file: str,
    expected_warning: str | None,
) -> None:
    """Resolve deferred JSON pointers without conflating missing and empty schemas."""
    parser = JsonSchemaParser("")
    parser.parse_json_pointer(raw, ref, [])
    if expected_warning is None:
        parser._report_parse_diagnostics()
    else:
        with pytest.warns(DanglingRefWarning, match=expected_warning):
            parser._report_parse_diagnostics()
    assert_output(f"{dump_templates(list(parser.results))}\n", EXPECTED_MALFORMED_PATH / expected_file)


def test_parse_file_object_path_without_resolved_ref() -> None:
    """Preserve private-call behavior when object paths are supplied without a ref string."""
    parser = JsonSchemaParser("")
    parser._parse_file({"$defs": {"Empty": {}}}, "Empty", [], ["$defs", "Empty"])
    assert_output(
        f"{dump_templates(list(parser.results))}\n",
        EXPECTED_MALFORMED_PATH / "parsed_empty_pointer.py",
    )


def test_external_dangling_pointer_strict_cli_error(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Apply strict pointer validation while loading an external schema file."""
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "external_dangling_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--strict-refs", "--disable-timestamp"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="empty_local_ref.json: #/$defs/Missing",
        output_should_not_exist=True,
    )


def test_external_dangling_pointer_warns_and_preserves_output(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Warn after parsing a missing fragment in an existing external schema."""
    with pytest.warns(DanglingRefWarning, match=r"#/\$defs/Missing.+empty_local_ref\.json"):
        run_main_and_assert(
            input_path=MALFORMED_DATA_PATH / "external_dangling_ref.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp"],
            assert_func=assert_file_content,
            expected_file=EXPECTED_MALFORMED_PATH / "external_dangling_ref.py",
            capsys=capsys,
            assert_no_stderr=True,
        )


def test_boolean_openapi_schema_remains_valid(
    output_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Skip discriminator inspection for valid boolean OpenAPI schemas."""
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "boolean_schema_openapi.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--disable-timestamp"],
        assert_func=assert_file_content,
        expected_file=EXPECTED_MALFORMED_PATH / "boolean_schema_openapi.py",
        capsys=capsys,
        assert_no_stderr=True,
    )


def test_dangling_local_ref_strict_generate_api() -> None:
    """Expose strict local-ref validation through generate() keyword options."""
    with pytest.raises(Error, match=r"Unresolved local \$ref"):
        generate(
            MALFORMED_DATA_PATH / "dangling_local_ref.json",
            input_file_type=InputFileType.JsonSchema,
            strict_refs=True,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
        )


def test_dangling_local_ref_strict_generate_config() -> None:
    """Expose strict local-ref validation through GenerateConfig."""
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        strict_refs=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
    )
    with pytest.raises(Error, match=r"Unresolved local \$ref"):
        generate(MALFORMED_DATA_PATH / "dangling_local_ref.json", config=config)


def test_dangling_local_ref_strict_pyproject(
    output_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Load strict local-ref validation from pyproject configuration."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "dangling_local_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Unresolved local $ref",
        output_should_not_exist=True,
        copy_files=[
            (
                DATA_PATH / "config" / "pyproject_strict_refs.toml",
                tmp_path / "pyproject.toml",
            )
        ],
    )


def test_dangling_ref_warning_is_not_emitted_before_parsing_finishes(
    output_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A later parser failure wins because pending dangling-ref warnings are not emitted early."""

    def raise_late_parser_error(*_args: object, **_kwargs: object) -> None:
        message = "late parser failure"
        raise RuntimeError(message)

    monkeypatch.setattr(JsonSchemaParser, "_generate_forced_base_models", raise_late_parser_error)
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "dangling_local_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="late parser failure",
        output_should_not_exist=True,
    )


def _raise_json_decode_error() -> None:
    json.loads("{")


def _raise_yaml_parse_error() -> None:
    yaml.safe_load("[")


def _raise_graphql_syntax_error() -> None:
    raise GraphQLSyntaxError(GraphQLSource(""), 0, "formatter failure")


@pytest.mark.parametrize(
    ("parser_type", "input_path", "input_file_type", "raise_error"),
    [
        (
            JsonSchemaParser,
            MALFORMED_DATA_PATH / "empty_local_ref.json",
            "jsonschema",
            _raise_json_decode_error,
        ),
        (
            JsonSchemaParser,
            MALFORMED_DATA_PATH / "empty_local_ref.json",
            "jsonschema",
            _raise_yaml_parse_error,
        ),
        (
            GraphQLParser,
            DATA_PATH / "graphql" / "casing.graphql",
            "graphql",
            _raise_graphql_syntax_error,
        ),
    ],
    ids=("json-decode", "yaml-parse", "graphql-syntax"),
)
def test_input_parse_exception_during_render_keeps_traceback(
    parser_type: type[JsonSchemaParser | GraphQLParser],
    input_path: Path,
    input_file_type: InputFileTypeLiteral,
    raise_error: Callable[[], None],
    output_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Input exception classes outside decoder boundaries still reach the traceback catch-all."""

    def raise_during_render(*_args: object, **_kwargs: object) -> None:
        raise_error()

    monkeypatch.setattr(parser_type, "_generate_module_output", raise_during_render)
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=input_file_type,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=TRACEBACK_HEADER,
        output_should_not_exist=True,
    )


def test_external_ref_parse_error_has_format_and_source_context() -> None:
    """External JSON/YAML decoding failures are translated at the reference-load boundary."""
    with pytest.raises(
        InvalidFileFormatError,
        match=r"Invalid file format for jsonschema at .+truncated_external\.json",
    ):
        generate(
            MALFORMED_DATA_PATH / "malformed_external_ref.json",
            input_file_type=InputFileType.JsonSchema,
            formatters=[Formatter.BUILTIN],
        )


def test_external_ref_non_dict_data_is_misformatted_input(tmp_path: Path) -> None:
    """Translate the decoder's non-mapping result at the exact referenced-file load boundary."""
    ref_path = tmp_path / "list.json"
    ref_path.write_text("[]", encoding="utf-8")
    parser = JsonSchemaParser("", base_path=tmp_path)

    with pytest.raises(
        InvalidFileFormatError,
        match=r"Invalid file format for jsonschema at .+list\.json: TypeError",
    ):
        parser._get_ref_body_from_remote(ref_path.name)


def test_external_ref_cache_type_error_is_not_misclassified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Let cache implementation failures retain their original exception type."""
    parser = JsonSchemaParser("", base_path=tmp_path)

    def raise_cache_error(*_args: object, **_kwargs: object) -> None:
        message = "reference cache failure"
        raise TypeError(message)

    monkeypatch.setattr(parser.remote_object_cache, "get_or_put", raise_cache_error)

    with pytest.raises(TypeError, match="reference cache failure"):
        parser._get_ref_body_from_remote("schema.json")


def test_external_ref_transport_type_error_is_not_misclassified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let transport failures outside text decoding retain their original exception type."""
    parser = JsonSchemaParser("")

    def raise_transport_error(*_args: object, **_kwargs: object) -> None:
        message = "reference transport failure"
        raise TypeError(message)

    monkeypatch.setattr(parser, "_get_text_from_url", raise_transport_error)

    with pytest.raises(TypeError, match="reference transport failure"):
        parser._get_ref_body_from_url("https://example.com/schema.json")


@pytest.mark.parametrize(
    ("url", "body"),
    [
        ("https://example.com/truncated.json", "{"),
        ("https://example.com/truncated.yaml", "schema: ["),
    ],
    ids=("json", "yaml"),
)
def test_malformed_remote_ref_body_has_format_and_url_context(
    url: str,
    body: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Translate malformed fetched bodies at the decoder boundary with their URL."""
    parser = JsonSchemaParser("")
    monkeypatch.setattr(parser, "_get_text_from_url", lambda _: body)

    with pytest.raises(
        InvalidFileFormatError,
        match=rf"Invalid file format for jsonschema at {url}",
    ):
        parser._get_ref_body_from_url(url)


@pytest.mark.parametrize(
    ("parser_type", "fixture_name", "input_file_type"),
    [
        (JsonSchemaParser, "empty_local_ref.json", "jsonschema"),
        (GraphQLParser, "bad.graphql", "graphql"),
    ],
    ids=("jsonschema", "graphql"),
)
def test_unexpected_parser_error_keeps_traceback(
    parser_type: type[JsonSchemaParser | GraphQLParser],
    fixture_name: str,
    input_file_type: InputFileTypeLiteral,
    output_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unexpected parser failures still reach the existing traceback catch-all."""

    def raise_unexpected_error(*_args: object, **_kwargs: object) -> None:
        message = "unexpected parser failure"
        raise RuntimeError(message)

    monkeypatch.setattr(parser_type, "parse_raw", raise_unexpected_error)
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / fixture_name,
        output_path=output_file,
        input_file_type=input_file_type,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=TRACEBACK_HEADER,
        output_should_not_exist=True,
    )


def test_parser_base_exception_is_not_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-Exception control-flow failures are disposed and re-raised unchanged."""

    def raise_keyboard_interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(JsonSchemaParser, "parse_raw", raise_keyboard_interrupt)
    with pytest.raises(KeyboardInterrupt):
        generate(
            MALFORMED_DATA_PATH / "empty_local_ref.json",
            input_file_type=InputFileType.JsonSchema,
            formatters=[Formatter.BUILTIN],
        )


def test_parser_internal_missing_file_keeps_traceback(
    output_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Do not misclassify unrelated parser, template, or plugin file failures as missing input."""

    def raise_internal_file_error(*_args: object, **_kwargs: object) -> None:
        message = "formatter-plugin.json"
        raise FileNotFoundError(message)

    monkeypatch.setattr(JsonSchemaParser, "parse_raw", raise_internal_file_error)
    run_main_and_assert(
        input_path=MALFORMED_DATA_PATH / "empty_local_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=TRACEBACK_HEADER,
        output_should_not_exist=True,
    )
