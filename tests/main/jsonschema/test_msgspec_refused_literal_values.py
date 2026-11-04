"""Write a ``Literal`` value that msgspec cannot decode as its type, with a warning that the value is not checked."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

OPTION_SETS = {
    "x_python_type": (
        "Model",
        "msgspec_refused_literal_values",
        (),
        {
            "valid": {"lone": 5.0, "data": "YWI=", "mixed": 2.5, "nested": 5.0, "listed": [5.0], "text": "a"},
            "invalid": {"lone": "x", "data": 1, "mixed": [], "nested": "b", "listed": ["x"], "text": "c"},
        },
    ),
    "type_overrides": (
        "TypeOverridesLiteralModelMembers",
        "type_overrides_literal_model_members",
        ("--type-overrides", json.dumps({"Ratio": "typing.Literal[0.5, 1.5]"})),
        {"valid": {"ratio": 0.5, "status": 200}, "invalid": {"ratio": "x", "status": 200}},
    ),
}


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_refused_literal_values(output_file: Path, recwarn: pytest.WarningsRecorder, option_set: str) -> None:
    """Write a float or bytes ``Literal`` value as float or bytes and warn, so the model builds a decoder.

    msgspec holds only None, bool, int and str values in a ``Literal``: ``Literal[5.0]`` and ``Literal[b'ab']`` raised
    ``TypeError`` when a decoder was built. A ``Literal`` of other values keeps them beside the type
    (``Literal[1, 'a'] | float``), also inside ``Optional``, a nested ``Literal`` and a list. The value is not checked:
    a float outside the ``Literal`` is accepted, as the warning says. A document of the wrong JSON type is rejected.
    """
    title, fixture, options, documents = OPTION_SETS[option_set]
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / f"{fixture}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "msgspec.Struct", *options],
    )
    warnings = dict.fromkeys(
        str(warning.message)
        for warning in recwarn
        if warning.category is UserWarning and "msgspec Literal holds only" in str(warning.message)
    )
    with _generated_model(output_file, f"{fixture}_model", title) as model:
        fields = "".join(f"{field.name}: {_type_verdict(field.type)}\n" for field in msgspec.structs.fields(model))
        verdicts = "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents.items())
    assert_output("".join(f"{message}\n" for message in warnings) + fields + verdicts)


def _type_verdict(type_: object) -> str:
    """Return whether msgspec builds a JSON decoder for a type."""
    try:
        msgspec.json.Decoder(type_)
    except TypeError:
        return "refused by msgspec"
    return "decoded"


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a JSON document."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError as error:
        return f"rejected: {error}"
    return "accepted"
