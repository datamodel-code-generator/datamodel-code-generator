"""The module passes (L5): what the models of one output module take while the module is processed.

Each pass is a function of the parser and the models or the state of the module, called from the pipeline tables of
``parser/base.py`` once per output module; the parser comes in through ``ModulesHost`` and the state through
``ModuleStateView``, which ``Parser`` and its module state satisfy structurally.
"""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Any, Final, NamedTuple, TypeAlias, cast

from typing_extensions import Protocol

from datamodel_code_generator import FieldTypeCollisionStrategy
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator._reference_graph import (
    EdgeKind,
    GraphSnapshot,
    ReferenceGraph,
    graph_answer,
    reference_graph,
)
from datamodel_code_generator._requirements import python_target_meets
from datamodel_code_generator._scc import strongly_connected_components
from datamodel_code_generator.enums import DefaultValueType
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model._decisions.enum_kind import decide_enum_kind
from datamodel_code_generator.model._decisions.literal_default import decide_literal_default
from datamodel_code_generator.model.base import (
    UNDEFINED,
    ConstraintsBase,
    DataModel,
    _apply_literal_default,
    _find_base_classes,
    _keyed_answer,
    _ModelKeys,
    _reference_names_model,
    get_inherited_fields,
)
from datamodel_code_generator.model.enum import Enum, IntEnum, Member, StrEnum, _enum_class
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._keys import _DERIVED_FROM_KEY
from datamodel_code_generator.parser._passes._shared import (
    ConstructorInfoHost,
    RenamedTypesHost,
    TypeOverrideHost,
    _annotated_reference,
    _copy_data_type,
    _declares_unique_items,
    _derived_models,
    _is_pydantic_v2_root_model,
    _ModelPositions,
    _register_data_type_import,
    _restore_inherited_field_names_for_unique_aliases,
    _runtime_validation_reference_keys,
    _unique_items_tuple,
    enum_site_override_import,
    get_constructor_field_adjustment,
    get_inherited_constructor_info,
    record_renamed_type,
    replace_model_in_list,
)
from datamodel_code_generator.parser._passes._state import _RenamedType
from datamodel_code_generator.reference import ModelResolver
from datamodel_code_generator.types import Types

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.parser._imports_plan import ModuleImportLedger
    from datamodel_code_generator.parser._passes._state import _ConstNarrowing
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType, DataTypeManager

__all__ = [
    "ModuleStateView",
    "ModulesHost",
    "apply_base_class_type_overrides",
    "apply_field_type_overrides",
    "change_field_name",
    "extract_module_inherited_enums",
    "mark_unused_models_start",
    "record_decimal_alias_models",
    "remove_module_overridden_models",
    "rename_fields_hiding_types",
    "replace_unique_list_to_set",
    "restore_inherited_field_names_for_unique_aliases",
    "set_default_enum_member",
    "set_one_literal_on_default",
    "set_reference_default_value_to_field",
    "snapshot_module_unused_models",
    "sort_models",
    "type_const_narrowed_values",
    "type_module_dict_keys_by_root_types",
]


class ModulesHost(ConstructorInfoHost, RenamedTypesHost, TypeOverrideHost, Protocol):
    """The options, run state and hooks of the parser that the module passes read and write."""

    @property
    def _const_narrowed_values(self) -> dict[int, tuple[DataType, _ConstNarrowing]]:
        """The values typed by an enum that a const narrows, by data type id, with that const."""

    @property
    def _constrained_decimal_alias_model_ids(self) -> set[int]:
        """The models of the modules where a field named ``Decimal`` may shadow the type."""

    @property
    def _enum_admits_other_values(self) -> list[tuple[DataModelFieldBase, _ConstNarrowing]]:
        """The values typed by an enum whose const admits only one of its values, with that const."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def _hidden_named_types(self) -> dict[tuple[ModelKey, str | None], bool]:
        """Whether a field hides the named type of its model, by model key and field name."""

    @property
    def _hiding_field_names(self) -> dict[tuple[ModelKey, str | None], str]:
        """The new names of the fields that hide a named type, by model key and field name."""

    @property
    def _import_ledger(self) -> ModuleImportLedger:
        """The ledger of the imports each module adds."""

    @property
    def _type_override_imports(self) -> dict[str, Import]:
        """The imports of the types that override models and fields, by model name or scoped field name."""

    @property
    def builtin_names(self) -> frozenset[str]:
        """The builtin names of the target Python version."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def data_type(self) -> type[DataType]:
        """The data type class of the data type manager."""

    @property
    def data_type_manager(self) -> DataTypeManager:
        """The data type manager of the output."""

    @property
    def deserialize_default_value_types(self) -> frozenset[DefaultValueType]:
        """The kinds of default values deserialized into their types."""

    @property
    def field_type_collision_strategy(self) -> FieldTypeCollisionStrategy | None:
        """How a field named like its type is told apart from it, if configured."""

    @property
    def keep_model_order(self) -> bool:
        """Whether models keep the order of the schema."""

    @property
    def pydantic_v2_root_model_type(self) -> type[DataModel] | None:
        """The root model type whose class body evaluates its root type, if the output has one."""

    @property
    def results(self) -> list[DataModel]:
        """The parsed models."""

    @property
    def set_default_enum_member(self) -> bool:
        """Whether defaults that match an enum member are written as that member."""

    @property
    def snake_case_field(self) -> bool:
        """Whether field names are written in snake case."""

    @property
    def target_pydantic_version(self) -> TargetPydanticVersion | None:
        """The Pydantic version the output targets, if configured."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    @property
    def use_annotated(self) -> bool:
        """Whether field constraints are written inside ``Annotated``."""

    @property
    def use_one_literal_as_default(self) -> bool:
        """Whether a field of one literal takes it as its default."""

    @property
    def use_tuple_for_closed_arrays(self) -> bool:
        """Whether closed arrays are written as tuples."""

    @property
    def use_unique_items_as_set(self) -> bool:
        """Whether arrays of unique items are written as sets."""

    def _apply_override_to_data_type(self, data_type: DataType) -> bool:
        """Apply the model-level type overrides to a data type and its nested types; return whether any applied."""

    def _apply_override_to_field(self, field: DataModelFieldBase, override_import: Import) -> None:
        """Replace a field's type by the type of a type override."""

    def _create_set_from_list(self, data_type: DataType) -> DataType | None:
        """Return a set copy of a list type, or the type after its nested lists became sets in place."""


