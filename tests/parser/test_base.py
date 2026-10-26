"""Tests for base parser classes and utilities."""

from __future__ import annotations

import gc
import weakref
from collections import OrderedDict
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel as PydanticBaseModel

import datamodel_code_generator._internal_utils as internal_utils
from datamodel_code_generator import AllOfMergeMode
from datamodel_code_generator.enums import CollapseRootModelsNameStrategy
from datamodel_code_generator.imports import Import, Imports
from datamodel_code_generator.model import DataModel, DataModelFieldBase

if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator.parser.schema_version import JsonSchemaFeatures

from datamodel_code_generator.model.dataclass import DataClass as DataclassModel
from datamodel_code_generator.model.dataclass import DataModelField as DataclassField
from datamodel_code_generator.model.msgspec import DataModelField as MsgspecField
from datamodel_code_generator.model.msgspec import Struct as MsgspecStruct
from datamodel_code_generator.model.pydantic_v2 import BaseModel, DataModelField
from datamodel_code_generator.model.pydantic_v2.base_model import Constraints
from datamodel_code_generator.model.pydantic_v2.dataclass import DataClass as PydanticDataclassModel
from datamodel_code_generator.model.pydantic_v2.dataclass import DataModelField as PydanticDataclassField
from datamodel_code_generator.model.pydantic_v2.root_model import RootModel
from datamodel_code_generator.model.pydantic_v2.root_model_type_alias import RootModelTypeAlias
from datamodel_code_generator.model.runtime_validation import (
    PropertyCountRule,
    RequiredGroupsRule,
    _make_internal_schema_runtime_validation,
)
from datamodel_code_generator.model.type_alias import TypeAlias, TypeAliasTypeBackport, TypeStatement
from datamodel_code_generator.parser._passes._shared import (
    _detach_deferred_inherited_field_parents,
    _get_discriminator_field_value,
    _get_enum_from_base,
    _restore_inherited_field_names_for_unique_aliases,
    exact_import,
    is_ancestor_package_reference,
)
from datamodel_code_generator.parser.base import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
    Child,
    HashableComparable,
    ModuleContext,
    Parser,
    T,
    _apply_constructor_field_adjustments,
    _contains_model_reference,
    _copy_data_model_field,
    _copy_data_type,
    _copy_resolved_inherited_field,
    _find_field,
    _get_inherited_type_modifiers,
    _get_pydantic_v2_root_model_type,
    _is_pydantic_v2_data_model_field,
    _merge_data_type_modifiers,
    _needs_validate_default,
    _unwrap_type_alias,
    add_model_path_to_list,
    alias_shadowed_imports,
    escape_characters,
    get_module_directory,
    get_most_of_parent,
    modules,
    override_required_field,
    plans,
    relative,
    render_prep,
    sort_data_models,
    structure,
    to_hashable,
)
from datamodel_code_generator.parser.generation import GenerationStore
from datamodel_code_generator.reference import Reference, model_key_of, snake_to_upper_camel
from datamodel_code_generator.types import DataType


class A(DataModel):
    """Test data model class A."""


class B(DataModel):
    """Test data model class B."""


class FieldDependencyModel(DataModel):
    """Test model whose field references require definition ordering."""

    REQUIRES_FIELD_DEPENDENCY_ORDERING = True


class C(Parser):
    """Test parser class C."""

    @property
    def schema_features(self) -> JsonSchemaFeatures:
        """Return mock schema features."""
        from datamodel_code_generator.enums import JsonSchemaVersion
        from datamodel_code_generator.parser.schema_version import JsonSchemaFeatures

        return JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft202012)

    def parse_raw(self, name: str, raw: dict[str, Any]) -> None:
        """Parse raw data into models."""


def test_parser() -> None:
    """Test parser initialization."""
    c = C(
        data_model_type=D,
        data_model_root_type=B,
        data_model_field_type=DataModelFieldBase,
        base_class="Base",
        source="",
    )
    assert c.data_model_type == D
    assert c.data_model_root_type == B
    assert c.data_model_field_type == DataModelFieldBase
    assert c.base_class == "Base"
    # Test schema_features property of test stub
    assert c.schema_features.prefix_items is True
    c._register_runtime_expression()
    assert c._has_runtime_expressions is True


def test_parser_aliases_plain_shadowed_imports_without_structured_consumers(parser_fixture: C) -> None:
    """The original no-IR alias path remains available for ordinary field type collisions."""
    field = DataModelField(
        name="date",
        data_type=DataType(type="date", import_=Import(from_="datetime", import_="date")),
    )
    model = BaseModel(reference=_reference("Model"), fields=[field])

    alias_shadowed_imports(
        parser_fixture,
        [model],
        {"date"},
    )

    assert field.data_type.type == "date_aliased"


def test_parser_iterates_mapping_source_without_serializing_it() -> None:
    """Keep in-memory schema mappings structured at the parser boundary."""
    raw_source = {"title": "Model"}
    parser = C(
        data_model_type=D,
        data_model_root_type=B,
        data_model_field_type=DataModelFieldBase,
        source=raw_source,
    )

    assert next(parser.iter_source).raw_data is raw_source


@pytest.mark.parametrize(
    ("model_type", "expected"),
    [
        pytest.param(B, None, id="default-model"),
        pytest.param(RootModel, RootModel, id="root-model"),
        pytest.param(RootModelTypeAlias, RootModelTypeAlias, id="root-model-type-alias"),
        pytest.param(FieldDependencyModel, FieldDependencyModel, id="custom-capability"),
    ],
)
def test_field_dependency_ordering_capability(
    model_type: type[DataModel],
    expected: type[DataModel] | None,
) -> None:
    """Resolve field dependency ordering without importing a concrete backend in the parser."""
    assert _get_pydantic_v2_root_model_type(model_type) is expected


def test_field_dependency_ordering_capability_is_inherited() -> None:
    """External RootModel subclasses preserve the existing MRO-based behavior."""

    class ExternalRootModel(RootModel):
        pass

    assert _get_pydantic_v2_root_model_type(ExternalRootModel) is ExternalRootModel


@pytest.mark.parametrize(
    ("original_name", "alias", "name", "expected_alias"),
    [
        pytest.param("", None, "field_", "", id="empty-original-name"),
        pytest.param("source", "", "field_", "", id="empty-alias"),
        pytest.param(None, None, None, "", id="missing-names"),
    ],
)
def test_field_metadata_preserves_empty_names(
    original_name: str | None,
    alias: str | None,
    name: str | None,
    expected_alias: str,
) -> None:
    """Treat empty metadata names as explicit values rather than missing values."""
    field = DataModelFieldBase(
        name=name,
        original_name=original_name,
        alias=alias,
        data_type=DataType(type="str"),
        required=True,
    )

    assert Parser._field_metadata(field) == {
        "name": name if name is not None else "",
        "alias": expected_alias,
        "original_name": original_name,
        "type": "str",
        "required": True,
    }


def test_local_source_cache_yields_fresh_source_objects(tmp_path: Path) -> None:
    """Test cached local source materialization preserves fresh Source object semantics."""
    source_path = tmp_path / "schema.json"
    source_path.write_text("{}", encoding="utf-8")
    parser = C(
        data_model_type=D,
        data_model_root_type=B,
        data_model_field_type=DataModelFieldBase,
        source=tmp_path,
    )
    parser._cache_local_sources = True

    first_source = next(parser.iter_source)
    first_source.raw_data = {"mutated": True}
    second_source = next(parser.iter_source)

    assert second_source is not first_source
    assert second_source.raw_data is None
    assert second_source.text == "{}"


def test_local_source_cache_reset_clears_state(tmp_path: Path) -> None:
    """Test local source cache reset clears cached source state."""
    source_path = tmp_path / "schema.json"
    source_path.write_text("{}", encoding="utf-8")
    parser = C(
        data_model_type=D,
        data_model_root_type=B,
        data_model_field_type=DataModelFieldBase,
        source=tmp_path,
    )

    parser._cache_local_sources = True
    parser._local_source_cache = tuple(parser._iter_source_uncached())
    parser._reset_local_source_cache()

    assert parser._cache_local_sources is False
    assert parser._local_source_cache is None


