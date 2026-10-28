"""The render preparation passes (L6, ``RENDER_PREPARATION``): what the models take once every decision is written.

Each pass is a function of the parser and the state of one output module (``_PreparedModule``, as
``_process_single_module`` left it) or of every module (``_FinalizeState``, of ``_finalize_modules``), called from the
pipeline tables of ``parser/_passes/_tables.py``; the parser comes in through ``RenderPrepHost``, which ``Parser``
satisfies structurally. Constructor ordering also runs early, in the module body, under the form of
``--order-constructor-fields-by final-names`` off.
"""

from __future__ import annotations

import dataclasses
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, cast

from typing_extensions import Protocol

from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, reference_graph
from datamodel_code_generator._requirements import pydantic_target_meets, python_target_meets
from datamodel_code_generator._scc import find_circular_sccs
from datamodel_code_generator.model._render_plan import RENDER_READY
from datamodel_code_generator.model.base import (
    UNDEFINED,
    DataModel,
    _check_render_plan_accounting,
    _find_base_classes,
    linearize_data_models,
    ordering_constructor_fields,
)
from datamodel_code_generator.model.enum import Enum, Member
from datamodel_code_generator.model.type_alias import TypeAliasBase, TypeStatement
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._shared import (
    ConstructorInfoHost,
    RecursionFactsHost,
    ReferenceExpressionHost,
    ReplacedRootsHost,
    RuntimeExpressionFlags,
    _context_models,
    _copy_data_type,
    _forward_enum_member_literals,
    _get_data_type_target_full_name,
    _get_model_module_name,
    _hidden_type_names,
    _is_pydantic_v2_root_model,
    _ModelPositions,
    _unwrap_type_alias,
    constructor_base_models,
    get_constructor_field_adjustment,
    get_constructor_ordering_conflicts,
    get_inherited_constructor_info,
    has_import_override,
    module_members,
    normalize_default_value_constraints,
    resolve_default_value_descriptor,
    set_reference_expression,
    unused_model_lists,
)
from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base
from datamodel_code_generator.python_literal import PythonCode, PythonRuntimeExpression
from datamodel_code_generator.types import DataType, DefaultValueRecipe, type_expr_of

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.enums import DefaultValueType, TargetPydanticVersion
    from datamodel_code_generator.model._render_plan import (
        AssignmentFacts,
        FieldRenderPlan,
        NestedFactoryMetadata,
        PlannedField,
        RenderReady,
    )
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._passes._shared import _ConstructorFieldAdjustment, _InheritedConstructorInfo
    from datamodel_code_generator.parser._passes._state import (
        ModuleContextView,
        ModulePath,
        _FinalizeState,
        _PreparedModule,
    )
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataTypeManager, DefaultValueDescriptor

__all__ = [
    "InheritedFieldCopies",
    "RenderPrepHost",
    "assign_module_forward_reference_fields",
    "collect_all_models",
    "collect_live_models",
    "decide_nested_model_default_factories",
    "deserialize_default_value",
    "finalize_rendered_nullability",
    "finalize_sequence_interface",
    "fix_constructor_field_ordering",
    "fix_constructor_field_ordering_bases_first",
    "fix_tag_opt_out_field_ordering",
    "freeze_field_render_plans",
    "mark_set_item_models_hashable",
    "order_constructor_fields_by_decided_nested_factories",
    "prepare_type_alias_fields",
    "set_validate_default_on_fields",
    "unbind_hidden_type_fields",
    "update_module_type_aliases",
    "validates_structured_default",
    "warn_required_assignments_after_defaults",
]


class RenderPrepHost(
    ConstructorInfoHost,
    RecursionFactsHost,
    ReferenceExpressionHost,
    ReplacedRootsHost,
    RuntimeExpressionFlags,
    Protocol,
):
    """The options, run state and hooks of the parser that the render preparation passes read and write."""

    _decimal_default_warning_count: int
    _decimal_default_warning_examples: list[str] | None

    @property
    def _import_overrides(self) -> dict[str, str] | None:
        """The modules ``--import-overrides`` routes imports through, by imported name."""

    @property
    def _quotes_hidden_types(self) -> bool:
        """Whether annotations are not deferred, so a type that a field hides is written as a string."""

    @property
    def config(self) -> ParserConfig:
        """The parser configuration."""

    @property
    def data_model_field_type(self) -> type[DataModelFieldBase]:
        """The field type of the output."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def data_type_manager(self) -> DataTypeManager:
        """The data type manager of the output."""

    @property
    def deserialize_default_value_types(self) -> frozenset[DefaultValueType]:
        """The kinds of default values deserialized into their types."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""

    @property
    def pydantic_v2_root_model_type(self) -> type[DataModel] | None:
        """The root model type whose class body evaluates its root type, if the output has one."""

    @property
    def target_pydantic_version(self) -> TargetPydanticVersion | None:
        """The Pydantic version the output targets, if configured."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    def _copy_model_field(self, field: DataModelFieldBase) -> DataModelFieldBase:
        """Copy a field with its data types."""

    def _nested_model_default_factory_decision(
        self,
        modules: Sequence[Sequence[DataModel]],
        require_update: Collection[ModelKey],
    ) -> NestedFactoryMetadata:
        """Decide the declaration order and the reference cycles that the nested models' default factories read."""

    def _register_runtime_expression(self) -> None:
        """Mark a parser-owned expression created after the initial import scan."""


_DECIMAL_WARNING_EXAMPLE_LIMIT: Final = 5


@record
class InheritedFieldCopies:
    """The written modules that keyword-only copies of inherited fields go to, and how they name other modules."""

    contexts: Sequence[ModuleContextView]
    internal_modules: set[ModulePath]
    model_path_to_module_name: Mapping[str, str]


def _assigned_key(model: DataModel) -> Callable[[DataModelFieldBase], bool]:
    """Return the sort key that puts the fields the model's class body assigns after the others.

    A field counts as it takes its place in the constructor (``AssignmentFacts.place_assigned``).
    """
    return lambda field: model._field_assignment_facts(field).place_assigned  # noqa: SLF001


