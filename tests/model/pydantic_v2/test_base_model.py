"""Tests for Pydantic v2 BaseModel generation helpers."""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections import defaultdict
from itertools import product
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, Formatter, PythonVersion
from datamodel_code_generator.config import JSONSchemaParserConfig
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import _rebuild_model_with_datamodel_namespace, get_data_model_types
from datamodel_code_generator.model.pydantic_v2 import ConfigDict
from datamodel_code_generator.model.pydantic_v2._schema_runtime_validation import (
    plan_schema_runtime_validation_bases,
)
from datamodel_code_generator.model.pydantic_v2.base_model import (
    BaseModel,
    Constraints,
    DataModelField,
    _construct_parser_simple_field,
)
from datamodel_code_generator.model.pydantic_v2.root_model import RootModel
from datamodel_code_generator.model.runtime_validation import (
    PatternPropertiesRule,
    PropertyCountRule,
    RequiredGroupsRule,
    SchemaRuntimeValidation,
    UniqueItemsRule,
    _make_internal_schema_runtime_validation,
)
from datamodel_code_generator.parser.base import Parser, _get_builtin_pydantic_v2_field_constructor
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.reference import Reference, model_key_of
from datamodel_code_generator.types import DataType
from tests.conftest import assert_output

EXPECTED_PYDANTIC_V2_MODEL_PATH = Path(__file__).parents[2] / "data" / "expected" / "model" / "pydantic_v2"
JSON_SCHEMA_DATA_PATH = Path(__file__).parents[2] / "data" / "jsonschema"


def _schema_runtime_validation() -> SchemaRuntimeValidation:
    return _make_internal_schema_runtime_validation(
        required_groups=[
            RequiredGroupsRule(
                keyword="oneOf",
                groups=((("value",),),),
            )
        ]
    )


def _property_count_schema_runtime_validation() -> SchemaRuntimeValidation:
    return _make_internal_schema_runtime_validation(
        property_count=PropertyCountRule(min_properties=1),
    )


def test_base_model_methods_render_once_after_all_fields() -> None:
    """Render model methods once after every field declaration."""
    model = BaseModel(
        fields=[
            DataModelField(name="first", data_type=DataType(type="str"), required=True),
            DataModelField(name="second", data_type=DataType(type="int"), required=True),
        ],
        reference=Reference(name="Model", path="Model"),
    )
    model.methods.append('def generated_method(self) -> str:\n        return "ok"')

    rendered = model.render()

    assert_output(
        f"method count: {rendered.count('def generated_method(')}\n\n{rendered}\n",
        EXPECTED_PYDANTIC_V2_MODEL_PATH / "base_model_methods_once.txt",
    )


def test_schema_runtime_validation_unique_items_follows_model_description() -> None:
    """Render uniqueItems metadata after ordinary class-body template data."""
    model = BaseModel(
        fields=[DataModelField(name="values", data_type=DataType(type="str"), required=True)],
        reference=Reference(name="UniqueItems", path="#/UniqueItems"),
        description="Unique values.",
        extra_template_data=defaultdict(
            dict,
            {
                "#/UniqueItems": {
                    "schema_runtime_validation": _make_internal_schema_runtime_validation(
                        unique_items=[UniqueItemsRule(path=(("values",),))]
                    )
                }
            },
        ),
    )

    expected = EXPECTED_PYDANTIC_V2_MODEL_PATH / "schema_runtime_unique_items_description.txt"
    assert_output(model.render(), expected)

    model._process_schema_runtime_validation()
    model.invalidate_render_caches()

    assert_output(model.render(), expected)


@pytest.mark.allow_direct_assert
def test_schema_runtime_validation_base_inheritance_detects_transitive_base() -> None:
    """Detect inherited runtime validation bases through non-runtime intermediate models."""
    runtime_base = BaseModel(
        fields=[],
        reference=Reference(name="RuntimeBase", path="#/RuntimeBase"),
        extra_template_data=defaultdict(
            dict,
            {"#/RuntimeBase": {"schema_runtime_validation": _schema_runtime_validation()}},
        ),
    )

    intermediate = BaseModel(
        fields=[],
        reference=Reference(name="Intermediate", path="#/Intermediate"),
        base_classes=[runtime_base.reference],
    )
    runtime_leaf = BaseModel(
        fields=[],
        reference=Reference(name="RuntimeLeaf", path="#/RuntimeLeaf"),
        base_classes=[intermediate.reference],
    )

    assert BaseModel._inherits_schema_runtime_validation_base(runtime_leaf, seen=set())
    assert not BaseModel._inherits_schema_runtime_validation_base(
        runtime_leaf,
        seen={model_key_of(runtime_leaf.reference)},
    )


