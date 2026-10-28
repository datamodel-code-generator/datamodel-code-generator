"""``--use-enum-values-in-discriminator`` for variants whose tags name no member of the Enum that types their tag field.

Up to 0.86.0 such a variant was tagged with a member that does not exist, such as ``Literal[K.A]`` for the schema name
``A`` or for a mapping key the Enum lacks, so the Pydantic module failed at import, and the other outputs failed when
their annotations were evaluated. The variant is now tagged with the members its tags name, else with every member of
its Enum, which are the only values its tag field holds. A union whose variants then share a value stays a plain union.
The ``Literal`` of msgspec holds no Enum member, so a msgspec variant takes the values of those members.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, get_type_hints

import msgspec
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, _model_json_validator, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "discriminator_enum_unnamed_tags.json"
CONST_OUTSIDE_ENUM_PATH = JSON_SCHEMA_DATA_PATH / "discriminator_enum_const_outside_enum.json"
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    pytest.param(
        "typing.TypedDict",
        marks=pytest.mark.skipif(
            sys.version_info < (3, 12), reason="Pydantic validates typing.TypedDict from Python 3.12 on"
        ),
    ),
    "msgspec.Struct",
)
OPTIONS = ("--use-enum-values-in-discriminator", "--use-subclass-enum", "--formatters", "builtin", "--disable-warnings")
VARIANT_PAYLOADS: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
    ("A", ({"k": "x"}, {"k": "y"}, {"k": "z"}, {"k": "A"})),
    ("B", ({"k": "z"}, {"k": "x"}, {"k": "B"})),
    ("C", ({"k": "x"}, {"k": "y"}, {"k": "foo"})),
    ("D", ({"k": "z"}, {"k": "w"}, {"k": "bar"})),
    ("E", ({"k": "x"}, {"k": "foo"}, {"k": "w"})),
    ("F", ({"k": "z"}, {"k": "x"})),
    ("G", ({"k": "x", "g": "a"}, {"k": "w", "g": "a"}, {"k": "x"})),
    ("H", ({"k": "z", "h": 1}, {"k": "x", "h": "one"})),
    ("I", ({"k": "p"}, {"k": "q", "i": 1}, {"k": "I"}, {"k": "r"})),
    ("J", ({"k": "r"}, {"k": "p"})),
    ("N1", ({"k": 1, "n": "a"}, {"k": 3, "n": "a"}, {"k": 1})),
    ("N2", ({"k": 2, "m": 1}, {"k": 2, "m": "one"})),
    ("P2", ({"k": 1, "q": "a"}, {"k": 2, "q": "a"})),
)
UNION_PAYLOADS: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
    ("own_enums", ({"k": "x"}, {"k": "z"}, {"k": "A"}, {"k": "q"})),
    ("mapped", ({"k": "y"}, {"k": "w"}, {"k": "foo"})),
    ("mixed", ({"k": "x"}, {"k": "z"}, {"k": "foo"})),
    ("shared", ({"k": "x", "g": "a"}, {"k": "y", "h": 1}, {"k": "x"})),
    ("inherited", ({"k": "p"}, {"k": "r"}, {"k": "I"})),
    ("coerced", ({"k": 1, "n": "a"}, {"k": 2, "m": 1}, {"k": 1})),
)
"""Documents per variant class and per property of ``Model``, some valid against the schema and some not."""


def _verdict(model: Any, text: str, *, is_msgspec: bool) -> str:
    """Return whether a generated model decodes a JSON document."""
    get_type_hints(model)
    try:
        if is_msgspec:
            msgspec.json.decode(text, type=model)
        else:
            _model_json_validator(model)(text)
    except (ValidationError, msgspec.ValidationError):
        return "rejects"
    return "accepts"


def _verdicts(output_file: Path, output_model_type: str) -> str:
    """Return whether the schema and each generated model accept each document, one line per document.

    msgspec takes the values of the members in the ``Literal`` of a variant, and decodes no union of several Structs
    that are not tagged, so its unions are left out. Pydantic dataclasses do not read the Enum of a base for a tag, so
    ``I`` keeps its schema name as before and is left out with its union.
    """
    schema = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    is_msgspec = output_model_type == "msgspec.Struct"
    left_out = ("I", "Model.inherited") if output_model_type == "pydantic_v2.dataclass" else ()
    lines: list[str] = []
    with _generated_model(output_file, "discriminator_enum_unnamed_tags_model", "Model") as root:
        namespace = vars(sys.modules[root.__module__])
        targets = [
            (
                name,
                Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}),
                namespace[name],
                documents,
            )
            for name, documents in VARIANT_PAYLOADS
        ]
        if not is_msgspec:
            targets += [
                (
                    f"Model.{name}",
                    Draft202012Validator({"properties": {name: schema["properties"][name]}, "$defs": schema["$defs"]}),
                    root,
                    tuple({name: document} for document in documents),
                )
                for name, documents in UNION_PAYLOADS
            ]
        lines.extend(
            f"{name} {json.dumps(document)}: schema {'accepts' if validator.is_valid(document) else 'rejects'}, "
            f"model {_verdict(model, json.dumps(document), is_msgspec=is_msgspec)}\n"
            for name, validator, model, documents in targets
            if name not in left_out
            for document in documents
        )
    return "".join(lines)


@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_use_enum_values_in_discriminator_without_member(output_model_type: str, output_file: Path) -> None:
    """Tag a variant with the members its tags name, else with every member of its Enum, and import the module.

    ``A`` and ``B`` hold their own Enums and are tagged by their schema names, ``C`` and ``D`` by mapping keys the Enums
    lack, ``E`` by a mapping key that names a member and one that does not, ``I`` inherits the Enum of its base. ``G``
    and ``H`` share one Enum, so their tags would share every value: that union keeps no discriminator, as does the
    one of ``N1`` and ``N2``, whose mapping key ``"1"`` names the member ``1`` that the other one, tagged by no member,
    takes too. The member ``1`` of the plain Enum that ``P1`` is tagged with never equals the literal ``1`` of ``P2``,
    so that union keeps its discriminator. The module imports and each model accepts the documents of its schema and
    rejects the others, ``P1`` apart, whose Enum member Pydantic does not take for the JSON value.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, *OPTIONS],
    )
    assert_output(_verdicts(output_file, output_model_type), name="payloads")


@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "dataclasses.dataclass", "msgspec.Struct"])
def test_use_enum_values_in_discriminator_const_outside_enum(output_model_type: str, output_file: Path) -> None:
    """Keep the tag field that is the one ``Literal`` of its tag, a ``const`` outside the Enum of its base.

    Such a variant never takes members, so its union keeps the discriminator although both variants would share every
    member of the base Enum. A msgspec variant cannot be tagged on a field its base declares, so it stays untagged and
    keeps the field as the ``Literal`` of its ``const``.
    """
    run_main_and_assert(
        input_path=CONST_OUTSIDE_ENUM_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, *OPTIONS],
    )