def _apply_constructor_field_adjustments(
    builder: GraphBuilder,
    model: DataModel,
    first_adjustment: tuple[DataModelFieldBase, _ConstructorFieldAdjustment],
    adjustments: Iterable[tuple[DataModelFieldBase, _ConstructorFieldAdjustment]],
) -> None:
    """Apply exact required-field assignments and keyword-only ordering fixes."""
    enable_model_keyword_only = False
    for field, adjustment in chain((first_adjustment,), adjustments):
        match adjustment:
            case "assignment":
                builder.force_field_assignment(field)
            case _:
                assert adjustment == "keyword_only"
                if model.REQUIRES_MODEL_LEVEL_KW_ONLY:
                    enable_model_keyword_only = True
                else:
                    builder.set_field_extra(field, "kw_only", value=True)
    if enable_model_keyword_only:
        model.enable_model_keyword_only()


def _contains_model_reference(data_type: DataType) -> bool:
    """Check if a data type tree contains any reference to a non-alias model."""
    stack = [data_type]
    seen: set[int] = set()
    while stack:
        resolved = _unwrap_type_alias(stack.pop())
        resolved_id = id(resolved)
        if resolved_id in seen:
            continue
        seen.add(resolved_id)
        if (
            resolved.reference
            and isinstance(resolved.reference.source, DataModel)
            and not isinstance(resolved.reference.source, Enum)
            and not resolved.reference.source.is_alias
        ):
            return True
        if resolved.dict_key:
            stack.append(resolved.dict_key)
        stack.extend(resolved.data_types)
    return False


def _needs_validate_default(data_type: DataType) -> bool:
    """Check if a field needs validate_default=True to coerce defaults into model instances."""
    resolved = _unwrap_type_alias(data_type)
    return _contains_model_reference(resolved)


def _applies_default(field: DataModelFieldBase) -> bool:
    """Return whether the default of a field is one that render preparation reads: set, and in effect for the field."""
    return not (
        (field.required and not field.use_default_with_required)
        or field.default is None
        or field.default is UNDEFINED
        or isinstance(field.default, Member)
    )


def validates_structured_default(field: DataModelFieldBase) -> bool:
    """Return whether render preparation writes ``validate_default=True`` for the default of a field.

    It reads the facts that are final at the end of the module loop: the required state, the default and the type.
    """
    return _applies_default(field) and _needs_validate_default(field.data_type)


def _wraps_member_hints(data_type: DataType) -> bool:
    """Return whether the type writes the hints of its members inside a container."""
    return (
        data_type.is_list
        or data_type.is_dict
        or data_type.is_set
        or data_type.is_frozen_set
        or data_type.is_mapping
        or data_type.is_sequence
        or data_type.is_tuple
    )


def _no_hidden_type_names(model: DataModel) -> Collection[str | None]:  # noqa: ARG001
    """Report no hidden types, for annotations that a future import defers."""
    return ()


def _is_generic_class_container(data_type: DataType) -> bool:
    """Return whether the type subscripts a generic class, whose arguments mypy resolves eagerly."""
    return (
        data_type.is_list
        or data_type.is_dict
        or data_type.is_set
        or data_type.is_frozen_set
        or data_type.is_mapping
        or data_type.is_sequence
        or (data_type.is_tuple and data_type.use_standard_collections)
    )


def _iter_alias_references(data_type: DataType, *, in_container: bool = False) -> Iterator[tuple[DataModel, bool]]:
    """Yield each referenced deferrable alias with whether a container encloses the reference."""
    in_container = in_container or _is_generic_class_container(data_type)
    if (
        (reference := data_type.reference)
        and isinstance(source := reference.source, DataModel)
        and source.DEFERS_RECURSIVE_TYPE_HINT
    ):
        yield source, in_container
    for child in (*data_type.data_types, *((data_type.dict_key,) if data_type.dict_key else ())):
        yield from _iter_alias_references(child, in_container=in_container)


def _iter_outermost_containers(
    data_type: DataType, wrappers: tuple[DataType, ...] = ()
) -> Iterator[tuple[DataType, tuple[DataType, ...]]]:
    """Yield each container no other container encloses, with the types that wrap it."""
    if _is_generic_class_container(data_type):
        yield data_type, wrappers
        return
    for child in data_type.data_types:
        yield from _iter_outermost_containers(child, (*wrappers, data_type))


def _only_groups_members(data_type: DataType) -> bool:
    """Return whether the type only groups its members, so an enclosing union can hold them directly."""
    return bool(data_type.data_types) and not (
        data_type.type
        or data_type.reference
        or data_type.alias
        or data_type.is_func
        or data_type.kwargs
        or data_type.python_type
        or data_type.literals
        or data_type.enum_member_literals
        or data_type.dict_key
        or data_type.discriminator
        or data_type.use_serialize_as_any
        or data_type.is_custom_type
        or data_type.is_tuple
        or data_type._is_forward_reference  # noqa: SLF001
        or _is_generic_class_container(data_type)
    )


def _flatten_union_members(data_type: DataType, enclosing: tuple[DataType, ...] = ()) -> None:
    """Hoist grouped members and their ``None`` into the union, so ruff finds no nested or repeated member to rewrite.

    Ruff rewrites a union with a nested union or a repeated member into a ``|`` chain, which fails at runtime
    once a member is a string. A member's ``None`` moves to its union and to each union that one is flattened into.
    """
    members: list[DataType] = []
    unions = (data_type, *enclosing)
    for member in data_type.data_types:
        if member.is_optional:
            member._materialize_render_state("null-moved-to-union")  # noqa: SLF001
            for union in unions:
                union._materialize_render_state("member-null")  # noqa: SLF001
        if _only_groups_members(member):
            _flatten_union_members(member, unions)
            members.extend(member.data_types)
        else:
            members.append(member)
    for member in members:
        member.parent = data_type
    data_type.data_types = members


