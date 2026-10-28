"""End-to-end coverage for scalar and array roots wrapped in allOf."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Formatter, InputFileType
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    XML_SCHEMA_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_generate_and_assert,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("entry_point", ["api", "cli"])
@pytest.mark.parametrize("field_constraints", [False, True])
@pytest.mark.parametrize(
    "fixture",
    [
        "string_inline",
        "string_ref",
        "integer_inline",
        "integer_ref",
        "array_inline",
        "array_ref",
        "nullable_inline",
        "nullable_ref",
        "string_external",
        "defaults_inline",
        "ordinary",
    ],
)
def test_allof_root_types(output_file: Path, fixture: str, field_constraints: bool, entry_point: str) -> None:
    """Preserve allOf value shapes and constraints without changing ordinary object and enum models."""
    input_path = JSON_SCHEMA_DATA_PATH / "allof_root_types" / f"{fixture}.json"
    expected_name = f"{fixture}_{'field' if field_constraints else 'constrained'}"
    options = {
        "input_file_type": InputFileType.JsonSchema,
        "field_constraints": field_constraints,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    if entry_point == "api":
        run_generate_and_assert(
            input_=input_path if fixture == "string_external" else json.loads(input_path.read_text(encoding="utf-8")),
            expected_file=EXPECTED_JSON_SCHEMA_PATH / "allof_root_types" / f"{expected_name}_api.py",
            assert_input_unchanged=True,
            **options,
        )
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            assert_func=assert_file_content,
            expected_file=f"allof_root_types/{expected_name}.py",
            **options,
        )
    else:
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="jsonschema",
            assert_func=assert_file_content,
            expected_file=f"allof_root_types/{expected_name}.py",
            extra_args=[
                "--disable-timestamp",
                "--formatters",
                "builtin",
                *(["--field-constraints"] if field_constraints else []),
            ],
            force_exec_validation=True,
        )

    payload = json.loads((DATA_PATH / "payloads" / "allof_root_types.json").read_text(encoding="utf-8"))[fixture]
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_root_types",
        model_name="Root",
        valid_json=json.dumps(payload["valid"]),
        invalid_json=json.dumps(payload["invalid"]),
        expected_error_type=payload["error_type"],
        expected_attribute_path=payload["attribute_path"],
        expected_attribute_value=payload.get("attribute_value", payload["valid"]),
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="allof_root_types",
        model_name="Root",
        invalid_json=json.dumps(payload["constraint_invalid"]),
        expected_error_type=payload["constraint_error_type"],
    )


@pytest.mark.parametrize("entry_point", ["api", "cli"])
def test_xmlschema_allof_root_type(output_file: Path, entry_point: str) -> None:
    """Keep a named XML simple type scalar when the root element references it."""
    input_path = XML_SCHEMA_DATA_PATH / "allof_root_type.xsd"
    if entry_point == "api":
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.XMLSchema,
            field_constraints=True,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
            assert_func=assert_file_content,
            expected_file="allof_root_types/xmlschema.py",
        )
    else:
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="xmlschema",
            assert_func=assert_file_content,
            expected_file="allof_root_types/xmlschema.py",
            extra_args=["--field-constraints", "--disable-timestamp", "--formatters", "builtin"],
            force_exec_validation=True,
        )
    payload = json.loads((DATA_PATH / "payloads" / "allof_root_types.json").read_text(encoding="utf-8"))["string_ref"]
    assert_generated_model_json_validation(
        output_file,
        module_name="xmlschema_allof_root_type",
        model_name="Root",
        valid_json=json.dumps(payload["valid"]),
        invalid_json=json.dumps(payload["invalid"]),
        expected_error_type=payload["error_type"],
        expected_attribute_path=payload["attribute_path"],
        expected_attribute_value=payload["valid"],
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="xmlschema_allof_root_type",
        model_name="Root",
        invalid_json=json.dumps(payload["constraint_invalid"]),
        expected_error_type=payload["constraint_error_type"],
    )
