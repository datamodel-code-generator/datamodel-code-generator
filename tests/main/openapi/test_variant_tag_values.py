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
    """Return whether a generated union decodes a JSON document; msgspec refuses a union of untagged Structs."""
    try:
        if is_msgspec:
            msgspec.json.decode(text, type=model)
        else:
            _model_json_validator(model)(text)
    except (ValidationError, msgspec.ValidationError):
        return "rejects"
    except TypeError:
        return "cannot decode it"
    return "accepts"


def _generate_and_decode(
    input_name: str,
    output_model_type: str,
    output_file: Path,
    documents: tuple[dict[str, object], ...],
    *args: str,
    unions: tuple[str, ...] = ("Pet",),
) -> None:
    """Generate the models of an input, then record whether its schema and each of ``unions`` accept a document."""
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
    lines: list[str] = []
    for union in unions:
        validator = Draft202012Validator({"$ref": f"#/components/schemas/{union}", "components": components})
        with _generated_model(output_file, f"variant_tag_values_{input_name}", union) as model:
            for document in documents:
                text = json.dumps(document)
                schema_verdict = "accepts" if validator.is_valid(document) else "rejects"
                model_verdict = _verdict(model, text, is_msgspec=is_msgspec)
                lines.append(f"{union} {text}: schema {schema_verdict}, model {model_verdict}\n")
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
    Pydantic outputs write such a union without ``discriminator=`` (``test_tags_that_admit_null``).
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


NULLABLE_UNIONS = (
    "TypeList",
    "Mapped",
    "Optional",
    "Lone",
    "EnumNull",
    "SeveralNull",
    "ConstOptional",
    "RefOrNull",
    "Inline",
    "Nested",
)
"""The unions of ``nullable_tags``, each of variants whose tag property also admits null in another way."""
NULLABLE = ({"kind": "cat"}, {"kind": "kitty"}, {"kind": "dog"}, {"kind": "bird"}, {"kind": None}, {"kind": "Cat"})
"""Documents of the unions of ``nullable_tags``, the null and the schema name included."""


@pytest.mark.parametrize(
    "args",
    [
        pytest.param([], id="default"),
        pytest.param(["--use-enum-values-in-discriminator"], id="enum-values"),
        pytest.param(["--force-optional"], id="force-optional"),
        pytest.param(["--enum-field-as-literal", "all"], id="literals"),
        pytest.param(["--enum-field-as-literal", "one"], id="one-literal"),
    ],
)
@pytest.mark.parametrize("output_model_type", BACKENDS)
def test_tags_that_admit_null(output_model_type: str, args: list[str], output_file: Path) -> None:
    """Tag the variants of a discriminated union by the values their tag property admits, a null being none (#7102).

    The tag property admits null by its ``type`` list (``TypeList``, #7102, also with a mapping, ``Mapped``, and
    optional, ``Optional``), by a null in its enum (``EnumNull``, ``SeveralNull``, and the one variant of ``Lone``),
    beside a ``const`` (``ConstOptional``), or as a branch of an ``anyOf`` (``RefOrNull``); the variants are also
    inline allOf schemas (``Inline``, #7357) and the variants of a nested union (``Nested``), with each enum also
    written as a ``Literal`` (``--enum-field-as-literal``), whose null is no tag either. Pydantic
    reads no tag in a field that admits None, so the modules failed to import (``discriminator-needs-literal``): such a
    union is written without ``discriminator=``, and its variants keep the null their schemas admit. A null in the
    enum gave the variant its schema name, ``Literal['Cat']``, or the internal path marker of an inline variant, which
    every document of the variant failed; the variant now takes the values of its enum. msgspec warns that it
    decodes no Enum with a null member, which these inputs hold on purpose.
    """
    quiet = ("--disable-warnings",) if output_model_type == "msgspec.Struct" else ()
    _generate_and_decode(
        "nullable_tags", output_model_type, output_file, NULLABLE, *args, *quiet, unions=NULLABLE_UNIONS
    )


