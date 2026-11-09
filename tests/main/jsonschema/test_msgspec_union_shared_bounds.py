"""Keep on a msgspec union the constraints every member of one type implies."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "msgspec_union_shared_bounds.json"
PAYLOADS = DATA_PATH / "payloads" / "msgspec_union_shared_bounds.json"
OPTION_SETS = {
    "default": (),
    "no_use_annotated": ("--no-use-annotated",),
    "collapse_root_models": ("--collapse-root-models",),
    "read_bounds_exactly": ("--read-msgspec-union-bounds-exactly",),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_union_shared_bounds(output_file: Path, option_set: str) -> None:
    """Write the constraints every member of a union of one type implies, also where no member writes just them.

    msgspec checks every member of one type against the constraints of all of them, so a union of differently
    constrained strings or integers was written without any (#4756, #6267) and accepted "abcd" for
    `str 2..3 | str <=3 ^a` and 0 for `int 1..5 | int >=7`. The union keeps `max_length=3` and `ge=1` now, which every
    member implies. A float member also counts the integers and the Decimal numbers msgspec decodes as a float: a
    Decimal member, which takes no Meta bound, keeps the floats unbounded (-0.5), and an integer member at least -5
    keeps -1.0. An int member beside a float or Decimal member stays without them, as before, and so does a member
    beside a Literal of its type. Every valid payload decodes under every option set; the invalid ones record whether
    the model rejects them.
    """
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=INPUT,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                *("--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp"),
                *OPTION_SETS[option_set],
            ],
        )
    assert_output(
        "".join(f"{warning.message}\n" for warning in recorded_warnings if "union" in str(warning.message)),
        name="warnings",
    )
    payloads = json.loads(PAYLOADS.read_text())
    validator = Draft202012Validator(json.loads(INPUT.read_text()))
    with _generated_model(output_file, "msgspec_union_shared_bounds_model", "Bounds") as model:
        for field_name, values in payloads["valid"].items():
            for value in values:
                validator.validate({field_name: value})
                msgspec.json.decode(json.dumps({field_name: value}), type=model)
        verdicts = []
        for field_name, values in payloads["invalid"].items():
            for value in values:
                with pytest.raises(SchemaValidationError):
                    validator.validate({field_name: value})
                verdicts.append(f"{field_name} {json.dumps(value)}: {_verdict(model, {field_name: value})}\n")
    assert_output("".join(verdicts), name="invalid")


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a JSON document."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejected"
    return "accepted"