class ModuleStateView(Protocol):
    """What the module passes read and write of the state of one ``_process_single_module`` call."""

    model_list: list[DataModel]
    model_list_before_override_removal: list[DataModel]
    unused_models_start: int
    current_unused_models: Sequence[DataModel]

    @property
    def imports(self) -> Imports:
        """The imports of the module."""

    @property
    def model_path_to_module_name(self) -> dict[str, str]:
        """The module name of each model, by model path."""

    @property
    def scoped_model_resolver(self) -> ModelResolver:
        """The resolver of the names the module binds."""

    @property
    def unused_models(self) -> list[DataModel]:
        """The models no module writes, collected over the modules processed so far."""


def _named_enum(source: DataModel) -> Enum | None:
    """Return the enum a value typed by ``source`` names: ``source``, or the enum the root of a nullable enum types.

    None when the root types no enum, as when its const already makes it a Literal.
    """
    if isinstance(source, Enum):
        return source
    return next(
        (
            nested_source
            for root in source.fields[:1]
            for nested in root.data_type.all_data_types
            if nested.reference is not None and isinstance(nested_source := nested.reference.source, Enum)
        ),
        None,
    )


ModelName: TypeAlias = str
ModelNames: TypeAlias = set[ModelName]
ModelDeps: TypeAlias = dict[ModelName, set[ModelName]]
OrderIndex: TypeAlias = dict[ModelName, int]
_BUILTIN_CONTAINER_COLLISION_FLAGS: dict[str, str] = {
    "list": "is_list",
    "dict": "is_dict",
    "set": "is_set",
    "frozenset": "is_frozen_set",
    "tuple": "is_tuple",
}


def _is_builtin_type_collision(current_name: str, data_type: DataType) -> bool:
    if data_type.type == current_name and not data_type.import_:
        return True

    if flag := _BUILTIN_CONTAINER_COLLISION_FLAGS.get(current_name):
        return bool(getattr(data_type, flag))

    return False


ComponentId: TypeAlias = int
Components: TypeAlias = list[list[ModelName]]
ComponentOf: TypeAlias = dict[ModelName, ComponentId]
ComponentEdges: TypeAlias = dict[ComponentId, set[ComponentId]]

ClassNode: TypeAlias = tuple[ModelName, ...]
ClassGraph: TypeAlias = dict[ClassNode, set[ClassNode]]
_KEEP_MODEL_ORDER_EDGES: Final = (EdgeKind.DEFINITION, EdgeKind.FIELD)
"""The edges keep model order keeps the models of one cycle together over."""


class _KeepModelOrderDeps(NamedTuple):
    strong: ModelDeps
    all: ModelDeps


class _KeepModelOrderComponents(NamedTuple):
    components: Components
    comp_of: ComponentOf


class _KeepModelOrderGraph(NamedTuple):
    graph: ReferenceGraph
    key_of_name: Mapping[ModelName, ModelKey]
    name_of_key: Mapping[ModelKey, ModelName]


def _collect_keep_model_order_deps(  # noqa: PLR0913
    model: DataModel,
    *,
    keyed: KeyedModels,
    model_names: ModelNames,
    imported: ModelNames,
    pydantic_v2_root_model_type: type[DataModel] | None,
    use_deferred_annotations: bool,
) -> tuple[set[ModelName], set[ModelName]]:
    """Collect (strong_deps, all_deps) used by keep_model_order sorting, by the class names of the models referenced.

    - strong_deps: base class references (within-module, non-imported)
    - all_deps: base class refs + (optionally) field refs (within-module, non-imported)

    Row ``legacy-keep-order-short-name``: a reference names a model of the module by its short name, so it also adds a
    false dependency on a same-named model of another module imported as a module. Under the ``MODEL_GRAPH`` check,
    the dependencies by model key (`KeyedModels.key_of`) that differ are a violation.
    """
    class_name = model.class_name
    base_class_references = [b.reference for b in model.base_classes if b.reference]
    field_references = [t.reference for f in model.fields for t in f.data_type.all_data_types if t.reference]

    if (
        use_deferred_annotations
        and not isinstance(model, TypeAliasBase)
        and not _is_pydantic_v2_root_model(model, pydantic_v2_root_model_type)
    ):
        field_references = []

    def module_names(references: Iterable[Reference]) -> set[ModelName]:
        return {
            name
            for reference in references
            if (key := keyed.key_of(reference)) in keyed.models
            and (name := keyed.models[key].class_name) not in imported
            and name != class_name
        }

    def names_by_short_name(references: Iterable[Reference]) -> set[ModelName]:
        return {
            r
            for r in {reference.short_name for reference in references}
            if r in model_names and r not in imported and r != class_name
        }

    def by_key() -> tuple[set[ModelName], set[ModelName]]:
        strong = module_names(base_class_references)
        return strong, strong | module_names(field_references)

    return _keyed_answer(
        "keep model order",
        (
            names_by_short_name(base_class_references),
            names_by_short_name([*base_class_references, *field_references]),
        ),
        by_key,
    )