@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
def test_strictly_nullable_tags(output_model_type: str, output_file: Path) -> None:
    """Write a union without ``discriminator=`` where ``--strict-nullable`` lets its variants' tags be null (#7102).

    ``Cat.kind`` is ``nullable: true`` with ``enum: [cat]`` in OpenAPI 3.0, which ``--strict-nullable`` writes as
    ``Literal['cat'] | None``, so the module failed to import.
    """
    _generate_and_decode(
        "nullable_tags_strict", output_model_type, output_file, NULLABLE[:3] + NULLABLE[4:], "--strict-nullable"
    )


@pytest.mark.parametrize(
    "args",
    [pytest.param([], id="default"), pytest.param(["--use-msgspec-tagged-unions"], id="tagged-unions")],
)
def test_msgspec_inline_variant_of_several_values(args: list[str], output_file: Path) -> None:
    """Type the tag field of an inline msgspec variant by the values its base admits, not the path marker (#7357).

    ``Cat.kind`` admits ``cat`` and ``kitty``, and an allOf schema that extends ``Cat`` is a variant of ``Pet``. A
    msgspec Struct takes one tag, so such a variant took its schema path, an internal marker. It now takes both
    values in its tag field and stays untagged, as the base keeps its tag field as an instance field.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "inline_several_values.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp"),
            "--disable-warnings",
            *args,
        ],
    )


@pytest.mark.parametrize(
    ("output_model_type", "args"),
    [
        pytest.param("pydantic_v2.BaseModel", [], id="default"),
        pytest.param("pydantic_v2.BaseModel", ["--target-pydantic-version", "2.12"], id="pydantic-2.12"),
        pytest.param("pydantic_v2.BaseModel", ["--target-pydantic-version", "2.14"], id="pydantic-2.14"),
        pytest.param("pydantic_v2.BaseModel", ["--enum-field-as-literal", "all"], id="literals"),
        pytest.param("pydantic_v2.dataclass", [], id="dataclass"),
    ],
)
def test_lone_variant_of_root_model_tag(output_model_type: str, args: list[str], output_file: Path) -> None:
    """Write the union of one variant whose tag is a RootModel without ``discriminator=`` below Pydantic 2.13 (#7327).

    ``Card.type`` is a ``$ref`` to ``CardType: const: card``, a ``RootModel[Literal['card']]``, and ``PaymentMethod``
    lists ``Card`` alone. Pydantic reads a tag through a RootModel only from 2.13, so the module failed to import on
    the releases an unset target or an older one allows; a target of 2.14 keeps ``discriminator=``, and a type alias
    (``pydantic_v2.dataclass``) is a tag every release reads. One variant parses alike either way.
    """
    _generate_and_decode(
        "lone_root_model_tag",
        output_model_type,
        output_file,
        ({"type": "card", "number": "1"}, {"type": "cash"}),
        *args,
        unions=("PaymentMethod",),
    )


@pytest.mark.cli_doc(
    options=["--read-mapping-keys-by-schema-tag-type"],
    option_description="""Read discriminator mapping keys as values of the tag type the schema declares.

A mapping key is text, so the key `'2'` of a variant whose tag property is an integer enum names the integer `2`. When
an earlier discriminated union already wrote the tag field of the variant, or of the base it inherits it from, as its
own `Literal`, the key was read against that `Literal` or stayed text, `Literal['2']`, so the union rejected
`{"kind": 2}` and accepted `{"kind": "2"}`, and the output depended on the order of the unions (as in 0.100.0). With
this option the key is read against the type the schema declares, `Literal[2]`, whichever union comes first.""",
    input_schema="openapi/variant_tag_values/mapping_key_after_union.yaml",
    cli_args=["--read-mapping-keys-by-schema-tag-type"],
    golden_output=(
        "main/openapi/variant_tag_values/"
        "mapping_key_after_an_earlier_union[pydantic_v2.BaseModel-mapping_key_after_union-option].py"
    ),
    comparison_output=(
        "main/openapi/variant_tag_values/"
        "mapping_key_after_an_earlier_union[pydantic_v2.BaseModel-mapping_key_after_union-released].py"
    ),
)
@pytest.mark.parametrize(
    "option",
    [pytest.param([], id="released"), pytest.param(["--read-mapping-keys-by-schema-tag-type"], id="option")],
)
@pytest.mark.parametrize("input_name", ["mapping_key_after_union", "mapping_key_before_union"])
@pytest.mark.parametrize("output_model_type", BACKENDS)
def test_mapping_key_after_an_earlier_union(
    output_model_type: str, input_name: str, option: list[str], output_file: Path
) -> None:
    """Read a mapping key as a value of the tag type the schema declares, whichever union writes the tag (#7113).

    ``First`` maps ``'1'`` to ``Base``, whose ``kind`` is an integer enum ``[1, 2]``, and writes ``Base.kind`` as
    ``Literal[1]``; ``Second`` maps ``'2'`` to ``Child``, an allOf subclass of ``Base``. Without the option the key
    ``'2'`` stays text where ``First`` comes first, ``Literal['2']``, which rejects ``{"kind": 2}``, and names the
    integer where ``Second`` does. With ``--read-mapping-keys-by-schema-tag-type`` it names ``2`` in both orders.
    msgspec warns where the text tag sits beside the integer one, which it cannot decode.
    """
    _generate_and_decode(
        input_name,
        output_model_type,
        output_file,
        ({"kind": 2, "name": "x"}, {"kind": "2"}, {"kind": 5}, {"kind": 1}),
        *option,
        *(("--disable-warnings",) if output_model_type == "msgspec.Struct" else ()),
        unions=("Second",),
    )


@pytest.mark.parametrize(
    "option",
    [pytest.param([], id="released"), pytest.param(["--read-mapping-keys-by-schema-tag-type"], id="option")],
)
@pytest.mark.parametrize("input_name", ["enum_key_after_union", "enum_key_before_union"])
@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "dataclasses.dataclass"])
def test_enum_mapping_key_after_an_earlier_union(
    output_model_type: str, input_name: str, option: list[str], output_file: Path
) -> None:
    """Write the tag of an Enum-typed subclass alike whichever union writes its base's tag (#7113).

    ``Base.kind`` is the string Enum ``Kind``; ``First`` maps ``a`` to ``Base``, ``Second`` maps ``b`` to ``Child``, an
    allOf subclass of ``Base``. Under ``--use-enum-values-in-discriminator`` ``Child.kind`` was ``Literal['b']`` where
    ``First`` comes first, as ``Base.kind`` holds no Enum any more, and ``Literal[Kind.b, 'b']`` where ``Second`` does.
    With ``--read-mapping-keys-by-schema-tag-type`` it is the Enum's in both orders.
    """
    _generate_and_decode(
        input_name,
        output_model_type,
        output_file,
        ({"kind": "b", "name": "x"}, {"kind": "a"}, {"kind": "c"}),
        "--use-enum-values-in-discriminator",
        *option,
        unions=("Second",),
    )


@pytest.mark.parametrize(
    "option",
    [pytest.param([], id="released"), pytest.param(["--read-mapping-keys-by-schema-tag-type"], id="option")],
)
@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "msgspec.Struct"])
def test_mapping_key_of_a_referenced_tag_type(output_model_type: str, option: list[str], output_file: Path) -> None:
    """Read a mapping key as an integer where the tag property names an integer schema (#7113).

    ``Base.kind`` names ``Kind``, an integer, which the output writes as a root model or a type alias; ``Card`` and
    ``Cash`` inherit it. Without the option the keys ``'1'`` and ``'2'`` stay text, which rejects ``{"kind": 1}``;
    with ``--read-mapping-keys-by-schema-tag-type`` they name the integers in both unions, also in ``Refund``, which
    reads ``Card`` and ``Cash`` after ``Payment`` wrote their tags. ``Voucher`` declares no ``kind``, so its key stays
    text either way.
    """
    _generate_and_decode(
        "mapping_key_of_referenced_type",
        output_model_type,
        output_file,
        (
            {"kind": 1, "number": "4"},
            {"kind": "1", "number": "4"},
            {"kind": 2, "amount": 5},
            {"kind": "3", "code": "x"},
        ),
        *option,
        *(("--disable-warnings",) if output_model_type == "msgspec.Struct" else ()),
        unions=("Payment", "Refund"),
    )
