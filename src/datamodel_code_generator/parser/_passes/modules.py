"""The module passes (L5): what the models of one output module take while the module is processed.

Each pass is a function of the parser and the models or the state of the module, called from the pipeline tables of
``parser/_passes/_tables.py`` once per output module; the parser comes in through ``ModulesHost`` (importing the
models of other modules, reuse, collapse and discriminator tags through the narrower ``ImportFromHost``, ``ReuseHost``,
``CollapseHost`` and ``DiscriminatorHost``) and the state through ``ModuleStateView``, which ``Parser`` and its module
state satisfy structurally. ``process_each_module`` runs the passes of each module through the
``_process_single_module`` hook (``ModuleLoopHost``); ``apply_base_stand_in_imports``,
``write_rule_aliases_as_classes`` (``RuleAliasHost``), ``name_inner_models_in_rules`` and
``settle_discriminated_unions`` run once every module is processed, on the run through ``ModulesRun``.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import chain, islice
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple, TypeAlias, cast

from typing_extensions import Protocol, final

from datamodel_code_generator import CollapseRootModelsNameStrategy, Error, FieldTypeCollisionStrategy, ReuseScope
from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._constraint_applicability import TAG_LITERAL_UNTAKEN_KEYWORD
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator._internal_utils import get_most_of_parent
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
from datamodel_code_generator._shared_types import _CollapseRootModelsRecursionError
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.enums import DefaultValueType
from datamodel_code_generator.imports import IMPORT_OPTIONAL, IMPORT_UNION, Import
from datamodel_code_generator.model._decisions.enum_kind import decide_enum_kind, holds_inherited_member_value
from datamodel_code_generator.model._decisions.literal_default import decide_literal_default
from datamodel_code_generator.model._decisions.tag_value import (
    is_tag_value,
    same_tag,
    same_tags,
    struct_tag_conflicts,
    struct_tag_field,
    tag_key,
    variant_tags,
)
from datamodel_code_generator.model.base import (
    UNDEFINED,
    BaseClassDataType,
    ConstraintsBase,
    DataModel,
    DataModelFieldBase,
    _find_base_classes,
    _keyed_answer,
    _ModelKeys,
    _reference_names_model,
    _share_path_identity,
    get_inherited_fields,
)
from datamodel_code_generator.model.enum import Enum, IntEnum, Member, StrEnum, _enum_class, get_raw_enum_member_value
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.parser._facts import Phase
from datamodel_code_generator.parser._finality import module_checkpoint, pass_scope
from datamodel_code_generator.parser._graph_ids import UNRESOLVED_MODEL_KEY, keyed_models
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._model_shapes import REUSE_MODEL, skips_root_model
from datamodel_code_generator.parser._passes._keys import _DERIVED_FROM_KEY
from datamodel_code_generator.parser._passes._shared import (
    _TYPE_DEPENDENT_CONSTRAINTS_KEY,
    BaseStandInHost,
    ConstructorInfoHost,
    DiscriminatorValue,
    RecursionFactsHost,
    ReferenceExpressionHost,
    RenamedTypesHost,
    ReplacedRootsHost,
    TypeOverrideHost,
    _annotated_reference,
    _assign_module_name,
    _context_models,
    _copy_data_type,
    _declares_unique_items,
    _derived_models,
    _get_data_type_target_full_name,
    _get_discriminator_field_value,
    _get_enum_from_base,
    _get_mapping_discriminator_values,
    _get_model_module_name,
    _get_schema_path,
    _has_root_field_constraints,
    _is_discriminator_container,
    _is_discriminator_wrapper,
    _is_pydantic_v2_root_model,
    _iter_discriminator_data_types,
    _iter_import_bindings,
    _keeps_instance_field,
    _ModelPositions,
    _named_data_types,
    _parsed_rule_data_types,
    _register_data_type_import,
    _restore_inherited_field_names_for_unique_aliases,
    _rule_data_types,
    _runtime_validation_reference_keys,
    _tag_field_matches,
    _unique_items_tuple,
    _VariantDiscriminatorTags,
    enum_discriminator_literal,
    enum_discriminator_members,
    enum_site_override_import,
    get_constructor_field_adjustment,
    get_inherited_constructor_info,
    module_members,
    names_left_by_replaced_roots,
    record_renamed_type,
    redirect_collapsed_reuse_users,
    register_reference_import,
    replace_model_in_list,
    require_update_action,
    set_reference_expression,
)
from datamodel_code_generator.parser._passes._state import ConfigT, ContextT, RecursionFacts, ResultT, _RenamedType
from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base
from datamodel_code_generator.parser._passes.render_prep import validates_structured_default
from datamodel_code_generator.python_literal import runtime_expression_imports
from datamodel_code_generator.reference import _ALIAS_RESOLUTION_CLASS_NAME_KEY, SPECIAL_PATH_MARKER, ModelResolver
from datamodel_code_generator.types import ANY, NONE, DataType, Types

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.enums import DataclassArguments, TargetPydanticVersion
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model._decisions.tag_value import TagFieldMatch, VariantTagSource
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.parser._imports_plan import ModuleImportLedger
    from datamodel_code_generator.parser._model_shapes import ModelShapes, ShapeRow
    from datamodel_code_generator.parser._passes._shared import _ReuseOptimizationContext
    from datamodel_code_generator.parser._passes._state import (
        ModuleContextView,
        ModulePath,
        ProcessingRun,
        _ConstNarrowing,
        _PreparedModule,
    )
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import ModelType, Reference
    from datamodel_code_generator.types import DataTypeManager

__all__ = [
    "CollapseHost",
    "DiscriminatorHost",
    "ImportFromHost",
    "ModuleLoopHost",
    "ModuleStateView",
    "ModulesHost",
    "ModulesRun",
    "ReuseHost",
    "RuleAliasHost",
    "apply_base_class_type_overrides",
    "apply_base_stand_in_imports",
    "apply_discriminator_type",
    "apply_field_type_overrides",
    "change_field_name",
    "change_module_from_import",
    "collapse_module_root_models",
    "collapse_root_models",
    "extract_module_inherited_enums",
    "mark_unused_models_start",
    "name_inner_models_in_rules",
    "process_each_module",
    "record_decimal_alias_models",
    "remove_module_overridden_models",
    "rename_fields_hiding_types",
    "replace_unique_list_to_set",
    "restore_inherited_field_names_for_unique_aliases",
    "reuse_model",
    "set_default_enum_member",
    "set_one_literal_on_default",
    "set_reference_default_value_to_field",
    "settle_discriminated_unions",
    "snapshot_module_unused_models",
    "sort_models",
    "type_const_narrowed_values",
    "type_module_dict_keys_by_root_types",
    "write_rule_aliases_as_classes",
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
    def collapse_root_models(self) -> bool:
        """Whether root models are collapsed into their users."""

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


class ReuseHost(RecursionFactsHost, Protocol):
    """The options, run state and hooks of the parser that module-scope reuse reads and writes."""

    @property
    def _collapsed_reuse_models(self) -> dict[int, list[DataModel]]:
        """The models collapsed reuse removed, by the id of their canonical model."""

    @property
    def _collapsed_reuse_users(self) -> dict[int, tuple[DataType, Reference]]:
        """The data types collapsed reuse redirected, with the reference each named before."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def _removed_reuse_users(self) -> dict[int, tuple[DataType, list[str]]]:
        """The data types whose duplicate reuse removed, by id, with the paths they named."""

    @property
    def _reuse_optimization_context(self) -> _ReuseOptimizationContext:
        """The constraints that type overrides put on reuse."""

    @property
    def _typed_map_value_reference_ids(self) -> set[int]:
        """The ids of the references of typed map values."""

    @property
    def collapse_reuse_models(self) -> bool:
        """Whether reuse removes duplicates instead of subclassing them."""

    @property
    def collapse_root_models(self) -> bool:
        """Whether root models are collapsed into their users."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""

    @property
    def reuse_model(self) -> bool:
        """Whether models that render the same are reused."""

    @property
    def reuse_scope(self) -> ReuseScope | None:
        """The scope of reuse."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""

    def _model_shapes(self, row: ShapeRow) -> ModelShapes[DataModel, Reference]:
        """Return the keys of one comparison pass of the dedup or reuse user of a row."""


class InlineVariantHost(Protocol):
    """What deciding which inline variants take the tag they inherit reads of the parser."""

    _inline_variant_models: _InlineVariantTags | None

    @property
    def field_name_model_type(self) -> ModelType:
        """The model type that field names are validated for."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of model and field names."""

    @property
    def results(self) -> list[DataModel]:
        """The parsed models."""

    @property
    def use_enum_values_in_discriminator(self) -> bool:
        """Whether discriminator tags are written as the members of their enums."""


class CollapseHost(
    InlineVariantHost, RecursionFactsHost, ReferenceExpressionHost, RenamedTypesHost, ReplacedRootsHost, Protocol
):
    """The options, run state and hooks of the parser that collapsing root models reads and writes."""

    @property
    def _collapses_list_item_root_models(self) -> bool:
        """Whether collapse inlines the root models that list items name."""

    @property
    def _const_narrowed_values(self) -> dict[int, tuple[DataType, _ConstNarrowing]]:
        """The values typed by an enum that a const narrows, by data type id, with that const."""

    @property
    def _copy_model_type(self) -> Callable[[DataType], DataType]:
        """The function that copies a data type tree without detaching its model references."""

    @property
    def _emitting_module_lists(self) -> list[tuple[str, list[DataModel]]]:
        """The live model list of each output module, by module name."""

    @property
    def _emitting_module_names(self) -> dict[int, str]:
        """The module that emits each model found in the live lists, by model id."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def collapse_root_models(self) -> bool:
        """Whether root models are collapsed into their users."""

    @property
    def collapse_root_models_name_strategy(self) -> CollapseRootModelsNameStrategy | None:
        """How collapsed root models name the models that replace them, if configured."""

    @property
    def data_model_field_type(self) -> type[DataModelFieldBase]:
        """The field type of the output."""

    @property
    def field_constraints(self) -> bool:
        """Whether constraints are written as ``Field()`` arguments."""

    @property
    def generate_schema_validators(self) -> bool:
        """Whether models get the schema validator rules the types cannot express."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""

    def _keeps_root_model_named(self, model: DataModel, circular_root_models: Collection[ModelKey]) -> bool:
        """Return whether collapsing keeps a root model named wherever it is referenced."""


class DiscriminatorHost(InlineVariantHost, Protocol):
    """The options, run state and hooks of the parser that writing discriminator tags reads and writes."""

    _deferred_tagged_unions: list[_DiscriminatorUnion]

    @property
    def _collapsed_reuse_models(self) -> dict[int, list[DataModel]]:
        """The models collapsed reuse removed, by the id of their canonical model."""

    @property
    def _collapsed_reuse_users(self) -> dict[int, tuple[DataType, Reference]]:
        """The data types collapsed reuse redirected, with the reference each named before."""

    @property
    def _defers_tagged_unions(self) -> bool:
        """Whether modules remain whose unions may tag a struct, so the unions such a struct blocks wait."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def _import_ledger(self) -> ModuleImportLedger:
        """The ledger of the imports each module adds."""

    @property
    def _reused_variant_tag_fields(self) -> dict[int, DataModelFieldBase]:
        """The tag fields that a union with a reused variant annotates with a new tag, by field id."""

    @property
    def _variant_discriminator_tags(self) -> dict[tuple[int, str], _VariantDiscriminatorTags]:
        """The tags of discriminated union variants, by variant id and tag property."""

    @property
    def config(self) -> ParserConfig:
        """The parser configuration."""

    @property
    def data_model_field_type(self) -> type[DataModelFieldBase]:
        """The field type of the output."""

    @property
    def force_optional_for_required_fields(self) -> bool:
        """Whether required fields are made optional."""

    @property
    def tag_msgspec_by_payload_name(self) -> bool:
        """Whether msgspec tag fields are named by their payload names."""

    @property
    def use_msgspec_tagged_unions(self) -> bool:
        """Whether msgspec unions are tagged by their discriminators."""

    @property
    def use_serialization_alias(self) -> bool:
        """Whether aliases are written as serialization aliases."""

    def _create_discriminator_data_type(
        self,
        enum_source: Enum | None,
        discriminator_values: list[DiscriminatorValue],
        discriminator_model: DataModel,
        imports: Imports,
    ) -> DataType:
        """Return the type of a tag field, of the members of its enum if it has one."""

    def _data_model_field_common_kwargs(self) -> dict[str, Any]:
        """Return the keyword arguments every field the parser builds takes."""

    def _get_discriminator_field_name(
        self, data_types: Iterable[DataType], property_name: str, default_name: str
    ) -> str:
        """Return the Python name the variants of a union share for its tag property."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""

    def _split_field_alias(self, alias: str | list[str] | None) -> tuple[str | None, list[str] | None]:
        """Split one output alias from multiple validation aliases."""

    def get_serialization_alias(
        self, original_field_name: str, field_name: str, class_name: str | None = None
    ) -> str | None:
        """Return the serialization alias configured for a field, if any."""


