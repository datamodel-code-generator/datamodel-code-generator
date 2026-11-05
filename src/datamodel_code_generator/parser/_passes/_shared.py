"""Helpers that passes of more than one layer call, as functions of explicit arguments.

A helper that reads parser state takes it as arguments, or takes the parser through one of the narrow protocols below,
which ``Parser`` satisfies structurally. This module imports no parser module but the generation store, the import
ledger, the finality checks, the model graph ids, the allOf keyword rules, the model shapes, the shared keys and the run
state, so it never imports ``parser/base.py``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple, TypeAlias

from typing_extensions import Protocol, final, override

from datamodel_code_generator._constraint_applicability import TYPE_DEPENDENT_CONSTRAINTS_KEY
from datamodel_code_generator._container_kinds import (
    ARRAYS,
    CONTAINER_FLAGS,
    CONTAINERS,
    MAPPINGS,
    SETS,
    UNHASHABLE_CONTAINERS,
    WRITTEN_CONTAINERS,
    written_slot,
)
from datamodel_code_generator._graph_links import clear_parents
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._module_paths import (
    dotted_module as _module_name_from_module_path,  # noqa: F401  # Preserve the established _shared name.
)
from datamodel_code_generator._module_paths import (
    dotted_name,
    exact_import,  # noqa: F401  # Preserve the established _shared name.
    is_ancestor_package_reference,  # noqa: F401  # Preserve the established _shared name.
)
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, reference_graph
from datamodel_code_generator._target_unicode import target_pattern
from datamodel_code_generator._user_keys import class_field_key, model_type_override, type_override_classes
from datamodel_code_generator.imports import Import, _override_module
from datamodel_code_generator.model._decisions.assignment import requires_value
from datamodel_code_generator.model._decisions.hashability import SetItemFacts, set_items_can_hash
from datamodel_code_generator.model._decisions.tag_value import (
    FieldTag,
    enum_tag_literal,
    enum_tag_members,
    field_tag,
    mapped_tags,
    tag_field_kept,
    tag_field_matches,
)
from datamodel_code_generator.model.base import (
    ConstraintsBase,
    DataModel,
    _effective_field_owners,
    _find_base_classes,
    _ModelKeys,
    direct_base_models,
    get_inherited_fields,
    linearize_data_models,
    python_base_classes,
    sort_data_models_for_mro,
)
from datamodel_code_generator.model.enum import Enum, get_raw_enum_member_value
from datamodel_code_generator.model.type_alias import TypeAliasBase, TypeStatement
from datamodel_code_generator.parser._field_rows import uses_default_with_required
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._intersection import inherits_keyword
from datamodel_code_generator.parser._model_shapes import skips_root_model
from datamodel_code_generator.parser._passes._keys import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _DERIVED_FROM_KEY,
    declares_raw_schema_default,
    declares_type_list_unique_items,
    is_unique_items_set,
    raw_schema_default,
)
from datamodel_code_generator.parser._passes._state import RecursionFacts, _ModuleNames
from datamodel_code_generator.python_literal import _text_constructor_expression, stable_repr
from datamodel_code_generator.reference import (
    _SPECIAL_PATH_FORMAT,
    ModelType,
    _source_path,
    _special_path,
    schema_path_of,
    unique_items_of,
)
from datamodel_code_generator.types import DataType, DefaultValueRecipe, union_members

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence

    from typing_extensions import Unpack

    from datamodel_code_generator import AllOfMergeMode, ReuseScope
    from datamodel_code_generator._container_kinds import ContainerSlot
    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator._user_keys import ClassScope
    from datamodel_code_generator.enums import DefaultValueType, TargetPydanticVersion
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model._decisions.tag_value import EnumTagMembers, FieldTagSite, TagFieldMatch
    from datamodel_code_generator.model._render_plan import AssignmentFacts
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._field_rows import FieldCopyFacts, FieldIdentity
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.parser._imports_plan import ModuleImportLedger
    from datamodel_code_generator.parser._model_shapes import BaseDedup, ModelShapes, ReuseGroups, ShapeRow
    from datamodel_code_generator.parser._null_facts import NullRule
    from datamodel_code_generator.parser._passes._state import ModuleContextView, ModulePath
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import ModelResolver, Reference
    from datamodel_code_generator.types import DataTypeManager, DefaultValueDescriptor


def with_dedupe_request_response_hint(message: str) -> str:
    """End the message of an error that the 0.86.0 read/write facts raise with the option that avoids it.

    Without ``--dedupe-request-response-variants`` the facts of a key come from a temporary parse and the walk reads
    every reference of a key, as 0.86.0 did, so an input can stop where the option generates it.
    """
    stop = "" if message.endswith(".") else "."
    return (
        f"{message}{stop} --dedupe-request-response-variants decides the read/write variants from the schema graph"
        " and can generate such schemas."
    )


@record
class _InheritedTypeModifiers:
    """Compact deferred state for a partial inherited field."""

    null_rule: NullRule | None
    is_optional: bool
    is_dict: bool
    is_list: bool
    is_set: bool
    is_frozen_set: bool
    is_mapping: bool
    is_sequence: bool
    is_tuple: bool
    tuple_item_count: int | None
    kwargs: dict[str, Any] | None
    list_wrapper: DataType | None
    drops_null_members: bool = False

    @property
    def excludes_null(self) -> bool:
        """Return whether the overriding schema drops the inherited null."""
        return self.null_rule is not None


def iter_models_field_data_types(
    models: Iterable[DataModel],
) -> Iterator[tuple[DataModel, DataModelFieldBase, DataType]]:
    """Yield (model, field, data_type) for all models, fields, and nested data types."""
    for model in models:
        for field in model.fields:
            for data_type in field.data_type.all_data_types:
                yield model, field, data_type


def _unwrap_type_alias(data_type: DataType) -> DataType:
    """Follow type alias references to the underlying data type."""
    current = data_type
    seen: set[int] = set()
    while current.reference and isinstance(current.reference.source, TypeAliasBase):
        source = current.reference.source
        if id(source) in seen or not source.fields:
            break
        seen.add(id(source))
        current = source.fields[0].data_type
    return current


def _unwrap_default_scalar_data_type(data_type: DataType) -> DataType:
    """Unwrap aliases and nullable scalar wrappers without traversing unions or containers."""
    data_type = _unwrap_type_alias(data_type)
    while (
        data_type.type is None
        and data_type.reference is None
        and len(data_type.data_types) == 1
        and not data_type._is_container  # noqa: SLF001
        and not data_type.literals
        and not data_type.enum_member_literals
    ):
        data_type = _unwrap_type_alias(data_type.data_types[0])
    return data_type


def _resolve_default_scalar_data_type(data_type: DataType) -> DataType | None:
    """Resolve one backend-neutral scalar leaf without traversing unions or containers."""
    if data_type._is_container:  # noqa: SLF001
        return None
    if data_type.reference or data_type.data_types:
        data_type = _unwrap_default_scalar_data_type(data_type)
    if (
        data_type.reference
        or data_type.data_types
        or data_type._is_container  # noqa: SLF001
        or data_type.literals
        or data_type.enum_member_literals
    ):
        return None
    return data_type


def _detach_deferred_inherited_field_parents(field: DataModelFieldBase) -> None:
    """Break parent cycles on a deferred field that is about to be discarded."""
    field.parent = None
    clear_parents(field.data_type)
    match field.__dict__.get(_DEFERRED_INHERITED_TYPE_KEY):
        case DataType() as deferred_type:
            clear_parents(deferred_type)
        case _InheritedTypeModifiers(list_wrapper=DataType() as list_wrapper):
            clear_parents(list_wrapper)


def _restore_inherited_field_names_for_unique_aliases(
    builder: GraphBuilder,
    fields: list[DataModelFieldBase],
    inherited_fields: Mapping[str, DataModelFieldBase],
) -> bool:
    """Keep a required inherited field's Python name when wire aliases must be unique."""
    for field in fields:
        if not (original_name := field.original_name):  # pragma: no cover - msgspec fields come from named properties
            continue
        inherited_field = inherited_fields.get(original_name)
        if (
            inherited_field is None
            or inherited_field.original_name != original_name
            or not (inherited_name := inherited_field.name)
            or field.name == inherited_name
        ):
            continue
        for colliding_field in fields:
            if colliding_field is field or colliding_field.name != inherited_name:
                continue
            if (  # pragma: no cover - each schema object owns one field per wire name
                colliding_field.original_name == original_name or field.name is None
            ):
                continue
            builder.rename_field(colliding_field, field.name, alias=colliding_field.original_name)
            builder.rename_field(field, inherited_name)
            return True
    return False


class GraphBuilderHost(Protocol):
    """The builder that the passes which change the model graph go through."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""


class GenerationStoreHost(GraphBuilderHost, Protocol):
    """The generation store of the parse, with the builder that changes the model graph through it."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""


class MergedDuplicatesHost(Protocol):
    """The duplicates deduplication and reuse merged into each model."""

    @property
    def _merged_duplicates(self) -> dict[int, list[DataModel]]:
        """The duplicates merged into a model, removed or made empty subclasses of it, by the id of that model."""


class MergedReferenceNullHost(GraphBuilderHost, Protocol):
    """The builder that merges a model into an equal one, with the option that keeps the null its users named."""

    @property
    def keep_reference_null_on_merged_models(self) -> bool:
        """Whether a reference keeps the null of the schema it names after its model is merged into an equal one."""


class CollapsedReuseHost(GenerationStoreHost, MergedReferenceNullHost, MergedDuplicatesHost, Protocol):
    """The duplicates collapsed reuse removed and the users it redirected, with the builder that redirects them."""

    @property
    def _collapsed_reuse_models(self) -> dict[int, list[DataModel]]:
        """The models collapsed reuse removed, by the id of their canonical model."""

    @property
    def _collapsed_reuse_users(self) -> dict[int, tuple[DataType, Reference]]:
        """The data types collapsed reuse redirected, with the reference each named before."""


class DataModelTypeHost(Protocol):
    """The model type of the output."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""


class OutputOptionsHost(DataModelTypeHost, Protocol):
    """The model type of the output, the kinds of defaults it deserializes and the Pydantic version it targets."""

    @property
    def deserialize_default_value_types(self) -> frozenset[DefaultValueType]:
        """The kinds of default values deserialized into their types."""

    @property
    def target_pydantic_version(self) -> TargetPydanticVersion | None:
        """The Pydantic version the output targets, if configured."""


class GenericBaseHost(Protocol):
    """The generic base class the output's models inherit from (--use-generic-base-class)."""

    @property
    def generic_base_class_config(self) -> dict[str, Any]:
        """The configuration of the generated base class."""

    @property
    def use_generic_base_class(self) -> bool:
        """Whether the models share a generated base class."""


def generic_base_config(host: GenericBaseHost) -> dict[str, Any] | None:
    """Return the configuration of the generic base class the output's models inherit from, or None without one."""
    return host.generic_base_class_config if host.use_generic_base_class else None


class RootModelTypeHost(Protocol):
    """The root model type of the output whose class body evaluates its root type."""

    @property
    def pydantic_v2_root_model_type(self) -> type[DataModel] | None:
        """The root model type whose class body evaluates its root type, if the output has one."""


