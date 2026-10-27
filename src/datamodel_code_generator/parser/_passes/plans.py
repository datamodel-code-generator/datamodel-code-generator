"""The plan passes (L4): what the passes that process each output module read, decided before the first module.

Each pass is a function of the parser and the run, called from the pipeline tables of ``parser/_passes/_tables.py``;
the parser comes in through ``PlansHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from collections import defaultdict
from functools import partial
from itertools import chain
from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol

from datamodel_code_generator import ReuseScope
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, reference_graph
from datamodel_code_generator._scc import find_circular_sccs
from datamodel_code_generator.model._decisions.tag_value import mapping_path_keys, schema_path_keys
from datamodel_code_generator.model.base import BaseLayoutView, _find_base_classes, linearize_data_models
from datamodel_code_generator.parser._graph_ids import keyed_models, unchecked
from datamodel_code_generator.parser._model_shapes import reuse_candidate_key
from datamodel_code_generator.parser._passes._shared import (
    InheritedFieldHost,
    _get_schema_path,
    _iter_discriminator_data_types,
    _module_name_from_module_path,
    override_required_field,
)
from datamodel_code_generator.parser._passes._state import RecursionFacts

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator import CollapseRootModelsNameStrategy
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser._passes._state import ModuleModels, StructuredRun
    from datamodel_code_generator.parser._passes.modules import _InlineVariantTags
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.reference import ModelType, Reference

__all__ = [
    "FlattenHost",
    "PlansHost",
    "flatten_conflicting_base_layouts",
    "record_emitting_modules",
    "reset_per_run_state",
    "set_circular_root_model_paths",
    "unconstrain_union_members_in_modules",
]


class _Clearable(Protocol):
    """Run state that the next parse starts without."""

    def clear(self) -> None:
        """Remove every entry."""


class FlattenHost(InheritedFieldHost, Protocol):
    """What making a model declare the fields of its bases reads and writes of the parser."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""