class ImportFromHost(ReferenceExpressionHost, ReplacedRootsHost, BaseStandInHost, Protocol):
    """The option, the import ledger, the replaced roots, the stand-ins and the dropped roots that importing reads."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def _self_named_root_models(self) -> list[DataModel]:
        """The root models naming themselves that the deduplication of root models dropped, whose users name them."""


class ModuleLoopHost(RecursionFactsHost, Protocol[ConfigT, ContextT, ResultT]):
    """The hook and run state of the parser that processing the output modules one by one calls and writes."""

    _defers_tagged_unions: bool

    def _process_single_module(  # noqa: PLR0913, PLR0917
        self,
        module_: ModulePath,
        models: list[DataModel],
        results: dict[ModulePath, ResultT],
        config: ConfigT,
        internal_modules: set[ModulePath],
        model_path_to_module_name: dict[str, str],
        require_update_action_models: list[str],
        unused_models: list[DataModel],
    ) -> ContextT:
        """Process a single module and return its context."""


class RuleAliasHost(ImportFromHost, Protocol):
    """The options, run state and hook of the parser that writing rule aliases as classes reads and calls."""

    @property
    def _prepared_modules(self) -> list[_PreparedModule[Any]]:
        """The output modules as ``_process_single_module`` left them, for their render preparation."""

    @property
    def custom_template_dir(self) -> Path | None:
        """The directory of custom templates, if configured."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def dataclass_arguments(self) -> DataclassArguments | None:
        """The arguments of the dataclass decorator, if configured."""

    @property
    def keyword_only(self) -> bool:
        """Whether the models take keyword-only arguments."""

    def _create_data_model(self, model_type: type[DataModel] | None = None, **kwargs: Any) -> DataModel:
        """Create a model of ``model_type``, the model type of the output by default."""


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
    def internal_modules(self) -> set[ModulePath]:
        """The internal modules that import cycles merged models into."""

    @property
    def is_init(self) -> bool:
        """Whether the module is a package ``__init__``."""

    @property
    def model_path_to_module_name(self) -> dict[str, str]:
        """The module name of each model, by model path."""

    @property
    def scoped_model_resolver(self) -> ModelResolver:
        """The resolver of the names the module binds."""

    @property
    def unused_models(self) -> list[DataModel]:
        """The models no module writes, collected over the modules processed so far."""


class ModulesRun(Protocol):
    """What the module passes that run once every module is processed read of the run."""

    @property
    def contexts(self) -> Sequence[ModuleContextView]:
        """The processed modules, in module order."""

    @property
    def internal_modules(self) -> set[ModulePath]:
        """The internal modules that import cycles merged models into."""

    @property
    def model_path_to_module_name(self) -> dict[str, str]:
        """The module name of each model, by model path."""

    @property
    def unused_models(self) -> list[DataModel]:
        """The models no module writes."""


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


def _rename_field_preserving_alias(builder: GraphBuilder, field: DataModelFieldBase, new_name: str) -> None:
    """Rename a Python field without losing an existing source-name alias."""
    builder.rename_field(field, new_name, alias=field.name)


_COLLAPSE_ROOT_MODELS: Final = ImportProducer("collapse-root-models")


def _reaches_data_type(data_type: DataType, target: DataType) -> bool:
    """Return whether target is data_type or lies under it, through nested data types and dict keys."""
    reached: set[int] = set()
    data_type.walk(lambda _: None, reached)
    return id(target) in reached


def _is_any_variant(data_type: DataType) -> bool:
    return data_type.type == ANY or (
        not data_type.reference and not data_type.data_types and not data_type.literals and not data_type.type
    )


def _iter_discriminator_fields(
    fields: Iterable[DataModelFieldBase],
    field_name: str,
    *,
    field_name_mode: TagFieldMatch = "python-or-original-name",
) -> Iterator[tuple[DataModelFieldBase, DiscriminatorValue]]:
    """Yield the fields of the discriminator's name that carry one discriminator value, with that value."""
    for field in fields:
        if (
            _tag_field_matches(field, field_name_mode, field_name)
            and (value := _get_discriminator_field_value(field)) is not None
        ):
            yield field, value


def _find_discriminator_value(
    fields: Iterable[DataModelFieldBase],
    field_name: str,
    *,
    field_name_mode: TagFieldMatch = "python-or-original-name",
) -> DiscriminatorValue | None:
    return next(
        (value for _, value in _iter_discriminator_fields(fields, field_name, field_name_mode=field_name_mode)), None
    )


def _get_discriminator_values(  # noqa: PLR0913
    discriminator_model: DataModel,
    field_name: str,
    mapping: dict[str, str],
    *,
    inline_models: _InlineVariantTags,
    require_literal: bool = False,
    field_name_mode: TagFieldMatch = "python-or-original-name",
) -> list[DiscriminatorValue]:
    """Return the tags a union lists a variant by; one of a nested choice, never retagged, takes only its own tag."""
    return _variant_tags(
        "nested" if require_literal else "variant",
        discriminator_model,
        field_name,
        mapping,
        inline_models=inline_models,
        field_name_mode=field_name_mode,
    )


def _find_reused_discriminator_value(
    discriminator_model: DataModel,
    field_name: str,
    *,
    field_name_mode: TagFieldMatch,
) -> DiscriminatorValue | None:
    """Return the tag that the schema of a model gives it when reuse replaced it with an empty subclass.

    The base class the subclass inherits from can carry the tag another union wrote, which is not its own.
    """
    reused = discriminator_model.reused_reference
    if reused is None or not isinstance(source := reused.source, DataModel):
        return None
    return _find_discriminator_value(source.fields, field_name, field_name_mode=field_name_mode)


def _get_union_discriminator_values(
    discriminator_model: DataModel,
    field_name: str,
    mapping: dict[str, str],
    *,
    inline_models: _InlineVariantTags,
    field_name_mode: TagFieldMatch,
) -> list[DiscriminatorValue]:
    """Return the tags a union selects a variant by: mapping values, the tag of its own schema, else its name."""
    return _variant_tags(
        "union", discriminator_model, field_name, mapping, inline_models=inline_models, field_name_mode=field_name_mode
    )


def _variant_tags(  # noqa: PLR0913
    source: VariantTagSource,
    discriminator_model: DataModel,
    field_name: str,
    mapping: dict[str, str],
    *,
    inline_models: _InlineVariantTags,
    field_name_mode: TagFieldMatch,
) -> list[DiscriminatorValue]:
    """Return the tags of a variant (``variant_tags``) from its tag fields, the mapping and its schema path."""
    return [
        *variant_tags(
            source, _VariantTagFacts(discriminator_model, field_name, mapping, field_name_mode, inline_models)
        ).values
    ]


@final
@dataclass(frozen=True, slots=True)
class _VariantTagFacts:
    """The facts of a variant that ``variant_tags`` reads, from its fields, the mapping and its schema path."""

    model: DataModel
    field_name: str
    mapping: dict[str, str]
    field_name_mode: TagFieldMatch
    inline_models: _InlineVariantTags

    def own_tag(self) -> DiscriminatorValue | None:
        """Return the tag of the variant's own tag field."""
        return _find_discriminator_value(self.model.fields, self.field_name, field_name_mode=self.field_name_mode)

    def reused_tag(self) -> DiscriminatorValue | None:
        """Return the tag of the model reuse replaced with the variant."""
        return _find_reused_discriminator_value(self.model, self.field_name, field_name_mode=self.field_name_mode)

    def inherited_tag(self) -> DiscriminatorValue | None:
        """Return the tag of a tag field the variant inherits."""
        return _find_discriminator_value(
            self.model.iter_all_fields(), self.field_name, field_name_mode=self.field_name_mode
        )

    def inline_tag(self) -> DiscriminatorValue | None:
        """Return the tag an inline variant inherits as ``inline_variant_models`` read it, where it lets it take it."""
        if (tag_fields := self.inline_models.get(id(self.model))) is None or SPECIAL_PATH_MARKER not in (
            self.path_segment()
        ):
            return None
        return next(
            (value for field, value in tag_fields if _tag_field_matches(field, self.field_name_mode, self.field_name)),
            None,
        )

    def mapped_tags(self) -> list[str]:
        """Return the mapping keys naming the variant or its bases."""
        return _get_mapping_discriminator_values(self.model, self.mapping)

    def path_segment(self) -> str:
        """Return the last segment of the variant's schema path."""
        return _get_schema_path(self.model).split("/")[-1]


def _iter_mapping_discriminator_values(discriminator_model: DataModel, mapping: dict[str, str]) -> Iterator[str]:
    """Yield the mapping values that select a variant, computed when the first one is asked for."""
    yield from _get_mapping_discriminator_values(discriminator_model, mapping)


_InlineVariantTags: TypeAlias = "Mapping[int, tuple[tuple[DataModelFieldBase, DiscriminatorValue], ...]]"
"""The fields with one tag of each inline variant that may take the tag it inherits, by variant id, with their tags."""

_DiscriminatorTag: TypeAlias = tuple[DataModel, DataModelFieldBase, DiscriminatorValue | None]
_DiscriminatorUnion: TypeAlias = tuple[DataModel, list[_DiscriminatorTag], str, dict[str, str]]


def _iter_derived_fields(
    model: DataModel, derived_models: Mapping[ModelKey, list[DataModel]], visited: _ModelKeys
) -> Iterator[DataModelFieldBase]:
    """Yield the fields that the classes derived from the model declare, each class once."""
    for derived_model in derived_models.get(model_key_of(model.reference), ()):
        if not visited.first(derived_model):  # pragma: no cover
            continue
        yield from derived_model.fields
        yield from _iter_derived_fields(derived_model, derived_models, visited)


def _has_related_instance_field(
    model: DataModel,
    source_name: str,
    tagged_fields: set[int],
    derived_models: Mapping[ModelKey, list[DataModel]],
    *,
    flattens_reused: bool,
) -> bool:
    """Return whether a base or derived struct keeps an instance field of that name once the unions are tagged.

    With ``flattens_reused``, the base of a model that reuse made an empty subclass does not count: the model declares
    the fields of its base when it is tagged beside other structs (``tag_fields_of_tagged_bases``).
    """
    return any(
        id(field) not in tagged_fields and _keeps_instance_field(field, source_name)
        for field in chain(
            (
                field
                for base_model in (
                    () if flattens_reused and model.reused_reference is not None else _find_base_classes(model)
                )
                for field in base_model.iter_all_fields()
            ),
            _iter_derived_fields(model, derived_models, _ModelKeys("derived fields")),
        )
    )