class RootModelHost(Protocol):
    """The hook that tells whether a model uses the configured root model representation."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""


def site_display_name(host: RootModelHost, model: DataModel, field: DataModelFieldBase) -> str:
    """Return the name warnings and tag plans give a field's site: the class of a root model, else ``Class.field``."""
    return model.class_name if host._is_root_model(model) else class_field_key(model.class_name, field.name)  # noqa: SLF001


class CollapseRootModelsHost(Protocol):
    """The option that collapses root models into their users."""

    @property
    def collapse_root_models(self) -> bool:
        """Whether root models are collapsed into their users."""


class ReuseOptionsHost(CollapseRootModelsHost, Protocol):
    """The options of model reuse, with the option that collapses root models into their users."""

    @property
    def reuse_model(self) -> bool:
        """Whether models that render the same are reused."""

    @property
    def reuse_scope(self) -> ReuseScope | None:
        """The scope of reuse."""


class DeduplicationHost(CollapsedReuseHost, GenerationStoreHost, ReuseOptionsHost, RootModelHost, Protocol):
    """What deduplicating and reusing models reads and writes of the parser: the shapes, the reuse plan and options."""

    @property
    def _reuse_groups(self) -> ReuseGroups:
        """The plan of which model of a group of duplicates stays canonical."""

    @property
    def _reuse_optimization_context(self) -> _ReuseOptimizationContext:
        """The constraints that type overrides put on reuse."""

    @property
    def collapse_reuse_models(self) -> bool:
        """Whether reuse removes duplicates instead of subclassing them."""

    def _model_shapes(self, row: ShapeRow) -> ModelShapes[DataModel, Reference]:
        """Return the keys of one comparison pass of the dedup or reuse user of a row."""


class TargetPythonVersionHost(Protocol):
    """The Python version the output targets."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""


class ImportOptionsHost(TargetPythonVersionHost, Protocol):
    """The options that writing the imports of the modules reads: the import overrides and the target Python version."""

    @property
    def _import_overrides(self) -> dict[str, str] | None:
        """The modules ``--import-overrides`` routes imports through, by imported name."""


class ModuleNamesView(Protocol):
    """The internal modules of the run and the module name of each model, as naming a model in a module reads them."""

    @property
    def internal_modules(self) -> set[ModulePath]:
        """The internal modules that import cycles merged models into."""

    @property
    def model_path_to_module_name(self) -> Mapping[str, str]:
        """The module name of each model, by model path."""


class ModuleScopeView(ModuleNamesView, Protocol):
    """The internal modules of the run and the module name of each model."""

    @property
    @override
    def model_path_to_module_name(self) -> dict[str, str]:
        """The module name of each model, by model path."""


class InheritedFieldHost(Protocol):
    """The options, store and hooks that resolving inherited placeholder fields reads from the parser."""

    allof_merge_mode: AllOfMergeMode
    apply_default_values_for_required_fields: bool
    force_optional_for_required_fields: bool
    generation_store: GenerationStore
    model_resolver: ModelResolver

    def _copy_inherited_field(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        force_optional: bool,
        partial_merge_mode: AllOfMergeMode,
        reserved_names: set[str],
    ) -> DataModelFieldBase | None:
        """Copy a deferred inherited field, or return None when it is not deferred."""

    def _copy_model_field(
        self,
        field: DataModelFieldBase,
        *,
        data_type: DataType | None = None,
        register_references: bool = True,
        identity: FieldIdentity | None = None,
        **facts: Unpack[FieldCopyFacts],
    ) -> DataModelFieldBase:
        """Copy a field and its mutable state without copying model references, made with ``identity`` and ``facts``."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""


class RuntimeExpressionFlags(GraphBuilderHost, Protocol):
    """The shared imports and per-run flags that passes creating runtime expressions write on the parser."""

    imports: Imports
    _has_bound_python_types: bool
    _has_runtime_expressions: bool
    _import_ledger: ModuleImportLedger


def register_runtime_expression(host: RuntimeExpressionFlags) -> None:
    """Mark a parser-owned expression created after the initial import scan."""
    host._has_runtime_expressions = True  # noqa: SLF001


def field_name_model_type_of(data_model_type: type[DataModel]) -> ModelType:
    """Return the ModelType that field names of ``data_model_type`` are validated for.

    ModelType.PYDANTIC for Pydantic models (which have reserved attributes like 'schema', 'model_fields', etc.),
    ModelType.MSGSPEC for msgspec Structs (whose imported ``field`` must not be shadowed by a field named ``field``),
    and ModelType.CLASS for other model types (TypedDict, dataclass) which don't have such constraints.
    """
    return model_type if (model_type := data_model_type.FIELD_NAME_MODEL_TYPE) is not None else ModelType.CLASS


def replace_model_in_list(
    models: list[DataModel],
    original: DataModel,
    replacement: DataModel,
) -> None:
    """Replace model at its position in list."""
    # Use direct assignment instead of insert+remove for O(n) instead of O(2n)
    idx = models.index(original)
    models[idx] = replacement


def _distinct_bases(host: GraphBuilderHost, base_dedup: BaseDedup, model: DataModel) -> None:
    """Give each child of a merged model its bases once each, told apart by module and type hint or by reference."""
    for child in model.reference.iter_data_model_children():
        bases = child.base_classes
        host._graph_builder.set_base_classes(  # noqa: SLF001
            child,
            {f"{base.module_name}.{base.type_hint}": base for base in bases}.values()
            if base_dedup == "by-type-hint"
            else {id(base.reference): base for base in bases}.values(),
        )


def settle_python_bases(host: GenerationStoreHost, reference: Reference) -> None:
    """Give every model that inherits ``reference``, however distant, bases Python creates its class from.

    A merge changes what these models inherit, which may list a base twice or put a base before a model that inherits
    it; each model Python would refuse takes the bases of `python_base_classes`, its ancestors first.
    """
    index = host.generation_store.index
    descendants: dict[int, DataModel] = {}
    pending = index.models_inheriting(reference)
    while pending:
        if id(model := pending.pop()) not in descendants:
            descendants[id(model)] = model
            pending.extend(index.models_inheriting(model.reference))
    refused = [model for model in descendants.values() if python_base_classes(model) is not None]
    for model in reversed(sort_data_models_for_mro(refused)):
        if (bases := python_base_classes(model)) is not None:
            host._graph_builder.set_base_classes(model, bases)  # noqa: SLF001


def merge_duplicate(
    host: CollapsedReuseHost, duplicate: DataModel, canonical: DataModel, row: ShapeRow, *, keeps_duplicate_tags: bool
) -> list[DataType]:
    """Hand every user of a duplicate that leaves the output to the equal canonical model, and retire the duplicate.

    Every user, in any module, names the canonical model and records the null of the schema it named (``merged``),
    which it keeps under ``--keep-reference-null-on-merged-models``.
    With ``keeps_duplicate_tags``, for a duplicate that reuse removes instead of making it a subclass, a discriminated
    union that named the duplicate keeps mapping it to the tags of its own schema, which are compared with the tags of
    the canonical model. The children of the duplicate take their bases once each, told apart as the user of ``row``
    tells them (``ShapeRow.base_dedup``; None keeps them as redirected). The caller drops the duplicate from its
    module's models. The canonical model records the duplicate (``merged_models``). Return the redirected users.
    """
    builder = host._graph_builder  # noqa: SLF001
    host._merged_duplicates.setdefault(id(canonical), []).append(duplicate)  # noqa: SLF001
    users = builder.redirect_reference_users(
        duplicate.reference, canonical.reference, merged=True, keeps_named=host.keep_reference_null_on_merged_models
    )
    if keeps_duplicate_tags:
        collapsed_users = host._collapsed_reuse_users  # noqa: SLF001
        for user in users:
            collapsed_users.setdefault(id(user), (user, duplicate.reference))
        host._collapsed_reuse_models.setdefault(id(canonical), []).append(duplicate)  # noqa: SLF001
    if (base_dedup := row.base_dedup) is not None:
        _distinct_bases(host, base_dedup, duplicate)
    settle_python_bases(host, canonical.reference)
    builder.retire_model(duplicate)
    return users


def merged_models(host: MergedDuplicatesHost, model: DataModel) -> list[DataModel]:
    """Return the duplicates merged into ``model``, with those merged into each of them before."""
    merged: list[DataModel] = []
    pending = [model]
    while pending:
        merged.extend(found := host._merged_duplicates.get(id(pending.pop()), []))  # noqa: SLF001
        pending.extend(found)
    return merged


def normalize_default_value_constraints(
    host: RuntimeExpressionFlags,
    models: list[DataModel],
    *,
    data_type_manager: DataTypeManager,
    import_overrides: dict[str, str] | None,
) -> bool:
    """Keep backend-declared runtime constraint values structured until alias resolution."""
    from decimal import Decimal  # noqa: PLC0415

    normalized = False
    for model, _, data_type in iter_models_field_data_types(models):
        if (
            not model.SUPPORTS_DESERIALIZED_DEFAULT_VALUES
            or not data_type.kwargs
            or (resolved := resolve_default_value_descriptor(data_type_manager, data_type)) is None
        ):
            continue
        annotation_import, descriptor = resolved
        if not descriptor.normalize_constraints or has_import_override(
            import_overrides, annotation_import, descriptor.constructor_import
        ):
            continue
        if descriptor.recipe is not DefaultValueRecipe.Decimal:  # pragma: no cover - future backend recipe
            continue
        kwargs = data_type.kwargs
        for key, value in kwargs.items():
            if not isinstance(value, Decimal):
                continue
            if kwargs is data_type.kwargs:
                kwargs = kwargs.copy()
            kwargs[key] = _text_constructor_expression(descriptor.constructor_import, str(value))
        if kwargs is data_type.kwargs:
            continue
        host._graph_builder.set_type_expressions(  # noqa: SLF001
            data_type,
            kwargs=kwargs,
            runtime_expression_imports=(*data_type.runtime_expression_imports, descriptor.constructor_import),
        )
        register_runtime_expression(host)
        normalized = True
    return normalized


def resolve_default_value_descriptor(
    data_type_manager: DataTypeManager, data_type: DataType
) -> tuple[Import, DefaultValueDescriptor] | None:
    """Return a backend-declared scalar descriptor and its emitted annotation import."""
    if (scalar_data_type := _resolve_default_scalar_data_type(data_type)) is None:
        return None
    if (annotation_import := scalar_data_type.import_) is None:
        return None
    if (descriptor := data_type_manager.get_default_value_descriptor(scalar_data_type)) is None:
        return None
    return annotation_import, descriptor


def has_import_override(import_overrides: dict[str, str] | None, *imports: Import) -> bool:
    """Avoid changing generated defaults when an import policy redirects their bindings."""
    if not import_overrides:
        return False
    return any(
        _override_module(import_overrides, import_.from_, import_.import_) not in {None, import_.from_}
        for import_ in imports
    )


def inherited_default_source(
    merge_mode: AllOfMergeMode, field: DataModelFieldBase, inherited_field: DataModelFieldBase
) -> DataModelFieldBase:
    """Return the field whose default the override ``field`` of ``inherited_field`` takes.

    The inherited one where the merge mode inherits ``default`` (``parser/_intersection.py:inherits_keyword``) and
    ``field`` declares none.
    """
    if inherits_keyword(merge_mode.value, "default") and not declares_raw_schema_default(field):
        return inherited_field
    return field


