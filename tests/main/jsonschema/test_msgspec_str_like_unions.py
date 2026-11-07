"""Write one str for the str-like members of a msgspec union that holds more than one of them."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema import ValidationError as SchemaValidationError

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "msgspec_str_like_unions.json"
PAYLOADS = DATA_PATH / "payloads" / "msgspec_str_like_unions.json"
OPTION_SETS = {
    "released": (),
    "merge": ("--merge-msgspec-str-like-union-members",),
    "merge_collapse_root_models": ("--merge-msgspec-str-like-union-members", "--collapse-root-models"),
}


@pytest.mark.cli_doc(
    options=["--merge-msgspec-str-like-union-members"],
    option_description="""Write one str in place of the str-like members of a msgspec union that holds more than one.

msgspec decodes a union only if it holds at most one str-like member: `str` (with the str values of a `Literal`), an
Enum of str values, `bytes`, the dates and times, `UUID` and `Decimal`. A schema whose `anyOf` or `oneOf` holds two
of them, such as a `format: uuid` string beside a plain string, writes `UUID | str`, which msgspec refuses with
`TypeError` for every document, and the generator warns about it. With this option such a union is written with one
`str` in place of those members, also through the type aliases of one str-like type, so it decodes every string they
accept, as `str`, without their formats and constraints; a warning names the members each union loses. A union whose
str-like members include an Enum, a member typed by `x-python-type` or an alias of another union is left as it is,
with its warning.""",
    input_schema="jsonschema/msgspec_str_like_unions.json",
    cli_args=[
        "--output-model-type",
        "msgspec.Struct",
        "--use-standard-primitive-types",
        "--merge-msgspec-str-like-union-members",
    ],
    golden_output="main/jsonschema/msgspec_str_like_unions/msgspec_str_like_unions[merge].py",
    comparison_output="main/jsonschema/msgspec_str_like_unions/msgspec_str_like_unions[released].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_str_like_unions(output_file: Path, option_set: str) -> None:
    """Write one ``str`` for colliding str-like union members under --merge-msgspec-str-like-union-members.

    Without it ``Model`` holds unions such as ``UUID | str``, which msgspec refuses to decode, as in 0.90.0. With it the
    unions of a property, of list items, of map values, of a ``Literal`` beside ``UUID``, of a nested ``anyOf`` and of
    a type alias decode every valid document as ``str``, while a lone ``UUID`` keeps its check; the union of ``UUID``
    and an Enum in ``Paint`` stays as it is.
    """
    with pytest.warns(UserWarning, match="msgspec") as warned:
        run_main_and_assert(
            input_path=INPUT,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                *("--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp"),
                *("--use-standard-primitive-types", "--target-python-version", "3.12"),
                *OPTION_SETS[option_set],
            ],
        )
    payloads = json.loads(PAYLOADS.read_text())
    validator = Draft202012Validator(json.loads(INPUT.read_text()), format_checker=FormatChecker())
    for payload in payloads["valid"]:
        validator.validate(payload)
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"][0], **change})
    documents = [
        *(("valid", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(change)}", {**payloads["valid"][0], **change}) for change in payloads["invalid"]),
    ]
    with _generated_model(output_file, "msgspec_str_like_unions_model", "Model") as model:
        verdicts = "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents)
    messages = sorted(str(warning.message) for warning in warned if "union" in str(warning.message))
    assert_output(verdicts + "".join(f"warning: {message}\n" for message in messages), name="payloads")


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a JSON document, or that msgspec refuses the model's types."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejected"
    except TypeError:
        return "type refused by msgspec"
    return "accepted"