def _build_keep_model_order_dependency_maps(
    models: list[DataModel],
    *,
    model_names: ModelNames,
    imported: ModelNames,
    pydantic_v2_root_model_type: type[DataModel] | None,
    use_deferred_annotations: bool,
) -> _KeepModelOrderDeps:
    strong_deps: ModelDeps = {}
    all_deps: ModelDeps = {}
    runtime_validation_references = _runtime_validation_reference_keys(models, same_module=False)
    class_names = (
        {model_key_of(model.reference): model.class_name for model in models} if runtime_validation_references else {}
    )
    keyed = keyed_models(models, "keep model order")
    for model in models:
        strong, deps = _collect_keep_model_order_deps(
            model,
            keyed=keyed,
            model_names=model_names,
            imported=imported,
            pydantic_v2_root_model_type=pydantic_v2_root_model_type,
            use_deferred_annotations=use_deferred_annotations,
        )
        if reference_keys := runtime_validation_references.get(model_key_of(model.reference)):
            runtime_validation_deps = {class_names[reference_key] for reference_key in reference_keys} - imported
            strong |= runtime_validation_deps
            deps |= runtime_validation_deps
        strong_deps[model.class_name] = strong
        all_deps[model.class_name] = deps
    return _KeepModelOrderDeps(strong=strong_deps, all=all_deps)


def _keep_model_order_graph(
    deps: _KeepModelOrderDeps, model_by_name: Mapping[ModelName, DataModel]
) -> _KeepModelOrderGraph:
    """Take the snapshot of the dependencies that keep model order moves the models of one module for."""
    key_of_name = {name: model_key_of(model.reference) for name, model in model_by_name.items()}
    graph = reference_graph(
        GraphSnapshot.KEEP_MODEL_ORDER,
        key_of_name.values(),
        {
            EdgeKind.DEFINITION: {
                key_of_name[name]: map(key_of_name.__getitem__, deps.strong[name]) for name in deps.strong
            },
            EdgeKind.FIELD: {
                key_of_name[name]: map(key_of_name.__getitem__, all_deps - deps.strong[name])
                for name, all_deps in deps.all.items()
            },
        },
    )
    return _KeepModelOrderGraph(graph, key_of_name, {key: name for name, key in key_of_name.items()})


def _build_keep_model_order_components(
    all_deps: ModelDeps,
    order_index: OrderIndex,
    keyed: _KeepModelOrderGraph,
) -> _KeepModelOrderComponents:
    def ordered(sccs: Iterable[Iterable[ModelName]]) -> Components:
        components = [sorted(scc, key=order_index.__getitem__) for scc in sccs]
        components.sort(key=lambda members: min(order_index[n] for n in members))
        return components

    def old() -> Components:
        class_graph: ClassGraph = {(name,): {(dep,) for dep in deps} for name, deps in all_deps.items()}
        return ordered((node[0] for node in scc) for scc in strongly_connected_components(class_graph))

    components = graph_answer(
        GraphSnapshot.KEEP_MODEL_ORDER,
        ordered(map(keyed.name_of_key.__getitem__, scc) for scc in keyed.graph.components(_KEEP_MODEL_ORDER_EDGES)),
        old,
    )
    comp_of: ComponentOf = {name: i for i, members in enumerate(components) for name in members}
    return _KeepModelOrderComponents(components=components, comp_of=comp_of)


def _build_keep_model_order_component_edges(
    all_deps: ModelDeps,
    comp_of: ComponentOf,
    num_components: int,
) -> ComponentEdges:
    comp_edges: ComponentEdges = {i: set() for i in range(num_components)}
    for name, deps in all_deps.items():
        name_comp = comp_of[name]
        for dep in deps:
            if (dep_comp := comp_of[dep]) != name_comp:
                comp_edges[dep_comp].add(name_comp)
    return comp_edges


def _build_keep_model_order_component_order(
    components: Components,
    comp_edges: ComponentEdges,
    order_index: OrderIndex,
) -> list[ComponentId]:
    comp_key = [min(order_index[n] for n in members) for members in components]
    return stable_toposort(
        list(range(len(components))),
        comp_edges,
        key=lambda component_id: comp_key[component_id],
    )


def _build_keep_model_ordered_names(
    ordered_comp_ids: list[ComponentId],
    components: Components,
    strong_deps: ModelDeps,
    order_index: OrderIndex,
    keyed: _KeepModelOrderGraph,
) -> list[ModelName]:
    name_of_key = keyed.name_of_key
    ordered_names: list[ModelName] = []
    for component_id in ordered_comp_ids:
        members = components[component_id]
        if len(members) > 1:
            in_order = members

            def old(members: list[ModelName] = in_order) -> list[ModelName]:
                strong_edges: dict[ModelName, set[ModelName]] = {n: set() for n in members}
                member_set = set(members)
                for base in members:
                    derived = {member for member in members if base in strong_deps.get(member, set()) & member_set}
                    strong_edges[base].update(derived)
                return stable_toposort(members, strong_edges, key=order_index.__getitem__)

            members = graph_answer(
                GraphSnapshot.KEEP_MODEL_ORDER,
                [
                    name_of_key[key]
                    for key in keyed.graph.dependency_order(
                        (EdgeKind.DEFINITION,),
                        [keyed.key_of_name[name] for name in in_order],
                        lambda key: order_index[name_of_key[key]],
                    )
                ],
                old,
            )
        ordered_names.extend(members)
    return ordered_names


def _reorder_models_keep_model_order(
    models: list[DataModel],
    imports: Imports,
    *,
    pydantic_v2_root_model_type: type[DataModel] | None,
    use_deferred_annotations: bool,
) -> None:
    """Reorder models deterministically based on their dependencies.

    Starts from class_name order and only moves models when required to satisfy dependencies.
    Cycles are kept as SCC groups; within each SCC, base-class dependencies are prioritized.
    """
    models.sort(key=lambda x: x.class_name)
    imported: ModelNames = {i for v in imports.values() for i in v}
    model_by_name = {m.class_name: m for m in models}
    model_names: ModelNames = set(model_by_name)
    order_index: OrderIndex = {m.class_name: i for i, m in enumerate(models)}

    deps = _build_keep_model_order_dependency_maps(
        models,
        model_names=model_names,
        imported=imported,
        pydantic_v2_root_model_type=pydantic_v2_root_model_type,
        use_deferred_annotations=use_deferred_annotations,
    )
    keyed = _keep_model_order_graph(deps, model_by_name)
    comps = _build_keep_model_order_components(deps.all, order_index, keyed)
    comp_edges = _build_keep_model_order_component_edges(deps.all, comps.comp_of, len(comps.components))
    ordered_comp_ids = _build_keep_model_order_component_order(comps.components, comp_edges, order_index)
    ordered_names = _build_keep_model_ordered_names(ordered_comp_ids, comps.components, deps.strong, order_index, keyed)
    models[:] = [model_by_name[name] for name in ordered_names]