def test_add_model_path_to_list() -> None:
    """Test method which adds model paths to "update" list."""
    reference_1 = Reference(path="Base1", original_name="A", name="A")
    reference_2 = Reference(path="Alias2", original_name="B", name="B")
    reference_3 = Reference(path="Alias3", original_name="B", name="B")
    reference_4 = Reference(path="Alias4", original_name="B", name="B")
    reference_5 = Reference(path="Alias5", original_name="B", name="B")
    model1 = BaseModel(fields=[], reference=reference_1)
    model2 = TypeAlias(fields=[], reference=reference_2)
    model3 = TypeAlias(fields=[], reference=reference_3)
    model4 = TypeAliasTypeBackport(fields=[], reference=reference_4)
    model5 = TypeStatement(fields=[], reference=reference_5)

    paths = add_model_path_to_list(None, model1)
    assert "Base1" in paths
    assert len(paths) == 1

    paths = list[str]()
    add_model_path_to_list(paths, model1)
    assert "Base1" in paths
    assert len(paths) == 1

    add_model_path_to_list(paths, model1)
    assert len(paths) != 2
    assert len(paths) == 1

    add_model_path_to_list(paths, model2)
    assert "Alias2" not in paths

    add_model_path_to_list(paths, model3)
    assert "Alias3" not in paths

    add_model_path_to_list(paths, model4)
    assert "Alias4" not in paths

    add_model_path_to_list(paths, model5)
    assert "Alias5" not in paths


def test_sort_data_models() -> None:
    """Test sorting data models by dependencies."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    reference_c = Reference(path="C", original_name="C", name="C")
    data_type_a = DataType(reference=reference_a)
    data_type_b = DataType(reference=reference_b)
    data_type_c = DataType(reference=reference_c)
    reference = [
        BaseModel(
            fields=[
                DataModelField(data_type=data_type_a),
                DataModelFieldBase(data_type=data_type_c),
            ],
            reference=reference_a,
        ),
        BaseModel(
            fields=[DataModelField(data_type=data_type_b)],
            reference=reference_b,
        ),
        BaseModel(
            fields=[DataModelField(data_type=data_type_b)],
            reference=reference_c,
        ),
    ]

    unresolved, resolved, require_update_action_models = sort_data_models(reference)
    keys = {item.path: model_key_of(item) for item in (reference_a, reference_b, reference_c)}
    expected = OrderedDict()
    expected[keys["B"]] = reference[1]
    expected[keys["C"]] = reference[2]
    expected[keys["A"]] = reference[0]

    assert resolved == expected
    assert unresolved == []
    assert require_update_action_models == [keys["B"], keys["A"]]

    _, _, seeded_require_update_action_models = sort_data_models(reference, require_update_action_models=[keys["B"]])
    assert seeded_require_update_action_models == [keys["B"], keys["A"]]


def test_sort_data_models_unresolved() -> None:
    """Test sorting data models with unresolved references."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    reference_c = Reference(path="C", original_name="C", name="C")
    reference_d = Reference(path="D", original_name="D", name="D")
    reference_v = Reference(path="V", original_name="V", name="V")
    reference_z = Reference(path="Z", original_name="Z", name="Z")
    data_type_a = DataType(reference=reference_a)
    data_type_b = DataType(reference=reference_b)
    data_type_c = DataType(reference=reference_c)
    data_type_v = DataType(reference=reference_v)
    data_type_z = DataType(reference=reference_z)
    reference = [
        BaseModel(
            fields=[
                DataModelField(data_type=data_type_a),
                DataModelFieldBase(data_type=data_type_c),
            ],
            reference=reference_a,
        ),
        BaseModel(
            fields=[DataModelField(data_type=data_type_b)],
            reference=reference_b,
        ),
        BaseModel(
            fields=[DataModelField(data_type=data_type_b)],
            reference=reference_c,
        ),
        BaseModel(
            fields=[
                DataModelField(data_type=data_type_a),
                DataModelField(data_type=data_type_c),
                DataModelField(data_type=data_type_z),
            ],
            reference=reference_d,
        ),
        BaseModel(
            fields=[DataModelField(data_type=data_type_v)],
            reference=reference_z,
        ),
    ]

    with pytest.raises(Exception):  # noqa: B017, PT011
        sort_data_models(reference)


