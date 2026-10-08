"""Tests for generation store and index helpers."""

from __future__ import annotations

import ast
from collections import defaultdict
from operator import iadd, imul
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from inline_snapshot import snapshot

from datamodel_code_generator.enums import TargetPydanticVersion
from datamodel_code_generator.imports import IMPORT_DECIMAL, IMPORT_LIST, IMPORT_SET
from datamodel_code_generator.model.base import BaseClassDataType, DataModel, DataModelFieldBase
from datamodel_code_generator.model.dataclass import DataClass as StandardDataClass
from datamodel_code_generator.model.msgspec import Constraints as MsgspecConstraints
from datamodel_code_generator.model.msgspec import DataModelField as MsgspecDataModelField
from datamodel_code_generator.model.msgspec import Struct as MsgspecStruct
from datamodel_code_generator.model.pydantic_v2 import BaseModel, DataModelField, RootModel, RootModelTypeAlias
from datamodel_code_generator.model.pydantic_v2.base_model import Constraints as PydanticConstraints
from datamodel_code_generator.model.pydantic_v2.dataclass import DataClass as PydanticDataClass
from datamodel_code_generator.model.type_alias import TypeAliasTypeBackport
from datamodel_code_generator.model.typed_dict import TypedDict
from datamodel_code_generator.parser.generation import (
    GENERATION_STORE_MUTATION_METHODS,
    DataTypeFact,
    GenerationStore,
    _GenerationModelList,
    set_model_base_classes,
)
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType

if TYPE_CHECKING:
    from collections.abc import Iterator

IMPORT_CACHE_CLEARING_MUTATION_METHODS = frozenset({
    "append_field",
    "collapse_root_data_type",
    "detach_data_type_ref",
    "detach_model_data_type_refs",
    "insert_field",
    "move_model",
    "redirect_model_reference_users",
    "redirect_reference_users",
    "remove_field",
    "rename_model",
    "replace_data_type_ref",
    "replace_field_type",
    "replace_nested_data_type",
    "reset_base_classes",
    "set_base_classes",
    "set_fields",
    "set_nested_data_types",
    "update_model_reference",
})
IMPORT_CACHE_NEUTRAL_MUTATION_METHODS = frozenset({
    "defer_refresh",
    "discard_derived_facts",
    "register_model",
})
GENERATION_MODEL_LIST_MUTATION_METHODS = frozenset({
    "__delitem__",
    "__iadd__",
    "__imul__",
    "__setitem__",
    "append",
    "clear",
    "extend",
    "insert",
    "pop",
    "remove",
    "reverse",
    "sort",
})
GENERATION_MODEL_LIST_NON_MUTATING_METHODS = frozenset({
    "__add__",
    "__class_getitem__",
    "__contains__",
    "__eq__",
    "__ge__",
    "__getattribute__",
    "__getitem__",
    "__gt__",
    "__iter__",
    "__le__",
    "__len__",
    "__lt__",
    "__mul__",
    "__ne__",
    "__repr__",
    "__reversed__",
    "__rmul__",
    "__sizeof__",
    "copy",
    "count",
    "index",
})
GENERATION_MODEL_LIST_LIFECYCLE_METHODS = frozenset({"__init__", "__new__"})


def _base_model(name: str = "Model", fields: list[DataModelField] | None = None) -> BaseModel:
    return BaseModel(fields=fields or [], reference=Reference(path=name, original_name=name, name=name))


def _dict_key_reference_classes(
    model_type: type[DataModel],
    *,
    include_dict_keys: bool = False,
    target_pydantic_version: TargetPydanticVersion | None = None,
) -> frozenset[str]:
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_value = Reference(path="Value", original_name="Value", name="Value")
    reference_key = Reference(path="Key", original_name="Key", name="Key")
    data_type = DataType(
        data_types=[DataType(reference=reference_value)],
        dict_key=DataType(reference=reference_key),
    )
    extra_template_data: defaultdict[str, dict[str, object]] = defaultdict(dict)
    if target_pydantic_version is not None:
        extra_template_data["Model"]["target_pydantic_version"] = target_pydantic_version
    model = model_type(
        fields=[DataModelFieldBase(data_type=data_type)],
        reference=reference_model,
        extra_template_data=extra_template_data,
    )
    store = GenerationStore()
    store.register_model(model)
    if include_dict_keys:
        return store.index.reference_classes_for_model_including_dict_keys(model)
    return store.index.reference_classes_for_model(model)


def test_generation_store_import_cache_contract_covers_mutation_surface() -> None:
    """New store mutation APIs must be classified for import-cache safety."""
    assert (
        IMPORT_CACHE_CLEARING_MUTATION_METHODS | IMPORT_CACHE_NEUTRAL_MUTATION_METHODS
    ) == GENERATION_STORE_MUTATION_METHODS


