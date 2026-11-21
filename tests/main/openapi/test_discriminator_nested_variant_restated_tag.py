"""A discriminated union whose nested ``oneOf`` gives it a variant whose parents both restate an optional const tag.

L5 writes the tag default into the tag field of the root-most parent only, while the class of the variant takes the
field of the nearer parent, which stays optional: Pydantic refused the union at import (#6875). The union is written
without ``discriminator=``; the variants keep their tag fields, so a module that holds only them keeps its bytes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import OPEN_API_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DATA_PATH = OPEN_API_DATA_PATH / "discriminator_nested_variant_restated_tag"


@pytest.mark.parametrize(
    ("output_model_type", "expected_repr"),
    [
        ("pydantic_v2.BaseModel", "Holder(thing=Young(root=Kitten(kind='pet', meow=None, tiny=None)))"),
        ("pydantic_v2.dataclass", "Holder(thing=Kitten(kind='pet', meow=None, tiny=None))"),
    ],
    ids=["pydantic_v2.BaseModel", "pydantic_v2.dataclass"],
)
def test_nested_variant_without_its_tag_in_the_field_its_class_has(
    output_model_type: str, expected_repr: str, output_file: Path
) -> None:
    """Write ``Holder.thing`` without ``discriminator=``, as ``Kitten`` has ``Cat.kind``, which is optional (#6875).

    ``Animal`` and ``Cat`` both declare ``kind: {const: pet}`` without requiring it, and ``Young`` groups ``Kitten``,
    which inherits from ``Cat``, as a variant of ``Holder.thing``.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "restated_optional_tag.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, "--formatters", "builtin", "--disable-warnings"],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"discriminator_nested_variant_restated_tag_{output_model_type.replace('.', '_')}",
        model_name="Holder",
        valid_json='{"thing": {"kind": "pet"}}',
        invalid_json='{"thing": {"kind": "car"}}',
        expected_error_type="literal_error",
        expected_repr=expected_repr,
    )


def test_module_of_the_nested_variant_keeps_its_bytes(output_dir: Path) -> None:
    """Write ``things.Holder.thing`` without ``discriminator=`` and keep ``pets``, which imported before, as it was.

    The schemas of #6875 in two modules: ``pets`` holds the variants and ``Owner``, whose ``cat`` takes ``Cat`` with
    its optional ``kind``; only ``things``, whose union Pydantic refused, changes.
    """
    run_main_and_assert(
        input_path=DATA_PATH / "restated_optional_tag_across_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--formatters", "builtin", "--disable-warnings"],
        runtime_validation_module="things",
        runtime_validation_model_name="Holder",
        runtime_validation_data={"thing": {"kind": "pet"}},
    )