def test_sort_data_models_unresolved_raise_recursion_error() -> None:
    """Test sorting data models raises error on recursion limit."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    reference_c = Reference(path="C", original_name="C", name="C")
    reference_d = Reference(path="D", original_name="D", name="D")
    reference_v = Reference(path="V", original_name="V", name="V")
    reference_z = Reference(path="Z", original_name="Z", name="Z")
    data_type_a = DataType(reference=reference_a)
    data_type_b = DataType(reference=reference_b)
    data_type_c = DataType(reference=reference_c)
    data_type_v = DataType(reference=reference_v)
    data_type_z = DataType(reference=reference_z)
    reference = [
        BaseModel(
            fields=[
                DataModelField(data_type=data_type_a),
                DataModelFieldBase(data_type=data_type_c),
            ],
            reference=reference_a,
        ),
        BaseModel(
            fields=[DataModelField(data_type=data_type_b)],
            reference=reference_b,
        ),
        BaseModel(
            fields=[DataModelField(data_type=data_type_b)],
            reference=reference_c,
        ),
        BaseModel(
            fields=[
                DataModelField(data_type=data_type_a),
                DataModelField(data_type=data_type_c),
                DataModelField(data_type=data_type_z),
            ],
            reference=reference_d,
        ),
        BaseModel(
            fields=[DataModelField(data_type=data_type_v)],
            reference=reference_z,
        ),
    ]

    with pytest.raises(Exception):  # noqa: B017, PT011
        sort_data_models(reference, recursion_count=100000)


@pytest.mark.parametrize(
    ("current_module", "reference", "val"),
    [
        ("", "Foo", ("", "")),
        ("a", "a.Foo", ("", "")),
        ("a", "a.b.Foo", (".", "b")),
        ("a.b", "a.Foo", (".", "Foo")),
        ("a.b.c", "a.Foo", ("..", "Foo")),
        ("a.b.c", "Foo", ("...", "Foo")),
    ],
)
def test_relative(current_module: str, reference: str, val: tuple[str, str]) -> None:
    """Test relative import calculation."""
    assert relative(current_module, reference) == val


@pytest.mark.parametrize(
    ("from_", "import_", "name", "val"),
    [
        (".", "mod", "Foo", (".mod", "Foo")),
        ("..", "mod", "Foo", ("..mod", "Foo")),
        (".a", "mod", "Foo", (".a.mod", "Foo")),
        ("..a", "mod", "Foo", ("..a.mod", "Foo")),
        ("..a.b", "mod", "Foo", ("..a.b.mod", "Foo")),
    ],
)
def test_exact_import(from_: str, import_: str, name: str, val: tuple[str, str]) -> None:
    """Test exact import formatting."""
    assert exact_import(from_, import_, name) == val


@pytest.mark.parametrize(
    ("current_module", "reference", "expected"),
    [
        ("", "Foo", False),  # no current module
        ("a", "Foo", True),  # root package is the immediate parent
        ("a.b", "Foo", True),  # root package is a grandparent
        ("a.b.c", "Foo", True),  # root package is a deeper ancestor
        ("a.b", "a.Foo", True),  # immediate parent package
        ("a.b.c", "a.Foo", True),  # deeper ancestor package
        ("a", "a.Foo", False),  # same module
        ("a.b", "a.b.Foo", False),  # same module, nested
        ("a", "a.b.Foo", False),  # child module
        ("a.b", "a.c.Foo", False),  # sibling module
        ("a.b", "z.Foo", False),  # unrelated module
    ],
)
def test_is_ancestor_package_reference(current_module: str, reference: str, *, expected: bool) -> None:
    """Test detection of references declared in an ancestor package's ``__init__.py``.

    This is exactly the set of cases where :func:`relative` returns a class name rather
    than a module name, so callers must not treat its result as an importable module.
    """
    assert is_ancestor_package_reference(current_module, reference) is expected


@pytest.mark.parametrize(
    ("current_module", "reference", "expected"),
    [
        ("a", "Foo", (".", "Foo")),
        ("a.b", "Foo", ("..", "Foo")),
        ("a.b.c", "Foo", ("...", "Foo")),
        ("a.b", "a.Foo", (".", "Foo")),
        ("a.b.c", "a.Foo", ("..", "Foo")),
    ],
)
def test_relative_returns_class_name_for_ancestor_package(
    current_module: str,
    reference: str,
    expected: tuple[str, str],
) -> None:
    """``relative`` yields the class name, not a module, for ancestor package references."""
    assert relative(current_module, reference) == expected
    assert is_ancestor_package_reference(current_module, reference)


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        (
            "_hello",
            "_Hello",
        ),  # In case a name starts with a underline, we should keep it.
        ("hello_again", "HelloAgain"),  # regular snake case
        ("hello__again", "HelloAgain"),  # handles double underscores
        (
            "hello___again_again",
            "HelloAgainAgain",
        ),  # handles double and single underscores
        ("hello_again_", "HelloAgain"),  # handles trailing underscores
        ("hello", "Hello"),  # no underscores
        ("____", "_"),  # degenerate case, but this is the current expected behavior
    ],
)
def test_snake_to_upper_camel(word: str, expected: str) -> None:
    """Tests the snake to upper camel function."""
    actual = snake_to_upper_camel(word)
    assert actual == expected


class D(DataModel):
    """Test data model class D."""


@pytest.fixture
def parser_fixture() -> C:
    """Create a test parser instance for unit tests."""
    return C(
        data_model_type=D,
        data_model_root_type=B,
        data_model_field_type=DataModelFieldBase,
        base_class="Base",
        source="",
    )


def _reference(path: str) -> Reference:
    return Reference(path=path, original_name=path, name=path)


def test_pydantic_v2_data_model_field_compatibility_helper() -> None:
    """Keep the public compatibility helper working with real field objects."""
    pydantic_field = DataModelField(name="value", data_type=DataType(type="str"))
    generic_field = DataModelFieldBase(name="value", data_type=DataType(type="str"))

    assert _is_pydantic_v2_data_model_field(pydantic_field)
    assert not _is_pydantic_v2_data_model_field(generic_field)


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        pytest.param(None, (None, None), id="none"),
        pytest.param("external", ("external", None), id="single"),
        pytest.param(["first", "second"], (None, ["first", "second"]), id="validation"),
    ],
)
def test_parser_splits_field_alias_policy(
    parser_fixture: C,
    alias: str | list[str] | None,
    expected: tuple[str | None, list[str] | None],
) -> None:
    """Share alias normalization across schema parser implementations."""
    assert parser_fixture._split_field_alias(alias) == expected


def test_parser_resolves_effective_default_policy(parser_fixture: C) -> None:
    """Share required default handling across schema parser implementations."""
    parser_fixture.apply_default_values_for_required_fields = True

    assert parser_fixture._effective_default_state(
        "value",
        "default",
        has_default=True,
        required=True,
        class_name="Model",
    ) == ("default", True, True)


@pytest.mark.parametrize(
    ("model_type", "field_type", "expected_assignment", "expected_new_extras", "keyword_only"),
    [
        pytest.param(DataclassModel, DataclassField, "field()", {}, False, id="stdlib"),
        pytest.param(DataclassModel, DataclassField, "field()", {}, True, id="stdlib-model-kw-only"),
        pytest.param(
            PydanticDataclassModel,
            PydanticDataclassField,
            "Field(...)",
            {"kw_only": True},
            False,
            id="pydantic-v2",
        ),
        pytest.param(
            PydanticDataclassModel,
            PydanticDataclassField,
            "Field(...)",
            {},
            True,
            id="pydantic-v2-model-kw-only",
        ),
    ],
)
def test_dataclass_required_override_of_inherited_default_uses_exact_assignment(
    parser_fixture: C,
    model_type: type[DataModel],
    field_type: type[DataModelFieldBase],
    expected_assignment: str,
    expected_new_extras: dict[str, bool],
    *,
    keyword_only: bool,
) -> None:
    """Clear only the leaking inherited default without restricting later positional fields."""
    base_reference = _reference("Base")
    model_type(
        fields=[
            field_type(name="defaulted", data_type=DataType(type="str"), required=False),
            field_type(name="required", data_type=DataType(type="str"), required=True),
        ],
        reference=base_reference,
    )
    required_override = field_type(name="defaulted", data_type=DataType(type="str"), required=True)
    unchanged_override = field_type(name="required", data_type=DataType(type="str"), required=True)
    new_required = field_type(name="child", data_type=DataType(type="str"), required=True)
    child = model_type(
        fields=[required_override, unchanged_override, new_required],
        base_classes=[base_reference],
        reference=_reference("Child"),
        keyword_only=keyword_only,
    )

    render_prep.fix_constructor_field_ordering(parser_fixture, [child])

    assert required_override.extras == {}
    assert str(required_override) == expected_assignment
    assert unchanged_override.extras == {}
    assert new_required.extras == expected_new_extras


def test_constructor_adjustment_promotes_msgspec_keyword_only_to_model() -> None:
    """Backends that require model-level keyword-only state receive one adjustment."""
    field = MsgspecField(name="value", data_type=DataType(type="str"), required=True)
    model = MsgspecStruct(
        fields=[field],
        reference=_reference("Struct"),
    )

    _apply_constructor_field_adjustments(GenerationStore().builder(), model, (field, "keyword_only"), ())

    assert model.has_keyword_only_definition()


def test_apply_type_overrides_fast_path_preserves_models(parser_fixture: C) -> None:
    """An empty override policy leaves model fields and bases untouched."""
    field = DataModelField(name="value", data_type=DataType(type="str"))
    model = BaseModel(fields=[field], reference=_reference("Model"))
    parser_fixture._type_override_imports = {"Other.value": Import(from_="custom", import_="Value")}
    parser_fixture._model_type_override_imports = {}

    modules.apply_field_type_overrides(parser_fixture, [model])
    modules.apply_base_class_type_overrides(parser_fixture, [model])

    assert model.fields == [field]


def test_dataclass_inherited_init_cleanup_without_other_adjustments(parser_fixture: C) -> None:
    """Clearing inherited init metadata alone refreshes ordering and skips unnamed base fields."""
    base_reference = _reference("InitBase")
    inherited_field = DataclassField(
        name="value",
        data_type=DataType(type="str"),
        required=True,
        extras={"init": False},
    )
    DataclassModel(
        fields=[
            DataclassField(name=None, data_type=DataType(type="str"), required=True),
            inherited_field,
        ],
        reference=base_reference,
    )
    required_override = DataclassField(
        name="value",
        data_type=DataType(type="str"),
        required=True,
        extras={"init": False},
    )
    DataclassModel.prepare_required_inherited_field(required_override, inherited_field)
    unnamed_child = DataclassField(name=None, data_type=DataType(type="str"), required=True)
    child = DataclassModel(
        fields=[required_override, unnamed_child],
        base_classes=[base_reference],
        reference=_reference("InitChild"),
    )

    render_prep.fix_constructor_field_ordering(parser_fixture, [child])

    assert required_override.extras == {}
    assert not str(required_override)
    assert unnamed_child.extras == {}


def test_dataclass_metadata_assignment_without_default_stays_positional(parser_fixture: C) -> None:
    """Do not treat metadata-only field() assignments as constructor defaults."""
    base_reference = _reference("MetadataBase")
    metadata_field = DataclassField(
        name="metadata",
        data_type=DataType(type="str"),
        default=None,
        required=False,
        strip_default_none=True,
        extras={"repr": False},
    )
    DataclassModel(fields=[metadata_field], reference=base_reference)
    child_field = DataclassField(
        name="child",
        data_type=DataType(type="str"),
        required=True,
    )
    child = DataclassModel(
        fields=[child_field],
        base_classes=[base_reference],
        reference=_reference("MetadataChild"),
    )

    render_prep.fix_constructor_field_ordering(parser_fixture, [child])

    assert str(metadata_field) == "field(repr=False)"
    assert child_field.extras == {}


def test_get_enum_from_base_skips_models_without_inherited_enum_capability() -> None:
    """Do not inherit discriminator enums from output models that opt out."""
    from datamodel_code_generator.model.enum import Enum

    enum_reference = _reference("Kind")
    Enum(fields=[], reference=enum_reference)
    base_reference = _reference("Base")
    base_model = PydanticDataclassModel(
        fields=[
            PydanticDataclassField(
                name="kind",
                original_name="kind",
                data_type=DataType(reference=enum_reference),
            )
        ],
        reference=base_reference,
    )
    child_model = BaseModel(
        fields=[],
        base_classes=[base_reference],
        reference=_reference("Child"),
    )

    assert not base_model.SUPPORTS_INHERITED_DISCRIMINATOR_ENUM
    assert _get_enum_from_base(child_model, "kind") is None


def _create_override_required_models(
    original_data_type: DataType,
    *,
    original_default: object | None = None,
    original_has_default: bool = False,
) -> tuple[BaseModel, BaseModel, DataModelField]:
    base_reference = _reference("BaseModel")
    base_field = DataModelField(
        name="value",
        original_name="value",
        data_type=original_data_type,
        required=False,
        default=original_default,
        has_default=original_has_default,
    )
    base_model = BaseModel(fields=[base_field], reference=base_reference)
    child_field = DataModelField(
        name="value",
        original_name="value",
        data_type=DataType(),
        required=False,
    )
    child_model = BaseModel(
        fields=[child_field],
        base_classes=[base_reference],
        reference=_reference("ChildModel"),
    )
    return base_model, child_model, child_field


def _override_required_fields(parser: C, models: list[BaseModel]) -> None:
    override_required_field(parser, models)


def test_find_field_skips_non_matching_fields() -> None:
    """Field lookup checks later fields after a non-matching field."""
    first_field = DataModelField(
        name="first",
        original_name="first",
        data_type=DataType(type="str"),
    )
    second_field = DataModelField(
        name="second",
        original_name="second",
        data_type=DataType(type="int"),
    )
    model = BaseModel(fields=[first_field, second_field], reference=_reference("SearchModel"))

    assert _find_field("second", [model]) is second_field


def test_find_field_prefers_effective_override_and_original_name() -> None:
    """Field lookup honors parent overrides and exact source names across direct bases."""
    grandparent_reference = _reference("Grandparent")
    grandparent_field = DataModelField(
        name="value",
        original_name="value",
        data_type=DataType(type="str"),
    )
    BaseModel(fields=[grandparent_field], reference=grandparent_reference)
    parent_field = DataModelField(
        name="value",
        original_name="value",
        data_type=DataType(type="int"),
    )
    parent = BaseModel(
        fields=[parent_field],
        base_classes=[grandparent_reference],
        reference=_reference("Parent"),
    )
    generated_name_collision = DataModelField(
        name="target",
        original_name="other",
        data_type=DataType(type="bool"),
    )
    collision_base = BaseModel(
        fields=[generated_name_collision],
        reference=_reference("CollisionBase"),
    )
    exact_original_name = DataModelField(
        name="target_field",
        original_name="target",
        data_type=DataType(type="float"),
    )
    exact_base = BaseModel(
        fields=[exact_original_name],
        reference=_reference("ExactBase"),
    )

    assert _find_field("value", [parent]) is parent_field
    assert _find_field("target", [collision_base, exact_base]) is exact_original_name


def test_find_field_follows_multiple_inheritance_mro() -> None:
    """The first declared base wins when a nested model inherits duplicate fields."""
    first_reference = _reference("FirstBase")
    first_field = DataModelField(
        name="value",
        original_name="value",
        data_type=DataType(type="str"),
    )
    BaseModel(fields=[first_field], reference=first_reference)
    second_reference = _reference("SecondBase")
    second_field = DataModelField(
        name="value",
        original_name="value",
        data_type=DataType(type="int"),
    )
    BaseModel(fields=[second_field], reference=second_reference)
    combined = BaseModel(
        fields=[],
        base_classes=[first_reference, second_reference],
        reference=_reference("Combined"),
    )

    assert _find_field("value", [combined]) is first_field


def test_find_field_follows_c3_diamond_mro() -> None:
    """A later direct base takes precedence over an earlier base's shared ancestor."""
    root_reference = _reference("RootBase")
    root_field = DataModelField(
        name="value",
        original_name="value",
        data_type=DataType(type="str"),
    )
    BaseModel(fields=[root_field], reference=root_reference)
    first_reference = _reference("FirstBranch")
    BaseModel(fields=[], base_classes=[root_reference], reference=first_reference)
    second_reference = _reference("SecondBranch")
    second_field = DataModelField(
        name="value",
        original_name="value",
        data_type=DataType(type="int"),
    )
    BaseModel(fields=[second_field], base_classes=[root_reference], reference=second_reference)
    diamond = BaseModel(
        fields=[],
        base_classes=[first_reference, second_reference],
        reference=_reference("Diamond"),
    )

    assert _find_field("value", [diamond]) is second_field