def _replace_default_enum_list_members(
    default: list[Any],
    enum_source: Enum,
    alias: str | None,
    enum_members: list[Any] | None,
) -> list[Any] | None:
    """Replace matching list values without changing the original default list."""
    values = default if enum_members is None else enum_members
    for index, value in enumerate(values):
        if isinstance(value, Member):
            continue
        if (enum_member := enum_source.find_member(value)) is None:
            continue
        if enum_members is None:
            enum_members = default.copy()
        enum_members[index] = enum_member
        if alias:
            enum_member.alias = alias
    return enum_members


def _value_positions(field: DataModelFieldBase) -> list[tuple[DataModelFieldBase | DataType, DataType]]:
    """Return each data type of ``field`` with the field or data type that holds it, the field's type first."""
    return [
        (field, field.data_type),
        *(
            (holder, nested)
            for holder in field.data_type.all_data_types
            for nested in (*holder.data_types, *((holder.dict_key,) if holder.dict_key is not None else ()))
        ),
    ]


def _named_reference(field: DataModelFieldBase, name: str | None) -> Reference | None:
    """Return the referenced type that the field's annotation names ``name``."""
    for data_type in field.data_type.all_data_types:
        if (reference := _annotated_reference(data_type)) is not None and (
            data_type.alias or reference.short_name
        ) == name:
            return reference
    return None


def _same_named_reference(model: DataModel, name: str | None, *, skips_aliases: bool) -> Reference | None:
    """Return the type named ``name`` that an annotation of the model refers to, by that name with ``skips_aliases``."""
    return next(
        (
            reference
            for field in model.fields
            for data_type in field.data_type.all_data_types
            if (reference := _annotated_reference(data_type)) is not None
            and reference.short_name == name
            and not (skips_aliases and data_type.alias and data_type.alias != name)
        ),
        None,
    )


def _model_family(model: DataModel, derived_models: Mapping[ModelKey, list[DataModel]]) -> dict[ModelKey, DataModel]:
    """Return the models that the model inherits from or passes its fields on to, transitively, by model key."""
    keys = _ModelKeys("model family")
    family = {keys.of(model): model}
    pending = [model]
    while pending:
        current = pending.pop()
        for kin in chain(_find_base_classes(current), derived_models.get(model_key_of(current.reference), ())):
            if (kin_key := keys.of(kin)) not in family:
                family[kin_key] = kin
                pending.append(kin)
    return family


def _family_field_names(family: Mapping[ModelKey, DataModel]) -> set[str]:
    """Return the Python names and aliases of the fields of the family's models."""
    return {
        name
        for member in family.values()
        for field in member.fields
        for name in (field.name, field.alias, *(field.validation_aliases or ()))
        if name is not None
    }


def _rename_field_preserving_alias(field: DataModelFieldBase, new_name: str) -> None:
    """Rename a Python field without losing an existing source-name alias."""
    if field.alias is None:
        field.alias = field.name
    field.name = new_name


def record_decimal_alias_models(parser: ModulesHost, state: ModuleStateView) -> None:
    """Remember the models of a module where a field named ``Decimal`` may shadow the type."""
    parser._constrained_decimal_alias_model_ids.update(map(id, state.model_list))  # noqa: SLF001


def replace_unique_list_to_set(parser: ModulesHost, models: list[DataModel]) -> None:
    for model in models:
        for model_field in model.fields:
            if (
                not parser.use_unique_items_as_set
                or not _declares_unique_items(model_field)
                or (parser.use_tuple_for_closed_arrays and _unique_items_tuple(model_field.data_type) is not None)
            ):
                continue
            set_data_type = parser._create_set_from_list(model_field.data_type)  # noqa: SLF001
            if set_data_type:  # pragma: no cover
                # Check if default list elements are hashable before converting type
                if isinstance(model_field.default, list):
                    try:
                        converted_default = set(model_field.default)
                    except TypeError:
                        # Keep the unhashable default unchanged. Nested list types may
                        # already have been converted in place, so invalidate their imports.
                        parser._graph_builder.note_changed_models(model)  # noqa: SLF001
                        continue
                    parser._graph_builder.set_field_default(model_field, converted_default)  # noqa: SLF001
                parser._graph_builder.replace_field_type(model_field, set_data_type)  # noqa: SLF001


def extract_inherited_enum(models: list[DataModel]) -> None:
    for model in models.copy():
        if model.fields:
            continue
        enums: list[Enum] = []
        for base_model in model.base_classes:
            if not base_model.reference:
                continue
            source_model = base_model.reference.source
            if isinstance(source_model, Enum):
                enums.append(source_model)
        if enums:
            kind = decide_enum_kind("merged", first_specialized=isinstance(enums[0], (IntEnum, StrEnum)))
            merged_enum = _enum_class(kind, enums[0].__class__)(
                fields=[f for e in enums for f in e.fields],
                description=model.description,
                reference=model.reference,
            )
            replace_model_in_list(models, model, merged_enum)