def _alias_reference_cycles(models: Sequence[DataModel]) -> dict[int, int]:
    """Map each alias on a cycle whose every reference is a container item to that cycle."""
    aliases: dict[ModelKey, DataModel] = {}
    graph: dict[ModelKey, set[ModelKey]] = {}
    pending = [model for model in models if model.DEFERS_RECURSIVE_TYPE_HINT]
    while pending:
        if (key := model_key_of((model := pending.pop()).reference)) in graph:
            continue
        aliases[key] = model
        graph[key] = set()
        for field in model.fields:
            for target, in_container in _iter_alias_references(field.data_type):
                if in_container:
                    graph[key].add(model_key_of(target.reference))
                    pending.append(target)
    snapshot = reference_graph(GraphSnapshot.ALIAS_CYCLES, graph, {EdgeKind.CONTAINER_ITEM: graph})
    cycles = graph_answer(
        GraphSnapshot.ALIAS_CYCLES,
        snapshot.circular_components((EdgeKind.CONTAINER_ITEM,)),
        lambda: find_circular_sccs(graph),
    )
    return {id(aliases[key]): index for index, scc in enumerate(cycles) for key in scc}


def _iter_cycle_containers(
    data_type: DataType, cycle: int, cycles: Mapping[int, int], forward_references: Collection[int]
) -> Iterator[tuple[DataType, tuple[DataType, ...], set[bool]]]:
    """Yield each outermost container with its wrappers and whether its cycle references are forward ones."""
    for container, wrappers in _iter_outermost_containers(data_type):
        yield (
            container,
            wrappers,
            {
                id(nested) in forward_references
                for nested in container.all_data_types
                if nested.reference and cycles.get(id(nested.reference.source)) == cycle
            },
        )


def collect_set_item_references(models: list[DataModel]) -> list[Reference]:
    """Collect the references of all types used as set/frozenset items."""
    return [
        nested.reference
        for model in models
        for field in model.fields
        for data_type in field.data_type.all_data_types
        if data_type.is_set or data_type.is_frozen_set
        for item_type in data_type.data_types
        for nested in item_type.all_data_types
        if nested.reference
    ]


def mark_set_item_models_hashable(parser: RenderPrepHost, models: list[DataModel]) -> None:
    """Mark models used as set/frozenset items with hash flag for __hash__ generation, found by model key."""
    set_item_references = collect_set_item_references(models)
    if not set_item_references:
        return
    keyed = keyed_models(models, "set item models")
    set_item_keys = keyed.keys_of(set_item_references)
    set_item_models = [
        model for model in models if model_key_of(model.reference) in set_item_keys and not isinstance(model, Enum)
    ]
    # User imports may shadow the builtin annotations recognized by the backend.
    native_hash_paths = (
        set()
        if parser.config.additional_imports
        else parser.data_model_type.get_native_hash_model_paths(set_item_models)
    )
    for model in set_item_models:
        if model.reference.path in native_hash_paths:
            continue
        model.enable_identity_hash()


def set_validate_default_on_fields(
    parser: RenderPrepHost,
    models: list[DataModel],
) -> None:
    """Set validate_default=True on fields with structured defaults needing validation."""
    deserialized_default = False
    for model in models:
        if isinstance(model, Enum):
            continue
        for model_field in model.fields:
            if not _applies_default(model_field):
                continue
            deserialized_default = (
                deserialize_default_value(
                    parser,
                    model,
                    model_field,
                )
                or deserialized_default
            )
            if _needs_validate_default(model_field.data_type) and model_field.enable_structured_default_validation():
                parser._graph_builder.set_field_extra(model_field, "validate_default", value=True)  # noqa: SLF001
    if deserialized_default:
        normalize_default_value_constraints(
            parser,
            models,
            data_type_manager=parser.data_type_manager,
            import_overrides=parser._import_overrides,  # noqa: SLF001
        )


def deserialize_default_value(
    parser: RenderPrepHost,
    model: DataModel,
    field: DataModelFieldBase,
) -> bool:
    """Deserialize one backend-declared scalar default or record its warning."""
    if (
        not model.SUPPORTS_DESERIALIZED_DEFAULT_VALUES
        or field.has_default_factory
        or isinstance(field.default, (PythonCode, PythonRuntimeExpression))
    ):
        return False
    if (resolved := resolve_default_value_descriptor(parser.data_type_manager, field.data_type)) is None:
        return False
    annotation_import, descriptor = resolved
    if has_import_override(parser._import_overrides, annotation_import, descriptor.constructor_import):  # noqa: SLF001
        return False
    match descriptor.recipe:
        case DefaultValueRecipe.Decimal:
            return deserialize_decimal_default(
                parser,
                model,
                field,
                descriptor,
            )
    return False  # pragma: no cover - future backend recipe


def deserialize_decimal_default(
    parser: RenderPrepHost,
    model: DataModel,
    field: DataModelFieldBase,
    descriptor: DefaultValueDescriptor,
) -> bool:
    """Deserialize one Decimal recipe after backend classification."""
    default_type = type(field.default)
    if default_type.__module__ == "decimal" and default_type.__name__ == "Decimal":
        # Retain the constructor identity for alias resolution.
        value = str(field.default)
    elif descriptor.option_kind not in parser.deserialize_default_value_types:
        record_decimal_default_warning(parser, model, field)
        return False
    else:
        from decimal import Decimal, InvalidOperation  # noqa: PLC0415

        try:
            value = str(Decimal(str(field.default)))
        except (InvalidOperation, TypeError, ValueError):
            record_decimal_default_warning(parser, model, field)
            return False
    parser._graph_builder.set_field_default(  # noqa: SLF001
        field, PythonRuntimeExpression.from_import_call(descriptor.constructor_import, repr(value), value=value)
    )
    field._set_runtime_expression_imports((descriptor.constructor_import,))  # noqa: SLF001
    parser._register_runtime_expression()  # noqa: SLF001
    return True


def record_decimal_default_warning(parser: RenderPrepHost, model: DataModel, field: DataModelFieldBase) -> None:
    """Record a bounded example set without retaining every affected field."""
    parser._decimal_default_warning_count += 1  # noqa: SLF001
    examples = parser._decimal_default_warning_examples  # noqa: SLF001
    if examples is None:
        parser._decimal_default_warning_examples = [f"{model.class_name}.{field.name or '<root>'}"]  # noqa: SLF001
    elif len(examples) < _DECIMAL_WARNING_EXAMPLE_LIMIT:
        examples.append(f"{model.class_name}.{field.name or '<root>'}")


