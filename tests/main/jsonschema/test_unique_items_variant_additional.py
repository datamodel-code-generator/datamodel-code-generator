"""End-to-end coverage for uniqueItems on Request/Response additional properties."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator import (
    DataModelType,
    Formatter,
    InputFileType,
    ReadOnlyWriteOnlyModelType,
    generate,
)
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_generate_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_variant_additional_values(output_file: Path) -> None:
    """Keep JSON Schema declared properties outside each variant's additional rule."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_variant_additional.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_variant_additional.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.RequestResponse,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_variant_additional.py",
        **generate_kwargs,
    )
    generate(
        input_=schema,
        output=output_file,
        **generate_kwargs,
    )
    for model_name, module_name, visible_field, hidden_field in (
        ("VariantAdditionalRequest", "unique_items_variant_additional_request", "requestValues", "responseValues"),
        ("VariantAdditionalResponse", "unique_items_variant_additional_response", "responseValues", "requestValues"),
    ):
        assert_generated_model_json_validation(
            output_file,
            module_name=module_name,
            model_name=model_name,
            valid_json=f'{{"{visible_field}":[1,2],"{hidden_field}":[1,1]}}',
            invalid_json=f'{{"{visible_field}":[1,2],"extra":[1,1]}}',
            expected_error_type="value_error",
        )