def _tag_discriminator_unions(
    builder: GraphBuilder,
    unions: list[_DiscriminatorUnion],
    derived_models: Mapping[ModelKey, list[DataModel]],
    *,
    defers_related: bool,
) -> list[_DiscriminatorUnion]:
    """Tag the unions whose variants all carry their tag, whatever the order the unions are declared in.

    msgspec refuses a tag field that a base or derived struct keeps as an instance field, so unions are left out
    until the remaining ones agree. A struct that two tag fields claim keeps the first one. While other modules
    remain, the unions left out for such a struct are returned instead, as a union of a later module may tag it.
    """
    related: dict[int, list[str]] = {}
    while True:
        tagged_fields: set[int] = set()
        untaggable: dict[int, list[str]] = {}
        for index, (_, tags, _, mapping) in enumerate(unions):
            if index in related:
                continue
            if names := [
                model.class_name
                for model, field, value in tags
                if struct_tag_conflicts(value, _iter_mapping_discriminator_values(model, mapping))
                or any(
                    other is not field and (other.extras.get("is_classvar") or id(other) in tagged_fields)
                    for other in model.fields
                )
            ]:
                untaggable[index] = names
            else:
                tagged_fields.update(id(field) for _, field, _ in tags)
        if not (
            found := {
                index: names
                for index, (_, tags, tag_field, _) in enumerate(unions)
                if index not in related
                and index not in untaggable
                and (
                    names := [
                        model.class_name
                        for model, _, _ in tags
                        if _has_related_instance_field(
                            model, tag_field, tagged_fields, derived_models, flattens_reused=len(tags) > 1
                        )
                    ]
                )
            }
        ):
            break
        related.update(found)
    deferred: list[_DiscriminatorUnion] = []
    for index, (owner, tags, tag_field, _) in enumerate(unions):
        if names := untaggable.get(index):
            reason = f"{', '.join(names)} cannot take exactly one tag for it"
        elif defers_related and index in related:
            deferred.append(unions[index])
            continue
        elif names := related.get(index):
            reason = f"a base or derived struct of {', '.join(names)} keeps {tag_field!r} as a field"
        else:
            _apply_discriminator_tags(builder, tags, tag_field)
            continue
        warn_as_parser_base(
            f"Discriminator {tag_field!r} of {owner.class_name} cannot be represented as msgspec tags: "
            f"{reason}; its variants are left untagged.",
            UserWarning,
            stacklevel=2,
        )
    return deferred


def _apply_discriminator_tags(builder: GraphBuilder, tags: list[_DiscriminatorTag], tag_field: str) -> None:
    """Tag the variants and take their discriminators out of the instance fields."""
    for model, field, value in tags:
        if model.apply_discriminator_tag(field, tag_field, value):
            builder.set_field_extra(field, "is_classvar", value=True)


def _get_mapped_discriminator_tag(
    values: list[DiscriminatorValue], enum_source: Enum | None
) -> DiscriminatorValue | None:
    """Return the wire tag of a variant whose discriminator resolves to exactly one value."""
    if len(values) != 1:
        return None
    return enum_discriminator_literal(enum_source, values[0]) if enum_source else values[0]


def _discriminator_tag_constraints(field: DataModelFieldBase) -> tuple[ConstraintsBase, dict[str, Any], str] | None:
    """Return the constraints of a tag an output model writes as ``Field()`` arguments, their values and pattern."""
    if not field.SUPPORTS_FIELD_CONSTRAINTS or not isinstance(constraints := field.constraints, ConstraintsBase):
        return None
    values = constraints.model_dump(by_alias=True, exclude_unset=True)
    if (pattern := values.pop(TAG_LITERAL_UNTAKEN_KEYWORD, None)) is None:
        return None
    return constraints, values, pattern


def _tag_satisfies_pattern(value: DiscriminatorValue, pattern: str) -> bool:
    """Return whether a tag value satisfies a pattern as ``re.search`` finds it, which a non-string always does.

    A pattern Python cannot compile counts as satisfied.
    """
    try:
        return not isinstance(value, str) or re.search(pattern, value) is not None
    except re.error:
        return True


def _drop_discriminator_tag_patterns(
    builder: GraphBuilder,
    tag_fields: Iterable[tuple[DataModelFieldBase, Sequence[DiscriminatorValue]]],
) -> None:
    """Write the tags of a union without patterns where an output model writes constraints as ``Field()`` arguments.

    Pydantic checks a pattern on the tag's ``Literal`` by validating the value again as a ``str``, which hides the
    ``Literal`` from the discriminator, and the ``Literal`` decides the tag anyway once every tag value satisfies the
    pattern. A tag value outside its pattern leaves every pattern of the union, as the schema rejects that variant.
    The other constraints stay.
    """
    patterned = [
        (field, found, tag_values)
        for field, tag_values in tag_fields
        if (found := _discriminator_tag_constraints(field)) is not None
    ]
    if not all(
        _tag_satisfies_pattern(value, pattern) for _, (_, _, pattern), tag_values in patterned for value in tag_values
    ):
        return
    for field, (constraints, values, _), _ in patterned:
        builder.set_field_constraints(field, type(constraints).model_validate(values))
        field.invalidate_semantic_caches()


def _iter_nested_discriminator_variants(
    data_types: Iterable[DataType],
    field_name: str,
    *,
    field_name_mode: Literal["source-name", "python-or-original-name"],
) -> Iterator[tuple[DataType, DataModelFieldBase, Sequence[DiscriminatorValue]]]:
    """Yield the variants that nested choices give a union, with their tag fields and tag values.

    Such a variant keeps the tag it declares or inherits, so the field giving it is its own one, else the inherited
    one, which the empty subclass of a reused model also reads. A union's lone variant is not one of them: its model
    keeps the tag field it writes, and ``settle_discriminated_unions`` writes the union without ``discriminator=``
    where that field is no tag Pydantic reads.
    """
    for data_type, can_update, _ in _iter_discriminator_data_types(data_types):
        source = data_type.reference.source if data_type.reference else None
        if can_update or not isinstance(source, DataModel):
            continue
        for tag_field, value in islice(
            _iter_discriminator_fields(
                chain(source.fields, source.iter_all_fields()), field_name, field_name_mode=field_name_mode
            ),
            1,
        ):
            yield data_type, tag_field, (value,)


def _is_lone_variant(field: DataModelFieldBase) -> bool:
    """Return whether a field's type names the one variant of a union that ``discriminator=`` is written for.

    Its type is the variant itself, a ``$ref`` or a type that is no model at all, so there is no list of variants to
    read, and the variant itself is read.
    """
    return field.SUPPORTS_DISCRIMINATOR and not field.data_type.data_types


def _is_enum_tag(tag_field: DataModelFieldBase) -> bool:
    """Return whether a variant's tag field is typed by its Enum, which Pydantic does not read as a ``Literal``."""
    return not tag_field.data_type.literals and tag_field.data_type.find_source(Enum) is not None


def _find_tag_enum(model: DataModel, field_name: str, tag_name: str, *, use_field_name: bool) -> Enum | None:
    """Return the Enum typing a variant's tag field, of the field itself or else of its bases."""
    return next(
        (
            enum_source
            for field in model.fields
            if _tag_field_matches(field, "source-name" if use_field_name else "python-or-original-name", tag_name)
            and (enum_source := field.data_type.find_source(Enum))
        ),
        None,
    ) or _get_enum_from_base(model, field_name, use_field_name=use_field_name)


def _is_enum_member_tag(
    model: DataModel, field_name: str, tag_name: str, value: DiscriminatorValue, *, use_field_name: bool
) -> bool:
    """Return whether the Enum typing a variant's tag field has a member for ``value``, written as that member."""
    enum_source = _find_tag_enum(model, field_name, tag_name, use_field_name=use_field_name)
    return enum_source is not None and enum_source.find_member(value, coerce_strings=True) is not None


def _members_equal_values(enum_source: Enum) -> bool:
    """Return whether the members of an Enum equal their values at runtime, as Pydantic compares discriminator choices.

    So do the members of an ``IntEnum``, a ``StrEnum`` and an Enum that subclasses its value type; a member of a plain
    Enum equals only itself.
    """
    return isinstance(enum_source, (IntEnum, StrEnum)) or any(
        base.reference is None and base.import_ is None for base in enum_source.base_classes
    )


def _written_enum_member_keys(
    model: DataModel, field_name: str, tag_name: str, values: list[DiscriminatorValue], *, use_field_name: bool
) -> list[object]:
    """Return the keys of the choices that the members a variant is written with make (``enum_discriminator_members``).

    A member is its value where members equal their values, else only itself (its Enum and its value). A variant tagged
    by no member takes every member, so its tags are their values whatever the Enum compares. A tag field that already
    is the one ``Literal`` of the variant's tag is kept as it is.
    """
    literal_kept = any(
        _tag_field_matches(field, "source-name" if use_field_name else "python-or-original-name", tag_name)
        and len(literals := field.data_type.literals) == 1
        and same_tag(literals[0], values[0], "legacy-python-equality")
        for field in model.fields
    )
    if (
        literal_kept
        or (enum_source := _find_tag_enum(model, field_name, tag_name, use_field_name=use_field_name)) is None
    ):
        return []
    tags = enum_discriminator_members(enum_source, values)
    values_of_members = [tag_key(member.value, "legacy-python-equality") for member in tags.members]
    keys: list[object] = (
        values_of_members if _members_equal_values(enum_source) else [(id(enum_source), v) for v in values_of_members]
    )
    return [*keys, *values_of_members] if tags.rule == "all-members" else keys


def _discriminator_variants_are_valid(  # noqa: PLR0913
    data_types: Iterable[DataType],
    field_name: str,
    mapping: dict[str, str],
    *,
    inline_models: _InlineVariantTags,
    original_name: str | None = None,
    enum_member_tags: bool = False,
) -> bool:
    discriminator_value_owners: dict[object, int] = {}
    for data_type, can_update_discriminator, owner in _iter_discriminator_data_types(data_types):
        if not data_type.reference and data_type.type == NONE:
            continue
        if _is_discriminator_container(data_type) or not data_type.reference:
            return False
        discriminator_model = data_type.reference.source
        if (
            not isinstance(discriminator_model, DataModel)
            or not discriminator_model.SUPPORTS_DISCRIMINATOR
            or _is_discriminator_wrapper(discriminator_model)
        ):
            return False

        discriminator_values = _get_discriminator_values(
            discriminator_model,
            original_name if original_name is not None else field_name,
            mapping,
            inline_models=inline_models,
            require_literal=not can_update_discriminator,
            field_name_mode="source-name" if original_name is not None else "python-or-original-name",
        )
        if not discriminator_values or not all(
            is_tag_value(value, bool_literals=discriminator_model.SUPPORTS_BOOLEAN_LITERAL)
            or (
                enum_member_tags
                and not discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR
                and _is_enum_member_tag(
                    discriminator_model,
                    field_name,
                    original_name if original_name is not None else field_name,
                    value,
                    use_field_name=original_name is not None,
                )
            )
            for value in discriminator_values
        ):
            return False
        keys = [tag_key(value, "legacy-python-equality") for value in discriminator_values]
        if (
            enum_member_tags
            and can_update_discriminator
            and not discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR
        ):
            keys += _written_enum_member_keys(
                discriminator_model,
                field_name,
                original_name if original_name is not None else field_name,
                discriminator_values,
                use_field_name=original_name is not None,
            )
        for key in keys:
            if (previous_owner := discriminator_value_owners.get(key)) is not None and previous_owner != owner:
                return False
            discriminator_value_owners[key] = owner
    return True