def fix_tag_opt_out_field_ordering(opted_out: list[DataModel], models: list[DataModel]) -> None:
    """Make keyword-only the structs that opted out of a tag, and those inheriting from them, when they need it.

    A field named like the inherited tag field is then a field of its own after the inherited ones, as the base
    declares the tag field as a class variable instead, so a required one can follow a default. Bases are fixed
    before the structs inheriting from them, as their keyword-only fields are not positional there.
    """
    if not opted_out:
        return
    opted_out_ids = {id(model) for model in opted_out}
    for model, ancestors in sorted(
        (
            (model, ancestors)
            for model in models
            if (ancestors := linearize_data_models(_find_base_classes(model)))
            and not opted_out_ids.isdisjoint(map(id, (model, *ancestors)))
        ),
        key=lambda item: len(item[1]),
    ):
        effective_fields: dict[str, tuple[DataModelFieldBase, DataModel, bool | None]] = {
            field.name: (field, declaring_model, None)
            for declaring_model in (*reversed(ancestors), model)
            for field in declaring_model.fields
            if field.name and not field.is_class_var
        }
        if get_constructor_ordering_conflicts(model, effective_fields, set()).own:
            model.enable_model_keyword_only()


def fix_constructor_field_ordering(
    parser: RenderPrepHost,
    models: list[DataModel],
    *,
    early: bool = False,
    copies: InheritedFieldCopies | None = None,
) -> None:
    """Fix constructor field ordering after inherited defaults are resolved.

    ``early`` runs it where the modules are processed, before the overrides replace the bases and before every
    field has its final name, the form of ``--order-constructor-fields-by final-names`` off. The rendered nullability
    is final only after the modules, so it then counts the defaults as the construction key gives them. With
    ``copies``, a written model gets a keyword-only copy of each inherited field that two of its bases put after a
    default. The nested factories read row ``legacy-nested-factory-order-before-nullability``.
    """
    with ordering_constructor_fields(before_nullability=True, construction_defaults=early):
        fix_constructor_field_ordering_of(parser, models, early=early, copies=copies)


def fix_constructor_field_ordering_bases_first(
    parser: RenderPrepHost, models: list[DataModel], copies: InheritedFieldCopies
) -> None:
    """Fix the constructor field ordering of every module's models, each after the models it inherits from.

    A model then reads its bases as this pass left them: the fields they make keyword-only and the copies they take.
    """
    fix_constructor_field_ordering(
        parser,
        sorted(models, key=lambda model: len(constructor_base_models(parser, model))),
        copies=copies,
    )


def order_constructor_fields_by_decided_nested_factories(parser: RenderPrepHost, state: _FinalizeState[Any]) -> None:
    """Order constructor fields again by the nested factories render preparation decided.

    Under ``--order-constructor-fields-by written-defaults`` the orderings before the decision leave the nested
    factories out, so a factory that a reference cycle drops moves no field.
    """
    with cast("RenderReady", RENDER_READY.get()).nested_factories_decided():
        fix_constructor_field_ordering(parser, state.all_models)


def fix_constructor_field_ordering_of(
    parser: RenderPrepHost, models: list[DataModel], *, early: bool, copies: InheritedFieldCopies | None = None
) -> None:
    """Fix the constructor field ordering of ``models`` (``fix_constructor_field_ordering``)."""
    written = {} if copies is None else {id(model): ctx for ctx in copies.contexts for model in ctx.models}
    for model in models:
        restored_inherited_state = False
        for field in model.fields:
            restored_inherited_state = (
                parser._graph_builder.restore_required_inherited_field(model, field)  # noqa: SLF001
                or restored_inherited_state
            )

        if (inherited := get_inherited_constructor_info(parser, model, early=early)) is None:
            if (order_key := get_local_constructor_ordering_key(model)) is not None:
                parser._graph_builder.set_fields(model, sorted(model.fields, key=order_key))  # noqa: SLF001
            continue
        if (
            copies is not None
            and (ctx := written.get(id(model))) is not None
            and (copied := copy_inherited_conflicts(parser, model, inherited, ctx, written, copies))
        ):
            parser._graph_builder.set_fields(model, [*copied, *model.fields])  # noqa: SLF001
            inherited = cast("_InheritedConstructorInfo", get_inherited_constructor_info(parser, model))
        field_adjustments: Iterator[tuple[DataModelFieldBase, _ConstructorFieldAdjustment]] = (
            (field, adjustment)
            for field in model.fields
            if (
                adjustment := get_constructor_field_adjustment(
                    model,
                    field,
                    inherited,
                    supports_inherited_override=model.SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT,
                )
            )
            is not None
        )
        first_adjustment: tuple[DataModelFieldBase, _ConstructorFieldAdjustment] | None = next(
            field_adjustments,
            None,
        )
        if first_adjustment is None:
            if restored_inherited_state:
                parser._graph_builder.set_fields(model, sorted(model.fields, key=_assigned_key(model)))  # noqa: SLF001
            continue

        _apply_constructor_field_adjustments(
            parser._graph_builder,  # noqa: SLF001
            model,
            first_adjustment,
            field_adjustments,
        )
        parser._graph_builder.set_fields(model, sorted(model.fields, key=_assigned_key(model)))  # noqa: SLF001


def copy_inherited_conflicts(  # noqa: PLR0913, PLR0917
    parser: RenderPrepHost,
    model: DataModel,
    inherited: _InheritedConstructorInfo,
    ctx: ModuleContextView,
    written: Mapping[int, ModuleContextView],
    copies: InheritedFieldCopies,
) -> list[DataModelFieldBase]:
    """Copy into ``model`` the inherited fields that follow a constructor default its bases set up.

    ``dataclasses`` rejects such a class, and only ``model`` joins the bases, so each copy becomes one of its own
    fields, which the ordering then makes keyword-only. A copy names the models of its type from the module of
    ``model``. A field of a model that is not written, a stand-in or a base a model-level override replaced, is left
    as it is.
    """
    if not inherited.inherited_conflicts:
        return []
    members = keyed_models(module_members(parser, ctx.models), "module members")
    module_name = _get_model_module_name(model, copies.model_path_to_module_name)
    copied_fields: list[DataModelFieldBase] = []
    for field, declaring_model in inherited.inherited_conflicts:
        if id(declaring_model) not in written:
            continue
        copied = parser._copy_model_field(field)  # noqa: SLF001
        for data_type in copied.data_type.all_data_types:
            if (reference := data_type.reference) is None:
                continue
            set_reference_expression(
                parser,
                data_type,
                module_name,
                _get_data_type_target_full_name(data_type, reference, copies.model_path_to_module_name),
                ctx.imports,
                ctx.scoped_model_resolver,
                same_module=members.key_of(reference) in members.models,
                init=ctx.is_init,
                internal_modules=copies.internal_modules,
                producer=ImportProducer("inherited-keyword-only-copies", model),
            )
        copied_fields.append(copied)
    return copied_fields