def test_parser_layers_treat_output_template_metadata_as_opaque() -> None:
    """Parser facts must use model capabilities instead of backend template keys."""
    generation_path = Path(__file__).parents[2] / "src/datamodel_code_generator/parser/generation.py"
    jsonschema_path = generation_path.with_name("jsonschema.py")
    parser_trees = tuple(ast.parse(path.read_text(encoding="utf-8")) for path in (generation_path, jsonschema_path))
    generation_attributes = {node.attr for node in ast.walk(parser_trees[0]) if isinstance(node, ast.Attribute)}
    attributes = {node.attr for tree in parser_trees for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    string_literals = {
        node.value
        for tree in parser_trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert "extra_template_data" not in generation_attributes
    assert "_additional_properties_reference_classes" not in attributes
    assert not {
        "additionalPropertiesReferenceClasses",
        "additionalPropertiesType",
        "use_typeddict_backport",
    }.intersection(string_literals)


def test_generation_index_does_not_use_metadata_collection_truthiness() -> None:
    """Consume every model-owned dependency even when a collection overrides truthiness."""

    class FalseyReferenceClasses(frozenset[str]):
        def __bool__(self) -> bool:
            return False

    reference_classes = FalseyReferenceClasses({"Metadata"})
    assert not reference_classes

    class MetadataModel(TypedDict):
        @property
        def _additional_properties_reference_classes(self) -> frozenset[str]:
            return reference_classes

    model = MetadataModel(
        fields=[],
        reference=Reference(path="Model", original_name="Model", name="Model"),
        extra_template_data=defaultdict(dict, {"Model": {"additionalPropertiesType": "Metadata"}}),
    )
    store = GenerationStore()
    store.register_model(model)

    assert {
        "reference_classes": store.index.reference_classes_for_model(model),
        "fallback_reference_classes": store.index.reference_classes_for_model_including_dict_keys(model),
        "typed_dict_kwargs": model._internal_template_data["typed_dict_kwargs"],
    } == snapshot({
        "reference_classes": frozenset({"Metadata"}),
        "fallback_reference_classes": frozenset({"Metadata"}),
        "typed_dict_kwargs": {"extra_items": "'Metadata'"},
    })


def test_generation_index_including_dict_keys_falls_back_for_untracked_model() -> None:
    """Keep direct-model fallback behavior when no store facts are available."""
    target = Reference(path="Target", original_name="Target", name="Target")
    key = Reference(path="Key", original_name="Key", name="Key")
    model = _base_model(
        fields=[
            DataModelField(
                data_type=DataType(data_types=[DataType(reference=target)], dict_key=DataType(reference=key))
            )
        ]
    )

    assert GenerationStore().index.reference_classes_for_model_including_dict_keys(model) == frozenset({
        "Key",
        "Target",
    })


def test_generation_store_indexes_model_and_reference_order() -> None:
    """Store facts preserve append order and expose model dependencies."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    data_type_b = DataType(reference=reference_b)
    model_a = BaseModel(fields=[DataModelField(data_type=data_type_b)], reference=reference_a)
    model_b = BaseModel(fields=[], reference=reference_b)
    store = GenerationStore()

    store.register_model(model_a)
    store.register_model(model_b)

    fact_a = store.index.model_fact(model_a)
    fact_b = store.index.model_fact(model_b)
    assert {
        "models": [model.reference.path for model in store.models],
        "facts": [
            (fact_a.path, fact_a.parse_order) if fact_a else None,
            (fact_b.path, fact_b.parse_order) if fact_b else None,
        ],
        "reference_classes": sorted(store.index.reference_classes_for_model(model_a)),
        "model_for_reference_b": store.index.model_for_reference(reference_b) is model_b,
        "data_type_facts_for_reference_b": [
            fact.data_type is data_type_b for fact in store.index.data_type_facts_for_reference(reference_b)
        ],
    } == snapshot(
        {
            "models": ["A", "B"],
            "facts": [("A", 0), ("B", 1)],
            "reference_classes": ["B"],
            "model_for_reference_b": True,
            "data_type_facts_for_reference_b": [True],
        },
    )


def test_generation_facts_remove_legacy_edge_buckets() -> None:
    """Legacy edge buckets should be absent while reverse edges remain populated."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    data_type_b = DataType(reference=reference_b)
    model_a = BaseModel(fields=[DataModelField(data_type=data_type_b)], reference=reference_a)
    model_b = BaseModel(fields=[], reference=reference_b)
    store = GenerationStore()

    store.register_model(model_a)
    store.register_model(model_b)
    facts = store.current_facts()

    assert not hasattr(facts, "field_edges")
    assert not hasattr(facts, "base_edges")
    assert not hasattr(facts, "all_edges")
    assert [list(data_type_ids) for data_type_ids in facts.reverse_edges.values()] == [[0]]


def test_generation_store_replace_data_type_ref_updates_children_and_index() -> None:
    """Reference replacement keeps compatibility children and derived facts aligned."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    reference_c = Reference(path="C", original_name="C", name="C")
    data_type = DataType(reference=reference_b)
    model = BaseModel(fields=[DataModelField(data_type=data_type)], reference=reference_a)
    store = GenerationStore()
    store.register_model(model)
    cached_before_mutation = sorted(store.index.reference_classes_for_model(model))

    store.replace_data_type_ref(data_type, reference_c)

    assert {
        "cached_before_mutation": cached_before_mutation,
        "old_children": [child is data_type for child in reference_b.children],
        "new_children": [child is data_type for child in reference_c.children],
        "reference_classes": sorted(store.index.reference_classes_for_model(model)),
        "has_reference_b": store.index.has_data_type_references(reference_b),
        "has_reference_c": store.index.has_data_type_references(reference_c),
    } == snapshot(
        {
            "cached_before_mutation": ["B"],
            "old_children": [],
            "new_children": [True],
            "reference_classes": ["C"],
            "has_reference_b": False,
            "has_reference_c": True,
        },
    )


def test_generation_index_detects_remaining_reference_users() -> None:
    """Reference user checks can exclude the data type being rewritten."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_target = Reference(path="Target", original_name="Target", name="Target")
    first_type = DataType(reference=reference_target)
    second_type = DataType(reference=reference_target)
    model = BaseModel(
        fields=[
            DataModelField(data_type=first_type),
            DataModelField(data_type=second_type),
        ],
        reference=reference_model,
    )
    store = GenerationStore()
    store.register_model(model)

    before_detach = store.index.has_data_type_references_other_than(reference_target, first_type)
    store.detach_data_type_ref(second_type)
    after_detach = store.index.has_data_type_references_other_than(reference_target, first_type)

    assert {"before_detach": before_detach, "after_detach": after_detach} == snapshot(
        {"before_detach": True, "after_detach": False},
    )


def test_generation_index_combines_root_collapse_reference_usage() -> None:
    """Root collapse checks should scan a reference's reverse edges once."""

    class RootModel(BaseModel):
        pass

    reference_inner = Reference(path="Inner", original_name="Inner", name="Inner")
    reference_wrapper = Reference(path="Wrapper", original_name="Wrapper", name="Wrapper")
    reference_direct = Reference(path="Direct", original_name="Direct", name="Direct")
    wrapper_type = DataType(reference=reference_inner)
    duplicate_wrapper_type = DataType(reference=reference_inner)
    direct_type = DataType(reference=reference_inner)
    wrapper_model = RootModel(
        fields=[
            DataModelField(data_type=wrapper_type),
            DataModelField(data_type=duplicate_wrapper_type),
        ],
        reference=reference_wrapper,
    )
    direct_model = BaseModel(fields=[DataModelField(data_type=direct_type)], reference=reference_direct)
    store = GenerationStore()
    store.register_model(wrapper_model)
    store.register_model(direct_model)

    wrappers, direct_refs = store.index.root_collapse_reference_usage(
        reference_inner,
        excluded_model=wrapper_model,
        is_root_model=lambda model: isinstance(model, RootModel),
    )

    assert {
        "wrappers": [model.reference.path for model in wrappers],
        "direct_refs": [fact.data_type is direct_type for fact in direct_refs],
    } == snapshot({"wrappers": ["Wrapper"], "direct_refs": [True]})


def test_generation_store_defer_refresh_batches_mutations() -> None:
    """Batching avoids rebuilding facts once per mutation."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    reference_c = Reference(path="C", original_name="C", name="C")
    data_type = DataType(reference=reference_b)
    model = BaseModel(fields=[DataModelField(data_type=data_type)], reference=reference_a)
    store = GenerationStore()
    store.register_model(model)
    store.refresh()
    version_before_mutation = store.facts_version

    with store.defer_refresh():
        store.replace_data_type_ref(data_type, reference_c)
        store.update_model_reference(model, reference_name="Renamed", new_path="Renamed")
        dirty_after_mutation = store._dirty
        version_after_mutation = store.facts_version
        reference_classes_inside_defer = sorted(store.index.reference_classes_for_model(model))

    version_after_defer = store.facts_version
    reference_classes_after_defer = sorted(store.index.reference_classes_for_model(model))

    assert {
        "version_before_mutation": version_before_mutation,
        "dirty_after_mutation": dirty_after_mutation,
        "version_after_mutation": version_after_mutation,
        "version_after_defer": version_after_defer,
        "model_path": model.path,
        "reference_name": model.reference.name,
        "reference_classes_inside_defer": reference_classes_inside_defer,
        "reference_classes_after_defer": reference_classes_after_defer,
    } == snapshot(
        {
            "version_before_mutation": 1,
            "dirty_after_mutation": True,
            "version_after_mutation": 1,
            "version_after_defer": 2,
            "model_path": "Renamed",
            "reference_name": "Renamed",
            "reference_classes_inside_defer": ["B"],
            "reference_classes_after_defer": ["C"],
        },
    )


def test_generation_store_refresh_now_updates_facts_inside_defer() -> None:
    """Callers that need fresh facts inside a mutation block can refresh explicitly."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    reference_c = Reference(path="C", original_name="C", name="C")
    data_type = DataType(reference=reference_b)
    model = BaseModel(fields=[DataModelField(data_type=data_type)], reference=reference_a)
    store = GenerationStore()
    store.register_model(model)
    store.refresh()

    with store.defer_refresh():
        store.replace_data_type_ref(data_type, reference_c)
        version_after_mutation = store.facts_version
        store.refresh_now()
        version_after_refresh_now = store.facts_version
        reference_classes = sorted(store.index.reference_classes_for_model(model))

    assert {
        "version_after_mutation": version_after_mutation,
        "version_after_refresh_now": version_after_refresh_now,
        "version_after_defer": store.facts_version,
        "reference_classes": reference_classes,
    } == snapshot(
        {
            "version_after_mutation": 1,
            "version_after_refresh_now": 2,
            "version_after_defer": 2,
            "reference_classes": ["C"],
        },
    )


def test_generation_store_nested_defer_refresh_rebuilds_on_outer_exit() -> None:
    """Nested deferred blocks should rebuild facts only when the outer block exits."""
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    reference_c = Reference(path="C", original_name="C", name="C")
    data_type = DataType(reference=reference_b)
    model = BaseModel(fields=[DataModelField(data_type=data_type)], reference=reference_a)
    store = GenerationStore()
    store.register_model(model)
    store.refresh()

    with store.defer_refresh():
        with store.defer_refresh():
            store.replace_data_type_ref(data_type, reference_c)
        version_after_inner_exit = store.facts_version
        dirty_after_inner_exit = store._dirty

    assert {
        "version_after_inner_exit": version_after_inner_exit,
        "dirty_after_inner_exit": dirty_after_inner_exit,
        "version_after_outer_exit": store.facts_version,
        "reference_classes": sorted(store.index.reference_classes_for_model(model)),
    } == snapshot(
        {
            "version_after_inner_exit": 1,
            "dirty_after_inner_exit": True,
            "version_after_outer_exit": 2,
            "reference_classes": ["C"],
        },
    )


def test_generation_store_discard_derived_facts_preserves_identity_and_defer_contract() -> None:
    """Explicit fact disposal releases snapshots without changing stable IDs or deferred reads."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_value = Reference(path="Value", original_name="Value", name="Value")
    model = BaseModel(
        fields=[DataModelField(data_type=DataType(reference=reference_value))],
        reference=reference_model,
    )
    store = GenerationStore()
    store.register_model(model)
    store.refresh()
    model_id = store.model_id(model)
    facts_before = store._facts
    version_before = store.facts_version

    with store.defer_refresh():
        with pytest.raises(RuntimeError, match="cannot be discarded"):
            store.discard_derived_facts()
        facts_preserved_inside_defer = store._facts is facts_before
        classes_inside_defer = sorted(store.index.reference_classes_for_model(model))

    store.discard_derived_facts()
    discarded_fact_counts = (
        len(store._facts.model_facts),
        len(store._facts.data_type_facts),
    )
    dirty_after_discard = store._dirty
    model_id_after_discard = store.model_id(model)
    version_after_discard = store.facts_version
    classes_after_refresh = sorted(store.index.reference_classes_for_model(model))

    assert {
        "facts_preserved_inside_defer": facts_preserved_inside_defer,
        "classes_inside_defer": classes_inside_defer,
        "discarded_fact_counts": discarded_fact_counts,
        "dirty_after_discard": dirty_after_discard,
        "stable_model_id": model_id_after_discard == model_id,
        "version_before": version_before,
        "version_after_discard": version_after_discard,
        "version_after_refresh": store.facts_version,
        "classes_after_refresh": classes_after_refresh,
    } == snapshot(
        {
            "facts_preserved_inside_defer": True,
            "classes_inside_defer": ["Value"],
            "discarded_fact_counts": (0, 0),
            "dirty_after_discard": True,
            "stable_model_id": True,
            "version_before": 1,
            "version_after_discard": 1,
            "version_after_refresh": 2,
            "classes_after_refresh": ["Value"],
        },
    )


def test_generation_store_records_nested_and_dict_key_roles() -> None:
    """Data type facts should distinguish nested values from dictionary keys."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_value = Reference(path="Value", original_name="Value", name="Value")
    reference_key = Reference(path="Key", original_name="Key", name="Key")
    value_type = DataType(reference=reference_value)
    key_type = DataType(reference=reference_key)
    dict_type = DataType(data_types=[value_type], dict_key=DataType(data_types=[key_type]))
    model = BaseModel(fields=[DataModelField(data_type=dict_type)], reference=reference_model)
    store = GenerationStore()

    store.register_model(model)
    store.refresh()

    assert [
        (fact.role, fact.reference.path if fact.reference else None) for fact in store.data_type_facts.values()
    ] == snapshot(
        [
            ("field", None),
            ("nested", "Value"),
            ("dict_key", None),
            ("dict_key", "Key"),
            ("base", None),
        ],
    )
    assert store.index.reference_classes_for_model(model) == snapshot(frozenset({"Value"}))


@pytest.mark.parametrize(
    "target_pydantic_version",
    [None, TargetPydanticVersion.V2, TargetPydanticVersion.V2_11, TargetPydanticVersion.V2_12],
    ids=["unset", "2", "2.11", "2.12"],
)
@pytest.mark.parametrize(
    ("model_type", "builtin_pydantic_model"),
    [
        pytest.param(BaseModel, True, id="pydantic-v2-base-model"),
        pytest.param(RootModel, True, id="pydantic-v2-root-model"),
        pytest.param(RootModelTypeAlias, True, id="pydantic-v2-root-model-type-alias"),
        pytest.param(PydanticDataClass, True, id="pydantic-v2-dataclass"),
        pytest.param(StandardDataClass, False, id="standard-dataclass"),
        pytest.param(TypeAliasTypeBackport, False, id="pydantic-v2-auxiliary-type-alias"),
    ],
)
def test_generation_index_dict_key_reference_policy_matrix(
    model_type: type[DataModel],
    builtin_pydantic_model: bool,
    target_pydantic_version: TargetPydanticVersion | None,
) -> None:
    """Only built-in Pydantic v2 models targeting Pydantic before 2.8 order dict-key references first."""
    include_dict_key_reference = builtin_pydantic_model and target_pydantic_version is TargetPydanticVersion.V2
    expected = frozenset({"Key", "Value"} if include_dict_key_reference else {"Value"})

    assert _dict_key_reference_classes(model_type, target_pydantic_version=target_pydantic_version) == expected
    assert _dict_key_reference_classes(
        model_type, include_dict_keys=True, target_pydantic_version=target_pydantic_version
    ) == frozenset({"Key", "Value"})


def test_generation_index_external_pydantic_subclasses_do_not_inherit_dict_key_reference_policy() -> None:
    """Keep external Pydantic model subclasses outside the built-in compatibility policy."""

    class ExternalBaseModel(BaseModel):
        pass

    class ExternalDataClass(PydanticDataClass):
        pass

    assert [
        _dict_key_reference_classes(ExternalBaseModel, target_pydantic_version=TargetPydanticVersion.V2),
        _dict_key_reference_classes(ExternalDataClass, target_pydantic_version=TargetPydanticVersion.V2),
    ] == [frozenset({"Value"}), frozenset({"Value"})]


def test_generation_store_replaces_nested_data_types() -> None:
    """Nested type replacement should keep parent pointers and facts in sync."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_old = Reference(path="Old", original_name="Old", name="Old")
    reference_new = Reference(path="New", original_name="New", name="New")
    old_type = DataType(reference=reference_old)
    parent_type = DataType(data_types=[old_type])
    old_type.parent = parent_type
    model = BaseModel(fields=[DataModelField(data_type=parent_type)], reference=reference_model)
    store = GenerationStore()
    store.register_model(model)

    new_type = DataType(reference=reference_new)
    store.set_nested_data_types(parent_type, [new_type])

    assert {
        "old_parent": old_type.parent,
        "new_parent": new_type.parent is parent_type,
        "reference_classes": sorted(store.index.reference_classes_for_model(model)),
    } == snapshot(
        {
            "old_parent": None,
            "new_parent": True,
            "reference_classes": ["New"],
        },
    )


def test_set_model_base_classes_supports_store_and_legacy_fallback() -> None:
    """The helper keeps direct fallback compatibility while updating store facts when available."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_base = Reference(path="Base", original_name="Base", name="Base")
    reference_legacy = Reference(path="Legacy", original_name="Legacy", name="Legacy")
    model = BaseModel(fields=[], reference=reference_model)
    store = GenerationStore()
    store.register_model(model)

    set_model_base_classes(model, [BaseClassDataType(reference=reference_base)], store)
    store_reference_classes = sorted(store.index.reference_classes_for_model(model))
    set_model_base_classes(model, [BaseClassDataType(reference=reference_legacy)], None)

    assert {
        "store_reference_classes": store_reference_classes,
        "legacy_base_classes": [base_class.reference.path for base_class in model.base_classes if base_class.reference],
    } == snapshot(
        {
            "store_reference_classes": ["Base"],
            "legacy_base_classes": ["Legacy"],
        },
    )


def test_generation_model_list_contract_classifies_every_list_method() -> None:
    """Every list-defined callable on this Python runtime must be classified."""
    list_methods = {name for name, value in vars(list).items() if callable(value)}

    # CPython may move inherited, non-mutating dunder methods out of
    # ``list.__dict__`` between releases, so extra classifications are valid.
    assert {
        "unclassified": sorted(
            list_methods
            - GENERATION_MODEL_LIST_MUTATION_METHODS
            - GENERATION_MODEL_LIST_NON_MUTATING_METHODS
            - GENERATION_MODEL_LIST_LIFECYCLE_METHODS,
        ),
        "mutators_not_overridden": sorted(
            GENERATION_MODEL_LIST_MUTATION_METHODS - vars(_GenerationModelList).keys(),
        ),
    } == snapshot({"unclassified": [], "mutators_not_overridden": []})


def test_generation_model_list_invalidates_each_list_mutation() -> None:  # noqa: PLR0912
    """Each Parser.results-compatible mutation must independently invalidate and rebuild facts."""
    expected_paths = {
        "__delitem__": ["A"],
        "__iadd__": ["A", "B", "C"],
        "__imul__": [],
        "__setitem__": ["C"],
        "append": ["A", "B", "C"],
        "clear": [],
        "extend": ["A", "B", "C"],
        "insert": ["A", "C", "B"],
        "pop": ["A"],
        "remove": ["B"],
        "reverse": ["B", "A"],
        "sort": ["B", "A"],
    }
    expected_returns = {"__iadd__": "self", "__imul__": "self", "pop": "B"}

    for mutation in sorted(GENERATION_MODEL_LIST_MUTATION_METHODS):
        model_a = _base_model("A")
        model_b = _base_model("B")
        model_c = _base_model("C")
        store = GenerationStore()
        store.models.extend([model_a, model_b])
        store.refresh()

        match mutation:
            case "__delitem__":
                del store.models[1:]
                result = None
            case "__iadd__":
                result = iadd(store.models, [model_c])
            case "__imul__":
                result = imul(store.models, 0)
            case "__setitem__":
                store.models[:] = [model_c]
                result = None
            case "append":
                result = store.models.append(model_c)
            case "clear":
                result = store.models.clear()
            case "extend":
                result = store.models.extend([model_c])
            case "insert":
                result = store.models.insert(1, model_c)
            case "pop":
                result = store.models.pop()
            case "remove":
                result = store.models.remove(model_a)
            case "reverse":
                result = store.models.reverse()
            case "sort":
                result = store.models.sort(key=lambda model: model.path, reverse=True)
            case _:  # pragma: no cover
                message = f"Unhandled list mutation: {mutation}"
                raise AssertionError(message)

        dirty_after_mutation = store._dirty
        model_paths = [model.path for model in store.models]
        fact_paths = [fact.path for fact in sorted(store.model_facts.values(), key=lambda fact: fact.parse_order)]
        match mutation:
            case "__iadd__" | "__imul__":
                returned = "self" if result is store.models else "other"
            case "pop":
                returned = result.path
            case _:
                returned = result

        assert {
            "dirty": dirty_after_mutation,
            "models": model_paths,
            "facts": fact_paths,
            "returned": returned,
        } == {
            "dirty": True,
            "models": expected_paths[mutation],
            "facts": expected_paths[mutation],
            "returned": expected_returns.get(mutation),
        }


def test_generation_model_list_sort_without_key_invalidates_facts() -> None:
    """The no-key list.sort overload must preserve its return value and invalidate facts."""
    store = GenerationStore()
    store.models.append(_base_model("A"))
    store.refresh()

    result = store.models.sort()

    assert {
        "dirty": store._dirty,
        "models": [model.path for model in store.models],
        "facts": [fact.path for fact in store.model_facts.values()],
        "returned": result,
    } == snapshot({"dirty": True, "models": ["A"], "facts": ["A"], "returned": None})


@pytest.mark.parametrize("mutation", ["extend", "__iadd__"])
def test_generation_model_list_invalidates_partial_mutation_on_iterator_error(mutation: str) -> None:
    """Partially consumed iterables must not leave facts marked clean."""
    store = GenerationStore()
    store.models.append(_base_model("A"))
    store.refresh()

    def failing_models() -> Iterator[BaseModel]:
        yield _base_model("B")
        raise RuntimeError

    with pytest.raises(RuntimeError):
        getattr(store.models, mutation)(failing_models())

    assert {
        "dirty": store._dirty,
        "models": [model.path for model in store.models],
        "facts": [fact.path for fact in sorted(store.model_facts.values(), key=lambda fact: fact.parse_order)],
    } == snapshot({"dirty": True, "models": ["A", "B"], "facts": ["A", "B"]})


def test_generation_model_list_invalidates_on_sort_key_error() -> None:
    """A failing sort key must not leave facts marked clean."""
    store = GenerationStore()
    store.models.extend([_base_model("B"), _base_model("A")])
    store.refresh()
    key_calls = 0

    def failing_key(model: BaseModel) -> str:
        nonlocal key_calls
        key_calls += 1
        if key_calls == 1:
            return model.path
        raise RuntimeError

    with pytest.raises(RuntimeError):
        store.models.sort(key=failing_key)

    assert {
        "dirty": store._dirty,
        "key_calls": key_calls,
        "models": [model.path for model in store.models],
        "facts": [fact.path for fact in sorted(store.model_facts.values(), key=lambda fact: fact.parse_order)],
    } == snapshot({"dirty": True, "key_calls": 2, "models": ["B", "A"], "facts": ["B", "A"]})


def test_generation_index_returns_empty_results_for_unknown_objects() -> None:
    """Unknown references and objects should return explicit empty values, not stale facts."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_target = Reference(path="Target", original_name="Target", name="Target")
    reference_unknown = Reference(path="Unknown", original_name="Unknown", name="Unknown")
    data_type = DataType(reference=reference_target)
    model = BaseModel(fields=[DataModelField(data_type=data_type)], reference=reference_model)
    unknown_model = BaseModel(
        fields=[DataModelField(data_type=DataType(reference=reference_unknown))],
        reference=reference_unknown,
    )
    unknown_data_type = DataType(reference=reference_unknown)
    store = GenerationStore()
    store.register_model(model)

    assert {
        "unknown_model_fact": store.index.model_fact(unknown_model),
        "known_model_id_for_reference": store.index.model_id_for_reference(reference_model),
        "unknown_model_for_reference": store.index.model_for_reference(reference_unknown),
        "unknown_data_type_facts_for_reference": store.index.data_type_facts_for_reference(reference_unknown),
        "unknown_has_other_refs": store.index.has_data_type_references_other_than(reference_unknown, data_type),
        "known_owner": store.index.owner_model_for_data_type(data_type) is model,
        "unknown_owner": store.index.owner_model_for_data_type(unknown_data_type),
        "unknown_reference_classes": sorted(store.index.reference_classes_for_model(unknown_model)),
        "facts_property": store.facts is store.current_facts(),
        "model_facts_property": len(store.model_facts),
        "data_type_fact_by_object_property": id(data_type) in store.data_type_fact_by_object,
        "model_by_path_property": dict(store.model_by_path),
        "model_by_ref_id_property": sorted(store.model_by_ref_id.values()),
        "data_types_by_model_property": {
            model_id: list(data_type_ids) for model_id, data_type_ids in store.data_types_by_model.items()
        },
        "reverse_edges_property": [list(data_type_ids) for data_type_ids in store.reverse_edges.values()],
    } == snapshot(
        {
            "unknown_model_fact": None,
            "known_model_id_for_reference": 0,
            "unknown_model_for_reference": None,
            "unknown_data_type_facts_for_reference": (),
            "unknown_has_other_refs": False,
            "known_owner": True,
            "unknown_owner": None,
            "unknown_reference_classes": ["Unknown"],
            "facts_property": True,
            "model_facts_property": 1,
            "data_type_fact_by_object_property": True,
            "model_by_path_property": {"Model": 0},
            "model_by_ref_id_property": [0],
            "data_types_by_model_property": {0: [0, 1]},
            "reverse_edges_property": [[0]],
        },
    )


def test_generation_index_exposes_root_collapse_helpers_independently() -> None:
    """Split root-collapse helpers should preserve the combined query's ordering."""

    class RootModel(BaseModel):
        pass

    reference_inner = Reference(path="Inner", original_name="Inner", name="Inner")
    reference_wrapper = Reference(path="Wrapper", original_name="Wrapper", name="Wrapper")
    reference_direct = Reference(path="Direct", original_name="Direct", name="Direct")
    reference_base = Reference(path="Base", original_name="Base", name="Base")
    reference_unknown = Reference(path="Unknown", original_name="Unknown", name="Unknown")
    wrapper_model = RootModel(
        fields=[
            DataModelField(data_type=DataType(reference=reference_inner)),
            DataModelField(data_type=DataType(reference=reference_inner)),
        ],
        reference=reference_wrapper,
    )
    direct_model = BaseModel(
        fields=[DataModelField(data_type=DataType(reference=reference_inner))],
        reference=reference_direct,
    )
    base_model = BaseModel(fields=[], base_classes=[reference_inner], reference=reference_base)
    store = GenerationStore()
    store.register_model(wrapper_model)
    store.register_model(direct_model)
    store.register_model(base_model)

    wrappers = store.index.root_model_wrappers_for_reference(reference_inner, RootModel)
    direct_refs = store.index.direct_non_root_refs_for_reference(
        reference_inner,
        excluded_model=base_model,
        root_model_type=RootModel,
    )
    missing_direct_refs = store.index.direct_non_root_refs_for_reference(
        reference_unknown,
        excluded_model=base_model,
        root_model_type=RootModel,
    )
    missing_root_wrappers = store.index.root_model_wrappers_for_reference(reference_unknown, RootModel)
    missing_wrappers, missing_collapse_direct_refs = store.index.root_collapse_reference_usage(
        reference_unknown,
        excluded_model=base_model,
        is_root_model=lambda model: isinstance(model, RootModel),
    )

    assert {
        "wrappers": [model.reference.path for model in wrappers],
        "direct_refs": [fact.owner_field_index for fact in direct_refs],
        "missing_direct_refs": missing_direct_refs,
        "missing_root_wrappers": missing_root_wrappers,
        "missing_wrappers": missing_wrappers,
        "missing_collapse_direct_refs": missing_collapse_direct_refs,
    } == snapshot(
        {
            "wrappers": ["Wrapper"],
            "direct_refs": [0],
            "missing_direct_refs": [],
            "missing_root_wrappers": [],
            "missing_wrappers": [],
            "missing_collapse_direct_refs": [],
        },
    )


def test_generation_store_updates_model_and_field_metadata() -> None:
    """Model metadata and field mutations should refresh dependent facts through store APIs."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_a = Reference(path="A", original_name="A", name="A")
    reference_b = Reference(path="B", original_name="B", name="B")
    model = BaseModel(fields=[], reference=reference_model)
    field_a = DataModelField(data_type=DataType(reference=reference_a))
    field_b = DataModelField(data_type=DataType(reference=reference_b))
    store = GenerationStore()
    store.register_model(model)

    store.append_field(model, field_a)
    store.insert_field(model, 0, field_b)
    store.remove_field(model, field_a)
    store.rename_model(model, class_name="RenamedModel", reference_name="Renamed")
    store.move_model(model, new_path="pkg.Renamed", new_file_path=Path("pkg.py"))

    assert {
        "fields": [field.data_type.reference.path for field in model.fields if field.data_type.reference],
        "class_name": model.class_name,
        "reference_name": model.reference.name,
        "path": model.path,
        "file_path": model.file_path,
        "reference_classes": sorted(store.index.reference_classes_for_model(model)),
    } == snapshot(
        {
            "fields": ["B"],
            "class_name": "Renamed",
            "reference_name": "Renamed",
            "path": "pkg.Renamed",
            "file_path": Path("pkg.py"),
            "reference_classes": ["B"],
        },
    )


def test_generation_store_field_mutations_clear_model_imports_cache() -> None:
    """Field list mutations should not leave DataModel.imports stale."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    model = BaseModel(fields=[], reference=reference_model)
    list_field = DataModelField(data_type=DataType(is_list=True))
    store = GenerationStore()
    store.register_model(model)

    assert IMPORT_LIST not in model.imports

    store.append_field(model, list_field)
    assert list_field.parent is model
    assert IMPORT_LIST in model.imports

    store.remove_field(model, list_field)
    assert list_field.parent is None
    assert IMPORT_LIST not in model.imports

    detached_field = DataModelField(data_type=DataType(is_set=True))
    model.fields.append(detached_field)
    assert detached_field.parent is None

    store.remove_field(model, detached_field)
    assert detached_field.parent is None
    assert IMPORT_SET not in model.imports

    store.set_fields(model, [list_field])
    assert list_field.parent is model
    assert IMPORT_LIST in model.imports

    store.set_fields(model, [detached_field])
    assert list_field.parent is None
    assert detached_field.parent is model
    assert IMPORT_LIST not in model.imports
    assert IMPORT_SET in model.imports


def test_generation_store_nested_type_mutation_clears_model_imports_cache() -> None:
    """Nested DataType replacement should refresh cached field-derived imports."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    list_type = DataType(is_list=True, data_types=[DataType(type="str")])
    model = BaseModel(fields=[DataModelField(data_type=list_type)], reference=reference_model)
    store = GenerationStore()
    store.register_model(model)

    assert IMPORT_LIST in model.imports
    assert IMPORT_DECIMAL not in model.imports

    store.set_nested_data_types(list_type, [DataType.from_import(IMPORT_DECIMAL)])

    assert IMPORT_LIST in model.imports
    assert IMPORT_DECIMAL in model.imports


def test_generation_store_field_type_replacement_clears_model_imports_cache() -> None:
    """Store-owned field type replacement should clear the cached owner model imports."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    field = DataModelField(data_type=DataType(is_list=True))
    model = BaseModel(fields=[], reference=reference_model)
    store = GenerationStore()
    store.register_model(model)
    store.append_field(model, field)

    assert IMPORT_LIST in model.imports
    assert IMPORT_SET not in model.imports

    store.replace_field_type(field, DataType(is_set=True))

    assert IMPORT_LIST not in model.imports
    assert IMPORT_SET in model.imports


def test_generation_store_model_mutations_clear_cached_imports_contract() -> None:
    """Model-level store mutations must clear an already populated imports cache."""
    model = _base_model()
    store = GenerationStore()
    store.register_model(model)

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.update_model_reference(model, reference_name="Renamed")
    assert model._IMPORTS_CACHE_KEY not in model.__dict__

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.rename_model(model, reference_name="RenamedAgain")
    assert model._IMPORTS_CACHE_KEY not in model.__dict__

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.move_model(model, new_path="pkg.RenamedAgain", new_file_path=Path("pkg.py"))
    assert model._IMPORTS_CACHE_KEY not in model.__dict__

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.set_base_classes(model, [BaseClassDataType(type="object")])
    assert model._IMPORTS_CACHE_KEY not in model.__dict__

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.reset_base_classes(model)
    assert {
        "cache_key": model._IMPORTS_CACHE_KEY in model.__dict__,
        "path": model.path,
        "reference_name": model.reference.name,
        "file_path": model.file_path,
    } == snapshot({
        "cache_key": False,
        "path": "pkg.RenamedAgain",
        "reference_name": "RenamedAgain",
        "file_path": Path("pkg.py"),
    })


def test_generation_store_field_collection_mutations_clear_cached_imports_contract() -> None:
    """Field collection helpers must invalidate stale model imports."""
    model = _base_model()
    store = GenerationStore()
    store.register_model(model)
    list_field = DataModelField(data_type=DataType(is_list=True))
    set_field = DataModelField(data_type=DataType(is_set=True))

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.append_field(model, list_field)
    assert model._IMPORTS_CACHE_KEY not in model.__dict__
    assert IMPORT_LIST in model.imports

    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.insert_field(model, 0, set_field)
    assert model._IMPORTS_CACHE_KEY not in model.__dict__
    assert IMPORT_LIST in model.imports
    assert IMPORT_SET in model.imports

    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.remove_field(model, list_field)
    assert model._IMPORTS_CACHE_KEY not in model.__dict__
    assert IMPORT_LIST not in model.imports
    assert IMPORT_SET in model.imports

    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.set_fields(model, [DataModelField(data_type=DataType.from_import(IMPORT_DECIMAL))])
    assert {
        "cache_key": model._IMPORTS_CACHE_KEY in model.__dict__,
        "has_decimal": IMPORT_DECIMAL in model.imports,
        "has_set": IMPORT_SET in model.imports,
        "list_parent": list_field.parent is None,
        "set_parent": set_field.parent is None,
    } == snapshot({
        "cache_key": False,
        "has_decimal": True,
        "has_set": False,
        "list_parent": True,
        "set_parent": True,
    })


def test_generation_store_data_type_mutations_clear_cached_imports_contract() -> None:
    """DataType helpers must clear the cache for the owner model they mutate."""
    old_reference = Reference(path="Old", original_name="Old", name="Old")
    new_reference = Reference(path="New", original_name="New", name="New")
    data_type = DataType(reference=old_reference)
    field = DataModelField(data_type=data_type)
    model = _base_model(fields=[field])
    store = GenerationStore()
    store.register_model(model)

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.replace_data_type_ref(data_type, new_reference)
    assert model._IMPORTS_CACHE_KEY not in model.__dict__
    assert data_type.reference is new_reference

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.detach_data_type_ref(data_type)
    assert model._IMPORTS_CACHE_KEY not in model.__dict__
    assert data_type.reference is None

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.replace_field_type(field, DataType(is_set=True))
    assert {
        "cache_key": model._IMPORTS_CACHE_KEY in model.__dict__,
        "has_set": IMPORT_SET in model.imports,
        "field_parent": field.data_type.parent is field,
    } == snapshot({
        "cache_key": False,
        "has_set": True,
        "field_parent": True,
    })


def test_generation_store_nested_data_type_mutations_clear_cached_imports_contract() -> None:
    """Nested DataType helpers must invalidate the imports cache of the outer model."""
    list_type = DataType(is_list=True, data_types=[DataType(type="str")])
    model = _base_model(fields=[DataModelField(data_type=list_type)])
    store = GenerationStore()
    store.register_model(model)

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.set_nested_data_types(list_type, [DataType.from_import(IMPORT_DECIMAL)])
    assert model._IMPORTS_CACHE_KEY not in model.__dict__
    assert IMPORT_DECIMAL in model.imports

    nested_type = list_type.data_types[0]
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.replace_nested_data_type(list_type, nested_type, DataType(type="str"))
    assert {
        "cache_key": model._IMPORTS_CACHE_KEY in model.__dict__,
        "has_decimal": IMPORT_DECIMAL in model.imports,
        "nested_types": [nested.type for nested in list_type.data_types],
    } == snapshot({
        "cache_key": False,
        "has_decimal": False,
        "nested_types": ["str"],
    })


def test_generation_store_atomic_replace_invalidates_shared_base_owner_cache() -> None:
    """Atomic replacement keeps cache invalidation for a shared field/base DataType."""

    class CustomDataModelField(DataModelField):
        pass

    old_reference = Reference(path="Old", original_name="Old", name="Old")
    shared_data_type = BaseClassDataType(reference=old_reference)
    field = CustomDataModelField(data_type=shared_data_type)
    field_owner = _base_model("FieldOwner", fields=[field])
    base_owner = _base_model("BaseOwner")
    base_owner.base_classes.append(shared_data_type)
    store = GenerationStore()
    store.register_model(field_owner)
    store.register_model(base_owner)

    base_owner.get_dedup_key()
    assert base_owner._dedup_key_cache

    replacement = DataType(type="int")
    with store._replace_data_type_and_detach_data_type_ref(
        shared_data_type,
        replacement,
        owner=field,
        replacement_kind="field",
    ):
        pass

    assert {
        "field_replaced": field.data_type is replacement,
        "old_parent": shared_data_type.parent,
        "old_reference": shared_data_type.reference,
        "old_children": old_reference.children,
        "base_cache": base_owner._dedup_key_cache,
    } == snapshot({
        "field_replaced": True,
        "old_parent": None,
        "old_reference": None,
        "old_children": [],
        "base_cache": {},
    })


def test_generation_store_atomic_replace_invalidates_scope_on_body_error() -> None:
    """An abnormal replacement-body error disables incremental reference tracking."""

    class ReplacementBodyError(Exception):
        pass

    old_reference = Reference(path="Old", original_name="Old", name="Old")
    old_data_type = DataType(reference=old_reference)
    field = DataModelField(data_type=old_data_type)
    model = _base_model("Model", fields=[field])
    store = GenerationStore()
    store.register_model(model)

    def replace_with_body_error() -> None:
        with store._replace_data_type_and_detach_data_type_ref(
            old_data_type,
            DataType(type="int"),
            owner=field,
            replacement_kind="field",
        ):
            raise ReplacementBodyError

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        with pytest.raises(ReplacementBodyError):
            replace_with_body_error()
        assert scope.enabled is False


def test_generation_store_atomic_replace_custom_field_override_falls_back_to_fresh_facts() -> None:  # noqa: PLR0914
    """Custom field replacement side effects must use the legacy fresh-index path."""

    def build_state() -> tuple[
        GenerationStore,
        BaseModel,
        DataModelField,
        DataType,
        Reference,
        Reference,
        Reference,
        Reference,
    ]:
        model_reference = Reference(path="Model", original_name="Model", name="Model")
        old_reference = Reference(path="Old", original_name="Old", name="Old")
        new_reference = Reference(path="New", original_name="New", name="New")
        peer_old_reference = Reference(path="PeerOld", original_name="PeerOld", name="PeerOld")
        peer_new_reference = Reference(path="PeerNew", original_name="PeerNew", name="PeerNew")
        old_data_type = DataType(reference=old_reference)
        peer_data_type = DataType(reference=peer_old_reference)

        class CustomDataModelField(DataModelField):
            def replace_data_type(
                self,
                new_data_type: DataType,
                *,
                clear_old_parent: bool = True,
            ) -> None:
                super().replace_data_type(new_data_type, clear_old_parent=clear_old_parent)
                peer_data_type.replace_reference(peer_new_reference)

        field = CustomDataModelField(data_type=old_data_type)
        model = BaseModel(
            fields=[field, DataModelField(data_type=peer_data_type)],
            reference=model_reference,
        )
        store = GenerationStore()
        store.register_model(model)
        store.refresh()
        return (
            store,
            model,
            field,
            old_data_type,
            old_reference,
            new_reference,
            peer_old_reference,
            peer_new_reference,
        )

    def observe(
        store: GenerationStore,
        model: BaseModel,
        old_reference: Reference,
        new_reference: Reference,
        peer_old_reference: Reference,
        peer_new_reference: Reference,
    ) -> tuple[str, tuple[int, int, int, int], tuple[bool, bool, bool, bool]]:
        return (
            model.render(),
            (
                len(store.index.data_type_facts_for_reference(old_reference)),
                len(store.index.data_type_facts_for_reference(new_reference)),
                len(store.index.data_type_facts_for_reference(peer_old_reference)),
                len(store.index.data_type_facts_for_reference(peer_new_reference)),
            ),
            (
                store.index.has_data_type_references(old_reference),
                store.index.has_data_type_references(new_reference),
                store.index.has_data_type_references(peer_old_reference),
                store.index.has_data_type_references(peer_new_reference),
            ),
        )

    (
        scoped_store,
        scoped_model,
        scoped_field,
        scoped_old_data_type,
        scoped_old_reference,
        scoped_new_reference,
        scoped_peer_old_reference,
        scoped_peer_new_reference,
    ) = build_state()
    with scoped_store._collapse_root_reference_scope():
        scoped_scope = scoped_store._active_root_collapse_reference_scope
        assert scoped_scope is not None
        with scoped_store._replace_data_type_and_detach_data_type_ref(
            scoped_old_data_type,
            DataType(reference=scoped_new_reference),
            owner=scoped_field,
            replacement_kind="field",
        ):
            pass
        assert scoped_scope.enabled is False
        assert scoped_store._root_collapse_has_data_type_references(scoped_peer_new_reference) is None
    scoped_result = observe(
        scoped_store,
        scoped_model,
        scoped_old_reference,
        scoped_new_reference,
        scoped_peer_old_reference,
        scoped_peer_new_reference,
    )

    (
        legacy_store,
        legacy_model,
        legacy_field,
        legacy_old_data_type,
        legacy_old_reference,
        legacy_new_reference,
        legacy_peer_old_reference,
        legacy_peer_new_reference,
    ) = build_state()
    legacy_store.replace_field_type(legacy_field, DataType(reference=legacy_new_reference))
    legacy_store.detach_data_type_ref(legacy_old_data_type)
    legacy_result = observe(
        legacy_store,
        legacy_model,
        legacy_old_reference,
        legacy_new_reference,
        legacy_peer_old_reference,
        legacy_peer_new_reference,
    )

    assert scoped_result == legacy_result
    assert scoped_field.data_type.reference is scoped_new_reference
    assert legacy_field.data_type.reference is legacy_new_reference
    assert scoped_old_data_type.reference is legacy_old_data_type.reference is None
    assert scoped_new_reference.path == legacy_new_reference.path == "New"


def test_generation_store_atomic_replace_custom_data_type_tree_falls_back() -> None:
    """Custom DataType traversal must not enter the incremental replacement path."""

    class CustomDataType(DataType):
        @property
        def all_data_types(self) -> Iterator[DataType]:
            yield from super().all_data_types

    reference = Reference(path="Target", original_name="Target", name="Target")
    old_data_type = CustomDataType(reference=reference)
    model = _base_model(fields=[DataModelField(data_type=old_data_type)])
    store = GenerationStore()
    store.register_model(model)

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        with store._replace_data_type_and_detach_data_type_ref(
            old_data_type,
            DataType(type="int"),
            owner=model.fields[0],
            replacement_kind="field",
        ):
            pass
        assert scope.enabled is False

    assert model.fields[0].data_type.type == "int"
    assert old_data_type.reference is None
    assert not store.index.has_data_type_references(reference)


def test_generation_store_atomic_replace_inherited_field_stays_incremental() -> None:
    """A field subclass that inherits replacement behavior keeps the fast path."""

    class InheritedDataModelField(DataModelField):
        pass

    reference = Reference(path="Target", original_name="Target", name="Target")
    old_data_type = DataType(reference=reference)
    field = InheritedDataModelField(data_type=old_data_type)
    model = _base_model(fields=[field])
    store = GenerationStore()
    store.register_model(model)

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        with store._replace_data_type_and_detach_data_type_ref(
            old_data_type,
            DataType(type="int"),
            owner=field,
            replacement_kind="field",
        ):
            pass
        assert scope.enabled

    assert field.data_type.type == "int"
    assert old_data_type.reference is None
    assert not store.index.has_data_type_references(reference)


def test_generation_store_atomic_replace_nested_builtin_keeps_scope() -> None:
    """Built-in nested replacement remains eligible for incremental tracking."""
    reference = Reference(path="Target", original_name="Target", name="Target")
    old_data_type = DataType(reference=reference)
    parent_data_type = DataType(data_types=[old_data_type])
    model = _base_model(fields=[DataModelField(data_type=parent_data_type)])
    store = GenerationStore()
    store.register_model(model)

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        with store._replace_data_type_and_detach_data_type_ref(
            old_data_type,
            DataType(type="int"),
            owner=parent_data_type,
            replacement_kind="nested",
        ):
            pass
        assert scope.enabled

    assert model.fields[0].data_type.data_types[0].type == "int"
    assert old_data_type.reference is None
    assert not store.index.has_data_type_references(reference)


def test_generation_store_reference_detach_helpers_clear_cached_imports_contract() -> None:
    """Bulk reference helpers must clear the cache through their delegated mutations."""
    reference = Reference(path="Referenced", original_name="Referenced", name="Referenced")
    root_type = DataType(reference=reference)
    model = _base_model(fields=[DataModelField(data_type=root_type)])
    store = GenerationStore()
    store.register_model(model)

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.collapse_root_data_type(root_type, Reference(path="Inner", original_name="Inner", name="Inner"))
    assert model._IMPORTS_CACHE_KEY not in model.__dict__
    assert root_type.reference is not None

    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__
    store.detach_model_data_type_refs(model)
    assert {
        "cache_key": model._IMPORTS_CACHE_KEY in model.__dict__,
        "root_reference": root_type.reference,
    } == snapshot({
        "cache_key": False,
        "root_reference": None,
    })


def test_generation_store_reference_redirects_clear_each_owner_imports_cache_once() -> None:
    """Reference redirects should clear each affected owner model once."""
    old_reference = Reference(path="Old", original_name="Old", name="Old")
    new_reference = Reference(path="New", original_name="New", name="New")
    model = _base_model(
        fields=[
            DataModelField(data_type=DataType(reference=old_reference)),
            DataModelField(data_type=DataType(reference=old_reference)),
        ]
    )
    store = GenerationStore()
    store.register_model(model)
    assert model.imports is not None
    assert model._IMPORTS_CACHE_KEY in model.__dict__

    store.redirect_reference_users(old_reference, new_reference)

    assert {
        "cache_key": model._IMPORTS_CACHE_KEY in model.__dict__,
        "field_references": [field.data_type.reference.path for field in model.fields if field.data_type.reference],
        "old_children": old_reference.children,
        "new_children": len(new_reference.children),
    } == snapshot({
        "cache_key": False,
        "field_references": ["New", "New"],
        "old_children": [],
        "new_children": 2,
    })


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param("replace_field_type", id="field-type"),
        pytest.param("replace_data_type_ref", id="reference"),
        pytest.param("replace_nested_reference", id="nested-reference"),
        pytest.param("set_nested_data_types", id="nested-types"),
        pytest.param("redirect_reference_users", id="redirect"),
    ],
)
@pytest.mark.parametrize("starts_self_referencing", [False, True])
def test_generation_store_type_mutations_invalidate_semantic_and_render_caches(
    mutation: str,
    *,
    starts_self_referencing: bool,
) -> None:
    """Every store type mutation must invalidate field semantics and model identity."""
    model_reference = Reference(path="Node", original_name="Node", name="Node")
    other_reference = Reference(path="Other", original_name="Other", name="Other")
    old_reference, new_reference = (
        (model_reference, other_reference) if starts_self_referencing else (other_reference, model_reference)
    )
    nested_data_type = DataType(reference=old_reference)
    data_type = (
        DataType(data_types=[nested_data_type])
        if mutation in {"replace_nested_reference", "set_nested_data_types"}
        else nested_data_type
    )
    if data_type is not nested_data_type:
        nested_data_type.parent = data_type
    field = DataModelField(name="value", data_type=data_type)
    if mutation in {"replace_nested_reference", "set_nested_data_types"}:
        nested_data_type.parent = field.data_type
    model = BaseModel(fields=[field], reference=model_reference)
    store = GenerationStore()
    store.register_model(model)

    assert field.self_reference() is starts_self_referencing
    assert model.get_dedup_key()

    match mutation:
        case "replace_field_type":
            store.replace_field_type(field, DataType(reference=new_reference))
        case "replace_data_type_ref" | "replace_nested_reference":
            store.replace_data_type_ref(nested_data_type, new_reference)
        case "set_nested_data_types":
            store.set_nested_data_types(data_type, [DataType(reference=new_reference)])
        case "redirect_reference_users":
            store.redirect_reference_users(old_reference, new_reference)
        case _:  # pragma: no cover
            pytest.fail(f"Unsupported mutation: {mutation}")

    assert field.self_reference() is not starts_self_referencing
    assert model._dedup_key_cache == {}


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param("append_field", id="append"),
        pytest.param("insert_field", id="insert"),
        pytest.param("set_fields", id="set"),
    ],
)
def test_generation_store_field_ownership_mutations_invalidate_self_reference_cache(mutation: str) -> None:
    """Moving a cached field to another model must refresh its parent semantics."""
    original_model = _base_model("Original")
    field = DataModelField(name="value", data_type=DataType(reference=original_model.reference))
    original_model.fields.append(field)
    field.parent = original_model
    destination_model = _base_model("Destination")
    store = GenerationStore()
    store.register_model(original_model)
    store.register_model(destination_model)

    assert field.self_reference()

    match mutation:
        case "append_field":
            store.append_field(destination_model, field)
        case "insert_field":
            store.insert_field(destination_model, 0, field)
        case "set_fields":
            store.set_fields(destination_model, [field])
        case _:  # pragma: no cover
            pytest.fail(f"Unsupported mutation: {mutation}")

    assert field.parent is destination_model
    assert not field.self_reference()


