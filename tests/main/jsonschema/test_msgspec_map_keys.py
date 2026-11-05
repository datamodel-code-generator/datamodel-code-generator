"""Check the keys of a msgspec map whose keys propertyNames or patternProperties constrain."""

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

INPUT = JSON_SCHEMA_DATA_PATH / "msgspec_map_keys.json"
PAYLOADS = DATA_PATH / "payloads" / "msgspec_map_keys.json"
OPTION_SETS = {
    "released": (),
    "meta": ("--use-msgspec-meta-for-map-keys",),
    "meta_no_annotated": ("--use-msgspec-meta-for-map-keys", "--no-use-annotated"),
    "meta_collapse_root_models": ("--use-msgspec-meta-for-map-keys", "--collapse-root-models"),
}


@pytest.mark.cli_doc(
    options=["--use-msgspec-meta-for-map-keys"],
    option_description="""Check the keys of a msgspec map whose keys propertyNames or patternProperties constrain.

A map whose keys a `propertyNames` schema or the patterns of `patternProperties` constrain by `pattern`, `minLength`
or `maxLength` is written with `str` keys in msgspec.Struct output, so every key decodes, while Pydantic output checks
them. With this option the keys are `Annotated[str, Meta(...)]`, which msgspec checks at decode. The keys of a
`propertyNames` union stay `str`, as msgspec decodes a union of strings by one of them.""",
    input_schema="jsonschema/msgspec_map_keys.json",
    cli_args=["--output-model-type", "msgspec.Struct", "--use-msgspec-meta-for-map-keys"],
    golden_output="main/jsonschema/msgspec_map_keys/msgspec_map_keys[meta].py",
    comparison_output="main/jsonschema/msgspec_map_keys/msgspec_map_keys[released].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_map_keys(output_file: Path, option_set: str) -> None:
    """Write the constrained keys of a map as ``Annotated[str, Meta(...)]`` under --use-msgspec-meta-for-map-keys.

    Without it the keys are ``str`` and every invalid key decodes, as in 0.88.0. With it the keys of a propertyNames
    pattern, of its lengths, of patternProperties, of both, of a map inside an array and of an ``anyOf`` with one
    branch are rejected as the schema rejects them; the keys of a propertyNames union stay ``str``.
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
    with _generated_model(output_file, "msgspec_map_keys_model", "Model") as model:
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