def apply_inherited_field_default(
    field: DataModelFieldBase,
    inherited_field: DataModelFieldBase,
    *,
    scope: ClassScope,
    model_resolver: ModelResolver,
    apply_default_values_for_required_fields: bool,
) -> None:
    """Resolve an inherited schema default in the derived class scope."""
    if model_resolver.default_value_overrides and (raw := raw_schema_default(inherited_field)) is not None:
        field.default, field.has_default = model_resolver.resolve_default_value(
            field.original_name or field.name or "", *raw, class_name=scope.name, output_names=scope.output_names
        )
        match field.default:
            case dict() | list() | set():
                field.default = deepcopy(field.default)
    field.use_default_with_required = uses_default_with_required(
        required=field.required, has_default=field.has_default, use_default=apply_default_values_for_required_fields
    )


def placeholder_identity(placeholder: DataModelFieldBase) -> FieldIdentity:
    """Return the name, original name and aliases the copy that replaces ``placeholder`` takes (its own lists)."""
    return {
        "name": placeholder.name,
        "original_name": placeholder.original_name,
        "alias": placeholder.alias,
        "validation_aliases": (
            list(placeholder.validation_aliases) if placeholder.validation_aliases is not None else None
        ),
        "serialization_alias": placeholder.serialization_alias,
        "use_serialization_alias": placeholder.use_serialization_alias,
    }


def _inherited_placeholder_name(field: DataModelFieldBase) -> str | None:
    """Return the name of the inherited property that a placeholder field without a type of its own names, or None."""
    data_type = field.data_type
    if (
        data_type.data_types  # noqa: PLR0916
        or data_type.reference
        or data_type.type
        or data_type.literals
        or data_type.enum_member_literals
        or data_type.dict_key
    ):
        return None
    return field.original_name


def override_required_field(
    host: InheritedFieldHost,
    models: list[DataModel],
) -> None:
    """Replace the placeholder fields that name an inherited property by copies of the inherited fields."""
    pending_models = (
        model
        for model in models
        if not (isinstance(model, Enum) or host._is_root_model(model))  # noqa: SLF001
        and any(_inherited_placeholder_name(field) is not None for field in model.fields)
    )
    if (first_model := next(pending_models, None)) is None:
        return

    builder = host.generation_store.builder()
    builder.discard_derived_facts()
    for model in chain((first_model,), pending_models):
        resolved_fields: list[DataModelFieldBase] = []
        reserved_names = {field.name for field in model.fields if field.name}
        inherited_fields = get_inherited_fields(_find_base_classes(model))
        pending_fields = model.fields
        pending_fields.reverse()
        builder.set_fields(model, [])
        while pending_fields:
            model_field = pending_fields.pop()
            model_field.parent = None
            if (original_name := _inherited_placeholder_name(model_field)) is None:
                resolved_fields.append(model_field)
                continue

            _detach_deferred_inherited_field_parents(model_field)
            original_field = inherited_fields.get(original_name)
            if not original_field:
                continue
            if (
                copied_original_field := host._copy_inherited_field(  # noqa: SLF001
                    model_field,
                    original_field,
                    force_optional=host.force_optional_for_required_fields,
                    partial_merge_mode=host.allof_merge_mode,
                    reserved_names=reserved_names,
                )
            ) is None:
                copied_original_field = host._copy_model_field(  # noqa: SLF001
                    original_field, identity=placeholder_identity(model_field), required=True
                )
            default_source = original_field
            if not (scope := model_field.__dict__.get(_DEFERRED_INHERITED_FIELD_KEY)) and (
                scope := model_field.__dict__.get(_DEFERRED_INHERITED_CLASS_KEY)
            ):
                default_source = inherited_default_source(host.allof_merge_mode, model_field, original_field)
            if scope:
                apply_inherited_field_default(
                    copied_original_field,
                    default_source,
                    scope=scope,
                    model_resolver=host.model_resolver,
                    apply_default_values_for_required_fields=host.apply_default_values_for_required_fields,
                )
            elif host.apply_default_values_for_required_fields and copied_original_field.has_default:
                copied_original_field.use_default_with_required = True
            resolved_fields.append(copied_original_field)
        if model.REQUIRES_UNIQUE_FIELD_ALIASES:
            _restore_inherited_field_names_for_unique_aliases(builder, resolved_fields, inherited_fields)
        builder.set_fields(model, resolved_fields)


class ConstructorConflicts(NamedTuple):
    """The positional fields of a model's constructor that follow a positional default.

    ``own`` names the model's own fields that follow a constructor or signature default. ``inherited`` holds the
    inherited fields, with the model that declares each, that follow a constructor default without one: ``dataclasses``
    rejects the class, and no base sees the conflict, as only the model joins the bases that set it up.
    """

    own: frozenset[str]
    inherited: tuple[tuple[DataModelFieldBase, DataModel], ...]


def takes_positional_place(facts: AssignmentFacts, field: DataModelFieldBase, declaring_model: DataModel) -> bool:
    """Return whether ``field`` takes a positional place in the constructor of a model that has it.

    It does when it takes part in the constructor and neither the field nor ``declaring_model``, the class that
    declares it, makes it keyword-only.
    """
    kw_only = field.constructor_keyword_only
    return (
        facts.participates_in_constructor
        and kw_only is not True
        and (kw_only is not None or not declaring_model.has_keyword_only_definition())
    )


def get_constructor_ordering_conflicts(
    model: DataModel,
    effective_fields: Mapping[str, tuple[DataModelFieldBase, DataModel, bool | None]],
    required_assignment_names: set[str],
) -> ConstructorConflicts:
    """Return the positional fields of the model's effective constructor that follow a positional default.

    A field of the model's own follows one as the class body writes the defaults, which the class rejects, or as
    each field takes its place (``AssignmentFacts.has_place_default``), which keeps 0.86.0's keyword-only fields; an
    inherited field, which only the copies fix, follows one as they are written.
    """
    seen_constructor_default = seen_signature_default = seen_place_default = False
    ordering_conflicts: set[str] = set()
    inherited_conflicts: list[tuple[DataModelFieldBase, DataModel]] = []
    for field_name, (field, declaring_model, inherited_has_default) in effective_fields.items():
        if not takes_positional_place(facts := model._field_assignment_facts(field), field, declaring_model):  # noqa: SLF001
            continue
        required_assignment_is_constructor_default = (
            model.REQUIRED_ASSIGNMENT_COUNTS_AS_CONSTRUCTOR_DEFAULT
            and requires_value(field)
            and (field_name in required_assignment_names or facts.assigned)
        )
        field_has_signature_default = (
            False
            if required_assignment_is_constructor_default
            else (facts.has_default if inherited_has_default is None else inherited_has_default)
        )
        field_has_constructor_default = required_assignment_is_constructor_default or field_has_signature_default
        field_has_place_default = required_assignment_is_constructor_default or (
            field_has_signature_default and facts.has_place_default
        )
        if declaring_model is model:
            follows_written_default = (seen_constructor_default and not field_has_constructor_default) or (
                seen_signature_default and not field_has_signature_default
            )
            if follows_written_default or (seen_place_default and not field_has_place_default):
                ordering_conflicts.add(field_name)
        elif seen_constructor_default and not field_has_constructor_default:
            inherited_conflicts.append((field, declaring_model))
        seen_constructor_default = seen_constructor_default or field_has_constructor_default
        seen_signature_default = seen_signature_default or field_has_signature_default
        seen_place_default = seen_place_default or field_has_place_default
    return ConstructorConflicts(frozenset(ordering_conflicts), tuple(inherited_conflicts))


def _is_pydantic_v2_root_model(model: DataModel, root_model_type: type[DataModel] | None) -> bool:
    """Return whether the model's fields need dependency ordering: the root type requires it and so does its class.

    A model the configured type keeps as its base class, such as a ``RootModel`` that ``--use-root-model-type-alias``
    keeps for a constrained root, is ordered like the configured type.
    """
    return root_model_type is not None and (
        isinstance(model, root_model_type) or model.REQUIRES_FIELD_DEPENDENCY_ORDERING
    )


DefinitionReadsRow: TypeAlias = Literal[
    "definition",
    "legacy-class-body-without-alias-values",
    "legacy-keep-order-type-statement",
    "legacy-evaluated-without-root-model",
]
"""Which fields of a model ``definition_reads`` reads, for each pass that orders or checks what a module evaluates.

``definition`` reads those that the statement defining the model evaluates, for the forward references the module
quotes (``render_prep.py:update_type_aliases``). The legacy rows keep what their passes read before:

- ``legacy-class-body-without-alias-values``: only a root model's fields, for the order of the models whose class body
  evaluates others (``_sort_models_by_class_body_references``, ``settle.py:sort_data_models``); a type alias's value,
  the functional TypedDict and evaluated annotations are left out.
- ``legacy-keep-order-type-statement``: ``--keep-model-order`` (``modules.py:_keep_model_order_deps``) also reads the
  value of a ``type`` statement, and the functional TypedDict as a class.
- ``legacy-evaluated-without-root-model``: the check of the root models that name themselves
  (``imports.py:reject_alias_cycles_without_schema``) reads a root model as a class, and the own fields of the
  functional TypedDict when the module evaluates annotations.
"""


def definition_reads(
    model: DataModel,
    *,
    row: DefinitionReadsRow = "definition",
    root_model_type: type[DataModel] | None = None,
    evaluates_annotations: bool = False,
) -> Sequence[DataModelFieldBase] | None:
    """Return the fields whose types the statement that defines ``model`` evaluates as the module runs, or None.

    A ``type`` statement defers its value. The functional TypedDict evaluates the fields of its dict literal, the
    inherited ones included (``fields_evaluated_at_definition``); any other type alias evaluates its value, a root
    model of ``root_model_type`` its root type (``_is_pydantic_v2_root_model``), and a class its annotations when
    ``evaluates_annotations`` tells that the module does not defer them. A read of the model's own fields returns its
    ``fields`` list itself. ``row`` keeps what a pass read before (``DefinitionReadsRow``).
    """
    if row == "legacy-class-body-without-alias-values":
        return model.fields if _is_pydantic_v2_root_model(model, root_model_type) else None
    reads_own_fields = evaluates_annotations or isinstance(model, TypeAliasBase)
    if row == "legacy-keep-order-type-statement":
        return model.fields if reads_own_fields or _is_pydantic_v2_root_model(model, root_model_type) else None
    if isinstance(model, TypeStatement):
        return None
    if row == "legacy-evaluated-without-root-model":
        return model.fields if reads_own_fields else model.fields_evaluated_at_definition() or None
    if evaluated := model.fields_evaluated_at_definition():
        return evaluated
    return model.fields if reads_own_fields or _is_pydantic_v2_root_model(model, root_model_type) else None