def test_generation_store_field_removal_invalidates_self_reference_cache() -> None:
    """Detaching a cached field must clear parent-dependent semantics."""
    model = _base_model("Model")
    field = DataModelField(name="value", data_type=DataType(reference=model.reference))
    model.fields.append(field)
    field.parent = model
    store = GenerationStore()
    store.register_model(model)

    assert field.self_reference()

    store.remove_field(model, field)

    assert field.parent is None
    assert not field.self_reference()


def test_generation_store_self_reference_invalidation_updates_pydantic_constraints() -> None:
    """Pydantic constraints must follow the current, not cached, self-reference state."""
    model_reference = Reference(path="Node", original_name="Node", name="Node")
    other_reference = Reference(path="Other", original_name="Other", name="Other")
    data_type = DataType(reference=other_reference)
    field = DataModelField(
        name="value",
        data_type=data_type,
        constraints=PydanticConstraints(pattern="x"),
    )
    model = BaseModel(fields=[field], reference=model_reference)
    store = GenerationStore()
    store.register_model(model)

    assert str(field) == "Field(None, pattern='x')"

    store.replace_data_type_ref(data_type, model_reference)

    assert not str(field)


def test_generation_store_self_reference_invalidation_updates_msgspec_metadata() -> None:
    """Msgspec metadata and imports must follow the current self-reference state."""
    model_reference = Reference(path="Node", original_name="Node", name="Node")
    other_reference = Reference(path="Other", original_name="Other", name="Other")
    data_type = DataType(reference=other_reference)
    field = MsgspecDataModelField(
        name="value",
        data_type=data_type,
        constraints=MsgspecConstraints(pattern="x"),
        required=True,
        use_annotated=True,
    )
    model = MsgspecStruct(fields=[field], reference=model_reference)
    store = GenerationStore()
    store.register_model(model)

    assert field.annotated == "Annotated[Other, Meta(pattern='x')]"
    assert field.imports

    store.replace_data_type_ref(data_type, model_reference)

    assert field.annotated is None
    assert field.imports == ()


