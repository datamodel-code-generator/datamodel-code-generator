"""Tests for Pydantic v2 DataType helpers."""

from __future__ import annotations

import pytest

from datamodel_code_generator.imports import IMPORT_ANNOTATED, IMPORT_DECIMAL, IMPORT_UNION, Import
from datamodel_code_generator.model.pydantic_v2 import BaseModel, DataModelField
from datamodel_code_generator.model.pydantic_v2.imports import IMPORT_CONDECIMAL, IMPORT_SERIALIZE_AS_ANY
from datamodel_code_generator.model.pydantic_v2.types import DataTypeManager, PydanticV2DataType
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import (
    CONSTRAINED_DECIMAL_DEFAULT_VALUE_DESCRIPTOR,
    DECIMAL_DEFAULT_VALUE_DESCRIPTOR,
    DataType,
)


class TypeHintErrorDataType(PydanticV2DataType):
    """DataType that fails if type_hint is evaluated."""

    @property
    def type_hint(self) -> str:
        """Fail when type_hint is evaluated."""
        raise AssertionError


@pytest.mark.allow_direct_assert
@pytest.mark.parametrize("preserve_union_member_order", [False, True])
def test_pydantic_v2_data_type_owns_discriminator_rendering(preserve_union_member_order: bool) -> None:
    """Pydantic v2 renders discriminated unions through its backend DataType."""

    class OverrideDataType(PydanticV2DataType):
        """Backend test type with a visible discriminator wrapper."""

        @staticmethod
        def _wrap_discriminator_type_hint(type_: str, discriminator: str) -> str:
            """Expose calls through the backend-owned rendering hook."""
            return f"Backend[{type_}|{discriminator}]"

    data_type = PydanticV2DataType(
        data_types=[PydanticV2DataType(type="str"), PydanticV2DataType(type="int")],
        discriminator="kind",
        preserve_union_member_order=preserve_union_member_order,
    )
    override_data_type = OverrideDataType(
        data_types=[OverrideDataType(type="str"), OverrideDataType(type="int")],
        discriminator="kind",
        preserve_union_member_order=preserve_union_member_order,
    )

    assert data_type.type_hint == "Annotated[Union[str, int], Field(discriminator='kind')]"
    assert data_type.base_type_hint == "Union[str, int]"
    assert list(data_type.imports) == [IMPORT_UNION, IMPORT_ANNOTATED]
    assert override_data_type.type_hint == "Backend[Union[str, int]|kind]"


@pytest.mark.allow_direct_assert
def test_pydantic_v2_data_type_owns_base_type_rendering() -> None:
    """Pydantic v2 owns constrained-type conversion used by RootModel hints."""
    data_type = PydanticV2DataType(
        data_types=[
            PydanticV2DataType(type="constr", is_func=True, kwargs={"pattern": "(?=a)"}),
            PydanticV2DataType(type="conint", is_func=True, kwargs={"gt": 0}),
        ],
        is_list=True,
    )

    assert data_type.type_hint == "List[Union[constr(pattern='(?=a)'), conint(gt=0)]]"
    assert data_type.base_type_hint == "List[Union[str, conint(gt=0)]]"


@pytest.mark.allow_direct_assert
def test_pydantic_v2_data_type_declares_rendering_boundary() -> None:
    """Backend hooks must remain declared on PydanticV2DataType rather than inherited implicitly."""
    assert "_wrap_discriminator_type_hint" in vars(PydanticV2DataType)
    assert "_CONSTRAINED_TYPE_TO_BASE" in vars(PydanticV2DataType)
    assert "_BASE_TYPE_HINT_CONTAINER_ORDER" in vars(PydanticV2DataType)


@pytest.mark.allow_direct_assert
def test_imports_skip_serialize_as_any_type_hint_when_disabled() -> None:
    """Do not render type_hint just to reject SerializeAsAny imports."""
    data_type = TypeHintErrorDataType(type="User", use_serialize_as_any=False)

    assert list(data_type.imports) == []


@pytest.mark.allow_direct_assert
def test_imports_include_serialize_as_any_when_enabled() -> None:
    """Keep SerializeAsAny import when the reference structure needs it."""
    reference = Reference(path="#/$defs/User", name="User")
    model = BaseModel(
        fields=[DataModelField(name="name", data_type=DataType(type="str"), required=True)],
        reference=reference,
    )
    reference.children.append(model)
    data_type = PydanticV2DataType(reference=reference, use_serialize_as_any=True)

    assert list(data_type.imports) == [IMPORT_SERIALIZE_AS_ANY]


@pytest.mark.allow_direct_assert
def test_imports_skip_serialize_as_any_when_type_renders_without_wrapper() -> None:
    """Do not import SerializeAsAny for reference structures rendered through type."""
    reference = Reference(path="#/$defs/User", name="User")
    model = BaseModel(
        fields=[DataModelField(name="name", data_type=DataType(type="str"), required=True)],
        reference=reference,
    )
    reference.children.append(model)
    data_type = PydanticV2DataType(type="Shared.User", reference=reference, use_serialize_as_any=True)

    assert list(data_type.imports) == []


@pytest.mark.allow_direct_assert
def test_imports_skip_serialize_as_any_without_reference() -> None:
    """Do not render type_hint or assert when no reference can be wrapped."""
    data_type = TypeHintErrorDataType(type="User", use_serialize_as_any=True)

    assert list(data_type.imports) == []


@pytest.mark.allow_direct_assert
def test_transform_kwargs_iterates_filter_when_kwargs_are_larger() -> None:
    """Keep schema-to-model kwarg mapping stable when filtered keys are fewer."""
    data_type_manager = DataTypeManager()

    assert data_type_manager.transform_kwargs(
        {
            "minimum": 1,
            "maximum": 9,
            "multipleOf": 2,
            "pattern": "ignored",
            "extra": "ignored",
        },
        ("minimum", "maximum", "multipleOf"),
    ) == {"ge": 1, "le": 9, "multiple_of": 2}


@pytest.mark.allow_direct_assert
def test_pydantic_v2_data_type_manager_declares_decimal_value_semantics() -> None:
    """Keep final Decimal and condecimal semantics in the Pydantic backend."""
    data_type_manager = DataTypeManager()

    assert (
        data_type_manager.get_default_value_descriptor(DataType(import_=IMPORT_DECIMAL))
        is DECIMAL_DEFAULT_VALUE_DESCRIPTOR
    )
    assert (
        data_type_manager.get_default_value_descriptor(DataType(import_=IMPORT_CONDECIMAL))
        is CONSTRAINED_DECIMAL_DEFAULT_VALUE_DESCRIPTOR
    )
    assert (
        data_type_manager.get_default_value_descriptor(
            DataType(import_=Import(import_="condecimal", from_="pydantic", alias="DecimalType"))
        )
        is CONSTRAINED_DECIMAL_DEFAULT_VALUE_DESCRIPTOR
    )
