"""Leave out the msgspec Struct tags beyond the 64-bit range, which msgspec refuses when the class is defined."""

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

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "msgspec_wide_int_tags.json"
PAYLOADS_PATH = DATA_PATH / "payloads" / "msgspec_wide_int_tags" / "msgspec_wide_int_tags.json"
OPTION_SETS = {
    "default": (),
    "tagged_unions": ("--use-msgspec-tagged-unions",),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_wide_int_tags(output_file: Path, recwarn: pytest.WarningsRecorder, option_set: str) -> None:
    """Write no tag msgspec refuses, so the module imports, and keep the field that holds it.

    msgspec takes an int tag only within the signed 64-bit range. A variant of a union whose tag lies beyond it, and a
    struct whose redeclared tag field of a tagged base holds such a value, stay untagged, and the warnings name the
    value and the union msgspec cannot decode; the tags within the range stay.
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
    with _generated_model(output_file, "msgspec_wide_int_tags_model", "Model") as model:
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