def test_generation_store_redirects_unattached_reference_users() -> None:
    """Unattached data types remain supported without a field or model cache owner."""
    old_reference = Reference(path="Old", original_name="Old", name="Old")
    new_reference = Reference(path="New", original_name="New", name="New")
    data_type = DataType(reference=old_reference)
    store = GenerationStore()

    store.redirect_reference_users(old_reference, new_reference)

    assert data_type.reference is new_reference


def test_generation_store_render_cache_invalidation_supports_legacy_model_hook() -> None:
    """External model implementations with only the legacy import hook remain compatible."""

    class ImportsOnlyModel:
        def __init__(self) -> None:
            self.reference = Reference(path="Before", original_name="Before", name="Before")
            self.class_name = "Before"
            self.cache_cleared = False

        def clear_imports_cache(self) -> None:
            self.cache_cleared = True

    model = ImportsOnlyModel()
    store = GenerationStore()

    store.update_model_reference(model, class_name="After")  # ty: ignore[invalid-argument-type]

    assert model.class_name == "After"
    assert model.cache_cleared


def test_generation_store_model_reference_redirect_clears_only_affected_owner_imports_cache() -> None:
    """Scoped model reference redirects should invalidate only matching owner models."""
    target_model = _base_model("Target")
    owner_model = _base_model(
        "Owner",
        fields=[
            DataModelField(data_type=DataType(reference=target_model.reference)),
            DataModelField(data_type=DataType(reference=target_model.reference)),
        ],
    )
    other_model = _base_model("Other", fields=[DataModelField(data_type=DataType(reference=target_model.reference))])
    store = GenerationStore()
    store.register_model(target_model)
    store.register_model(owner_model)
    store.register_model(other_model)
    assert owner_model.imports is not None
    assert other_model.imports is not None
    assert owner_model._IMPORTS_CACHE_KEY in owner_model.__dict__
    assert other_model._IMPORTS_CACHE_KEY in other_model.__dict__

    store.redirect_model_reference_users(
        target_model,
        [owner_model],
        Reference(path="NewTarget", original_name="NewTarget", name="NewTarget"),
    )

    assert {
        "owner_cache_key": owner_model._IMPORTS_CACHE_KEY in owner_model.__dict__,
        "other_cache_key": other_model._IMPORTS_CACHE_KEY in other_model.__dict__,
        "owner_references": [
            field.data_type.reference.path for field in owner_model.fields if field.data_type.reference
        ],
        "other_references": [
            field.data_type.reference.path for field in other_model.fields if field.data_type.reference
        ],
    } == snapshot({
        "owner_cache_key": False,
        "other_cache_key": True,
        "owner_references": ["NewTarget", "NewTarget"],
        "other_references": ["Target"],
    })