def get_local_constructor_ordering_key(
    model: DataModel,
) -> Callable[[DataModelFieldBase], tuple[bool, bool, bool, bool]] | None:
    """Return the positional order key that keeps constructor defaults last, when a local required field follows one.

    A required field rendered as ``= Field(...)`` is a dataclass default but required in the validator's
    signature, so it also precedes fields with real defaults when the output model asks for that order.
    Otherwise only the assignments the output model keeps among the required fields precede them. Fields that the
    written defaults tie are then ordered as they take their places (``AssignmentFacts.place_assigned``), the order
    of 0.86.0. Each field is classified once, for the check and the key.
    """
    if not model.SUPPORTS_KW_ONLY:
        return None
    facts = {id(field): model._field_assignment_facts(field) for field in model.fields}  # noqa: SLF001
    if model.orders_required_assignments_before_defaults():

        def written_key(field: DataModelFieldBase, field_facts: AssignmentFacts) -> tuple[bool, bool]:  # noqa: ARG001
            return field_facts.assigned, field_facts.has_default

    else:

        def written_key(field: DataModelFieldBase, field_facts: AssignmentFacts) -> tuple[bool, bool]:
            return (
                assigned := field_facts.assigned,
                assigned and not model.keeps_assignment_with_required_fields(field),
            )

    def order_key(field: DataModelFieldBase) -> tuple[bool, bool, bool, bool]:
        key = written_key(field, field_facts := facts[id(field)])
        return (*key, *(key if field_facts.place_assigned else (False, False)))

    previous_key = (False, False, False, False)
    for field in model.fields:
        if not facts[id(field)].participates_in_constructor:
            continue
        kw_only = field.constructor_keyword_only
        if kw_only is True or (kw_only is None and model.has_keyword_only_definition()):
            continue
        if (key := order_key(field)) < previous_key:
            return order_key
        previous_key = key
    return None


def warn_required_assignments_after_defaults(models: list[DataModel]) -> None:
    """Warn once per model whose signature lists a required ``= Field(...)`` field after a default.

    Such a field, its own or inherited, is a dataclass default, so the class is created, but its validator
    signature cannot be read on Pydantic 2.13+, which Python 3.10 does while creating a subclass.
    """
    for model in models:
        if (
            model.REQUIRED_ASSIGNMENT_COUNTS_AS_CONSTRUCTOR_DEFAULT
            and not model.orders_required_assignments_before_defaults()
            and has_required_assignment_after_default(model)
        ):
            warn_as_parser_base(
                f"{model.class_name} has a required field after a field with a default, in its own fields or "
                "those of its bases: Pydantic 2.13 and later cannot read its signature, and on Python 3.10 its "
                "subclasses fail at import. Pass --target-pydantic-version 2.14 to write required fields first.",
                UserWarning,
                stacklevel=2,
            )


def has_required_assignment_after_default(model: DataModel) -> bool:
    """Return whether a positional field without a signature default follows one with a default."""
    fields: dict[str, tuple[DataModelFieldBase, DataModel]] = {}
    for declaring_model in (
        *reversed(
            linearize_data_models([
                base.reference.source
                for base in model.base_classes
                if base.reference and isinstance(base.reference.source, DataModel)
            ])
        ),
        model,
    ):
        fields.update((field.name, (field, declaring_model)) for field in declaring_model.fields if field.name)
    seen_default = False
    for field, declaring_model in fields.values():
        facts = model._field_assignment_facts(field)  # noqa: SLF001
        kw_only = field.constructor_keyword_only
        if (
            not facts.participates_in_constructor
            or kw_only is True
            or (kw_only is None and declaring_model.has_keyword_only_definition())
        ):
            continue
        has_default = facts.has_default
        if seen_default and not has_default:
            return True
        seen_default = seen_default or has_default
    return False


def disable_union_operator_for_forward_ref(
    data_type: DataType,
    graph_builder: GraphBuilder,
    inlined_holders: Callable[[DataType], tuple[DataType, ...]],
    *,
    use_typing_generics: bool = False,
) -> None:
    """Keep a quoted name out of ``|`` and, with ``use_typing_generics``, out of builtin generics.

    Every union above the name through its parent stops using ``|``. A string cannot be an operand of ``|``, so
    every union that joins the name, itself or through the unions it spreads, and every type whose hint is the
    name, as it joins its ``None``, stop using it too where they hold the name through a type sharing the parent's
    children, as the sites of a collapsed root model do (``inlined_holders``). A container ends the walk: it takes
    the name inside its brackets. With ``use_typing_generics``, every type above the name through its parent and
    every type holding the name itself writes the ``typing`` generic, as a builtin generic before Python 3.11 does
    not resolve a string it holds.
    """
    graph_builder.drop_union_operator(data_type)
    parent = data_type.parent
    while isinstance(parent, DataType):
        if parent.is_union:
            graph_builder.drop_union_operator(parent)
        if use_typing_generics:
            graph_builder.use_typing_generics(parent)
        parent = parent.parent
    pending = [(data_type, True)]
    seen: set[int] = set()
    while pending:
        node, writes_name = pending.pop()
        for holder in (node.parent, *inlined_holders(node)):
            if not isinstance(holder, DataType) or id(holder) in seen or holder.alias or holder.type:
                continue
            seen.add(id(holder))
            joins_members = holder.is_union and not holder.is_tuple
            wraps_members = _wraps_member_hints(holder)
            if joins_members or (writes_name and not wraps_members):
                graph_builder.drop_union_operator(holder)
            if use_typing_generics and node is data_type:
                graph_builder.use_typing_generics(holder)
            if not wraps_members:
                pending.append((holder, writes_name and not joins_members))


