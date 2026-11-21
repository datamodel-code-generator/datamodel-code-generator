"""Nested containers whose inner level an allOf or ``uniqueItems`` rewrites.

A TypedDict whose extra values are maps, refined by an allOf whose members say that the inner values are objects and
name their model, takes those maps of the model as its ``extra_items``, in either order of the members. An array of
arrays whose inner arrays list unique items is a list of sets under ``--use-unique-items-as-set``, and a list of lists
where the items take no hashable value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


def test_allof_of_inner_maps_types_the_extra_items_by_the_named_model(output_file: Path) -> None:
    """Write ``extra_items=dict[str, Label]``, not the untyped ``dict[str, dict[str, Any]]`` of the other member."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "object_map_values_refined_by_allof.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "typing.TypedDict", "--disable-timestamp", "--formatters", "builtin"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="object_map_values_refined_by_allof",
        model_name="Catalog",
        valid_json='{"default_locale": "en", "en": {"title": {"text": "Hello", "lang": "en"}}}',
        invalid_json='{"default_locale": "en", "en": {"title": {"lang": "en"}}}',
        expected_error_type="missing",
    )


@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "dataclasses.dataclass"])
def test_inner_unique_item_arrays_are_sets(output_file: Path, output_model_type: str) -> None:
    """Write the inner arrays of unique items as sets, and as lists where their items are unhashable dataclasses."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "nested_unique_item_sets.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--use-unique-items-as-set",
            "--disable-timestamp",
            "--formatters",
            "builtin",
        ],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="nested_unique_item_sets",
        model_name="Shelter",
        valid_json='{"groups": [[{"name": "Rex"}, {"name": "Tom"}]], "tags": [["calm", "small"]]}',
        invalid_json='{"groups": [[{"species": "cat"}]]}',
        expected_error_type="missing",
    )