def test_generation_store_reference_redirect_invalidates_base_class_owner_render_cache() -> None:
    """Unscoped redirects must invalidate models that own parentless base class types."""
    target_model = _base_model("Target")
    owner_model = BaseModel(
        fields=[],
        base_classes=[target_model.reference],
        reference=Reference(path="Owner", original_name="Owner", name="Owner"),
    )
    new_reference = Reference(path="NewTarget", original_name="NewTarget", name="NewTarget")
    store = GenerationStore()
    store.register_model(target_model)
    store.register_model(owner_model)
    original_key = owner_model.get_dedup_key()

    store.redirect_reference_users(target_model.reference, new_reference)

    assert owner_model.base_classes[0].reference is new_reference
    assert owner_model._dedup_key_cache == {}
    assert owner_model.get_dedup_key() != original_key


def test_generation_store_model_reference_redirect_resolves_base_class_owner() -> None:
    """Scoped redirects must resolve base class ownership from generation facts."""
    target_model = _base_model("Target")
    owner_model = BaseModel(
        fields=[],
        base_classes=[target_model.reference],
        reference=Reference(path="Owner", original_name="Owner", name="Owner"),
    )
    other_model = BaseModel(
        fields=[],
        base_classes=[target_model.reference],
        reference=Reference(path="Other", original_name="Other", name="Other"),
    )
    new_reference = Reference(path="NewTarget", original_name="NewTarget", name="NewTarget")
    store = GenerationStore()
    store.register_model(target_model)
    store.register_model(owner_model)
    store.register_model(other_model)
    assert owner_model.get_dedup_key()
    assert other_model.get_dedup_key()

    store.redirect_model_reference_users(target_model, [owner_model], new_reference)

    assert owner_model.base_classes[0].reference is new_reference
    assert owner_model._dedup_key_cache == {}
    assert other_model.base_classes[0].reference is target_model.reference
    assert other_model._dedup_key_cache


