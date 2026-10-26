"""``--use-enum-values-in-discriminator`` for a variant that several discriminated unions share.

Up to 0.86.0 the first union wrote the tag field of a variant as ``Literal[<Enum>.<member>]`` and detached its
reference to the Enum, so a later union over the same variant read no tag from that field. A variant tagged by the one
member of its own Enum then took the schema name, ``Literal['Cat']``, which accepted that name and rejected the value
its schema holds. A later union now reads the value of that member as the tag, as the first union read it, and writes
it as ``Literal['cat']`` (#5099). Output that decoded the documents of its schema keeps its bytes.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, _generated_model, _model_json_validator, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DATA_PATH = OPEN_API_DATA_PATH / "shared_enum_discriminator"
OPTIONS = ("--use-enum-values-in-discriminator", "--formatters", "builtin", "--disable-warnings")
PAYLOADS: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
    ("Cat", ({"petType": "cat", "meows": 1}, {"petType": "Cat"}, {"petType": "dog"})),
    ("Dog", ({"petType": "dog", "barks": 1.5}, {"petType": "Dog"}, {"petType": "cat"})),
    ("Pet", ({"petType": "cat"}, {"petType": "dog"}, {"petType": "Cat"}, {"petType": "bird"})),
    ("Owner", ({"pet": {"petType": "cat"}}, {"pet": {"petType": "dog"}}, {"pet": {"petType": "Dog"}}, {})),
)
"""Documents per model of ``discriminator_union_mode.yaml``, some valid against the schema and some not."""


def _verdict(model: Any, text: str, *, is_msgspec: bool) -> str:
    """Return whether a generated model decodes a JSON document."""
    try:
        if is_msgspec:
            msgspec.json.decode(text, type=model)
        else:
            _model_json_validator(model)(text)
    except (ValidationError, msgspec.ValidationError):
        return "rejects"
    return "accepts"


@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "msgspec.Struct"])
def test_variant_of_two_unions(output_model_type: str, output_file: Path) -> None:
    """Tag a variant that two unions share with the value of the one member of its own Enum.

    ``Cat`` and ``Dog`` each hold a one-member Enum, and ``Pet`` and ``Owner.pet`` list both without a mapping. The
    second union wrote the schema names, ``Literal['Cat']`` and ``Literal['Dog']``, which accepted those names and
    rejected the values of the Enums. Each model now accepts the documents of its schema and rejects the others. The
    msgspec unions are tagged, so that msgspec decodes them.
    """
    input_path = OPEN_API_DATA_PATH / "discriminator_union_mode.yaml"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--use-msgspec-tagged-unions",
            *OPTIONS,
        ],
    )
    components = yaml.safe_load(input_path.read_text(encoding="utf-8"))["components"]
    is_msgspec = output_model_type == "msgspec.Struct"
    lines: list[str] = []
    with _generated_model(output_file, "shared_enum_discriminator_two_unions", "Owner") as owner:
        namespace = vars(sys.modules[owner.__module__])
        for name, documents in PAYLOADS:
            validator = Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": components})
            for document in documents:
                text = json.dumps(document)
                schema_verdict = "accepts" if validator.is_valid(document) else "rejects"
                lines.append(
                    f"{name} {text}: schema {schema_verdict}, "
                    f"model {_verdict(namespace[name], text, is_msgspec=is_msgspec)}\n"
                )
    assert_output("".join(lines), name="payloads")


def test_shared_enum_of_two_mapped_unions(output_file: Path) -> None:
    """Keep the values that the second of two mapped unions writes for the variants they share, as 0.86.0 did.

    ``Pet`` writes ``Literal[PetType.cat]`` and ``Literal[PetType.dog]``, and the nullable ``Owner.pet`` then writes the
    values ``Literal['cat']`` and ``Literal['dog']`` over them, which decode the documents of the schema. Their Enum has
    two members, so the tag a variant holds comes from the mappings either way.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "repeated.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", *OPTIONS],
    )


@pytest.mark.parametrize("order", ["mapped_first", "unmapped_first"])
def test_mapped_and_unmapped_union_in_either_order(order: str, output_file: Path) -> None:
    """Tag the variants of a mapped and an unmapped union over an inherited Enum alike in either order.

    The schema names that tag the variants in ``Unmapped`` name no member of ``Kind``, so both variants would take every
    member and that union stays a plain union. The members that ``Mapped`` writes do not make them their own tags.
    """
    run_main_and_assert(
        input_path=DATA_PATH / f"{order}.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", *OPTIONS],
    )