def test_find_field_handles_cyclic_generated_bases() -> None:
    """Malformed cyclic inheritance terminates and keeps the nearest declaration."""
    first_reference = _reference("FirstCycle")
    second_reference = _reference("SecondCycle")
    first_field = DataModelField(
        name="value",
        original_name="value",
        data_type=DataType(type="str"),
    )
    first = BaseModel(
        fields=[first_field],
        base_classes=[second_reference],
        reference=first_reference,
    )
    second = BaseModel(
        fields=[],
        base_classes=[first_reference],
        reference=second_reference,
    )

    assert _find_field("value", [first, second]) is first_field


def test_override_required_field_copies_reference_type(parser_fixture: C) -> None:
    """Required inherited placeholders are replaced with reference-backed fields."""
    target_reference = _reference("TargetModel")
    BaseModel(fields=[], reference=target_reference)
    base_model, child_model, child_field = _create_override_required_models(
        DataType(reference=target_reference),
    )
    parser_fixture.generation_store.register_model(base_model)
    parser_fixture.generation_store.register_model(child_model)

    _override_required_fields(parser_fixture, [base_model, child_model])

    replacement = child_model.fields[0]
    assert replacement is not child_field
    assert replacement.original_name == "value"
    assert replacement.required is True
    assert replacement.parent is child_model
    assert replacement.data_type.reference is target_reference
    assert replacement.data_type.parent is replacement


def test_override_required_field_copies_nested_types(parser_fixture: C) -> None:
    """Nested inherited data types are recursively copied with complete parent links."""
    target_reference = _reference("NestedTarget")
    BaseModel(fields=[], reference=target_reference)
    original_data_type = DataType(
        data_types=[
            DataType(reference=target_reference),
            DataType(data_types=[DataType(type="str")]),
            DataType(type="int"),
        ],
    )
    base_model, child_model, child_field = _create_override_required_models(original_data_type)
    parser_fixture.generation_store.register_model(base_model)
    parser_fixture.generation_store.register_model(child_model)

    _override_required_fields(parser_fixture, [base_model, child_model])

    replacement = child_model.fields[0]
    copied_data_type = replacement.data_type
    assert replacement is not child_field
    assert copied_data_type is not original_data_type
    assert copied_data_type.parent is replacement
    assert copied_data_type.data_types[0].reference is target_reference
    assert copied_data_type.data_types[0] is not original_data_type.data_types[0]
    assert copied_data_type.data_types[0].parent is copied_data_type
    assert copied_data_type.data_types[1] is not original_data_type.data_types[1]
    assert copied_data_type.data_types[1].data_types[0].type == "str"
    assert copied_data_type.data_types[1].parent is copied_data_type
    assert copied_data_type.data_types[1].data_types[0].parent is copied_data_type.data_types[1]
    assert copied_data_type.data_types[2].type == "int"
    assert copied_data_type.data_types[2] is not original_data_type.data_types[2]
    assert copied_data_type.data_types[2].parent is copied_data_type


def test_copy_data_type_preserves_reference_graph_and_isolates_mutable_state() -> None:
    """Reference-preserving copies own every mutable node in the DataType tree."""

    class SpecializedDataType(DataType):
        pass

    value_reference = _reference("ValueModel")
    key_reference = _reference("KeyModel")
    original_value_type = SpecializedDataType(
        reference=value_reference,
        is_optional=True,
        kwargs={"metadata": ["original"]},
        literals=["value"],
        enum_member_literals=[("ValueModel", "value")],
    )
    original_key_type = SpecializedDataType(reference=key_reference)
    original_data_type = SpecializedDataType(
        data_types=[original_value_type],
        is_dict=True,
        dict_key=original_key_type,
        kwargs={"constraints": {"minimum": 1}},
        literals=["root"],
        enum_member_literals=[("Root", "root")],
    )

    copied_data_type = _copy_data_type(original_data_type)
    copied_value_type = copied_data_type.data_types[0]
    copied_key_type = copied_data_type.dict_key

    assert isinstance(copied_data_type, SpecializedDataType)
    assert copied_data_type is not original_data_type
    assert copied_value_type is not original_value_type
    assert copied_key_type is not original_key_type
    assert copied_value_type.parent is copied_data_type
    assert copied_key_type is not None
    assert copied_key_type.parent is copied_data_type
    assert copied_value_type.reference is value_reference
    assert copied_key_type.reference is key_reference
    assert copied_value_type.is_optional is True
    assert sum(child is copied_value_type for child in value_reference.children) == 1
    assert sum(child is copied_key_type for child in key_reference.children) == 1

    copied_data_type.kwargs["constraints"]["minimum"] = 2
    copied_data_type.literals.append("copied")
    copied_data_type.enum_member_literals.append(("Root", "copied"))
    copied_value_type.kwargs["metadata"].append("copied")

    assert original_data_type.kwargs == {"constraints": {"minimum": 1}}
    assert original_data_type.literals == ["root"]
    assert original_data_type.enum_member_literals == [("Root", "root")]
    assert original_value_type.kwargs == {"metadata": ["original"]}


def test_copy_data_model_field_isolates_aliases_and_mutable_default() -> None:
    """Inherited field copies do not share aliases or mutable default state."""
    field = DataModelField(
        name="value",
        original_name="value",
        validation_aliases=["value", "legacy-value"],
        data_type=DataType(type="str"),
        default={"nested": ["original"]},
        has_default=True,
    )

    copied_field = _copy_data_model_field(field)
    copied_field.validation_aliases.append("copied")
    copied_field.default["nested"].append("copied")

    assert field.validation_aliases == ["value", "legacy-value"]
    assert field.default == {"nested": ["original"]}


