"""Tests for output model construction capabilities."""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, cast

import pytest

from datamodel_code_generator import DataModelType, DefaultValueType, PythonVersion
from datamodel_code_generator.imports import IMPORT_DECIMAL
from datamodel_code_generator.model import base as model_base_module
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.model.pydantic_v2.imports import IMPORT_CONDECIMAL
from datamodel_code_generator.model.types import DataTypeManager as _CommonDataTypeManager
from datamodel_code_generator.parser._passes._shared import _resolve_default_scalar_data_type
from datamodel_code_generator.parser.base import render_prep
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.python_literal import PythonRuntimeExpression
from datamodel_code_generator.reference import ModelType, Reference
from datamodel_code_generator.types import DataType

if TYPE_CHECKING:
    from datamodel_code_generator.model import DataModelSet

_CONSTRUCTION_CAPABILITIES: dict[DataModelType, tuple[ModelType | None, bool]] = {
    DataModelType.PydanticV2BaseModel: (ModelType.PYDANTIC, False),
    DataModelType.PydanticV2Dataclass: (None, True),
    DataModelType.DataclassesDataclass: (None, True),
    DataModelType.TypingTypedDict: (None, False),
    DataModelType.MsgspecStruct: (ModelType.MSGSPEC, False),
}


class _CustomDataModel(DataModel):
    """Custom model without an output-specific construction contract."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "TypedDict.jinja2"


class _NoDeserializedDefaultsDataModel(_CustomDataModel):
    """Custom renderer that deliberately keeps schema defaults serialized."""

    SUPPORTS_DESERIALIZED_DEFAULT_VALUES: ClassVar[bool] = False


class _UnmarkedDefaultValueDataTypeManager(_CommonDataTypeManager):
    """Custom output manager that deliberately does not expose runtime value semantics."""

    DEFAULT_VALUE_DESCRIPTORS = MappingProxyType({})


def _parser(
    model_types: DataModelSet,
    *,
    data_model_type: type[DataModel] | None = None,
    data_type_manager_type: type[_CommonDataTypeManager] | None = None,
) -> JsonSchemaParser:
    """Create a parser configured with the supplied output model set."""
    return JsonSchemaParser(
        "",
        data_model_type=data_model_type or model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=data_type_manager_type or model_types.data_type_manager,
    )


@pytest.mark.allow_direct_assert
def test_construction_capability_matrix_covers_every_data_model_type() -> None:
    """Require each canonical output target to keep an intentional contract."""
    assert set(_CONSTRUCTION_CAPABILITIES) == set(DataModelType)


@pytest.mark.allow_direct_assert
def test_generic_model_keeps_reference_import_boundary() -> None:
    """Prevent field-name policy from adding a generic-to-reference import edge."""
    reference_imports = {
        alias.name
        for node in ast.walk(ast.parse(inspect.getsource(model_base_module)))
        if isinstance(node, ast.ImportFrom) and node.module == "datamodel_code_generator.reference"
        for alias in node.names
    }

    assert reference_imports == {"Reference", "_BaseModel"}
    assert "ModelType" not in vars(model_base_module)


@pytest.mark.parametrize(
    ("output_model_type", "expected"),
    [
        pytest.param(output_model_type, capabilities, id=output_model_type.name)
        for output_model_type, capabilities in _CONSTRUCTION_CAPABILITIES.items()
    ],
)
@pytest.mark.allow_direct_assert
def test_standard_model_construction_capabilities(
    output_model_type: DataModelType,
    expected: tuple[ModelType | None, bool],
) -> None:
    """Resolve field-name and dataclass construction behavior without backend inspection."""
    model_types = get_data_model_types(output_model_type, target_python_version=PythonVersion.PY_311)
    declared_model_type, uses_dataclass_arguments = expected
    parser = _parser(model_types)

    assert model_types.data_model.FIELD_NAME_MODEL_TYPE is declared_model_type
    assert model_types.data_model.USES_DATACLASS_ARGUMENTS is uses_dataclass_arguments
    assert parser.field_name_model_type is (declared_model_type or ModelType.CLASS)


@pytest.mark.parametrize(
    ("output_model_type", "expected"),
    [
        pytest.param(output_model_type, capabilities, id=output_model_type.name)
        for output_model_type, capabilities in _CONSTRUCTION_CAPABILITIES.items()
    ],
)
@pytest.mark.allow_direct_assert
def test_external_model_subclasses_inherit_construction_capabilities(
    output_model_type: DataModelType,
    expected: tuple[ModelType | None, bool],
) -> None:
    """Preserve MRO-based behavior for external subclasses of built-in outputs."""
    model_types = get_data_model_types(output_model_type, target_python_version=PythonVersion.PY_311)
    external_model_type = cast("type[DataModel]", type("ExternalModel", (model_types.data_model,), {}))
    declared_model_type, uses_dataclass_arguments = expected
    parser = _parser(model_types, data_model_type=external_model_type)
    parser.dataclass_arguments = {"slots": True}

    result = parser._create_data_model(
        reference=Reference(path="ExternalModel", name="ExternalModel"),
        fields=[],
        dataclass_arguments={"order": True},
    )

    assert external_model_type.FIELD_NAME_MODEL_TYPE is declared_model_type
    assert external_model_type.USES_DATACLASS_ARGUMENTS is uses_dataclass_arguments
    assert parser.field_name_model_type is (declared_model_type or ModelType.CLASS)
    assert type(result) is external_model_type
    assert result.dataclass_arguments == ({"order": True} if uses_dataclass_arguments else {})


@pytest.mark.allow_direct_assert
def test_custom_model_construction_capabilities_fail_closed() -> None:
    """Keep arbitrary custom models on ordinary class and constructor behavior."""
    model_types = get_data_model_types(DataModelType.TypingTypedDict, target_python_version=PythonVersion.PY_311)
    parser = _parser(model_types, data_model_type=_CustomDataModel)

    result = parser._create_data_model(
        reference=Reference(path="CustomModel", name="CustomModel"),
        fields=[],
        dataclass_arguments={"frozen": True},
    )

    assert _CustomDataModel.FIELD_NAME_MODEL_TYPE is None
    assert parser.field_name_model_type is ModelType.CLASS
    assert result.dataclass_arguments == {}


@pytest.mark.allow_direct_assert
def test_custom_renderer_can_disable_deserialized_defaults() -> None:
    """Honor a custom output model's default-expression capability without backend checks."""
    model_types = get_data_model_types(DataModelType.PydanticV2BaseModel, target_python_version=PythonVersion.PY_311)
    parser = _parser(model_types, data_model_type=_NoDeserializedDefaultsDataModel)
    parser.deserialize_default_value_types = frozenset({DefaultValueType.Decimal})
    field = model_types.field_model(
        name="amount",
        data_type=DataType(type="Decimal", import_=IMPORT_DECIMAL),
        default="1.23",
        required=False,
    )
    model = _NoDeserializedDefaultsDataModel(
        reference=Reference(path="CustomModel", name="CustomModel"),
        fields=[field],
    )

    render_prep.deserialize_default_value(parser, model, field)

    assert DataModel.SUPPORTS_DESERIALIZED_DEFAULT_VALUES
    assert not model.SUPPORTS_DESERIALIZED_DEFAULT_VALUES
    assert field.default == "1.23"
    assert not field.runtime_expression_imports


