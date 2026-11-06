"""Type a schema whose declared types leave out array by those types, whatever items or prefixItems it holds."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pydantic
import pytest
from jsonschema import Draft202012Validator

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "items_beside_other_types.json"
DOCUMENTS = (
    {"nullableString": "abc"},
    {"nullableString": None},
    {"nullableString": [1]},
    {"string": "abc"},
    {"reference": "abc"},
    {"object": {"a": 1}},
    {"object": [1]},
    {"prefixed": True},
    {"prefixed": [1]},
    {"arrayOrString": [1]},
    {"arrayOrString": "abc"},
    {"untyped": [1]},
)


@pytest.mark.parametrize(
    "output_model_type", ["pydantic_v2.BaseModel", "msgspec.Struct", "dataclasses.dataclass", "typing.TypedDict"]
)
def test_items_beside_other_types(output_file: Path, output_model_type: str) -> None:
    """Type a schema by its declared types where none is array: items and prefixItems apply only to arrays.

    A type list with array beside items keeps its list, and items without a declared type keep it an array. The
    Pydantic model accepts and rejects the documents as the schema does.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--disable-timestamp", "--output-model-type", output_model_type],
    )
    if output_model_type != "pydantic_v2.BaseModel":
        return
    validator = Draft202012Validator(json.loads(INPUT_PATH.read_text()))
    with _generated_model(output_file, "items_beside_other_types_model", "Model") as model:
        assert_output(
            "".join(
                f"{json.dumps(document)} ({'valid' if validator.is_valid(document) else 'invalid'}): "
                f"{_verdict(model, document)}\n"
                for document in DOCUMENTS
            ),
            name="payloads",
        )


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model accepts a JSON document."""
    try:
        model.model_validate_json(json.dumps(document))
    except pydantic.ValidationError:
        return "rejected"
    return "accepted"
