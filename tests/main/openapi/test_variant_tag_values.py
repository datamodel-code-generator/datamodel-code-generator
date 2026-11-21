"""The tags of a discriminated variant whose tag property admits several values (#7089, #7083, #7084).

Up to 0.97.0 a variant whose tag property is an enum of several values, ``Cat.kind: enum [cat, kitty]``, was tagged
by its schema name, ``Literal['Cat']``, which refused every value its schema admits and accepted the name. Without a
mapping key naming it, such a variant now takes the values no other variant claims; a variant a mapping names takes
the values its keys name, and the other unclaimed values under ``--use-unmapped-variant-tag-values``. A msgspec Struct
keeps the tag its schema name or mapping key gives it, as it takes one tag. Mapping keys that name the
values of an integer enum are integers, also where ``--enum-field-as-literal all`` writes the enum as a ``Literal``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import msgspec
import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, _generated_model, _model_json_validator, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DATA_PATH = OPEN_API_DATA_PATH / "variant_tag_values"
BACKENDS = ("pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "msgspec.Struct")
NAMED = ({"kind": "cat"}, {"kind": "kitty"}, {"kind": "Cat"}, {"kind": "dog"})
"""Documents of a union whose ``Cat`` admits ``cat`` and ``kitty``, and of one that names it ``Cat``."""
NUMBERED = ({"kind": 1}, {"kind": 2}, {"kind": 3}, {"kind": "1"}, {"kind": "Cat"})
"""Documents of a union told apart by integers, and of ones that carry text."""
SHARED = ({"kind": 1, "name": "Tom"}, {"kind": 2, "bark": True}, {"kind": "1", "name": "Tom"})
"""Documents of a union whose variants share an integer enum, each of the variant its required property selects."""
RELEASED_AND_OPTION = pytest.mark.parametrize(
    "option", [pytest.param([], id="released"), pytest.param(["--use-unmapped-variant-tag-values"], id="option")]
)


def _verdict(model: object, text: str, *, is_msgspec: bool) -> str:
    """Return whether a generated union decodes a JSON document."""
    try:
        if is_msgspec:
            msgspec.json.decode(text, type=model)
        else:
            _model_json_validator(model)(text)
    except (ValidationError, msgspec.ValidationError):
        return "rejects"
    return "accepts"


def _generate_and_decode(
    input_name: str,
    output_model_type: str,
    output_file: Path,
    documents: tuple[dict[str, object], ...],
    *args: str,
) -> None:
    """Generate the models of an input, then record whether its schema and its ``Pet`` union accept each document."""
    input_path = DATA_PATH / f"{input_name}.yaml"
    is_msgspec = output_model_type == "msgspec.Struct"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--output-model-type", output_model_type, "--formatters", "builtin", "--disable-timestamp"),
            *(("--use-msgspec-tagged-unions",) if is_msgspec else ()),
            *args,
        ],
    )
    components = yaml.safe_load(input_path.read_text(encoding="utf-8"))["components"]
    validator = Draft202012Validator({"$ref": "#/components/schemas/Pet", "components": components})
    lines: list[str] = []
    with _generated_model(output_file, f"variant_tag_values_{input_name}", "Pet") as pet:
        for document in documents:
            text = json.dumps(document)
            schema_verdict = "accepts" if validator.is_valid(document) else "rejects"
            lines.append(f"Pet {text}: schema {schema_verdict}, model {_verdict(pet, text, is_msgspec=is_msgspec)}\n")
    assert_output("".join(lines), name="payloads")


_SEVERAL_VALUES = (
    ("several_values", [], "strings"),
    ("integer_values", [], "integers"),
    ("several_values", ["--enum-field-as-literal", "all"], "strings-as-literals"),
)


@pytest.mark.parametrize(
    ("output_model_type", "input_name", "args"),
    [
        pytest.param(backend, input_name, args, id=f"{backend}-{label}")
        for backend in BACKENDS
        for input_name, args, label in _SEVERAL_VALUES
        if not (backend == "msgspec.Struct" and input_name == "integer_values")
    ],
)
def test_variant_of_several_values(output_model_type: str, input_name: str, args: list[str], output_file: Path) -> None:
    """Tag a variant that no mapping key names with the values its tag property admits (#7089).

    ``Cat.kind`` admits ``cat`` and ``kitty`` (``1`` and ``3`` beside ``Dog``'s ``2``), and ``Pet`` has no mapping. It
    took the schema name, ``Literal['Cat']``, which rejected every document of ``Cat`` and accepted ``Cat``; it now
    takes both values, so each model accepts the documents of its schema and rejects the others. A msgspec Struct takes
    one tag, so it keeps the schema name.
    """
    documents = NUMBERED if input_name == "integer_values" else NAMED
    _generate_and_decode(input_name, output_model_type, output_file, documents, *args)


@pytest.mark.parametrize("output_model_type", ["dataclasses.dataclass", "msgspec.Struct"])
def test_nullable_variant_of_several_values(output_model_type: str, output_file: Path) -> None:
    """Tag a variant whose nullable tag property admits several values with those values, a null being none.

    ``Cat.kind`` is ``type: [string, 'null']`` with ``enum: [cat, kitty]``; it took ``Literal['Cat'] | None``. The
    Pydantic outputs refuse an optional tag field in a discriminated union either way, so they are not listed.
    """
    _generate_and_decode("nullable_values", output_model_type, output_file, NAMED)


@pytest.mark.cli_doc(
    options=["--use-unmapped-variant-tag-values"],
    option_description="""Give a mapped variant the unmapped values of its tag property.

A discriminated variant whose tag property admits several values, `Cat.kind: enum [cat, kitty]`, is selected by every
one of them in the schema's `oneOf`. Without this option a variant that a mapping key names takes only its mapping
keys, `Literal['cat']`, as in 0.97.0, so `{"kind": "kitty"}`, which the schema accepts, is rejected. With it the
variant keeps its mapping keys and also takes the values of its tag property that no mapping key and no other variant
claim, `Literal['cat', 'kitty']`, so the union accepts the documents of the variant that carry them. A value that
another variant also admits stays rejected, as a tag selects one variant. A variant that no mapping key names takes
those values without the option. A msgspec Struct keeps its one tag.""",
    input_schema="openapi/variant_tag_values/several_values_mapped.yaml",
    cli_args=["--use-unmapped-variant-tag-values"],
    golden_output="main/openapi/variant_tag_values/mapped_variant_of_several_values[pydantic_v2.BaseModel-option].py",
    comparison_output=(
        "main/openapi/variant_tag_values/mapped_variant_of_several_values[pydantic_v2.BaseModel-released].py"
    ),
)
@RELEASED_AND_OPTION
@pytest.mark.parametrize("output_model_type", BACKENDS)
def test_mapped_variant_of_several_values(option: list[str], output_model_type: str, output_file: Path) -> None:
    """Tag a variant a mapping names with the other values its tag property admits only under the option (#7089).

    The mapping names ``Cat`` by ``cat`` only. Without ``--use-unmapped-variant-tag-values`` it keeps
    ``Literal['cat']``, which rejects ``kitty``, as 0.97.0 does; with it ``Cat`` takes ``kitty`` too, which no key and
    no other variant claim. A msgspec Struct keeps one tag either way.
    """
    _generate_and_decode("several_values_mapped", output_model_type, output_file, NAMED, *option)


@pytest.mark.parametrize(
    ("input_name", "documents", "args"),
    [
        pytest.param("several_values_mapped_twice", (*NAMED, {"kind": "feline"}), [], id="key-outside-the-enum"),
        pytest.param("integer_values_mapped", NUMBERED, ["--use-enum-values-in-discriminator"], id="integer-enum"),
    ],
)
@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "dataclasses.dataclass"])
def test_mapped_variant_keeps_its_mapping_keys(
    output_model_type: str,
    input_name: str,
    documents: tuple[dict[str, object], ...],
    args: list[str],
    output_file: Path,
) -> None:
    """Give a variant a mapping names its mapping keys and then its unclaimed values under the option.

    The mapping names ``Cat`` by ``cat`` and by ``feline``, which its enum lacks: the option dropped ``feline``,
    which the variant took without it. Under ``--use-enum-values-in-discriminator`` the key ``"1"`` of the integer
    enum ``[1, 3]`` stays text, which matched none of its values, so the option did not add ``3``.
    """
    _generate_and_decode(
        input_name, output_model_type, output_file, documents, "--use-unmapped-variant-tag-values", *args
    )


@pytest.mark.parametrize(
    "args",
    [
        pytest.param([], id="default"),
        pytest.param(["--use-enum-values-in-discriminator"], id="enum-values"),
        pytest.param(["--enum-field-as-literal", "all"], id="literals"),
    ],
)
@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "dataclasses.dataclass"])
def test_nested_variant_of_several_values(output_model_type: str, args: list[str], output_file: Path) -> None:
    """Keep the discriminator of a union that lists the variant through a nested discriminated union.

    ``Pet`` lists ``Feline``, a root model of ``Cat | Lion``, beside ``Dog``. ``Feline`` writes ``Cat.kind`` as
    ``Literal['cat', 'kitty']``, which ``Pet`` read as no tag, so it dropped its discriminator and left ``Dog.kind``
    its Enum. Under ``--use-enum-values-in-discriminator`` it stays a plain union, as in 0.97.0.
    """
    _generate_and_decode(
        "nested_union_of_several_values",
        output_model_type,
        output_file,
        (*NAMED, {"kind": "lion"}),
        *args,
    )


def test_object_values_of_a_tag_property(output_file: Path) -> None:
    """Generate a union whose variant's tag property admits only objects, which tag no variant, as in 0.97.0."""
    _generate_and_decode(
        "object_values", "pydantic_v2.BaseModel", output_file, ({"kind": {"code": 1}}, {"kind": "dog"})
    )


def test_msgspec_lone_union_of_two_mapping_keys(output_file: Path) -> None:
    """Keep a msgspec Struct that two mapping keys name untagged, as in 0.97.0, where its union lists no other Struct.

    msgspec needs no tag for one Struct, so the union decodes both keys.
    """
    _generate_and_decode(
        "lone_union_two_mapping_keys",
        "msgspec.Struct",
        output_file,
        ({"inner": {"kind": "cat"}}, {"inner": {"kind": "kitty"}}),
        "--disable-warnings",
    )


@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "dataclasses.dataclass", "msgspec.Struct"])
def test_integer_mapping_keys_of_literal_enum(output_model_type: str, output_file: Path) -> None:
    """Read mapping keys as the integers of an enum ``--enum-field-as-literal all`` writes as a ``Literal``.

    The shared ``PetKind: enum [1, 2]`` becomes ``Literal[1, 2]``, and the keys ``"1"`` and ``"2"`` were written as the
    text ``Literal['1']``, which rejected ``{"kind": 1}`` and accepted ``{"kind": "1"}``. They now name the integers.
    """
    _generate_and_decode(
        "integer_kind_mapped", output_model_type, output_file, SHARED, "--enum-field-as-literal", "all"
    )
