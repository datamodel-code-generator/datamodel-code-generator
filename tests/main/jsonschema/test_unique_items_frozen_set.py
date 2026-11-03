"""End-to-end coverage for the uniqueItems validators of a field typed as a frozen set."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


def test_main_jsonschema_unique_items_frozen_set(output_file: Path) -> None:
    """Check the uniqueItems of a frozen set's own array, as of a list's (#5818).

    ``--generate-schema-validators`` took a frozen set for a type that receives no array, so an array of duplicates
    that the schema rejects was accepted (``[[1, 2], [1, 2]]``, ``[1, 1]``): only the item arrays were checked.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "unique_items_frozen_set.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--generate-schema-validators",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
    )
    for duplicated_json in ('{"groups": [[1, 2], [1, 2]]}', '{"ids": [1, 1]}', '{"groups": [[1, 1]]}'):
        assert_generated_model_json_validation(
            output_file,
            module_name="unique_items_frozen_set",
            model_name="Holder",
            valid_json='{"groups": [[1, 2], [3]], "ids": [1, 2]}',
            invalid_json=duplicated_json,
            expected_error_type="value_error",
        )