def test_merge_inherited_type_modifiers_overlays_kwargs() -> None:
    """Deferred type modifiers copy and overlay independent constraint maps."""
    copied_kwargs_type = DataType(type="str")
    _merge_data_type_modifiers(
        copied_kwargs_type,
        DataType(kwargs={"min_length": 2}),
    )

    intersected_kwargs_type = DataType(type="str", kwargs={"min_length": 1})
    _merge_data_type_modifiers(
        intersected_kwargs_type,
        DataType(kwargs={"min_length": 3, "max_length": 8}),
    )

    container_type = DataType(type="str")
    _merge_data_type_modifiers(
        container_type,
        DataType(
            is_optional=True,
            is_dict=True,
            is_list=True,
            is_set=True,
            is_frozen_set=True,
            is_mapping=True,
            is_sequence=True,
            is_tuple=True,
            tuple_item_count=3,
        ),
        preserve_container_shape=True,
        preserve_optional=True,
    )

    overriding_tuple_type = DataType(is_tuple=True, tuple_item_count=2)
    _merge_data_type_modifiers(
        overriding_tuple_type,
        DataType(is_tuple=True, tuple_item_count=3),
        preserve_container_shape=True,
    )

    assert copied_kwargs_type.kwargs == {"min_length": 2}
    assert intersected_kwargs_type.kwargs == {"min_length": 3, "max_length": 8}
    assert container_type.is_optional
    assert container_type.is_dict
    assert container_type.is_list
    assert container_type.is_set
    assert container_type.is_frozen_set
    assert container_type.is_mapping
    assert container_type.is_sequence
    assert container_type.is_tuple
    assert container_type.tuple_item_count == 3
    assert overriding_tuple_type.tuple_item_count == 2


def test_copy_resolved_inherited_field_preserves_wrapper_and_schema_metadata() -> None:
    """A partial array wrapper retains optionality, constraints, and raw defaults."""
    inherited_field = DataModelField(
        name="items",
        original_name="items",
        data_type=DataType(data_types=[DataType(type="str")], is_list=True),
        constraints=Constraints(minLength=2, pattern="^a"),
    )
    field = DataModelField(
        name="items",
        original_name="items",
        data_type=DataType(
            data_types=[DataType(data_types=[DataType(type="str")], is_list=True)],
            is_optional=True,
        ),
        constraints=Constraints(minLength=3, pattern="z$"),
        default=["az"],
        has_default=True,
    )
    field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = field.data_type
    field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] = ["raw"]

    copied_field = _copy_resolved_inherited_field(
        field,
        inherited_field,
        partial_merge_mode=AllOfMergeMode.All,
    )

    assert copied_field is not None
    assert copied_field.data_type.is_optional
    assert not copied_field.data_type.is_list
    assert copied_field.data_type.data_types[0].is_list
    assert copied_field.data_type.data_types[0].data_types[0].type == "str"
    assert copied_field.constraints == Constraints(
        minLength=3,
        pattern="z$",
    )
    assert copied_field.default == ["az"]
    assert copied_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] == ["raw"]


def test_copy_resolved_required_only_inherited_field_metadata() -> None:
    """A required-only declaration keeps child aliases on the inherited field type."""
    inherited_field = DataModelField(
        name="inherited_name",
        original_name="source-name",
        data_type=DataType(type="str"),
    )
    field = DataModelField(
        name="source_name",
        original_name="source-name",
        data_type=DataType(),
        required=True,
        serialization_alias="serialized-name",
        use_serialization_alias=True,
    )
    field.__dict__[_DEFERRED_INHERITED_FIELD_KEY] = "Derived"

    copied_field = _copy_resolved_inherited_field(field, inherited_field)

    assert copied_field is not None
    assert copied_field.required
    assert copied_field.serialization_alias == "serialized-name"
    assert copied_field.use_serialization_alias


@pytest.mark.parametrize("gc_initially_enabled", [True, False])
def test_detach_deferred_inherited_field_parents_releases_cycles_without_gc(
    *,
    gc_initially_enabled: bool,
) -> None:
    """Discarded placeholder trees are reclaimed immediately without touching live references."""
    gc_state_setters = (gc.disable, gc.enable)
    restore_gc = gc_state_setters[gc.isenabled()]
    gc_state_setters[gc_initially_enabled]()
    gc.disable()
    try:
        field = DataModelField(
            name="items",
            original_name="items",
            data_type=DataType(
                data_types=[
                    DataType(
                        data_types=[DataType(type="str")],
                        is_list=True,
                    ),
                ],
            ),
        )
        modifiers = _get_inherited_type_modifiers(field.data_type)
        field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = modifiers
        assert modifiers.list_wrapper is not None
        tracked_objects = [
            field,
            *field.data_type.all_data_types,
            modifiers.list_wrapper,
            *modifiers.list_wrapper.all_data_types,
        ]
        weak_references = [weakref.ref(value) for value in tracked_objects]

        _detach_deferred_inherited_field_parents(field)
        del field, modifiers, tracked_objects

        assert all(reference() is None for reference in weak_references)
    finally:
        restore_gc()


def test_detach_deferred_inherited_field_data_type_releases_parent_links() -> None:
    """The pre-compression deferred type form also releases every parent link."""
    field = DataModelField(
        name="items",
        original_name="items",
        data_type=DataType(data_types=[DataType(type="str")], is_list=True),
    )
    deferred_type = DataType(data_types=[DataType(type="int")], is_list=True)
    deferred_type.parent = field
    field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = deferred_type

    _detach_deferred_inherited_field_parents(field)

    assert field.parent is None
    assert all(data_type.parent is None for data_type in deferred_type.all_data_types)


def test_override_required_field_copies_plain_type_with_required_default() -> None:
    """Required inherited fields can keep defaults when that option is enabled."""
    parser = C(source="", apply_default_values_for_required_fields=True)
    original_data_type = DataType(type="str")
    base_model, child_model, child_field = _create_override_required_models(
        original_data_type,
        original_default="'fallback'",
        original_has_default=True,
    )
    parser.generation_store.register_model(base_model)
    parser.generation_store.register_model(child_model)
    concrete_field = DataModelField(
        name="local",
        original_name="local",
        data_type=DataType(type="int"),
    )
    child_model.fields.append(concrete_field)

    _override_required_fields(parser, [base_model, child_model])

    replacement = child_model.fields[0]
    assert replacement is not child_field
    assert replacement.data_type is not original_data_type
    assert replacement.data_type.type == "str"
    assert replacement.data_type.parent is replacement
    assert replacement.required is True
    assert replacement.has_default is True
    assert replacement.default == "'fallback'"
    assert replacement.use_default_with_required is True
    assert child_model.fields[1] is concrete_field


def test_override_required_field_resolves_scoped_mutable_default() -> None:
    """Late inherited defaults use the derived scope and own mutable values."""
    parser = C(
        source="",
        default_value_overrides={"ChildModel.value": {"source": ["override"]}},
        allof_merge_mode=AllOfMergeMode.All,
    )
    base_model, child_model, child_field = _create_override_required_models(
        DataType(type="str"),
        original_default="'schema'",
        original_has_default=True,
    )
    base_model.fields[0].__dict__[_RAW_SCHEMA_DEFAULT_KEY] = "schema"
    child_field.__dict__[_DEFERRED_INHERITED_CLASS_KEY] = "ChildModel"
    parser.generation_store.register_model(base_model)
    parser.generation_store.register_model(child_model)

    _override_required_fields(parser, [base_model, child_model])

    replacement = child_model.fields[0]
    replacement.default["source"].append("child")
    assert replacement.default == {"source": ["override", "child"]}
    assert parser.model_resolver.default_value_overrides["ChildModel.value"] == {
        "source": ["override"],
    }


def test_override_required_field_removes_placeholder_without_inherited_field(parser_fixture: C) -> None:
    """Placeholders without a matching inherited field are removed."""
    base_reference = _reference("EmptyBase")
    base_model = BaseModel(fields=[], reference=base_reference)
    child_field = DataModelField(
        name="missing",
        original_name="missing",
        data_type=DataType(),
    )
    child_model = BaseModel(
        fields=[child_field],
        base_classes=[base_reference],
        reference=_reference("MissingChild"),
    )
    parser_fixture.generation_store.register_model(base_model)
    parser_fixture.generation_store.register_model(child_model)

    _override_required_fields(parser_fixture, [base_model, child_model])

    assert child_model.fields == []


def test_additional_imports() -> None:
    """Test that additional imports are inside imports container."""
    new_parser = C(
        source="",
        additional_imports=["collections.deque"],
    )
    assert len(new_parser.imports) == 1
    assert new_parser.imports["collections"] == {"deque"}


def test_no_additional_imports() -> None:
    """Test that not additional imports are not affecting imports container."""
    new_parser = C(
        source="",
    )
    assert len(new_parser.imports) == 0


