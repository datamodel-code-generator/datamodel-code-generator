"""Leave out the number constraints msgspec.Meta refuses, so that the generated Structs decode."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

NUMBER_LIMITS = JSON_SCHEMA_DATA_PATH / "msgspec_number_limits"
PAYLOADS = DATA_PATH / "payloads" / "msgspec_number_limits"
OPTION_SETS = {
    "default": (),
    "site_models": ("--use-site-models-for-constrained-refs",),
    "collapse_root_models": ("--collapse-root-models",),
    "no_annotated": ("--no-use-annotated",),
    "union_bounds_exactly": ("--read-msgspec-union-bounds-exactly",),
}


@pytest.mark.cli_doc(
    options=["--read-msgspec-union-bounds-exactly"],
    option_description="""Decide the bounds of the int member of a msgspec union on the bounds the schema writes.

msgspec decodes every JSON integer as the int member of a union that has a float member too, so the int member goes
without its `Meta` bounds when they would reject an integer the float member accepts. Without this option the float
member's bound is read as the float it is written as, which past `2**53` is another integer: `{"type": "number",
"minimum": 1234567890123456700}` reads as `1234567890123456768`, so `Annotated[int, Meta(ge=1234567890123456750)]`
keeps its bound and rejects `1234567890123456720`, which the number branch accepts. With it the check reads the bounds
the schema writes, exactly (msgspec.Struct output only).""",
    input_schema="jsonschema/msgspec_number_limits/union_integer_bounds.json",
    cli_args=["--output-model-type", "msgspec.Struct", "--read-msgspec-union-bounds-exactly"],
    golden_output=(
        "main/jsonschema/msgspec_number_limits/msgspec_number_limits[union_bounds_exactly-union_integer_bounds].py"
    ),
    comparison_output=(
        "main/jsonschema/msgspec_number_limits/msgspec_number_limits[collapse_root_models-union_integer_bounds].py"
    ),
)
@pytest.mark.parametrize(
    "name", ["int64_constraints", "float_range_bounds", "exact_int_divisors", "union_integer_bounds"]
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_number_limits(output_file: Path, recwarn: pytest.WarningsRecorder, name: str, option_set: str) -> None:
    """Write the number constraints of an int within the 64-bit range only, and finite numbers only.

    msgspec refuses a ``Meta`` whose int constraint lies beyond the 64-bit range, at every decode, but decodes ints of
    any size, so the field goes without it, accepts the values beyond it and warns, also on the int that an
    ``x-python-type`` of ``Annotated`` qualifies and for a bound beyond the float range. Any other number beyond the
    float range is left out like the infinite bound it is as a float, without a warning. A divisor or an exclusive
    bound within the range that a float writes as another number (``2**62``) stays the integer the schema gives.

    The int member of a union with a number member loses its bounds when they reject an integer the number member
    accepts, as msgspec decodes every integer as the int member; ``--read-msgspec-union-bounds-exactly`` decides it on
    the bounds the schema writes, so a number bound past ``2**53`` no longer reads as the float it is written as.
    """
    input_path = NUMBER_LIMITS / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "msgspec.Struct", *OPTION_SETS[option_set]],
    )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(str(warning.message) for warning in recwarn if warning.category is UserWarning)
        ),
        name="warnings",
    )
    payloads = json.loads((PAYLOADS / f"{name}.json").read_text())
    validator = Draft202012Validator(json.loads(input_path.read_text()))
    for payload in payloads["valid"]:
        validator.validate(payload)
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"][0], **change})
    assert_output(_payload_verdicts(output_file, payloads), name="payloads")


def _payload_verdicts(output_file: Path, payloads: dict[str, Any]) -> str:
    """Return whether the generated ``Model`` decodes each valid payload and each invalid change of the first one."""
    documents = [
        *(("valid", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(change)}", {**payloads["valid"][0], **change}) for change in payloads["invalid"]),
    ]
    with _generated_model(output_file, "msgspec_number_limits_model", "Model") as model:
        return "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents)


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a JSON document."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejected"
    return "accepted"
