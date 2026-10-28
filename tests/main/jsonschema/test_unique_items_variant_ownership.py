"""End-to-end coverage for uniqueItems validation ownership across model variants."""

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
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_validation,
    run_generate_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_variant_ownership(output_file: Path) -> None:
    """Keep uniqueItems validation on the visible request or response child field."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_variant_ownership.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_variant_ownership.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.RequestResponse,
        "use_annotated": True,
        "field_constraints": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_variant_ownership.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_request",
        model_name="ParentRequest",
        valid_json='{"child":{"requestValues":[1,2]}}',
        invalid_json='{"child":{"requestValues":[1,1]}}',
        expected_error_type="value_error",
        expected_attribute_path=("child", "requestValues"),
        expected_attribute_value=[1, 2],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_response",
        model_name="ParentResponse",
        valid_json='{"child":{"responseValues":[1,2]}}',
        invalid_json='{"child":{"responseValues":[1,1]}}',
        expected_error_type="value_error",
        expected_attribute_path=("child", "responseValues"),
        expected_attribute_value=[1, 2],
    )
