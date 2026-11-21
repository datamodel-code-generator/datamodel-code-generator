"""Regression coverage for JSON Schema validation edge cases."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_allof_type_list(output_file: Path) -> None:
    """Generate an allOf schema containing a multi-type branch."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "allof_type_list.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10"],
        force_exec_validation=True,
    )


def test_main_jsonschema_required_only_type_list_with_schema_validator(output_file: Path) -> None:
    """Generate schema validators when a required-only branch uses a multi-type declaration."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "schema_validators_required_type_list.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
            "--schema-validator-type",
            "pydantic-v2",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
        force_exec_validation=True,
    )


def test_main_jsonschema_required_only_object_type_list_with_schema_validator(output_file: Path) -> None:
    """Generate schema validators when a required-only branch has type [object]."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "schema_validators_required_object_type_list.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
            "--schema-validator-type",
            "pydantic-v2",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
        force_exec_validation=True,
    )


def test_main_jsonschema_enum_names_allow_null_entries(output_file: Path) -> None:
    """Treat null x-enum-varnames and x-enumNames entries as missing metadata."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "enum_names_null.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10"],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    ("output_model_type", "extra_args"),
    [
        ("pydantic_v2.BaseModel", []),
        ("msgspec.Struct", []),
        ("msgspec.Struct", ["--use-msgspec-nullable-enum-alias"]),
    ],
    ids=["pydantic_v2.BaseModel", "msgspec.Struct", "msgspec.Struct-nullable-enum-alias"],
)
def test_main_jsonschema_enum_names_beside_null(
    output_model_type: str, extra_args: list[str], output_file: Path
) -> None:
    """Give members beside a null value the x-enum-varnames, x-enumNames and x-enum-descriptions entries of 0.83.0.

    A list that gives null an entry shifts the entries of the values after null, which is reported.
    """
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "enum_names_beside_null.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                "--disable-timestamp",
                "--target-python-version",
                "3.10",
                "--output-model-type",
                output_model_type,
                "--use-field-description",
                *extra_args,
            ],
            force_exec_validation=True,
        )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(str(record.message) for record in recorded if record.category is UserWarning)
        ),
        name="warnings",
    )


def test_main_jsonschema_draft4_exclusive_bounds_without_bound(output_file: Path) -> None:
    """Ignore incomplete draft-4 exclusive-bound declarations."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "draft4_exclusive_without_bound.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10"],
        force_exec_validation=True,
    )


OPENWEATHER_SAMPLE = {"id": 420006353, "name": "Mountain View", "cod": 200}
OBJECT_SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}
ROOT_IDS_OF_ANOTHER_TYPE: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {
    "id-number": (OPENWEATHER_SAMPLE, None),
    "dollar-id-number": ({"$id": 420006353, **OBJECT_SCHEMA}, None),
    "id-boolean": ({"id": True, **OBJECT_SCHEMA}, None),
    "dollar-id-boolean": ({"$id": True, **OBJECT_SCHEMA}, None),
    "id-number-in-referenced-document": (
        {"type": "object", "properties": {"other": {"$ref": "other.json"}}},
        {"id": 5122, **OBJECT_SCHEMA},
    ),
}


