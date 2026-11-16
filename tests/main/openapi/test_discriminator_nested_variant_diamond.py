"""A discriminated union whose nested ``oneOf`` gives it a variant that inherits its tag field through a diamond.

``D`` is ``allOf [B, C]``, ``B`` and ``C`` extend ``E``, which declares the tag, and ``C`` declares it again. Pydantic
gives ``D`` the field of its first base that has one, ``B``'s, which ``B`` takes from ``E``, while ``C``'s is the
nearest in the C3 order (#6905). The union keeps ``discriminator=`` where that field carries the tag, as 0.96.0
wrote it, and is written without it where it does not, which Pydantic refused at import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import OPEN_API_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DATA_PATH = OPEN_API_DATA_PATH / "discriminator_nested_variant_diamond"


def _generate(input_name: str, output_file: Path, output_model_type: str, *args: str) -> None:
    run_main_and_assert(
        input_path=DATA_PATH / input_name,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--output-model-type", output_model_type, *args, "--formatters", "builtin", "--disable-warnings"],
    )


@pytest.mark.parametrize(
    ("output_model_type", "expected_repr"),
    [
        ("pydantic_v2.BaseModel", "Holder(thing=Group(root=D(kind='d', e=None, c=None, b=None)))"),
        ("pydantic_v2.dataclass", "Holder(thing=D(kind='d', e=None, c=None, b=None))"),
    ],
    ids=["pydantic_v2.BaseModel", "pydantic_v2.dataclass"],
)
def test_optional_tag_the_class_takes_from_its_first_base(
    output_model_type: str, expected_repr: str, output_file: Path
) -> None:
    """Write ``Holder.thing`` without ``discriminator=``, as ``D`` takes ``E.kind``, which is optional (#6905).

    ``C`` requires ``kind``, but ``D`` takes the field of ``B``, its first base, which ``B`` inherits from ``E``.
    """
    _generate("optional_tag_from_the_first_base.yaml", output_file, output_model_type)
    assert_generated_model_json_validation(
        output_file,
        module_name=f"discriminator_nested_variant_diamond_{output_model_type.replace('.', '_')}",
        model_name="Holder",
        valid_json='{"thing": {"kind": "d"}}',
        invalid_json='{"thing": {"kind": "car"}}',
        expected_error_type="literal_error",
        expected_repr=expected_repr,
    )


def test_optional_tag_the_class_takes_from_its_first_base_in_a_list(output_file: Path) -> None:
    """Write the items of ``Shelter.things`` that ``--collapse-root-models`` inlines without ``discriminator=``.

    The inlined union takes the decision of its site, the root model ``Thing``, whose nested variant ``D`` has no tag
    in the field its class takes from ``B``.
    """
    _generate(
        "optional_tag_from_the_first_base_in_a_list.yaml",
        output_file,
        "pydantic_v2.BaseModel",
        "--collapse-root-models",
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="discriminator_nested_variant_diamond_in_a_list",
        model_name="Shelter",
        valid_json='{"things": [{"kind": "d"}, {"kind": "plane"}]}',
        invalid_json='{"things": [{"kind": "car"}]}',
        expected_error_type="literal_error",
        expected_repr="Shelter(things=[Group(root=D(kind='d', e=None, c=None, b=None)), Plane(kind='plane')])",
    )


@pytest.mark.parametrize(
    ("input_name", "output_model_type", "args", "expected_repr"),
    [
        (
            "optional_tag_restated_by_the_second_base.yaml",
            "pydantic_v2.BaseModel",
            (),
            "Holder(thing=Group(root=D(kind='d', e=None, c=None, b=None)))",
        ),
        ("shallow_diamond.yaml", "pydantic_v2.BaseModel", (), "Holder(thing=Group(root=D(kind='d', c=None, b=None)))"),
        (
            "shallow_diamond.yaml",
            "pydantic_v2.dataclass",
            ("--force-optional",),
            "Holder(thing=D(kind='d', c=None, b=None))",
        ),
    ],
    ids=["restated-by-second-base", "shallow", "shallow-dataclass-force-optional"],
)
def test_tag_the_class_takes_from_its_first_base(
    input_name: str, output_model_type: str, args: tuple[str, ...], expected_repr: str, output_file: Path
) -> None:
    """Keep ``discriminator='kind'``, as ``D`` takes ``E.kind = 'd'``, though ``C`` may declare ``kind`` optional.

    ``C`` restates ``kind`` without requiring it (``--force-optional`` makes the required one of the shallow diamond
    optional), but ``D`` takes the field of ``B``, which ``B`` inherits from ``E``: a ``Literal`` with a default, which
    Pydantic reads as the tag, so a payload without ``kind`` names no choice.
    """
    _generate(input_name, output_file, output_model_type, *args)
    assert_generated_model_json_validation(
        output_file,
        module_name=f"discriminator_nested_variant_diamond_tagged_{output_model_type.replace('.', '_')}",
        model_name="Holder",
        valid_json='{"thing": {"kind": "d"}}',
        invalid_json='{"thing": {}}',
        expected_error_type="union_tag_not_found",
        expected_repr=expected_repr,
    )


def test_enum_of_all_tags_the_class_takes_from_its_first_base(output_file: Path) -> None:
    """Write ``Holder.thing`` without ``discriminator=``, as ``D`` takes ``E.kind``, which also holds ``plane``.

    ``E`` lists every tag (``enum: [d, plane]``, written as a ``Literal`` by ``--enum-field-as-literal all``) and ``C``
    narrows it to ``d``; ``D`` takes the field of ``B``, which ``B`` inherits from ``E``, so ``D`` and ``Plane`` both
    hold ``plane``, which Pydantic refused at import (``mapped to multiple choices``).
    """
    _generate(
        "enum_tag_from_the_first_base.yaml", output_file, "pydantic_v2.BaseModel", "--enum-field-as-literal", "all"
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="discriminator_nested_variant_diamond_enum",
        model_name="Holder",
        valid_json='{"thing": {"kind": "d"}}',
        invalid_json='{"thing": {"kind": "car"}}',
        expected_error_type="literal_error",
        expected_repr="Holder(thing=Group(root=D(kind='d', e=None, c=None, b=None)))",
    )
