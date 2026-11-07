"""Reject the unknown fields of a msgspec Struct whose schema admits no property but its own."""

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

INPUT = JSON_SCHEMA_DATA_PATH / "msgspec_forbid_unknown_fields.json"
PAYLOADS = DATA_PATH / "payloads" / "msgspec_forbid_unknown_fields.json"
OPTION_SETS = {
    "released": (),
    "forbid": ("--use-msgspec-forbid-unknown-fields",),
    "forbid_extra_fields_ignore": ("--use-msgspec-forbid-unknown-fields", "--extra-fields", "ignore"),
}


@pytest.mark.cli_doc(
    options=["--use-msgspec-forbid-unknown-fields"],
    option_description="""Reject the unknown fields of a msgspec Struct whose schema admits no property but its own.

A schema with `additionalProperties: false` rejects every property its `properties` do not name, and Pydantic output
writes `extra='forbid'` for it, but a msgspec.Struct ignores unknown fields, so it decodes payloads the schema
rejects. With this option such a Struct is written with `forbid_unknown_fields=True`, also where an inline `allOf`
member closes it; a subclass inherits it. A schema with `patternProperties` beside `additionalProperties: false`
admits the keys its patterns match, which msgspec cannot check, so its Struct stays open. `--extra-fields allow` or
`ignore` and `--allow-extra-fields` keep every Struct open.""",
    input_schema="jsonschema/msgspec_forbid_unknown_fields.json",
    cli_args=["--output-model-type", "msgspec.Struct", "--use-msgspec-forbid-unknown-fields"],
    golden_output="main/jsonschema/msgspec_forbid_unknown_fields/msgspec_forbid_unknown_fields[forbid].py",
    comparison_output="main/jsonschema/msgspec_forbid_unknown_fields/msgspec_forbid_unknown_fields[released].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_forbid_unknown_fields(output_file: Path, option_set: str) -> None:
    """Write forbid_unknown_fields=True on a closed Struct under --use-msgspec-forbid-unknown-fields (#4741).

    Without it every Struct ignores unknown fields, as in 0.89.0. With it the closed model, the model an inline allOf
    member closes, the subclass of a closed model and the closed root reject an unknown field, as the schema does; the
    model with patternProperties beside additionalProperties: false accepts the keys its pattern admits, so it stays
    open, and --extra-fields ignore keeps every Struct open. Every valid payload decodes under every option set.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp"),
            *OPTION_SETS[option_set],
        ],
    )
    payloads = json.loads(PAYLOADS.read_text())
    validator = Draft202012Validator(json.loads(INPUT.read_text()))
    for payload in payloads["valid"]:
        validator.validate(payload)
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"][0], **change})
    documents = [
        *(("valid", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(change)}", {**payloads["valid"][0], **change}) for change in payloads["invalid"]),
    ]
    with _generated_model(output_file, "msgspec_forbid_unknown_fields_model", "Model") as model:
        assert_output(
            "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents), name="payloads"
        )


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a JSON document."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejected"
    return "accepted"
