"""Tests for Pydantic v2 dataclass generation."""

from __future__ import annotations

from datamodel_code_generator.model import DataModelFieldBase
from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.model.pydantic_v2.base_model import Constraints, has_lookaround_pattern
from datamodel_code_generator.model.pydantic_v2.dataclass import DataClass, DataModelField, DataModelFieldBackport
from datamodel_code_generator.model.pydantic_v2.imports import IMPORT_CONFIG_DICT
from datamodel_code_generator.model.pydantic_v2.types import DataTypeManager
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType, StrictTypes, Types


def test_data_class() -> None:
    """Test basic DataClass generation with required field."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)

    data_class = DataClass(
        fields=[field],
        reference=Reference(name="test_model", path="test_model"),
    )

    assert data_class.name == "test_model"
    assert data_class.decorators == []
    rendered = data_class.render()
    assert "@dataclass" in rendered
    assert "class test_model:" in rendered
    assert "a: str" in rendered


def test_data_class_base_class() -> None:
    """Test DataClass generation with base class inheritance."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)

    data_class = DataClass(
        fields=[field],
        base_classes=[Reference(name="Base", original_name="Base", path="Base")],
        reference=Reference(name="test_model", path="test_model"),
    )

    assert data_class.name == "test_model"
    assert data_class.decorators == []
    rendered = data_class.render()
    assert "@dataclass" in rendered
    assert "class test_model(Base):" in rendered
    assert "a: str" in rendered


def test_data_class_optional() -> None:
    """Test DataClass generation with field default value."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), default="'abc'", required=False)

    data_class = DataClass(
        fields=[field],
        reference=Reference(name="test_model", path="test_model"),
    )

    assert data_class.name == "test_model"
    rendered = data_class.render()
    assert "@dataclass" in rendered
    assert "class test_model:" in rendered
    # pydantic v2 uses Optional[str] for optional fields
    assert "a:" in rendered


def test_data_class_get_data_type() -> None:
    """Test data type retrieval for DataClass fields."""
    data_type_manager = DataTypeManager()
    # Check that the type is correctly mapped
    result = data_type_manager.get_data_type(Types.integer)
    assert result.type == "int"


def test_data_type_manager_returns_copied_type_map_entries() -> None:
    """Pydantic type map entries are reusable prototypes, not caller-owned objects."""
    data_type_manager = DataTypeManager()

    integer_type = data_type_manager.get_data_type(Types.integer)
    int64_type = data_type_manager.get_data_type(Types.int64)
    integer_type.alias = "CustomInt"

    assert integer_type is not int64_type
    assert int64_type.alias is None


def test_data_type_manager_returns_copied_strict_type_map_entries() -> None:
    """Strict type map entries should not be shared between callers."""
    data_type_manager = DataTypeManager(strict_types=[StrictTypes.int])

    integer_type = data_type_manager.get_data_type(Types.integer)
    int64_type = data_type_manager.get_data_type(Types.int64)
    integer_type.alias = "CustomInt"

    assert integer_type is not int64_type
    assert int64_type.alias is None


def test_data_class_field_ordering() -> None:
    """Test that fields are sorted: required first, defaults last."""
    required_field = DataModelFieldBase(name="required_field", data_type=DataType(type="str"), required=True)
    optional_field = DataModelFieldBase(
        name="optional_field", data_type=DataType(type="str"), default="None", required=False
    )

    # Pass fields in wrong order (optional first)
    data_class = DataClass(
        fields=[optional_field, required_field],
        reference=Reference(name="test_model", path="test_model"),
    )

    # Required field should come first after sorting
    assert data_class.fields[0].name == "required_field"
    assert data_class.fields[1].name == "optional_field"


def test_data_class_frozen() -> None:
    """Test DataClass with frozen=True."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)

    data_class = DataClass(
        fields=[field],
        reference=Reference(name="test_model", path="test_model"),
        frozen=True,
    )

    assert data_class.dataclass_arguments.get("frozen") is True
    assert "@dataclass(frozen=True)" in data_class.render()


def test_data_class_kw_only() -> None:
    """Test DataClass with keyword_only=True."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)

    data_class = DataClass(
        fields=[field],
        reference=Reference(name="test_model", path="test_model"),
        keyword_only=True,
    )

    assert data_class.dataclass_arguments.get("kw_only") is True
    assert "@dataclass(kw_only=True)" in data_class.render()


def test_data_class_with_description() -> None:
    """Test DataClass with docstring description."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)

    data_class = DataClass(
        fields=[field],
        reference=Reference(name="test_model", path="test_model"),
        description="This is a test model.",
    )

    rendered = data_class.render()
    assert "class test_model:" in rendered
    assert '"""' in rendered
    assert "This is a test model." in rendered


def test_data_model_field() -> None:
    """Test DataModelField creation."""
    field = DataModelField(
        name="test_field",
        data_type=DataType(type="str"),
        required=True,
    )

    assert field.name == "test_field"
    assert field.required is True


def test_data_model_field_keeps_existing_alias_fallback_state_pydantic20() -> None:
    """Test fallback does not duplicate aliases or overwrite serialization aliases."""
    field = DataModelFieldBackport(
        name="test_field",
        data_type=DataType(type="str"),
        required=True,
        alias="not-valid",
        validation_aliases=["not-valid"],
        serialization_alias="wire-name",
    )

    assert field.alias is None
    assert field.validation_aliases == ["not-valid"]
    assert field.serialization_alias == "wire-name"


def test_has_lookaround_pattern_skips_reference_without_fields() -> None:
    """Following a reference whose source is not a model (no ``fields``) is safe."""
    sourceless_ref = Reference(name="Plain", path="plain")
    sourceless_ref.source = DataType(type="str")
    field = DataModelFieldBase(name="value", data_type=DataType(reference=sourceless_ref), required=False)

    assert has_lookaround_pattern([field], follow_references=True) is False


def test_data_class_imports_cache_clears_after_lookaround_regex_engine() -> None:
    """Lookaround regex config imports are visible after an earlier imports read."""
    field = DataModelFieldBase(
        name="a",
        data_type=DataType(type="str"),
        constraints=Constraints(pattern="(?<=prefix)value"),
        required=True,
    )
    data_class = DataClass(fields=[field], reference=Reference(name="Model", path="model"))

    base_imports_getter = DataModel.imports.fget

    assert base_imports_getter is not None
    base_imports = base_imports_getter(data_class)
    assert IMPORT_CONFIG_DICT not in base_imports

    data_class.render()

    assert IMPORT_CONFIG_DICT in data_class.imports


def test_create_reuse_model() -> None:
    """Test creating a reuse model from existing DataClass."""
    field = DataModelFieldBase(name="a", data_type=DataType(type="str"), required=True)

    data_class = DataClass(
        fields=[field],
        reference=Reference(name="test_model", path="test_model"),
        frozen=True,
    )

    base_ref = Reference(name="BaseModel", path="base_model")
    reuse_model = data_class.create_reuse_model(base_ref)

    assert reuse_model.fields == []
    assert len(reuse_model.base_classes) == 1
    # base_classes are wrapped in BaseClassDataType which has a reference attribute
    assert reuse_model.base_classes[0].reference is base_ref
    assert reuse_model.frozen is True