def field_forward_references(  # noqa: PLR0913
    field: DataModelFieldBase,
    model: DataModel,
    model_index: Mapping[str, int],
    index: int,
    hidden_names: Collection[str | None] = (),
    *,
    quotes_type_statements: bool = False,
) -> list[tuple[DataType, str]]:
    """Collect the field's references to same-module models declared at or after the model.

    A model whose name is in ``hidden_names`` counts as declared later. A ``type`` statement counts only with
    ``quotes_type_statements``, for a type that the module evaluates as it runs: its value is deferred, but its name
    is bound only when the statement runs, and a deferred annotation reaches the name later.
    """
    forward_references: list[tuple[DataType, str]] = []
    for data_type in field.data_type.all_data_types:
        if not data_type.reference:
            continue
        source = data_type.reference.source
        if not isinstance(source, DataModel):
            continue  # pragma: no cover
        if source.module_path != model.module_path:
            continue
        name = data_type.reference.short_name
        if name in hidden_names and not data_type.alias:
            forward_references.append((data_type, name))
            continue
        if isinstance(source, TypeStatement) and not quotes_type_statements:
            continue
        source_index = model_index.get(name)
        if source_index is not None and source_index >= index:
            forward_references.append((data_type, name))
    return forward_references


def quote_forward_rule_types(model: DataModel, model_index: Mapping[str, int], index: int) -> None:
    """Write each schema validator rule type that names a model declared at or after the model as one string.

    The class body evaluates its rules when it runs, before the model and the later models of its module are bound;
    the validator helper resolves the string in the module when it validates. The rules get quoted copies, so
    another holder of the rules or their types keeps them.
    """
    if not (runtime_validation := model.schema_runtime_validation) or not runtime_validation.pattern_properties:
        return

    def quoted(data_type: DataType) -> DataType:
        if not any(
            (reference := nested.reference) is not None
            and isinstance(source := reference.source, DataModel)
            and source.module_path == model.module_path
            and model_index.get(reference.short_name, -1) >= index
            for nested in data_type.all_data_types
        ):
            return data_type
        forward = type(data_type)(data_types=[_copy_data_type(data_type)])
        forward._set_forward_reference(True)  # noqa: FBT003, SLF001
        return forward

    rules = [
        dataclasses.replace(
            rule,
            pattern_properties=tuple((pattern, quoted(data_type)) for pattern, data_type in rule.pattern_properties),
            additional_property_type=(
                None if (additional := rule.additional_property_type) is None else quoted(additional)
            ),
        )
        for rule in runtime_validation.pattern_properties
    ]
    if all(
        new_type is old_type
        for rule, old_rule in zip(rules, runtime_validation.pattern_properties, strict=True)
        for new_type, old_type in zip(rule.data_types, old_rule.data_types, strict=True)
    ):
        return
    model._set_internal_template_data(  # noqa: SLF001
        "schema_runtime_validation",
        runtime_validation._replace_pattern_properties(rules),  # noqa: SLF001
    )


def deferrable_alias_cycles(models: Sequence[DataModel], model_index: Mapping[str, int]) -> dict[int, int]:
    """Find the container cycles among aliases that quoted containers resolve for mypy and pyright alike.

    mypy cannot resolve a cycle of aliases that only reference each other as container items, unless
    the containers holding the forward references are strings. pyright cannot resolve an alias declared
    earlier on the cycle from inside such a string, so a cycle with a container that references it both
    forward and backward keeps its names quoted one by one.
    """
    cycles = _alias_reference_cycles(models)
    unresolvable: set[int] = set()
    for index, model in enumerate(models):
        if (cycle := cycles.get(id(model))) is None:
            continue
        for field in model.fields:
            forward = {id(data_type) for data_type, _ in field_forward_references(field, model, model_index, index)}
            if any(len(kinds) > 1 for *_, kinds in _iter_cycle_containers(field.data_type, cycle, cycles, forward)):
                unresolvable.add(cycle)
    return {alias: cycle for alias, cycle in cycles.items() if cycle not in unresolvable}


def defer_recursive_containers(  # noqa: PLR0913
    field: DataModelFieldBase,
    model: DataModel,
    *,
    alias_cycles: Mapping[int, int],
    model_index: Mapping[str, int],
    index: int,
    graph_builder: GraphBuilder,
) -> None:
    """Quote each outermost container that forward-references the model's cycle, with its names unquoted.

    Collapsed root models share nested types between their sites, so the field first gets its own copy.
    A string cannot be an operand of ``|``, so the container and its wrappers stop using the union operator.
    """
    if (cycle := alias_cycles.get(id(model))) is None:
        return
    forward = {id(data_type) for data_type, _ in field_forward_references(field, model, model_index, index)}
    if not any(True in kinds for *_, kinds in _iter_cycle_containers(field.data_type, cycle, alias_cycles, forward)):
        return
    graph_builder.replace_field_type(field, _copy_data_type(field.data_type))
    forward = {id(data_type) for data_type, _ in field_forward_references(field, model, model_index, index)}
    deferred = [
        (container, wrappers)
        for container, wrappers, kinds in _iter_cycle_containers(field.data_type, cycle, alias_cycles, forward)
        if True in kinds
    ]
    for container, wrappers in deferred:
        container._set_forward_reference(True)  # noqa: FBT003, SLF001
        for quoted in (container, *wrappers):
            quoted.use_union_operator = False
        for nested in container.all_data_types:
            if (reference := nested.reference) and nested._is_quoted_reference:  # noqa: SLF001
                graph_builder.name_reference(nested, reference.short_name)
    for root in deferred[0][1][:1]:
        _flatten_union_members(root)


def quote_forward_enum_member_literals(  # noqa: PLR0913
    field: DataModelFieldBase,
    positions: _ModelPositions,
    index: int,
    graph_builder: GraphBuilder,
    inlined_holders: Callable[[DataType], tuple[DataType, ...]],
    *,
    use_typing_generics: bool,
) -> bool:
    """Write each enum member literal of the field whose enum is defined later as a string; return whether any."""
    member_literals = _forward_enum_member_literals(field, positions, index)
    for member_literal in member_literals:
        member_literal._set_forward_reference(True)  # noqa: FBT003, SLF001
        disable_union_operator_for_forward_ref(
            member_literal, graph_builder, inlined_holders, use_typing_generics=use_typing_generics
        )
    return bool(member_literals)


