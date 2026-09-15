"""Execute required-group and conditional-presence rules from external fixtures."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType, Error, Formatter, InputFileType
from datamodel_code_generator.model.base import TEMPLATE_DIR
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_generate_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path

_CASES = json.loads((DATA_PATH / "payloads/schema_validators_presence_cases.json").read_text())


@pytest.mark.parametrize("case_name", _CASES)
@pytest.mark.parametrize("template", ["compiled", "builtin_jinja", "custom_directory"])
def test_schema_validators_presence(output_file: Path, tmp_path: Path, case_name: str, template: str) -> None:
    """Compare generated code and execute presence rules through each template path."""
    custom_dir = None
    if template == "builtin_jinja":
        custom_dir = TEMPLATE_DIR
    elif template == "custom_directory":
        custom_dir = tmp_path / "templates"
        custom_dir.mkdir()
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / f"{case_name}.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "schema_validators_presence" / f"{case_name}.py",
        output_model_type=DataModelType.PydanticV2BaseModel,
        generate_schema_validators=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        custom_template_dir=custom_dir,
    )
    case = _CASES[case_name]
    for valid in case["valid"]:
        assert_generated_model_json_validation(
            output_file,
            module_name=case_name,
            model_name=case["model"],
            valid_json=json.dumps(valid),
            invalid_json=json.dumps(case["invalid"][0]),
            expected_error_type=case["error"],
        )
    for invalid in case["invalid"]:
        assert_generated_model_json_invalid(
            output_file,
            module_name=case_name,
            model_name=case["model"],
            invalid_json=json.dumps(invalid),
            expected_error_type=case["error"],
        )


@pytest.mark.parametrize("case_name", ["schema_validators_presence_else", "schema_validators_not_required"])
def test_schema_validators_presence_custom_helper(output_file: Path, case_name: str) -> None:
    """Reject incompatible custom helpers before producing incorrect runtime rules."""
    run_generate_and_assert(
        input_=JSON_SCHEMA_DATA_PATH / f"{case_name}.json",
        input_file_type=InputFileType.JsonSchema,
        output=output_file,
        expected_error=Error,
        expected_error_match="Custom schema runtime validation helper overrides",
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "schema_validators_presence/custom_helper_error.txt",
        output_model_type=DataModelType.PydanticV2BaseModel,
        generate_schema_validators=True,
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        custom_template_dir=DATA_PATH / "templates/schema_validators_presence",
    )
