"""Regression coverage for external JSON Schema references."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Error, InputFileType
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from tests.conftest import assert_output, validate_generated_code
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_generate_file_and_assert, run_main_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_nested_external_definitions_collapse_root_models(output_file: Path) -> None:
    """Resolve nested external definitions once while collapsing their root models."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "nested_external_defs" / "root.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="nested_external_defs.py",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10", "--collapse-root-models"],
        force_exec_validation=True,
    )


def test_main_jsonschema_directory_external_ref_is_wrapped(
    capsys: pytest.CaptureFixture[str],
    output_file: Path,
) -> None:
    """Report a directory external reference as a generator error."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "external_ref_errors" / "directory.json",
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
    )
    assert_output(capsys.readouterr().err, EXPECTED_JSON_SCHEMA_PATH / "directory_external_ref.txt")


def test_main_jsonschema_missing_external_ref_is_wrapped(
    capsys: pytest.CaptureFixture[str],
    output_file: Path,
) -> None:
    """Report a missing external reference as a generator error."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "external_ref_errors" / "not_found.json",
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
    )
    missing_ref = JSON_SCHEMA_DATA_PATH / "external_ref_errors" / "missing.json"
    output = capsys.readouterr().err.replace(str(missing_ref), "<missing-ref>")
    assert_output(output, EXPECTED_JSON_SCHEMA_PATH / "not_found_external_ref.txt")


def test_generate_jsonschema_nested_external_ref_with_relative_base_path(output_file: Path) -> None:
    """Keep the relative-base fast path working for nested external references."""
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "nested_external_defs" / "root.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        assert_func=assert_file_content,
        expected_file="nested_external_defs.py",
        disable_timestamp=True,
        collapse_root_models=True,
    )
    validate_generated_code(output_file.read_text(encoding="utf-8"), str(output_file), do_exec=True)


def test_jsonschema_file_uri_directory_external_ref_is_wrapped() -> None:
    """Wrap actual file-URI directory reads in the generator error type."""
    directory = JSON_SCHEMA_DATA_PATH / "external_ref_errors" / "directory"
    parser = JsonSchemaParser("", base_path=directory.parent)
    file_uri = directory.as_uri()

    with pytest.raises(Error) as exception_info:
        parser._get_ref_body_from_url(file_uri)

    output = f"{str(exception_info.value).replace(file_uri, '{file_uri}')}\n"
    assert_output(output, EXPECTED_JSON_SCHEMA_PATH / "file_uri_directory_external_ref.txt")


def test_jsonschema_non_directory_permission_error_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Do not misclassify unrelated permission failures as directory reads."""
    source = tmp_path / "blocked.json"
    source.write_text("{}", encoding="utf-8")
    parser = JsonSchemaParser("", base_path=tmp_path)
    permission_error = PermissionError("blocked file")

    def raise_permission_error(*_args: object, **_kwargs: object) -> None:
        raise permission_error

    monkeypatch.setattr(parser.remote_object_cache, "get_or_put", raise_permission_error)
    with pytest.raises(PermissionError) as exception_info:
        parser._get_ref_body_from_remote(source.name)

    assert_output(
        f"same_exception={exception_info.value is permission_error}\nmessage={exception_info.value}\n",
        EXPECTED_JSON_SCHEMA_PATH / "permission_external_ref.txt",
    )


def test_jsonschema_external_ref_mapping_returns_loaded_reference() -> None:
    """Keep configured external mappings as already-loaded graph leaves."""
    source = JSON_SCHEMA_DATA_PATH / "external_anchor" / "child.json"
    parser = JsonSchemaParser(
        source,
        base_path=source.parent,
        external_ref_mapping={str(source): "external.models"},
    )

    reference = parser.resolve_ref(f"{source}#/$defs/AnchoredChild")

    output = "loaded\n" if reference.loaded else "not loaded\n"
    assert_output(output, EXPECTED_JSON_SCHEMA_PATH / "mapped_external_ref.txt")


def test_jsonschema_malformed_external_ref_is_rejected_before_external_mapping() -> None:
    """Reject malformed refs before either external-mapping fast path runs."""
    source = JSON_SCHEMA_DATA_PATH / "external_ref_errors" / "child.json"
    ref = f"{source.name}#/$defs/Child#extra"
    parser = JsonSchemaParser(
        source,
        base_path=source.parent,
        external_ref_mapping={str(source): "external.models"},
    )

    output = ""
    for resolve in (parser.get_ref_data_type, parser.resolve_ref):
        with pytest.raises(Error) as exception_info:
            resolve(ref)
        output += f"{exception_info.value}\n"

    assert_output(output, EXPECTED_JSON_SCHEMA_PATH / "malformed_mapped_external_ref.txt")