def update_type_aliases(  # noqa: PLR0913
    models: list[DataModel],
    pydantic_v2_root_model_type: type[DataModel] | None,
    *,
    use_deferred_annotations: bool = True,
    defers_annotations_natively: bool,
    defers_recursive_containers: bool,
    graph_builder: GraphBuilder,
    inlined_holders: Callable[[DataType], tuple[DataType, ...]],
    hidden_type_names: Callable[[DataModel], Collection[str | None]],
    quotes_forward_enum_member_literals: bool,
    use_typing_generics: bool,
) -> None:
    """Update type aliases and RootModels to properly handle forward references per PEP 484.

    When annotations are not deferred (no ``from __future__ import annotations`` and no
    native PEP 649 deferred annotations), self-referencing fields in regular DataModel
    classes also need their type aliased to a quoted string so that static analysers
    (e.g. Ruff F821) and older runtimes do not trip over the forward reference. The same
    holds, even for native deferred annotations, for the types that ``hidden_type_names`` reports as hidden
    by a field of the model, and for the fields that ``fields_evaluated_at_definition`` returns, which the
    statement defining the model evaluates as it runs (the functional TypedDict, with the fields it inherits).
    A ``type`` statement the module defines later is quoted only where the runtime evaluates its name as the model is
    defined: in those fields, in aliases and root models, in every field unless the annotations are deferred, by
    ``use_deferred_annotations`` or by the target (``defers_annotations_natively``), and in a field that needs
    immediate resolution unless the target defers its annotations.
    An enum member literal whose enum the module defines later is written as a string wherever a forward name
    is quoted, and with ``quotes_forward_enum_member_literals`` in every model, as no deferred annotation holds it.
    ``use_typing_generics`` tells that ``typing`` cannot resolve ``list["Name"]``, so a hidden type, and any
    quoted name of a model that asks for it, is written as ``List["Name"]``.
    """
    model_index: dict[str, int] = {m.class_name: i for i, m in enumerate(models)}
    positions = _ModelPositions(models, "type aliases")
    alias_cycles: Mapping[int, int] | None = None
    quotes_in_typing_generics = use_typing_generics and not use_deferred_annotations
    evaluates_annotations = not (use_deferred_annotations or defers_annotations_natively)

    for i, model in enumerate(models):
        quote_forward_rule_types(model, model_index, i)
        is_type_alias_or_root = isinstance(model, TypeAliasBase) or _is_pydantic_v2_root_model(
            model,
            pydantic_v2_root_model_type,
        )
        # When annotations are deferred (from __future__ or native PEP-649) only
        # TypeAliasBase / RootModel need quoting; regular DataModels are fine as-is.
        # Typed extra annotations are an exception: class-body __annotations__ dicts
        # are evaluated immediately, and Pydantic can force native deferred annotations
        # while constructing the class, so their forward references must stay quoted.
        evaluated_fields = model.fields_evaluated_at_definition()
        runs_at_definition = is_type_alias_or_root or bool(evaluated_fields)
        process_all_fields = runs_at_definition or not use_deferred_annotations
        quotes_type_statements = runs_at_definition or evaluates_annotations
        quotes_member_literals = process_all_fields or quotes_forward_enum_member_literals
        hidden_names = hidden_type_names(model)
        if (
            not quotes_member_literals
            and not hidden_names
            and not any(field.requires_immediate_forward_reference_resolution for field in model.fields)
        ):
            continue
        if isinstance(model, TypeStatement):
            continue

        has_aliased_forward_ref = False
        for field in evaluated_fields or model.fields:
            if quotes_member_literals and quote_forward_enum_member_literals(
                field,
                positions,
                i,
                graph_builder,
                inlined_holders,
                use_typing_generics=quotes_in_typing_generics and model.WRITES_QUOTED_NAMES_IN_TYPING_GENERICS,
            ):
                has_aliased_forward_ref = True
            if (
                hidden_only := not process_all_fields and not field.requires_immediate_forward_reference_resolution
            ) and not hidden_names:
                continue
            forward_references = field_forward_references(
                field,
                model,
                model_index,
                i,
                hidden_names,
                quotes_type_statements=quotes_type_statements
                or (field.requires_immediate_forward_reference_resolution and not defers_annotations_natively),
            )
            if hidden_only:
                forward_references = [reference for reference in forward_references if reference[1] in hidden_names]
            if not forward_references:
                continue
            for data_type, name in forward_references:
                graph_builder.name_reference(data_type, f'"{name}"')
                disable_union_operator_for_forward_ref(
                    data_type,
                    graph_builder,
                    inlined_holders,
                    use_typing_generics=quotes_in_typing_generics
                    and (model.WRITES_QUOTED_NAMES_IN_TYPING_GENERICS or name in hidden_names),
                )
            if defers_recursive_containers and model.DEFERS_RECURSIVE_TYPE_HINT:
                if alias_cycles is None:
                    alias_cycles = deferrable_alias_cycles(models, model_index)
                defer_recursive_containers(
                    field,
                    model,
                    alias_cycles=alias_cycles,
                    model_index=model_index,
                    index=i,
                    graph_builder=graph_builder,
                )
            has_aliased_forward_ref = True

        if has_aliased_forward_ref and process_all_fields and not model.has_forward_reference:
            graph_builder.mark_forward_references(model)


def assign_module_forward_reference_fields(parser: RenderPrepHost, models: list[DataModel]) -> None:
    """Let each model render fields by assignment when ``Field()`` inside ``Annotated`` would be unsafe.

    A model that writes fields by assignment may reorder them.
    """
    positions = {model.path: index for index, model in enumerate(models)}
    for index, model in enumerate(models):
        parser._graph_builder.use_assignment_for_forward_references(model, positions, index, refresh="deferred")  # noqa: SLF001


def unbind_hidden_type_fields(parser: RenderPrepHost, state: _PreparedModule[Any]) -> None:
    """Leave fields unbound where they hide a type of their class's annotations and the model can."""
    for model in state.model_list:
        if (names := _hidden_type_names(model)) and model.unbind_fields(names):
            parser._graph_builder.note_changed_models(model)  # noqa: SLF001


