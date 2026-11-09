"""Keep the items of an array whose items name the array itself under --keep-self-referencing-array-items."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "self_referencing_array_items.json"
PAYLOADS = DATA_PATH / "payloads" / "self_referencing_array_items.json"


@pytest.mark.cli_doc(
    options=["--keep-self-referencing-array-items"],
    option_description="""Keep the items of an array whose items name the array itself.

An array schema whose items name it, such as `Node = array of (Node | Leaf)` or `Chain = array of Chain`, is written
as a bare `list` (`class Node(RootModel[list])`), which accepts any item. With this option it keeps its items
(`RootModel[list[Union["Node", Leaf]]]`), so an item the schema rejects is rejected. A type list that lists the array
beside other types (`type: [array, string]`) is written as before. msgspec output keeps the bare `list`: msgspec cannot
decode a type alias that names itself through no Struct.""",
    input_schema="jsonschema/self_referencing_array_items.json",
    cli_args=["--keep-self-referencing-array-items"],
    golden_output="main/jsonschema/self_referencing_array_items/self_referencing_array_items[pydantic_v2-kept].py",
    comparison_output=(
        "main/jsonschema/self_referencing_array_items/self_referencing_array_items[pydantic_v2-released].py"
    ),
)
@pytest.mark.parametrize(
    "option_set",
    [pytest.param((), id="released"), pytest.param(("--keep-self-referencing-array-items",), id="kept")],
)
@pytest.mark.parametrize(
    "output_model_type", ["pydantic_v2.BaseModel", "msgspec.Struct"], ids=["pydantic_v2", "msgspec"]
)
def test_self_referencing_array_items(output_file: Path, output_model_type: str, option_set: tuple[str, ...]) -> None:
    """Type the items of a self-referencing array under the option, except in msgspec output (#5534).

    Without it `Node` and `Chain` are bare lists that accept every item, as in 0.90.0. With it Pydantic writes
    `list[Union["Node", Leaf]]` and `list["Chain"]`, which reject a number item, and msgspec keeps the bare lists, as it
    cannot decode an alias that names itself.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", output_model_type, "--formatters", "builtin", "--disable-timestamp"),
            *option_set,
        ],
    )
    payloads = json.loads(PAYLOADS.read_text())
    documents = [
        *((f"valid {json.dumps(payload)}", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(payload)}", payload) for payload in payloads["invalid"]),
    ]
    with _generated_model(output_file, "self_referencing_array_items_model", "Root") as model:
        assert_output(
            "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents), name="payloads"
        )


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a document."""
    try:
        if isinstance(model, type) and issubclass(model, msgspec.Struct):
            msgspec.convert(document, model)
        else:
            model.model_validate(document)
    except (msgspec.ValidationError, pydantic.ValidationError):
        return "rejected"
    return "accepted"