def extract_module_inherited_enums(parser: ModulesHost, state: ModuleStateView) -> None:
    """Merge the enums that empty subclasses inherit, each merged enum taking the store place of its subclass."""
    models = state.model_list.copy()
    extract_inherited_enum(state.model_list)
    for model, merged_enum in zip(models, state.model_list, strict=True):
        if merged_enum is not model:
            parser._graph_builder.replace_model(model, merged_enum)  # noqa: SLF001


def mark_unused_models_start(state: ModuleStateView) -> None:
    """Remember where the unused models this module adds will start."""
    state.unused_models_start = len(state.unused_models)


def set_reference_default_value_to_field(
    parser: ModulesHost,
    models: list[DataModel],
) -> None:
    for model in models:
        for model_field in model.fields:
            if not model_field.data_type.reference or model_field.has_default:
                continue
            if (
                isinstance(model_field.data_type.reference.source, DataModel)
                and model_field.data_type.reference.source.default != UNDEFINED
            ):
                # pragma: no cover
                parser._graph_builder.set_field_default(model_field, model_field.data_type.reference.source.default)  # noqa: SLF001


def set_default_enum_member(
    parser: ModulesHost,
    models: list[DataModel],
) -> None:
    """Convert matching defaults to enum members while preserving unmatched list values."""
    if not parser.set_default_enum_member and DefaultValueType.Enum not in parser.deserialize_default_value_types:
        return
    for model in models:
        for model_field in model.fields:
            if model_field.default is None:
                continue
            default = model_field.default
            enum_members: list[Any] | None = None
            for data_type in model_field.data_type.all_data_types:
                if (reference := data_type.reference) is None:
                    continue
                if not isinstance(enum_source := reference.source, Enum):
                    continue
                match default:
                    case list():
                        enum_members = _replace_default_enum_list_members(
                            default,
                            enum_source,
                            data_type.alias,
                            enum_members,
                        )
                    case _:
                        if (enum_member := enum_source.find_member(default)) is None:
                            continue
                        default = enum_member
                        if data_type.alias:
                            enum_member.alias = data_type.alias
                        break
            default = enum_members or default
            if default is model_field.default:
                continue
            parser._graph_builder.set_field_default(model_field, default)  # noqa: SLF001


def sort_models(
    parser: ModulesHost,
    models: list[DataModel],
    imports: Imports,
    *,
    use_deferred_annotations: bool,
) -> None:
    if not parser.keep_model_order:
        return

    _reorder_models_keep_model_order(
        models,
        imports,
        pydantic_v2_root_model_type=parser.pydantic_v2_root_model_type,
        use_deferred_annotations=use_deferred_annotations,
    )


def change_field_name(  # noqa: PLR0912
    parser: ModulesHost,
    models: list[DataModel],
) -> None:
    if not (parser.data_model_type.SUPPORTS_FIELD_RENAMING and parser.data_model_type.NORMALIZES_FIELD_NAMES):
        return

    rename_type = (
        parser.field_type_collision_strategy == FieldTypeCollisionStrategy.RenameType
        and parser.data_model_type.SUPPORTS_FIELD_TYPE_RENAMING
    )
    all_class_names = {cast("str", model.class_name) for model in models if model.class_name} if rename_type else set()
    resolver: ModelResolver | None = None

    for model in models:
        if "Enum" in model.base_class or (not model.BASE_CLASS and not isinstance(model, parser.data_model_type)):
            continue

        for field in (field for field in model.fields if field.name != parser.data_model_type.TYPED_EXTRA_FIELD_NAME):
            field_name = field.name
            reference_type_names: set[str] = set()
            colliding_reference: Reference | None = None

            for data_type in field.data_type.all_data_types:
                if (reference := data_type.reference) is None:
                    continue
                if rename_type and (alias := data_type.alias) and alias != field_name:
                    continue
                reference_name = reference.short_name
                reference_type_names.add(reference_name)
                if rename_type and colliding_reference is None and reference_name == field_name:
                    colliding_reference = reference

            if colliding_reference is not None or (
                not rename_type
                and (parser.data_model_type.NORMALIZES_FIELD_NAMES or field_name in reference_type_names)
            ):
                resolver = resolver or ModelResolver(
                    snake_case_field=parser.snake_case_field,
                    remove_suffix_number=True,
                    target_python_version=parser.target_python_version,
                )
                if colliding_reference is not None:
                    resolver._reset_for_reuse(all_class_names.copy())  # noqa: SLF001
                    source = cast("DataModel", colliding_reference.source)
                    resolver.exclude_names.add(cast("str", field_name))
                    new_class_name = resolver.add(["type"], cast("str", source.class_name)).name  # ty: ignore[redundant-cast]
                    record_renamed_type(parser, source, _RenamedType(source.class_name, colliding_reference.path))
                    parser._graph_builder.rename_model(source, class_name=new_class_name)  # noqa: SLF001
                    all_class_names.add(new_class_name)
                else:
                    resolver._reset_for_reuse(reference_type_names)  # noqa: SLF001
                    new_field_name = resolver._get_unique_field_name(cast("str", field_name))  # noqa: SLF001
                    if field_name != new_field_name:
                        _rename_field_preserving_alias(field, new_field_name)

            if (
                parser.data_model_type.NORMALIZES_FIELD_NAMES
                and (current_name := field.name) in parser.builtin_names
                and any(_is_builtin_type_collision(current_name, dt) for dt in field.data_type.all_data_types)
            ):
                if field.alias is None:
                    field.alias = field_name
                field.name = f"{current_name}_"


def restore_inherited_field_names_for_unique_aliases(models: list[DataModel]) -> None:
    """Reserve inherited Python names for output models with unique wire aliases."""
    for model in models:
        if model.REQUIRES_UNIQUE_FIELD_ALIASES:
            _restore_inherited_field_names_for_unique_aliases(
                model.fields, get_inherited_fields(_find_base_classes(model))
            )