@pytest.mark.parametrize("case", ROOT_IDS_OF_ANOTHER_TYPE)
def test_main_jsonschema_root_id_of_another_type(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A root ``id`` or ``$id`` that is a number or a boolean is refused as a keyword of the wrong type is (#6881).

    The message names the keyword, the value and the document that holds it, also where a reference loads that
    document; the run ended in a traceback before. pydantic's link to its documentation is left out of the stored
    text, as it names the installed version.
    """
    schema, referenced = ROOT_IDS_OF_ANOTHER_TYPE[case]
    (tmp_path / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    if referenced is not None:
        (tmp_path / "other.json").write_text(json.dumps(referenced), encoding="utf-8")
    run_main_and_assert(
        input_path=tmp_path / "schema.json",
        output_path=tmp_path / "model.py",
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp"],
        expected_exit=Exit.ERROR,
    )
    lines = capsys.readouterr().err.splitlines(keepends=True)
    assert_output("".join(line for line in lines if "errors.pydantic.dev" not in line))


def test_main_jsonschema_root_id_object_generates_as_before(output_file: Path, tmp_path: Path) -> None:
    """A root ``id`` that is an object, such as a property schema written beside ``properties``, is kept as read.

    Runs that read one generate, so its document generates the code it generated before (#6881).
    """
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"id": {"type": "string"}, **OBJECT_SCHEMA}), encoding="utf-8")
    run_main_and_assert(
        input_path=schema,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp"],
    )


DRAFT_07 = "http://json-schema.org/draft-07/schema#"
SETTINGS_WITH_SLIPPED_DESCRIPTION = {
    "$schema": DRAFT_07,
    "title": "Settings",
    "type": "object",
    "$defs": {
        "enable": {"enum": ["cpython-prerelease", "graalpy", "pypy"]},
        "description": "A Python version or flavor to enable.",
    },
    "properties": {"enable": {"type": "array", "items": {"$ref": "#/$defs/enable"}}},
}
DEFS_MEMBERS_SKIPPED: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {
    "draft-07": (SETTINGS_WITH_SLIPPED_DESCRIPTION, None),
    "referenced-draft-07-document": (
        {"$schema": DRAFT_07, "type": "object", "properties": {"settings": {"$ref": "other.json"}}},
        SETTINGS_WITH_SLIPPED_DESCRIPTION,
    ),
}
DEFS_MEMBERS_REFUSED: dict[str, dict[str, Any]] = {
    "2020-12": {**SETTINGS_WITH_SLIPPED_DESCRIPTION, "$schema": "https://json-schema.org/draft/2020-12/schema"},
    "referenced-member": {
        **SETTINGS_WITH_SLIPPED_DESCRIPTION,
        "properties": {"description": {"$ref": "#/$defs/description"}},
    },
}


@pytest.mark.parametrize("case", DEFS_MEMBERS_SKIPPED)
def test_main_jsonschema_defs_member_that_is_not_a_schema_is_skipped(
    case: str, output_file: Path, tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    """A member of ``$defs`` that is not a schema is skipped where ``$defs`` is no keyword (#6880).

    Draft 7 and older have no ``$defs`` keyword, so their meta-schemas do not constrain its members: the document of the
    issue, alone or loaded by a reference, generates the code it generates with the string inside the definition, and
    a warning names the member.
    """
    schema, referenced = DEFS_MEMBERS_SKIPPED[case]
    (tmp_path / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    if referenced is not None:
        (tmp_path / "other.json").write_text(json.dumps(referenced), encoding="utf-8")
    run_main_and_assert(
        input_path=tmp_path / "schema.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp"],
    )
    messages = dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
    assert_output("".join(f"{message}\n" for message in messages))


@pytest.mark.parametrize("case", DEFS_MEMBERS_REFUSED)
def test_main_jsonschema_defs_member_that_is_not_a_schema_is_refused(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A member of ``$defs`` that is not a schema stays refused where ``$defs`` is a keyword, or a reference names it.

    2019-09 and later require every member of ``$defs`` to be a schema; a reference to the member of a draft-07
    document reads it as one. Both are refused with the message they got before (#6880).
    """
    (tmp_path / "schema.json").write_text(json.dumps(DEFS_MEMBERS_REFUSED[case]), encoding="utf-8")
    run_main_and_assert(
        input_path=tmp_path / "schema.json",
        output_path=tmp_path / "model.py",
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp"],
        expected_exit=Exit.ERROR,
    )
    lines = capsys.readouterr().err.splitlines(keepends=True)
    assert_output("".join(line for line in lines if "errors.pydantic.dev" not in line))