def test_collect_used_names_retains_qualified_python_type_module() -> None:
    """Keep the module portion of qualified semantic annotations in use."""
    from datamodel_code_generator._python_type_annotation import PythonTypeQualifiedName
    from datamodel_code_generator._python_type_binding import BoundPythonType

    model = BaseModel(
        fields=[
            DataModelField(
                name="value",
                data_type=DataType(
                    type="external.Model",
                    python_type=BoundPythonType(PythonTypeQualifiedName(("external", "Model")), ()),
                ),
            )
        ],
        reference=_reference("Model"),
    )

    assert Parser._collect_used_names_from_models([model]) == {"BaseModel", "Model", "Optional", "external", "value"}


@pytest.mark.parametrize(
    ("input_data", "expected"),
    [
        (
            {
                ("folder1", "module1.py"): "content1",
                ("folder1", "module2.py"): "content2",
                ("folder1", "__init__.py"): "init_content",
            },
            {
                ("folder1", "module1.py"): "content1",
                ("folder1", "module2.py"): "content2",
                ("folder1", "__init__.py"): "init_content",
            },
        ),
        (
            {
                ("folder1.module", "file.py"): "content1",
                ("folder1.module", "__init__.py"): "init_content",
            },
            {
                ("folder1", "module", "file.py"): "content1",
                ("folder1", "__init__.py"): "init_content",
                ("folder1", "module", "__init__.py"): "init_content",
            },
        ),
    ],
)
def test_postprocess_result_modules(input_data: Any, expected: Any) -> None:
    """Test postprocessing of result modules."""
    result = Parser._Parser__postprocess_result_modules(input_data)
    assert result == expected


def test_find_member_with_integer_enum() -> None:
    """Test find_member method with integer enum values."""
    from datamodel_code_generator.model.enum import Enum
    from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

    # Create test Enum with integer values
    enum = Enum(
        reference=Reference(path="test_path", original_name="TestEnum", name="TestEnum"),
        fields=[
            DataModelField(
                name="VALUE_1000",
                default="1000",
                data_type=DataType(type="int"),
                required=True,
            ),
            DataModelField(
                name="VALUE_100",
                default="100",
                data_type=DataType(type="int"),
                required=True,
            ),
            DataModelField(
                name="VALUE_0",
                default="0",
                data_type=DataType(type="int"),
                required=True,
            ),
        ],
    )

    # Test finding members with integer values
    assert enum.find_member(1000).field.name == "VALUE_1000"
    assert enum.find_member(100).field.name == "VALUE_100"
    assert enum.find_member(0).field.name == "VALUE_0"

    # String values only match integer members for discriminator mapping keys
    assert enum.find_member("1000") is None
    assert enum.find_member("1000", coerce_strings=True).field.name == "VALUE_1000"
    assert enum.find_member("100", coerce_strings=True).field.name == "VALUE_100"
    assert enum.find_member("0", coerce_strings=True).field.name == "VALUE_0"

    # Test with non-existent values
    assert enum.find_member(999) is None
    assert enum.find_member("999", coerce_strings=True) is None


def test_find_member_with_string_enum() -> None:
    """Test find_member method with string enum values."""
    from datamodel_code_generator.model.enum import Enum, EnumMemberValue
    from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

    enum = Enum(
        reference=Reference(path="test_path", original_name="TestEnum", name="TestEnum"),
        fields=[
            DataModelField(
                name="NO_DEFAULT",
                data_type=DataType(type="str"),
                required=True,
            ),
            DataModelField(
                name="VALUE_A",
                default=EnumMemberValue("value_a"),
                data_type=DataType(type="str"),
                required=True,
            ),
            DataModelField(
                name="VALUE_B",
                default=EnumMemberValue("value_b"),
                data_type=DataType(type="str"),
                required=True,
            ),
            DataModelField(
                name="BARE",
                default=EnumMemberValue("bare value"),
                data_type=DataType(type="str"),
                required=True,
            ),
        ],
    )

    member = enum.find_member("bare value")
    assert member is not None
    assert member.field.name == "BARE"

    member = enum.find_member("value_a")
    assert member is not None
    assert member.field.name == "VALUE_A"
    assert member.value == "value_a"
    assert str(member.field.default) == "'value_a'"
    assert repr(member.field.default) == "'value_a'"

    member = enum.find_member("value_b")
    assert member is not None
    assert member.field.name == "VALUE_B"

    # A value that is only the quoted form of a member is a different value
    assert enum.find_member("'value_a'") is None


def test_find_member_with_mixed_enum() -> None:
    """Test find_member method with mixed type enum values."""
    from datamodel_code_generator.model.enum import Enum, EnumMemberValue
    from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

    enum = Enum(
        reference=Reference(path="test_path", original_name="TestEnum", name="TestEnum"),
        fields=[
            DataModelField(
                name="INT_VALUE",
                default="100",
                data_type=DataType(type="int"),
                required=True,
            ),
            DataModelField(
                name="STR_VALUE",
                default=EnumMemberValue("value_a"),
                data_type=DataType(type="str"),
                required=True,
            ),
        ],
    )

    member = enum.find_member(100)
    assert member is not None
    assert member.field.name == "INT_VALUE"

    assert enum.find_member("100") is None
    member = enum.find_member("100", coerce_strings=True)
    assert member is not None
    assert member.field.name == "INT_VALUE"

    member = enum.find_member("value_a")
    assert member is not None
    assert member.field.name == "STR_VALUE"

    assert enum.find_member("'value_a'") is None


@pytest.mark.usefixtures("parse_memo")
def test_find_member_indexes_preserve_json_semantics_and_invalidate() -> None:
    """Cache member lookup without changing order, coercion, or mutable-field behavior."""
    from datamodel_code_generator.model.enum import NULL_ENUM_MEMBER_VALUE, Enum, EnumMemberValue
    from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

    fields = [
        DataModelField(name="NO_DEFAULT", default=None, data_type=DataType(type="None"), required=True),
        DataModelField(name="NULL", default=NULL_ENUM_MEMBER_VALUE, data_type=DataType(type="None"), required=True),
        DataModelField(name="BOOL", default=True, data_type=DataType(type="bool"), required=True),
        DataModelField(name="INT", default=1, data_type=DataType(type="int"), required=True),
        DataModelField(name="STR", default=EnumMemberValue("1"), data_type=DataType(type="str"), required=True),
        DataModelField(
            name="NULL_TEXT", default=EnumMemberValue("None"), data_type=DataType(type="str"), required=True
        ),
        DataModelField(name="LIST", default=[1], data_type=DataType(type="list"), required=True),
    ]
    enum = Enum(fields=fields, reference=Reference(path="mixed", name="Mixed"))

    assert enum.find_member(True).field.name == "BOOL"  # ty: ignore[union-attr]
    null_member = enum.find_member(None)
    assert null_member.field.name == "NULL"  # ty: ignore[union-attr]
    assert null_member.value is None  # ty: ignore[union-attr]
    assert str(null_member.field.default) == "None"  # ty: ignore[union-attr]
    assert repr(null_member.field.default) == "None"  # ty: ignore[union-attr]
    assert enum.find_member("None").field.name == "NULL_TEXT"  # ty: ignore[union-attr]
    assert enum.find_member("None", coerce_strings=True).field.name == "NULL"  # ty: ignore[union-attr]
    assert enum.find_member(1.0).field.name == "INT"  # ty: ignore[union-attr]
    assert enum.find_member("1").field.name == "STR"  # ty: ignore[union-attr]
    assert enum.find_member("1", coerce_strings=True).field.name == "INT"  # ty: ignore[union-attr]
    assert enum.find_member([1]).field.name == "LIST"  # ty: ignore[union-attr]
    assert enum.find_member([2]) is None

    fields[1].default = EnumMemberValue("updated_null")
    fields[1].invalidate_semantic_caches()

    assert enum.find_member(None) is None
    assert enum.find_member("updated_null").field.name == "NULL"  # ty: ignore[union-attr]

    fields[4].default = EnumMemberValue("updated")
    fields[4].invalidate_semantic_caches()

    assert enum.find_member("1") is None
    assert enum.find_member("updated").field.name == "STR"  # ty: ignore[union-attr]


