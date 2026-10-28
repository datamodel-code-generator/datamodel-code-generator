"""End-to-end coverage for uniqueItems property input aliases."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator import AliasGenerator, DataModelType, Formatter, InputFileType, generate
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_generate_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_property_alias(output_file: Path) -> None:
    """Only validate raw names Pydantic accepts as the declared property."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_property_alias.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_property_alias.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "snake_case_field": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_property_alias.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_property_alias",
        model_name="UniqueItemsPropertyAlias",
        valid_json='{"my_list":[1,1]}',
        invalid_json='{"my-list":[1,1]}',
        expected_error_type="value_error",
    )
    serialization_kwargs = {
        **generate_kwargs,
        "use_serialization_alias": True,
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_property_alias_serialization.py",
        **serialization_kwargs,
    )
    generate(input_=schema, output=output_file, **serialization_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_property_alias_serialization",
        model_name="UniqueItemsPropertyAlias",
        valid_json='{"my-list":[1,1]}',
        invalid_json='{"my_list":[1,1]}',
        expected_error_type="value_error",
    )
    population_kwargs = {
        **generate_kwargs,
        "allow_population_by_field_name": True,
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_property_alias_population.py",
        **population_kwargs,
    )
    generate(input_=schema, output=output_file, **population_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_property_alias_population",
        model_name="UniqueItemsPropertyAlias",
        valid_json='{"my-list":[1,2],"my_list":[1,1]}',
        invalid_json='{"my-list":[1,1]}',
        expected_error_type="value_error",
    )
    serialization_generator_kwargs = {
        **generate_kwargs,
        "alias_generator": AliasGenerator.ToCamel,
        "use_serialization_alias": True,
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_property_alias_serialization_alias_generator.py",
        **serialization_generator_kwargs,
    )
    generate(input_=schema, output=output_file, **serialization_generator_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_property_alias_serialization_alias_generator",
        model_name="UniqueItemsPropertyAlias",
        valid_json='{"myList":[1,2],"my_list":[1,1]}',
        invalid_json='{"myList":[1,1]}',
        expected_error_type="value_error",
    )
