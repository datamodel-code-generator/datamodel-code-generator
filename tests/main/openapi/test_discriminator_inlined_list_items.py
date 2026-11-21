"""A discriminated union that ``--collapse-root-models`` inlines as the items of a list.

Collapse writes the ``discriminator=`` of the root model onto the union it inlines, by the Python name the property
takes in any class, when the module of the list is processed: before the site of the root model writes the name its
variants share (a property named like its Enum is ``Kind_1`` in the variants, #6874) or drops the discriminator
(#6892). Pydantic refused such a list at import. Once every tag is written, a list whose variants do not all carry
the tag it names takes the decision of its site; one whose variants carry it keeps its bytes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.main.conftest import OPEN_API_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DATA_PATH = OPEN_API_DATA_PATH / "discriminator_inlined_list_items"
OPTIONS = ("--output-model-type", "pydantic_v2.BaseModel", "--collapse-root-models", "--formatters", "builtin")


def test_list_items_name_the_tag_field_their_variants_have(output_file: Path) -> None:
    """Name the tag field ``Kind_1`` that the variants have, as the root model ``Pet`` does (#6874).

    The property ``Kind`` is named like the Enum that types it, so its field is ``Kind_1`` in ``Cat`` and ``Dog``.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "tag_named_like_enum.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*OPTIONS, "--disable-warnings"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="discriminator_inlined_list_items_named_like_enum",
        model_name="Shelter",
        valid_json='{"pets": [{"Kind": "cat", "lives": 9}, {"Kind": "dog", "bark": true}]}',
        invalid_json='{"pets": [{"Kind": "cow"}]}',
        expected_error_type="union_tag_invalid",
        expected_repr="Shelter(pets=[Cat(Kind_1='cat', lives=9), Dog(Kind_1='dog', bark=True)])",
    )


def test_list_items_of_a_union_its_site_writes_without_discriminator(output_file: Path) -> None:
    """Write the list items without ``discriminator=`` where the root model ``Method`` is written without one (#6892).

    ``LegacyOrder`` tags ``Card`` with ``card`` first, which ``Method`` maps to ``CardV2``: the two share a tag, so
    ``Method`` is a plain union and ``CardV2.kind`` stays a string.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "remapped_variant.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*OPTIONS, "--disable-warnings"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="discriminator_inlined_list_items_remapped_variant",
        model_name="Wallet",
        valid_json='{"methods": [{"kind": "card", "number": "4242"}, {"kind": "card", "token": "t"}]}',
        invalid_json='{"methods": [{"number": "4242"}]}',
        expected_error_type="missing",
        expected_repr="Wallet(methods=[Card(kind='card', number='4242'), CardV2(kind='card', token='t')])",
    )


def test_list_items_keep_a_discriminator_their_variants_carry(output_file: Path) -> None:
    """Keep the ``discriminator=`` of the list items where every variant carries the tag, though ``Method`` drops it.

    ``CardV2`` inherits ``Base.kind``, which ``Ledger`` tagged ``base``, so Pydantic builds the list: the module
    imported before and keeps its bytes.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "remapped_variant_with_tagged_base.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*OPTIONS, "--disable-warnings"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="discriminator_inlined_list_items_tagged_base",
        model_name="Wallet",
        valid_json='{"methods": [{"kind": "card", "number": "4242"}, {"kind": "base", "token": "t"}]}',
        invalid_json='{"methods": [{"kind": "cash"}]}',
        expected_error_type="union_tag_invalid",
        expected_repr="Wallet(methods=[Card(kind='card', number='4242'), CardV2(kind='base', token='t')])",
    )


def test_list_items_copied_into_another_module(output_dir: Path) -> None:
    """Name the tag field ``Kind_1`` also in the copy of a collapsed list that another module inlines.

    ``pets.Pets`` is an array of the union ``pets.Pet``, which collapse inlines into it, and ``api.Shelter.all`` copies
    that array: the copy keeps the site of ``pets.Pet``, as the list ``api.Shelter.some`` names it directly.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "tag_named_like_enum_across_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[*OPTIONS, "--disable-warnings"],
        runtime_validation_module="api",
        runtime_validation_model_name="Shelter",
        runtime_validation_data={"all": [{"Kind": "cat", "lives": 9}], "some": [{"Kind": "dog", "bark": True}]},
    )