def test_discriminator_field_value_preserves_structured_string_quotes() -> None:
    """Treat quote characters in structured enum values as semantic data."""
    from datamodel_code_generator.model.enum import Enum, EnumMemberValue
    from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

    enum_reference = Reference(path="kind", name="Kind")
    Enum(
        fields=[
            DataModelField(
                name="QUOTED",
                default=EnumMemberValue("'quoted'"),
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=enum_reference,
    )
    discriminator_field = DataModelField(
        name="kind",
        data_type=DataType(reference=enum_reference),
    )

    assert _get_discriminator_field_value(discriminator_field) == "'quoted'"


@pytest.mark.parametrize(
    ("default", "expected"),
    [
        ("'legacy'", "legacy"),
        ("bare legacy value", "bare legacy value"),
    ],
)
def test_discriminator_field_value_accepts_legacy_enum_default(default: str, expected: str) -> None:
    """Keep rendered defaults from third-party parser subclasses compatible."""
    from datamodel_code_generator.model.enum import Enum
    from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

    enum_reference = Reference(path="kind", name="Kind")
    Enum(
        fields=[
            DataModelField(
                name="LEGACY",
                default=default,
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=enum_reference,
    )
    discriminator_field = DataModelField(
        name="kind",
        data_type=DataType(reference=enum_reference),
    )

    assert _get_discriminator_field_value(discriminator_field) == expected


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("\u0000", r"\x00"),  # Test null byte
        ("'", r"\'"),  # Test single quote
        ("\b", r"\b"),  # Test backspace
        ("\f", r"\f"),  # Test form feed
        ("\n", r"\n"),  # Test newline
        ("\r", r"\r"),  # Test carriage return
        ("\t", r"\t"),  # Test tab
        ("\\", r"\\"),  # Test backslash
    ],
)
def test_character_escaping(input_str: str, expected: str) -> None:
    """Test character escaping in strings."""
    assert input_str.translate(escape_characters) == expected


def test_parser_escape_characters_is_enum_compatibility_alias() -> None:
    """Keep the historical parser export bound to the canonical enum table."""
    from datamodel_code_generator.model.enum import escape_characters as enum_escape_characters

    assert escape_characters is enum_escape_characters


@pytest.mark.parametrize("flag", [True, False])
def test_use_non_positive_negative_number_constrained_types(flag: bool) -> None:
    """Test configuration of non-positive negative number constrained types."""
    instance = C(source="", use_non_positive_negative_number_constrained_types=flag)

    assert instance.data_type_manager.use_non_positive_negative_number_constrained_types == flag


def test_to_hashable_simple_values() -> None:
    """Test to_hashable with simple values."""
    assert to_hashable("string") == "string"
    assert to_hashable(123) == 123
    assert to_hashable(None) is None
    assert to_hashable(None) != to_hashable("")


def test_parser_base_reexports_internal_utils() -> None:
    """Test parser.base preserves public helper imports from the leaf module."""
    assert to_hashable is internal_utils.to_hashable
    assert get_most_of_parent is internal_utils.get_most_of_parent
    assert Child is internal_utils.Child
    assert HashableComparable is internal_utils.HashableComparable
    assert T is internal_utils.T


def test_get_most_of_parent_honors_type_filter() -> None:
    """Test parent traversal returns None when the requested type is absent."""
    child = SimpleNamespace(parent="root")

    assert get_most_of_parent(child, str) == "root"
    assert get_most_of_parent(child, int) is None


def test_get_most_of_parent_walks_plain_parent_attributes() -> None:
    """Test parent traversal with plain objects that expose a parent attribute."""
    root = SimpleNamespace()
    middle = SimpleNamespace(parent=root)
    child = SimpleNamespace(parent=middle)

    assert get_most_of_parent(child) is root
    assert get_most_of_parent(SimpleNamespace(parent=None)) is None


def test_to_hashable_list_and_tuple() -> None:
    """Test to_hashable with list and tuple."""
    result = to_hashable([3, 1, 2])
    assert isinstance(result, tuple)
    assert result == (1, 2, 3)  # sorted

    result = to_hashable((3, 1, 2))
    assert isinstance(result, tuple)
    assert result == (1, 2, 3)  # sorted


def test_to_hashable_dict() -> None:
    """Test to_hashable with dict."""
    result = to_hashable({"b": 2, "a": 1})
    assert isinstance(result, tuple)
    # sorted by key
    assert result == (("a", 1), ("b", 2))


def test_to_hashable_set() -> None:
    """Test to_hashable with set."""
    result = to_hashable({3, 1, 2})
    assert isinstance(result, frozenset)
    assert result == frozenset({1, 2, 3})


def test_to_hashable_pydantic_base_model() -> None:
    """Test to_hashable with pydantic BaseModel."""

    class Item(PydanticBaseModel):
        name: str
        tags: set[str]

    result = to_hashable(Item(name="item", tags={"blue", "red"}))
    assert result == (("name", "item"), ("tags", frozenset({"blue", "red"})))


def test_to_hashable_mixed_types_fallback() -> None:
    """Test to_hashable with mixed types that cannot be compared."""
    mixed_list = [complex(1, 2), complex(3, 4)]
    result = to_hashable(mixed_list)
    assert isinstance(result, tuple)
    # Should preserve order since sorting fails
    assert result == (complex(1, 2), complex(3, 4))


def test_to_hashable_nested_structures() -> None:
    """Test to_hashable with nested structures."""
    nested = {"outer": [{"inner": 1}]}
    result = to_hashable(nested)
    assert isinstance(result, tuple)


def test_postprocess_result_modules_single_element_tuple() -> None:
    """Test postprocessing with single element tuple (len < 2)."""
    input_data = {
        ("__init__.py",): "init_content",
    }
    result = Parser._Parser__postprocess_result_modules(input_data)
    # Single element tuple should remain unchanged
    assert ("__init__.py",) in result


def test_postprocess_result_modules_single_file_no_dot() -> None:
    """Test postprocessing with single file without dot in name."""
    input_data = {
        ("module.py",): "content",
        ("__init__.py",): "init_content",
    }
    result = Parser._Parser__postprocess_result_modules(input_data)
    assert ("module.py",) in result


def test_postprocess_result_modules_single_element_no_dot() -> None:
    """Test postprocessing with single element without dot (len(r) < 2 branch)."""
    input_data = {
        ("__init__.py",): "init_content",
        ("file",): "content",  # Single element without dot, so len(r) = 1
    }
    result = Parser._Parser__postprocess_result_modules(input_data)
    assert ("file",) in result


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ((), ()),  # empty
        (("pkg",), ("pkg",)),  # single
        (("pkg", "issuing"), ("pkg",)),  # submodule
        (("foo", "bar", "baz"), ("foo", "bar")),  # deeply nested
    ],
    ids=["empty", "single", "submodule", "deeply_nested"],
)
def test_get_module_directory(module: tuple[str, ...], expected: tuple[str, ...]) -> None:
    """Test get_module_directory with various inputs."""
    assert get_module_directory(module) == expected


@pytest.mark.parametrize(
    ("scc_modules", "existing_modules", "expected"),
    [
        # name conflict: _internal already exists
        ({(), ("sub",)}, {("_internal",)}, ("_internal_1",)),
        # multiple conflicts: _internal and _internal_1 exist
        ({(), ("sub",)}, {("_internal",), ("_internal_1",)}, ("_internal_2",)),
        # different prefix break: LCP computation hits break
        ({("common", "a"), ("common", "b"), ("other", "x")}, set(), ("_internal",)),
    ],
    ids=["name_conflict", "multiple_conflicts", "different_prefix_break"],
)
def test_compute_internal_module_path(
    scc_modules: set[tuple[str, ...]],
    existing_modules: set[tuple[str, ...]],
    expected: tuple[str, ...],
) -> None:
    """Test compute_internal_module_path with various conflict scenarios."""
    result = structure.compute_internal_module_path(scc_modules, existing_modules)
    assert result == expected


def test_module_dependency_cycles_with_missing_ref(parser_fixture: C) -> None:
    """Test module_dependency_cycles when reference path is not in path_to_module."""
    missing_reference = Reference(path="nonexistent.Model", original_name="Missing", name="Missing")
    model1 = BaseModel(
        fields=[DataModelField(data_type=DataType(reference=missing_reference))],
        reference=Reference(path="pkg.Model1", original_name="Model1", name="Model1"),
    )
    parser_fixture.generation_store.register_model(model1)

    module_models_list = [
        (("pkg",), [model1]),
    ]

    assert structure.module_dependency_cycles(parser_fixture, module_models_list) == []


def test_collapse_root_models_preserves_root_model_used_by_other_modules() -> None:
    """Root collapse should not mark a root model unused while another model still references it."""
    parser = C(
        data_model_type=BaseModel,
        data_model_root_type=RootModel,
        data_model_field_type=DataModelField,
        base_class="Base",
        source="",
    )
    parser.collapse_root_models = True
    parser.collapse_root_models_name_strategy = CollapseRootModelsNameStrategy.Child
    root_reference = Reference(path="Root", original_name="Root", name="Root")
    inner_reference = Reference(path="Inner", original_name="Inner", name="Inner")
    root_model = RootModel(
        fields=[DataModelField(data_type=DataType(reference=inner_reference))],
        reference=root_reference,
    )
    local_type = DataType(reference=root_reference)
    local_model = BaseModel(
        fields=[DataModelField(data_type=local_type)],
        reference=Reference(path="pkg.Local", original_name="Local", name="Local"),
    )
    external_type = DataType(reference=root_reference)
    external_model = BaseModel(
        fields=[DataModelField(data_type=external_type)],
        reference=Reference(path="other.External", original_name="External", name="External"),
    )
    for model in (root_model, local_model, external_model):
        parser.generation_store.register_model(model)
    unused_models: list[DataModel] = []

    modules.collapse_root_models(parser, [local_model], unused_models, Imports(), parser.model_resolver)

    assert local_type.reference is inner_reference
    assert external_type.reference is root_reference
    assert unused_models == []