def inline_variant_models(parser: InlineVariantHost) -> _InlineVariantTags:
    """Return the tag fields of the inline variants that may take the tag they inherit, decided once from every union.

    An inline variant has no name to be tagged by, so its schema path ends in an internal marker, which no payload
    carries. It takes the tag it inherits (``variant_tags``) unless a union that lists it would then list two variants
    with one tag, which turns that union into a plain union and takes the tags from its named variants. Every
    variant of such a union keeps the marker, and so does a variant that another union lists. The decision reads the
    unions before any of them writes a tag, as a variant takes its tag from the first union that writes one, and the
    variants take the tags it read: a tag another union writes into a base later is not one the variant inherits.
    """
    if (decided := parser._inline_variant_models) is not None:  # noqa: SLF001
        return decided
    inline: dict[int, tuple[tuple[DataModelFieldBase, DiscriminatorValue], ...]] = {}
    clashing: set[int] = set()
    for model in parser.results:
        for field in model.fields:
            match field.extras.get("discriminator"):
                case {"propertyName": str() as property_name} as discriminator if property_name:
                    pass
                case _:
                    continue
            variants = {
                id(source): tuple(
                    (tag_field, value)
                    for tag_field in source.iter_all_fields()
                    if (value := _get_discriminator_field_value(tag_field)) is not None
                )
                for data_type, _, _ in _iter_discriminator_data_types(field.data_type.data_types)
                if data_type.reference
                and isinstance(source := data_type.reference.source, DataModel)
                and SPECIAL_PATH_MARKER in _get_schema_path(source).rpartition("/")[2]
            }
            if not variants:
                continue
            field_name, _ = parser.model_resolver.get_valid_field_name_and_alias(
                field_name=property_name, model_type=parser.field_name_model_type
            )
            inline |= variants
            if [
                _discriminator_variants_are_valid(
                    field.data_type.data_types,
                    field_name,
                    discriminator.get("mapping", {}),
                    inline_models=models,
                    original_name=property_name if field.SUPPORTS_DISCRIMINATOR else None,
                    enum_member_tags=parser.use_enum_values_in_discriminator,
                )
                for models in ({}, variants)
            ] == [True, False]:
                clashing.update(variants)
    parser._inline_variant_models = decided = {  # noqa: SLF001
        key: tag_fields for key, tag_fields in inline.items() if key not in clashing
    }
    return decided