@record
class _ReuseOptimizationContext:
    """Parser-owned semantic constraints for model reuse optimizations."""

    type_override_model_names: frozenset[str] = frozenset()
    base_stand_in_keys: frozenset[ModelKey] = frozenset()
    """The model keys of the stand-ins, which are never written and take part in no reuse."""

    @classmethod
    def from_type_overrides(cls, type_overrides: Mapping[str, str]) -> _ReuseOptimizationContext:
        """Build reuse constraints without importing output-backend policy."""
        return cls(type_override_classes(type_overrides))

    @property
    def constrains(self) -> bool:
        """Return whether any model is kept out of reuse."""
        return bool(self.type_override_model_names or self.base_stand_in_keys)

    def allows_model(self, model: DataModel) -> bool:
        """Return whether reuse can preserve all pending parser semantics."""
        return (
            model.class_name not in self.type_override_model_names
            and model_key_of(model.reference) not in self.base_stand_in_keys
        )

    def eligible_models(self, models: Iterable[DataModel]) -> Iterable[DataModel]:
        """Return models eligible for reuse without allocating on the fast path."""
        if not self.constrains:
            return models
        return (model for model in models if self.allows_model(model))


def compared_models(host: DeduplicationHost, row: ShapeRow, models: Iterable[DataModel]) -> Iterable[DataModel]:
    """Return the models that the deduplication or reuse user of a row compares, in their order.

    Reuse leaves the models of type overrides and the stand-ins out (``_ReuseOptimizationContext``), and the row
    leaves the root models out (``skips_root_model``). The filters run as the result is read, so a caller that changes
    ``models`` meanwhile passes a copy.
    """
    candidates = host._reuse_optimization_context.eligible_models(models)  # noqa: SLF001
    if skips_root_model(row, collapse_root_models=host.collapse_root_models):
        return (model for model in candidates if not host._is_root_model(model))  # noqa: SLF001
    return candidates


def _sort_models_by_class_body_references(
    models: list[DataModel],
    pydantic_v2_root_model_type: type[DataModel] | None,
    runtime_validation_references: Mapping[ModelKey, frozenset[ModelKey]] | None = None,
) -> list[DataModel]:
    """Define first the models a class body evaluates: base classes, root types and runtime rule types.

    The root types are those of a root model (``definition_reads``, row ``legacy-class-body-without-alias-values``).
    The rule types are by default those the rules name in any module (``_runtime_validation_reference_keys``).
    """
    if runtime_validation_references is None:
        runtime_validation_references = _runtime_validation_reference_keys(models, same_module=False)
    keyed = keyed_models(models, GraphSnapshot.CLASS_BODY_ORDER.value)
    order_index = {key: index for index, key in enumerate(keyed.models)}
    targets: dict[ModelKey, set[ModelKey]] = {}
    for model_key, model in keyed.models.items():
        model_targets = targets[model_key] = {
            keyed.key_of(base_class.reference) for base_class in model.base_classes if base_class.reference
        }
        model_targets.update(runtime_validation_references.get(model_key, ()))
        model_targets.update(
            keyed.key_of(data_type.reference)
            for field in definition_reads(
                model, row="legacy-class-body-without-alias-values", root_model_type=pydantic_v2_root_model_type
            )
            or ()
            for data_type in field.data_type.all_data_types
            if data_type.reference
        )
        model_targets.discard(model_key)
    graph = reference_graph(GraphSnapshot.CLASS_BODY_ORDER, keyed.models, {EdgeKind.CLASS_BODY: targets})
    sorted_keys = graph.dependency_order((EdgeKind.CLASS_BODY,), list(order_index), order_index.__getitem__)
    return [keyed.models[key] for key in sorted_keys]


def _runtime_validation_reference_keys(
    models: Sequence[DataModel], *, same_module: bool
) -> dict[ModelKey, frozenset[ModelKey]]:
    """Map models to the listed models their runtime rules name; with ``same_module``, other modules are imported."""
    references_by_key: dict[ModelKey, frozenset[ModelKey]] = {}
    keyed: KeyedModels | None = None
    for model in models:
        if not (references := model.schema_runtime_validation_references):
            continue
        keyed = keyed or keyed_models(models, "runtime validation references")
        module_path = model.module_path if same_module else None
        if reference_keys := frozenset(
            reference_key
            for reference in references
            if (reference_key := keyed.key_of(reference)) in keyed.models
            and (
                module_path is None
                or (isinstance(source := reference.source, DataModel) and source.module_path == module_path)
            )
        ):
            references_by_key[model_key_of(model.reference)] = reference_keys
    return references_by_key


def _rule_data_types(model: DataModel, *, unwritable: bool = False) -> Iterator[DataType]:
    """Yield the data types of a model's schema validator rules, nested ones included.

    With ``unwritable``, a type alias yields them too until the parser writes it as a class that writes them.
    """
    if rules := model.schema_runtime_validation or (unwritable and model._unwritable_runtime_rules):  # noqa: SLF001
        for rule_data_type in rules.data_types:
            yield from rule_data_type.all_data_types


def _data_types_beside_fields(model: DataModel) -> Iterator[DataType]:
    """Yield the data types a model writes beside its fields and bases.

    Those are the types of its schema validator rules and of its backend settings, such as TypedDict ``extra_items``.
    """
    yield from _rule_data_types(model)
    yield from model._setting_data_types()  # ruff: ignore[private-member-access]


def _named_data_types(model: DataModel) -> Iterator[DataType]:
    """Yield the data types a model writes: those of its fields and bases, then those beside them.

    The class writes the types of its rules and backend settings as it writes its field types, so a model they name in
    another module is imported the same way.
    """
    yield from model.all_data_types
    yield from _data_types_beside_fields(model)


def add_model_path_to_list(
    paths: list[str] | None,
    model: DataModel,
    /,
) -> list[str]:
    """Add the model's path to the paths, a new list for None, unless the model is a type alias or listed."""
    if paths is None:
        paths = []
    if model.is_alias:
        return paths
    if (path := model.path) in paths:
        return paths
    paths.append(path)
    return paths


def _get_schema_path(model: DataModel | Reference) -> str:
    """Return the path of the schema a model is generated from, through read and write variants and reuse.

    It is the path the model had before the merge of an import cycle moved it (``schema_path_of``).
    """
    if isinstance(model, DataModel):
        source, reference = model, model.reference
    else:
        source, reference = model.source, model
    reused = source.reused_reference if isinstance(source, DataModel) else None
    return _source_path(schema_path_of(reused or reference))


def _get_mapping_discriminator_values(discriminator_model: DataModel, mapping: dict[str, str]) -> list[str]:
    """Return the mapping values that select a variant directly or through its base classes (``mapped_tags``)."""
    if not mapping:
        return []
    return mapped_tags(
        mapping,
        schema_path=_get_schema_path(discriminator_model),
        base_schema_paths=(
            _get_schema_path(reference)
            for base_class in discriminator_model.base_classes
            if (reference := base_class.reference)
        ),
    )


class RecursionFactsHost(Protocol):
    """The recursion facts of the run, which the passes that add models needing a reference update action extend."""

    _recursion_facts: RecursionFacts


def require_update_action(
    host: RecursionFactsHost, models: Sequence[DataModel], require_update_action_models: list[str] | None = None
) -> None:
    """Add the models that are not type aliases to the models needing a reference update action.

    ``require_update_action_models`` is the list of their paths that ``_process_single_module`` takes.
    """
    recursion_facts = host._recursion_facts  # noqa: SLF001
    for model in models:
        add_model_path_to_list(require_update_action_models, model)
    host._recursion_facts = RecursionFacts(  # noqa: SLF001
        require_update=recursion_facts["require_update"].union(
            model_key_of(model.reference) for model in models if not model.is_alias
        ),
        circular_roots=recursion_facts["circular_roots"],
    )


class ReuseSubclassHost(
    GenerationStoreHost, MergedReferenceNullHost, MergedDuplicatesHost, RecursionFactsHost, Protocol
):
    """The builder that replaces a reuse duplicate by a subclass, and the recursion facts the subclass may join."""


def removes_reuse_duplicate(host: DeduplicationHost, duplicate: DataModel, *, inherits: bool) -> bool:
    """Return whether reuse removes a duplicate instead of making it an empty subclass of the canonical model.

    An Enum is removed, every duplicate is under ``--collapse-reuse-models``, and so is every duplicate where the
    output's models cannot inherit the canonical model (``inherits``: tree-scope reuse reads the output's capability).
    """
    return isinstance(duplicate, Enum) or not inherits or host.collapse_reuse_models


def subclass_duplicate(
    host: ReuseSubclassHost, duplicate: DataModel, canonical: DataModel, models: list[DataModel], users: list[DataModel]
) -> DataModel | None:
    """Replace a duplicate in ``models`` and in the store by an empty subclass of the equal canonical model.

    The subclass keeps the duplicate's name; the users that ``users`` own name it and record the null of the schema
    they named, which they keep under ``--keep-reference-null-on-merged-models``, and the canonical model records the
    duplicate (``merged_models``). Return the subclass when the canonical model needs a reference update action, which
    the caller adds once with ``require_update_action``.
    """
    builder = host._graph_builder  # noqa: SLF001
    host._merged_duplicates.setdefault(id(canonical), []).append(duplicate)  # noqa: SLF001
    inherited = duplicate.create_reuse_model(canonical.reference)
    builder.redirect_model_reference_users(
        duplicate, users, inherited.reference, keeps_named=host.keep_reference_null_on_merged_models
    )
    replace_model_in_list(models, duplicate, inherited)
    builder.replace_model(duplicate, inherited)
    settle_python_bases(host, inherited.reference)
    return inherited if model_key_of(canonical.reference) in host._recursion_facts["require_update"] else None  # noqa: SLF001


DiscriminatorValue: TypeAlias = str | int | bool


def _module_name_of(model_path_to_module_name: Mapping[str, str], reference: Reference) -> str | None:
    """Return the module name scope planning gives the model ``reference`` names, or None."""
    if isinstance(model_path_to_module_name, _ModuleNames):
        return model_path_to_module_name.of(reference)
    return model_path_to_module_name.get(reference.path)


@final
@record
class _FieldTagFacts:
    """The facts of a field that ``field_tag`` reads: its ``const``, its ``Literal`` and the models its type names.

    The models are the Enum and the root model or type alias its type names.
    """

    field: DataModelFieldBase
    read: frozenset[int] = frozenset()
    """The root models whose fields the facts are read through, so a cycle of root models gives no tag."""

    def has_const(self) -> bool:
        """Return whether the field has a ``const`` other than None."""
        return self.field.extras.get("const") is not None

    def const(self) -> Any:
        """Return the field's ``const``."""
        return self.field.extras.get("const")

    def literals(self) -> list[DiscriminatorValue]:
        """Return the values of the ``Literal`` the field's type is."""
        return self.field.data_type.literals

    def enum_member(self) -> tuple[int, Any]:
        """Return how many members the Enum the field's type names has, and the first one's value.

        A union that wrote the field as ``Literal[<Enum>.<member>]`` detached its reference to the Enum, which is then
        found through the members, so a later union reads the tag the first one read.
        """
        fields = enum_source.fields if (enum_source := _annotated_enum(self.field.data_type)) else []
        return (len(fields), get_raw_enum_member_value(fields[0].default)) if fields else (0, None)

    def referenced_tag(self) -> FieldTag | None:
        """Return the tag of the root model or type alias the field's type names, as ``variant-field`` reads its field.

        A one-value enum the field references is such a model under ``--enum-field-as-literal``.
        """
        data_type = self.field.data_type
        if data_type.data_types or (reference := data_type.reference) is None:
            return None
        source = reference.source
        if (
            not isinstance(source, DataModel)
            or not _is_discriminator_wrapper(source)
            or len(source.fields) != 1
            or id(source) in self.read
        ):
            return None
        tag = field_tag("variant-field", _FieldTagFacts(source.fields[0], self.read | {id(source)}))
        return None if tag is None else FieldTag(tag.value, "referenced-schema")