@pytest.mark.allow_direct_assert
def test_schema_runtime_validation_helpers_are_gated_by_parser_option() -> None:
    """Avoid scanning and rendering runtime helpers for the normal Pydantic v2 path."""
    runtime_model = BaseModel(
        fields=[],
        reference=Reference(name="RuntimeModel", path="#/RuntimeModel"),
        extra_template_data=defaultdict(
            dict,
            {"#/RuntimeModel": {"schema_runtime_validation": _schema_runtime_validation()}},
        ),
    )

    assert not BaseModel.render_module_code([runtime_model])

    runtime_model.extra_template_data["schema_runtime_validation_enabled"] = True

    assert "_JsonSchemaRuntimeValidationBase" in BaseModel.render_module_code([runtime_model])


@pytest.mark.allow_direct_assert
def test_schema_runtime_validation_module_plan_skips_leading_generic_model() -> None:
    """Find opt-in runtime metadata after an unannotated generated base model."""
    generic_model = BaseModel.create_base_class_model(
        config={},
        reference=Reference(name="BaseModel", path="#/BaseModel"),
    )
    assert generic_model is not None
    runtime_model = BaseModel(
        fields=[],
        reference=Reference(name="RuntimeModel", path="#/RuntimeModel"),
        extra_template_data=defaultdict(
            dict,
            {"#/RuntimeModel": {"schema_runtime_validation": _schema_runtime_validation()}},
        ),
    )
    runtime_model.extra_template_data["schema_runtime_validation_enabled"] = True
    models = [generic_model, runtime_model]

    BaseModel.prepare_module_code(models)
    first = BaseModel.render_module_code(models)
    BaseModel.invalidate_module_code_cache(models)
    BaseModel.prepare_module_code(models)
    second = BaseModel.render_module_code(models)

    assert "class _JsonSchemaRuntimeValidationBase(BaseModel):" in first
    assert "class RuntimeModel(_JsonSchemaRuntimeValidationBase):" in runtime_model.render()
    assert first == second


@pytest.mark.allow_direct_assert
def test_property_count_class_body_line_is_idempotent() -> None:
    """Render repeated module code without duplicating a model's property-count rule."""
    runtime_validation = _make_internal_schema_runtime_validation(
        pattern_properties=[
            PatternPropertiesRule(
                declared_properties=(),
                pattern_properties=(("^value", DataType(type="str")),),
            )
        ],
        property_count=PropertyCountRule(min_properties=1),
        unique_items=[UniqueItemsRule(path=())],
    )
    runtime_model = BaseModel(
        fields=[],
        reference=Reference(name="RuntimeModel", path="#/RuntimeModel"),
        extra_template_data=defaultdict(
            dict,
            {"#/RuntimeModel": {"schema_runtime_validation": runtime_validation}},
        ),
    )
    runtime_model.extra_template_data["schema_runtime_validation_enabled"] = True

    first = BaseModel.render_module_code([runtime_model])
    runtime_model._process_schema_runtime_validation()
    runtime_model.invalidate_render_caches()
    second = BaseModel.render_module_code([runtime_model])

    assert first == second
    assert runtime_model.render().count("__json_schema_property_count_rule__") == 1
    assert runtime_model.render().count("__json_schema_unique_items__") == 1