@pytest.mark.allow_direct_assert
def test_custom_data_type_manager_fails_closed_without_value_semantics() -> None:
    """Require a custom backend to opt into generated runtime default expressions."""
    model_types = get_data_model_types(DataModelType.PydanticV2BaseModel, target_python_version=PythonVersion.PY_311)
    parser = _parser(
        model_types,
        data_model_type=_CustomDataModel,
        data_type_manager_type=_UnmarkedDefaultValueDataTypeManager,
    )
    parser.deserialize_default_value_types = frozenset({DefaultValueType.Decimal})
    field = model_types.field_model(
        name="amount",
        data_type=DataType(type="Decimal", import_=IMPORT_DECIMAL),
        default="1.23",
        required=False,
    )
    model = _CustomDataModel(
        reference=Reference(path="CustomModel", name="CustomModel"),
        fields=[field],
    )

    assert not render_prep.deserialize_default_value(parser, model, field)
    assert field.default == "1.23"
    assert not field.runtime_expression_imports


@pytest.mark.allow_direct_assert
def test_default_value_scalar_resolver_rejects_containers_and_unions() -> None:
    """Only a direct scalar leaf can ask a backend for default-value semantics."""
    scalar = DataType(type="Decimal", import_=IMPORT_DECIMAL)

    assert _resolve_default_scalar_data_type(scalar) is scalar
    assert _resolve_default_scalar_data_type(DataType(import_=IMPORT_DECIMAL, is_list=True)) is None
    assert (
        _resolve_default_scalar_data_type(DataType(data_types=[DataType(import_=IMPORT_DECIMAL), DataType(type="str")]))
        is None
    )