def const_narrowed_data_type(
    parser: ModulesHost, data_type: DataType, narrowing: _ConstNarrowing, enum: Enum
) -> DataType | None:
    """Return the type of a value narrowed to a const: None, ``Literal[<Enum>.<member>]``, or None to keep it."""
    if narrowing.const is None:
        return parser.data_type_manager.get_data_type(Types.null)
    if not narrowing.as_member or (member := enum.find_member(narrowing.const)) is None:
        return None
    new_data_type = parser.data_type(
        enum_member_literals=[(enum.reference.short_name, cast("str", member.field.name))],
        is_optional=data_type.is_optional,
    )
    new_data_type._set_enum_member_literal_reference(enum.reference)  # noqa: SLF001
    return new_data_type


def type_const_narrowed_values(
    parser: ModulesHost,
    models: list[DataModel],
    imports: Imports,
) -> None:
    """Type each value whose model is built from an enum that a const narrows by that const.

    Runs once the referenced enum is final: after reuse and collapse redirect the value and after member and
    discriminator names are settled. A value of a named enum whose root types no enum, as when its const already
    makes it a Literal or None, keeps its type. The values that keep the enum type are warned about once msgspec
    tags are final. A value that collapse shares between the root model and its holders is replaced in each holder.
    """
    if not parser._const_narrowed_values:  # noqa: SLF001
        return
    narrowed: dict[int, DataType] = {}
    for model in models:
        for field in model.fields:
            for owner, data_type in _value_positions(field):
                entry = parser._const_narrowed_values.get(id(data_type))  # noqa: SLF001
                if entry is None or entry[0] is not data_type or data_type.reference is None:
                    continue
                narrowing = entry[1]
                if (enum := _named_enum(cast("DataModel", data_type.reference.source))) is None:
                    continue
                if (new_data_type := const_narrowed_data_type(parser, data_type, narrowing, enum)) is None:
                    parser._enum_admits_other_values.append((field, narrowing))  # noqa: SLF001
                    continue
                narrowed[id(data_type)] = data_type
                if owner is field:
                    parser._graph_builder.replace_field_type(field, new_data_type)  # noqa: SLF001
                elif (parent := cast("DataType", owner)).dict_key is data_type:
                    parser._graph_builder.replace_nested_data_type(parent, data_type, new_data_type)  # noqa: SLF001
                else:
                    parser._graph_builder.set_nested_data_types(  # noqa: SLF001
                        parent, [new_data_type if nested is data_type else nested for nested in parent.data_types]
                    )
                parser._import_ledger.append(  # noqa: SLF001
                    imports, new_data_type.imports, ImportProducer("const-narrowed-values", model, field)
                )
    for data_type in narrowed.values():
        parser._graph_builder.detach_data_type_ref(data_type)  # noqa: SLF001


def set_one_literal_on_default(parser: ModulesHost, models: list[DataModel]) -> None:
    """Write the default, required and nullability one literal gives its field (``decide_literal_default``)."""
    if not parser.use_one_literal_as_default:
        return
    for model in models:
        for model_field in model.fields:
            if len(literals := model_field.data_type.literals) != 1:
                continue
            decision = decide_literal_default(
                "one-literal",
                literals[0],
                required=model_field.required,
                nullable=model_field.nullable,
                type_has_null=model_field.type_has_null,
            )
            _apply_literal_default(model_field, decision)


def same_named_fields(parser: ModulesHost, model: DataModel) -> list[DataModelFieldBase]:
    """Return the fields of the model named like a type that an annotation of the model refers to."""
    model_type = parser.data_model_type
    if "Enum" in model.base_class or (not model.BASE_CLASS and not isinstance(model, model_type)):
        return []
    type_names = {
        reference.short_name
        for field in model.fields
        for data_type in field.data_type.all_data_types
        if (reference := _annotated_reference(data_type)) is not None
    }
    return [
        field for field in model.fields if field.name != model_type.TYPED_EXTRA_FIELD_NAME and field.name in type_names
    ]


def hides_named_type(
    parser: ModulesHost,
    model: DataModel,
    field: DataModelFieldBase,
    positions: _ModelPositions,
    *,
    use_deferred_annotations: bool,
) -> bool:
    """Return whether the finished class hides a type named like the field from an annotation of the class.

    The class body binds the field's name when the template writes an assignment, which constructor
    ordering and the assignment form of a forward reference add after this step. The annotation is resolved
    from a string under the future import and as a quoted forward reference, and native deferred annotations
    resolve a forward reference once the class exists as well. The annotation of another field sees the binding
    in any order, as Python 3.14 evaluates every annotation once the class body has run.
    """
    users = [
        (user, reference) for user in model.fields if (reference := _named_reference(user, field.name)) is not None
    ]
    if not users:
        return False
    index = positions.get(model.reference)
    writes_strings = use_deferred_annotations and not python_target_meets(
        "python.deferred_annotations", parser.target_python_version.version_key
    )
    assigned = (
        model.field_binds_class_attribute(field)
        or (
            (inherited := get_inherited_constructor_info(parser, model)) is not None
            and get_constructor_field_adjustment(
                model,
                field,
                inherited,
                supports_inherited_override=model.SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT,
            )
            is not None
        )
        or (
            parser.use_annotated
            and use_deferred_annotations
            and index is not None
            and bool(field.field)
            and model.assigns_forward_reference_field(field, positions.paths, index)
        )
    )
    for user, reference in users:
        is_forward = index is not None and positions.rank(reference) >= index
        if model.class_attribute_hides_named_type(
            user,
            assigned=assigned,
            resolved_late=writes_strings or is_forward,
            as_string=writes_strings or (is_forward and not use_deferred_annotations),
        ):
            return True
    return False