@pytest.mark.allow_direct_assert
def test_runtime_root_model_extra_config_is_idempotent() -> None:
    """Merge the neutral extra setting into an existing RootModel config once."""
    plain_runtime_model = RootModel(
        fields=[DataModelField(name="root", data_type=DataType(type="int"), required=True)],
        reference=Reference(name="PlainRuntimeRoot", path="#/PlainRuntimeRoot"),
    )
    plain_runtime_model._internal_template_data["schema_runtime_validation_use_base"] = True
    runtime_model = RootModel(
        fields=[DataModelField(name="root", data_type=DataType(type="str"), required=True)],
        reference=Reference(name="RuntimeRoot", path="#/RuntimeRoot"),
        extra_template_data=defaultdict(
            dict,
            {"#/RuntimeRoot": {"config": ConfigDict(regex_engine='"python-re"', frozen=True)}},
        ),
    )
    runtime_model._internal_template_data["schema_runtime_validation_use_base"] = True

    BaseModel._neutralize_generic_extra_config_for_runtime_root_models(
        [plain_runtime_model, runtime_model],
        uses_generated_generic_base_class=True,
    )
    BaseModel._neutralize_generic_extra_config_for_runtime_root_models(
        [plain_runtime_model, runtime_model],
        uses_generated_generic_base_class=True,
    )

    assert plain_runtime_model._additional_imports.count(Import.from_full_path("pydantic.ConfigDict")) == 1
    plain_rendered = plain_runtime_model.render()
    assert plain_rendered.count("model_config = ConfigDict(") == 1
    assert "extra=None," in plain_rendered
    assert runtime_model._additional_imports.count(Import.from_full_path("pydantic.ConfigDict")) == 1
    rendered = runtime_model.render()
    assert rendered.count("model_config = ConfigDict(") == 1
    assert 'regex_engine="python-re",' in rendered
    assert "frozen=True," in rendered
    assert "extra=None," in rendered


@pytest.mark.allow_direct_assert
def test_schema_runtime_validation_module_plan_tracks_renamed_models() -> None:
    """Replan synthetic helper names after any model in the module is renamed."""
    runtime_model = BaseModel(
        fields=[],
        reference=Reference(name="RuntimeModel", path="#/RuntimeModel"),
        extra_template_data=defaultdict(
            dict,
            {
                "#/RuntimeModel": {
                    "schema_runtime_validation": _make_internal_schema_runtime_validation(
                        required_groups=_schema_runtime_validation().required_groups,
                        property_count=PropertyCountRule(min_properties=1),
                    )
                }
            },
        ),
    )
    colliding_model = BaseModel(
        fields=[],
        reference=Reference(
            name="_JsonSchemaRuntimeValidationBaseCore",
            path="#/JsonSchemaRuntimeValidationBaseCore",
        ),
    )
    runtime_model.extra_template_data["schema_runtime_validation_enabled"] = True

    first = BaseModel.render_module_code([runtime_model, colliding_model])
    colliding_model.class_name = "RenamedCollision"
    BaseModel.invalidate_module_code_cache([runtime_model, colliding_model])
    second = BaseModel.render_module_code([runtime_model, colliding_model])

    assert "class _JsonSchemaRuntimeValidationBaseCore2(BaseModel):" in first
    assert "class _JsonSchemaRuntimeValidationBaseCore(BaseModel):" in second
    assert "_JsonSchemaRuntimeValidationBaseCore2" not in second
    assert "class RenamedCollision(BaseModel):" in colliding_model.render()
    assert BaseModel.invalidate_module_code_cache([]) is None


@pytest.mark.allow_direct_assert
def test_property_count_inherited_capability_skips_unused_local_helper() -> None:
    """Avoid emitting a local helper when an external grandparent already supplies it."""
    property_parent = BaseModel(
        fields=[],
        reference=Reference(name="PropertyParent", path="#/PropertyParent"),
        extra_template_data=defaultdict(
            dict,
            {"#/PropertyParent": {"schema_runtime_validation": _property_count_schema_runtime_validation()}},
        ),
    )
    intermediate = BaseModel(
        fields=[],
        reference=Reference(name="Intermediate", path="#/Intermediate"),
        base_classes=[property_parent.reference],
    )
    property_child = BaseModel(
        fields=[],
        reference=Reference(name="PropertyChild", path="#/PropertyChild"),
        base_classes=[intermediate.reference],
        extra_template_data=defaultdict(
            dict,
            {"#/PropertyChild": {"schema_runtime_validation": _property_count_schema_runtime_validation()}},
        ),
    )
    property_child.extra_template_data["schema_runtime_validation_enabled"] = True

    assert not BaseModel.render_module_code([property_child])
    assert not property_child._internal_template_data["schema_runtime_validation_use_base"]


@pytest.mark.allow_direct_assert
def test_schema_runtime_validation_base_planner_handles_cyclic_models() -> None:
    """Stop capability planning at a malformed generated-model inheritance cycle."""

    class RuntimeModel:
        def __init__(self) -> None:
            self.base_models: list[RuntimeModel] = []

    first = RuntimeModel()
    second = RuntimeModel()
    first.base_models = [second]
    second.base_models = [first]
    runtime_validation = _schema_runtime_validation()

    result = plan_schema_runtime_validation_bases(
        [first],
        {id(first): runtime_validation, id(second): runtime_validation},
        get_base_models=lambda model: model.base_models,
        get_external_capabilities=lambda _model: (False, False),
        get_model_requirements=lambda _model, _validation: (True, False),
    )

    assert result == {id(first): (False, False), id(second): (True, False)}


