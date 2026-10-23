"""Warn about the msgspec enums whose values msgspec refuses: neither all str nor all int."""

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

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "msgspec_mixed_enums.json"
PAYLOADS_PATH = DATA_PATH / "payloads" / "msgspec_mixed_enums" / "msgspec_mixed_enums.json"
OPTION_SETS = {
    "default": (),
    "subclass_enum": ("--use-subclass-enum",),
    "literal_one": ("--enum-field-as-literal", "one"),
    "literal_all": ("--enum-field-as-literal", "all"),
    "nullable_enum_alias": ("--use-msgspec-nullable-enum-alias",),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_mixed_enums(output_file: Path, recwarn: pytest.WarningsRecorder, option_set: str) -> None:
    """Keep the enums msgspec cannot decode as they are, and name --enum-field-as-literal all in a warning.

    msgspec decodes an Enum only when its values are all str or all int, so the Struct holding such an enum refuses
    every payload; Literal values, which that option writes, decode, and a float value is written as float.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
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
    payloads = json.loads(PAYLOADS_PATH.read_text())
    validator = Draft202012Validator(json.loads(INPUT_PATH.read_text()))
    valid = payloads["valid"][0]
    validator.validate(valid)
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**valid, **change})
    documents = [
        ("valid", valid),
        *((f"invalid {json.dumps(change)}", {**valid, **change}) for change in payloads["invalid"]),
    ]
    with _generated_model(output_file, "msgspec_mixed_enums_model", "Model") as model:
        verdicts = "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents)
    assert_output(verdicts, name="payloads")


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a JSON document, or that msgspec refuses the model's type."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejected"
    except TypeError:
        return "type refused by msgspec"
    return "accepted"