def rename_named_type(  # noqa: PLR0913, PLR0917
    parser: ModulesHost,
    field: DataModelFieldBase,
    reference: Reference,
    class_names: set[str],
    all_models: Collection[DataModel],
    derived_models: Mapping[ModelKey, list[DataModel]],
) -> None:
    """Rename the type that the field hides to a name that no field of a family using the type has."""
    source = cast("DataModel", reference.source)
    resolver = ModelResolver(
        snake_case_field=parser.snake_case_field,
        remove_suffix_number=True,
        target_python_version=parser.target_python_version,
    )
    resolver._reset_for_reuse(class_names.copy())  # noqa: SLF001
    resolver.exclude_names.add(cast("str", field.name))
    resolver.exclude_names.update(
        name
        for user in all_models
        if any(
            (user_reference := _annotated_reference(data_type)) is not None
            and _reference_names_model(user_reference, source, "users of a renamed type")
            for data_type in user.all_data_types
        )
        for name in _family_field_names(_model_family(user, derived_models))
    )
    record_renamed_type(parser, source, _RenamedType(source.class_name, reference.path))
    parser._graph_builder.rename_model(source, class_name=resolver.add(["type"], source.class_name).name)  # noqa: SLF001
    class_names.add(source.class_name)


def family_hides_named_type(  # noqa: PLR0913
    parser: ModulesHost,
    model: DataModel,
    field_name: str | None,
    derived_models: Mapping[ModelKey, list[DataModel]],
    positions: _ModelPositions,
    *,
    use_deferred_annotations: bool,
) -> bool:
    """Return whether a class of the model's inheritance family hides the type named like its field.

    The classes of one family write the field under one name, so one decision covers all of them.
    """
    if (hidden := parser._hidden_named_types.get((model_key_of(model.reference), field_name))) is not None:  # noqa: SLF001
        return hidden
    family = _model_family(model, derived_models)
    hidden = any(
        field.name == field_name
        and hides_named_type(parser, member, field, positions, use_deferred_annotations=use_deferred_annotations)
        for member in family.values()
        for field in same_named_fields(parser, member)
    )
    parser._hidden_named_types.update(dict.fromkeys(((key, field_name) for key in family), hidden))  # noqa: SLF001
    return hidden


def rename_fields_hiding_types(parser: ModulesHost, models: list[DataModel], *, use_deferred_annotations: bool) -> None:
    """Rename a field named like a type of its class's annotations where the finished class hides that type.

    A model that normalizes field names has renamed every field named like its own type already. The classes of
    one family write the field under one new name, which no field of the family has as its name or alias.
    """
    model_type = parser.data_model_type
    if not model_type.SUPPORTS_FIELD_RENAMING:
        return
    declarations = [(model, fields) for model in models if (fields := same_named_fields(parser, model))]
    if not declarations:
        return
    positions = _ModelPositions(models, "hidden named types")
    all_models = {id(model): model for model in chain(parser.results, models)}.values()
    derived_models = _derived_models(all_models)
    rename_type = (
        parser.field_type_collision_strategy == FieldTypeCollisionStrategy.RenameType
        and model_type.SUPPORTS_FIELD_TYPE_RENAMING
    )
    class_names = {model.class_name for model in models} if rename_type else set()
    for model, fields in declarations:
        for field in fields:
            field_name = field.name
            if (
                not family_hides_named_type(
                    parser,
                    model,
                    field_name,
                    derived_models,
                    positions,
                    use_deferred_annotations=use_deferred_annotations,
                )
                or (reference := _same_named_reference(model, field_name, skips_aliases=rename_type)) is None
            ):
                continue
            if rename_type:
                rename_named_type(parser, field, reference, class_names, all_models, derived_models)
                continue
            if (new_name := parser._hiding_field_names.get((model_key_of(model.reference), field_name))) is None:  # noqa: SLF001
                family = _model_family(model, derived_models)
                resolver = ModelResolver(
                    exclude_names=_family_field_names(family),
                    snake_case_field=parser.snake_case_field,
                    remove_suffix_number=True,
                    target_python_version=parser.target_python_version,
                )
                new_name = resolver._get_unique_field_name(cast("str", field_name))  # noqa: SLF001
                parser._hiding_field_names.update(dict.fromkeys(((key, field_name) for key in family), new_name))  # noqa: SLF001
            _rename_field_preserving_alias(field, new_name)


def remove_overridden_models(parser: ModulesHost, models: list[DataModel]) -> list[DataModel]:
    """Remove models that are being overridden by custom types (model-level only).

    Only model-level overrides (keys without dots) cause model removal.
    Scoped overrides (ClassName.field) only affect specific fields.
    """
    if not (overrides := parser._model_type_override_imports):  # noqa: SLF001
        return models
    kept: list[DataModel] = []
    for model in models:
        if model.class_name in overrides or enum_site_override_import(parser, model.reference) is not None:
            parser._graph_builder.retire_model(model)  # noqa: SLF001
        else:
            kept.append(model)
    return kept


def remove_module_overridden_models(parser: ModulesHost, state: ModuleStateView) -> None:
    """Keep the models a model-level type override replaces out of the module, and the list before it."""
    state.model_list_before_override_removal = state.model_list
    state.model_list = remove_overridden_models(parser, state.model_list)


def derived_override_import(parser: ModulesHost, model: DataModel) -> Import | None:
    """Return the model-level override of the target a site root model is built from, which applies to it too.

    A site model built from one ``$ref`` target and the site's other parts (``_DERIVED_FROM_KEY``) holds the
    target's values, so its root type takes the type that replaces the target, with the constraints that type
    takes.
    """
    if (
        not (model.IS_ROOT_MODEL or model.IS_ALIAS)
        or (target := model.reference.__dict__.get(_DERIVED_FROM_KEY)) is None
    ):
        return None
    return parser._model_type_override_imports.get(target.name)  # noqa: SLF001


