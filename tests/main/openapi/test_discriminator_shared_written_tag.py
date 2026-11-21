"""A discriminated union whose variants hold one tag value in the tag fields that were written.

Pydantic maps each value of a variant's tag field to that variant and refuses a union where one value is mapped to two
(``Value ... mapped to multiple choices``), so the generated module failed at import. The check of a union reads the
tags of its variants before any of them is written, by its own names, and two documents wrote such a union anyway:
one whose discriminator property is named like the Enum that types it (#6865) and one that lists a nested ``oneOf``
beside the parent of the variant it groups (#6876). The union is now written without ``discriminator=`` once every
tag is written; the tag fields of the variants stay as they were, and output that imported before keeps its bytes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import OPEN_API_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DATA_PATH = OPEN_API_DATA_PATH / "discriminator_shared_written_tag"
OPTIONS = ("--formatters", "builtin", "--disable-warnings")


def test_tag_property_named_like_its_enum(output_file: Path) -> None:
    """Write no ``discriminator=`` where every variant takes every member of the Enum of its base (#6865).

    The property ``Kind`` is named like the Enum that types it, so the field of ``Base`` is ``Kind_1``. The check of
    the union then finds no Enum for the variants, which inherit the field, and L5 writes every member of ``Kind`` into
    ``C`` and into ``Z``. The union is a plain one: the module imports and decodes a document of the schema.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "tag_named_like_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--use-enum-values-in-discriminator", *OPTIONS],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="discriminator_shared_written_tag_named_like_enum",
        model_name="Holder",
        valid_json='{"item": {"Kind": "c", "one": 1}}',
        invalid_json='{"item": {"Kind": "q"}}',
        expected_error_type="literal_error",
        expected_repr="Holder(item=C(Kind_1='c', one=1))",
    )


def test_tag_property_named_like_its_enum_in_collapsed_list_items(output_file: Path) -> None:
    """Keep the output of the same union where ``--collapse-root-models`` inlines it as the items of a list.

    The root model ``Pet`` that holds the union is not written and the list names its variants without
    ``discriminator=``, so nothing refuses the members that ``C`` and ``Z`` share: the module imported before and
    keeps its bytes.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "tag_named_like_enum_list_items.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-enum-values-in-discriminator",
            "--collapse-root-models",
            *OPTIONS,
        ],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="discriminator_shared_written_tag_list_items",
        model_name="Holder",
        valid_json='{"items": [{"Kind": "c", "one": 1}, {"Kind": "z", "two": 2}]}',
        invalid_json='{"items": [{"Kind": "q"}]}',
        expected_error_type="literal_error",
        expected_repr="Holder(items=[C(Kind_1='c', one=1), Z(Kind_1='z', two=2)])",
    )


@pytest.mark.parametrize(
    ("output_model_type", "expected_repr"),
    [
        ("pydantic_v2.BaseModel", "Shelter(any=None, guest=Young(root=Kitten(kind='Cat', meow='m', tiny=None)))"),
        ("pydantic_v2.dataclass", "Shelter(any=None, guest=Kitten(kind='Cat', meow='m', tiny=None))"),
    ],
    ids=["pydantic_v2.BaseModel", "pydantic_v2.dataclass"],
)
def test_nested_variant_inherits_the_tag_of_a_listed_parent(
    output_model_type: str, expected_repr: str, output_file: Path
) -> None:
    """Write no ``discriminator=`` where a nested ``oneOf`` gives a union a variant that has its parent's tag (#6876).

    ``Shelter.any`` tags ``Animal`` and ``Cat``. ``Shelter.guest`` lists ``Young``, a ``oneOf`` of ``Kitten``, beside
    ``Cat``, the parent of ``Kitten``: the class ``Kitten`` has the tag field of ``Cat``, so both choices hold ``Cat``.
    ``guest`` is a plain union and ``any`` keeps its discriminator. ``Young`` is a root model in a BaseModel module and
    a type alias in a dataclass one: one choice for Pydantic either way.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "nested_variant_inherits_tag.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, *OPTIONS],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"discriminator_shared_written_tag_nested_{output_model_type.replace('.', '_')}",
        model_name="Shelter",
        valid_json='{"guest": {"kind": "Cat", "meow": "m"}}',
        invalid_json='{"guest": {"kind": "Dog"}}',
        expected_error_type="literal_error",
        expected_repr=expected_repr,
    )


def test_nested_variant_inherits_the_tag_of_a_listed_parent_in_collapsed_list_items(output_dir: Path) -> None:
    """Write no ``discriminator=`` onto the same union where ``--collapse-root-models`` inlines it as list items.

    ``pets.Guest`` lists ``pets.Young`` beside ``pets.Cat`` and is the items of ``api.Shelter.guests``. The module
    ``pets`` is processed first, so ``Animal`` and ``Cat`` have their tags when the list takes the union with the
    discriminator its own check gives it, and the root model ``Guest`` is not written. The union inside the list is
    asked like the union of a field: it is a plain one, and the package imports and decodes a document of the schema.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "nested_variant_inherits_tag_list_items.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--collapse-root-models", *OPTIONS],
        runtime_validation_module="api",
        runtime_validation_model_name="Shelter",
        runtime_validation_data={"guests": [{"kind": "pets.Cat", "meow": "m"}]},
    )
