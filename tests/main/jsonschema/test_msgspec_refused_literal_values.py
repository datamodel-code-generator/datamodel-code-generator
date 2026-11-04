"""Write a ``Literal`` value that msgspec cannot decode as its type, with a warning that the value is not checked.

A ``Literal`` of bytes and str values, which no msgspec type holds, is refused.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pytest

from datamodel_code_generator.__main__ import Exit
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


_REFUSED_MIXED_LITERALS = {
    "x_python_type": (
        {"properties": {"data": {"type": "string", "x-python-type": "typing.Literal[b'ab', 'a']"}}},
        (),
        "`Literal[b'ab', 'a']` in the msgspec field `Model.data`",
        "`Literal['ab', 'a']`",
    ),
    "nested": (
        {
            "properties": {
                "data": {"x-python-type": "typing.Optional[typing.Literal[typing.Literal[b'\\xff', b'b'], 'a', 1]]"}
            }
        },
        (),
        "`Literal[Literal[b'\\xff', b'b'], 'a', 1]` in the msgspec field `Model.data`",
        "`Literal['\\\\xff', 'b', 'a', 1]`",
    ),
    "root": (
        {
            "$defs": {"Data": {"type": "string", "x-python-type": "typing.Literal['a', b'ab']"}},
            "properties": {"data": {"$ref": "#/$defs/Data"}},
        },
        (),
        "`Literal['a', b'ab']` in the msgspec type alias `Data`",
        "`Literal['a', 'ab']`",
    ),
    "type_overrides": (
        {"properties": {"data": {"type": "string"}}},
        ("--type-overrides", json.dumps({"Model.data": "typing.Literal[b'ab', 'a', b'a']"})),
        "`Literal[b'ab', 'a', b'a']` in the msgspec field `Model.data`",
        "`Literal['ab', 'a']`",
    ),
}


@pytest.mark.parametrize("output_model_type", ["msgspec.Struct", "pydantic_v2.BaseModel"])
@pytest.mark.parametrize("case", _REFUSED_MIXED_LITERALS)
def test_msgspec_refused_mixed_bytes_and_str_literal(
    tmp_path: Path, output_file: Path, capsys: pytest.CaptureFixture[str], case: str, output_model_type: str
) -> None:
    """Fail for a ``Literal`` that holds both bytes and str values, which no msgspec type holds (#6219).

    A msgspec ``Literal`` holds no bytes value, and ``bytes`` beside the str values, as the ``Literal['a'] | bytes``
    that was written with a warning, is a union of two str-like members, so neither built a decoder. The error names
    the ``Literal`` and the field or type alias, and gives the values as text, the bytes decoded as UTF-8, as an
    example. Another output model type writes the ``Literal`` as it did.
    """
    schema, options, where, example = _REFUSED_MIXED_LITERALS[case]
    input_path = tmp_path / "mixed_literal.json"
    input_path.write_text(json.dumps({"title": "Model", "type": "object", **schema}))
    refused = output_model_type == "msgspec.Struct"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--formatters",
            "builtin",
            "--disable-timestamp",
            *options,
        ],
        expected_exit=Exit.ERROR if refused else Exit.OK,
        capsys=capsys,
        expected_stderr_contains=(
            f"Cannot write {where}: a msgspec union holds one str-like member, so no msgspec type holds both bytes and "
            "str values. Write values of one kind in the annotation (`x-python-type` or `--type-overrides`), for "
            f"example {example} or `bytes`, or generate another `--output-model-type`."
        )
        if refused
        else None,
        output_should_not_exist=refused,
    )


def _type_verdict(type_: object) -> str:
    """Build the JSON decoder of a type, which msgspec refuses with TypeError, and say it decodes."""
    msgspec.json.Decoder(type_)
    return "decoded"


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a JSON document."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError as error:
        return f"rejected: {error}"
    return "accepted"
