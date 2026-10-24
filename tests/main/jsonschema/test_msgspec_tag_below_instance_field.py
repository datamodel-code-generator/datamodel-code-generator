"""Leave a msgspec variant untagged when a base keeps the tag field as a field, which msgspec refuses at import."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, get_type_hints

import msgspec
import pytest
from jsonschema import Draft202012Validator

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "msgspec_tag_below_instance_field.json"
PAYLOADS_PATH = DATA_PATH / "payloads" / "msgspec_tag_below_instance_field" / "msgspec_tag_below_instance_field.json"
OPTION_SETS = {
    "default": (),
    "payload_name": ("--tag-msgspec-by-payload-name",),
    "tagged_unions": ("--use-msgspec-tagged-unions",),
}


def _verdict(model: Any, document: dict[str, Any]) -> str:
    """Return whether a generated type decodes a JSON document, or that msgspec refuses the type."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejects"
    except TypeError:
        return "type refused by msgspec"
    return "accepts"


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_tag_below_instance_field(output_file: Path, recwarn: pytest.WarningsRecorder, option_set: str) -> None:
    """Keep the tag field of a variant whose base declares it, so the module imports and the variant decodes it.

    A variant that narrows the field of an ``allOf`` base by a ``const`` (a string, an integer, a member of the base
    Enum, through a base that declares nothing and below a variant that is itself one) was tagged on that field, which
    msgspec refuses with ``tag_field conflicts with an existing field``. It now opts out of the tag and its field is the
    ``Literal`` of its ``const``, so each variant accepts the documents of its schema and rejects the others. A union of
    such variants is reported as one msgspec cannot decode, as before for a union that is not tagged. A base that a tag
    makes a class variable, directly or through the tag of a base it redeclares, leaves its variants tagged, and
    their unions decode. The tag
    field of a property named ``tag-k`` is ``tag_k`` unless ``--tag-msgspec-by-payload-name`` is given, and msgspec only
    compares the name a field is written under.
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
    schema = json.loads(INPUT_PATH.read_text())
    payloads = json.loads(PAYLOADS_PATH.read_text())
    lines: list[str] = []
    with _generated_model(output_file, "msgspec_tag_below_instance_field_model", "Model") as model:
        namespace = vars(sys.modules[model.__module__])
        hints = get_type_hints(model)
        for name, documents in payloads["variants"].items():
            validator = Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]})
            lines.extend(
                f"{name} {json.dumps(document)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
                f"model {_verdict(namespace[name], document)}\n"
                for document in documents
            )
        for name, documents in payloads["unions"].items():
            validator = Draft202012Validator({"$ref": f"#/properties/{name}", **schema})
            lines.extend(
                f"{name} {json.dumps(document)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
                f"model {_verdict(hints[name], document)}\n"
                for document in documents
            )
    assert_output("".join(lines), name="payloads")
