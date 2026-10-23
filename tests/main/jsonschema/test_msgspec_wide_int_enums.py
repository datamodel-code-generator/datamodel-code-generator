"""Warn about the msgspec enums and Literal values holding an int beyond the 64-bit range, which msgspec refuses."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, get_type_hints

import msgspec
import pytest
from jsonschema import Draft202012Validator

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "msgspec_wide_int_enums.json"
PAYLOADS_PATH = DATA_PATH / "payloads" / "msgspec_wide_int_enums" / "msgspec_wide_int_enums.json"
OPTION_SETS = {
    "default": (),
    "subclass_enum": ("--use-subclass-enum",),
    "literal_one": ("--enum-field-as-literal", "one"),
    "literal_all": ("--enum-field-as-literal", "all"),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_wide_int_enums(output_file: Path, recwarn: pytest.WarningsRecorder, option_set: str) -> None:
    """Keep the enums and Literal values msgspec cannot decode as they are, and name each in a warning.

    msgspec decodes an Enum or a Literal int only within the signed 64-bit range, so the type holding a value beyond
    it refuses every payload, written as an enum or as Literal values alike; the types within the range decode.
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
    valid = payloads["valid"]
    documents = [
        (name, value, {**valid, name: value}) for name, value in (*valid.items(), *payloads["invalid"].items())
    ]
    with _generated_model(output_file, "msgspec_wide_int_enums_model", "Model") as model:
        hints = get_type_hints(model)
        verdicts = "".join(
            f"{name}={json.dumps(value)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
            f"model {_verdict(hints[name], value)}\n"
            for name, value, document in documents
        )
    assert_output(verdicts, name="payloads")


def _verdict(field_type: Any, value: object) -> str:
    """Return whether a field type decodes a JSON value, or that msgspec refuses the type."""
    try:
        msgspec.json.decode(json.dumps(value), type=field_type)
    except msgspec.ValidationError:
        return "rejects"
    except (NotImplementedError, TypeError):
        return "type refused by msgspec"
    return "accepts"
