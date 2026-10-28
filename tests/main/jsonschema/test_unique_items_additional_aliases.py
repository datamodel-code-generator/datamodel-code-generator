"""End-to-end coverage for additionalProperties uniqueItems aliases."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator import AliasGenerator, DataModelType, Formatter, InputFileType, generate
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_generate_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_additional_aliases(output_file: Path) -> None:
    """Treat an unavailable field name as an additional property."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_additional_aliases.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_additional_aliases.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "snake_case_field": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_additional_aliases.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_additional_aliases",
        model_name="AdditionalAliases",
        valid_json='{"known-value":[1,2],"extra":[1,2]}',
        invalid_json='{"known-value":[1,2],"known_value":[1,1]}',
        expected_error_type="value_error",
    )
    generate(
        input_=schema,
        output=output_file,
        allow_population_by_field_name=True,
        **generate_kwargs,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_additional_aliases_population_by_name",
        model_name="AdditionalAliases",
        valid_json='{"known_value":[1,1],"extra":[1,2]}',
        invalid_json='{"known_value":[1,1],"extra":[1,1]}',
        expected_error_type="value_error",
        expected_attribute_path=("known_value",),
        expected_attribute_value=[1, 1],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_additional_aliases_population_alias_priority",
        model_name="AdditionalAliases",
        valid_json='{"known-value":[1,2]}',
        invalid_json='{"known-value":[1,2],"known_value":[1,1]}',
        expected_error_type="value_error",
    )
    serialization_kwargs = {
        **generate_kwargs,
        "alias_generator": AliasGenerator.ToCamel,
        "use_serialization_alias": True,
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_additional_aliases_serialization.py",
        **serialization_kwargs,
    )
    generate(input_=schema, output=output_file, **serialization_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_additional_aliases_serialization",
        model_name="AdditionalAliases",
        valid_json='{"knownValue":[1,1],"extra":[1,2]}',
        invalid_json='{"knownValue":[1,2],"known-value":[1,1]}',
        expected_error_type="value_error",
        expected_attribute_path=("known_value",),
        expected_attribute_value=[1, 1],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_additional_aliases_serialization_alias_priority",
        model_name="AdditionalAliases",
        valid_json='{"knownValue":[1,2]}',
        invalid_json='{"knownValue":[1,2],"known_value":[1,1]}',
        expected_error_type="value_error",
    )