def test_generation_store_model_reference_redirect_invalidates_shared_base_class_owners() -> None:
    """A shared base class occurrence must invalidate every owning model."""
    target_model = _base_model("Target")
    first_model = _base_model("First")
    second_model = _base_model("Second")
    third_model = _base_model("Third")
    shared_base_class = BaseClassDataType(reference=target_model.reference)
    new_reference = Reference(path="NewTarget", original_name="NewTarget", name="NewTarget")
    store = GenerationStore()
    store.register_model(target_model)
    store.register_model(first_model)
    store.register_model(second_model)
    store.register_model(third_model)
    store.set_base_classes(first_model, [shared_base_class])
    store.set_base_classes(second_model, [shared_base_class])
    store.set_base_classes(third_model, [shared_base_class])
    assert first_model.get_dedup_key()
    assert second_model.get_dedup_key()
    assert third_model.get_dedup_key()

    store.redirect_model_reference_users(target_model, [first_model], new_reference)

    assert shared_base_class.reference is new_reference
    assert first_model._dedup_key_cache == {}
    assert second_model._dedup_key_cache == {}
    assert third_model._dedup_key_cache == {}


def test_generation_store_indexes_each_shared_base_class_owner_once() -> None:
    """Repeated shared base occurrences must not duplicate the owning model id."""
    first_model = _base_model("First")
    second_model = _base_model("Second")
    shared_base_class = BaseClassDataType(type="object")
    store = GenerationStore()
    store.register_model(first_model)
    store.register_model(second_model)
    store.set_base_classes(first_model, [shared_base_class])
    store.set_base_classes(second_model, [shared_base_class, shared_base_class])

    facts = store.current_facts()
    owner_ids = facts.base_owner_model_ids_by_object[id(shared_base_class)]

    assert isinstance(owner_ids, list)
    assert [facts.model_facts[model_id].model for model_id in owner_ids] == [first_model, second_model]


