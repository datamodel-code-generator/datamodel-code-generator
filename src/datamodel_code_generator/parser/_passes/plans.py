"""The plan passes (L4): what the passes that process each output module read, decided before the first module.

Each pass is a function of the parser and the run, called from the pipeline tables of ``parser/_passes/_tables.py``;
the parser comes in through ``PlansHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from functools import partial
from itertools import chain
from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol

from datamodel_code_generator import ReuseScope
from datamodel_code_generator._graph import post_order, reach
from datamodel_code_generator._module_paths import dotted_module
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot
from datamodel_code_generator._user_keys import model_type_override
from datamodel_code_generator.model.base import BaseLayoutView, _find_base_classes, linearize_data_models
from datamodel_code_generator.parser._field_rows import require_field
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._model_shapes import reuse_candidate_key
from datamodel_code_generator.parser._passes._shared import (
    DataModelTypeHost,
    GraphBuilderHost,
    InheritedFieldHost,
    RecursionFactsHost,
    ReuseOptionsHost,
    override_required_field,
)
from datamodel_code_generator.parser._passes._state import RecursionFacts
from datamodel_code_generator.parser._tag_plan import TagPlanHost, field_namer, iter_union_sites, possible_tag_names

if TYPE_CHECKING:
    from datamodel_code_generator import CollapseRootModelsNameStrategy
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser._passes._state import ModuleModels, StructuredRun, _KeptParentRoot
    from datamodel_code_generator.parser._tag_plan import TagCapabilities
    from datamodel_code_generator.reference import ModelType, Reference
    from datamodel_code_generator.types import DataType, DataTypeManager

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


class FlattenHost(InheritedFieldHost, GraphBuilderHost, Protocol):
    """What making a model declare the fields of its bases reads and writes of the parser."""

    @property
    def keep_defaults_of_inherited_required_fields(self) -> bool:
        """Whether a field only a base requires keeps its default (``--keep-defaults-of-inherited-required-fields``)."""


class PlansHost(RecursionFactsHost, FlattenHost, ReuseOptionsHost, DataModelTypeHost, TagPlanHost, Protocol):
    """The options, run state and hooks of the parser that the plan passes read and write."""

    _emitting_module_lists: list[tuple[str, list[DataModel]]]
    _emitting_module_names: dict[int, str]
    _generic_base_model: DataModel | None

    @property
    def data_type_manager(self) -> DataTypeManager:
        """The type manager of the output model, whose constraint options the union members are read with."""

    @property
    def _base_stand_ins(self) -> dict[int, Reference | None]:
        """The stand-ins of bases that the output cannot import, by base."""

    @property
    def _collapsed_reuse_models(self) -> dict[int, list[DataModel]]:
        """The models collapsed reuse removed, by the id of their canonical model."""

    @property
    def _hidden_named_types(self) -> dict[tuple[ModelKey, str | None], bool]:
        """Whether a field hides the named type of its model, by model key and field name."""

    @property
    def _hiding_field_names(self) -> dict[tuple[ModelKey, str | None], str]:
        """The fields that hide a named type, by model key and field name."""

    @property
    def _kept_parent_roots(self) -> dict[int, _KeptParentRoot]:
        """The roots that the ``parent`` strategy kept for their later holders."""

    @property
    def _model_type_override_imports(self) -> dict[str, Import]:
        """The imports of the types that override models, by model name."""

    @property
    def _prepared_modules(self) -> _Clearable:
        """The modules prepared for rendering."""

    @property
    def _removed_reuse_users(self) -> dict[int, tuple[DataType, list[str]]]:
        """The data types whose duplicate reuse removed, with the paths they named."""

    @property
    def _replaced_root_models(self) -> dict[int, tuple[DataModel, DataModel]]:
        """The roots that collapse replaced by their inner models."""

    @property
    def _tag_capabilities(self) -> TagCapabilities:
        """The rows of the output's model and field classes that the tag passes read."""

    @property
    def collapse_root_models_name_strategy(self) -> CollapseRootModelsNameStrategy | None:
        """How collapsed root models name the models that replace them, if configured."""

    @property
    def field_name_model_type(self) -> ModelType:
        """The model type that field names are validated for."""


def reset_per_run_state(parser: PlansHost, _run: StructuredRun[Any]) -> None:
    """Forget what the passes of an earlier parse recorded on the parser."""
    parser._hidden_named_types.clear()  # noqa: SLF001
    parser._hiding_field_names.clear()  # noqa: SLF001
    parser._tag_plan.reset()  # noqa: SLF001
    parser._prepared_modules.clear()  # noqa: SLF001
    parser._base_stand_ins.clear()  # noqa: SLF001
    parser._generic_base_model = None  # noqa: SLF001
    parser._removed_reuse_users.clear()  # noqa: SLF001
    parser._replaced_root_models.clear()  # noqa: SLF001
    parser._kept_parent_roots.clear()  # noqa: SLF001