def _is_discriminator_wrapper(model: DataModel) -> bool:
    return model.IS_ALIAS or model.IS_ROOT_MODEL


def _iter_discriminator_data_types(
    data_types: Iterable[DataType],
    active_union_models: set[int] | None = None,
    *,
    can_update_discriminator: bool = True,
    discriminator_owner: int | None = None,
    wrappers: list[DataModel] | None = None,
) -> Iterator[tuple[DataType, bool, int]]:
    for data_type in data_types:
        if members := union_members(data_type):
            yield from _iter_discriminator_data_types(
                members,
                active_union_models,
                can_update_discriminator=False,
                discriminator_owner=discriminator_owner,
                wrappers=wrappers,
            )
        else:
            source = data_type.reference.source if data_type.reference else None
            owner = discriminator_owner or (id(source) if source is not None else id(data_type))
            if not isinstance(source, DataModel) or not _is_discriminator_wrapper(source):
                yield data_type, can_update_discriminator, owner
            else:
                source_id = id(source)
                if active_union_models is None:
                    active_union_models = set()
                if source_id in active_union_models or not source.fields:
                    yield data_type, can_update_discriminator, owner
                else:
                    active_union_models.add(source_id)
                    if wrappers is not None:
                        wrappers.append(source)
                    try:
                        yield from _iter_discriminator_data_types(
                            (source.fields[0].data_type,),
                            active_union_models,
                            can_update_discriminator=False,
                            discriminator_owner=owner,
                            wrappers=wrappers,
                        )
                    finally:
                        active_union_models.remove(source_id)


def _get_model_module_name(model: DataModel, model_path_to_module_name: Mapping[str, str]) -> str:
    """Return the module name scope planning gives a model, or the model's own module name."""
    module_name = _module_name_of(model_path_to_module_name, model.reference)
    return model.module_name if module_name is None else module_name


def _get_data_type_target_full_name(
    data_type: DataType,
    reference: Reference,
    model_path_to_module_name: Mapping[str, str],
) -> str:
    if (ref_module_name := _module_name_of(model_path_to_module_name, reference)) is None:
        return data_type.full_name
    return dotted_name(ref_module_name, reference.short_name)


def _context_models(contexts: Iterable[ModuleContextView]) -> list[DataModel]:
    return [model for ctx in contexts for model in ctx.models]


class ReplacedRootsHost(Protocol):
    """The roots that collapse replaced by their inner models, which leave the modules that held them."""

    @property
    def _replaced_root_models(self) -> dict[int, tuple[DataModel, DataModel]]:
        """The replaced roots by id, with the root and the inner model that replaces it."""


def module_members(host: ReplacedRootsHost, models: list[DataModel]) -> list[DataModel]:
    """Return the models of a module that stay in it: a root replaced by its inner model leaves at the decision."""
    if not (replaced_root_models := host._replaced_root_models):  # noqa: SLF001
        return models
    return [model for model in models if id(model) not in replaced_root_models]


def names_left_by_replaced_roots(host: ReplacedRootsHost, models: list[DataModel], members: KeyedModels) -> set[str]:
    """Return the class names that roots replaced by their inner models leave free in a module.

    A root keeps its name for the inner model when the inner model, which takes the root's path, is in the same
    module.
    """
    replaced_root_models = host._replaced_root_models  # noqa: SLF001
    return {
        model.class_name
        for model in models
        if (replacement := replaced_root_models.get(id(model))) is not None
        and model_key_of(replacement[1].reference) not in members.models
    }


class UniqueItemsHolder(Protocol):
    """What a uniqueItems plan reads of a field: its type, the constraints of its own schema and its default."""

    @property
    def data_type(self) -> DataType:
        """The type whose lists the plan writes as sets."""

    @property
    def constraints(self) -> Any:
        """The constraints of the holder's own schema, whose ``uniqueItems`` declares the arrays of its own level."""

    @property
    def default(self) -> Any:
        """The default, which becomes a set where a list of the holder's own level becomes one."""


@final
@record(eq=False, slots=False)
class AdditionalPropertiesHolder:
    """The type of a model's additional properties (TypedDict ``extra_items``) as a uniqueItems plan reads a field.

    It is the field built from the additionalProperties schema: its own schema declares no keyword and it takes no
    default, so the arrays its nodes record ``uniqueItems`` on are those of nested schemas, decided as the values of a
    map are (``sources="merged"``). It keeps a ``__dict__``, as a field does, for the records the plans read there.
    """

    data_type: DataType
    constraints: None = None
    default: None = None


def additional_properties_holder(model: Any) -> AdditionalPropertiesHolder | None:
    """Return the type of the additional properties that the settings of ``model`` write, as a holder, or None."""
    return None if (data_type := model.additional_properties_type) is None else AdditionalPropertiesHolder(data_type)


def _declares_unique_items(field: UniqueItemsHolder) -> bool:
    """Return whether a field declares ``uniqueItems: true``."""
    match field.constraints:
        case ConstraintsBase(unique_items=True) | {"uniqueItems": True}:
            return True
    return False


@final
@record(eq=False)
class UniqueItemsPlan:
    """The arrays of a field's type that --use-unique-items-as-set writes as sets, and the default the field takes."""

    sets: tuple[DataType, ...] = ()
    """The lists that become sets."""
    default: set[Any] | None = None
    """The field's list default as a set, where a list of the field's own level becomes one; None keeps the default."""
    tuples: tuple[DataType, ...] = ()
    """The tuples that a uniqueItems array is written as under --use-tuple-for-closed-arrays, which would lose their
    positions as a set: the lists beside them stay lists."""


_NO_UNIQUE_ITEMS: Final = UniqueItemsPlan()

UniqueItemsSources: TypeAlias = Literal["field", "merged", "all"]
"""Which schemas declaring ``uniqueItems`` a plan reads: ``field`` the field's own schema, but that of a type list,
which every release since 0.52.0 decides before reuse; ``merged`` the others, the type list's (0.89.0) and those of the
nodes that record it, decided once deduplication and reuse merged models, with the lists of the field's own schema that
``field`` kept, which the sets of the others may let hold their items; ``all`` both."""
_Path: TypeAlias = tuple[int, ...]


def unique_items_plan(
    field: UniqueItemsHolder,
    *,
    keeps_tuples: bool,
    sources: UniqueItemsSources = "all",
    merged: Sequence[UniqueItemsHolder | None] = (),
) -> UniqueItemsPlan:
    """Return the arrays of a field's type that --use-unique-items-as-set writes as sets.

    A schema that declares ``uniqueItems`` declares it for the arrays of the type built from it, the type itself or the
    members of its union (``_unique_items_arrays``): the field's own schema for the field's type, and the schema of
    each node that records it (``reference.unique_items_of``), such as a member of an anyOf, the items of an array or
    the values of a map; ``sources`` picks among them. A list among them becomes a set where some value its items take
    hashes, read after the lists those items hold (``set_items_can_hash``), and, at the field's own level, where the
    field's list default can be a set. Under --use-tuple-for-closed-arrays (``keeps_tuples``), a tuple among the arrays
    of a schema keeps all of them as they are. ``merged`` are the fields of the same name of the models merged into the
    field's model (None for a model without one): a list of ``sources="merged"`` counts only where each of them declares
    ``uniqueItems`` at the same position of its type too, so a merge never makes a set of the array of a model that
    does not declare it. A list but those of the field's own schema (``_own_unique_arrays``) stays a list in a union
    beside another member of its kind that keeps its lists (``_beside_its_kind``), which the set would split from a
    member written alike, decided again until no such set is left. A model item is read as a value that hashes,
    whatever its class: collapse may still inline the model it names, and ``unhashable_unique_sets`` reads the class
    once it has.
    """
    return _unique_items_plan(field, _PlanReading(keeps_tuples, sources, None, reads_classes=False), merged)


@final
@record(eq=False)
class _PlanReading:
    """How a plan reads a field's type: the options of ``unique_items_plan`` and the type aliases it is read inside."""

    keeps_tuples: bool
    sources: UniqueItemsSources
    generic_base_config: Mapping[str, Any] | None
    reads_classes: bool = True
    """Whether a model item takes no hashable value where its class never hashes its instances."""
    aliases: frozenset[int] = frozenset()
    """The type aliases, by id, whose values the plan is read in."""


def _unique_items_plan(
    field: UniqueItemsHolder, reading: _PlanReading, merged: Sequence[UniqueItemsHolder | None]
) -> UniqueItemsPlan:
    """Return ``unique_items_plan`` of a field as ``reading`` reads it."""
    if not (arrays := _declared_unique_arrays(field, reading.sources)):
        return _NO_UNIQUE_ITEMS
    if merged:
        declared = [{path for path, _ in _declared_unique_arrays(other, "all")} if other else set() for other in merged]
        arrays = [(path, group) for path, group in arrays if all(path in paths for paths in declared)]
    return plan_unique_sets(
        field,
        [group for _, group in arrays],
        default=default if isinstance(default := field.default, list) else None,
        keeps_tuples=reading.keeps_tuples,
        can_hold=lambda node, sets: _items_can_hash(node, sets, reading),
    )


def unhashable_unique_sets(
    field: UniqueItemsHolder,
    *,
    keeps_tuples: bool,
    generic_base_config: Mapping[str, Any] | None,
    lists: Collection[_Path] = (),
) -> list[_Path]:
    """Return the paths of the sets --use-unique-items-as-set wrote in a field's type that turn back into lists.

    ``unique_items_plan`` decides them before collapse, which may still inline the model their items name, so it reads
    a model item as a value that hashes. Once collapse has, a set (``is_unique_items_set``) is a list again where no
    value of its items hashes as the plan reads them, now with the class of each model they name: a model whose class
    never hashes its instances takes none (``DataModel._instances_never_hash``). So is a set but those of the field's
    own schema that sits beside a member of its kind that keeps its lists, as the plan reads it (``_beside_its_kind``),
    where that member holds one of ``lists``, the paths of the sets that turned back into lists before: the plan would
    have kept it a list beside them. The sets are read as they are written, so a set that holds one of them as a frozen
    set, or sits beside it, is asked for again once it is a list. ``generic_base_config`` is the configuration of the
    generic base class that the output's models inherit from (--use-generic-base-class), which a model item reads.
    """
    if not any(map(is_unique_items_set, field.data_type.all_data_types)):
        return []
    reading = _PlanReading(keeps_tuples, "all", generic_base_config)
    own = _own_unique_arrays(field, sets=True)
    nodes = dict(_nodes_with_paths(field.data_type, ()))
    return [
        path
        for path, node in nodes.items()
        if is_unique_items_set(node)
        and (
            not _items_can_hash(node, (), reading) or (id(node) not in own and _beside_its_kind(path, nodes, (), lists))
        )
    ]


