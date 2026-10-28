"""End-to-end coverage for draft-07 additionalItems uniqueItems tails."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, Formatter, InputFileType, generate
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_generate_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_draft7_additional_items(output_file: Path) -> None:
    """Validate every tuple tail position governed by additionalItems."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_draft7_additional_items.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_draft7_additional_items.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / "unique_items_draft7_additional_items.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name="unique_items_draft7_additional_items",
        model_name="Draft7UniqueItemsTail",
        valid_json='["head",1,[2,3],[4,5]]',
        invalid_json='["head",1,[2,2],[4,5]]',
        expected_error_type="value_error",
        expected_attribute_path=("root",),
        expected_attribute_value=["head", 1, [2, 3], [4, 5]],
    )
    assert_generated_model_json_invalid(
        output_file,
        module_name="unique_items_draft7_additional_items",
        model_name="Draft7UniqueItemsTail",
        invalid_json='["head",1,[2,3],[4,4]]',
        expected_error_type="value_error",
    )
