"""End-to-end coverage for uniqueItems validators alongside generic container Mapping fields."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, Formatter, InputFileType, generate
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_validation,
    run_generate_and_assert,
    run_main_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_generic_container_mapping(output_file: Path) -> None:
    """Keep a Mapping field usable when the schema also needs the aliased runtime import."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_generic_container_mapping.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_generic_container_mapping.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "use_generic_container_types": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_generic_container_mapping.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_generic_container_mapping",
        model_name="ContainerMapping",
        valid_json='{"tags":["a","b"],"labels":{"x":"y"}}',
        invalid_json='{"tags":["a","a"],"labels":{"x":"y"}}',
        expected_error_type="value_error",
    )


def test_main_jsonschema_generic_container_mapping_module_split(output_dir: Path) -> None:
    """Keep both Mapping bindings correct in each module when generation splits classes across files."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "mapping_alias_module_split.json",
        output_path=output_dir,
        input_file_type="jsonschema",
        expected_directory=EXPECTED_JSON_SCHEMA_PATH / "mapping_alias_module_split",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--generate-schema-validators",
            "--use-generic-container-types",
            "--module-split-mode",
            "single",
            "--disable-timestamp",
        ],
    )
    for module_name, model_name in (("first", "First"), ("second", "Second")):
        assert_generated_model_json_validation(
            output_dir / f"{module_name}.py",
            module_name=module_name,
            model_name=model_name,
            valid_json='{"tags":["a","b"],"labels":{"x":"y"}}',
            invalid_json='{"tags":["a","a"],"labels":{"x":"y"}}',
            expected_error_type="value_error",
        )
