"""Tests for dataclass model field generation."""

from __future__ import annotations

import pytest

from datamodel_code_generator.model.dataclass import DataClass, DataModelField
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType


def test_data_model_field_process_const() -> None:
    """Test process_const method functionality."""
    field = DataModelField(name="test_field", data_type=DataType(type="str"), required=True, extras={"const": "v1"})

    field.process_const()

    assert field.const is True
    assert field.nullable is False
    assert field.data_type.literals == ["v1"]
    assert field.default == "v1"


def test_data_model_field_process_const_no_const() -> None:
    """Test process_const when no const is in extras."""
    field = DataModelField(name="test_field", data_type=DataType(type="str"), required=True, extras={})

    original_nullable = field.nullable
    original_default = field.default
    original_const = field.const

    field.process_const()

    assert field.const == original_const
    assert field.nullable == original_nullable
    assert field.default == original_default


@pytest.mark.parametrize(
    ("extras", "strip_default_none", "expected"),
    [
        pytest.param({"repr": False}, False, "field(repr=False, default=None)", id="metadata"),
        pytest.param({"kw_only": True}, False, "field(kw_only=True, default=None)", id="keyword-only"),
        pytest.param({"repr": False}, True, "field(repr=False)", id="stripped-metadata"),
        pytest.param({"init": False}, False, "field(init=False, default=None)", id="init-disabled"),
        pytest.param({"init": False}, True, "field(init=False, default=None)", id="stripped-init-disabled"),
    ],
)
def test_data_model_field_optional_none_preserves_field_semantics(
    extras: dict[str, bool],
    strip_default_none: bool,
    expected: str,
) -> None:
    """Preserve optional defaults whenever field metadata bypasses the template default."""
    field = DataModelField(
        name="value",
        data_type=DataType(type="str"),
        default=None,
        required=False,
        strip_default_none=strip_default_none,
        extras=extras,
    )

    assert str(field) == expected


def test_data_model_field_init_false_preserves_default_factory() -> None:
    """Do not add a competing None default when init-disabled fields use a factory."""
    field = DataModelField(
        name="value",
        data_type=DataType(type="list"),
        default=None,
        required=False,
        extras={"default_factory": "list", "init": False},
    )

    assert str(field) == "field(default_factory=list, init=False)"


def test_data_model_field_init_false_prefers_computed_nested_default_factory() -> None:
    """A computed nested factory must replace the otherwise necessary None default."""
    nested_reference = Reference(path="Nested", original_name="Nested", name="Nested")
    DataClass(reference=nested_reference, fields=[])
    field = DataModelField(
        name="value",
        data_type=DataType(reference=nested_reference),
        default=None,
        required=False,
        extras={"init": False},
        use_default_factory_for_optional_nested_models=True,
    )

    assert str(field) == "field(init=False, default_factory=Nested)"


def test_data_model_field_renders_nested_mapping_default_as_constructor() -> None:
    """Build typed nested defaults while preserving renamed constructor fields."""
    nested_reference = Reference(path="Nested", original_name="Nested", name="Nested")
    DataClass(
        reference=nested_reference,
        fields=[
            DataModelField(
                name="renamed",
                original_name="external-name",
                data_type=DataType(type="str"),
            ),
        ],
    )
    field = DataModelField(
        name="nested",
        data_type=DataType(reference=nested_reference),
        default={"external-name": "preset"},
    )

    assert str(field) == "field(default_factory=lambda: Nested(renamed='preset'))"

    field.default = {"unknown": "preset"}

    assert str(field) == "field(default_factory=lambda: {'unknown': 'preset'})"

    mapping_union_field = DataModelField(
        name="mapping",
        data_type=DataType(data_types=[DataType(is_dict=True), DataType(reference=nested_reference)]),
        default={"external-name": "preset"},
    )

    assert str(mapping_union_field) == "field(default_factory=lambda: {'external-name': 'preset'})"


def test_data_model_field_nested_constructor_requires_all_signature_arguments() -> None:
    """Only build nested dataclasses whose constructor arguments are satisfied."""
    leaf_reference = Reference(path="Leaf", original_name="Leaf", name="Leaf")
    DataClass(reference=leaf_reference, fields=[])
    nested_reference = Reference(path="Nested", original_name="Nested", name="Nested")
    DataClass(
        reference=nested_reference,
        fields=[
            DataModelField(
                name="required_value",
                data_type=DataType(type="str"),
                required=True,
            ),
            DataModelField(
                name="defaulted_value",
                data_type=DataType(type="str"),
                default="fallback",
                required=True,
                has_default=True,
                use_default_with_required=True,
            ),
            DataModelField(
                name="not_initialized",
                data_type=DataType(type="str"),
                required=True,
                extras={"init": False},
            ),
            DataModelField(
                name="stripped_none",
                data_type=DataType(type="str"),
                default=None,
                strip_default_none=True,
            ),
            DataModelField(
                name="factory_value",
                data_type=DataType(type="list"),
                extras={"default_factory": "list"},
            ),
            DataModelField(
                name="child",
                data_type=DataType(reference=leaf_reference),
                default={},
            ),
        ],
    )
    field = DataModelField(
        name="nested",
        data_type=DataType(reference=nested_reference),
        default={"required_value": "preset", "stripped_none": None},
    )

    assert str(field) == "field(default_factory=lambda: Nested(required_value='preset', stripped_none=None))"

    field.default = {}
    assert str(field) == "field(default_factory=dict)"

    field.default = {
        "required_value": "preset",
        "stripped_none": None,
        "not_initialized": "invalid",
    }
    assert str(field) == (
        "field(default_factory=lambda: "
        "{'required_value': 'preset', 'stripped_none': None, 'not_initialized': 'invalid'})"
    )


