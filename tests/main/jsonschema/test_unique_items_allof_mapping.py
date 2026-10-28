"""End-to-end coverage for allOf mapping uniqueItems constraints."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, Formatter, InputFileType, generate
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_generate_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_allof_mapping(output_file: Path) -> None:
    """Apply inherited mapping uniqueItems checks on the composed child model."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_allof_mapping.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_allof_mapping.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_allof_mapping.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_allof_mapping",
        model_name="AllOfMapping",
        valid_json='{"patternValues":[1,2],"extraValues":[1,2]}',
        invalid_json='{"patternValues":[1,1]}',
        expected_error_type="value_error",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="unique_items_allof_mapping",
        model_name="AllOfMapping",
        invalid_json='{"extraValues":[1,1]}',
        expected_error_type="value_error",
    )


def test_main_jsonschema_unique_items_allof_mapping_type_alias(output_file: Path) -> None:
    """Write the validated base mapping as the RootModel it is without --use-type-alias, which a child cannot extend."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "unique_items_allof_mapping.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--generate-schema-validators",
            "--use-type-alias",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_allof_mapping_type_alias",
        model_name="AllOfMapping",
        valid_json='{"patternValues":[1,2],"extraValues":[1,2]}',
        invalid_json='{"patternValues":[1,1]}',
        expected_error_type="value_error",
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="unique_items_allof_mapping_type_alias",
        model_name="AllOfMapping",
        invalid_json='{"extraValues":[1,1]}',
        expected_error_type="value_error",
    )