def update_module_type_aliases(parser: RenderPrepHost, state: _PreparedModule[Any]) -> None:
    """Quote forward references and defer recursive containers; without future imports a hidden type is a string."""
    update_type_aliases(
        state.model_list,
        parser.pydantic_v2_root_model_type,
        use_deferred_annotations=state.config.use_deferred_annotations,
        defers_annotations_natively=python_target_meets(
            "python.deferred_annotations", parser.target_python_version.version_key
        ),
        defers_recursive_containers=pydantic_target_meets(
            "pydantic.type_alias_module_namespace", parser.target_pydantic_version
        ),
        graph_builder=parser._graph_builder,  # noqa: SLF001
        inlined_holders=parser.generation_store.index.inlined_holders_of,
        hidden_type_names=_hidden_type_names if parser._quotes_hidden_types else _no_hidden_type_names,  # noqa: SLF001
        quotes_forward_enum_member_literals=parser._quotes_hidden_types,  # noqa: SLF001
        use_typing_generics=not python_target_meets(
            "python.builtin_generic_strings", parser.target_python_version.version_key
        ),
    )


def collect_live_models(state: _PreparedModule[Any]) -> None:
    """Take the models of the module that are not unused."""
    if not (unused_models := state.current_unused_models):
        state.live_models = state.model_list
        return
    unused_model_ids = {id(model) for model in unused_models}
    state.live_models = [model for model in state.model_list if id(model) not in unused_model_ids]


def prepare_type_alias_fields(
    parser: RenderPrepHost, contexts: Sequence[ModuleContextView], unused_models: list[DataModel]
) -> None:
    """Prepare alias bodies once every module has collapsed its roots.

    A type alias has no model config to select Python's regex engine, so its patterns carry it,
    and its constraints use ``Annotated`` because type checkers reject ``con*`` calls in aliases.
    Module imports drop the replaced ``con*`` imports; finalization adds the annotation imports.
    """
    if (prepare_field := parser.data_model_field_type.PREPARE_TYPE_ALIAS_FIELD) is None:
        return
    prepared_patterns: dict[str, PythonRuntimeExpression] = {}
    unused_model_ids = {id(model) for model in unused_models}
    replace_field_type = parser._graph_builder.replace_field_type  # noqa: SLF001
    for ctx in contexts:
        for model in ctx.models:
            if not isinstance(model, TypeAliasBase) or id(model) in unused_model_ids or not model.fields:
                continue
            previous_imports = model.imports
            if prepare_field(field := model.fields[0], prepared_patterns, replace_field_type):
                field.invalidate_semantic_caches()
                current_imports = model.imports
                parser._import_ledger.remove(  # noqa: SLF001
                    ctx.imports,
                    [import_ for import_ in previous_imports if import_ not in current_imports],
                    ImportProducer("type-alias-fields", model, field),
                )
                parser._register_runtime_expression()  # noqa: SLF001


def finalize_rendered_nullability(parser: RenderPrepHost, state: _FinalizeState[Any]) -> None:
    """Write the None each type hint of every model renders, once per node, before imports and rendering read it.

    It runs after the module bodies: the members' hints read the union operator and the quotes that updating type
    aliases writes (row ``legacy-member-null-from-hint-text``).
    """
    seen: set[int] = set()
    for model in _context_models(state.contexts):
        for field in model.fields:
            for data_type in field.data_type.all_data_types:
                if id(data_type) not in seen:
                    seen.add(id(data_type))
                    parser._graph_builder.write_rendered_null(data_type)  # noqa: SLF001


def collect_all_models(state: _FinalizeState[Any]) -> None:
    """Take the models of every module, before unused models leave them."""
    state.all_models = [model for ctx in state.contexts for model in ctx.models]


def finalize_sequence_interface(state: _FinalizeState[Any]) -> None:
    """Let each root model decide whether it renders the sequence interface."""
    for model in state.all_models:
        model.finalize_sequence_interface()


def decide_nested_model_default_factories(parser: RenderPrepHost, state: _FinalizeState[Any]) -> None:
    """Decide the nested models' default factories over the model lists the modules write, unused models gone."""
    written_lists = {id(ctx.models) for ctx in state.contexts}
    leaving = {
        id(model)
        for model, _, models in unused_model_lists(parser, state)
        if id(models) in written_lists and model in models
    }
    cast("RenderReady", RENDER_READY.get()).nested_factories = parser._nested_model_default_factory_decision(  # noqa: SLF001
        [[model for model in ctx.models if id(model) not in leaving] for ctx in state.contexts],
        parser._recursion_facts["require_update"],  # noqa: SLF001
    )


def freeze_field_render_plans(state: _FinalizeState[Any]) -> None:
    """Freeze one render plan for every field of the models that render fields from plans (B2 follows).

    Under row ``legacy.L-NF`` the plan reads the nested-factory decision, and a field whose factory it changes
    keeps the plan without it for the import snapshot. With ``DATAMODEL_CODE_GENERATOR_CHECK_RENDER_PLANS`` each
    plan must account for every item its field declares.
    """
    ready = cast("RenderReady", RENDER_READY.get())
    mode = check_mode(CheckArea.RENDER_PLANS)
    for model in state.all_models:
        if not model._FREEZES_FIELD_RENDER_PLANS:  # noqa: SLF001
            continue
        for model_field in model.fields:
            if (plan := model_field._build_frozen_render_plan()) is None:  # noqa: SLF001
                continue
            field = cast("PlannedField", model_field)
            import_plan = None
            if ready.nested_factories is not None:
                import_text = field._render_plan_text(plan)  # noqa: SLF001
                with ready.nested_factories_decided():
                    decided = cast("FieldRenderPlan", field._build_frozen_render_plan())  # noqa: SLF001
                    if field._render_plan_text(decided) != import_text:  # noqa: SLF001
                        import_plan = plan
                plan = decided
            ready.freeze(field, plan, import_plan, None if mode is None else type_expr_of(model_field.data_type))
            if mode is not None:
                _check_render_plan_accounting(mode, model, field, plan)