def test_generation_store_detach_model_refs_resolves_deferred_base_class_owner() -> None:
    """Deferred mutations must resolve live base class ownership before facts exist."""
    target_model = _base_model("Target")
    owner_model = BaseModel(
        fields=[],
        base_classes=[target_model.reference],
        reference=Reference(path="Owner", original_name="Owner", name="Owner"),
    )
    store = GenerationStore()
    store.register_model(target_model)
    store.register_model(owner_model)
    assert owner_model.get_dedup_key()

    store.detach_model_data_type_refs(owner_model)

    assert owner_model.base_classes[0].reference is None
    assert owner_model._dedup_key_cache == {}


def test_generation_store_redirects_model_reference_users_by_owner() -> None:
    """Reference redirection should only affect children owned by the requested models."""
    reference_target = Reference(path="Target", original_name="Target", name="Target")
    reference_owner = Reference(path="Owner", original_name="Owner", name="Owner")
    reference_other = Reference(path="Other", original_name="Other", name="Other")
    reference_new = Reference(path="New", original_name="New", name="New")
    target_model = BaseModel(fields=[], reference=reference_target)
    owner_type = DataType(reference=reference_target)
    other_type = DataType(reference=reference_target)
    owner_model = BaseModel(fields=[DataModelField(data_type=owner_type)], reference=reference_owner)
    other_model = BaseModel(fields=[DataModelField(data_type=other_type)], reference=reference_other)
    store = GenerationStore()
    store.register_model(target_model)
    store.register_model(owner_model)
    store.register_model(other_model)

    store.redirect_model_reference_users(target_model, [owner_model], reference_new)

    assert {
        "owner_reference": owner_type.reference.path if owner_type.reference else None,
        "other_reference": other_type.reference.path if other_type.reference else None,
        "old_children": [child is other_type for child in reference_target.children],
        "new_children": [child is owner_type for child in reference_new.children],
        "owner_reference_classes": sorted(store.index.reference_classes_for_model(owner_model)),
        "other_reference_classes": sorted(store.index.reference_classes_for_model(other_model)),
    } == snapshot(
        {
            "owner_reference": "New",
            "other_reference": "Target",
            "old_children": [True],
            "new_children": [True],
            "owner_reference_classes": ["New"],
            "other_reference_classes": ["Target"],
        },
    )


def test_generation_store_collapse_and_attach_reference_edges() -> None:
    """Root collapse and first reference attachment should keep compatibility children aligned."""
    reference_model = Reference(path="Model", original_name="Model", name="Model")
    reference_outer = Reference(path="Outer", original_name="Outer", name="Outer")
    reference_inner = Reference(path="Inner", original_name="Inner", name="Inner")
    reference_attached = Reference(path="Attached", original_name="Attached", name="Attached")
    root_type = DataType(reference=reference_outer)
    unreferenced_root_type = DataType()
    model = BaseModel(fields=[DataModelField(data_type=root_type)], reference=reference_model)
    unattached_type = DataType()
    detached_type = DataType()
    store = GenerationStore()
    store.register_model(model)

    store.collapse_root_data_type(root_type, reference_inner)
    store.collapse_root_data_type(unreferenced_root_type, reference_inner)
    store.replace_data_type_ref(unattached_type, reference_attached)
    store.detach_data_type_ref(detached_type)

    assert {
        "iter_paths": [tracked_model.reference.path for tracked_model in store],
        "root_reference": root_type.reference.path if root_type.reference else None,
        "unreferenced_root_reference": (
            unreferenced_root_type.reference.path if unreferenced_root_type.reference else None
        ),
        "detached_reference": detached_type.reference.path if detached_type.reference else None,
        "old_children": [child is root_type for child in reference_outer.children],
        "inner_children": [child is root_type or child is unreferenced_root_type for child in reference_inner.children],
        "attached_children": [child is unattached_type for child in reference_attached.children],
        "reference_classes": sorted(store.index.reference_classes_for_model(model)),
    } == snapshot(
        {
            "iter_paths": ["Model"],
            "root_reference": "Inner",
            "unreferenced_root_reference": "Inner",
            "detached_reference": None,
            "old_children": [],
            "inner_children": [True, True],
            "attached_children": [True],
            "reference_classes": ["Inner"],
        },
    )


def test_generation_store_root_collapse_scope_tracks_deltas_until_exit() -> None:
    """Incremental counts follow supported root mutations and refresh once at exit."""
    model_reference = Reference(path="Model", original_name="Model", name="Model")
    outer_reference = Reference(path="Outer", original_name="Outer", name="Outer")
    inner_reference = Reference(path="Inner", original_name="Inner", name="Inner")
    root_type = DataType(reference=outer_reference)
    other_type = DataType(reference=outer_reference)
    model = BaseModel(
        fields=[DataModelField(data_type=root_type), DataModelField(data_type=other_type)],
        reference=model_reference,
    )
    outer_model = _base_model("Outer")
    outer_model.reference = outer_reference
    inner_model = _base_model("Inner")
    inner_model.reference = inner_reference
    store = GenerationStore()
    store.register_model(model)
    store.register_model(outer_model)
    store.register_model(inner_model)
    store.refresh()
    version_before_scope = store.facts_version

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert scope.enabled
        assert store._root_collapse_has_data_type_references(outer_reference)
        assert store._root_collapse_has_data_type_references(outer_reference, excluded_data_type=root_type)
        store.collapse_root_data_type(root_type, inner_reference)
        assert store._root_collapse_has_data_type_references(outer_reference)
        assert store._root_collapse_has_data_type_references(inner_reference)
        assert store.facts_version == version_before_scope

    assert store._active_root_collapse_reference_scope is None
    assert store.facts_version == version_before_scope + 1
    assert store.index.has_data_type_references(outer_reference)
    assert store.index.has_data_type_references(inner_reference)


def test_generation_store_root_collapse_scope_rejects_nesting() -> None:
    """One collapse pass owns the tracker scope exclusively."""
    store = GenerationStore()

    def enter_nested_scope() -> None:
        with store._collapse_root_reference_scope():
            pass  # pragma: no cover

    with store._collapse_root_reference_scope(), pytest.raises(RuntimeError, match="cannot be nested"):
        enter_nested_scope()


def test_generation_store_root_collapse_scope_noop_stays_lazy() -> None:
    """A collapse pass with no candidates does not eagerly build generation facts."""
    model = _base_model(fields=[DataModelField(data_type=DataType(type="str"))])
    store = GenerationStore()
    store.register_model(model)
    version_before_scope = store.facts_version

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert scope.initialized is False
        assert scope.mutated is False

    assert store.facts_version == version_before_scope
    assert store._dirty
    assert store._active_root_collapse_reference_scope is None


def test_generation_store_root_collapse_scope_falls_back_inside_defer() -> None:
    """A deferred stale snapshot never enables incremental tracking."""
    model = _base_model(fields=[DataModelField(data_type=DataType(type="str"))])
    store = GenerationStore()
    store.register_model(model)

    with store.defer_refresh(), store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_has_data_type_references(model.reference) is None
        assert scope.enabled is False
        assert scope.reference_counts == {}