def holds_own_unique_items_set(field: DataModelFieldBase) -> bool:
    """Return whether a set --use-unique-items-as-set wrote is the field's type or a member of its union."""
    return any(map(is_unique_items_set, _unique_items_arrays(field.data_type, sets=True)))


def _items_can_hash(node: DataType, sets: Collection[int], reading: _PlanReading) -> bool:
    """Return whether some value of the items of ``node`` hashes once the lists of ``sets`` (by id) are sets."""
    return set_items_can_hash(tuple(_set_item_facts(item, sets, reading) for item in node.data_types))


def plan_unique_sets(
    field: UniqueItemsHolder,
    arrays: Iterable[tuple[DataType, ...]],
    *,
    default: Sequence[Any] | None,
    keeps_tuples: bool,
    can_hold: Callable[[DataType, Collection[int]], bool],
) -> UniqueItemsPlan:
    """Return the plan of a field whose schemas declare ``arrays`` unique, each group the arrays of one schema.

    The lists of a group become sets, inner lists first, where ``can_hold`` says a set can hold the items of the list
    once the lists of ``sets`` (by id) are sets; under ``keeps_tuples`` a tuple among a group keeps all of it. A list
    but those of the field's own schema (``_own_unique_arrays``) stays a list in a union beside another member of its
    kind that keeps its lists (``_beside_its_kind``), decided again until no such set is left. The field's array
    ``default`` becomes the set of its values where a list of the field's own level becomes one and they hash;
    otherwise those lists stay. A list inside the field's type stays a list where the field's default holds an array
    (``_arrays_a_default_holds``), which the default would hold as a list where the type promises a set.
    """
    lists: dict[int, DataType] = {}
    tuples: dict[int, DataType] = {}
    for group in arrays:
        if keeps_tuples and any(array.is_tuple for array in group):
            tuples.update((id(array), array) for array in group if array.is_tuple)
        else:
            lists.update((id(array), array) for array in group if array.is_list)
    own = _own_unique_arrays(field)
    nodes = dict(_nodes_with_paths(field.data_type, ()))
    own_level = {*map(id, _unique_items_arrays(field.data_type))}
    kept = {
        path for path in _arrays_a_default_holds(field.data_type, field.default, ()) if id(nodes[path]) not in own_level
    }
    while True:
        sets: dict[int, DataType] = {}
        for path, node in nodes.items():
            if id(node) in lists and path not in kept and can_hold(node, sets):
                sets[id(node)] = node
        if not (
            beside := {
                path
                for path, node in nodes.items()
                if id(node) in sets and id(node) not in own and _beside_its_kind(path, nodes, sets)
            }
        ):
            break
        kept |= beside
    default_set = None
    if (own_level := [array for array in _unique_items_arrays(field.data_type) if id(array) in sets]) and (
        default is not None
    ):
        try:
            default_set = set(default)
        except TypeError:
            for array in own_level:
                del sets[id(array)]
    return UniqueItemsPlan(tuple(sets.values()), default_set, tuple(tuples.values()))


def _arrays_a_default_holds(data_type: DataType, value: Any, path: _Path) -> Iterator[_Path]:
    """Yield the paths of the array-like nodes of a type tree, at ``path``, where a default ``value`` holds an array.

    The items of an array and the values of a map are read in every node the container holds, and a union in each of
    its members, so every node that may take an array of the default is found.
    """
    if isinstance(value, (list, tuple)) and data_type in ARRAYS:
        yield path
        items: Iterable[Any] = value
    elif isinstance(value, dict) and data_type in MAPPINGS:
        items = value.values()
    elif data_type not in CONTAINERS:
        items = (value,)
    else:
        return
    for item in items:
        for index, nested in enumerate(data_type.data_types):
            yield from _arrays_a_default_holds(nested, item, (*path, index))


def _own_unique_arrays(field: UniqueItemsHolder, *, sets: bool = False) -> set[int]:
    """Return, by id, the arrays that the field's own schema declares unique, but those of a type list.

    They are decided as every release since 0.52.0 decides them, so ``_beside_its_kind`` does not keep them lists.
    With ``sets``, the sets --use-unique-items-as-set wrote from them are among them (``_unique_items_arrays``).
    """
    return {id(array) for _, group in _declared_unique_arrays(field, "field", sets=sets) for array in group}


def _beside_its_kind(
    path: _Path, nodes: Mapping[_Path, DataType], sets: Collection[int], lists: Collection[_Path] | None = None
) -> bool:
    """Return whether the list at ``path``, as a set, sits in a union beside a member of its kind that keeps its lists.

    ``nodes`` are the nodes of the field's type by their paths, ``sets`` the lists that become sets by id. The member
    of the union is the list, or the node that holds it as its only member (``list[int] | list[str]``,
    ``dict[str, list[int]] | dict[str, list[int]]``, the values of a map of two patterns); the other member is of its
    kind where both are array-like or both are maps, and keeps its lists where it holds no set, one it is or one of
    ``sets``. A msgspec union holds one member of each kind, so such a union either decodes because the output writes
    its members alike as one, which the set would split, or never decodes; the list keeps the output either way.
    With ``lists``, the paths of the sets that turned back into lists, the other member counts only where it holds one.
    """
    while path and len(nodes[path[:-1]].data_types) == 1:
        path = path[:-1]
    if not path or (holder := nodes[path[:-1]]).is_tuple or (kind := _union_kind(member := nodes[path])) is None:
        return False
    return any(
        other is not member
        and _union_kind(other) == kind
        and not any(id(nested) in sets or nested in SETS for _, nested in _nodes_with_paths(other, ()))
        and (lists is None or any(at in lists for at, _ in _nodes_with_paths(other, (*path[:-1], index))))
        for index, other in enumerate(holder.data_types)
    )


def names_alias_beside_arrays(sites: Iterable[tuple[DataType, DataType]]) -> bool:
    """Return whether every site of a type alias names it in a union beside an array-like member that keeps its lists.

    ``sites`` are, for each place that names the alias, the type of its field and the node of that type that names
    it. The member of the union is the node, or the node that holds it as its only member; the other member is
    array-like (``_union_kind``) and holds no set nor a node whose schema declares ``uniqueItems``. A msgspec union
    holds one array-like member, so such a union decodes only where the alias's value is a list written like the
    other member, which a set of the alias would split. An alias without sites keeps its sets.
    """
    found = False
    for root, node in sites:
        nodes = dict(_nodes_with_paths(root, ()))
        path = next((at for at, nested in nodes.items() if nested is node), ())
        while path and len(nodes[path[:-1]].data_types) == 1:
            path = path[:-1]
        if (
            not path
            or (holder := nodes[path[:-1]]).is_tuple
            or not any(
                other is not nodes[path]
                and _union_kind(other) == "array"
                and not any(nested in SETS or unique_items_of(nested) for _, nested in _nodes_with_paths(other, ()))
                for other in holder.data_types
            )
        ):
            return False
        found = True
    return found


def without_own_unique_sets(field: DataModelFieldBase, plan: UniqueItemsPlan) -> UniqueItemsPlan:
    """Return ``plan`` without the sets of the field's own level, whose list default stays the list."""
    own = {*map(id, _unique_items_arrays(field.data_type))}
    return UniqueItemsPlan(tuple(array for array in plan.sets if id(array) not in own), None, plan.tuples)


def _union_kind(data_type: DataType) -> Literal["array", "map"] | None:
    """Return whether a union member is array-like or a map, read through the nodes that hold one member only."""
    while not (data_type in ARRAYS or data_type in MAPPINGS) and len(data_type.data_types) == 1:
        data_type = data_type.data_types[0]
    if data_type in ARRAYS:
        return "array"
    return "map" if data_type in MAPPINGS else None


def _declared_unique_arrays(
    field: UniqueItemsHolder, sources: UniqueItemsSources, *, sets: bool = False
) -> list[tuple[_Path, tuple[DataType, ...]]]:
    """Return the arrays that the schemas ``sources`` picks declare unique, by schema, each with its node's position.

    The position is the path of indexes from the field's type to the node built from the schema; the arrays of the
    field's own schema but a type list's are the ``field`` source, so ``merged`` leaves them out wherever a node
    records the keyword too, and reads those of them that are still lists once more, as one schema at the field's
    level: the lists the arrays of the other schemas become sets in may let them hold their items. With ``sets``, the
    sets --use-unique-items-as-set wrote are among the arrays.
    """
    field_type = field.data_type
    own = _declares_unique_items(field)
    own_released = own and not declares_type_list_unique_items(field)
    released = {*map(id, _unique_items_arrays(field_type, sets=sets))} if own_released else set()
    found: list[tuple[_Path, tuple[DataType, ...]]] = []
    if sources != "merged" and own_released:
        found.append(((), tuple(_unique_items_arrays(field_type, sets=sets))))
    if sources == "field":
        return found
    if own and (sources == "merged" or not own_released):
        found.append(((), tuple(_unique_items_arrays(field_type, sets=sets))))
    for path, node in _nodes_with_paths(field_type, ()):
        if unique_items_of(node) and (
            group := tuple(
                array
                for array in _unique_items_arrays(node, sets=sets)
                if sources == "all" or id(array) not in released
            )
        ):
            found.append((path, group))
    return found


def unique_items_positions(model: DataModel) -> tuple[tuple[_Path, ...], ...]:
    """Return, field by field, the positions of the arrays whose schemas declare ``uniqueItems`` in the field's type.

    Two models that differ in them are no duplicates under --use-unique-items-as-set-by-value, which writes sets on the
    final types only where a schema declares the keyword. The type of the additional properties that the settings of
    the model write (``additional_properties_holder``) comes last, read as a field.
    """
    holders: Iterable[UniqueItemsHolder] = model.fields
    if (holder := additional_properties_holder(model)) is not None:
        holders = (*model.fields, holder)
    return tuple(tuple(path for path, _ in _declared_unique_arrays(field, "all")) for field in holders)


def _nodes_with_paths(data_type: DataType, path: _Path) -> Iterator[tuple[_Path, DataType]]:
    """Yield the nodes of a type tree, each after the nodes it holds, with its path of indexes from the root."""
    for index, nested in enumerate(data_type.data_types):
        yield from _nodes_with_paths(nested, (*path, index))
    yield path, data_type


def _unique_items_arrays(data_type: DataType, *, sets: bool = False) -> Iterator[DataType]:
    """Yield the lists and tuples that the ``uniqueItems`` of the schema of ``data_type`` declares unique.

    They are the type itself or the members of its union; the arrays a container holds are its items or its values,
    which the keyword does not reach. With ``sets``, the sets --use-unique-items-as-set wrote from such lists
    (``is_unique_items_set``) are yielded too.
    """
    if data_type.is_list or data_type.is_tuple or (sets and is_unique_items_set(data_type)):
        yield data_type
    elif data_type not in CONTAINERS:
        for nested in data_type.data_types:
            yield from _unique_items_arrays(nested, sets=sets)