@pytest.mark.allow_direct_assert
def test_property_count_inherits_external_core_pattern_type_import() -> None:
    """Keep local pattern class-variable types importable without a local core helper."""
    external_core = BaseModel(
        fields=[],
        reference=Reference(name="ExternalCore", path="#/ExternalCore"),
        extra_template_data=defaultdict(
            dict,
            {
                "#/ExternalCore": {
                    "schema_runtime_validation": _make_internal_schema_runtime_validation(
                        pattern_properties=[
                            PatternPropertiesRule(
                                declared_properties=(),
                                pattern_properties=(("^value", DataType(type="str")),),
                            )
                        ]
                    )
                }
            },
        ),
    )
    imported_type = DataType.from_import(Import.from_full_path("external_types.ExternalValue"))
    property_child = BaseModel(
        fields=[],
        reference=Reference(name="PropertyChild", path="#/PropertyChild"),
        base_classes=[external_core.reference],
        extra_template_data=defaultdict(
            dict,
            {
                "#/PropertyChild": {
                    "schema_runtime_validation": _make_internal_schema_runtime_validation(
                        pattern_properties=[
                            PatternPropertiesRule(
                                declared_properties=(),
                                pattern_properties=(("^value", imported_type),),
                            )
                        ],
                        property_count=PropertyCountRule(min_properties=1),
                    )
                }
            },
        ),
    )
    property_child.extra_template_data["schema_runtime_validation_enabled"] = True

    BaseModel.render_module_code([property_child])

    assert Import.from_full_path("external_types.ExternalValue") in property_child.imports


@pytest.mark.allow_direct_assert
def test_property_count_core_helper_name_avoids_model_collision() -> None:
    """Keep a synthetic mixed-runtime helper distinct from generated or imported base names."""
    colliding_model = BaseModel(
        fields=[],
        reference=Reference(
            name="_JsonSchemaRuntimeValidationBaseCore",
            path="#/JsonSchemaRuntimeValidationBaseCore",
        ),
    )
    external_model = BaseModel(
        fields=[],
        reference=Reference(
            name="_JsonSchemaRuntimeValidationBaseCore2",
            path="#/ExternalJsonSchemaRuntimeValidationBaseCore2",
        ),
    )
    inheriting_model = BaseModel(
        fields=[],
        reference=Reference(name="InheritingModel", path="#/InheritingModel"),
        base_classes=[external_model.reference],
    )
    imported_model_class = BaseModel(
        fields=[],
        reference=Reference(
            name="_JsonSchemaRuntimeValidationBaseCore3",
            path="#/ExternalJsonSchemaRuntimeValidationBaseCore3",
        ),
    )
    imported_model = BaseModel(
        fields=[
            DataModelField(
                name="external",
                data_type=DataType(reference=imported_model_class.reference),
                required=True,
            )
        ],
        reference=Reference(name="ImportedModel", path="#/ImportedModel"),
    )

    assert (
        BaseModel._get_unique_schema_runtime_validation_base_class_name(
            "_JsonSchemaRuntimeValidationBase",
            "Core",
            [colliding_model, inheriting_model, imported_model],
        )
        == "_JsonSchemaRuntimeValidationBaseCore4"
    )


