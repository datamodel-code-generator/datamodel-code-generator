"""End-to-end coverage for uniqueItems Request/Response root aliases."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

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


@pytest.mark.parametrize("use_type_alias", ["use_type_alias", "use_root_model_type_alias"])
def test_main_jsonschema_unique_items_variant_root_alias(
    output_file: Path,
    use_type_alias: str,
) -> None:
    """Keep a root alias when only its Request/Response child owns uniqueItems."""
    schema = json.loads((JSON_SCHEMA_DATA_PATH / "unique_items_variant_root_alias.json").read_text())
    generate_kwargs = {
        "input_file_type": InputFileType.JsonSchema,
        "input_filename": "unique_items_variant_root_alias.json",
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "generate_schema_validators": True,
        "read_only_write_only_model_type": ReadOnlyWriteOnlyModelType.RequestResponse,
        use_type_alias: True,
        "disable_timestamp": True,
        "formatters": [Formatter.BUILTIN],
    }
    run_generate_and_assert(
        input_=schema,
        expected_file=EXPECTED_JSON_SCHEMA_PATH / f"unique_items_variant_root_alias_{use_type_alias}.py",
        **generate_kwargs,
    )
    generate(input_=schema, output=output_file, **generate_kwargs)
    assert_generated_model_json_validation(
        output_file,
        module_name=f"unique_items_variant_root_alias_{use_type_alias}",
        model_name="VariantRootItemRequest",
        valid_json='{"requestValues":[1,2],"extra":[1,2]}',
        invalid_json='{"requestValues":[1,2],"extra":[1,1]}',
        expected_error_type="value_error",
    )
