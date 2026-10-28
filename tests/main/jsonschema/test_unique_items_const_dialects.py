"""End-to-end coverage for draft-aware const union branches."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, Formatter, InputFileType, generate
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_validation,
    run_generate_and_assert,
)
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_const_dialects(output_file: Path) -> None:
    """Ignore ``const`` in Draft 4 but honor its scalar branch in Draft 6."""
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    for filename, model_name, valid_json, invalid_json, expected_error_type, expected_value in (
        (
            "unique_items_const_draft4.json",
            "UniqueItemsConstDraft4",
            "[1,1]",
            "not json",
            "json_invalid",
            [1, 1],
        ),
        (
            "unique_items_const_draft6.json",
            "UniqueItemsConstDraft6",
            "[1,2]",
            "[1,1]",
            "value_error",
            [1, 2],
        ),
    ):
        schema = json.loads((JSON_SCHEMA_DATA_PATH / filename).read_text())
        run_generate_and_assert(
            input_=schema,
            input_filename=filename,
            expected_file=EXPECTED_JSON_SCHEMA_PATH / filename.replace(".json", ".py"),
            **generate_kwargs,
        )
        generate(input_=schema, output=output_file, input_filename=filename, **generate_kwargs)
        assert_generated_model_json_validation(
            output_file,
            module_name=filename.removesuffix(".json"),
            model_name=model_name,
            valid_json=valid_json,
            invalid_json=invalid_json,
            expected_error_type=expected_error_type,
            expected_attribute_path=("root",),
            expected_attribute_value=expected_value,
        )