@pytest.mark.allow_direct_assert
def test_parser_simple_field_matches_validated_field() -> None:
    """Keep every fast-constructor field and private slot equivalent to Pydantic."""
    representative_values = {
        "name": "item_name",
        "default": "'item'",
        "required": True,
        "alias": "item-name",
        "validation_aliases": ["item_name", "item-name"],
        "serialization_alias": "itemName",
        "constraints": None,
        "strip_default_none": True,
        "order_constructor_fields_by_written_defaults": True,
        "nullable": True,
        "parent": None,
        "extras": {},
        "use_annotated": True,
        "use_serialize_as_any": True,
        "has_default": True,
        "use_field_description": True,
        "use_field_description_example": True,
        "use_inline_field_description": True,
        "const": False,
        "original_name": "item-name",
        "use_default_kwarg": True,
        "use_missing_sentinel": True,
        "use_one_literal_as_default": True,
        "target_python_version": PythonVersion.PY_313,
        "type_has_null": True,
        "read_only": True,
        "write_only": True,
        "use_frozen_field": True,
        "use_serialization_alias": True,
        "use_default_factory_for_optional_nested_models": True,
        "use_default_with_required": True,
    }
    assert representative_values.keys() | {"data_type"} == DataModelField.model_fields.keys()
    assert {
        name for name, field_info in DataModelField.model_fields.items() if field_info.default_factory is not None
    } == {"extras"}

    pydantic_slots = {
        slot
        for model_type in DataModelField.__mro__
        for slot in getattr(model_type, "__slots__", ())
        if slot.startswith("__pydantic_")
    }
    for values in ({}, representative_values):
        data_type = DataType(type="str")
        validated = DataModelField(**values, data_type=data_type)
        constructed = _construct_parser_simple_field(**values, data_type=data_type)

        assert constructed.__dict__ == validated.__dict__
        assert {slot: getattr(constructed, slot) for slot in pydantic_slots} == {
            slot: getattr(validated, slot) for slot in pydantic_slots
        }
        assert constructed.model_dump() == validated.model_dump()
        assert constructed.model_dump(exclude_unset=True) == validated.model_dump(exclude_unset=True)
        assert repr(constructed) == repr(validated)
        assert constructed.type_hint == validated.type_hint
        assert constructed.field == validated.field


@pytest.mark.allow_direct_assert
def test_parser_simple_field_preserves_parent_and_fresh_extras() -> None:
    """Set reference parents and never share the mutable extras default."""
    first_type = DataType(reference=Reference(path="#/First", name="First"))
    second_type = DataType(reference=Reference(path="#/Second", name="Second"))
    first = _construct_parser_simple_field(data_type=first_type)
    second = _construct_parser_simple_field(data_type=second_type)

    assert first_type.parent is first
    assert second_type.parent is second
    assert first.extras == second.extras == {}
    assert first.extras is not second.extras


@pytest.mark.allow_direct_assert
def test_parser_simple_field_falls_back_for_processed_values() -> None:
    """Retain Pydantic validation for constraints, extras, and const fields."""
    constrained = _construct_parser_simple_field(data_type=DataType(type="str"), constraints={})
    with_extras = _construct_parser_simple_field(
        data_type=DataType(type="str"),
        extras={"example": "sample"},
    )
    const = _construct_parser_simple_field(
        data_type=DataType(type="str"),
        extras={"const": "fixed"},
    )

    assert isinstance(constrained.constraints, Constraints)
    assert with_extras.extras == {"examples": ["sample"]}
    assert const.const
    assert const.data_type.literals == ["fixed"]


@pytest.mark.allow_direct_assert
def test_parser_simple_field_constructor_requires_exact_builtin_type() -> None:
    """Keep custom field subclasses on their public constructors."""

    class CustomDataModelField(DataModelField):
        pass

    assert _get_builtin_pydantic_v2_field_constructor(DataModelField) is _construct_parser_simple_field
    assert _get_builtin_pydantic_v2_field_constructor(CustomDataModelField) is None


@pytest.mark.allow_direct_assert
def test_parser_simple_field_call_keywords_match_model_fields() -> None:
    """Move future parser keyword audits out of the per-field runtime path."""
    parser_tree = ast.parse(textwrap.dedent(inspect.getsource(JsonSchemaParser)))
    constructor_calls = [
        node
        for node in ast.walk(parser_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_data_model_field_constructor"
    ]
    assert len(constructor_calls) == 2

    constructor_keywords: set[str] = set()
    for call in constructor_calls:
        expansions = [keyword.value for keyword in call.keywords if keyword.arg is None]
        assert len(expansions) == 1
        match expansions[0]:
            case ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id="self"),
                    attr="_data_model_field_common_kwargs",
                )
            ):
                pass
            case expansion:  # pragma: no cover
                pytest.fail(f"Unexpected field-constructor kwargs expansion: {ast.unparse(expansion)}")
        constructor_keywords.update(keyword.arg for keyword in call.keywords if keyword.arg is not None)

    init_tree = ast.parse(textwrap.dedent(inspect.getsource(Parser.__init__)))
    common_kwargs_assignments = [
        node.value
        for node in ast.walk(init_tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Attribute)
        and isinstance(node.target.value, ast.Name)
        and node.target.value.id == "self"
        and node.target.attr == "_data_model_field_common_kwargs_cache"
    ]
    assert len(common_kwargs_assignments) == 1
    assignment = common_kwargs_assignments[0]
    if not isinstance(assignment, ast.Dict) or not all(
        isinstance(key, ast.Constant) and isinstance(key.value, str) for key in assignment.keys
    ):  # pragma: no cover
        pytest.fail(f"Common field kwargs must stay a static dict: {ast.unparse(assignment)}")
    constructor_keywords.update(key.value for key in assignment.keys if isinstance(key, ast.Constant))

    assert "data_type" in constructor_keywords
    assert constructor_keywords <= DataModelField.model_fields.keys()