def apply_field_type_overrides(parser: ModulesHost, models: list[DataModel]) -> None:
    """Replace field type references with custom import types, before constructor ordering reads the field types.

    Supports two key formats:
    - Model-level: {"CustomType": "my_app.Type"} - applies to all references
    - Scoped: {"User.field": "my_app.Type"} - applies to specific field only

    Scoped overrides take priority over model-level overrides.
    """
    if not parser._type_override_imports:  # noqa: SLF001
        return
    for model in models:
        derived_import = derived_override_import(parser, model)
        for field in model.fields:
            # Check scoped override first: "ClassName.field_name"
            scoped_key = f"{model.class_name}.{field.name}"
            match parser._type_override_imports.get(scoped_key) or derived_import:  # noqa: SLF001
                case Import() as override_import:
                    parser._apply_override_to_field(field, override_import)  # noqa: SLF001
                case None if parser._model_type_override_imports:  # noqa: SLF001
                    # Apply model-level overrides to nested types
                    parser._apply_override_to_data_type(field.data_type)  # noqa: SLF001
                case None:
                    pass


def root_key_data_type(parser: ModulesHost, root_model: DataModel) -> DataType | None:
    """Return a root model's root type as a dict key writes it, or None when it cannot be a key.

    The model-level type overrides apply to the copy, as the module of a root model processed later applies them to
    the root type itself. A container cannot be a key. Constraints that the root keeps as ``Field()`` arguments are
    written into the string type as a key schema writes them; a root of another type cannot keep them.
    """
    root_field = root_model.fields[0]
    root_type = root_field.data_type
    keywords = (
        root_field.constraints.model_dump(by_alias=True, exclude_none=True, exclude={"unique_items"})
        if isinstance(root_field.constraints, ConstraintsBase)
        else {}
    )
    string_type = parser.data_type_manager.get_data_type(Types.string)
    if any((
        root_type.is_dict,
        root_type.is_mapping,
        root_type.is_list,
        root_type.is_sequence,
        root_type.is_set,
    )) or (keywords and (root_type.type, root_type.import_) != (string_type.type, string_type.import_)):
        return None
    if keywords:
        return parser.data_type_manager.get_data_type(
            Types.string,
            use_annotated=parser.use_annotated,
            target_pydantic_version=parser.target_pydantic_version,
            **keywords,
        )
    key_type = _copy_data_type(root_type)
    if parser._model_type_override_imports:  # noqa: SLF001
        parser._apply_override_to_data_type(key_type)  # noqa: SLF001
    return key_type


def type_dict_keys_by_root_types(
    parser: ModulesHost,
    models: list[DataModel],
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: dict[str, str],
) -> None:
    """Type each dict key that names a root model whose instances cannot be dict keys by that model's root type.

    Runs once the root types are final: after collapse, const narrowing and type overrides. Such a root model
    named inside the root type a key takes is typed the same way, and a cycle of them stops at the first repeated
    model. A root type that cannot be a dict key either keeps the root model, as does a member of a key that is no
    reference: another member may take every key. Each changed model refreshes its backend config, as a pattern
    that a key takes may need another regex engine.
    """
    for model in models:
        changed = False
        for field in model.fields:
            pending = [
                (key, holder, frozenset[ModelKey]())
                for holder in field.data_type.all_data_types
                if (key := holder.dict_key) is not None and key.reference is not None
            ]
            while pending:
                data_type, parent, expanded = pending.pop()
                if (reference := data_type.reference) is None:
                    pending.extend((nested, data_type, expanded) for nested in data_type.data_types)
                    continue
                if (
                    not isinstance(root_model := reference.source, DataModel)
                    or not root_model._keys_take_root_type  # ruff: ignore[private-member-access]
                    or model_key_of(root_model.reference) in expanded
                    or (root_type := root_key_data_type(parser, root_model)) is None
                ):
                    continue
                parser._graph_builder.detach_data_type_ref(data_type)  # noqa: SLF001
                if parent.dict_key is data_type:
                    parser._graph_builder.replace_nested_data_type(parent, data_type, root_type)  # noqa: SLF001
                else:
                    parser._graph_builder.set_nested_data_types(  # noqa: SLF001
                        parent, [root_type if nested is data_type else nested for nested in parent.data_types]
                    )
                producer = ImportProducer("type-dict-keys-by-root-types", model, field)
                for nested in root_type.all_data_types:
                    if nested.reference is not None:
                        _register_data_type_import(
                            nested,
                            model,
                            imports,
                            scoped_model_resolver,
                            model_path_to_module_name,
                            ledger=parser._import_ledger,  # noqa: SLF001
                            producer=producer,
                        )
                parser._import_ledger.append(imports, root_type.imports, producer)  # noqa: SLF001
                pending.append((root_type, parent, expanded | {model_key_of(root_model.reference)}))
                changed = True
        if changed:
            parser._graph_builder.note_changed_models(model)  # noqa: SLF001
            model.refresh_field_config()


def type_module_dict_keys_by_root_types(parser: ModulesHost, state: ModuleStateView) -> None:
    """Type the dict keys of the module that name root models by their root types."""
    type_dict_keys_by_root_types(
        parser,
        state.model_list,
        state.imports,
        state.scoped_model_resolver,
        state.model_path_to_module_name,
    )


def snapshot_module_unused_models(state: ModuleStateView) -> None:
    """Take the unused models the module added, as they are now."""
    if state.unused_models_start != len(state.unused_models):
        state.current_unused_models = state.unused_models[state.unused_models_start :]


def apply_base_class_type_overrides(parser: ModulesHost, models: list[DataModel]) -> None:
    """Replace base classes that model-level overrides name with custom import types, before constructor ordering.

    Each replaced base keeps the generated model it referenced as its stand-in: constructor ordering, after the
    decision phase, reads it as the only description of the class that replaces it.
    """
    if not parser._model_type_override_imports:  # noqa: SLF001
        return
    for model in models:
        for base_class in model.base_classes:
            reference = base_class.reference
            if parser._apply_override_to_data_type(base_class):  # noqa: SLF001
                parser._base_stand_ins[id(base_class)] = reference  # noqa: SLF001
                for import_ in base_class.all_imports:
                    parser._graph_builder.add_model_import(model, import_)  # noqa: SLF001