def takes_hashable_value(
    data_type: DataType, *, keeps_tuples: bool, generic_base_config: Mapping[str, Any] | None
) -> bool:
    """Return whether some value of ``data_type`` hashes, as ``unhashable_unique_sets`` reads the items of a set."""
    return set_items_can_hash((_set_item_facts(data_type, (), _PlanReading(keeps_tuples, "all", generic_base_config)),))


def aliases_first(models: Sequence[DataModel]) -> list[DataModel]:
    """Return ``models`` with their type aliases first, each after the aliases of ``models`` that its value names.

    A pass that writes the uniqueItems lists of the models as sets in this order decides an alias before the models
    that name it, so they read the sets of its value as written (``_set_item_facts``); an alias that its own value
    names, through other aliases or not, is read as any other model. The aliases are walked with a stack of their own,
    so a chain of aliases of any length is ordered.
    """
    aliases = {id(model): model for model in models if isinstance(model, TypeAliasBase)}
    ordered: dict[int, DataModel] = {}
    visiting: set[int] = set()

    def named(alias: DataModel) -> Iterator[DataModel]:
        for field in alias.fields:
            for nested in field.data_type.all_data_types:
                if (reference := nested.reference) is not None and id(reference.source) in aliases:
                    yield aliases[id(reference.source)]

    for alias in aliases.values():
        if id(alias) in visiting:
            continue
        visiting.add(id(alias))
        stack: list[tuple[DataModel, Iterator[DataModel]]] = [(alias, named(alias))]
        while stack:
            current, pending = stack[-1]
            if (target := next((other for other in pending if id(other) not in visiting), None)) is None:
                stack.pop()
                ordered[id(current)] = current
            else:
                visiting.add(id(target))
                stack.append((target, named(target)))
    return [*ordered.values(), *(model for model in models if id(model) not in aliases)]


@final
@record
class _Combined:
    """The facts of a tuple (``positions``) or a union of the ``count`` types read last (``_set_item_facts``)."""

    positions: bool
    count: int


_ItemRead: TypeAlias = "tuple[DataType, Collection[int], _PlanReading]"
"""A type to read, with the lists of the type that become sets by id and the reading."""


def _set_item_facts(data_type: DataType, sets: Collection[int], reading: _PlanReading) -> SetItemFacts:
    """Return the facts of an item type of an array that ``set_items_can_hash`` reads.

    ``sets`` holds the lists of the type that become sets, by id. A container is read as it is written: a set under
    --use-generic-container-types is a frozen set, whose values hash. The positions of a tuple of one repeated type are
    that type. A type alias is read as its value as it is written now, whose lists the passes write as sets before the
    models that name the alias read it (``aliases_first``), and takes None where its value admits it, also where the
    alias's field holds the None (``DataModel.holds_null``); one that holds itself is read as any other model the type
    names. A model takes no hashable value where its class never hashes its instances
    (``DataModel._instances_never_hash``) and the reading reads classes (``_PlanReading.reads_classes``). The types are
    read with a stack of their own (``_set_item_step``), so a chain of aliases of any length is read.
    """
    facts: list[SetItemFacts] = []
    pending: list[_ItemRead | _Combined] = [(data_type, sets, reading)]
    while pending:
        if isinstance(read := pending.pop(), _Combined):
            start = len(facts) - read.count
            parts = tuple(facts[start:])
            del facts[start:]
            facts.append(SetItemFacts(positions=parts) if read.positions else SetItemFacts(members=parts))
        elif isinstance(step := _set_item_step(*read), SetItemFacts):
            facts.append(step)
        else:
            combined, nested = step
            if combined is not None:
                pending.append(combined)
            pending.extend(reversed(nested))
    return facts[0]


def _set_item_step(
    data_type: DataType, sets: Collection[int], reading: _PlanReading
) -> SetItemFacts | tuple[_Combined | None, list[_ItemRead]]:
    """Return the facts of ``data_type`` (``_set_item_facts``), or the types they are made of and how they combine.

    The value of a type alias is the one type its facts are; a tuple and a union combine theirs (``_Combined``).
    """
    if data_type.is_optional:
        return SetItemFacts(null=True)
    if (
        (reference := data_type.reference) is not None
        and isinstance(alias := reference.source, TypeAliasBase)
        and id(alias) not in reading.aliases
        and alias.fields
    ):
        value = (alias.fields[0].data_type, (), replace(reading, aliases=reading.aliases | {id(alias)}))
        return SetItemFacts(null=True) if alias.holds_null else (None, [value])
    if (
        reading.reads_classes
        and reference is not None
        and isinstance(model := reference.source, DataModel)
        and model._instances_never_hash(reading.generic_base_config)  # noqa: SLF001
    ):
        return SetItemFacts(unhashable=True)
    if data_type.is_tuple:
        count = data_type.tuple_item_count
        positions = data_type.data_types if count is None else data_type.data_types[: min(count, 1)]
        return _Combined(positions=True, count=len(positions)), [(position, sets, reading) for position in positions]
    if id(data_type) in sets:
        slot: ContainerSlot | None = "set"
    else:
        slot = next((slot for slot in WRITTEN_CONTAINERS if getattr(data_type, CONTAINER_FLAGS[slot])), None)
    if slot is None:
        members = data_type.data_types
        return _Combined(positions=False, count=len(members)), [(member, sets, reading) for member in members]
    return SetItemFacts(
        unhashable=written_slot(slot, generic=data_type.use_generic_container) in UNHASHABLE_CONTAINERS.slots
    )


def _annotated_reference(data_type: DataType) -> Reference | None:
    """Return the type that the annotation of ``data_type`` names: its reference, or the enum of its enum members."""
    return data_type.reference or data_type._enum_member_literal_reference  # noqa: SLF001


def _annotated_references(model: DataModel) -> Iterator[tuple[DataType, Reference]]:
    """Yield each type that an annotation of the model's fields names, with the data type that names it."""
    for field in model.fields:
        for data_type in field.data_type.all_data_types:
            if (reference := _annotated_reference(data_type)) is not None:
                yield data_type, reference


def _annotated_enum(data_type: DataType) -> Enum | None:
    """Return the Enum the annotation of ``data_type`` or of a type inside it names (``_annotated_reference``)."""
    return next(
        (
            source
            for nested in data_type.all_data_types
            if (reference := _annotated_reference(nested)) is not None and isinstance(source := reference.source, Enum)
        ),
        None,
    )


@final
class _ModelPositions:
    """The position of each model in the model list of a module, found by model key."""

    __slots__ = ("_keyed", "_positions", "paths")

    def __init__(self, models: list[DataModel], site: str) -> None:
        """Index ``models``, which ``site`` names in a violation."""
        self._keyed = keyed_models(models, site)
        self._positions = {model_key_of(model.reference): index for index, model in enumerate(models)}
        self.paths: Mapping[str, int] = {model.path: index for index, model in enumerate(models)}
        """The position of each model by path, as the ``DataModel`` hooks that take positions read them."""

    def get(self, reference: Reference) -> int | None:
        """Return the position of the model of the list that ``reference`` names (`KeyedModels.key_of`), or None."""
        return self._positions.get(self._keyed.key_of(reference))

    def rank(self, reference: Reference) -> int:
        """Return the position of the model of the list that ``reference`` names, or -1."""
        return self._positions.get(self._keyed.key_of(reference), -1)


def _derived_models(models: Iterable[DataModel]) -> dict[ModelKey, list[DataModel]]:
    """Map the model key of each base model to the models of ``models`` derived from it directly."""
    keys = _ModelKeys()
    derived_models: defaultdict[ModelKey, list[DataModel]] = defaultdict(list)
    for derived_model in models:
        for base_model in _find_base_classes(derived_model):
            derived_models[keys.of(base_model)].append(derived_model)
    return derived_models


class BaseStandInHost(Protocol):
    """The stand-ins: models of schemas that bases name and the output types by an import, which are never written."""

    @property
    def _base_stand_in_imports(self) -> dict[ModelKey, Import]:
        """The imports of the classes the stand-in models stand for, by model key."""


def without_stand_ins(host: BaseStandInHost, models: list[DataModel]) -> list[DataModel]:
    """Return ``models`` without the stand-ins, which are never written: a stand-in is no member of its module."""
    if not (stand_ins := host._base_stand_in_imports):  # noqa: SLF001
        return models
    return [model for model in models if model_key_of(model.reference) not in stand_ins]


class TypeOverrideHost(BaseStandInHost, Protocol):
    """The model-level type overrides, which replace the models they name, and the stand-ins, which imports replace."""

    @property
    def _model_type_override_imports(self) -> dict[str, Import]:
        """The imports of the types that override models, by model name."""


def enum_site_override_import(host: TypeOverrideHost, reference: Reference) -> Import | None:
    """Return the model-level override of the target an enum site model is built from, which replaces the model.

    An enum cannot take the type that replaces its target as its root, so it gives way to that type as the target
    does: the model is left out and its references take the type.
    """
    if (target := reference.__dict__.get(_DERIVED_FROM_KEY)) is None or not isinstance(reference.source, Enum):
        return None
    return model_type_override(host._model_type_override_imports, target, row="class-name")  # noqa: SLF001


_ConstructorFieldAdjustment: TypeAlias = Literal["assignment", "keyword_only"]


class _InheritedConstructorInfo(NamedTuple):
    required_assignment_names: frozenset[str]
    inherited_stripped_none_names: frozenset[str]
    ordering_conflicts: frozenset[str]
    inherited_conflicts: tuple[tuple[DataModelFieldBase, DataModel], ...]


class ConstructorInfoHost(Protocol):
    """The bases constructor ordering reads in place of others: the stand-ins of overridden bases, the generic base."""

    @property
    def _base_stand_ins(self) -> dict[int, Reference | None]:
        """The generated model each base that a model-level type override replaced stood for, by base."""

    @property
    def _generic_base_model(self) -> DataModel | None:
        """The generated base class of every model, if the output has one."""


def constructor_base_models(host: ConstructorInfoHost, model: DataModel, *, early: bool = False) -> list[DataModel]:
    """Return the generated models whose fields ``model``'s constructor takes, in MRO order, the generic base left out.

    ``early`` reads the bases as parsed; otherwise a base a model-level type override replaced is the generated
    model it stood for.
    """
    if not model.base_classes:
        return []
    stand_ins = {} if early else host._base_stand_ins  # noqa: SLF001
    return [
        base
        for base in linearize_data_models(direct_base_models(model, stand_ins), stand_ins)
        if base is not host._generic_base_model  # noqa: SLF001
    ]