@pytest.mark.parametrize(
    ("use_missing_sentinel", "expected_file"),
    [
        pytest.param(False, "simple_pydantic_v2_field_states.snapshot", id="standard-default"),
        pytest.param(True, "simple_pydantic_v2_field_states_missing.snapshot", id="missing-sentinel"),
    ],
)
@pytest.mark.allow_direct_assert
def test_parser_simple_field_matches_public_constructor_for_schema_states(
    use_missing_sentinel: bool,
    expected_file: str,
) -> None:
    """Compare actual parser states with public validation across field-state pairs."""

    class PublicDataModelField(DataModelField):
        pass

    _rebuild_model_with_datamodel_namespace(PublicDataModelField)
    model_types = get_data_model_types(DataModelType.PydanticV2BaseModel)
    parser_kwargs = {
        "source": JSON_SCHEMA_DATA_PATH / "simple_pydantic_v2_field_states.json",
        "data_model_type": model_types.data_model,
        "data_model_root_type": model_types.root_model,
        "data_type_manager_type": model_types.data_type_manager,
        "dump_resolve_reference_action": model_types.dump_resolve_reference_action,
        "strict_nullable": True,
        "use_missing_sentinel": use_missing_sentinel,
        "apply_default_values_for_required_fields": True,
        "formatters": [Formatter.BUILTIN],
    }
    fast_parser = JsonSchemaParser(data_model_field_type=DataModelField, **parser_kwargs)
    public_parser = JsonSchemaParser(data_model_field_type=PublicDataModelField, **parser_kwargs)

    expected_path = EXPECTED_PYDANTIC_V2_MODEL_PATH / expected_file
    assert_output(fast_parser.parse(), expected_path)
    assert_output(public_parser.parse(), expected_path)

    fast_fields = {field.name: field for model in fast_parser.results for field in model.fields}
    public_fields = {field.name: field for model in public_parser.results for field in model.fields}
    assert fast_fields.keys() == public_fields.keys()
    assert {
        (field.required, field.nullable, field.has_default, field.use_missing_sentinel)
        for field in fast_fields.values()
    } == set(product((False, True), (False, True), (False, True), (use_missing_sentinel,)))
    for name, field in fast_fields.items():
        validated = public_fields[name]
        assert {key: value for key, value in field.__dict__.items() if key not in {"data_type", "parent"}} == {
            key: value for key, value in validated.__dict__.items() if key not in {"data_type", "parent"}
        }
        assert field.__pydantic_fields_set__ == validated.__pydantic_fields_set__
        assert field.__pydantic_extra__ == validated.__pydantic_extra__
        assert field.__pydantic_private__ == validated.__pydantic_private__
        assert type(field.parent) is type(validated.parent)
        assert field.type_hint == validated.type_hint
        assert field.field == validated.field


@pytest.mark.allow_direct_assert
def test_parser_neutral_field_uses_internal_constructor() -> None:
    """Construct schema-free fields through the parser-owned fast path."""
    data_model_types = get_data_model_types(DataModelType.PydanticV2BaseModel)
    parser = JsonSchemaParser(
        "{}",
        config=JSONSchemaParserConfig(
            data_model_type=data_model_types.data_model,
            data_model_root_type=data_model_types.root_model,
            data_model_field_type=data_model_types.field_model,
            data_type_manager_type=data_model_types.data_type_manager,
            dump_resolve_reference_action=data_model_types.dump_resolve_reference_action,
        ),
    )
    data_type = DataType(type="str")

    field = parser.get_object_field(
        field_name="value",
        field=None,
        required=True,
        field_type=data_type,
        alias=None,
        original_field_name="value",
    )

    assert type(field) is DataModelField
    assert field.name == "value"
    assert field.required
    assert field.data_type is data_type