class PlansHost(FlattenHost, Protocol):
    """The options, run state and hooks of the parser that the plan passes read and write."""

    _emitting_module_lists: list[tuple[str, list[DataModel]]]
    _emitting_module_names: dict[int, str]
    _generic_base_model: DataModel | None
    _inline_variant_models: _InlineVariantTags | None
    _recursion_facts: RecursionFacts

    @property
    def _base_stand_ins(self) -> _Clearable:
        """The stand-ins of bases that the output cannot import, by base."""

    @property
    def _collapsed_reuse_models(self) -> dict[int, list[DataModel]]:
        """The models collapsed reuse removed, by the id of their canonical model."""

    @property
    def _deferred_tagged_unions(self) -> _Clearable:
        """The tagged unions whose tags wait until every module is processed."""

    @property
    def _hidden_named_types(self) -> _Clearable:
        """Whether a field hides the named type of its model, by model key and field name."""

    @property
    def _hiding_field_names(self) -> _Clearable:
        """The fields that hide a named type, by model key and field name."""

    @property
    def _model_type_override_imports(self) -> dict[str, Import]:
        """The imports of the types that override models, by model name."""

    @property
    def _prepared_modules(self) -> _Clearable:
        """The modules prepared for rendering."""

    @property
    def _removed_reuse_users(self) -> _Clearable:
        """The data types whose duplicate reuse removed, with the paths they named."""

    @property
    def _renamed_type_paths(self) -> _Clearable:
        """The class names renamed types had, by the path imports of them were added under."""

    @property
    def _renamed_types(self) -> _Clearable:
        """The class names renamed types had, by model key."""

    @property
    def _replaced_root_models(self) -> _Clearable:
        """The roots that collapse replaced by their inner models."""

    @property
    def _variant_discriminator_tags(self) -> _Clearable:
        """The tags of discriminated union variants, by variant and tag field."""

    @property
    def collapse_root_models(self) -> bool:
        """Whether root models are collapsed into their users."""

    @property
    def collapse_root_models_name_strategy(self) -> CollapseRootModelsNameStrategy | None:
        """How collapsed root models name the models that replace them, if configured."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def field_name_model_type(self) -> ModelType:
        """The model type that field names are validated for."""

    @property
    def reuse_model(self) -> bool:
        """Whether models that render the same are reused."""

    @property
    def reuse_scope(self) -> ReuseScope | None:
        """The scope of reuse."""


def reset_per_run_state(parser: PlansHost, _run: StructuredRun[Any]) -> None:
    """Forget what the passes of an earlier parse recorded on the parser."""
    parser._hidden_named_types.clear()  # noqa: SLF001
    parser._hiding_field_names.clear()  # noqa: SLF001
    parser._variant_discriminator_tags.clear()  # noqa: SLF001
    parser._inline_variant_models = None  # noqa: SLF001
    parser._deferred_tagged_unions.clear()  # noqa: SLF001
    parser._prepared_modules.clear()  # noqa: SLF001
    parser._base_stand_ins.clear()  # noqa: SLF001
    parser._generic_base_model = None  # noqa: SLF001
    parser._removed_reuse_users.clear()  # noqa: SLF001
    parser._renamed_types.clear()  # noqa: SLF001
    parser._renamed_type_paths.clear()  # noqa: SLF001
    parser._replaced_root_models.clear()  # noqa: SLF001


def flatten_conflicting_base_layouts(parser: PlansHost, module_models: ModuleModels) -> None:
    """Make the models whose generated bases the output cannot combine declare every field without them.

    Bases are handled before the models inheriting from them, as their own fields then give the layout. The
    layouts are seen as the processing of each module may leave them: a discriminator field may become a msgspec
    tag, which takes no slot, and reuse may make a duplicate an empty subclass of the model it duplicates, whose
    layout it then has. A model that this processing leaves conflicting keeps failing the final check of the
    output. A model that a type override replaces has a layout the generator does not know, as a configured base
    class has.
    """
    if not parser.data_model_type.FLATTENS_CONFLICTING_BASE_LAYOUTS:
        return
    models = [model for _, models in module_models for model in models]
    if not (joins := [model for model in models if len(model.base_classes) > 1]):
        return
    duplicates = find_possible_reuse_duplicates(parser, module_models)
    layout_models: dict[int, DataModel] = {}
    pending = joins.copy()
    while pending:
        if id(model := pending.pop()) in layout_models:
            continue
        layout_models[id(model)] = model
        pending.extend(_find_base_classes(model))
        if (duplicated := duplicates.get(id(model))) is not None:
            pending.append(duplicated)
    tag_names = find_possible_tag_names(parser, models, list(layout_models.values()))
    overrides = parser._model_type_override_imports  # noqa: SLF001

    def is_slot(field: DataModelFieldBase) -> bool:
        names = tag_names.get(id(field.parent), frozenset())
        return not field.is_class_var and field.name not in names and field.original_name not in names

    view = BaseLayoutView(
        is_slot,
        lambda model: duplicates.get(id(model), model),
        lambda model: model.reference.name in overrides,
    )
    visits = [(model, False) for model in reversed(joins)]
    visited: set[int] = set()
    while visits:
        model, complete = visits.pop()
        if complete and len(model.base_classes) > 1:
            model.flatten_conflicting_base_layouts(view, partial(flatten_base_layouts, parser))
        elif not complete and id(model) not in visited:
            visited.add(id(model))
            visits.append((model, True))
            visits.extend((base, False) for base in reversed(_find_base_classes(model)))


def flatten_base_layouts(parser: FlattenHost, model: DataModel) -> None:
    """Make a model declare the fields of its generated bases and drop them.

    The required names the model and its bases take from their own bases are resolved first, as only the bases
    give their fields. A field becomes required as a required name makes it.
    """
    override_required_field(parser, linearize_data_models([model])[::-1])
    own_fields = {id(field) for field in model.fields}

    def adopt(field: DataModelFieldBase, required: bool) -> DataModelFieldBase:  # noqa: FBT001
        if id(field) not in own_fields:
            field = parser._copy_model_field(field)  # noqa: SLF001
        if required and not field.required:
            parser._graph_builder.require_field(  # noqa: SLF001
                field,
                use_default_with_required=parser.apply_default_values_for_required_fields and field.has_default,
            )
        return field

    fields = model.flatten_base_layouts(adopt)
    with parser._graph_builder.defer_refresh():  # noqa: SLF001
        for base_class in model.base_classes:
            parser._graph_builder.detach_data_type_ref(base_class)  # noqa: SLF001
        parser._graph_builder.set_fields(model, fields)  # noqa: SLF001
        parser._graph_builder.reset_base_classes(model)  # noqa: SLF001


def find_possible_tag_names(
    parser: PlansHost, models: list[DataModel], layout_models: list[DataModel]
) -> dict[int, frozenset[str]]:
    """Return, by model whose layout counts, the names of the fields that the discriminator step may make tags.

    It tags the variants that discriminated unions list, however nested or aliased, or that their mappings name,
    including those of the unions collapsed reuse removed, by the property name or the field name it resolves to.
    A struct inheriting from a tagged struct takes its tag field as a tag too. The mapped paths are indexed once
    by every key a model path can match them by.
    """
    listed: defaultdict[int, set[str]] = defaultdict(set)
    mapped: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for model in chain(models, chain.from_iterable(parser._collapsed_reuse_models.values())):  # noqa: SLF001
        for field in model.fields:
            if not (
                isinstance(discriminator := field.extras.get("discriminator"), dict)
                and isinstance(property_name := discriminator.get("propertyName"), str)
                and property_name
            ):
                continue
            for reference in (
                data_type.reference
                for data_type, _, _ in _iter_discriminator_data_types(field.data_type.all_data_types)
                if data_type.reference
            ):
                listed[id(reference.source)].add(property_name)
            if isinstance(mapping := discriminator.get("mapping"), dict):
                for path in mapping.values():
                    for key in mapping_path_keys(str(path), rule="legacy-any-key"):
                        mapped[key].add(property_name)
    variant_names: dict[int, set[str]] = {}
    for model in layout_models:
        property_names = set(listed.get(id(model), ()))
        for reference in (model.reference, *(base.reference for base in model.base_classes if base.reference)):
            for key in schema_path_keys(_get_schema_path(reference)):
                property_names.update(mapped.get(key, ()))
        variant_names[id(model)] = {
            name
            for property_name in property_names
            for class_name in (None, model.class_name)
            for name in (
                property_name,
                parser.model_resolver.get_valid_field_name_and_alias(
                    field_name=property_name, model_type=parser.field_name_model_type, class_name=class_name
                )[0],
            )
        }
    return {
        id(model): frozenset(
            chain.from_iterable(variant_names.get(id(ancestor), ()) for ancestor in linearize_data_models([model]))
        )
        for model in layout_models
    }


def find_possible_reuse_duplicates(parser: PlansHost, module_models: ModuleModels) -> dict[int, DataModel]:
    """Return, by model, the earlier model of its module that reuse may find it a duplicate of.

    Reuse compares rendered models, so a duplicate has the type, the bases and the field names of the model it
    duplicates.
    """
    duplicates: dict[int, DataModel] = {}
    if not parser.reuse_model or parser.reuse_scope == ReuseScope.Tree:
        return duplicates
    for _, models in module_models:
        firsts: dict[tuple[object, ...], DataModel] = {}
        for model in models:
            first = firsts.setdefault(
                reuse_candidate_key(
                    type(model),
                    (base.reference.path if base.reference else base.type_hint for base in model.base_classes),
                    (field.name for field in model.fields),
                ),
                model,
            )
            if first is not model:
                duplicates[id(model)] = first
    return duplicates


def unconstrain_union_members_in_modules(parser: PlansHost, run: StructuredRun[Any]) -> None:
    """Drop the constraints of union members, which tagged unions cannot carry."""
    for _, models in run.module_models:
        for field in (field for model in models for field in model.fields):
            parser._graph_builder.drop_union_member_constraints(field, refresh="deferred")  # noqa: SLF001


def set_circular_root_model_paths(
    parser: PlansHost,
    module_models: ModuleModels,
    *,
    require_dict_key_edge: bool = False,
) -> None:
    """Record the root models in circular components before collapsing them."""
    snapshot = GraphSnapshot.CIRCULAR_ROOT_MODELS
    keyed = keyed_models(
        (model for _, models in module_models for model in models if parser._is_root_model(model)),  # noqa: SLF001
        snapshot.value,
    )
    index = parser.generation_store.index
    edges = {
        EdgeKind.ANY_REFERENCE: {
            key: index.referenced_keys_for_model(model, keyed, all_dict_keys=True)
            for key, model in keyed.models.items()
        }
    }
    if require_dict_key_edge:
        edges[EdgeKind.MAPPING_KEY] = {
            key: keyed.keys_of(root_dict_key_references(model)) for key, model in keyed.models.items()
        }
    graph = reference_graph(snapshot, keyed.models, edges)

    def circular_keys(
        components: list[set[ModelKey]],
        on_self_loop: Callable[[ModelKey], bool],
        mapping_keys: Callable[[ModelKey], frozenset[ModelKey]],
    ) -> frozenset[ModelKey]:
        if not require_dict_key_edge:
            return frozenset(key for component in components for key in component)
        return frozenset(key for key in keyed.models if on_self_loop(key)).union(
            *(component for component in components if any(mapping_keys(key) & component for key in component))
        )

    def old() -> frozenset[ModelKey]:
        silent = unchecked(keyed)
        old_graph = {
            key: index.referenced_keys_for_model(model, silent, all_dict_keys=True) & keyed.models.keys()
            for key, model in keyed.models.items()
        }
        return circular_keys(
            find_circular_sccs(old_graph),
            lambda key: key in old_graph[key],
            lambda key: silent.keys_of(root_dict_key_references(keyed.models[key])),
        )

    circular_roots = graph_answer(
        snapshot,
        circular_keys(
            graph.circular_components((EdgeKind.ANY_REFERENCE,)),
            lambda key: key in graph.targets(key, (EdgeKind.ANY_REFERENCE,)),
            lambda key: graph.targets(key, (EdgeKind.MAPPING_KEY,)),
        ),
        old,
    )
    recursion_facts = parser._recursion_facts  # noqa: SLF001
    parser._recursion_facts = RecursionFacts(  # noqa: SLF001
        require_update=recursion_facts["require_update"], circular_roots=circular_roots
    )


def root_dict_key_references(model: DataModel) -> list[Reference]:
    """Return the model references that mapping-key annotations of ``model`` reach."""
    references: list[Reference] = []
    data_types = [field.data_type for field in model.fields]
    seen_data_types: set[int] = set()
    while data_types:
        data_type = data_types.pop()
        if id(data_type) in seen_data_types:  # pragma: no cover - parser-owned DataType graphs are trees
            continue
        seen_data_types.add(id(data_type))
        if (dict_key := data_type.dict_key) is not None:
            if dict_key.reference:
                references.append(dict_key.reference)
            data_types.append(dict_key)
        data_types.extend(data_type.data_types)
    return references


def record_emitting_modules(parser: PlansHost, run: StructuredRun[Any]) -> None:
    """Record the module that writes each model, for ``--collapse-root-models-name-strategy``."""
    parser._emitting_module_lists = (  # noqa: SLF001
        [(_module_name_from_module_path(module_), models) for module_, models in run.module_models]
        if parser.collapse_root_models and parser.collapse_root_models_name_strategy is not None
        else []
    )
    parser._emitting_module_names = {  # noqa: SLF001
        id(model): module_name
        for module_name, models in parser._emitting_module_lists  # noqa: SLF001
        for model in models
    }