def get_inherited_constructor_info(
    host: ConstructorInfoHost, model: DataModel, *, early: bool = False
) -> _InheritedConstructorInfo | None:
    """Return inherited value leaks and exact positional ordering conflicts.

    A required field that redeclares an inherited default is assigned ``Field(...)`` unless that default takes no
    place in the constructor (``AssignmentFacts.place_assigned``): a ``None`` that ``--strip-default-none`` strips but
    the base still writes, which the redeclared field inherits, as in 0.86.0.
    """
    if not model.SUPPORTS_KW_ONLY:
        return None
    if not (inherited_models := constructor_base_models(host, model, early=early)):
        return None

    supports_inherited_override = model.SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT
    required_assignment_names: set[str] = set()
    inherited_stripped_none_names: set[str] = set()
    inherited_defaults: dict[int, bool] = {}
    if supports_inherited_override:
        for inherited_model in reversed(inherited_models):
            for field in inherited_model.fields:
                if not (field_name := field.name):
                    continue
                facts = model._field_assignment_facts(field)  # noqa: SLF001
                has_default, has_value_default = facts.default_info
                inherited_defaults[id(field)] = has_default
                if has_value_default or (has_default and model.REQUIRES_EXPLICIT_INHERITED_FACTORY_OVERRIDE):
                    required_assignment_names.add(field_name)
                    if facts.place_assigned:
                        inherited_stripped_none_names.discard(field_name)
                    else:
                        inherited_stripped_none_names.add(field_name)
    effective_fields = {
        name: (field, owner, None if owner is model else inherited_defaults.get(id(field)))
        for name, (owner, field) in _effective_field_owners(
            model,
            inherited_models,
            stand_ins={} if early else host._base_stand_ins,  # noqa: SLF001
        ).items()
    }
    conflicts = get_constructor_ordering_conflicts(model, effective_fields, required_assignment_names)
    return _InheritedConstructorInfo(
        frozenset(required_assignment_names),
        frozenset(inherited_stripped_none_names),
        conflicts.own,
        conflicts.inherited,
    )


def get_constructor_field_adjustment(
    model: DataModel,
    field: DataModelFieldBase,
    inherited: _InheritedConstructorInfo,
    *,
    supports_inherited_override: bool,
) -> _ConstructorFieldAdjustment | None:
    """Return the exact explicit assignment needed for a required child field."""
    facts = model._field_assignment_facts(field)  # noqa: SLF001
    if not facts.participates_in_constructor:
        return None
    if field.name in inherited.ordering_conflicts:
        return "keyword_only"
    if facts.assigned:
        return None
    if (
        field.required
        and supports_inherited_override
        and field.name in inherited.required_assignment_names
        and field.name not in inherited.inherited_stripped_none_names
    ):
        return "assignment"
    return None


_TYPE_DEPENDENT_CONSTRAINTS_KEY: Final = TYPE_DEPENDENT_CONSTRAINTS_KEY


def _assign_module_name(
    model_path_to_module_name: dict[str, str], model: DataModel, module_name: str, *, keep: bool = False
) -> None:
    """Give ``model`` the module name in the map, by model key too unless a subclass of the parser built the map."""
    if isinstance(model_path_to_module_name, _ModuleNames):
        model_path_to_module_name.assign(model, module_name, keep=keep)
    elif keep:
        model_path_to_module_name.setdefault(model.path, module_name)
    else:
        model_path_to_module_name[model.path] = module_name


def _parsed_rule_data_types(model: DataModel) -> Iterator[DataType]:
    """Yield the data types of the parser's schema validator rules of a model, held by a type alias too."""
    return _rule_data_types(model, unwritable=True)


def _tag_field_matches(
    field: DataModelFieldBase, match: TagFieldMatch, wanted: str, *, python_name: str | None = None
) -> bool:
    """Return whether a field holds the discriminator's property ``wanted`` (``tag_field_matches``)."""
    return tag_field_matches(
        match, wanted, name=field.name, original_name=field.original_name, alias=field.alias, python_name=python_name
    )


def _keeps_instance_field(field: DataModelFieldBase, source_name: str) -> bool:
    """Return whether a field is an instance field that the document names ``source_name`` (``tag_field_kept``)."""
    return tag_field_kept(
        source_name,
        is_class_var=bool(field.extras.get("is_classvar")),
        name=field.name,
        original_name=field.original_name,
        alias=field.alias,
    )


def _format_tags(tags: list[DiscriminatorValue]) -> str:
    """Spell discriminator tags as the document writes them."""
    return ", ".join(map(stable_repr, tags))


def enum_discriminator_literal(enum_source: Enum, value: DiscriminatorValue) -> DiscriminatorValue:
    """Return the tag ``Literal`` value that a discriminator value takes from the member of its enum, if any."""
    member = enum_source.find_member(value, coerce_strings=True)
    return enum_tag_literal(value, member.value) if member else value


def enum_discriminator_members(enum_source: Enum, values: Iterable[DiscriminatorValue]) -> EnumTagMembers:
    """Return the members of an Enum that tag a variant whose tags are ``values`` (``enum_tag_members``)."""
    return enum_tag_members(
        (enum_source.find_member(value, coerce_strings=True) for value in values),
        lambda: [enum_source.get_member(field) for field in enum_source.fields],
    )


def _get_discriminator_field_value(
    discriminator_field: DataModelFieldBase, site: FieldTagSite = "variant-field"
) -> DiscriminatorValue | None:
    """Return the one tag a variant's tag field holds (``field_tag``) at ``site``, a variant-field site."""
    tag = field_tag(site, _FieldTagFacts(discriminator_field))
    return None if tag is None else tag.value


def _get_enum_from_base(
    discriminator_model: DataModel, field_name: str, *, use_field_name: bool = False
) -> Enum | None:
    """Return the Enum typing the tag field a variant inherits, from the nearest ancestor whose field it types.

    The direct bases come first, in their order, then the ancestors above them in method resolution order, so a
    variant two levels below the schema whose Enum types the tag property takes that Enum, as a direct subclass does.
    """
    match_mode: TagFieldMatch = "python-name" if use_field_name else "python-or-original-name"
    direct = [
        base_class.reference.source
        for base_class in discriminator_model.base_classes
        if base_class.reference and base_class.reference.source
    ]
    return _declared_tag_enum(direct, field_name, match_mode) or _declared_tag_enum(
        [
            ancestor
            for ancestor in linearize_data_models([discriminator_model])[1:]
            if all(ancestor is not base for base in direct)
        ],
        field_name,
        match_mode,
    )


def _declared_tag_enum(models: Iterable[object], field_name: str, match_mode: TagFieldMatch) -> Enum | None:
    """Return the Enum typing the first field of ``models`` that holds the tag property, by models that inherit one."""
    for model in models:
        if not isinstance(model, DataModel) or not model.SUPPORTS_INHERITED_DISCRIMINATOR_ENUM:
            continue
        for field_ in model.fields:
            if _tag_field_matches(field_, match_mode, field_name) and (
                enum_source := field_.data_type.find_source(Enum)
            ):
                return enum_source
    return None


_STRING_LITERAL_SOURCE: Final = r"""'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|"""
_STRING_OR_IDENTIFIER_PATTERN: Final = re.compile(
    _STRING_LITERAL_SOURCE + r"""^[ \t]*[A-Za-z_]\w*(?=:)|([A-Za-z_]\w*)""", re.MULTILINE
)
_STRING_OR_TARGET_IDENTIFIER_SOURCE: Final = _STRING_LITERAL_SOURCE + r"""^[ \t]*(?#identifier)(?=:)|((?#identifier))"""


def _string_or_identifier_pattern(text: str, target_python_version: PythonVersion) -> re.Pattern[str]:
    """Return the pattern that finds string literals, field declarations and names in text, as the target names them."""
    return (
        _STRING_OR_IDENTIFIER_PATTERN
        if text.isascii()
        else target_pattern(_STRING_OR_TARGET_IDENTIFIER_SOURCE, target_python_version, re.MULTILINE)
    )


SPECIAL_PATH_FORMAT: str = _SPECIAL_PATH_FORMAT


def get_special_path(keyword: str, path: list[str]) -> list[str]:
    """Create a special path marker for internal reference tracking."""
    return _special_path(keyword, path)


def _forward_enum_member_literals(field: DataModelFieldBase, positions: _ModelPositions, index: int) -> list[DataType]:
    """Return the field's enum member literals whose enum the module defines at or after the model at ``index``.

    A member is a value, so a deferred annotation does not resolve it later: reading the class evaluates the member
    to a ``ForwardRef`` that stays inside the ``Literal``, unless the annotation is a string. ``positions`` gives each
    model of the module its order.
    """
    return [
        data_type
        for data_type in field.data_type.all_data_types
        if (reference := data_type._enum_member_literal_reference) is not None  # noqa: SLF001
        and positions.rank(reference) >= index
    ]


def _hidden_type_names(model: DataModel) -> Collection[str | None]:
    """Return the names of the model's fields that hide a type named by an annotation of the model.

    Python looks an annotation up in the class namespace first. A library that resolves string annotations
    from the module still finds such a type when it is written as a string, like a forward reference.
    """
    if not type(model).RESOLVES_STRING_ANNOTATIONS_FROM_MODULE:
        return ()
    return {reference.short_name for data_type, reference in _annotated_references(model) if not data_type.alias} & {
        field.name for field in model.fields if model.field_name_hides_types(field)
    }


@final
class UnusedModels:
    """Models that leave the output, by identity: ``model in unused`` asks of one, ``written`` keeps the others."""

    __slots__ = ("_ids",)

    def __init__(self, models: Iterable[DataModel]) -> None:
        """Take the models that leave."""
        self._ids = {id(model) for model in models}

    def __contains__(self, model: object) -> bool:
        """Return whether ``model`` is one of the models that leave."""
        return id(model) in self._ids

    def written(self, models: Iterable[DataModel]) -> list[DataModel]:
        """Return the models of ``models`` that stay, in their order."""
        return [model for model in models if id(model) not in self._ids]


class UnusedModelsHolder(Protocol):
    """A run, or the state of its finalization, that holds the unused models of the run and the modules they leave."""

    @property
    def contexts(self) -> Sequence[ModuleContextView]:
        """The output modules, each with the model list it writes."""

    @property
    def unused_models(self) -> list[DataModel]:
        """The models no module writes."""

    @property
    def model_to_module_models(self) -> dict[DataModel, tuple[ModulePath, list[DataModel]]]:
        """The module and the model list the module structure recorded for each model."""


def run_unused_models(host: ReplacedRootsHost, run: UnusedModelsHolder) -> UnusedModels:
    """Return the models that the passes between the module loop and unused-model removal leave out.

    They leave out exactly the models that removal takes out of a model list a module writes; one that removal leaves
    written in a merged module (``unused_model_lists``) still takes the passes.
    """
    return UnusedModels(model for model, _, _, written in unused_model_lists(host, run) if written)


def unused_model_lists(
    host: ReplacedRootsHost, state: UnusedModelsHolder
) -> Iterator[tuple[DataModel, ModulePath, list[DataModel], bool]]:
    """Yield each unused model with the module and the model list it leaves, and whether its module writes that list.

    A root model that collapsing replaced leaves the list of the module that writes it now. The list the module
    structure recorded for another model may be an earlier one than the list its module writes.
    """
    written_lists = {id(ctx.models) for ctx in state.contexts}
    replaced_root_modules = (
        {
            id(model): (ctx.module_key, ctx.models)
            for ctx in state.contexts
            for model in ctx.models
            if id(model) in replaced_root_models
        }
        if (replaced_root_models := host._replaced_root_models)  # noqa: SLF001
        else {}
    )
    for unused_model in state.unused_models:
        module, models = replaced_root_modules.pop(id(unused_model), None) or state.model_to_module_models[unused_model]
        yield unused_model, module, models, id(models) in written_lists