def flatten_conflicting_base_layouts(parser: PlansHost, module_models: ModuleModels) -> None:
    """Make the models whose generated bases the output cannot combine declare every field without them.

    Bases are handled before the models inheriting from them, as their own fields then give the layout. The
    layouts are seen as the processing of each module may leave them: a discriminator field may become a msgspec
    tag, which takes no slot, and reuse may make a duplicate an empty subclass of the model it duplicates, whose
    layout it then has. A model that this processing leaves conflicting keeps failing the final check of the
    output. A model that a type override replaces has a layout the generator does not know, as a configured base
    class has.
    """
    if not parser._tag_capabilities.flattens_layouts:  # noqa: SLF001
        return
    models = [model for _, models in module_models for model in models]
    if not (joins := [model for model in models if len(model.base_classes) > 1]):
        return
    duplicates = find_possible_reuse_duplicates(parser, module_models)

    def layout_targets(model: DataModel) -> list[DataModel]:
        targets = _find_base_classes(model)
        if (duplicated := duplicates.get(id(model))) is not None:
            targets.append(duplicated)
        return targets

    layout_models = reach(joins, layout_targets, key=id)
    tag_names = possible_tag_names(
        iter_union_sites(chain(models, chain.from_iterable(parser._collapsed_reuse_models.values()))),  # noqa: SLF001
        layout_models,
        field_namer(parser.model_resolver, parser.field_name_model_type),
    )
    overrides = parser._model_type_override_imports  # noqa: SLF001

    def is_slot(field: DataModelFieldBase) -> bool:
        names = tag_names.get(id(field.parent), frozenset())
        return not field.is_class_var and field.name not in names and field.original_name not in names

    view = BaseLayoutView(
        is_slot,
        lambda model: duplicates.get(id(model), model),
        lambda model: model_type_override(overrides, model.reference, row="class-name") is not None,
    )
    for model in post_order(joins, _find_base_classes, id):
        if len(model.base_classes) > 1:
            model.flatten_conflicting_base_layouts(view, partial(flatten_base_layouts, parser))


def flatten_base_layouts(parser: FlattenHost, model: DataModel) -> None:
    """Make a model declare the fields of its generated bases and take their options, and drop them.

    The required names the model and its bases take from their own bases are resolved first, as only the bases
    give their fields. A field becomes required as a required name makes it.
    """
    override_required_field(parser, linearize_data_models([model])[::-1])
    own_fields = {id(field) for field in model.fields}

    def adopt(field: DataModelFieldBase, required: bool) -> DataModelFieldBase:  # noqa: FBT001
        if id(field) not in own_fields:
            field = parser._copy_model_field(field)  # noqa: SLF001
        if required and not field.required:
            require_field(parser, field)
        return field

    parser._graph_builder.adopt_base_options(model)  # noqa: SLF001
    fields = model.flatten_base_layouts(adopt)
    with parser._graph_builder.defer_refresh():  # noqa: SLF001
        for base_class in model.base_classes:
            parser._graph_builder.detach_data_type_ref(base_class)  # noqa: SLF001
        parser._graph_builder.set_fields(model, fields)  # noqa: SLF001
        parser._graph_builder.reset_base_classes(model)  # noqa: SLF001


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
    options = parser.data_type_manager.constraint_options
    for _, models in run.module_models:
        for field in (field for model in models for field in model.fields):
            parser._graph_builder.drop_union_member_constraints(field, refresh="deferred", options=options)  # noqa: SLF001


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
    graph = index.reference_graph(
        snapshot,
        keyed,
        all_dict_keys=True,
        edges={
            EdgeKind.MAPPING_KEY: {
                key: keyed.keys_of(root_dict_key_references(model)) for key, model in keyed.models.items()
            }
        }
        if require_dict_key_edge
        else {},
    )

    components = graph.circular_components((EdgeKind.ANY_REFERENCE,))
    circular_roots = (
        frozenset(key for key in keyed.models if key in graph.targets(key, (EdgeKind.ANY_REFERENCE,))).union(
            *(
                component
                for component in components
                if any(graph.targets(key, (EdgeKind.MAPPING_KEY,)) & component for key in component)
            )
        )
        if require_dict_key_edge
        else frozenset(key for component in components for key in component)
    )
    recursion_facts = parser._recursion_facts  # noqa: SLF001
    parser._recursion_facts = RecursionFacts(  # noqa: SLF001
        require_update=recursion_facts["require_update"], circular_roots=circular_roots
    )


def root_dict_key_references(model: DataModel) -> list[Reference]:
    """Return the model references that mapping-key annotations of ``model`` reach."""
    return [
        reference
        for field in model.fields
        for data_type in field.data_type.all_data_types
        if (dict_key := data_type.dict_key) is not None and (reference := dict_key.reference)
    ]


def record_emitting_modules(parser: PlansHost, run: StructuredRun[Any]) -> None:
    """Record the module that writes each model, for ``--collapse-root-models-name-strategy``."""
    parser._emitting_module_lists = (  # noqa: SLF001
        [(dotted_module(module_), models) for module_, models in run.module_models]
        if parser.collapse_root_models and parser.collapse_root_models_name_strategy is not None
        else []
    )
    parser._emitting_module_names = {  # noqa: SLF001
        id(model): module_name
        for module_name, models in parser._emitting_module_lists  # noqa: SLF001
        for model in models
    }