def test_collapse_root_models_uses_model_module_names_without_scope_map() -> None:
    """Direct parser callers can collapse scalar roots without tree-scope planning."""
    parser = C(
        data_model_type=BaseModel,
        data_model_root_type=RootModel,
        data_model_field_type=DataModelField,
        base_class="Base",
        source="",
    )
    parser.collapse_root_models = True
    root_reference = Reference(path="Root", original_name="Root", name="Root")
    root_model = RootModel(
        fields=[DataModelField(data_type=DataType(type="str"))],
        reference=root_reference,
    )
    local_type = DataType(reference=root_reference)
    local_model = BaseModel(
        fields=[DataModelField(data_type=local_type)],
        reference=Reference(path="Local", original_name="Local", name="Local"),
    )
    for model in (root_model, local_model):
        parser.generation_store.register_model(model)

    modules.collapse_root_models(parser, [local_model], [], Imports(), parser.model_resolver)

    assert local_model.fields[0].data_type.type == "str"


def test_restore_inherited_field_names_handles_noncollisions_and_existing_aliases() -> None:
    """Field restoration handles both exhausted searches and pre-aliased collisions."""
    inherited = MsgspecField(
        name="inherited_name",
        original_name="wire_name",
        data_type=DataType(type="str"),
    )
    unmatched = MsgspecField(
        name="generated_name",
        original_name="wire_name",
        data_type=DataType(type="str"),
    )
    unrelated = MsgspecField(
        name="other_name",
        original_name="other_wire_name",
        data_type=DataType(type="str"),
    )

    builder = GenerationStore().builder()
    assert not _restore_inherited_field_names_for_unique_aliases(
        builder,
        [unmatched, unrelated],
        {"wire_name": inherited},
    )

    colliding = MsgspecField(
        name="inherited_name",
        original_name="collision_wire_name",
        alias="existing_alias",
        data_type=DataType(type="str"),
    )
    assert _restore_inherited_field_names_for_unique_aliases(
        builder,
        [unmatched, colliding],
        {"wire_name": inherited},
    )
    assert (unmatched.name, colliding.name, colliding.alias) == (
        "inherited_name",
        "generated_name",
        "existing_alias",
    )


def test_root_dict_key_reference_paths_ignores_primitive_keys() -> None:
    """Primitive mapping keys do not create circular model-reference edges."""
    model = BaseModel(
        fields=[
            DataModelField(
                name="values",
                data_type=DataType(
                    is_dict=True,
                    data_types=[DataType(type="int")],
                    dict_key=DataType(type="str"),
                ),
            )
        ],
        reference=_reference("Model"),
    )

    assert not plans.root_dict_key_references(model)


def test_finalize_modules_plans_schema_helpers_after_root_collapse() -> None:
    """Attach shared helper imports to a surviving runtime model after a root model is removed."""
    parser = C(
        data_model_type=BaseModel,
        data_model_root_type=RootModel,
        data_model_field_type=DataModelField,
        base_class="Base",
        source="",
        generate_schema_validators=True,
    )
    collapsed_root = RootModel(
        fields=[DataModelField(data_type=DataType(type="str"))],
        reference=Reference(path="Root", original_name="Root", name="Root"),
    )
    collapsed_root._set_internal_template_data(
        "schema_runtime_validation",
        _make_internal_schema_runtime_validation(property_count=PropertyCountRule(min_properties=1)),
    )
    collapsed_root.extra_template_data["schema_runtime_validation_enabled"] = True
    surviving_model = BaseModel(
        fields=[
            DataModelField(
                name="external",
                data_type=DataType.from_import(Import.from_full_path("external._JsonSchemaRuntimeValidationBaseCore")),
            )
        ],
        reference=Reference(
            path="Surviving",
            original_name="_JsonSchemaRuntimeValidationBaseCore",
            name="_JsonSchemaRuntimeValidationBaseCore",
        ),
    )
    surviving_model._set_internal_template_data(
        "schema_runtime_validation",
        _make_internal_schema_runtime_validation(
            required_groups=[RequiredGroupsRule(keyword="oneOf", groups=((("external",),),))],
            property_count=PropertyCountRule(min_properties=1),
        ),
    )
    surviving_model.extra_template_data["schema_runtime_validation_enabled"] = True
    models = [collapsed_root, surviving_model]
    imports = Imports()
    context = ModuleContext(
        module=("output.py",),
        module_key=("output.py",),
        models=models,
        is_init=False,
        imports=imports,
        scoped_model_resolver=parser.model_resolver,
    )

    parser._finalize_modules(
        [context],
        [collapsed_root],
        {collapsed_root: (("output.py",), models)},
        {("output.py",): imports},
    )

    assert models == [surviving_model]
    assert surviving_model.class_name != "_JsonSchemaRuntimeValidationBaseCore"
    assert Import.from_full_path("pydantic.model_validator") in surviving_model.imports
    rendered_module_code = BaseModel.render_module_code(models)
    assert "class _JsonSchemaRuntimeValidationBaseCore(BaseModel):" in rendered_module_code
    assert "class _JsonSchemaRuntimeValidationBase(_JsonSchemaRuntimeValidationBaseCore):" in rendered_module_code


def test_unwrap_type_alias_stops_on_recursive_alias() -> None:
    """Test _unwrap_type_alias stops when a type alias resolves back to itself."""
    alias_reference = Reference(path="Alias", original_name="Alias", name="Alias")
    alias_data_type = DataType(reference=alias_reference)
    alias_model = TypeStatement(fields=[DataModelField(data_type=alias_data_type)], reference=alias_reference)

    assert alias_reference.source is alias_model
    assert _unwrap_type_alias(alias_data_type) is alias_data_type


def test_contains_model_reference_traverses_nested_data_types() -> None:
    """Test _contains_model_reference walks nested container data types."""
    model_reference = Reference(path="Item", original_name="Item", name="Item")
    BaseModel(fields=[], reference=model_reference)

    list_of_models = DataType(
        is_list=True,
        data_types=[DataType(reference=model_reference)],
        use_union_operator=True,
    )

    assert _contains_model_reference(list_of_models) is True


def test_contains_model_reference_traverses_dict_key() -> None:
    """Test _contains_model_reference walks dict_key data types."""
    model_reference = Reference(path="KeyModel", original_name="KeyModel", name="KeyModel")
    BaseModel(fields=[], reference=model_reference)

    dict_with_model_key = DataType(
        is_dict=True,
        dict_key=DataType(reference=model_reference),
        use_union_operator=True,
    )

    assert _contains_model_reference(dict_with_model_key) is True


def test_needs_validate_default_for_union_type_alias() -> None:
    """Test _needs_validate_default resolves type aliases before checking union branches."""
    a_reference = Reference(path="A", original_name="A", name="A")
    b_reference = Reference(path="B", original_name="B", name="B")
    BaseModel(fields=[], reference=a_reference)
    BaseModel(fields=[], reference=b_reference)

    union_data_type = DataType(
        data_types=[DataType(reference=a_reference), DataType(reference=b_reference)],
        use_union_operator=True,
    )
    alias_reference = Reference(path="Alias", original_name="Alias", name="Alias")
    TypeStatement(fields=[DataModelField(data_type=union_data_type)], reference=alias_reference)

    assert _needs_validate_default(DataType(reference=alias_reference)) is True


def test_needs_validate_default_for_optional_single_model_union() -> None:
    """Test _needs_validate_default returns True for A | None union."""
    model_reference = Reference(path="A", original_name="A", name="A")
    BaseModel(fields=[], reference=model_reference)

    optional_model_union = DataType(
        data_types=[DataType(reference=model_reference), DataType(type="None")],
        use_union_operator=True,
    )

    assert _needs_validate_default(optional_model_union) is True


def test_get_enum_discriminator_literal_with_escaped_value() -> None:
    """Test discriminator literals use the runtime enum value, not its escaped source."""
    from datamodel_code_generator.model.enum import Enum, EnumMemberValue
    from datamodel_code_generator.model.pydantic_v2.base_model import DataModelField
    from datamodel_code_generator.parser.base import Parser
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

    enum = Enum(
        reference=Reference(path="test_path", original_name="TestEnum", name="TestEnum"),
        fields=[
            DataModelField(
                name="DON_T",
                default=EnumMemberValue("don't"),
                data_type=DataType(type="str"),
                required=True,
            ),
        ],
    )

    assert Parser._get_enum_discriminator_literal(enum, "don't") == "don't"
    assert Parser._get_enum_discriminator_literal(enum, "missing") == "missing"