def test_generation_store_root_collapse_scope_exception_cleans_without_refresh() -> None:
    """An interrupted collapse leaves the old dirty-state timing but drops scope state."""

    class ScopeInterruptedError(Exception):
        pass

    model_reference = Reference(path="Model", original_name="Model", name="Model")
    outer_reference = Reference(path="Outer", original_name="Outer", name="Outer")
    inner_reference = Reference(path="Inner", original_name="Inner", name="Inner")
    root_type = DataType(reference=outer_reference)
    model = BaseModel(fields=[DataModelField(data_type=root_type)], reference=model_reference)
    outer_model = _base_model("Outer")
    outer_model.reference = outer_reference
    inner_model = _base_model("Inner")
    inner_model.reference = inner_reference
    store = GenerationStore()
    store.register_model(model)
    store.register_model(outer_model)
    store.register_model(inner_model)
    store.refresh()
    version_before_scope = store.facts_version
    scope = None

    def interrupted_scope() -> None:
        nonlocal scope
        with store._collapse_root_reference_scope():
            scope = store._active_root_collapse_reference_scope
            assert scope is not None
            store.collapse_root_data_type(root_type, inner_reference)
            assert scope.reference_counts
            raise ScopeInterruptedError

    with pytest.raises(ScopeInterruptedError):
        interrupted_scope()

    assert store._active_root_collapse_reference_scope is None
    assert store._dirty
    assert store.facts_version == version_before_scope
    assert scope is not None
    assert scope.enabled is False
    assert scope.reference_counts == {}


def test_generation_store_root_collapse_scope_disables_for_shared_data_type() -> None:
    """Aliased DataType occurrences conservatively use the normal fresh-index path."""
    model_reference = Reference(path="Model", original_name="Model", name="Model")
    target_reference = Reference(path="Target", original_name="Target", name="Target")
    shared_type = DataType(reference=target_reference)
    model = BaseModel(
        fields=[DataModelField(data_type=shared_type), DataModelField(data_type=shared_type)],
        reference=model_reference,
    )
    store = GenerationStore()
    store.register_model(model)

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_has_data_type_references(target_reference) is None
        assert scope.enabled is False

    assert store._active_root_collapse_reference_scope is None


def test_generation_store_root_collapse_scope_invalidates_excluded_fact_mismatch() -> None:
    """Missing or aliased excluded facts must not produce an unsafe count."""
    model_reference = Reference(path="Model", original_name="Model", name="Model")
    target_reference = Reference(path="Target", original_name="Target", name="Target")
    tracked_type = DataType(reference=target_reference)
    model = BaseModel(fields=[DataModelField(data_type=tracked_type)], reference=model_reference)
    store = GenerationStore()
    store.register_model(model)

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_has_data_type_references(target_reference)
        assert store._root_collapse_has_data_type_references(target_reference, excluded_data_type=DataType()) is None
        assert scope.enabled is False
        assert scope.reference_counts == {}

    other_type = DataType()
    store = GenerationStore()
    store.register_model(
        BaseModel(fields=[DataModelField(data_type=tracked_type)], reference=model_reference),
    )
    store.current_facts().data_type_fact_by_object[id(tracked_type)] = DataTypeFact(
        id=999,
        data_type=other_type,
        owner_model=0,
        owner_field_index=0,
        role="field",
        reference=None,
    )

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_has_data_type_references(target_reference, excluded_data_type=tracked_type) is None
        assert scope.enabled is False
        assert scope.reference_counts == {}


def test_generation_store_root_collapse_scope_public_query_refreshes_facts() -> None:
    """Public index access refreshes mutated facts and disables incremental state."""
    model_reference = Reference(path="Model", original_name="Model", name="Model")
    old_reference = Reference(path="Old", original_name="Old", name="Old")
    new_reference = Reference(path="New", original_name="New", name="New")
    data_type = DataType(reference=old_reference)
    model = BaseModel(fields=[DataModelField(data_type=data_type)], reference=model_reference)
    old_model = _base_model("Old")
    old_model.reference = old_reference
    new_model = _base_model("New")
    new_model.reference = new_reference
    store = GenerationStore()
    store.register_model(model)
    store.register_model(old_model)
    store.register_model(new_model)
    store.refresh()

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        store.collapse_root_data_type(data_type, new_reference)
        assert store.index.has_data_type_references(new_reference)
        assert scope.enabled is False
        assert scope.reference_counts == {}


def test_generation_store_root_collapse_scope_invalidates_structural_changes() -> None:
    """Model-list changes invalidate the scoped counts before they can be consumed."""
    model_reference = Reference(path="Model", original_name="Model", name="Model")
    target_reference = Reference(path="Target", original_name="Target", name="Target")
    data_type = DataType(reference=target_reference)
    model = BaseModel(fields=[DataModelField(data_type=data_type)], reference=model_reference)
    store = GenerationStore()
    store.register_model(model)

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_has_data_type_references(target_reference)
        store.register_model(_base_model("Other"))
        assert scope.enabled is False
        assert scope.reference_counts == {}
        assert store._root_collapse_has_data_type_references(target_reference) is None
        store.replace_data_type_ref(data_type, None)
        assert scope.enabled is False


def test_generation_store_root_collapse_scope_discards_counts_on_public_discard() -> None:
    """Explicit fact disposal invalidates and releases scoped reference counts."""
    model_reference = Reference(path="Model", original_name="Model", name="Model")
    target_reference = Reference(path="Target", original_name="Target", name="Target")
    data_type = DataType(reference=target_reference)
    model = BaseModel(fields=[DataModelField(data_type=data_type)], reference=model_reference)
    store = GenerationStore()
    store.register_model(model)

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_has_data_type_references(target_reference)
        store.discard_derived_facts()
        assert scope.enabled is False
        assert scope.reference_counts == {}


def test_generation_store_root_collapse_scope_rejects_untracked_replacement_trees() -> None:
    """New trees sharing tracked objects or containing aliases conservatively fall back."""
    model_reference = Reference(path="Model", original_name="Model", name="Model")
    target_reference = Reference(path="Target", original_name="Target", name="Target")
    tracked_child = DataType(reference=target_reference)
    model = BaseModel(fields=[DataModelField(data_type=tracked_child)], reference=model_reference)
    store = GenerationStore()
    store.register_model(model)

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_prepare_data_type_replacement(DataType(), DataType()) is None
        assert scope.enabled is False

    store = GenerationStore()
    store.register_model(
        BaseModel(fields=[DataModelField(data_type=tracked_child)], reference=model_reference),
    )
    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert (
            store._root_collapse_prepare_data_type_replacement(
                tracked_child,
                DataType(data_types=[tracked_child]),
            )
            is None
        )
        assert scope.enabled is False

    duplicate_child = DataType()
    store = GenerationStore()
    duplicate_old_type = DataType()
    store.register_model(BaseModel(fields=[DataModelField(data_type=duplicate_old_type)], reference=model_reference))
    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert (
            store._root_collapse_prepare_data_type_replacement(
                duplicate_old_type,
                DataType(data_types=[duplicate_child, duplicate_child]),
            )
            is None
        )
        assert scope.enabled is False


def test_generation_store_root_collapse_scope_rejects_invalid_deltas() -> None:
    """Unknown and negative deltas disable the tracker instead of corrupting counts."""
    target_reference = Reference(path="Target", original_name="Target", name="Target")
    store = GenerationStore()
    store.register_model(_base_model("Target"))
    store.models[0].reference = target_reference

    assert store._root_collapse_apply_reference_deltas({id(target_reference): 1}) is False

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        unknown_reference = Reference(path="Unknown", original_name="Unknown", name="Unknown")
        assert store._root_collapse_apply_reference_deltas({id(unknown_reference): 1}) is False
        assert scope.enabled is False
        assert scope.reference_counts == {}

    store = GenerationStore()
    store.register_model(_base_model("Target"))
    target_reference = store.models[0].reference
    store.current_facts()
    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_apply_reference_deltas({id(target_reference): -1}) is False
        assert scope.enabled is False
        assert scope.reference_counts == {}


def test_generation_store_root_collapse_scope_reference_change_fallbacks() -> None:
    """Reference delta helpers handle no-op and untracked direct calls safely."""
    target_reference = Reference(path="Target", original_name="Target", name="Target")
    new_reference = Reference(path="New", original_name="New", name="New")
    tracked_type = DataType(reference=target_reference)
    no_reference_type = DataType()
    model = _base_model(
        fields=[DataModelField(data_type=tracked_type), DataModelField(data_type=no_reference_type)],
    )
    store = GenerationStore()
    store.register_model(model)

    assert store._root_collapse_prepare_data_type_replacement(tracked_type, tracked_type) is None
    assert store._root_collapse_record_reference_change(tracked_type, target_reference, new_reference) is False

    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_record_reference_change(tracked_type, target_reference, target_reference)
        assert store._root_collapse_prepare_data_type_replacement(no_reference_type, DataType()) == {}
        assert store._root_collapse_prepare_data_type_replacement(
            no_reference_type,
            DataType(reference=target_reference),
        ) == {id(target_reference): 1}
        assert store._root_collapse_record_reference_change(tracked_type, target_reference, None)
        assert store._root_collapse_record_reference_change(tracked_type, None, new_reference) is False
        assert scope.enabled is False
        assert scope.reference_counts == {}

    store = GenerationStore()
    store.register_model(_base_model("Model"))
    untracked_type = DataType(reference=target_reference)
    with store._collapse_root_reference_scope():
        scope = store._active_root_collapse_reference_scope
        assert scope is not None
        assert store._root_collapse_record_reference_change(untracked_type, target_reference, new_reference) is False
        assert scope.enabled is False
        assert scope.reference_counts == {}


def test_generation_store_atomic_replacement_detaches_parented_orphan_without_scope() -> None:
    """The legacy parent-owner fallback still detaches references outside a scope."""
    model_reference = Reference(path="Model", original_name="Model", name="Model")
    target_reference = Reference(path="Target", original_name="Target", name="Target")
    nested_type = DataType(reference=target_reference)
    parent_type = DataType(data_types=[nested_type])
    field = DataModelField(data_type=parent_type)
    model = BaseModel(fields=[field], reference=model_reference)
    store = GenerationStore()
    store.register_model(model)

    with store._replace_data_type_and_detach_data_type_ref(
        nested_type,
        DataType(type="str"),
        owner=field,
        replacement_kind="field",
    ):
        pass

    assert nested_type.parent is parent_type
    assert nested_type.reference is None