def test_data_model_field_nested_constructor_uses_effective_inherited_fields() -> None:
    """Inherited required fields and child overrides share the C3 field policy."""
    base_reference = Reference(path="Base", original_name="Base", name="Base")
    DataClass(
        reference=base_reference,
        fields=[
            DataModelField(
                name="inherited",
                data_type=DataType(type="str"),
                required=True,
            ),
        ],
    )
    child_reference = Reference(path="Child", original_name="Child", name="Child")
    DataClass(reference=child_reference, fields=[], base_classes=[base_reference])
    field = DataModelField(
        name="nested",
        data_type=DataType(reference=child_reference),
        default={},
    )

    assert str(field) == "field(default_factory=dict)"

    overridden_reference = Reference(path="Overridden", original_name="Overridden", name="Overridden")
    DataClass(
        reference=overridden_reference,
        fields=[
            DataModelField(
                name="inherited",
                data_type=DataType(type="str"),
                default="fallback",
            ),
        ],
        base_classes=[base_reference],
    )
    field.data_type = DataType(reference=overridden_reference)

    assert str(field) == "field(default_factory=lambda: Overridden())"


def test_data_model_field_nested_constructor_handles_name_collisions_and_ambiguity() -> None:
    """Original names win collisions, while multiple matching models keep raw mappings."""
    first_reference = Reference(path="First", original_name="First", name="First")
    DataClass(
        reference=first_reference,
        fields=[
            DataModelField(
                name="generated",
                original_name="wire",
                data_type=DataType(type="str"),
                required=True,
            ),
            DataModelField(
                name="wire",
                original_name="other",
                data_type=DataType(type="str"),
                default="fallback",
            ),
        ],
    )
    field = DataModelField(
        name="nested",
        data_type=DataType(reference=first_reference),
        default={"wire": "preset"},
    )

    assert str(field) == "field(default_factory=lambda: First(generated='preset'))"

    field.default = {"generated": "current", "wire": "original"}

    assert str(field) == "field(default_factory=lambda: {'generated': 'current', 'wire': 'original'})"

    second_reference = Reference(path="Second", original_name="Second", name="Second")
    DataClass(
        reference=second_reference,
        fields=[
            DataModelField(
                name="wire",
                data_type=DataType(type="str"),
                required=True,
            ),
        ],
    )
    field.data_type = DataType(
        data_types=[
            DataType(reference=first_reference),
            DataType(reference=second_reference),
        ],
    )
    field.default = {"wire": "preset"}

    assert str(field) == "field(default_factory=lambda: {'wire': 'preset'})"


def test_data_model_field_nested_constructor_rejects_recursive_mapping_factories() -> None:
    """Self- and mutually recursive defaults must not generate recursive factories."""
    node_reference = Reference(path="Node", original_name="Node", name="Node")
    DataClass(
        reference=node_reference,
        fields=[
            DataModelField(
                name="metadata",
                data_type=DataType(is_dict=True),
                default={},
            ),
            DataModelField(
                name="child",
                data_type=DataType(reference=node_reference),
                default={},
            ),
        ],
    )
    field = DataModelField(
        name="node",
        data_type=DataType(reference=node_reference),
        default={},
    )

    assert str(field) == "field(default_factory=dict)"

    left_reference = Reference(path="Left", original_name="Left", name="Left")
    right_reference = Reference(path="Right", original_name="Right", name="Right")
    DataClass(
        reference=left_reference,
        fields=[
            DataModelField(
                name="right",
                data_type=DataType(reference=right_reference),
                default={},
            ),
        ],
    )
    DataClass(
        reference=right_reference,
        fields=[
            DataModelField(
                name="left",
                data_type=DataType(reference=left_reference),
                default={},
            ),
        ],
    )
    field.data_type = DataType(reference=left_reference)

    assert str(field) == "field(default_factory=dict)"


@pytest.mark.parametrize(
    ("data_type", "default", "expected"),
    [
        pytest.param(
            DataType(is_dict=True),
            {"value": "preset"},
            "field(default_factory=lambda: {'value': 'preset'})",
            id="mapping",
        ),
        pytest.param(
            DataType(data_types=[DataType(is_mapping=True), DataType(type="str")]),
            {"value": "preset"},
            "field(default_factory=lambda: {'value': 'preset'})",
            id="mapping-union",
        ),
        pytest.param(
            DataType(type="str"),
            {"unknown": "preset"},
            "field(default_factory=lambda: {'unknown': 'preset'})",
            id="non-model",
        ),
    ],
)
def test_data_model_field_preserves_plain_mapping_default(
    data_type: DataType,
    default: dict[str, str],
    expected: str,
) -> None:
    """Keep mapping factories when no nested dataclass constructor applies."""
    field = DataModelField(name="value", data_type=data_type, default=default)

    assert str(field) == expected


@pytest.mark.parametrize(
    ("const", "default", "type_"),
    [
        (True, False, "bool"),
        (3, 0, "int"),
        ("fast", "", "str"),
    ],
)
def test_data_model_field_process_const_preserves_explicit_falsy_default(
    const: bool | int | str,
    default: bool | int | str,
    type_: str,
) -> None:
    """Do not treat explicit falsy schema defaults as missing defaults."""
    field = DataModelField(
        name="test_field",
        data_type=DataType(type=type_),
        default=default,
        has_default=True,
        extras={"const": const},
    )

    field.process_const()

    assert field.const is True
    assert field.nullable is False
    assert field.data_type.literals == [const]
    assert field.default == default