@pytest.mark.allow_direct_assert
def test_decimal_constraint_imports_stay_unmodified_without_a_deserialized_default() -> None:
    """Do not opt into runtime expressions for condecimal constraints alone."""
    model_types = get_data_model_types(DataModelType.PydanticV2BaseModel, target_python_version=PythonVersion.PY_311)
    parser = _parser(model_types)
    parser.deserialize_default_value_types = frozenset({DefaultValueType.Decimal})
    data_type = DataType(type="condecimal", import_=IMPORT_CONDECIMAL, kwargs={"ge": Decimal(0)})
    field = model_types.field_model(name="amount", data_type=data_type, default=None, required=False)
    model = model_types.data_model(
        reference=Reference(path="DecimalConstraint", name="DecimalConstraint"),
        fields=[field],
    )

    render_prep.set_validate_default_on_fields(parser, [model])

    assert data_type.kwargs == {"ge": Decimal(0)}
    assert not data_type.runtime_expression_imports
    assert not parser._has_runtime_expressions


@pytest.mark.allow_direct_assert
def test_decimal_constraint_non_runtime_values_stay_unmodified_after_deserialization() -> None:
    """Only native Decimal kwargs become runtime expressions after a successful conversion."""
    model_types = get_data_model_types(DataModelType.PydanticV2BaseModel, target_python_version=PythonVersion.PY_311)
    parser = _parser(model_types)
    parser.deserialize_default_value_types = frozenset({DefaultValueType.Decimal})
    decimal_field = model_types.field_model(
        name="amount",
        data_type=DataType(type="Decimal", import_=IMPORT_DECIMAL),
        default="1.23",
        required=False,
    )
    constraint_type = DataType(type="condecimal", import_=IMPORT_CONDECIMAL, kwargs={"strict": True})
    constraint_field = model_types.field_model(
        name="strict_amount", data_type=constraint_type, default=None, required=False
    )
    model = model_types.data_model(
        reference=Reference(path="DecimalConstraint", name="DecimalConstraint"),
        fields=[decimal_field, constraint_field],
    )

    render_prep.set_validate_default_on_fields(parser, [model])

    assert isinstance(decimal_field.default, PythonRuntimeExpression)
    assert constraint_type.kwargs == {"strict": True}
    assert not constraint_type.runtime_expression_imports


@pytest.mark.allow_direct_assert
def test_decimal_constraint_import_override_stays_unmodified_after_deserialization() -> None:
    """Skip condecimal normalization when its emitted import is overridden."""
    model_types = get_data_model_types(DataModelType.PydanticV2BaseModel, target_python_version=PythonVersion.PY_311)
    parser = _parser(model_types)
    parser.deserialize_default_value_types = frozenset({DefaultValueType.Decimal})
    parser._import_overrides = {"condecimal": "custom.module"}
    constraint_type = DataType(type="condecimal", import_=IMPORT_CONDECIMAL, kwargs={"ge": Decimal(0)})
    constraint_field = model_types.field_model(
        name="bounded_amount", data_type=constraint_type, default=None, required=False
    )
    decimal_field = model_types.field_model(
        name="amount",
        data_type=DataType(type="Decimal", import_=IMPORT_DECIMAL),
        default="1.23",
        required=False,
    )
    model = model_types.data_model(
        reference=Reference(path="DecimalConstraint", name="DecimalConstraint"),
        fields=[constraint_field, decimal_field],
    )

    render_prep.set_validate_default_on_fields(parser, [model])

    assert isinstance(decimal_field.default, PythonRuntimeExpression)
    assert constraint_type.kwargs == {"ge": Decimal(0)}
    assert not constraint_type.runtime_expression_imports