def _get_single_discriminator_default(
    data_type: DataType,
    enum_source: Enum | None,
    expected_value: DiscriminatorValue | None,
) -> DiscriminatorValue | Member | None:
    """Return the only valid discriminator default after resolving its type."""
    if len(literals := data_type.literals) == 1:
        return literals[0]
    if (
        len(data_type.enum_member_literals) != 1
        or enum_source is None
        or (member := enum_source.find_member(expected_value, coerce_strings=True)) is None
    ):
        return None
    return member


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
            value_type = "int" if isinstance(enums[0], IntEnum) else "str" if isinstance(enums[0], StrEnum) else None
            kind = decide_enum_kind("merged", first_specialized=value_type is not None)
            merged_enum = _enum_class(kind, enums[0].__class__)(
                fields=[
                    f
                    for e in enums
                    for f in e.fields
                    if value_type is None
                    or holds_inherited_member_value(value_type, get_raw_enum_member_value(f.default))
                ],
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


def reuse_model(parser: ReuseHost, models: list[DataModel], require_update_action_models: list[str]) -> None:
    """Replace the duplicates among the models of a module by empty subclasses of the first, or remove them."""
    if not parser.reuse_model or parser.reuse_scope == ReuseScope.Tree:
        return
    duplicates = []
    require_update = parser._recursion_facts["require_update"]  # noqa: SLF001
    updated_models: list[DataModel] = []
    reuse_candidates = (
        model
        for model in parser._reuse_optimization_context.eligible_models(models.copy())  # noqa: SLF001
        if not (
            skips_root_model(REUSE_MODEL, collapse_root_models=parser.collapse_root_models)
            and parser._is_root_model(model)  # noqa: SLF001
        )
    )
    typed_map_values = parser._typed_map_value_reference_ids  # noqa: SLF001
    canonical_models: dict[int, DataModel] = {}
    shapes = parser._model_shapes(REUSE_MODEL)  # noqa: SLF001
    for first_model, duplicate_model in shapes.iter_matching_duplicates(reuse_candidates, keyed=True):
        cached_model = canonical_models.get(id(first_model), first_model)
        model = duplicate_model
        if id(cached_model.reference) in typed_map_values and id(model.reference) not in typed_map_values:
            cached_model, model = model, cached_model
            canonical_models[id(first_model)] = cached_model
        cached_model_reference = cached_model.reference
        if (
            id(model.reference) in typed_map_values
            and not parser.collapse_reuse_models
            and parser.generation_store.has_users_in_other_modules(model)
        ):
            # A module written earlier names the value, as a field of a collapsed root model does, so it stays.
            continue
        if isinstance(model, Enum) or parser.collapse_reuse_models:
            redirect_removed_reuse_users(parser, model, cached_model)
            duplicates.append(model)
        elif id(model.reference) in typed_map_values:
            parser._graph_builder.redirect_model_reference_users(model, models, cached_model_reference)  # noqa: SLF001
            duplicates.append(model)
        else:
            inherited_model = model.create_reuse_model(cached_model_reference)
            parser._graph_builder.redirect_model_reference_users(model, models, inherited_model.reference)  # noqa: SLF001
            if model_key_of(cached_model_reference) in require_update:
                updated_models.append(inherited_model)
            replace_model_in_list(models, model, inherited_model)
            parser._graph_builder.replace_model(model, inherited_model)  # noqa: SLF001

    for duplicate in duplicates:
        models.remove(duplicate)
        parser._graph_builder.retire_model(duplicate)  # noqa: SLF001
    if updated_models:
        require_update_action(parser, updated_models, require_update_action_models)


def redirect_removed_reuse_users(parser: ReuseHost, duplicate: DataModel, canonical: DataModel) -> None:
    """Redirect every user of a duplicate that reuse removes, remembering what each user referred to.

    Modules processed earlier already imported the duplicate, so their imports are corrected after processing.
    """
    users = parser._removed_reuse_users  # noqa: SLF001
    for user in redirect_collapsed_reuse_users(
        parser._graph_builder,  # noqa: SLF001
        parser._collapsed_reuse_models,  # noqa: SLF001
        parser._collapsed_reuse_users,  # noqa: SLF001
        duplicate,
        canonical,
    ):
        users.setdefault(id(user), (user, []))[1].append(duplicate.reference.path)


def collapse_module_root_models(parser: CollapseHost, state: ModuleStateView) -> None:
    """Inline the root models of the module; reaching the recursion limit raises a retryable error."""
    try:
        collapse_root_models(
            parser,
            state.model_list,
            state.unused_models,
            state.imports,
            state.scoped_model_resolver,
            state.model_path_to_module_name,
            is_init=state.is_init,
            internal_modules=state.internal_modules,
        )
    except _CollapseRootModelsRecursionError:
        raise
    except RecursionError as exc:
        if (mode := check_mode(CheckArea.MODEL_GRAPH)) is not None:
            mode.violation(Error, "collapsing root models reached the recursion limit instead of stopping at a cycle")
        raise _CollapseRootModelsRecursionError from exc


def collapse_root_models(  # noqa: PLR0913, PLR0917
    parser: CollapseHost,
    models: list[DataModel],
    unused_models: list[DataModel],
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: dict[str, str] | None = None,
    *,
    is_init: bool = False,
    internal_modules: set[ModulePath] | None = None,
) -> None:
    """Inline the root models that the models of a module reference, under ``--collapse-root-models``."""
    if not parser.collapse_root_models:
        return

    with parser.generation_store._collapse_root_reference_scope():  # noqa: SLF001
        collapse_root_models_in_scope(
            parser,
            models,
            unused_models,
            imports,
            scoped_model_resolver,
            {} if model_path_to_module_name is None else model_path_to_module_name,
            is_init=is_init,
            internal_modules=internal_modules or set(),
        )


def has_other_root_references(parser: CollapseHost, reference: Reference, data_type: DataType) -> bool:
    """Return whether data types other than ``data_type`` name the root model of ``reference``.

    Under the ``MODEL_GRAPH`` check with a report file, record when the answer over the emitted models differs.
    """
    store = parser.generation_store
    if (answer := store._root_collapse_has_data_type_references(reference, excluded_data_type=data_type)) is None:  # noqa: SLF001
        answer = store.index.has_data_type_references_other_than(reference, data_type)
    if (
        (mode := check_mode(CheckArea.MODEL_GRAPH)) is not None
        and mode.report_path is not None
        and answer != store.index.has_data_type_references_other_than(reference, data_type, universe="emitted")
    ):
        path = redact_reference(reference.path)
        users = "are all models the output does not write" if answer else "are all models built after parsing"
        mode.record(
            f"{CheckArea.MODEL_GRAPH.value}: when collapsing root models, the other users of {path} {users}",
            kind="universe-differs",
            path=path,
            parsed=answer,
        )
    return answer


def collapse_root_models_in_scope(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915, PLR0917
    parser: CollapseHost,
    models: list[DataModel],
    unused_models: list[DataModel],
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: dict[str, str],
    *,
    is_init: bool,
    internal_modules: set[ModulePath],
) -> None:
    """Collapse the root models that this module's models reference.

    A holder of a root model whose root type is a reference to another model gets the output it would get if it
    referenced that model directly. Under the ``parent`` strategy that model takes the root model's name in its
    own module and replaces the root model, decided at the root model's first holder from the references the
    schema gives, so that every holder follows the same decision. A root model that a schema validator rule names
    stays written after its holders inline it: the rule checks values through its class. A root model that its inner
    model replaces leaves; once every module is processed, the rules name the inner model instead
    (``name_inner_models_in_rules``).
    """
    generation_store = parser.generation_store
    generation_index = generation_store.index
    graph_builder = parser._graph_builder  # noqa: SLF001
    circular_root_models = parser._recursion_facts["circular_roots"]  # noqa: SLF001
    rule_named_roots = frozenset(
        model_key_of(reference)
        for holder in (parser.results if parser.generate_schema_validators else ())
        for rule_data_type in _parsed_rule_data_types(holder)
        if (reference := rule_data_type.reference) is not None
    )
    is_root_model = parser._is_root_model  # noqa: SLF001
    collapses_list_item_root_models = parser._collapses_list_item_root_models  # noqa: SLF001
    redirected_holders: list[tuple[DataModel, DataType, Import | None]] = []

    for model in models:  # noqa: PLR1702
        for model_field in model.fields:
            for data_type in model_field.data_type.all_data_types:
                reference = data_type.reference
                if (
                    not reference
                    or not is_root_model(root_type_model := cast("DataModel", reference.source))
                    or parser._keeps_root_model_named(root_type_model, circular_root_models)  # noqa: SLF001
                ):
                    continue
                root_type_field = root_type_model.fields[0]
                if (
                    parser.field_constraints
                    and _has_root_field_constraints(root_type_field)
                    and any(
                        data_type.is_dict or data_type.is_union or data_type.is_list
                        for data_type in model_field.data_type.all_data_types
                    )
                ):
                    continue

                if root_type_field.data_type.reference:
                    if parser.collapse_root_models_name_strategy is None:
                        continue

                    inner_reference = root_type_field.data_type.reference
                    inner_model = cast("DataModel", inner_reference.source)

                    if (
                        parser.collapse_root_models_name_strategy == CollapseRootModelsNameStrategy.Parent
                        and id(root_type_model) not in parser._replaced_root_models  # noqa: SLF001
                    ):
                        root_model_wrappers, direct_refs = generation_index.root_collapse_reference_usage(
                            inner_reference,
                            excluded_model=root_type_model,
                            is_root_model=is_root_model,
                        )

                        if len(root_model_wrappers) > 1:
                            warn_as_parser_base(
                                f"Cannot apply 'parent' strategy for '{inner_model.class_name}' - "
                                f"it is referenced by multiple root models: "
                                f"{[m.class_name for m in root_model_wrappers]}. Skipping collapse.",
                                UserWarning,
                                stacklevel=2,
                            )
                            continue

                        if direct_refs:
                            warn_as_parser_base(
                                f"Cannot apply 'parent' strategy for '{inner_model.class_name}' - "
                                f"it is directly referenced by non-wrapper models. Skipping collapse.",
                                UserWarning,
                                stacklevel=2,
                            )
                            continue

                        replace_root_model_by_inner_model(
                            parser, root_type_model, inner_model, model_path_to_module_name
                        )

                    has_remaining_root_references = has_other_root_references(
                        parser, root_type_model.reference, data_type
                    )
                    graph_builder.collapse_root_data_type(data_type, inner_reference)
                    carry_const_narrowings(parser, root_type_field.data_type, data_type)

                    redirected_holders.append((
                        model,
                        data_type,
                        imports.reference_paths.get(root_type_model.path),
                    ))
                    if not has_remaining_root_references and (
                        id(root_type_model) in parser._replaced_root_models  # noqa: SLF001
                        or model_key_of(root_type_model.reference) not in rule_named_roots
                    ):
                        unused_models.append(root_type_model)

                    continue

                if (
                    not collapses_list_item_root_models
                    and isinstance(list_data_type := data_type.parent, DataType)
                    and list_data_type.is_list
                ):
                    continue

                # set copied data_type
                source_module_name = _get_model_module_name(root_type_model, model_path_to_module_name)
                target_module_name = _get_model_module_name(model, model_path_to_module_name)
                copied_data_type = (
                    parser._copy_model_type(root_type_field.data_type)  # noqa: SLF001
                    if source_module_name != target_module_name
                    else graph_builder.inline_shared(root_type_field.data_type, data_type)
                )
                carry_const_narrowings(parser, root_type_field.data_type, copied_data_type)
                has_remaining_root_references = has_other_root_references(parser, root_type_model.reference, data_type)

                replacement_context = None
                if isinstance(field_ := data_type.parent, parser.data_model_field_type):
                    # for field
                    # override empty field by root-type field
                    graph_builder.set_field_extras(model_field, {**root_type_field.extras, **model_field.extras})
                    if root_type_field.runtime_expression_imports:
                        model_field._set_runtime_expression_imports(  # noqa: SLF001
                            (
                                *model_field.runtime_expression_imports,
                                *runtime_expression_imports(model_field.extras),
                            )
                        )
                    graph_builder.apply_const(model_field, refresh="deferred")

                    if parser.field_constraints:
                        graph_builder.set_field_constraints(
                            model_field,
                            ConstraintsBase.merge_constraints(root_type_field.constraints, model_field.constraints),
                            type_dependent=root_type_field.__dict__.get(_TYPE_DEPENDENT_CONSTRAINTS_KEY),
                        )

                    replacement_context = generation_store._replace_data_type_and_detach_data_type_ref(  # noqa: SLF001
                        data_type,
                        copied_data_type,
                        owner=field_,
                        replacement_kind="field",
                    )

                elif isinstance(parent_data_type := data_type.parent, DataType) and parent_data_type.is_list:
                    if parser.field_constraints:
                        graph_builder.set_field_constraints(
                            model_field,
                            ConstraintsBase.merge_constraints(root_type_field.constraints, model_field.constraints),
                        )
                    discriminator = root_type_field.extras.get("discriminator")
                    if discriminator and root_type_field.SUPPORTS_DISCRIMINATOR:
                        has_any_variant = any(_is_any_variant(dt) for dt in copied_data_type.data_types)
                        if not has_any_variant:  # pragma: no branch
                            prop_name = (
                                discriminator.get("propertyName") if isinstance(discriminator, dict) else discriminator
                            )
                            mapping = discriminator.get("mapping", {}) if isinstance(discriminator, dict) else {}
                            field_name, _ = parser.model_resolver.get_valid_field_name_and_alias(
                                field_name=prop_name,
                                model_type=parser.field_name_model_type,
                            )
                            if _discriminator_variants_are_valid(
                                copied_data_type.data_types,
                                field_name,
                                mapping,
                                inline_models=inline_variant_models(parser),
                                enum_member_tags=parser.use_enum_values_in_discriminator,
                            ):
                                graph_builder.set_type_discriminator(copied_data_type, field_name)
                    replacement_context = generation_store._replace_data_type_and_detach_data_type_ref(  # noqa: SLF001
                        data_type,
                        copied_data_type,
                        owner=parent_data_type,
                        replacement_kind="nested",
                    )

                elif isinstance(parent_data_type := data_type.parent, DataType):
                    # for data_type
                    replacement_context = generation_store._replace_data_type_and_detach_data_type_ref(  # noqa: SLF001
                        data_type,
                        copied_data_type,
                        owner=parent_data_type,
                        replacement_kind="nested",
                    )
                else:  # pragma: no cover
                    continue

                if replacement_context is None:  # pragma: no cover
                    continue
                model_field.inherit_unconstrained_union_members(root_type_field)
                model_field.inherit_untaken_constraints(
                    root_type_field, whole_type=isinstance(field_, parser.data_model_field_type)
                )
                if isinstance(owner := data_type.parent, DataType) and _reaches_data_type(copied_data_type, owner):
                    msg = (
                        f"Inlining {root_type_model.class_name} into {model.class_name} would make a data type "
                        "contain itself"
                    )
                    raise _CollapseRootModelsRecursionError(msg)
                producer = ImportProducer("collapse-root-models", model, model_field)
                with replacement_context:
                    for d in copied_data_type.all_data_types:
                        _register_data_type_import(
                            d,
                            model,
                            imports,
                            scoped_model_resolver,
                            model_path_to_module_name,
                            ledger=parser._import_ledger,  # noqa: SLF001
                            producer=producer,
                        )

                    original_field = get_most_of_parent(data_type, DataModelFieldBase)
                    if original_field:  # pragma: no cover
                        # TODO: Improve detection of reference type
                        # Use list instead of set because Import is not hashable
                        excluded_imports = [IMPORT_OPTIONAL, IMPORT_UNION]
                        field_imports = [i for i in original_field.imports if i not in excluded_imports]
                        parser._import_ledger.append(  # noqa: SLF001
                            imports, field_imports, ImportProducer("collapse-root-models", model, original_field)
                        )

                parser._import_ledger.remove_referenced_imports(imports, root_type_model.path, producer)  # noqa: SLF001
                if (
                    not has_remaining_root_references
                    and model_key_of(root_type_model.reference) not in rule_named_roots
                ):
                    unused_models.append(root_type_model)

    import_collapsed_inner_models(
        parser,
        models,
        redirected_holders,
        imports,
        scoped_model_resolver,
        model_path_to_module_name,
        is_init=is_init,
        internal_modules=internal_modules,
    )


def carry_const_narrowings(parser: CollapseHost, source: DataType, copied: DataType) -> None:
    """Give each node of ``copied`` that collapse inlines the const narrowing of the ``source`` node it copies.

    A copy has the shape of its source; a node that the copy shares with its source already has its narrowing.
    """
    if not parser._const_narrowed_values:  # noqa: SLF001
        return
    pending = [(source, copied)]
    while pending:
        source_node, copied_node = pending.pop()
        if (entry := parser._const_narrowed_values.get(id(source_node))) is not None and entry[0] is source_node:  # noqa: SLF001
            parser._const_narrowed_values[id(copied_node)] = (copied_node, entry[1])  # noqa: SLF001
        pending.extend(zip(source_node.data_types, copied_node.data_types, strict=True))
        if (key := source_node.dict_key) is not None:
            pending.append((key, cast("DataType", copied_node.dict_key)))


def emitting_module_name(parser: CollapseHost, model: DataModel, model_path_to_module_name: dict[str, str]) -> str:
    """Return the name of the module whose live model list holds a model, found by identity.

    A model that a module's processing puts into its list after modules are structured, such as a reuse subclass,
    is looked up in the live lists and remembered.
    """
    if (module_name := parser._emitting_module_names.get(id(model))) is not None:  # noqa: SLF001
        return module_name
    for module_name, module_models in parser._emitting_module_lists:  # noqa: SLF001
        if any(module_model is model for module_model in module_models):
            parser._emitting_module_names[id(model)] = module_name  # noqa: SLF001
            return module_name
    return _get_model_module_name(model, model_path_to_module_name)


def take_recursion_facts(parser: CollapseHost, model: DataModel, path_owner: DataModel) -> None:
    """Give ``model``, which takes the path of ``path_owner``, the recursion facts of ``path_owner``, not its own.

    Legacy row ``legacy-recursion-follows-path``: the facts were reference paths that only the relocation into
    ``_internal`` modules followed, so a model that took another model's path took that model's facts.
    """
    recursion_facts = parser._recursion_facts  # noqa: SLF001
    key, owner_key = model_key_of(model.reference), model_key_of(path_owner.reference)

    def taken(keys: frozenset[ModelKey]) -> frozenset[ModelKey]:
        if (key in keys) == (owner_key in keys):
            return keys
        return keys ^ {key}

    parser._recursion_facts = RecursionFacts(  # noqa: SLF001
        require_update=taken(recursion_facts["require_update"]),
        circular_roots=taken(recursion_facts["circular_roots"]),
    )


def replace_root_model_by_inner_model(
    parser: CollapseHost,
    root_model: DataModel,
    inner_model: DataModel,
    model_path_to_module_name: dict[str, str],
) -> None:
    """Give the inner model the class name of the root model it replaces, in the module that emits it.

    The inner model also takes the root model's path, which tag values read, as the root model leaves the
    output. Modules processed earlier imported the inner model by its old name under its old path.
    """
    old_class_name, old_path = inner_model.class_name, inner_model.path
    inner_module_name = emitting_module_name(parser, inner_model, model_path_to_module_name)
    parser._graph_builder.rename_model(  # noqa: SLF001
        inner_model,
        class_name=root_model.class_name,
        new_path=root_model.reference.path,
    )
    parser._replaced_root_models[id(root_model)] = root_model, inner_model  # noqa: SLF001
    _share_path_identity(root_model, inner_model)
    take_recursion_facts(parser, inner_model, root_model)
    _assign_module_name(model_path_to_module_name, inner_model, inner_module_name)
    _assign_module_name(model_path_to_module_name, root_model, inner_module_name)
    if inner_model.class_name != old_class_name:
        record_renamed_type(parser, inner_model, _RenamedType(old_class_name, old_path))


def import_collapsed_inner_models(  # noqa: PLR0913, PLR0917
    parser: CollapseHost,
    models: list[DataModel],
    redirected_holders: list[tuple[DataModel, DataType, Import | None]],
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: dict[str, str],
    *,
    is_init: bool,
    internal_modules: set[ModulePath],
) -> None:
    """Import the inner models that collapse redirected this module's holders to, as direct references would.

    Each holder drops its import of the root model. A name that no import binds any more, and the name of a root
    of this module that its inner model replaced, are free for the inner model, which is imported where the
    holder's module does not emit it.
    """
    if not redirected_holders:
        return
    root_imports = [root_import for *_, root_import in redirected_holders if root_import is not None]
    parser._import_ledger.remove(imports, root_imports, _COLLAPSE_ROOT_MODELS)  # noqa: SLF001
    parser._import_ledger.release_names(  # noqa: SLF001
        imports,
        scoped_model_resolver,
        {root_import.binding_name for root_import in root_imports}.difference(_iter_import_bindings(imports)).union(
            names_left_by_replaced_roots(parser, models, keyed_models(module_members(parser, models), "module members"))
        ),
    )
    for holder, data_type, _ in redirected_holders:
        reference = cast("Reference", data_type.reference)
        holder_module_name = _get_model_module_name(holder, model_path_to_module_name)
        inner_module_name = (
            holder_module_name
            if (inner_model := reference.source) is None
            else emitting_module_name(parser, cast("DataModel", inner_model), model_path_to_module_name)
        )
        set_reference_expression(
            parser,
            data_type,
            holder_module_name,
            f"{inner_module_name}.{reference.short_name}" if inner_module_name else reference.short_name,
            imports,
            scoped_model_resolver,
            same_module=inner_module_name == holder_module_name,
            init=is_init,
            internal_modules=internal_modules,
            producer=ImportProducer("collapse-root-models", holder),
        )


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
                        _rename_field_preserving_alias(parser._graph_builder, field, new_field_name)  # noqa: SLF001

            if (
                parser.data_model_type.NORMALIZES_FIELD_NAMES
                and (current_name := field.name) in parser.builtin_names
                and any(_is_builtin_type_collision(current_name, dt) for dt in field.data_type.all_data_types)
            ):
                parser._graph_builder.rename_field(field, f"{current_name}_", alias=field_name)  # noqa: SLF001


def restore_inherited_field_names_for_unique_aliases(parser: ModulesHost, models: list[DataModel]) -> None:
    """Reserve inherited Python names for output models with unique wire aliases."""
    for model in models:
        if model.REQUIRES_UNIQUE_FIELD_ALIASES:
            _restore_inherited_field_names_for_unique_aliases(
                parser._graph_builder,  # noqa: SLF001
                model.fields,
                get_inherited_fields(_find_base_classes(model)),
            )


def apply_discriminator_type(  # noqa: PLR0912, PLR0914, PLR0915
    parser: DiscriminatorHost,
    models: list[DataModel],
    imports: Imports,
) -> None:
    """Write the tags that the discriminated unions of a module give their variants, and tag msgspec unions."""
    tagged_unions = parser.use_msgspec_tagged_unions
    tag_by_payload_name = tagged_unions or parser.tag_msgspec_by_payload_name
    unions: list[_DiscriminatorUnion] = []
    for model in models:  # noqa: PLR1702
        for field in model.fields:
            match field.extras.get("discriminator"):
                case {"propertyName": str() as property_name} as discriminator if property_name:
                    pass
                case _:
                    continue
            inline_models = inline_variant_models(parser)
            field_name, alias = parser.model_resolver.get_valid_field_name_and_alias(
                field_name=property_name, model_type=parser.field_name_model_type
            )
            mapping = discriminator.get("mapping", {})
            discriminator = {**discriminator, "propertyName": field_name}
            parser._graph_builder.set_field_extra(field, "discriminator", discriminator)  # noqa: SLF001
            # Any type cannot be a discriminated union variant (Pydantic v2 rejects it)
            has_any_variant = any(_is_any_variant(dt) for dt in field.data_type.data_types)
            if has_any_variant:  # pragma: no cover
                parser._graph_builder.drop_field_discriminator(field)  # noqa: SLF001
                continue
            if not _discriminator_variants_are_valid(
                field.data_type.data_types,
                field_name,
                mapping,
                inline_models=inline_models,
                original_name=property_name if field.SUPPORTS_DISCRIMINATOR else None,
                enum_member_tags=parser.use_enum_values_in_discriminator,
            ):
                parser._graph_builder.drop_field_discriminator(field)  # noqa: SLF001
                continue
            nested_variants = [
                *_iter_nested_discriminator_variants(
                    field.data_type.data_types,
                    property_name if field.SUPPORTS_DISCRIMINATOR else field_name,
                    field_name_mode="source-name" if field.SUPPORTS_DISCRIMINATOR else "python-or-original-name",
                )
            ]
            enum_tagged: list[DataType] = []
            tag_fields: list[tuple[DataModelFieldBase, Sequence[DiscriminatorValue]]] = []
            for data_type, tag_field, values in nested_variants:
                if (
                    field.SUPPORTS_DISCRIMINATOR
                    and not parser.use_enum_values_in_discriminator
                    and _is_enum_tag(tag_field)
                ):
                    enum_tagged.append(data_type)
                else:
                    tag_fields.append((tag_field, values))
            set_nested_discriminator_literal_defaults(parser, tag_fields)

            if field.SUPPORTS_DISCRIMINATOR:
                field_name = parser._get_discriminator_field_name(  # noqa: SLF001
                    field.data_type.data_types, property_name, field_name
                )
                discriminator["propertyName"] = field_name
                if field_name != property_name and alias is None:
                    alias = property_name

            tags: list[_DiscriminatorTag] = []
            union_name = model.class_name if parser._is_root_model(model) else f"{model.class_name}.{field.name}"  # noqa: SLF001
            collapsed_unions = get_collapsed_unions(parser, model, field)
            field_name_mode: Literal["python-name", "python-or-original-name"] = (
                "python-name" if field.SUPPORTS_DISCRIMINATOR else "python-or-original-name"
            )
            has_reused_variant = any(
                isinstance(source := data_type.reference.source, DataModel) and source.reused_reference is not None
                for data_type in field.data_type.data_types
                if data_type.reference
            )
            for index, data_type in (
                *enumerate(field.data_type.data_types),
                *((None, enum_tagged_type) for enum_tagged_type in enum_tagged),
            ):
                if not data_type.reference:  # pragma: no cover
                    continue
                discriminator_model = data_type.reference.source
                if (
                    not isinstance(discriminator_model, DataModel)
                    or not discriminator_model.SUPPORTS_DISCRIMINATOR
                    or _is_discriminator_wrapper(discriminator_model)
                ):  # pragma: no cover
                    continue

                annotates_new_tag = (
                    index is not None
                    and has_reused_variant
                    and not (tagged_unions and discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR)
                )
                # A resolved Python name can equal a different field's wire name.
                discriminator_values = _get_discriminator_values(
                    discriminator_model,
                    field_name,
                    mapping,
                    inline_models=inline_models,
                    require_literal=index is None,
                    field_name_mode=field_name_mode,
                )
                if index is not None:
                    record_variant_discriminator_tags(
                        parser,
                        discriminator_model,
                        union_name,
                        property_name=property_name,
                        field_name=field_name,
                        mapping=mapping,
                        values=discriminator_values,
                        inline_models=inline_models,
                        field_name_mode=field_name_mode,
                    )
                    for collapsed_name, collapsed_types, collapsed_mapping in (
                        (union_name, field.data_type.data_types, mapping),
                        *collapsed_unions,
                    ):
                        record_collapsed_variant_tags(
                            parser,
                            discriminator_model,
                            collapsed_name,
                            collapsed_types[index],
                            property_name=property_name,
                            field_name=field_name,
                            mapping=collapsed_mapping,
                            values=discriminator_values,
                            inline_models=inline_models,
                            field_name_mode=field_name_mode,
                        )
                has_one_literal = False
                for discriminator_field in discriminator_model.fields:
                    if not _tag_field_matches(discriminator_field, field_name_mode, field_name):
                        continue
                    expected_value = discriminator_values[0] if discriminator_values else None
                    if len(literals := discriminator_field.data_type.literals) == 1 and same_tag(
                        literal := literals[0], expected_value, "legacy-python-equality"
                    ):
                        has_one_literal = True
                        tag_fields.append((discriminator_field, (literal,)))
                        if not has_reused_variant:
                            record_reused_variant_tag_field(parser, discriminator_field, annotates_tag=False)
                        match discriminator_model:
                            case _ if discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
                                tags.append((discriminator_model, discriminator_field, literal))
                            case _:
                                set_tag_literal_default(
                                    parser,
                                    discriminator_field,
                                    literal,
                                    "tag",
                                )
                        # Found the discriminator field, no need to keep looking
                        break

                    if (
                        not tagged_unions
                        and discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR
                        and (const_value := discriminator_field.extras.get("const")) is not None
                        and same_tag(const_value, expected_value, "legacy-python-equality")
                    ):
                        has_one_literal = True
                        tags.append((discriminator_model, discriminator_field, const_value))
                        break

                    enum_source = discriminator_field.data_type.find_source(Enum)
                    if parser.use_enum_values_in_discriminator:
                        enum_source = enum_source or _get_enum_from_base(
                            discriminator_model, field_name, use_field_name=field.SUPPORTS_DISCRIMINATOR
                        )

                    for field_data_type in discriminator_field.data_type.all_data_types:
                        if field_data_type.reference:  # pragma: no cover
                            parser._graph_builder.detach_data_type_ref(field_data_type)  # noqa: SLF001

                    new_discriminator_data_type = parser._create_discriminator_data_type(  # noqa: SLF001
                        enum_source,
                        discriminator_values,
                        discriminator_model,
                        imports,
                    )
                    parser._graph_builder.replace_field_type(discriminator_field, new_discriminator_data_type)  # noqa: SLF001
                    discriminator_field.data_type.parent = discriminator_field
                    tag_fields.append((discriminator_field, discriminator_values))
                    record_reused_variant_tag_field(parser, discriminator_field, annotates_tag=annotates_new_tag)
                    literal_default = None
                    if tagged_unions and discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
                        tags.append((
                            discriminator_model,
                            discriminator_field,
                            _get_mapped_discriminator_tag(discriminator_values, enum_source),
                        ))
                    elif parser.force_optional_for_required_fields:
                        literal_default = _get_single_discriminator_default(
                            new_discriminator_data_type, enum_source, expected_value
                        )
                    set_tag_literal_default(
                        parser,
                        discriminator_field,
                        literal_default,
                        "written-tag",
                    )
                    parser._import_ledger.append(  # noqa: SLF001
                        imports,
                        discriminator_field.imports,
                        ImportProducer("discriminator", discriminator_model, discriminator_field),
                    )
                    has_one_literal = True
                if not has_one_literal:
                    new_data_type = parser._create_discriminator_data_type(  # noqa: SLF001
                        base_enum_source := _get_enum_from_base(
                            discriminator_model, field_name, use_field_name=field.SUPPORTS_DISCRIMINATOR
                        ),
                        discriminator_values,
                        discriminator_model,
                        imports,
                    )
                    # Handle multiple aliases (Pydantic v2 AliasChoices)
                    single_alias, validation_aliases = parser._split_field_alias(alias)  # noqa: SLF001
                    parser._graph_builder.append_field(  # noqa: SLF001
                        discriminator_model,
                        discriminator_field := parser.data_model_field_type(
                            name=field_name,
                            data_type=new_data_type,
                            required=True,
                            alias=single_alias,
                            validation_aliases=validation_aliases,
                            serialization_alias=parser.get_serialization_alias(
                                property_name, field_name, discriminator_model.name
                            ),
                            use_serialization_alias=parser.use_serialization_alias,
                            **parser._data_model_field_common_kwargs(),  # noqa: SLF001
                        ),
                    )
                    if parser.config.aliases:
                        discriminator_field.__dict__[_ALIAS_RESOLUTION_CLASS_NAME_KEY] = discriminator_model.class_name
                        parser.model_resolver.prepare_explicit_field_aliases(
                            [discriminator_field], discriminator_model.reference, parser.field_name_model_type
                        )
                    record_reused_variant_tag_field(parser, discriminator_field, annotates_tag=annotates_new_tag)
                    if discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR and (
                        tagged_unions
                        or (
                            len(field.data_type.data_types) > 1
                            and _find_reused_discriminator_value(
                                discriminator_model, field_name, field_name_mode=field_name_mode
                            )
                            is not None
                        )
                    ):
                        tags.append((
                            discriminator_model,
                            discriminator_field,
                            _get_mapped_discriminator_tag(discriminator_values, base_enum_source),
                        ))
            _drop_discriminator_tag_patterns(parser._graph_builder, tag_fields)  # noqa: SLF001
            if not tags:
                continue
            tag_field = struct_tag_field(
                property_name=property_name, field_name=field_name, by_payload_name=tag_by_payload_name
            )
            if tagged_unions:
                unions.append((model, tags, tag_field, mapping))
            else:
                _apply_discriminator_tags(parser._graph_builder, tags, tag_field)  # noqa: SLF001
    if (deferred := parser._deferred_tagged_unions) or unions:  # noqa: SLF001
        parser._deferred_tagged_unions = _tag_discriminator_unions(  # noqa: SLF001
            parser._graph_builder,  # noqa: SLF001
            [*deferred, *unions],
            _derived_models({id(model): model for model in chain(parser.results, models)}.values()),
            defers_related=parser._defers_tagged_unions,  # noqa: SLF001
        )


def set_tag_literal_default(
    parser: DiscriminatorHost,
    discriminator_field: DataModelFieldBase,
    literal: DiscriminatorValue | Member | None,
    source: Literal["tag", "written-tag"],
) -> None:
    """Write the default, required and nullability a discriminator tag gives its field (``decide_literal_default``).

    Pydantic rejects an optional tag, as its None or ``MISSING`` sentinel joins the type, so such a tag is the
    field's default instead; a tag whose type L5 writes (``written-tag``) is required unless forced optional.
    """
    decision = decide_literal_default(
        source,
        literal,
        required=discriminator_field.required,
        has_default=discriminator_field.has_default,
        nullable=discriminator_field.nullable,
        type_has_null=discriminator_field.type_has_null,
        force_optional=parser.force_optional_for_required_fields,
        missing_sentinel=discriminator_field.use_missing_sentinel,
        discriminator_argument=discriminator_field.SUPPORTS_DISCRIMINATOR,
    )
    parser._graph_builder.apply_literal_default(discriminator_field, decision)  # noqa: SLF001


def set_nested_discriminator_literal_defaults(
    parser: DiscriminatorHost,
    tag_fields: Iterable[tuple[DataModelFieldBase, Sequence[DiscriminatorValue]]],
) -> None:
    """Keep the single-literal tags of variants that nested choices give a union valid like the direct ones."""
    for tag_field, values in tag_fields:
        if tag_field.data_type.literals == [*values]:
            set_tag_literal_default(parser, tag_field, values[0], "tag")


def record_variant_discriminator_tags(  # noqa: PLR0913
    parser: DiscriminatorHost,
    variant: DataModel,
    union_name: str,
    *,
    property_name: str,
    field_name: str,
    mapping: dict[str, str],
    values: list[DiscriminatorValue],
    inline_models: _InlineVariantTags,
    field_name_mode: TagFieldMatch,
) -> None:
    """Remember the tags unions map a variant to, unless the variant declares its own tag.

    The values of a later union come from the tag an earlier union wrote, so the tags it maps the variant to are
    derived again from its mapping or the schema name.
    """
    if (recorded := parser._variant_discriminator_tags.get(key := (id(variant), property_name))) is not None:  # noqa: SLF001
        if not same_tags(values, recorded.kept, "legacy-python-equality"):
            recorded.others.setdefault(recorded.owner, recorded.kept)
            recorded.kept, recorded.owner = values, union_name
            recorded.others = {
                union: tags
                for union, tags in recorded.others.items()
                if not same_tags(tags, values, "legacy-python-equality")
            }
        elif not same_tags(
            tags := _get_union_discriminator_values(
                variant, field_name, mapping, inline_models=inline_models, field_name_mode=field_name_mode
            ),
            values,
            "legacy-python-equality",
        ):
            recorded.others.setdefault(union_name, tags)
        return
    if _find_discriminator_value(variant.fields, field_name, field_name_mode=field_name_mode) is None:
        parser._variant_discriminator_tags[key] = _VariantDiscriminatorTags(  # noqa: SLF001
            variant, property_name, values, union_name, {}
        )


def get_collapsed_unions(
    parser: DiscriminatorHost, model: DataModel, field: DataModelFieldBase
) -> list[tuple[str, list[DataType], dict[str, str]]]:
    """Return the discriminated unions that collapsed reuse removed as duplicates of a union field of a model.

    Users of a removed duplicate use the kept model, so their union decodes only the tags the kept one gives.
    """
    unions: list[tuple[str, list[DataType], dict[str, str]]] = []
    if not (duplicates := parser._collapsed_reuse_models.get(id(model))):  # noqa: SLF001
        return unions
    index = model.fields.index(field)
    for duplicate in duplicates:
        if len(duplicate.fields) != len(model.fields):  # pragma: no cover
            continue
        duplicate_field = duplicate.fields[index]
        if not isinstance(discriminator := duplicate_field.extras.get("discriminator"), dict) or len(
            data_types := duplicate_field.data_type.data_types
        ) != len(field.data_type.data_types):  # pragma: no cover
            continue
        name = (
            duplicate.class_name
            if parser._is_root_model(duplicate)  # noqa: SLF001
            else f"{duplicate.class_name}.{duplicate_field.name}"
        )
        unions.append((name, data_types, discriminator.get("mapping", {})))
    return unions


def record_reused_variant_tag_field(
    parser: DiscriminatorHost, field: DataModelFieldBase, *, annotates_tag: bool
) -> None:
    """Remember a field that a union with a reused variant annotates with a new tag, else forget it.

    Such unions were left untagged before, so the ``Literal`` that only their tags import must not rename a model.
    """
    if annotates_tag:
        parser._reused_variant_tag_fields[id(field)] = field  # noqa: SLF001
    else:
        parser._reused_variant_tag_fields.pop(id(field), None)  # noqa: SLF001


def record_collapsed_variant_tags(  # noqa: PLR0913
    parser: DiscriminatorHost,
    variant: DataModel,
    union_name: str,
    data_type: DataType,
    *,
    property_name: str,
    field_name: str,
    mapping: dict[str, str],
    values: list[DiscriminatorValue],
    inline_models: _InlineVariantTags,
    field_name_mode: TagFieldMatch,
) -> None:
    """Remember the tags a union maps a variant to when collapsed reuse replaced the model the union names.

    The union maps the removed duplicate to the tags its own schema, the mapping or its name give, which the kept
    model need not carry. They replace the tags derived from the kept model for that union.
    """
    user, reference = parser._collapsed_reuse_users.get(id(data_type), (None, None))  # noqa: SLF001
    if user is not data_type or reference is None or not isinstance(removed := reference.source, DataModel):
        return
    key = (id(variant), property_name)
    if not same_tags(
        tags := _get_discriminator_values(
            removed, field_name, mapping, inline_models=inline_models, field_name_mode=field_name_mode
        ),
        values,
        "legacy-python-equality",
    ):
        parser._variant_discriminator_tags.setdefault(  # noqa: SLF001
            key, _VariantDiscriminatorTags(variant, property_name, values, union_name, {})
        ).others[union_name] = tags
    elif (recorded := parser._variant_discriminator_tags.get(key)) is not None:  # noqa: SLF001
        recorded.others.pop(union_name, None)


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
            parser._graph_builder.apply_literal_default(model_field, decision)  # noqa: SLF001


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
            _rename_field_preserving_alias(parser._graph_builder, field, new_name)  # noqa: SLF001


def remove_overridden_models(parser: ModulesHost, models: list[DataModel]) -> list[DataModel]:
    """Remove models that are being overridden by custom types (model-level only), and the stand-ins.

    Only model-level overrides (keys without dots) cause model removal.
    Scoped overrides (ClassName.field) only affect specific fields.
    """
    overrides, stand_ins = parser._model_type_override_imports, parser._base_stand_in_imports  # noqa: SLF001
    if not (overrides or stand_ins):
        return models
    kept: list[DataModel] = []
    for model in models:
        if (
            model.class_name in overrides
            or enum_site_override_import(parser, model.reference) is not None
            or model_key_of(model.reference) in stand_ins
        ):
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
    decision phase, reads it as the only description of the class that replaces it. A base that names a stand-in of
    the parse is left to ``apply_base_stand_in_imports``.
    """
    if not parser._model_type_override_imports:  # noqa: SLF001
        return
    stand_ins = parser._base_stand_in_imports  # noqa: SLF001
    _replace_bases_by_imports(
        parser, models, lambda reference: reference is None or model_key_of(reference) not in stand_ins
    )


def _is_tag_type(data_type: DataType) -> bool:
    """Return whether Pydantic reads the type of a tag field as ``Literal`` values.

    Every leaf of the type holds literal values, through the unions and the root models, or type aliases, it names.
    """
    return all(leaf.literals or leaf.enum_member_literals for leaf, _, _ in _iter_discriminator_data_types([data_type]))


def _variant_carries_tag(data_type: DataType, field_name: str) -> bool:
    """Return whether a variant has the tag field ``field_name``, of ``Literal`` values and not optional.

    The variant may inherit the field. Pydantic reads an optional tag as the ``Literal`` and None, which no tag is.
    The None that a root model of nullable variants holds is no variant.
    """
    if not data_type.reference:
        return data_type.type == NONE
    return isinstance(source := data_type.reference.source, DataModel) and any(
        _is_tag_type(tag_field.data_type) and not tag_field._admits_none()  # noqa: SLF001
        for tag_field in islice(
            (
                variant_field
                for variant_field in chain(source.fields, source.iter_all_fields())
                if _tag_field_matches(variant_field, "python-name", field_name)
            ),
            1,
        )
    )


def _root_writes_default(wrapper: DataModel) -> bool:
    """Return whether the root of a root model, or of a type alias, gets a default from what is written for it.

    A class gets it from the call of its root field or from the assignment after its annotation, a type alias from the
    call of its root field inside ``Annotated`` alone, and a root model written as an alias (``Foo = RootModel[T]``)
    has no root field to write one. The call of a type alias that holds only the default of its schema is written in
    render preparation: ``validate_default=True`` makes the call, which writes the default unless ``--use-annotated``
    leaves it out.
    """
    root = wrapper.fields[0]
    call = root._render_plan().call  # noqa: SLF001
    call_default = call is not None and (
        call.factory is not None or (call.positional is not None and call.positional.kind == "default")
    )
    if wrapper.IS_ALIAS:
        return not wrapper.IS_ROOT_MODEL and (
            call_default or (call is None and not root.use_annotated and validates_structured_default(root))
        )
    return call_default or wrapper._field_assignment_facts(root).has_default  # noqa: SLF001


def _drop_nested_discriminators_of_root_defaults(parser: ModulesHost, field: DataModelFieldBase) -> None:
    """Write no ``discriminator=`` for a union inside a field's type that takes a root model with a default.

    ``--collapse-root-models`` writes the discriminator of the root model that a list names as its items onto the
    union it inlines there, a type of its own that the field's extras do not hold.
    """
    for nested in field.data_type.all_data_types:
        if nested is field.data_type or not nested.discriminator:
            continue
        wrappers: list[DataModel] = []
        deque(_iter_discriminator_data_types(nested.data_types, wrappers=wrappers), maxlen=0)
        if any(map(_root_writes_default, wrappers)):
            parser._graph_builder.set_type_discriminator(nested, None)  # noqa: SLF001


def settle_discriminated_unions(parser: ModulesHost, run: ModulesRun) -> None:
    """Write no ``discriminator=`` that Pydantic cannot build, once every module has written its tags.

    The variants a union lists get their tags from ``apply_discriminator_type``, but not the one variant a union names
    alone by its ``$ref``, and Pydantic rejects the union where that variant, or one the ``$ref`` reaches through
    root models, has no tag field of ``Literal`` values. A union of any module may still tag the variant, so a
    variant without a tag is final only once every module is processed.

    Pydantic rejects the one variant as well where it is no model at all: a scalar, ``Any`` or None, which
    ``--collapse-root-models`` leaves in the field in place of a scalar alias, or which the schema writes inline.

    Pydantic rejects as well a union that takes as a variant a root model, or a type alias, whose root has a default,
    however deep the root models it names reach: the default makes the schema of the root a ``default`` schema, which
    no tagged union holds.
    """
    for ctx in run.contexts:
        for model in ctx.models:
            for field in model.fields:
                match field.extras.get("discriminator"):
                    case {"propertyName": str() as property_name} if property_name:
                        lone = _is_lone_variant(field)
                        wrappers: list[DataModel] = []
                        variants = list(
                            _iter_discriminator_data_types(
                                [field.data_type] if lone else field.data_type.data_types, wrappers=wrappers
                            )
                        )
                        if (
                            lone
                            and not (
                                any(data_type.reference for data_type, _, _ in variants)
                                and all(_variant_carries_tag(data_type, property_name) for data_type, _, _ in variants)
                            )
                        ) or any(map(_root_writes_default, wrappers)):
                            parser._graph_builder.drop_field_discriminator(field)  # noqa: SLF001
                if parser.collapse_root_models:
                    _drop_nested_discriminators_of_root_defaults(parser, field)


def apply_base_stand_in_imports(parser: ModulesHost, run: ModulesRun) -> None:
    """Replace the bases that name a stand-in by the import of the class it stands for, once every module is processed.

    Until then the passes of every module, whatever the order of the modules, read the stand-in as a generated base
    when they walk the bases of a model of another module; constructor ordering reads it afterwards as model-level
    overrides keep it.
    """
    stand_ins = parser._base_stand_in_imports  # noqa: SLF001
    for ctx in run.contexts:
        _replace_bases_by_imports(
            parser, ctx.models, lambda reference: reference is not None and model_key_of(reference) in stand_ins
        )


def _replace_bases_by_imports(
    parser: ModulesHost, models: list[DataModel], replaces: Callable[[Reference | None], bool]
) -> None:
    """Replace the bases of the models whose reference ``replaces`` takes by their imports, keeping their models."""
    for model in models:
        for base_class in model.base_classes:
            reference = base_class.reference
            if replaces(reference) and parser._apply_override_to_data_type(base_class):  # noqa: SLF001
                parser._base_stand_ins[id(base_class)] = reference  # noqa: SLF001
                for import_ in base_class.all_imports:
                    parser._graph_builder.add_model_import(model, import_)  # noqa: SLF001


def name_inner_models_in_rules(parser: CollapseHost, run: ModulesRun) -> None:
    """Make the schema validator rules that name a root model the ``parent`` strategy replaced name its inner model.

    Collapse redirects the fields that hold such a root model module by module, and the root model leaves the output
    once collapse redirected its last holder, which is final only once every module is processed. The rules, which
    collapse does not redirect, then name the inner model, and their module imports it as a collapsed field's
    module does.
    """
    unused = {id(model) for model in run.unused_models}
    inner_references = {
        model_key_of(root_model.reference): inner_model.reference
        for root_model, inner_model in parser._replaced_root_models.values()  # noqa: SLF001
        if id(root_model) in unused
    }
    for ctx in run.contexts:
        rule_users = [
            (model, data_type, inner_reference, ctx.imports.reference_paths.get(reference.path))
            for model in ctx.models
            if id(model) not in unused
            for data_type in _rule_data_types(model)
            if (reference := data_type.reference) is not None
            and (inner_reference := inner_references.get(model_key_of(reference))) is not None
        ]
        for _, data_type, inner_reference, _ in rule_users:
            parser._graph_builder.collapse_root_data_type(data_type, inner_reference)  # noqa: SLF001
        import_collapsed_inner_models(
            parser,
            ctx.models,
            [(model, data_type, root_import) for model, data_type, _, root_import in rule_users],
            ctx.imports,
            ctx.scoped_model_resolver,
            run.model_path_to_module_name,
            is_init=ctx.is_init,
            internal_modules=run.internal_modules,
        )


def change_from_import(  # noqa: PLR0913
    parser: ImportFromHost,
    models: list[DataModel],
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    *,
    init: bool,
    internal_modules: set[tuple[str, ...]] | None = None,
    model_path_to_module_name: dict[str, str] | None = None,
) -> None:
    """Import into a module the models of other modules that its models reference, and name them by their imports.

    A stand-in is no member of its module: it takes no name there, and neither it nor a base that names it imports.
    A root that names itself, which deduplication dropped from every module, has no module to import from.
    """
    models = module_members(parser, models)
    if stand_ins := parser._base_stand_in_imports:  # noqa: SLF001
        models = [model for model in models if model_key_of(model.reference) not in stand_ins]
    unimported = {*stand_ins, *(model_key_of(model.reference) for model in parser._self_named_root_models)}  # noqa: SLF001
    members = keyed_models(models, "module members")
    internal_modules = internal_modules or set()
    model_path_to_module_name = model_path_to_module_name or {}

    ledger = parser._import_ledger  # noqa: SLF001
    for model in models:
        ledger.add_name(imports, scoped_model_resolver, [model.path], model.class_name)
    for model in models:
        producer = ImportProducer("change-from-import", model)
        before_import = model.imports
        ledger.append(imports, before_import, producer)
        current_module_name = _get_model_module_name(model, model_path_to_module_name)
        # Some imports are derived from type hints, so aliases can affect them.
        import_sensitive_alias_changed = False
        for data_type in _named_data_types(model):
            match data_type.reference:
                case None:
                    continue
                case reference if members.key_of(reference) in members.models or model_key_of(reference) in unimported:
                    continue
                case reference:
                    pass

            if alias := register_reference_import(
                reference,
                current_module_name,
                _get_data_type_target_full_name(data_type, reference, model_path_to_module_name),
                imports,
                scoped_model_resolver,
                init=init,
                internal_modules=internal_modules,
                base_class=isinstance(data_type, BaseClassDataType),
                producer=producer,
                treat_dot_as_module=parser.treat_dot_as_module,
                import_ledger=parser._import_ledger,  # noqa: SLF001
            ):
                parser._graph_builder.name_reference(data_type, alias)  # noqa: SLF001
                import_sensitive_alias_changed = True
        if import_sensitive_alias_changed:  # pragma: no cover
            after_import = model.imports
            if before_import != after_import:
                ledger.remove(imports, before_import, producer)
                ledger.append(imports, after_import, producer)


def change_module_from_import(parser: ImportFromHost, state: ModuleStateView) -> None:
    """Import the referenced models of other modules into the module."""
    change_from_import(
        parser,
        state.model_list,
        state.imports,
        state.scoped_model_resolver,
        init=state.is_init,
        internal_modules=state.internal_modules,
        model_path_to_module_name=state.model_path_to_module_name,
    )


def process_each_module(
    parser: ModuleLoopHost[ConfigT, ContextT, ResultT], run: ProcessingRun[ConfigT, ContextT, ResultT]
) -> None:
    """Process every output module through ``_process_single_module``, in module order."""
    module_models = run.module_models
    require_update = parser._recursion_facts["require_update"]  # noqa: SLF001
    require_update_action_models = [
        model.path for _, models in module_models for model in models if model_key_of(model.reference) in require_update
    ]
    for position, (module_, models) in enumerate(module_models, 1):
        parser._defers_tagged_unions = position < len(module_models)  # noqa: SLF001
        with pass_scope("Parser._process_single_module", Phase.MODULES), module_checkpoint(models):
            ctx = parser._process_single_module(  # noqa: SLF001
                module_,
                models,
                run.module_results,
                run.config,
                run.internal_modules,
                run.model_path_to_module_name,
                require_update_action_models,
                run.unused_models,
            )
        run.module_to_import[module_] = ctx.imports
        run.contexts.append(ctx)
    settle_update_actions(parser, require_update_action_models, _context_models(run.contexts))


def settle_update_actions(parser: RecursionFactsHost, listed_paths: Iterable[str], models: Iterable[DataModel]) -> None:
    """Give the reference update action to the models at the paths that ``_process_single_module`` listed.

    The parser lists the paths of the models needing the action. A listed path that none of them holds, as one a
    parser extension lists, names the model at it (row ``legacy-keyless-reference-by-path``).
    """
    keyed = keyed_models(models, "listed reference update actions")
    recursion_facts = parser._recursion_facts  # noqa: SLF001
    require_update = recursion_facts["require_update"]
    held = {keyed.models[key].path for key in require_update & keyed.models.keys()}
    parser._recursion_facts = RecursionFacts(  # noqa: SLF001
        require_update=require_update.union(
            keyed.resolve(path for path in listed_paths if path not in held) - {UNRESOLVED_MODEL_KEY}
        ),
        circular_roots=recursion_facts["circular_roots"],
    )


def write_rule_aliases_as_classes(parser: RuleAliasHost, run: ProcessingRun[Any, ModuleContextView, Any]) -> None:
    """Write each alias that holds schema validator rules and that collapse left written as the RootModel class.

    An alias has no class body for the rules. Whether collapse inlines an alias into all its holders is final only
    once every module is processed, so each alias still written takes its class here, under its model key and the
    source location the model metadata names for it. The class takes the template data the alias holds, its rules
    among them, not the parser's entry at its path: moving the alias into an ``_internal`` module or onto the path of
    the root model it replaces leaves that entry behind. The class imports the models of other modules that its rules
    name, as ``change_from_import`` imports them for a class that writes its rules from the start.
    """
    unused = {id(model) for model in run.unused_models}
    if not (
        aliases := [
            model
            for model in _context_models(run.contexts)
            if id(model) not in unused and model._unwritable_runtime_rules  # noqa: SLF001
        ]
    ):
        return
    root_model_type = cast(
        "Callable[[], type[DataModel]]", parser.data_model_type.SCHEMA_RUNTIME_VALIDATION_ROOT_MODEL
    )()
    replacements = {
        id(model): parser._create_data_model(  # noqa: SLF001
            model_type=root_model_type,
            reference=model.reference,
            fields=model.fields,
            decorators=model.decorators,
            custom_base_class=model.custom_base_class,
            custom_template_dir=parser.custom_template_dir,
            extra_template_data=defaultdict(dict, {model.path: model.extra_template_data}),
            path=model.file_path,
            description=model.description,
            nullable=model.nullable,
            keyword_only=parser.keyword_only,
            treat_dot_as_module=parser.treat_dot_as_module,
            dataclass_arguments=parser.dataclass_arguments,
        )
        for model in aliases
    }
    for model_list in chain(
        (ctx.models for ctx in run.contexts),
        (models for _, models in run.module_models),
        (prepared.model_list for prepared in parser._prepared_modules),  # noqa: SLF001
        (prepared.model_list_before_override_removal for prepared in parser._prepared_modules),  # noqa: SLF001
    ):
        model_list[:] = [replacements.get(id(model), model) for model in model_list]
    for model in aliases:
        parser._graph_builder.replace_model(model, replacements[id(model)])  # noqa: SLF001
    if (identities := run.model_identities) is not None:
        run.model_identities = {replacements.get(id(model), model): identity for model, identity in identities.items()}
    classes = {id(model) for model in replacements.values()}
    for ctx in run.contexts:
        members = keyed_models(module_members(parser, ctx.models), "module members")
        for model in ctx.models:
            if id(model) not in classes:
                continue
            for data_type in _rule_data_types(model):
                if (reference := data_type.reference) is None or members.key_of(reference) in members.models:
                    continue
                set_reference_expression(
                    parser,
                    data_type,
                    _get_model_module_name(model, run.model_path_to_module_name),
                    _get_data_type_target_full_name(data_type, reference, run.model_path_to_module_name),
                    ctx.imports,
                    ctx.scoped_model_resolver,
                    same_module=False,
                    init=ctx.is_init,
                    internal_modules=run.internal_modules,
                    producer=ImportProducer("change-from-import", model),
                )
