"""Under --strict-nullable an array item, a tuple item or a map value of any form keeps its declared null."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import msgspec
import pytest
from pydantic import TypeAdapter, ValidationError

from tests.capabilities import needs
from tests.conftest import assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

OPTION = "--keep-null-of-nullable-items"
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
TYPED_DICT_SKIP = needs("typed-dict-validation")
PAYLOADS: tuple[dict[str, object], ...] = (
    {"rows": [[1.0], None]},
    {"rows": [["x"]]},
    {"by_name": {"a": None}},
    {"counted_rows": [None, [1.0]]},
    {"grid_maps": [{"k": "v"}, None]},
    {"nested_maps": {"a": None}},
    {"by_pattern": {"ax": None}},
    {"pair": [None, "x"]},
    {"owners": [None, {"name": "n"}]},
    {"owners": [1]},
    {"owner_map": {"a": None}},
    {"kinds": ["a"]},
    {"kinds": [None]},
)
"""The values of one property each: null items and values, and values no output takes."""


def _decode(model: type, output_model_type: str, document: object) -> str:
    """Return whether a generated model accepts a JSON document, and the first error type of one it rejects."""
    text = json.dumps(document)
    try:
        if output_model_type == "msgspec.Struct":
            msgspec.json.decode(text, type=model)
        else:
            TypeAdapter(model).validate_json(text)
    except ValidationError as error:
        return f"rejected ({error.errors()[0]['type']})"
    except msgspec.ValidationError:
        return "rejected"
    return "accepted"


def _payload_lines(output_file: Path, output_model_type: str) -> str:
    """Return the verdict of the generated model on each payload."""
    with _generated_model(output_file, "nullable_item_forms_model", "Matrix") as model:
        return "".join(f"{json.dumps(payload)}: {_decode(model, output_model_type, payload)}\n" for payload in PAYLOADS)


@pytest.mark.cli_doc(
    options=["--keep-null-of-nullable-items"],
    option_description="""Write the null that `nullable: true` declares on an array item, a tuple item or a map value.

Under `--strict-nullable` a nullable string or object keeps its `| None` as an array item, a map value or a union
member, but a nullable array, a nullable map or an `allOf` of one `$ref` does not, so `[[1.0], null]` is rejected by
`list[list[float]]` though the schema allows it (as in 0.100.0). With this option every such item keeps the null:
`list[list[float] | None]`, `dict[str, Owner | None]`. The option adds no null to an item with an `enum` or a
`const`, as `nullable: true` beside an `enum` that lists no null adds none in OpenAPI 3.0.3; an `enum` that lists null
admits it with or without the option.""",
    input_schema="openapi/nullable_item_forms.yaml",
    cli_args=["--strict-nullable", "--keep-null-of-nullable-items"],
    golden_output="main/openapi/nullable_item_forms/nullable_item_forms[pydantic_v2.BaseModel-option].py",
    comparison_output="main/openapi/nullable_item_forms/nullable_item_forms[pydantic_v2.BaseModel-default].py",
)
@pytest.mark.parametrize("option", [pytest.param((), id="default"), pytest.param((OPTION,), id="option")])
@pytest.mark.parametrize(
    "output_model_type",
    [
        pytest.param(output_model_type, marks=[TYPED_DICT_SKIP] if output_model_type == "typing.TypedDict" else [])
        for output_model_type in OUTPUT_MODEL_TYPES
    ],
)
def test_nullable_item_forms(output_file: Path, output_model_type: str, option: tuple[str, ...]) -> None:
    """Keep the null of a nullable array, map, `allOf` of one `$ref` item only with the option.

    The items of arrays, tuples and maps, the values of pattern maps take the null; an
    enum item keeps rejecting it. Without the option the nullable array, map and `$ref` items reject null.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable_item_forms.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--formatters", "builtin", "--output-model-type", output_model_type, "--strict-nullable", *option),
            "--disable-timestamp",
        ],
    )
    assert_output(_payload_lines(output_file, output_model_type), name="payloads")


@pytest.mark.parametrize(
    "single_dict",
    [
        pytest.param((), id="map-null-left-to-field"),
        pytest.param(("--use-single-dict-for-object-maps",), id="single-dict"),
    ],
)
def test_nullable_item_forms_with_single_dict_maps(output_file: Path, single_dict: tuple[str, ...]) -> None:
    """Write the null of a nullable map item once, whether or not the map takes its own null."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable_item_forms.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--strict-nullable", OPTION, *single_dict, "--disable-timestamp"],
    )


def test_nullable_item_forms_without_strict_nullable(output_file: Path) -> None:
    """Leave every item as it is without --strict-nullable, which declares no null at a nullable item."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "nullable_item_forms.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", OPTION, "--disable-timestamp"],
    )
