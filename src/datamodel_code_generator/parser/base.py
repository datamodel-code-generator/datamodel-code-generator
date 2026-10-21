"""Abstract base parser and utilities for schema parsing.

Provides the Parser abstract base class that defines the parsing algorithm,
along with helper functions for model sorting, import resolution, and
code generation.
"""

from __future__ import annotations

import contextlib
import dataclasses
import operator
import os.path
import re
import sys
from abc import ABC, abstractmethod
from collections import Counter, OrderedDict, defaultdict
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import cache, partial
from itertools import chain, groupby, islice, pairwise
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    Generic,
    Literal,
    NamedTuple,
    Optional,
    TypeAlias,
    TypeVar,
    cast,
)
from urllib.parse import ParseResult
from warnings import warn
from weakref import WeakKeyDictionary

from pydantic import BaseModel, ConfigDict
from typing_extensions import Protocol, ReadOnly, TypedDict, Unpack, final

from datamodel_code_generator import (
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    CollapseRootModelsNameStrategy,
    DefaultValueTypeWarning,
    Error,
    FieldTypeCollisionStrategy,
    ModuleSplitMode,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    cached_path_exists,
)
from datamodel_code_generator._checks import CheckArea, CheckMode, check_mode, read_check_flags
from datamodel_code_generator._constraint_applicability import (
    TAG_LITERAL_UNTAKEN_KEYWORD,
    TYPE_DEPENDENT_CONSTRAINTS_KEY,
    overridden_declared_constraints,
    resplit_overridden_constraints,
    untaken_minimums,
)
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator._internal_utils import (
    HashableComparable,
    get_most_of_parent,
)
from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator._naming_hint import collecting_class_names
from datamodel_code_generator._parser_context import ParserSourceContext
from datamodel_code_generator._python_target_names import target_builtin_names
from datamodel_code_generator._reference_graph import (
    EdgeKind,
    GraphSnapshot,
    ReferenceGraph,
    graph_answer,
    reference_graph,
    walk_graph,
)
from datamodel_code_generator._requirements import (
    REQUIREMENTS,
    pydantic_floor,
    pydantic_target_meets,
    python_target_meets,
)
from datamodel_code_generator._scc import find_circular_sccs, strongly_connected_components
from datamodel_code_generator._shared_types import (
    DefaultPutDict,
    LiteralType,
    _CollapseRootModelsRecursionError,
)
from datamodel_code_generator._source import (
    YamlValue,
    _is_parsed_source_cache_enabled,
    _read_parser_source_data_from_path,
)
from datamodel_code_generator._target_unicode import target_pattern, title
from datamodel_code_generator._template_data import copy_extra_template_data
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.enums import (
    DefaultValueType,
    StrictTypes,
    _pydantic_version_key,
)
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATIONS,
    IMPORT_LITERAL,
    IMPORT_OPTIONAL,
    IMPORT_UNION,
    Import,
    Imports,
)
from datamodel_code_generator.model._decisions.enum_kind import decide_enum_kind, literal_value_kind
from datamodel_code_generator.model._decisions.literal_default import decide_literal_default
from datamodel_code_generator.model._decisions.tag_value import (
    enum_tag_literal,
    field_tag,
    is_tag_value,
    mapped_tags,
    mapping_path_keys,
    redeclared_tag_field,
    same_tag,
    same_tags,
    schema_path_keys,
    struct_tag_clash,
    struct_tag_conflicts,
    tag_field_matches,
    tag_key,
    variant_tags,
)
from datamodel_code_generator.model.base import (
    ALL_MODEL,  # noqa: F401  # Preserve the established parser.base export.
    GENERIC_BASE_CLASS_NAME,
    GENERIC_BASE_CLASS_PATH,
    UNDEFINED,
    BaseClassDataType,
    BaseLayoutView,
    ConstraintsBase,
    DataModel,
    DataModelFieldBase,
    _apply_literal_default,
    _find_base_classes,
    _keyed_answer,
    _model_key_of_model,
    _ModelKeys,
    _overlay_constraints,
    _reference_names_model,
    _refresh_custom_template_paths,
    _set_nested_model_default_factory_order,
    _share_path_identity,
    c3_merge,
    direct_base_models,
    get_inherited_fields,
    get_resolve_reference_action_capabilities,
    linearize_data_models,
    sort_data_models_for_mro,
)
from datamodel_code_generator.model.enum import (
    Enum,
    IntEnum,
    Member,
    StrEnum,
    _enum_class,
    get_raw_enum_member_value,
)
from datamodel_code_generator.model.enum import escape_characters as _enum_escape_characters
from datamodel_code_generator.model.output import (
    _expression_names,  # noqa: F401  # Preserve the existing parser helper export.
    _model_field_name_collisions,
    prepare_output_model_config,
)
from datamodel_code_generator.model.type_alias import TypeAliasBase, TypeStatement
from datamodel_code_generator.parser import _pass_decls as decls
from datamodel_code_generator.parser._facts import Phase
from datamodel_code_generator.parser._finality import (
    arm_parse_checks,
    decision_views,
    module_checkpoint,
    parse_checks,
    pass_scope,
    record_ordering_inputs,
)
from datamodel_code_generator.parser._graph_ids import (
    UNRESOLVED_MODEL_KEY,
    KeyedModels,
    is_root_or_owned,
    keyed_models,
    unchecked,
)
from datamodel_code_generator.parser._imports_plan import ImportProducer, ModuleImportLedger
from datamodel_code_generator.parser._model_shapes import (
    COLLAPSE_REUSE,
    REUSE_MODEL,
    SAME_NAME,
    TREE_SCOPE,
    KeySlot,
    ModelShapes,
    ShapeRow,
    reuse_candidate_key,
    skips_root_model,
    stdout_fingerprint,
)
from datamodel_code_generator.parser._passes._keys import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _MODEL_MODULE_PREFIX,
    _RAW_SCHEMA_DEFAULT_KEY,
)
from datamodel_code_generator.parser._passes._shared import (
    _clear_model_imports_cache,
    _clear_model_imports_cache_if_retained,
    _InheritedTypeModifiers,
    _resolve_exact_import,
    _restore_inherited_field_names_for_unique_aliases,
    _unwrap_type_alias,
    alias_shadowed_imports,
    apply_inherited_field_default,
    field_name_model_type_of,
    get_constructor_ordering_conflicts,
    has_import_override,
    normalize_default_value_constraints,
    override_required_field,
    redirect_collapsed_reuse_users,
    register_reference_import,
    register_runtime_expression,
    relative,
    replace_model_in_list,
    resolve_default_value_descriptor,
)
from datamodel_code_generator.parser._phases import DecisionOutcome, DecisionPass, _DecisionWriter
from datamodel_code_generator.parser._pipeline import (
    Boundary,
    DecisionPhase,
    ForEachModule,
    HookCall,
    PassSpec,
    Runner,
    Segment,
    StepLike,
    anchor_parse_caller,
    decision_order,
    release_parse_caller,
)
from datamodel_code_generator.parser._schema_graph import SCHEMA_GRAPH
from datamodel_code_generator.parser.generation import GenerationIndex, GenerationStore, set_model_base_classes
from datamodel_code_generator.parser.schema_version import SchemaFeaturesT
from datamodel_code_generator.python_literal import (
    PythonCode,
    PythonRuntimeExpression,
    _semantic_value_text,
    runtime_expression_imports,
)
from datamodel_code_generator.reference import (
    _ALIAS_RESOLUTION_CLASS_NAME_KEY,
    _UNMATCHED_MODEL_NAME_KEYS_REPORTED,
    SCHEMA_LOCATION_CONFLICT_KEY,
    ModelResolver,
    ModelType,
    Reference,
    _source_path,
    location_of,
    split_module_name,
)
from datamodel_code_generator.types import (
    ANY,
    NONE,
    DataType,
    DataTypeManager,
    DefaultValueDescriptor,
    DefaultValueRecipe,
    Types,
    build_union,
    union_members,
)
from datamodel_code_generator.util import camel_to_snake, record_watch_dependency

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Generator, Hashable, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._format_types import Formatter, PythonVersion
    from datamodel_code_generator._requirements import PydanticConstruct, PydanticFloor
    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.format import CodeFormatter
    from datamodel_code_generator.http import _HTTPFetchSession
    from datamodel_code_generator.model._decisions.tag_value import TagFieldMatch, VariantTagSource
    from datamodel_code_generator.model_metadata import GeneratedModelMetadata, ModelFieldMetadata, ModelMetadata
    from datamodel_code_generator.parser._null_facts import NullRule
    from datamodel_code_generator.parser._schema_graph import SchemaGraphBuilder


# Preserve the existing parser.base export while sharing one canonical escape table.
escape_characters = _enum_escape_characters

ParserConfigT = TypeVar("ParserConfigT", bound="ParserConfig")
_ConstructorFieldAdjustment: TypeAlias = Literal["assignment", "keyword_only"]


# Keep these as module-name checks so non-pydantic-v2 outputs do not import the
# pydantic_v2 generator package and its runtime feature gates.
_PYDANTIC_V2_BASE_MODEL_MODULE: Final = "datamodel_code_generator.model.pydantic_v2.base_model"
_CLASS_NAME_SEPARATOR_PATTERN: Final = re.compile(r"[^A-Za-z0-9]+")
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


_TOP_LEVEL_FUTURE_IMPORT_PATTERN: Final = re.compile(r"(?m)^from __future__ import ")
_TOP_LEVEL_RELATIVE_IMPORT_PATTERN: Final = re.compile(r"(?m)^from \.")

_TYPE_DEPENDENT_CONSTRAINTS_KEY: Final = TYPE_DEPENDENT_CONSTRAINTS_KEY
_DECIMAL_WARNING_EXAMPLE_LIMIT: Final = 5
_ADDITIONAL_IMPORTS: Final = ImportProducer("additional-imports")
_DEFERRED_ANNOTATIONS: Final = ImportProducer("deferred-annotations")
_FUTURE_IMPORTS: Final = ImportProducer("future-imports")
_GENERIC_BASE_CLASS: Final = ImportProducer("generic-base-class")
_COLLAPSE_ROOT_MODELS: Final = ImportProducer("collapse-root-models")
_REMOVE_UNUSED: Final = ImportProducer("remove-unused")
_IMPORT_CONFLICTS: Final = ImportProducer("import-conflicts")
_FIELD_NAME_BINDINGS: Final = ImportProducer("field-name-bindings")
_REUSED_VARIANT_LITERAL: Final = ImportProducer("reused-variant-literal")
_IMPORT_OVERRIDES: Final = ImportProducer("import-overrides")


@dataclass(frozen=True, slots=True)
class _ConstNarrowing:
    """The const that admits one value of the enum a model is built from, read at each value typed by that model.

    ``as_member`` says whether the value is typed ``Literal[<Enum>.<member>]``, which needs members equal to their
    values and an output whose Literal holds enum members; a null const types the value None.
    """

    const: object
    location: str
    as_member: bool


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


@cache
def _type_mro_contains_type(model_type: type[object], *, module: str, name: str) -> bool:
    return any(base.__module__ == module and base.__name__ == name for base in model_type.__mro__)


def _model_type(value: object | type[object]) -> type[object]:
    return value if isinstance(value, type) else value.__class__


def _is_pydantic_v2_data_model_field(value: object) -> bool:
    return _type_mro_contains_type(_model_type(value), module=_PYDANTIC_V2_BASE_MODEL_MODULE, name="DataModelField")


def _get_model_field_constructor(
    field_type: type[DataModelFieldBase],
) -> Callable[..., DataModelFieldBase]:
    """Resolve an exact output field's parser construction capability."""
    if constructor := field_type.__dict__.get("PARSER_CONSTRUCTOR"):
        return cast("Callable[..., DataModelFieldBase]", constructor)
    return field_type


def _source_context_from_config(config: ParserConfig) -> ParserSourceContext:
    """Adapt a public parser config to the neutral source policy."""
    remote_lock = getattr(config, "remote_lock", None)
    response_observer = remote_lock.record_response if remote_lock is not None else None
    return ParserSourceContext(
        base_path=config.base_path,
        encoding=config.encoding,
        remote_text_cache=config.remote_text_cache,
        allow_remote_refs=config.allow_remote_refs,
        strict_refs=config.strict_refs,
        allow_private_network=config.allow_private_network,
        http_backend=config.http_backend,
        http_headers=config.http_headers,
        http_local_ref_path=config.http_local_ref_path,
        http_ignore_tls=config.http_ignore_tls,
        http_query_parameters=config.http_query_parameters,
        http_timeout=config.http_timeout,
        external_ref_mapping=config.external_ref_mapping,
        remote_response_observer=response_observer,
    )


def _get_builtin_pydantic_v2_field_constructor(
    field_type: type[DataModelFieldBase],
) -> Callable[..., DataModelFieldBase] | None:
    """Return a declared exact-class constructor through the legacy helper."""
    constructor = _get_model_field_constructor(field_type)
    return None if constructor is field_type else constructor


def _get_field_dependency_ordering_model_type(model_type: type[DataModel]) -> type[DataModel] | None:
    """Return the configured model type when its fields require dependency ordering."""
    return model_type if model_type.REQUIRES_FIELD_DEPENDENCY_ORDERING else None


def _get_pydantic_v2_root_model_type(model_type: type[DataModel]) -> type[DataModel] | None:
    """Return the field-ordering model type through the legacy compatibility helper."""
    return _get_field_dependency_ordering_model_type(model_type)


def _is_pydantic_v2_root_model(model: DataModel, root_model_type: type[DataModel] | None) -> bool:
    return root_model_type is not None and isinstance(model, root_model_type)


def _is_pydantic_v2_dump_resolve_reference_action(value: object) -> bool:
    """Query the output-owned action capability through the legacy helper."""
    capabilities = get_resolve_reference_action_capabilities(value)
    return capabilities.filter_forward_references and capabilities.generated_formatter_safe


def __getattr__(name: str) -> Any:
    """Return compatibility model modules without importing them on parser load."""
    match name:
        case "dataclass_model":
            from datamodel_code_generator.model import dataclass as dataclass_model  # noqa: PLC0415

            return dataclass_model
        case "msgspec_model":
            from datamodel_code_generator.model import msgspec as msgspec_model  # noqa: PLC0415

            return msgspec_model
        case "Child" | "T" | "to_hashable":
            from datamodel_code_generator._internal_utils import Child, T, to_hashable  # noqa: PLC0415

            match name:
                case "Child":
                    return Child
                case "T":
                    return T
                case _:
                    return to_hashable
    raise AttributeError(name)


ModelName: TypeAlias = str
ModelNames: TypeAlias = set[ModelName]
ModelDeps: TypeAlias = dict[ModelName, set[ModelName]]
OrderIndex: TypeAlias = dict[ModelName, int]
DiscriminatorValue: TypeAlias = str | int | bool

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


def _has_root_field_constraints(root_field: DataModelFieldBase) -> bool:
    """Return whether a root field has constraints, as Field() arguments or written inside its type."""
    return (isinstance(constraints := root_field.constraints, ConstraintsBase) and constraints.has_constraints) or any(
        data_type._consumed_constraints  # noqa: SLF001
        for data_type in (root_field.data_type, *root_field.data_type.data_types)
    )


ComponentId: TypeAlias = int
Components: TypeAlias = list[list[ModelName]]
ComponentOf: TypeAlias = dict[ModelName, ComponentId]
ComponentEdges: TypeAlias = dict[ComponentId, set[ComponentId]]

ClassNode: TypeAlias = tuple[ModelName, ...]
ClassGraph: TypeAlias = dict[ClassNode, set[ClassNode]]
_KEEP_MODEL_ORDER_EDGES: Final = (EdgeKind.DEFINITION, EdgeKind.FIELD)
"""The edges keep model order keeps the models of one cycle together over."""

ModulePath: TypeAlias = tuple[str, ...]
ModuleModels: TypeAlias = list[tuple[ModulePath, list[DataModel]]]
ForwarderMap: TypeAlias = dict[ModulePath, tuple[ModulePath, list[tuple[str, str]]]]
DroppedConstraintFields: TypeAlias = frozenset[tuple[ModulePath, str]]


@dataclass(slots=True)
class DroppedConstraints:
    """The dropped-constraint warnings of the parse whose models a generation returns, each with its module."""

    warnings: DroppedConstraintFields = frozenset()


_DROPPED_CONSTRAINTS: ContextVar[DroppedConstraints | None] = ContextVar("dropped_constraints", default=None)


@contextlib.contextmanager
def dropped_constraints_recorded() -> Generator[DroppedConstraints]:
    """Record the dropped-constraint warnings of the generations run inside, for the parse whose models they return.

    A warning counts once per module and message, so same-named fields of different modules count apart, and a parse
    a generation discards, such as a compatibility retry it does not take, adds nothing.
    """
    record = DroppedConstraints()
    token = _DROPPED_CONSTRAINTS.set(record)
    try:
        yield record
    finally:
        _DROPPED_CONSTRAINTS.reset(token)


@final
class PydanticFloors:
    """The Pydantic floor that each parse of a generation records, so that the generation states its kept parse's."""

    __slots__ = ("_floors",)

    def __init__(self) -> None:
        """Start with no parse recorded."""
        self._floors: WeakKeyDictionary[Parser[Any, Any], PydanticFloor] = WeakKeyDictionary()

    def record(self, parser: Parser[Any, Any], floor: PydanticFloor | None) -> None:
        """Record the floor of the models that ``parser`` emitted, None when they need nothing above the target."""
        if floor is None:
            self._floors.pop(parser, None)
        else:
            self._floors[parser] = floor

    def of(self, parser: Parser[Any, Any]) -> PydanticFloor | None:
        """Return the floor that ``parser`` recorded, or None."""
        return self._floors.get(parser)


_PYDANTIC_FLOORS: ContextVar[PydanticFloors | None] = ContextVar("pydantic_floors", default=None)


@contextlib.contextmanager
def pydantic_floors_recorded() -> Generator[PydanticFloors]:
    """Record the Pydantic floors of the parses run inside, for the generation to state the one of the parse it keeps.

    A parse a generation discards, such as a compatibility retry it does not take, is never read.
    """
    record = PydanticFloors()
    token = _PYDANTIC_FLOORS.set(record)
    try:
        yield record
    finally:
        _PYDANTIC_FLOORS.reset(token)


def _module_key(
    data_model: DataModel, module_split_mode: ModuleSplitMode | None, target_python_version: PythonVersion
) -> ModulePath:
    if module_split_mode == ModuleSplitMode.Single:
        return (*data_model.module_path, camel_to_snake(data_model.class_name, target_python_version))
    return tuple(data_model.module_path)


def _group_models_by_module(
    data_models: Iterable[DataModel], module_split_mode: ModuleSplitMode | None, target_python_version: PythonVersion
) -> ModuleModels:
    """Group models by output module and include required empty package levels."""

    def sort_key(data_model: DataModel) -> tuple[int, ModulePath]:
        key = _module_key(data_model, module_split_mode, target_python_version)
        return (len(key), key)

    grouped_models = groupby(
        sorted(data_models, key=sort_key, reverse=True),
        key=lambda model: _module_key(model, module_split_mode, target_python_version),
    )
    module_models: ModuleModels = []
    previous_module: ModulePath = ()
    for module, models in ((key, [*values]) for key, values in grouped_models):
        if len(previous_module) - len(module) > 1:
            module_models.extend(
                (previous_module[:parts], []) for parts in range(len(previous_module) - 1, len(module), -1)
            )
        module_models.append((module, models))
        previous_module = module
    return module_models


@final
class _ModuleNames(dict[str, str]):  # noqa: FURB189
    """The module name scope planning gives each model, by path and by model key.

    The paths are what `Parser._process_single_module` takes and a subclass of the parser may read or write; the
    lookups (`_get_model_module_name`, `_get_data_type_target_full_name`) read the model keys. Row
    ``legacy-keyless-reference-by-path``: a model with no entry by key, as for an entry a subclass wrote by path, and
    a reference that no model was built with are looked up by path. Row ``legacy-module-follows-path``: the entries
    were paths, so a model that moves to another path leaves its entry behind (`forget`), and a root model whose
    inner model takes its path takes the inner model's entry. Under the ``MODEL_GRAPH`` check, an entry by key that
    differs from the entry at the model's path is a violation.
    """

    __slots__ = ("assigned", "by_key")

    def __init__(self) -> None:
        """Start without entries."""
        super().__init__()
        self.by_key: dict[ModelKey, str] = {}
        self.assigned: dict[str, str] = {}

    def assign(self, model: DataModel, module_name: str, *, keep: bool = False) -> None:
        """Give ``model`` the module name, or with ``keep`` only when it has none yet."""
        path = model.path
        if keep:
            if path not in self:
                self[path] = self.assigned[path] = module_name
            self.by_key.setdefault(model_key_of(model.reference), module_name)
            return
        self[path] = self.assigned[path] = module_name
        self.by_key[model_key_of(model.reference)] = module_name

    def forget(self, models: Iterable[DataModel]) -> None:
        """Drop the entries by key of models that moved to another path, whose entries by path name them no more.

        The models that import cycles merge into an internal module move there after their module names were given.
        """
        for model in models:
            self.by_key.pop(model_key_of(model.reference), None)

    def of(self, reference: Reference) -> str | None:
        """Return the module name of the model ``reference`` names, or None when it has none.

        An entry that a subclass wrote by path, before or after the parser gave the name, wins over the one by key;
        under the ``MODEL_GRAPH`` check, the two differing is a violation.
        """
        path = reference.path
        by_path = self.get(path)
        written = by_path != self.assigned.get(path)
        by_key = None if reference.source is None else self.by_key.get(model_key_of(reference))
        if written:
            return by_path if by_key is None else _keyed_answer("module names", by_path, lambda: by_key)
        if by_key is None:
            return by_path
        return _keyed_answer("module names", by_key, lambda: by_path)


def _index_module_models(
    module_models: ModuleModels, module_split_mode: ModuleSplitMode | None
) -> tuple[dict[DataModel, tuple[ModulePath, list[DataModel]]], _ModuleNames]:
    """Build model lookups for already-grouped output modules."""
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]] = {}
    model_path_to_module_name = _ModuleNames()
    for module, models in module_models:
        for model in models:
            model_to_module_models[model] = module, models
            if module_split_mode == ModuleSplitMode.Single:
                model_path_to_module_name.assign(model, ".".join(module))
    return model_to_module_models, model_path_to_module_name


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


def _module_name_of(model_path_to_module_name: Mapping[str, str], reference: Reference) -> str | None:
    """Return the module name scope planning gives the model ``reference`` names, or None."""
    if isinstance(model_path_to_module_name, _ModuleNames):
        return model_path_to_module_name.of(reference)
    return model_path_to_module_name.get(reference.path)


def _expand_result_module_path(input_tuple: tuple[str, ...]) -> tuple[str, ...]:
    """Expand dotted result paths without changing result identity or ordering."""
    r = []
    for item in input_tuple:
        p = item.split(".")
        if len(p) > 1:
            r.extend(p[:-1])
            r.append(p[-1])
        else:
            r.append(item)

    if len(r) >= 2:  # noqa: PLR2004
        r = [*r[:-2], f"{r[-2]}.{r[-1]}"]
    return tuple(r)


def _normalize_result_module_path(module: ModulePath, *, treat_dot_as_module: bool | None) -> ModulePath:
    """Apply the module-key normalization used by the public parser result."""
    normalized = tuple(part.replace("-", "_") for part in module)
    if treat_dot_as_module:
        return normalized
    return tuple(part[: part.rfind(".")].replace(".", "_") + part[part.rfind(".") :] for part in normalized)


def _reserve_import_alias(name: str, reserved_names: set[str]) -> str:
    """Reserve and return the first ``<name>_aliased`` name, with a suffix when taken."""
    alias = f"{name}_aliased"
    suffix = 1
    while alias in reserved_names:
        alias = f"{name}_aliased_{suffix}"
        suffix += 1
    reserved_names.add(alias)
    return alias


def _merged_class_names(class_names: Sequence[str]) -> tuple[str, ...]:
    """Return one distinct class name per model merged into one module, in merge order.

    A model keeps its name unless an earlier model of the merge already holds it; a later holder takes
    ``<name>_<n>`` with the smallest ``n >= 1`` that no model of the merge holds or has already taken.
    """
    taken = set(class_names)
    held: set[str] = set()
    names: list[str] = []
    for name in class_names:
        if name not in held:
            held.add(name)
            names.append(name)
            continue
        suffix = 1
        while (candidate := f"{name}_{suffix}") in taken:
            suffix += 1
        taken.add(candidate)
        names.append(candidate)
    return tuple(names)


def _bind_module_field_names(models: list[DataModel], imports: Imports, ledger: ModuleImportLedger) -> None:
    """Allocate aliases only where a property hides an emitted expression name.

    Type aliases sit at module scope, so an import they name keeps its name beside the alias.
    """
    field_names = {field.name for model in models for field in model.fields if field.name is not None}
    shadowable_imports = {
        effective_name: (from_, name)
        for from_, names in imports.items()
        for name in names
        if (effective_name := imports.get_effective_name(from_, name)) in field_names
    }
    if "list" in field_names and "list" not in shadowable_imports:
        shadowable_imports["list"] = ("builtins", "list")
    if not shadowable_imports:
        return
    collisions: set[str] = set()
    reserved_names = field_names | {model.class_name for model in models}
    for model in models:
        collisions.update(_model_field_name_collisions(model, shadowable_imports))
    if not collisions:
        return
    reserved_names.update(imports.get_effective_name(from_, name) for from_, names in imports.items() for name in names)
    type_alias_imports = {
        (import_.from_, import_.import_)
        for model in models
        if isinstance(model, TypeAliasBase)
        for import_ in model.imports
    }
    bindings: dict[str, str] = {}
    for name, (from_, imported_name) in shadowable_imports.items():
        if name not in collisions:
            continue
        alias = _reserve_import_alias(name, reserved_names)
        keep_unaliased = (from_, imported_name) in type_alias_imports
        aliased_import = Import(from_=from_, import_=imported_name, alias=alias, keep_unaliased=keep_unaliased)
        bindings[name] = alias
        if from_ == "builtins" or keep_unaliased:
            ledger.append(imports, aliased_import, _FIELD_NAME_BINDINGS)
        else:
            ledger.apply_alias(imports, aliased_import, _FIELD_NAME_BINDINGS)
    for model in models:
        if not isinstance(model, TypeAliasBase):
            model.set_field_name_bindings(bindings)


def _expand_export_module_path(module: ModulePath) -> ModulePath:
    """Expand dotted module components while preserving the Python file suffix."""
    parts = ".".join(module).split(".")
    return (*parts[:-2], f"{parts[-2]}.{parts[-1]}")


def _iter_import_bindings(imports: Imports) -> Iterator[str]:
    """Yield names bound by one generated import block."""
    for from_, imported_names in imports.items():
        for imported_name in imported_names:
            effective_name = imports.get_effective_name(from_, imported_name)
            yield (
                imported_name.partition(".")[0] if from_ is None and effective_name == imported_name else effective_name
            )


class ModuleContext(NamedTuple):
    """Context for processing a single module during code generation."""

    module: ModulePath
    module_key: ModulePath
    models: list[DataModel]
    is_init: bool
    imports: Imports
    scoped_model_resolver: ModelResolver


class ParseConfig(NamedTuple):
    """Configuration for the parse operation."""

    with_import: bool
    use_deferred_annotations: bool
    code_formatter: CodeFormatter | None
    module_split_mode: ModuleSplitMode | None
    all_exports_scope: AllExportsScope | None
    all_exports_collision_strategy: AllExportsCollisionStrategy | None


class StdoutBindingContext(NamedTuple):
    """Inputs required to validate bindings across concatenated modules."""

    common_imports: Imports
    models_by_name: Mapping[str, list[tuple[ModulePath, DataModel]]]
    treat_dot_as_module: bool | None


def _forward_defined_models(contexts: list[ModuleContext], forwarder_map: ForwarderMap) -> ForwarderMap:
    """Keep the re-exports of the models an internal module still defines, dropping a module left without any.

    Import cycles are merged before reuse and root model collapsing drop models from the internal module.
    """
    defined = {ctx.module_key: {model.class_name for model in ctx.models} for ctx in contexts}
    return {
        module: (internal_module, kept)
        for module, (internal_module, class_mappings) in forwarder_map.items()
        if (kept := [mapping for mapping in class_mappings if mapping[1] in defined[internal_module]])
    }


def _decode_json_pointer_part(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _source_path_from_reference_path(reference_path: str) -> list[str] | None:
    _, separator, fragment = reference_path.partition("#")
    return (
        None
        if not separator or (fragment and not fragment.startswith("/"))
        else []
        if not fragment
        else [_decode_json_pointer_part(part) for part in fragment[1:].split("/")]
    )


def _module_name_from_module_path(module: ModulePath) -> str:
    if module == ("__init__.py",):
        return ""

    parts = [*module]
    if parts and parts[-1].endswith(".py"):  # pragma: no branch - Module paths are Python files.
        parts[-1] = parts[-1][:-3]
    return ".".join(parts[:-1] if parts[-1:] == ["__init__"] else parts)


class _RenamedType(NamedTuple):
    """A class name a type had before a rename, and the path imports of that name were added under."""

    class_name: str
    import_path: str


def _stale_renamed_import(
    imports: Imports, data_type: DataType, renamed_types: Sequence[_RenamedType]
) -> Import | None:
    """Return the import of a module that still names a data type's type by one of the names it had before."""
    return next(
        (
            import_
            for renamed_type in renamed_types
            if (import_ := imports.reference_paths.get(renamed_type.import_path)) is not None
            and (
                import_.import_ == renamed_type.class_name
                or (data_type.alias or "").endswith(f".{renamed_type.class_name}")
            )
        ),
        None,
    )


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


class _InheritedConstructorInfo(NamedTuple):
    required_assignment_names: frozenset[str]
    ordering_conflicts: frozenset[str]


@dataclass(frozen=True, slots=True)
class ParserRunContext:
    """Immutable parser settings scoped to one facade-managed run."""

    diagnostic_source_path: Path | None = None
    prefetched_source: tuple[Path, bytes] | None = None
    formatter_cwd: Path | None = None
    preserve_circular_root_models: bool = False
    suppress_parse_warnings: bool = False
    untyped_nullable_maps: bool = False
    yielding_names: tuple[tuple[str, tuple[str, str | None]], ...] = ()


_DEFAULT_PARSER_RUN_CONTEXT = ParserRunContext()


@dataclass(frozen=True, slots=True)
class _ReuseOptimizationContext:
    """Parser-owned semantic constraints for model reuse optimizations."""

    type_override_model_names: frozenset[str] = frozenset()

    @classmethod
    def from_type_overrides(cls, type_overrides: Mapping[str, str]) -> _ReuseOptimizationContext:
        """Build reuse constraints without importing output-backend policy."""
        if not type_overrides:
            return cls()

        return cls(frozenset(override_key.partition(".")[0] for override_key in type_overrides))

    def allows_model(self, model: DataModel) -> bool:
        """Return whether reuse can preserve all pending parser semantics."""
        return model.class_name not in self.type_override_model_names

    def eligible_models(self, models: Iterable[DataModel]) -> Iterable[DataModel]:
        """Return models eligible for reuse without allocating on the fast path."""
        if not self.type_override_model_names:
            return models
        return (model for model in models if self.allows_model(model))


def _assigned_key(model: DataModel) -> Callable[[DataModelFieldBase], bool]:
    """Return the sort key that puts the fields the model's class body assigns after the others."""
    return lambda field: model._field_assignment_facts(field).assigned  # noqa: SLF001


def _apply_constructor_field_adjustments(
    model: DataModel,
    first_adjustment: tuple[DataModelFieldBase, _ConstructorFieldAdjustment],
    adjustments: Iterable[tuple[DataModelFieldBase, _ConstructorFieldAdjustment]],
) -> None:
    """Apply exact required-field assignments and keyword-only ordering fixes."""
    enable_model_keyword_only = False
    for field, adjustment in chain((first_adjustment,), adjustments):
        match adjustment:
            case "assignment":
                field.force_field_assignment()
            case _:
                assert adjustment == "keyword_only"
                if model.REQUIRES_MODEL_LEVEL_KW_ONLY:
                    enable_model_keyword_only = True
                else:
                    field.mark_as_keyword_only()
    if enable_model_keyword_only:
        model.enable_model_keyword_only()


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


def _sort_models_by_class_body_references(
    models: list[DataModel],
    pydantic_v2_root_model_type: type[DataModel] | None,
    runtime_validation_references: Mapping[ModelKey, frozenset[ModelKey]],
) -> list[DataModel]:
    """Define first the models a class body evaluates: base classes, root types and runtime rule types."""
    keyed = keyed_models(models, GraphSnapshot.CLASS_BODY_ORDER.value)
    order_index = {key: index for index, key in enumerate(keyed.models)}
    targets: dict[ModelKey, set[ModelKey]] = {}
    for model_key, model in keyed.models.items():
        model_targets = targets[model_key] = {
            keyed.key_of(base_class.reference) for base_class in model.base_classes if base_class.reference
        }
        model_targets.update(runtime_validation_references.get(model_key, ()))
        if _is_pydantic_v2_root_model(model, pydantic_v2_root_model_type):
            model_targets.update(
                keyed.key_of(data_type.reference)
                for field in model.fields
                for data_type in field.data_type.all_data_types
                if data_type.reference
            )
        model_targets.discard(model_key)
    graph = reference_graph(GraphSnapshot.CLASS_BODY_ORDER, keyed.models, {EdgeKind.CLASS_BODY: targets})

    def old() -> list[ModelKey]:
        edges: dict[ModelKey, set[ModelKey]] = {key: set() for key in keyed.models}
        for model_key, model_targets in targets.items():
            for reference_key in model_targets:
                if reference_key in edges:
                    edges[reference_key].add(model_key)
        return stable_toposort(list(order_index), edges, key=order_index.__getitem__)

    sorted_keys = graph_answer(
        GraphSnapshot.CLASS_BODY_ORDER,
        graph.dependency_order((EdgeKind.CLASS_BODY,), list(order_index), order_index.__getitem__),
        old,
    )
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


def _runtime_validation_names_later_model(
    models: list[DataModel],
    runtime_validation_references: Mapping[ModelKey, frozenset[ModelKey]],
) -> bool:
    """Return whether a runtime rule names a model that ``models`` list after the rule's model."""
    positions = {model_key_of(model.reference): index for index, model in enumerate(models)}
    return any(
        positions.get(reference_key, -1) > index
        for index, model in enumerate(models)
        for reference_key in runtime_validation_references.get(model_key_of(model.reference), ())
    )


SPECIAL_PATH_FORMAT: str = "#-datamodel-code-generator-#-{}-#-special-#"


def get_special_path(keyword: str, path: list[str]) -> list[str]:
    """Create a special path marker for internal reference tracking."""
    return [*path, SPECIAL_PATH_FORMAT.format(keyword)]


def dump_templates(templates: list[DataModel]) -> str:
    """Join model templates into a single code string."""
    return "\n\n\n".join(str(m) for m in templates)


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


def _reaches_data_type(data_type: DataType, target: DataType) -> bool:
    """Return whether target is data_type or lies under it, through nested data types and dict keys."""
    reached: set[int] = set()
    data_type.walk(lambda _: None, reached)
    return id(target) in reached


def _needs_validate_default(data_type: DataType) -> bool:
    """Check if a field needs validate_default=True to coerce defaults into model instances."""
    resolved = _unwrap_type_alias(data_type)
    return _contains_model_reference(resolved)


def _declares_unique_items(field: DataModelFieldBase) -> bool:
    """Return whether a field declares ``uniqueItems: true``."""
    match field.constraints:
        case ConstraintsBase(unique_items=True) | {"uniqueItems": True}:
            return True
    return False


def _unique_items_tuple(data_type: DataType) -> DataType | None:
    """Return the tuple a unique array is written as, where --use-unique-items-as-set writes a list as a set."""
    if data_type.is_tuple:
        return data_type
    if data_type.is_list or data_type.is_set or data_type.is_frozen_set:
        return None
    return next((found for nested in data_type.data_types if (found := _unique_items_tuple(nested)) is not None), None)


def _can_retain_model_imports_cache(
    models: list[DataModel],
    *,
    configured_types_are_builtin: bool,
) -> bool:
    """Return whether all configured and materialized generation types are built in."""
    if not configured_types_are_builtin:
        return False

    for model in models:
        if type(model).__module__.startswith(_MODEL_MODULE_PREFIX):
            continue
        return False
    return True


ReferenceMapSet = dict[str, set[str]]
SortedDataModels = dict[ModelKey, DataModel]


class RecursionFacts(TypedDict):
    """What makes models recursive in the output, by model key, for the passes after the one that finds it."""

    require_update: ReadOnly[frozenset[ModelKey]]
    """The models whose module calls the reference update action for them: the models the sort finds on a reference
    cycle or after a model they reference, and the models that reuse and the shared module make need it later."""
    circular_roots: ReadOnly[frozenset[ModelKey]]
    """The root models on a cycle of root models, which collapsing keeps named."""


_NO_RECURSION: Final = RecursionFacts(require_update=frozenset(), circular_roots=frozenset())


class _ParseArgs(NamedTuple):
    """The arguments of one ``Parser.parse()`` call."""

    with_import: bool | None
    format_: bool | None
    settings_path: Path | None
    disable_future_imports: bool
    all_exports_scope: AllExportsScope | None
    all_exports_collision_strategy: AllExportsCollisionStrategy | None
    module_split_mode: ModuleSplitMode | None
    collect_model_metadata: bool


_NO_DECISIONS: Final = DecisionOutcome(())


@final
@dataclass(slots=True)
class _PipelineRun:
    """The state of one ``Parser.parse()`` call, filled segment by segment; a value read before it is set raises."""

    args: _ParseArgs
    suppress_parse_warnings: bool
    config: ParseConfig = dataclasses.field(init=False)
    sorted_data_models: SortedDataModels = dataclasses.field(init=False)
    model_identities: Mapping[DataModel, str] | None = None
    module_models: ModuleModels = dataclasses.field(init=False)
    internal_modules: set[ModulePath] = dataclasses.field(init=False)
    forwarder_map: ForwarderMap = dataclasses.field(init=False)
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]] = dataclasses.field(init=False)
    model_path_to_module_name: dict[str, str] = dataclasses.field(init=False)
    module_results: dict[ModulePath, Result] = dataclasses.field(default_factory=dict)
    unused_models: list[DataModel] = dataclasses.field(default_factory=list)
    module_to_import: dict[ModulePath, Imports] = dataclasses.field(default_factory=dict)
    contexts: list[ModuleContext] = dataclasses.field(default_factory=list)
    opted_out: list[DataModel] = dataclasses.field(default_factory=list)
    decisions: DecisionOutcome = _NO_DECISIONS
    output: str | dict[tuple[str, ...], Result] = dataclasses.field(init=False)


class _SettleRun(Protocol):
    """What the passes up to the sorting of models read and write."""

    args: _ParseArgs
    suppress_parse_warnings: bool
    config: ParseConfig
    sorted_data_models: SortedDataModels
    model_identities: Mapping[DataModel, str] | None


class _StructuredRun(_SettleRun, Protocol):
    """What the passes from the module structure on read and write."""

    module_models: ModuleModels
    internal_modules: set[ModulePath]
    forwarder_map: ForwarderMap
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]]
    model_path_to_module_name: dict[str, str]


class _ModulesRun(_StructuredRun, Protocol):
    """What the processing of output modules reads and writes."""

    module_results: dict[ModulePath, Result]
    unused_models: list[DataModel]
    module_to_import: dict[ModulePath, Imports]
    contexts: list[ModuleContext]


class _FinishRun(_ModulesRun, Protocol):
    """What the passes after the output modules are processed read and write."""

    opted_out: list[DataModel]
    decisions: DecisionOutcome
    """What the decision phase changed."""
    output: str | dict[tuple[str, ...], Result]


def _as_settle_run(run: _PipelineRun) -> _SettleRun:
    return run


def _as_structured_run(run: _PipelineRun) -> _StructuredRun:
    return run


def _as_modules_run(run: _PipelineRun) -> _ModulesRun:
    return run


def _as_finish_run(run: _PipelineRun) -> _FinishRun:
    return run


@final
@dataclass(slots=True)
class _StructureState:
    """The state of one ``_build_module_structure`` call."""

    sorted_data_models: SortedDataModels
    module_split_mode: ModuleSplitMode | None
    module_models: ModuleModels = dataclasses.field(init=False)
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]] = dataclasses.field(init=False)
    model_path_to_module_name: _ModuleNames = dataclasses.field(init=False)
    internal_modules: set[ModulePath] = dataclasses.field(init=False)
    forwarder_map: ForwarderMap = dataclasses.field(init=False)


@final
@dataclass(slots=True, kw_only=True)
class _ModuleState:
    """The state of one ``_process_single_module`` call.

    ``model_list`` is the list of the module's models: after model-level type overrides removed models, the kept
    list, and ``model_list_before_override_removal`` the list before.
    """

    is_init: bool
    model_list: list[DataModel]
    imports: Imports
    scoped_model_resolver: ModelResolver
    all_module_fields: set[str]
    can_retain_cache: bool
    config: ParseConfig
    internal_modules: set[ModulePath]
    model_path_to_module_name: dict[str, str]
    require_update_action_models: list[str]
    unused_models: list[DataModel]
    unused_models_start: int = dataclasses.field(init=False)
    model_list_before_override_removal: list[DataModel] = dataclasses.field(init=False)
    current_unused_models: Sequence[DataModel] = ()
    live_models: list[DataModel] = dataclasses.field(init=False)


@final
@dataclass(slots=True, kw_only=True)
class _PreparedModule:
    """One output module as ``_process_single_module`` left it, for its render preparation after the decision phase.

    ``model_list`` holds the models of the module that render, ``model_list_before_override_removal`` also those a
    model-level type override replaces, which constructor ordering still orders, and ``current_unused_models`` the
    unused models the module added.
    """

    model_list: list[DataModel]
    model_list_before_override_removal: list[DataModel]
    can_retain_cache: bool
    config: ParseConfig
    current_unused_models: Sequence[DataModel]
    live_models: list[DataModel] = dataclasses.field(init=False)

    @classmethod
    def of(cls, state: _ModuleState) -> _PreparedModule:
        """Take the module of ``state`` with its model lists as they are now."""
        return cls(
            model_list=list(state.model_list),
            model_list_before_override_removal=list(state.model_list_before_override_removal),
            can_retain_cache=state.can_retain_cache,
            config=state.config,
            current_unused_models=state.current_unused_models,
        )


@final
@dataclass(slots=True)
class _FinalizeState:
    """The state of one ``_finalize_modules`` call.

    ``all_models`` are the models of every module as they were before unused models left them.
    """

    contexts: list[ModuleContext]
    unused_models: list[DataModel]
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]]
    module_to_import: dict[ModulePath, Imports]
    all_models: list[DataModel] = dataclasses.field(init=False)
    model_imports: dict[DataModel, tuple[Import, ...]] = dataclasses.field(init=False)
    renamed_models: bool = False
    additional_properties_models: list[DataModel] = dataclasses.field(default_factory=list)

    def collect_additional_properties_models(self) -> bool:
        """Take the models whose additional properties reference classes; return whether there is any."""
        self.additional_properties_models = [
            model for model in self.all_models if len(model.additional_properties_reference_classes)
        ]
        return bool(self.additional_properties_models)


def _same_state(state: _FinalizeState) -> _FinalizeState:
    return state


def _module_models(_parser: object, state: _ModuleState) -> list[DataModel]:
    return state.model_list


def _prepared_models(_parser: object, state: _PreparedModule) -> list[DataModel]:
    return state.model_list


def _all_models(_parser: object, state: _FinalizeState) -> list[DataModel]:
    return state.all_models


def _context_models(contexts: Iterable[ModuleContext]) -> list[DataModel]:
    return [model for ctx in contexts for model in ctx.models]


def _refresh_field_config(models: Iterable[DataModel]) -> None:
    for model in models:
        model.refresh_field_config()


MAX_RECURSION_COUNT: int = sys.getrecursionlimit()


def add_model_path_to_list(
    paths: list[str] | None,
    model: DataModel,
    /,
) -> list[str]:
    """
    Auxiliary method which adds model path to list, provided the following hold.

    - model is not a type alias
    - path is not already in the list.

    """
    if paths is None:
        paths = []
    if model.is_alias:
        return paths
    if (path := model.path) in paths:
        return paths
    paths.append(path)
    return paths


def _named_model_path(path: str, models: Mapping[str, DataModel]) -> str:
    """Return how a message names the model at a reference path: its schema location, or the path without one."""
    location = None if (model := models.get(path)) is None else location_of(model.reference)
    return redact_reference(path if location is None else str(location))


def sort_data_models(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
    unsorted_data_models: list[DataModel],
    sorted_data_models: SortedDataModels | None = None,
    require_update_action_models: list[ModelKey] | None = None,
    recursion_count: int = MAX_RECURSION_COUNT,
    generation_index: GenerationIndex | None = None,
    *,
    pydantic_v2_root_model_type: type[DataModel] | None = None,
    runtime_validation_references: Mapping[ModelKey, frozenset[ModelKey]] | None = None,
    keyed: KeyedModels | None = None,
) -> tuple[list[DataModel], SortedDataModels, list[ModelKey]]:
    """Sort data models by dependency order for correct forward references.

    Models, their references and the models needing a reference update action are keyed by model key; ``keyed``
    holds the models of the outermost call, which the paths that models list themselves resolve against.
    """
    if sorted_data_models is None:
        sorted_data_models = OrderedDict()

    runtime_validation_references = runtime_validation_references or {}

    if require_update_action_models is None:
        require_update_action_models = []
    require_update_action_model_keys = set(require_update_action_models)
    universe = (
        keyed_models((*sorted_data_models.values(), *unsorted_data_models), "sort_data_models")
        if keyed is None
        else keyed
    )

    def add_require_update_action_model(model: DataModel, key: ModelKey) -> None:
        if model.is_alias or key in require_update_action_model_keys:
            return
        require_update_action_models.append(key)
        require_update_action_model_keys.add(key)

    def get_reference_keys(model: DataModel, key: ModelKey) -> frozenset[ModelKey]:
        reference_keys = (
            universe.resolve(model.reference_classes)
            if generation_index is None
            else generation_index.referenced_keys_for_model(model, universe)
        )
        if model_runtime_validation_references := runtime_validation_references.get(key):
            return reference_keys | model_runtime_validation_references
        return reference_keys

    sorted_model_count: int = len(sorted_data_models)
    sorted_model_keys = set(sorted_data_models)

    unresolved_references: list[DataModel] = []
    for model in unsorted_data_models:
        key = model_key_of(model.reference)
        reference_keys = get_reference_keys(model, key)
        if not reference_keys:
            sorted_data_models[key] = model
            sorted_model_keys.add(key)
        elif key in reference_keys and len(reference_keys) == 1:  # only self-referencing
            sorted_data_models[key] = model
            sorted_model_keys.add(key)
            add_require_update_action_model(model, key)
        elif not reference_keys - {key} - sorted_model_keys:  # reference classes have been resolved
            sorted_data_models[key] = model
            sorted_model_keys.add(key)
            if key in reference_keys:
                add_require_update_action_model(model, key)
        else:
            unresolved_references.append(model)

    if unresolved_references:
        if sorted_model_count != len(sorted_data_models) and recursion_count:
            try:
                return sort_data_models(
                    unresolved_references,
                    sorted_data_models,
                    require_update_action_models,
                    recursion_count - 1,
                    generation_index,
                    pydantic_v2_root_model_type=pydantic_v2_root_model_type,
                    runtime_validation_references=runtime_validation_references,
                    keyed=universe,
                )
            except RecursionError:  # pragma: no cover
                pass

        # sort on base_class dependency
        seen_orderings: set[tuple[ModelKey, ...]] = set()
        while True:
            ordered_models: list[tuple[int, DataModel]] = []
            # Build lookup dict for O(1) index access instead of O(n) list.index()
            key_to_index = {model_key_of(m.reference): idx for idx, m in enumerate(unresolved_references)}
            for model in unresolved_references:
                if _is_pydantic_v2_root_model(model, pydantic_v2_root_model_type):
                    indexes = [
                        key_to_index[reference_key]
                        for f in model.fields
                        for t in f.data_type.all_data_types
                        if t.reference and (reference_key := universe.key_of(t.reference)) in key_to_index
                    ]
                else:
                    indexes = [
                        key_to_index[reference_key]
                        for b in model.base_classes
                        if b.reference and (reference_key := universe.key_of(b.reference)) in key_to_index
                    ]

                if indexes:
                    ordered_models.append((
                        max(indexes),
                        model,
                    ))
                else:
                    ordered_models.append((
                        -1,
                        model,
                    ))

            sorted_unresolved_models = [m[1] for m in sorted(ordered_models, key=operator.itemgetter(0))]
            if sorted_unresolved_models == unresolved_references:
                break

            sig = tuple(model_key_of(m.reference) for m in sorted_unresolved_models)
            if sig in seen_orderings:
                # Base-class dependency order has no fixed point (e.g. cyclic inheritance with
                # discriminators). Further iterations only permute the list; use stable order.
                unresolved_references.sort(key=lambda m: m.path)
                break

            seen_orderings.add(sig)
            unresolved_references = sorted_unresolved_models

        if runtime_validation_references and _runtime_validation_names_later_model(
            unresolved_references, runtime_validation_references
        ):
            unresolved_references = _sort_models_by_class_body_references(
                unresolved_references, pydantic_v2_root_model_type, runtime_validation_references
            )

        # circular reference
        unsorted_data_model_keys = set(key_to_index)
        for model in unresolved_references:
            key = model_key_of(model.reference)
            reference_keys = get_reference_keys(model, key)
            unresolved_model = reference_keys - {key} - sorted_model_keys
            base_model_keys = {universe.key_of(s.reference) for s in model.base_classes if s.reference}
            update_action_parent = require_update_action_model_keys.intersection(base_model_keys)
            if not unresolved_model:
                sorted_data_models[key] = model
                sorted_model_keys.add(key)
                if update_action_parent:
                    add_require_update_action_model(model, key)
                continue

            if not unresolved_model - unsorted_data_model_keys:
                sorted_data_models[key] = model
                sorted_model_keys.add(key)
                add_require_update_action_model(model, key)
                continue

            # unresolved
            models_by_path = {
                **{item.path: item for item in sorted_data_models.values()},
                **{item.path: item for item in unresolved_references},
            }

            def reference_paths(item: DataModel) -> frozenset[str]:
                reference_classes = (
                    item.reference_classes
                    if generation_index is None
                    else generation_index.reference_classes_for_model(item)
                )
                return reference_classes | {
                    universe.models[reference_key].path
                    for reference_key in runtime_validation_references.get(model_key_of(item.reference), ())
                }

            unresolved_classes = ", ".join(
                f"[class: {_named_model_path(item.path, models_by_path)} references: "
                f"{frozenset(_named_model_path(path, models_by_path) for path in reference_paths(item))}]"
                for item in unresolved_references
            )
            msg = f"A Parser can not resolve classes: {unresolved_classes}."
            raise Exception(msg)  # noqa: TRY002

    return unresolved_references, sorted_data_models, require_update_action_models


def sort_base_classes_for_mro(
    sorted_data_models: SortedDataModels,
    generation_store: GenerationStore | None = None,
) -> None:
    """Sort base classes in each model to ensure valid Python MRO.

    When a class inherits from multiple base classes where some bases inherit
    from others, Python's C3 linearization requires that child classes appear
    before their parent classes in the inheritance list.

    For example, if B inherits from A, then class C(A, B) is invalid but
    class C(B, A) is valid.
    """
    sorted_keyed = keyed_models(sorted_data_models.values(), "base class sources")
    for model in sorted_data_models.values():
        base_classes = model.base_classes
        if len(base_classes) <= 1:
            continue

        source_models = [
            source_model
            for base_class in base_classes
            if base_class.reference
            and (
                source_model := (
                    base_class.reference.source
                    if isinstance(base_class.reference.source, DataModel)
                    else sorted_data_models.get(sorted_keyed.key_of(base_class.reference))
                )
            )
            is not None
        ]
        ordered_models = sort_data_models_for_mro(source_models)
        ordered = keyed_models(ordered_models, "base class order")
        model_order = {model_key_of(source_model.reference): index for index, source_model in enumerate(ordered_models)}
        sorted_base_classes = sorted(
            base_classes,
            key=lambda base_class: (
                model_order.get(ordered.key_of(base_class.reference), 0) if base_class.reference else 0
            ),
        )
        if all(
            sorted_base_class is base_class
            for sorted_base_class, base_class in zip(sorted_base_classes, base_classes, strict=True)
        ):
            continue
        set_model_base_classes(model, sorted_base_classes, generation_store)


def get_module_directory(module: tuple[str, ...]) -> tuple[str, ...]:
    """Get the directory portion of a module tuple.

    Note: Module tuples in module_models do NOT include .py extension.
    The last element is either the module name (e.g., "issuing") or empty for root.

    Examples:
        ("pkg",) -> ("pkg",) - root module
        ("pkg", "issuing") -> ("pkg",) - submodule
        ("foo", "bar", "baz") -> ("foo", "bar") - deeply nested module
    """
    if not module:
        return ()
    if len(module) == 1:
        return module
    return module[:-1]


def title_to_class_name(title: str) -> str:
    """Convert a schema title to a valid Python class name."""
    classname = _CLASS_NAME_SEPARATOR_PATTERN.sub(" ", title)
    return "".join(x for x in classname.title() if not x.isspace())


def _find_field(field_name: str, models: list[DataModel]) -> DataModelFieldBase | None:
    """Find a field using generated models' C3 inheritance order."""
    return get_inherited_fields(models).get(field_name)


def _copy_data_type(data_type: DataType, *, register_references: bool = True) -> DataType:
    """Copy a DataType tree without detaching its model references."""
    copied_data_type = data_type.model_copy()
    copied_data_type.parent = None
    copied_data_type.children = []
    copied_data_type.literals = list(data_type.literals)
    copied_data_type.enum_member_literals = list(data_type.enum_member_literals)
    if (kwargs := data_type.kwargs) is not None:
        copied_data_type.kwargs = deepcopy(kwargs)

    data_types = data_type.data_types
    dict_key = data_type.dict_key
    match data_types, dict_key:
        case [], None:
            copied_data_type.data_types = []
        case _:
            copied_data_type.data_types = _copy_data_types(data_types, register_references=register_references)
            for nested_data_type in copied_data_type.data_types:
                nested_data_type.parent = copied_data_type
            if dict_key is not None:
                copied_data_type.dict_key = _copy_data_type(dict_key, register_references=register_references)
                copied_data_type.dict_key.parent = copied_data_type

    if register_references:
        copied_data_type.register_reference()
    return copied_data_type


def _copy_data_types(data_types: list[DataType], *, register_references: bool = True) -> list[DataType]:
    """Copy DataType trees while preserving shared model references."""
    return [_copy_data_type(data_type, register_references=register_references) for data_type in data_types]


def _root_type_models(model: DataModel) -> list[DataModel]:
    """Return the models that the type of the root field of ``model`` names."""
    if not model.fields:
        return []
    return [
        source_model
        for data_type in model.fields[0].data_type.all_data_types
        if data_type.reference and isinstance(source_model := data_type.reference.source, DataModel)
    ]


def _constrains_root_field(model: DataModel) -> bool:
    """Return whether the root field of ``model`` carries a constraint other than ``unique_items``."""
    if not model.fields:
        return False
    constraints = model.fields[0].constraints
    return isinstance(constraints, ConstraintsBase) and any(
        value is not None for name, value in constraints.model_dump().items() if name != "unique_items"
    )


def _has_preservable_root_constraints(model: DataModel) -> bool:
    """Return whether flattening a root wrapper would discard field-owned constraints.

    True when ``model``, or a model the snapshot of root types reaches from it, constrains its root field.
    """
    walk = walk_graph(
        GraphSnapshot.ROOT_CONSTRAINTS, EdgeKind.ROOT_TYPE, (model,), _model_key_of_model, _root_type_models
    )
    return graph_answer(
        GraphSnapshot.ROOT_CONSTRAINTS,
        any(map(_constrains_root_field, walk.node_of.values())),
        lambda: _walks_to_preservable_root_constraints(model, seen_models=set()),
    )


def _walks_to_preservable_root_constraints(model: DataModel, *, seen_models: set[int]) -> bool:
    """Return whether flattening a root wrapper would discard field-owned constraints, by walking the root types."""
    if not model.fields or id(model) in seen_models:
        return False
    seen_models.add(id(model))
    return _constrains_root_field(model) or any(
        _walks_to_preservable_root_constraints(source_model, seen_models=seen_models)
        for source_model in _root_type_models(model)
    )


def _copy_data_model_field(
    field: DataModelFieldBase,
    *,
    data_type: DataType | None = None,
    register_references: bool = True,
) -> DataModelFieldBase:
    """Copy a field and its mutable state without copying model references."""
    copied_data_type = data_type or _copy_data_type(
        field.data_type,
        register_references=register_references,
    )
    copied_field = field.model_copy(
        update={
            "data_type": copied_data_type,
            "parent": None,
        }
    )
    copied_field.extras = deepcopy(field.extras)
    if field.validation_aliases is not None:
        copied_field.validation_aliases = list(field.validation_aliases)
    match field.default:
        case dict() | list() | set():
            copied_field.default = deepcopy(field.default)
    copied_data_type.parent = copied_field
    return copied_field


def _get_inherited_type_modifiers(
    data_type: DataType,
    *,
    null_rule: NullRule | None = None,
) -> _InheritedTypeModifiers:
    """Compress a partial type before replacing its forward placeholder."""
    list_wrapper = (
        _copy_data_type(data_type, register_references=False)
        if not data_type.is_list and len(data_type.data_types) == 1 and data_type.data_types[0].is_list
        else None
    )
    return _InheritedTypeModifiers(
        null_rule=null_rule,
        is_optional=data_type.is_optional,
        is_dict=data_type.is_dict,
        is_list=data_type.is_list,
        is_set=data_type.is_set,
        is_frozen_set=data_type.is_frozen_set,
        is_mapping=data_type.is_mapping,
        is_sequence=data_type.is_sequence,
        is_tuple=data_type.is_tuple,
        tuple_item_count=data_type.tuple_item_count,
        kwargs=deepcopy(data_type.kwargs),
        list_wrapper=list_wrapper,
    )


def _without_null_members(data_type: DataType, current_type: DataType | _InheritedTypeModifiers) -> DataType:
    """Return a fresh copy of the inherited ``data_type`` without the None members its override excludes."""
    if not (
        isinstance(current_type, _InheritedTypeModifiers)
        and current_type.null_rule == "inherited-typed-without-null"
        and (members := union_members(data_type))
    ):
        return data_type
    kept = [member for member in members if member.type != NONE or member.data_types]
    rebuilt = data_type.model_copy(update={"data_types": build_union(DataType, kept).data_types})
    for member in rebuilt.data_types:
        member.parent = rebuilt
    return rebuilt


def _merge_data_type_modifiers(
    new_type: DataType,
    current_type: DataType | _InheritedTypeModifiers,
    *,
    preserve_container_shape: bool = False,
    preserve_optional: bool = False,
    preserve_inherited_kwargs: bool = False,
) -> None:
    """Merge an overriding type's container modifiers into an inherited type."""
    if preserve_optional:
        new_type.is_optional = new_type.is_optional or current_type.is_optional
    if isinstance(current_type, _InheritedTypeModifiers) and current_type.excludes_null:
        new_type.is_optional = False
    inherited_is_container = any((
        new_type.is_dict,
        new_type.is_list,
        new_type.is_set,
        new_type.is_frozen_set,
        new_type.is_mapping,
        new_type.is_sequence,
        new_type.is_tuple,
    ))
    if preserve_container_shape or inherited_is_container or not (new_type.reference or new_type.type):
        new_type.is_dict = new_type.is_dict or current_type.is_dict
        new_type.is_list = new_type.is_list or current_type.is_list
        new_type.is_set = new_type.is_set or current_type.is_set
        new_type.is_frozen_set = new_type.is_frozen_set or current_type.is_frozen_set
        new_type.is_mapping = new_type.is_mapping or current_type.is_mapping
        new_type.is_sequence = new_type.is_sequence or current_type.is_sequence
        new_type.is_tuple = new_type.is_tuple or current_type.is_tuple
        if new_type.tuple_item_count is None:
            new_type.tuple_item_count = current_type.tuple_item_count
    if preserve_inherited_kwargs or current_type.kwargs is None:
        return
    if new_type.kwargs is None:
        new_type.kwargs = deepcopy(current_type.kwargs)
        return
    for name, value in current_type.kwargs.items():
        new_type.kwargs[name] = deepcopy(value)


def _apply_inherited_field_nullability(
    field: DataModelFieldBase,
    inherited_field: DataModelFieldBase,
    copied_field: DataModelFieldBase,
    current_type: DataType | _InheritedTypeModifiers,
) -> None:
    """Keep omission optionality separate from the schema's null intersection."""
    if not copied_field.required and current_type.is_optional:
        copied_field.data_type.is_optional = True

    match current_type:
        case _InheritedTypeModifiers(excludes_null=True):
            copied_field.nullable = False if copied_field.required or field.nullable is False else None
            copied_field.type_has_null = False
        case _:
            copied_field.nullable = inherited_field.nullable
            copied_field.type_has_null = inherited_field.type_has_null


def _copy_resolved_inherited_field(  # noqa: PLR0913, PLR0915
    field: DataModelFieldBase,
    inherited_field: DataModelFieldBase,
    *,
    force_optional: bool = False,
    partial_merge_mode: AllOfMergeMode = AllOfMergeMode.All,
    register_references: bool = True,
    reserved_names: set[str] | None = None,
) -> DataModelFieldBase | None:
    """Resolve a deferred inherited field without copying its reference graph."""
    deferred_type = field.__dict__.get(_DEFERRED_INHERITED_TYPE_KEY)
    deferred_field = field.__dict__.get(_DEFERRED_INHERITED_FIELD_KEY)
    match deferred_type, deferred_field:
        case (DataType() | _InheritedTypeModifiers()) as current_type, _:
            metadata_source = field if partial_merge_mode == AllOfMergeMode.NoMerge else inherited_field
            copied_data_type = _copy_data_type(
                inherited_field.data_type,
                register_references=register_references,
            )
            copied_data_type = _without_null_members(copied_data_type, current_type)
            wrapper_template = (
                current_type.list_wrapper
                if isinstance(current_type, _InheritedTypeModifiers)
                else (
                    current_type
                    if not current_type.is_list
                    and len(current_type.data_types) == 1
                    and current_type.data_types[0].is_list
                    else None
                )
            )
            if wrapper_template is not None and copied_data_type.is_list:
                wrapper = _copy_data_type(wrapper_template, register_references=register_references)
                wrapper.data_types[0] = copied_data_type
                copied_data_type.parent = wrapper
                copied_data_type = wrapper
            else:
                _merge_data_type_modifiers(
                    copied_data_type,
                    current_type,
                    preserve_inherited_kwargs=partial_merge_mode == AllOfMergeMode.NoMerge,
                )
            copied_field = _copy_data_model_field(
                metadata_source,
                data_type=copied_data_type,
                register_references=False,
            )
            copied_field.name = field.name
            copied_field.required = False if force_optional else inherited_field.required or field.required
            _apply_inherited_field_nullability(field, inherited_field, copied_field, current_type)
            copied_field.original_name = field.original_name
            copied_field.alias = field.alias
            copied_field.validation_aliases = (
                list(field.validation_aliases) if field.validation_aliases is not None else None
            )
            copied_field.serialization_alias = field.serialization_alias
            copied_field.use_serialization_alias = field.use_serialization_alias
            if partial_merge_mode != AllOfMergeMode.NoMerge:
                copied_field.constraints = _overlay_constraints(
                    inherited_field.constraints, field.constraints, class_from="first-given"
                )
                copied_field.extras.update(deepcopy(field.extras))
                copied_field.read_only = inherited_field.read_only or field.read_only
                copied_field.write_only = inherited_field.write_only or field.write_only
            if field.has_default:
                copied_field.default = deepcopy(field.default)
                copied_field.has_default = True
                copied_field.use_default_with_required = field.use_default_with_required
                if _RAW_SCHEMA_DEFAULT_KEY in field.__dict__:
                    copied_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] = field.__dict__[_RAW_SCHEMA_DEFAULT_KEY]
            elif partial_merge_mode != AllOfMergeMode.All:
                copied_field.default = field.default
                copied_field.has_default = False
                copied_field.use_default_with_required = False
                if _RAW_SCHEMA_DEFAULT_KEY in field.__dict__:
                    copied_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] = field.__dict__[_RAW_SCHEMA_DEFAULT_KEY]
        case _, str():
            copied_field = _copy_data_model_field(inherited_field, register_references=register_references)
            copied_field.required = field.required
            copied_field.original_name = field.original_name
            copied_field.alias = field.alias
            copied_field.validation_aliases = (
                list(field.validation_aliases) if field.validation_aliases is not None else None
            )
            copied_field.serialization_alias = field.serialization_alias
            copied_field.use_serialization_alias = field.use_serialization_alias
            if inherited_field.name and (
                inherited_field.name == field.name
                or reserved_names is None
                or inherited_field.name not in reserved_names
            ):
                copied_field.name = inherited_field.name
                copied_field.alias = inherited_field.alias if field.alias is None else field.alias
            else:
                copied_field.name = field.name
        case _:
            return None

    copied_field.__dict__.pop(_DEFERRED_INHERITED_FIELD_KEY, None)
    copied_field.__dict__.pop(_DEFERRED_INHERITED_CLASS_KEY, None)
    copied_field.__dict__.pop(_DEFERRED_INHERITED_TYPE_KEY, None)
    return copied_field


def _module_qualified_name(reference: Reference, model_path_to_module_name: Mapping[str, str]) -> str:
    """Return the name of the referenced model qualified by its module, or its short name in the root module."""
    module_name = _get_model_module_name(cast("DataModel", reference.source), model_path_to_module_name)
    return f"{module_name}.{reference.short_name}" if module_name else reference.short_name


def _annotated_reference(data_type: DataType) -> Reference | None:
    """Return the type that the annotation of ``data_type`` names: its reference, or the enum of its enum members."""
    return data_type.reference or data_type._enum_member_literal_reference  # noqa: SLF001


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


def _derived_models(models: Iterable[DataModel]) -> dict[ModelKey, list[DataModel]]:
    """Map the model key of each base model to the models of ``models`` derived from it directly."""
    keys = _ModelKeys("derived models")
    derived_models: defaultdict[ModelKey, list[DataModel]] = defaultdict(list)
    for derived_model in models:
        for base_model in _find_base_classes(derived_model):
            derived_models[keys.of(base_model)].append(derived_model)
    return derived_models


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


def _hidden_type_names(model: DataModel) -> Collection[str | None]:
    """Return the names of the model's fields that hide a type named by an annotation of the model.

    Python looks an annotation up in the class namespace first. A library that resolves string annotations
    from the module still finds such a type when it is written as a string, like a forward reference.
    """
    if not type(model).RESOLVES_STRING_ANNOTATIONS_FROM_MODULE:
        return ()
    return {
        reference.short_name
        for field in model.fields
        for data_type in field.data_type.all_data_types
        if (reference := _annotated_reference(data_type)) is not None and not data_type.alias
    } & {field.name for field in model.fields if model.field_name_hides_types(field)}


def _no_hidden_type_names(model: DataModel) -> Collection[str | None]:  # noqa: ARG001
    """Report no hidden types, for annotations that a future import defers."""
    return ()


def _rename_field_preserving_alias(field: DataModelFieldBase, new_name: str) -> None:
    """Rename a Python field without losing an existing source-name alias."""
    if field.alias is None:
        field.alias = field.name
    field.name = new_name


class Result(BaseModel):
    """Generated code result with optional source file reference."""

    model_config = ConfigDict(defer_build=True)

    body: str
    future_imports: str = ""
    source: Optional[Path] = None  # noqa: UP045


class Source(BaseModel):
    """Schema source file with path and content."""

    model_config = ConfigDict(defer_build=True)

    path: Path
    text: str = ""
    raw_data: Any | None = None

    @classmethod
    def from_path(
        cls,
        path: Path,
        base_path: Path,
        encoding: str,
        *,
        data: bytes | None = None,
    ) -> Source:
        """Create a Source from a file path relative to base_path."""
        record_watch_dependency(path)
        return cls(
            path=path.relative_to(base_path),
            text=(
                path.read_text(encoding=encoding)
                if data is None
                else data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
            ),
        )

    @classmethod
    def from_cached_path(
        cls, path: Path, base_path: Path, encoding: str, *, keep_text: bool = False, data: bytes | None = None
    ) -> Source:
        """Create a Source from a cached parsed file path relative to base_path."""
        data, raw_data = _read_parser_source_data_from_path(path, encoding, data=data)
        return cls(
            path=path.relative_to(base_path),
            text=data.decode(encoding) if keep_text else "",
            raw_data=raw_data,
        )

    @classmethod
    def from_dict(cls, data: dict[str, YamlValue]) -> Source:
        """Create a Source from a dict."""
        return cls(path=Path(), raw_data=data)


def _is_any_variant(data_type: DataType) -> bool:
    return data_type.type == ANY or (
        not data_type.reference and not data_type.data_types and not data_type.literals and not data_type.type
    )


def _model_references(model: DataModel) -> list[Reference]:
    """Return the references of a model's data types in render order."""
    return [reference for data_type in model.all_data_types if (reference := data_type.reference)]


def _union_tags(model: DataModel) -> tuple[HashableComparable, ...]:
    """Return the field index, property and the tags of each variant of each discriminated union of a model.

    Unions that render alike decode other tags when their discriminators name another property or map their
    variants to other tags. The tags a mapping gives the variants are listed by variant position, so unions that
    map the same tags to variants that reuse merges match whatever schemas their mappings name.
    """
    if not (
        discriminators := [
            (index, discriminator, field.data_type.data_types)
            for index, field in enumerate(model.fields)
            if isinstance(discriminator := field.extras.get("discriminator"), dict)
        ]
    ):
        return ()
    from datamodel_code_generator._internal_utils import to_hashable  # noqa: PLC0415

    return tuple(
        (
            index,
            str(discriminator.get("propertyName")),
            tuple(
                to_hashable(_get_mapping_discriminator_values(source, mapping))
                if (mapping := discriminator.get("mapping"))
                and (reference := data_type.reference)
                and isinstance(source := reference.source, DataModel)
                else ()
                for data_type in data_types
            ),
        )
        for index, discriminator, data_types in discriminators
    )


@final
@dataclass(frozen=True, slots=True)
class _ModelShapeSource:
    """What the dedup and reuse keys of ``parser/_model_shapes.py`` read off the live models.

    A model writes what its rendering decides before it renders for a key (``DataModel._prepare_dedup_key``), so the
    rendering writes nothing.
    """

    target_python_version: PythonVersion

    def own_class_name(self, model: DataModel) -> str | None:  # noqa: PLR6301
        """Return the class name the model shares with its duplicates, or None for the name it renders under."""
        return model.duplicate_class_name

    def render(self, model: DataModel, class_name: str | None) -> tuple[str, Hashable, tuple[Hashable, ...]]:  # noqa: PLR6301
        """Return the model rendered under a class name, its imports and its values as written, uncached."""
        model._prepare_dedup_key()  # noqa: SLF001
        rendered, imports, *written = model._render_dedup_key(class_name)  # noqa: SLF001
        return rendered, imports, tuple(written)

    def dedup_key(self, model: DataModel, slot: KeySlot) -> tuple[Any, ...]:  # noqa: PLR6301
        """Return the dedup key the model cached in a slot, rendering and caching it when the slot is empty."""
        class_name, use_default = slot
        return model.get_dedup_key(class_name, use_default=use_default)

    def forget_dedup_keys(self, model: DataModel) -> None:  # noqa: PLR6301
        """Drop the dedup keys the model cached."""
        model._dedup_key_cache.clear()  # noqa: SLF001

    def mask_pattern(self, text: str) -> re.Pattern[str]:
        """Return the pattern that finds string literals, field declarations and names as the target names them."""
        return _string_or_identifier_pattern(text, self.target_python_version)

    def references(self, model: DataModel) -> list[Reference]:  # noqa: PLR6301
        """Return the references of the model's data types in render order."""
        return _model_references(model)

    def model_of(self, reference: Reference) -> DataModel | None:  # noqa: PLR6301
        """Return the model a reference names, or None when it names no model."""
        return source if isinstance(source := reference.source, DataModel) else None

    def identity(self, reference: Reference) -> ModelKey:  # noqa: PLR6301
        """Return what tells apart the models that references name: the model key."""
        return model_key_of(reference)

    def agreed(self, site: str, *, by_key: bool, by_path: Callable[[], bool]) -> bool:  # noqa: PLR6301
        """Return ``by_key``; under the ``MODEL_GRAPH`` check, a different ``by_path()`` is a violation."""
        return _keyed_answer(site, by_key, by_path)


def _distinct_bases(row: ShapeRow, bases: Iterable[BaseClassDataType]) -> Iterable[BaseClassDataType]:
    """Return the bases of a merged model's child once each, told apart as the user of the row tells them."""
    if row.base_dedup == "by-type-hint":
        return {f"{base.module_name}.{base.type_hint}": base for base in bases}.values()
    return {id(base.reference): base for base in bases}.values()


def _get_schema_path(model: DataModel | Reference) -> str:
    """Return the path of the schema a model is generated from, through read and write variants and reuse."""
    source = model if isinstance(model, DataModel) else model.source
    reused = source.reused_reference if isinstance(source, DataModel) else None
    return _source_path((reused or model).path)


def _tag_field_matches(
    field: DataModelFieldBase, match: TagFieldMatch, wanted: str, *, python_name: str | None = None
) -> bool:
    """Return whether a field holds the discriminator's property ``wanted`` (``tag_field_matches``)."""
    return tag_field_matches(
        match, wanted, name=field.name, original_name=field.original_name, alias=field.alias, python_name=python_name
    )


def _get_discriminator_field_value(discriminator_field: DataModelFieldBase) -> DiscriminatorValue | None:
    """Return the one tag a variant's tag field holds (``field_tag``)."""
    tag = field_tag("variant-field", _FieldTagFacts(discriminator_field))
    return None if tag is None else tag.value


@final
@dataclass(frozen=True, slots=True)
class _FieldTagFacts:
    """The facts of a field that ``field_tag`` reads: its ``const``, its ``Literal`` and the Enum its type names."""

    field: DataModelFieldBase

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
        """Return how many members the Enum the field's type names has, and the first one's value."""
        fields = enum_source.fields if (enum_source := self.field.data_type.find_source(Enum)) else []
        return (len(fields), get_raw_enum_member_value(fields[0].default)) if fields else (0, None)

    def referenced_tag(self) -> None:
        """Return no tag: a field has no ``$ref`` of its own."""


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


def _get_discriminator_values(
    discriminator_model: DataModel,
    field_name: str,
    mapping: dict[str, str],
    *,
    require_literal: bool = False,
    field_name_mode: TagFieldMatch = "python-or-original-name",
) -> list[DiscriminatorValue]:
    """Return the tags a union lists a variant by; one of a nested choice, never retagged, takes only its own tag."""
    return _variant_tags(
        "nested" if require_literal else "variant",
        discriminator_model,
        field_name,
        mapping,
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
    field_name_mode: TagFieldMatch,
) -> list[DiscriminatorValue]:
    """Return the tags a union selects a variant by: mapping values, the tag of its own schema, else its name."""
    return _variant_tags("union", discriminator_model, field_name, mapping, field_name_mode=field_name_mode)


def _variant_tags(
    source: VariantTagSource,
    discriminator_model: DataModel,
    field_name: str,
    mapping: dict[str, str],
    *,
    field_name_mode: TagFieldMatch,
) -> list[DiscriminatorValue]:
    """Return the tags of a variant (``variant_tags``) from its tag fields, the mapping and its schema path."""
    return [*variant_tags(source, _VariantTagFacts(discriminator_model, field_name, mapping, field_name_mode)).values]


@final
@dataclass(frozen=True, slots=True)
class _VariantTagFacts:
    """The facts of a variant that ``variant_tags`` reads, from its fields, the mapping and its schema path."""

    model: DataModel
    field_name: str
    mapping: dict[str, str]
    field_name_mode: TagFieldMatch

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

    def mapped_tags(self) -> list[str]:
        """Return the mapping keys naming the variant or its bases."""
        return _get_mapping_discriminator_values(self.model, self.mapping)

    def path_segment(self) -> str:
        """Return the last segment of the variant's schema path."""
        return _get_schema_path(self.model).split("/")[-1]


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


def _iter_mapping_discriminator_values(discriminator_model: DataModel, mapping: dict[str, str]) -> Iterator[str]:
    """Yield the mapping values that select a variant, computed when the first one is asked for."""
    yield from _get_mapping_discriminator_values(discriminator_model, mapping)


_DiscriminatorTag: TypeAlias = tuple[DataModel, DataModelFieldBase, DiscriminatorValue | None]
_DiscriminatorUnion: TypeAlias = tuple[DataModel, list[_DiscriminatorTag], str, dict[str, str]]


@dataclass(slots=True)
class _VariantDiscriminatorTags:
    """The tags a variant keeps from the first union that tagged it, and the other tags unions map it to."""

    variant: DataModel
    property_name: str
    kept: list[DiscriminatorValue]
    owner: str
    others: dict[str, list[DiscriminatorValue]]

    def warning_messages(self) -> Iterator[str]:
        """Describe the tags that the generated variant cannot carry."""
        if self.others:
            name = self.variant.class_name
            unions = ", ".join(f"{union} ({_format_tags(tags)})" for union, tags in self.others.items())
            yield (
                f"{name} is a variant of discriminated unions that map {self.property_name!r} to different tags: "
                f"it keeps {_format_tags(self.kept)} from {self.owner}, so these unions cannot decode it: {unions}."
            )


def _format_tags(tags: list[DiscriminatorValue]) -> str:
    """Spell discriminator tags as the document writes them."""
    return ", ".join(map(repr, tags))


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
    model: DataModel, source_name: str, tagged_fields: set[int], derived_models: Mapping[ModelKey, list[DataModel]]
) -> bool:
    """Return whether a base or derived struct keeps an instance field of that name once the unions are tagged."""
    return any(
        id(field) not in tagged_fields
        and not field.extras.get("is_classvar")
        and _tag_field_matches(field, "source-name", source_name)
        for field in chain(
            (field for base_model in _find_base_classes(model) for field in base_model.iter_all_fields()),
            _iter_derived_fields(model, derived_models, _ModelKeys("derived fields")),
        )
    )


def _tag_discriminator_unions(
    unions: list[_DiscriminatorUnion],
    derived_models: Mapping[ModelKey, list[DataModel]],
    *,
    can_retain_cache: bool,
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
                        if _has_related_instance_field(model, tag_field, tagged_fields, derived_models)
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
            _apply_discriminator_tags(tags, tag_field, can_retain_cache=can_retain_cache)
            continue
        warn(
            f"Discriminator {tag_field!r} of {owner.class_name} cannot be represented as msgspec tags: "
            f"{reason}; its variants are left untagged.",
            stacklevel=2,
        )
    return deferred


def _apply_discriminator_tags(tags: list[_DiscriminatorTag], tag_field: str, *, can_retain_cache: bool) -> None:
    """Tag the variants and take their discriminators out of the instance fields."""
    for model, field, value in tags:
        model.apply_discriminator_tag(field, tag_field, value)
        _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)


def _has_cyclic_ancestry(model: DataModel) -> bool:
    """Return whether the base classes of a model, however distant, inherit from one another in a cycle."""
    walk = walk_graph(GraphSnapshot.CYCLIC_ANCESTRY, EdgeKind.BASE, (model,), _model_key_of_model, _find_base_classes)
    return graph_answer(
        GraphSnapshot.CYCLIC_ANCESTRY,
        walk.graph.acyclic_order((EdgeKind.BASE,)) is None,
        lambda: _walks_to_cyclic_ancestry(model),
    )


def _walks_to_cyclic_ancestry(model: DataModel) -> bool:
    """Return whether the base classes of a model inherit from one another in a cycle, by walking them."""
    visiting: dict[int, bool] = {id(model): True}
    stack = [(id(model), iter(_find_base_classes(model)))]
    while stack:
        if (base := next(stack[-1][1], None)) is None:
            visiting[stack.pop()[0]] = False
            continue
        if visiting.get(id(base)):
            return True
        if id(base) not in visiting:
            visiting[id(base)] = True
            stack.append((id(base), iter(_find_base_classes(base))))
    return False


def _tag_fields_of_tagged_bases(models: Iterable[DataModel]) -> list[DataModel]:
    """Tag the structs that declare the tag field of a tagged base as a field, which msgspec rejects.

    A struct inherits the tag of its base, so its single literal becomes its own tag, as for a tagged variant, and a
    struct with any other such field opts out of the tag to keep the field. Bases of other modules are tagged by then,
    as every module is processed first, and a base is handled before the structs inheriting from it, as its own tag
    decides theirs. Inheritance cycles are reported later. Return the structs that opted out.
    """
    pending = {id(model): model for model in models}
    opted_out: list[DataModel] = []

    def tag_fields_of(model: DataModel) -> None:
        if pending.pop(id(model), None) is None:
            return
        if not model.base_classes or _has_cyclic_ancestry(model):
            return
        bases = _find_base_classes(model)
        for base in bases:
            tag_fields_of(base)
        if not (tag_fields := {tag[0] for base in bases if (tag := base.discriminator_tag) is not None}):
            return
        tagged_fields = [
            (field, tag_field, field_tag("legacy-literal-only", _FieldTagFacts(field)))
            for field in model.fields
            if not field.extras.get("is_classvar")
            and (
                tag_field := redeclared_tag_field(
                    tag_fields, name=field.name, original_name=field.original_name, alias=field.alias
                )
            )
            is not None
        ]
        if len(tags := [(field, name, tag) for field, name, tag in tagged_fields if tag is not None]) != len(
            tagged_fields
        ):
            model.opt_out_of_inherited_tag()
            opted_out.append(model)
            return
        for field, tag_field, tag in tags:
            model.apply_discriminator_tag(field, tag_field, tag.value)
            _clear_model_imports_cache_if_retained(model, can_retain_cache=True)

    while pending:
        tag_fields_of(next(iter(pending.values())))
    return opted_out


def _get_mapped_discriminator_tag(
    values: list[DiscriminatorValue], enum_source: Enum | None
) -> DiscriminatorValue | None:
    """Return the wire tag of a variant whose discriminator resolves to exactly one value."""
    if len(values) != 1:
        return None
    return Parser._get_enum_discriminator_literal(enum_source, values[0]) if enum_source else values[0]  # noqa: SLF001


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
    store: GenerationStore,
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
        store.set_field_constraints(field, type(constraints).model_validate(values))
        field.invalidate_semantic_caches()


def _iter_nested_discriminator_tag_fields(
    data_types: Iterable[DataType],
    field_name: str,
    *,
    field_name_mode: Literal["source-name", "python-or-original-name"],
) -> Iterator[tuple[DataModelFieldBase, Sequence[DiscriminatorValue]]]:
    """Yield the tag fields of the variants that nested choices give a union, with their tag values.

    Such a variant keeps the tag it declares or inherits, so the field giving it is its own one, else the inherited
    one, which the empty subclass of a reused model also reads.
    """
    for data_type, can_update_discriminator, _ in _iter_discriminator_data_types(data_types):
        source = data_type.reference.source if data_type.reference else None
        if can_update_discriminator or not isinstance(source, DataModel):
            continue
        for tag_field, value in islice(
            _iter_discriminator_fields(
                chain(source.fields, source.iter_all_fields()), field_name, field_name_mode=field_name_mode
            ),
            1,
        ):
            yield tag_field, (value,)


def _is_discriminator_container(data_type: DataType) -> bool:
    return (
        data_type.is_dict
        or data_type.is_list
        or data_type.is_set
        or data_type.is_frozen_set
        or data_type.is_mapping
        or data_type.is_sequence
        or data_type.is_tuple
    )


def _is_discriminator_wrapper(model: DataModel) -> bool:
    return model.IS_ALIAS or model.IS_ROOT_MODEL


def _iter_discriminator_data_types(
    data_types: Iterable[DataType],
    active_union_models: set[int] | None = None,
    *,
    can_update_discriminator: bool = True,
    discriminator_owner: int | None = None,
) -> Iterator[tuple[DataType, bool, int]]:
    for data_type in data_types:
        if data_type.is_union and not _is_discriminator_container(data_type):
            yield from _iter_discriminator_data_types(
                data_type.data_types,
                active_union_models,
                can_update_discriminator=False,
                discriminator_owner=discriminator_owner,
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
                    try:
                        yield from _iter_discriminator_data_types(
                            (source.fields[0].data_type,),
                            active_union_models,
                            can_update_discriminator=False,
                            discriminator_owner=owner,
                        )
                    finally:
                        active_union_models.remove(source_id)


_MsgspecRefusal: TypeAlias = tuple[tuple[DataModel, ...], str | None]
_NO_MSGSPEC_REFUSAL: _MsgspecRefusal = ((), None)


def _msgspec_refused_structs(models: Iterable[object]) -> _MsgspecRefusal:
    """Return the distinct Struct types of a union msgspec refuses, and how their tags clash if all are tagged.

    msgspec decodes a union of Struct types only if all are tagged, by one tag field, with distinct tags of one type.
    """
    structs = (
        *{
            id(model): model
            for model in models
            if isinstance(model, DataModel) and model.REQUIRES_TAGGED_UNION_DISCRIMINATOR
        }.values(),
    )
    if len(structs) < 2:  # noqa: PLR2004
        return _NO_MSGSPEC_REFUSAL
    if len(tags := [tag for struct in structs if (tag := struct.discriminator_tag) is not None]) < len(structs):
        return structs, None
    if (clash := struct_tag_clash(tags, "legacy-python-equality")) is None:
        return _NO_MSGSPEC_REFUSAL
    kind, values = clash
    if kind == "tag-fields":
        return structs, f"are tagged by different fields {_format_tags(values)}"
    if kind == "tag-types":
        return structs, "mix int and str tags"
    return structs, f"share the tag {_format_tags(values)}"


def _msgspec_refused_member_structs(data_types: Iterable[DataType]) -> _MsgspecRefusal:
    """Return the refused Struct types of a union, through the aliases it names."""
    return _msgspec_refused_structs(
        member.reference.source for member, _, _ in _iter_discriminator_data_types(data_types) if member.reference
    )


def _iter_union_alias_roots(data_type: DataType) -> Iterator[DataType]:
    """Yield the root types of the aliases a union names."""
    for member in data_type.data_types:
        if member.is_union and not _is_discriminator_container(member):
            yield from _iter_union_alias_roots(member)
        elif (
            member.reference
            and isinstance(source := member.reference.source, DataModel)
            and _is_discriminator_wrapper(source)
            and source.fields
        ):
            yield source.fields[0].data_type


def _msgspec_refused_union_structs(data_type: DataType) -> _MsgspecRefusal:
    """Return the Struct types of a union msgspec refuses, unless an alias it names is refused on its own."""
    if (
        not data_type.is_union
        or _is_discriminator_container(data_type)
        or not (refusal := _msgspec_refused_member_structs(data_type.data_types))[0]
        or any(_msgspec_refused_member_structs((root,))[0] for root in _iter_union_alias_roots(data_type))
    ):
        return _NO_MSGSPEC_REFUSAL
    return refusal


def _msgspec_union_message(name: str, refusal: _MsgspecRefusal, *, discriminated: bool) -> str:
    """Describe a union of Struct types that msgspec refuses to decode."""
    structs, clash = refusal
    names = ", ".join(struct.class_name for struct in structs)
    union = f"the discriminated union {name}" if discriminated else f"the union {name}"
    if clash is not None:
        return f"msgspec cannot decode {union}: its Struct types {names} {clash}."
    if discriminated:
        return (
            f"msgspec cannot decode {union}: its Struct types {names} are not all tagged. "
            "Pass --use-msgspec-tagged-unions to tag them."
        )
    return (
        f"msgspec cannot decode {union}: its Struct types {names} are not all tagged, and only the "
        "variants of a discriminated union are tagged, with --use-msgspec-tagged-unions."
    )


_MSGSPEC_STR_TYPES_REFUSED_BESIDE_LITERALS: frozenset[str] = frozenset({
    "bytes",
    "bytearray",
    "datetime",
    "date",
    "time",
    "timedelta",
    "UUID",
    "Decimal",
})
_MsgspecEnumClash: TypeAlias = tuple[str, tuple[str, ...]]


def _iter_written_union_members(data_types: Iterable[DataType]) -> Iterator[DataType]:
    """Yield the members of a union as written, spreading the member unions that render as part of it."""
    for data_type in data_types:
        if data_type.is_union and not _is_discriminator_container(data_type):
            yield from _iter_written_union_members(data_type.data_types)
        else:
            yield data_type


def _msgspec_enum_clashes(data_types: Iterable[DataType]) -> tuple[_MsgspecEnumClash, ...]:
    """Return each kind and its written members of a union that msgspec decodes once its Enums are written as Literal.

    msgspec decodes a union only if it holds an Enum of str values as its one str-like member and an Enum of int
    values as its one int-like member, while it takes Literal values beside each other and beside str or int. A
    member of another str-like type, such as bytes, refuses a Literal of str values too, so it leaves the str side be.
    """
    enums: dict[str, set[int]] = {"str": set(), "int": set()}
    names: dict[str, dict[str, None]] = {"str": {}, "int": {}}
    others: set[str] = set()
    refused: set[str] = set()
    for member in _iter_written_union_members(data_types):
        for leaf, _, _ in _iter_discriminator_data_types((member,)):
            if isinstance(source := leaf.reference.source if leaf.reference else None, Enum):
                if (
                    kinds := {literal_value_kind(get_raw_enum_member_value(field.default)) for field in source.fields}
                ) in ({"str"}, {"int"}):
                    enums[kind := kinds.pop()].add(id(source))
                    names[kind][member.type_hint] = None
            elif source is None and not leaf.data_types and not leaf.enum_member_literals:
                if leaf.type in _MSGSPEC_STR_TYPES_REFUSED_BESIDE_LITERALS:
                    refused.add("str")
                for kind in (
                    {literal_value_kind(literal) for literal in leaf.literals} or {leaf.type or ""}
                ) & names.keys():
                    others.add(kind)
                    names[kind][member.type_hint] = None
    return tuple(
        (kind, (*names[kind],))
        for kind, ids in enums.items()
        if ids and len(ids) + (kind in others) > 1 and kind not in refused
    )


def _msgspec_refused_union_enums(data_type: DataType) -> tuple[_MsgspecEnumClash, ...]:
    """Return each kind of a union msgspec refuses for an Enum, unless an alias it names is refused for that kind."""
    if (
        not data_type.is_union
        or _is_discriminator_container(data_type)
        or not (clashes := _msgspec_enum_clashes(data_type.data_types))
    ):
        return ()
    covered = {kind for root in _iter_union_alias_roots(data_type) for kind, _ in _msgspec_enum_clashes((root,))}
    return tuple(clash for clash in clashes if clash[0] not in covered)


def _msgspec_field_enum_clashes(data_types: Iterable[DataType]) -> dict[str, tuple[str, ...]]:
    """Return the written members of each kind that the unions among some types are refused for, the first union's."""
    clashes: dict[str, tuple[str, ...]] = {}
    for kind, members in chain.from_iterable(map(_msgspec_refused_union_enums, data_types)):
        clashes.setdefault(kind, members)
    return clashes


def _msgspec_enum_union_message(name: str, clash: _MsgspecEnumClash) -> str:
    """Describe a union that msgspec refuses to decode because it holds an Enum beside another member of its kind."""
    kind, members = clash
    return (
        f"msgspec cannot decode the union {name}: its {kind}-like members {', '.join(members)} include an Enum, "
        f"while a msgspec union holds an Enum only as its one {kind}-like member. Pass --enum-field-as-literal all "
        f"to write enums as Literal values, which msgspec decodes beside {kind} and other Literal values."
    )


def _literal_member(data_type: DataType) -> tuple[str, bool] | None:
    """Return the first member of a class or module written as a ``Literal`` value in a type, as written.

    The flag tells an enum member that ``--input-model`` or ``x-python-import`` symbols transport, which is one for
    sure, from a dotted ``x-python-type`` value, which may also name a constant of a module, such as
    ``http.client.HTTP_PORT``, that the generator cannot tell from an enum member.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeRuntimeSymbol,
        iter_literal_members,
        render_python_type_expr,
    )

    return next(
        (
            (render_python_type_expr(member), isinstance(member, PythonTypeRuntimeSymbol))
            for nested in data_type.all_data_types
            if nested.python_type is not None
            for member in iter_literal_members(nested.python_type.expression)
        ),
        None,
    )


def _msgspec_literal_member_message(name: str, member: tuple[str, bool]) -> str:
    """Describe a ``Literal`` value written as a member, which msgspec decodes only as None, bool, int or str."""
    value, enum_member = member
    if enum_member:
        return (
            f"msgspec cannot decode {name}: its Literal holds the enum member {value}, while a msgspec Literal holds "
            "only None, bool, int and str values."
        )
    return (
        f"msgspec decodes {name} only if {value} is a plain None, bool, int or str value: a msgspec Literal holds "
        "no other value, such as an enum member."
    )


def _msgspec_null_enum_message(model: Enum) -> str | None:
    """Describe an enum whose null member msgspec refuses, if the other members are all str or all int."""
    values = {field.name: get_raw_enum_member_value(field.default) for field in model.fields}
    if not (nulls := [name for name, value in values.items() if value is None]) or not (
        (kinds := {literal_value_kind(value) for value in values.values() if value is not None}) <= {"str"}
        or kinds <= {"int"}
    ):
        return None
    member = f"its member {nulls[0]} = None"
    fix = (
        "Pass --use-msgspec-nullable-enum-alias to write it without the null member and with a nullable alias "
        "that msgspec decodes."
    )
    if isinstance(model, IntEnum):
        return f"The IntEnum {model.class_name} cannot be imported: {member} is not an int. {fix}"
    return (
        f"msgspec cannot decode the enum {model.class_name}: {member} makes its values neither all str nor all int. "
        f"{fix}"
    )


def _is_enum_member_tag(
    model: DataModel, field_name: str, tag_name: str, value: DiscriminatorValue, *, use_field_name: bool
) -> bool:
    """Return whether the Enum typing a variant's tag field has a member for ``value``, written as that member."""
    enum_source = next(
        (
            enum_source
            for field in model.fields
            if _tag_field_matches(field, "source-name" if use_field_name else "python-or-original-name", tag_name)
            and (enum_source := field.data_type.find_source(Enum))
        ),
        None,
    ) or _get_enum_from_base(model, field_name, use_field_name=use_field_name)
    return enum_source is not None and enum_source.find_member(value, coerce_strings=True) is not None


def _discriminator_variants_are_valid(
    data_types: Iterable[DataType],
    field_name: str,
    mapping: dict[str, str],
    *,
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
        for key in (tag_key(value, "legacy-python-equality") for value in discriminator_values):
            if (previous_owner := discriminator_value_owners.get(key)) is not None and previous_owner != owner:
                return False
            discriminator_value_owners[key] = owner
    return True


def _get_enum_from_base(
    discriminator_model: DataModel, field_name: str, *, use_field_name: bool = False
) -> Enum | None:
    for base_class in discriminator_model.base_classes:
        if not base_class.reference or not base_class.reference.source:  # pragma: no cover
            continue
        base_model = base_class.reference.source
        if not isinstance(base_model, DataModel) or not base_model.SUPPORTS_INHERITED_DISCRIMINATOR_ENUM:
            continue
        for base_field in base_model.fields:  # pragma: no branch
            if not _tag_field_matches(
                base_field, "python-name" if use_field_name else "python-or-original-name", field_name
            ):
                continue
            if enum_from_base := base_field.data_type.find_source(Enum):  # pragma: no branch
                return enum_from_base
    return None


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
        ref_module_name = data_type.full_name.rsplit(".", 1)[0] if "." in data_type.full_name else ""
    return f"{ref_module_name}.{reference.short_name}" if ref_module_name else reference.short_name


def _register_data_type_import(  # ruff: ignore[too-many-arguments]
    data_type: DataType,
    model: DataModel,
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: dict[str, str] | None,
    *,
    ledger: ModuleImportLedger,
    producer: ImportProducer,
) -> None:
    match data_type.reference:
        case None:
            return
        case reference:
            pass

    model_path_to_module_name = model_path_to_module_name or {}
    current_module_name = _get_model_module_name(model, model_path_to_module_name)
    from_, import_ = full_path = relative(
        current_module_name,
        target_full_name := _get_data_type_target_full_name(data_type, reference, model_path_to_module_name),
    )
    if imports.use_exact:
        from_, import_ = full_path = _resolve_exact_import(
            current_module_name,
            target_full_name,
            from_,
            import_,
            reference.short_name,
        )
    if not (from_ and import_):
        return

    alias = scoped_model_resolver.add(full_path, import_)
    data_type.alias = alias.name if reference.short_name == import_ else f"{alias.name}.{reference.short_name}"
    ledger.append(
        imports,
        Import(
            from_=from_,
            import_=import_,
            alias=alias.name,
            reference_path=reference.path,
        ),
        producer,
    )


def _resolve_module_file(module_: ModulePath, results: dict[ModulePath, Result]) -> tuple[ModulePath, bool]:
    is_init = False

    if module_:
        if len(module_) == 1:
            parent: ModulePath = ("__init__.py",)
            if parent not in results:
                results[parent] = Result(body="")
        else:
            for i in range(1, len(module_)):
                parent = (*module_[:i], "__init__.py")
                if parent not in results:
                    results[parent] = Result(body="")
        if (*module_, "__init__.py") in results:
            return (*module_, "__init__.py"), True
        return tuple(part.replace("-", "_") for part in (*module_[:-1], f"{module_[-1]}.py")), is_init

    return ("__init__.py",), is_init


def _format_body_safe(body: str, code_formatter: CodeFormatter, *, generated_code: bool = False) -> str:
    try:
        return code_formatter._format_generated_code(body) if generated_code else code_formatter.format_code(body)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        warn(
            f"Failed to format code: {exc!r}. Emitting unformatted output.",
            stacklevel=1,
        )
        return body


def _remap_imports(ledger: ModuleImportLedger, imports: Imports, overrides: Mapping[str, str]) -> None:
    """Move overridden names to their modules, converting import override conflicts to a user-facing generator error."""
    try:
        ledger.remap_modules(imports, overrides, _IMPORT_OVERRIDES)
    except ValueError as e:
        raise Error(str(e)) from e


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


class Parser(ABC, Generic[ParserConfigT, SchemaFeaturesT]):
    """Abstract base class for schema parsers.

    Provides the parsing algorithm and code generation. Subclasses implement
    parse_raw() to handle specific schema formats.

    Type Parameters:
        ParserConfigT: The configuration type for this parser.
        SchemaFeaturesT: The schema features type (JsonSchemaFeatures or subclass).
    """

    @property
    @abstractmethod
    def schema_features(self) -> SchemaFeaturesT:
        """Get schema features based on detected version.

        Returns:
            Schema features instance with version-specific flags.
        """
        ...

    _generation_store_factory = staticmethod(GenerationStore.create_with_results)
    _model_resolver_factory = staticmethod(ModelResolver)
    _copy_model_field = staticmethod(partial(_copy_data_model_field))
    _copy_model_type = staticmethod(partial(_copy_data_type))
    _copy_inherited_field = staticmethod(partial(_copy_resolved_inherited_field))

    _config_class_name: ClassVar[str] = "ParserConfig"
    _cache_local_sources_during_parse: ClassVar[bool] = False
    _cache_parsed_sources_from_path: ClassVar[bool] = False
    _collapses_list_item_root_models: ClassVar[bool] = True
    _DECISION_PASSES: ClassVar[tuple[DecisionPass[Any], ...]] = ()
    """The decisions of the decision phase, in registration order; ``parser/_phases.py`` says how they run."""
    _http_fetch_session: _HTTPFetchSession | None = None
    _dropped_constraint_fields: DroppedConstraintFields = frozenset()
    _schema_graph: SchemaGraphBuilder | None = None

    @property
    def run_context(self) -> ParserRunContext:
        """Return the immutable settings for the current facade-managed run."""
        return self._run_context or _DEFAULT_PARSER_RUN_CONTEXT

    def configure_run_context(  # noqa: PLR0913
        self,
        *,
        diagnostic_source_path: Path | None = None,
        prefetched_source: tuple[Path, bytes] | None = None,
        formatter_cwd: Path | None = None,
        preserve_circular_root_models: bool = False,
        suppress_parse_warnings: bool = False,
        untyped_nullable_maps: bool = False,
        yielding_names: tuple[tuple[str, tuple[str, str | None]], ...] = (),
    ) -> None:
        """Configure parser run state without exposing implementation attributes."""
        context = ParserRunContext(
            diagnostic_source_path=diagnostic_source_path,
            prefetched_source=prefetched_source,
            formatter_cwd=formatter_cwd,
            preserve_circular_root_models=preserve_circular_root_models,
            suppress_parse_warnings=suppress_parse_warnings,
            untyped_nullable_maps=untyped_nullable_maps,
            yielding_names=yielding_names,
        )
        self._run_context = None if context == _DEFAULT_PARSER_RUN_CONTEXT else context
        self.model_resolver.assigned_yielding_names = dict(yielding_names)

    def _configure_legacy_run_context_attribute(self, attribute: str, value: object) -> None:
        """Keep historical private state writes as a parser-local compatibility facade."""
        context = self.run_context
        diagnostic_source_path = context.diagnostic_source_path
        formatter_cwd = context.formatter_cwd
        preserve_circular_root_models = context.preserve_circular_root_models
        match attribute:
            case "diagnostic_source_path":
                diagnostic_source_path = cast("Path | None", value)
            case "formatter_cwd":
                formatter_cwd = cast("Path | None", value)
            case _:
                preserve_circular_root_models = cast("bool", value)
        self.configure_run_context(
            diagnostic_source_path=diagnostic_source_path,
            formatter_cwd=formatter_cwd,
            preserve_circular_root_models=preserve_circular_root_models,
            suppress_parse_warnings=context.suppress_parse_warnings,
            prefetched_source=context.prefetched_source,
            untyped_nullable_maps=context.untyped_nullable_maps,
            yielding_names=context.yielding_names,
        )

    @property
    def _diagnostic_source_path(self) -> Path | None:
        return self.run_context.diagnostic_source_path

    @_diagnostic_source_path.setter
    def _diagnostic_source_path(self, value: Path | None) -> None:
        self._configure_legacy_run_context_attribute("diagnostic_source_path", value)

    @property
    def _formatter_cwd(self) -> Path | None:
        return self.run_context.formatter_cwd

    @_formatter_cwd.setter
    def _formatter_cwd(self, value: Path | None) -> None:
        self._configure_legacy_run_context_attribute("formatter_cwd", value)

    @property
    def _preserve_circular_root_models(self) -> bool:
        return self.run_context.preserve_circular_root_models

    @_preserve_circular_root_models.setter
    def _preserve_circular_root_models(self, value: bool) -> None:
        self._configure_legacy_run_context_attribute("preserve_circular_root_models", value)

    @classmethod
    def _get_config_class(cls) -> type[ParserConfig]:
        """Return the config class for this parser.

        Uses _config_class_name class variable to dynamically import the config class.
        Subclasses should set _config_class_name to their config class name.
        """
        import importlib  # noqa: PLC0415

        module = importlib.import_module("datamodel_code_generator.config")
        return getattr(module, cls._config_class_name)

    @classmethod
    def _create_default_config(cls, options: ParserConfigDict) -> ParserConfigT:
        """Create a default config from options.

        Uses _get_config_class() to determine which config class to instantiate.
        """
        from datamodel_code_generator.config import _rebuild_config_model  # noqa: PLC0415

        config_class = cls._get_config_class()

        _rebuild_config_model(
            config_class,
            {
                "StrictTypes": StrictTypes,
                "DataModel": DataModel,
                "DataModelFieldBase": DataModelFieldBase,
                "DataTypeManager": DataTypeManager,
            },
        )
        return config_class.model_validate(options)  # ty: ignore[invalid-return-type]

    def _create_data_model(self, model_type: type[DataModel] | None = None, **kwargs: Any) -> DataModel:
        """Create data model instance with dataclass_arguments support for DataClass."""
        if self.config.aliases and (fields := kwargs.get("fields")):
            self.model_resolver.prepare_explicit_field_aliases(fields, kwargs["reference"], self.field_name_model_type)
        # Add class decorators if not already provided
        if "decorators" not in kwargs and self.class_decorators:
            kwargs["decorators"] = list(self.class_decorators)
        data_model_class = model_type or self.data_model_type
        data_model_class.prepare_constructor_arguments(
            kwargs,
            dataclass_arguments=self.dataclass_arguments,
            frozen=self.frozen_dataclasses,
            keyword_only=self.keyword_only,
        )
        return data_model_class(**kwargs)

    def __init__(  # noqa: PLR0915
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        config: ParserConfigT | None = None,
        **options: Unpack[ParserConfigDict],
    ) -> None:
        """Initialize the Parser with configuration options.

        Args:
            source: The schema source to parse.
            config: Optional ParserConfig object with all configuration options.
            **options: Individual configuration options (alternative to config).

        Raises:
            ValueError: If both config and **options are provided.
        """
        if config is not None and options:
            msg = "Cannot specify both 'config' and keyword arguments. Use one or the other."
            raise ValueError(msg)

        if config is None:
            config = self._create_default_config(options)

        self.config = config
        self._source_context = getattr(config, "_source_context", None) or _source_context_from_config(config)
        self._has_bound_python_types = False
        self._has_runtime_expressions = False
        self._constrained_decimal_alias_model_ids: set[int] = set()
        self._hidden_named_types: dict[tuple[ModelKey, str | None], bool] = {}
        self._hiding_field_names: dict[tuple[ModelKey, str | None], str] = {}
        self._variant_discriminator_tags: dict[tuple[int, str], _VariantDiscriminatorTags] = {}
        self._deferred_tagged_unions: list[_DiscriminatorUnion] = []
        self._defers_tagged_unions = False
        self._prepared_modules: list[_PreparedModule] = []
        """The output modules ``_process_single_module`` processed, in module order, for their render preparation."""
        self._base_stand_ins: dict[int, Reference | None] = {}
        """The reference of each base that a model-level type override replaced, by the base's id: its model stands
        in for the replacing class in constructor ordering."""
        self._generic_base_model: DataModel | None = None
        """The empty base model ``--use-generic-base-class`` generated, which constructor ordering reads as no base."""
        self._removed_reuse_users: dict[int, tuple[DataType, list[str]]] = {}
        self._renamed_types: dict[ModelKey, list[_RenamedType]] = {}
        """The class names each renamed model had before, by model key."""
        self._renamed_type_paths: dict[str, list[_RenamedType]] = {}
        """The class names each renamed model had before, by its path when it was renamed: row
        ``legacy-keyless-reference-by-path`` reads it for a reference that no model was built with."""
        self._replaced_root_models: dict[int, tuple[DataModel, DataModel]] = {}
        """Each root model that collapse replaced by its inner model, by id: the root model and its inner model."""
        self._emitting_module_names: dict[int, str] = {}
        self._emitting_module_lists: list[tuple[str, list[DataModel]]] = []
        self._collapsed_reuse_models: dict[int, list[DataModel]] = {}
        self._collapsed_reuse_users: dict[int, tuple[DataType, Reference]] = {}
        self._reuse_union_tags: dict[DataModel, tuple[HashableComparable, ...]] = {}
        self._recursion_facts: RecursionFacts = _NO_RECURSION
        self._reused_variant_tag_fields: dict[int, DataModelFieldBase] = {}
        self._quotes_hidden_types = False

        self.keyword_only = config.keyword_only
        self.target_pydantic_version = config.target_pydantic_version
        self.frozen_dataclasses = config.frozen_dataclasses
        self.data_type_manager: DataTypeManager = config.data_type_manager_type(
            python_version=config.target_python_version,
            use_standard_collections=config.use_standard_collections,
            use_generic_container_types=config.use_generic_container_types,
            use_non_positive_negative_number_constrained_types=config.use_non_positive_negative_number_constrained_types,
            use_decimal_for_multiple_of=config.use_decimal_for_multiple_of,
            strict_types=config.strict_types,
            use_union_operator=config.use_union_operator,
            use_pendulum=config.use_pendulum,
            use_standard_primitive_types=config.use_standard_primitive_types,
            use_object_type=config.use_object_type,
            target_datetime_class=config.target_datetime_class,
            target_date_class=config.target_date_class,
            treat_dot_as_module=config.treat_dot_as_module or False,
            use_serialize_as_any=config.use_serialize_as_any,
        )
        self.data_type_manager.omit_redundant_constraints = config.omit_redundant_constraints
        self.data_model_type: type[DataModel] = config.data_model_type
        self.data_model_root_type: type[DataModel] = config.data_model_root_type
        self.pydantic_v2_root_model_type: type[DataModel] | None = _get_field_dependency_ordering_model_type(
            self.data_model_root_type
        )
        self.data_model_field_type: type[DataModelFieldBase] = config.data_model_field_type
        self._data_model_field_constructor = _get_model_field_constructor(self.data_model_field_type)
        self._configured_generation_types_are_builtin = all(
            generation_type.__module__.startswith(_MODEL_MODULE_PREFIX)
            for generation_type in (
                self.data_model_type,
                self.data_model_root_type,
                self.data_model_field_type,
                type(self.data_type_manager),
            )
        ) and all(
            (generation_type := getattr(config, attribute, None)) is None
            or generation_type.__module__.startswith(_MODEL_MODULE_PREFIX)
            for attribute in ("data_model_scalar_type", "data_model_union_type")
        )
        self._uses_standard_generation_templates = False

        self._import_ledger = ModuleImportLedger()
        self.imports: Imports = Imports(config.use_exact_imports)
        self.use_exact_imports: bool = config.use_exact_imports
        self.use_type_checking_imports: bool | None = config.use_type_checking_imports
        self._append_additional_imports(additional_imports=config.additional_imports)
        self.class_decorators: list[str] = config.class_decorators or []

        self.base_class: str | None = config.base_class
        self.base_class_map: dict[str, str | list[str]] | None = config.base_class_map
        self.target_python_version: PythonVersion = config.target_python_version
        self.builtin_names: frozenset[str] = target_builtin_names(self.target_python_version.version_key)
        self.generation_store, self.results = self._generation_store_factory()
        self._merged_copies: set[DataModel] = set()
        self.model_metadata: ModelMetadata | None = None
        self.model_metadata_identities: tuple[str, ...] = ()
        self.generated_class_names: tuple[tuple[str, str], ...] = ()
        self.invalid_dotted_stdout_repair_modules: tuple[ModulePath, ...] = ()
        self.generated_model_inventory: tuple[str, ...] | None = None
        self.source_data_fingerprint: bytes | None = None
        self.stdout_result_usable: bool = True
        self.dump_resolve_reference_action: Callable[[Iterable[str]], str] | None = config.dump_resolve_reference_action
        self.validation: bool = config.validation
        self.field_constraints: bool = config.field_constraints
        self.snake_case_field: bool = config.snake_case_field
        self.strip_default_none: bool = config.strip_default_none
        self.serialization_aliases: Mapping[str, str] = config.serialization_aliases or {}
        self.apply_default_values_for_required_fields: bool = config.apply_default_values_for_required_fields
        self.force_optional_for_required_fields: bool = config.force_optional_for_required_fields
        self.use_schema_description: bool = config.use_schema_description
        self.use_field_description: bool = config.use_field_description
        self.use_field_description_example: bool = config.use_field_description_example
        self.use_inline_field_description: bool = config.use_inline_field_description
        self.use_single_line_docstring: bool = config.use_single_line_docstring
        self.use_default_kwarg: bool = config.use_default_kwarg
        self.use_missing_sentinel: bool = config.use_missing_sentinel
        self.deserialize_default_value_types: frozenset[DefaultValueType] = frozenset(config.deserialize_default_values)
        self._decimal_default_warning_count = 0
        self._decimal_default_warning_examples: list[str] | None = None
        self._data_model_field_common_kwargs_cache: dict[str, Any] = {
            "use_missing_sentinel": self.use_missing_sentinel,
            "target_python_version": self.target_python_version,
        }
        self.reuse_model: bool = config.reuse_model
        self._typed_map_value_reference_ids: set[int] = set()
        self._const_narrowed_references: list[tuple[Reference, _ConstNarrowing]] = []
        self._const_narrowed_values: dict[int, tuple[DataType, _ConstNarrowing]] = {}
        self._enum_admits_other_values: list[tuple[DataModelFieldBase, _ConstNarrowing]] = []
        self.typed_nullable_maps = False
        self.yielding_names_to_assign: tuple[tuple[str, tuple[str, str | None]], ...] = ()
        self.typed_nullable_map_reference_ids: set[int] = set()
        self.typed_map_values_may_move_models = False
        self.model_placement: dict[ModulePath, frozenset[str]] = {}
        self.reuse_scope: ReuseScope | None = config.reuse_scope
        self.shared_module_name: str = config.shared_module_name
        self.encoding: str = self._source_context.encoding
        self.enum_field_as_literal: LiteralType | None = config.enum_field_as_literal
        self.enum_field_as_literal_map: dict[str, str] = config.enum_field_as_literal_map or {}
        self.ignore_enum_constraints: bool = config.ignore_enum_constraints
        self.set_default_enum_member: bool = config.set_default_enum_member
        self.use_subclass_enum: bool = config.use_subclass_enum
        self.use_specialized_enum: bool = config.use_specialized_enum
        self.strict_nullable: bool = config.strict_nullable
        self.use_generic_container_types: bool = config.use_generic_container_types
        self.use_union_operator: bool = config.use_union_operator
        self.enable_faux_immutability: bool = config.enable_faux_immutability
        self.custom_class_name_generator: Callable[[str], str] | None = config.custom_class_name_generator
        self.repair_invalid_dotted_stdout: bool = config.repair_invalid_dotted_stdout
        self.forced_invalid_dotted_stdout_repair_modules: tuple[ModulePath, ...] = (
            config.forced_invalid_dotted_stdout_repair_modules
        )
        self.field_extra_keys: set[str] = config.field_extra_keys or set()
        self.field_extra_keys_without_x_prefix: set[str] = config.field_extra_keys_without_x_prefix or set()
        self.model_extra_keys: set[str] = config.model_extra_keys or set()
        self.model_extra_keys_without_x_prefix: set[str] = config.model_extra_keys_without_x_prefix or set()
        self.field_include_all_keys: bool = config.field_include_all_keys

        self.remote_text_cache: DefaultPutDict[str, str] = self._source_context.remote_text_cache or DefaultPutDict()
        self.current_source_path: Path | None = None
        self._run_context: ParserRunContext | None = None
        self.use_title_as_name: bool = config.use_title_as_name
        self.use_tag_as_union_variant_name: bool = config.use_tag_as_union_variant_name
        self.infer_union_variant_names: bool = config.infer_union_variant_names or self.use_tag_as_union_variant_name
        self.use_operation_id_as_name: bool = config.use_operation_id_as_name
        self.use_unique_items_as_set: bool = config.use_unique_items_as_set
        self.use_tuple_for_fixed_items: bool = config.use_tuple_for_fixed_items
        self.use_tuple_for_fixed_length_arrays: bool = config.use_tuple_for_fixed_length_arrays
        self.use_total_false_for_typed_dict: bool = config.use_total_false_for_typed_dict
        self.use_closed_typed_dict: bool = config.use_closed_typed_dict
        self.allof_merge_mode: AllOfMergeMode = config.allof_merge_mode
        self.allof_class_hierarchy: AllOfClassHierarchy = config.allof_class_hierarchy
        self.dataclass_arguments = config.dataclass_arguments

        if self._source_context.base_path:
            self.base_path = self._source_context.base_path
        elif isinstance(source, Path):
            self.base_path = source.absolute() if source.is_dir() else source.absolute().parent
        else:
            self.base_path = Path.cwd()

        self.source: str | Path | list[Path] | ParseResult | dict[str, YamlValue] = source
        self._cache_local_sources = False
        self._local_source_cache: tuple[Source, ...] | None = None
        self._use_parsed_source_cache = (
            _is_parsed_source_cache_enabled()
            and self._cache_parsed_sources_from_path
            and isinstance(source, Path | list)
        )
        self.custom_template_dir = config.custom_template_dir
        self.extra_template_data: defaultdict[str, Any] = (
            copy_extra_template_data(data) if (data := config.extra_template_data) else defaultdict(dict)
        )
        self.validators = config.validators
        self.generate_schema_validators: bool = config.generate_schema_validators
        self._set_typed_extra_annotation_mode(use_deferred_annotations=True)

        self.data_model_type.configure_required_fields(
            self.extra_template_data,
            target_python_version=self.target_python_version,
            use_total_false=self.use_total_false_for_typed_dict,
        )

        self.use_generic_base_class: bool = config.use_generic_base_class
        self.generic_base_class_config = prepare_output_model_config(
            self.extra_template_data,
            validators=config.validators,
            use_generic_base_class=config.use_generic_base_class,
            allow_population_by_field_name=config.allow_population_by_field_name,
            alias_generator=config.alias_generator,
            no_alias=config.no_alias,
            allow_extra_fields=config.allow_extra_fields,
            extra_fields=config.extra_fields,
            enable_faux_immutability=config.enable_faux_immutability,
            use_attribute_docstrings=config.use_attribute_docstrings,
            use_single_line_docstring=config.use_single_line_docstring,
            target_pydantic_version=config.target_pydantic_version,
            schema_validator_base_class_name=config.schema_validator_base_class_name,
            generate_schema_validators=config.generate_schema_validators,
        )

        self.model_resolver = self._model_resolver_factory(
            base_url=source.geturl() if isinstance(source, ParseResult) else None,
            singular_name_suffix="" if config.disable_appending_item_suffix else None,
            aliases=config.aliases,
            model_name_map=config.model_name_map,
            empty_field_name=config.empty_enum_field_name,
            snake_case_field=config.snake_case_field,
            custom_class_name_generator=config.custom_class_name_generator,
            base_path=self.base_path,
            original_field_name_delimiter=config.original_field_name_delimiter,
            special_field_name_prefix=config.special_field_name_prefix,
            remove_special_field_name_prefix=config.remove_special_field_name_prefix,
            capitalise_enum_members=config.capitalise_enum_members,
            no_alias=config.no_alias,
            use_subclass_enum=config.use_subclass_enum,
            target_python_version=config.target_python_version,
            parent_scoped_naming=config.parent_scoped_naming,
            treat_dot_as_module=config.treat_dot_as_module,
            strict_dotted_module_names=config.strict_dotted_module_names,
            naming_strategy=config.naming_strategy,
            duplicate_name_suffix_map=config.duplicate_name_suffix,
            class_name_prefix=config.class_name_prefix,
            class_name_suffix=config.class_name_suffix,
            class_name_affix_scope=config.class_name_affix_scope,
            skip_affix_for_root=config.class_name is not None,
            default_value_overrides=config.default_value_overrides,
            http_backend=self._source_context.http_backend,
            field_name_resolver_classes=(
                {self.field_name_model_type: field_name_resolver_class}
                if (field_name_resolver_class := self.data_model_type.FIELD_NAME_RESOLVER_CLASS) is not None
                else None
            ),
        )
        self.class_name: str | None = config.class_name
        self.allow_leading_underscore_class_name: bool = config.allow_leading_underscore_class_name
        self.wrap_string_literal: bool | None = config.wrap_string_literal
        self.allow_remote_refs: bool | None = self._source_context.allow_remote_refs
        self.strict_refs: bool = self._source_context.strict_refs
        self.allow_private_network: bool = self._source_context.allow_private_network
        self.http_backend = self._source_context.http_backend
        self.http_headers: Sequence[tuple[str, str]] | None = self._source_context.http_headers
        self.http_local_ref_path: Path | None = self._source_context.http_local_ref_path
        self.http_query_parameters: Sequence[tuple[str, str]] | None = self._source_context.http_query_parameters
        self.http_ignore_tls: bool = self._source_context.http_ignore_tls
        self.http_timeout: float | None = self._source_context.http_timeout
        self._remote_response_observer = self._source_context.remote_response_observer
        self.use_annotated: bool = config.use_annotated
        if self.use_annotated and not self.field_constraints:  # pragma: no cover
            msg = "`use_annotated=True` has to be used with `field_constraints=True`"
            raise Exception(msg)  # noqa: TRY002
        self.use_serialize_as_any: bool = config.use_serialize_as_any
        self.use_non_positive_negative_number_constrained_types = (
            config.use_non_positive_negative_number_constrained_types
        )
        self.use_double_quotes = config.use_double_quotes
        self.allow_responses_without_content = config.allow_responses_without_content
        self.collapse_root_models = config.collapse_root_models
        self.collapse_root_models_name_strategy = config.collapse_root_models_name_strategy
        self.collapse_reuse_models = config.collapse_reuse_models
        self.skip_root_model = config.skip_root_model
        self.use_root_model_sequence_interface = config.use_root_model_sequence_interface
        self.use_type_alias = config.use_type_alias
        self.capitalise_enum_members = config.capitalise_enum_members
        self.keep_model_order = config.keep_model_order
        self.use_one_literal_as_default = config.use_one_literal_as_default
        self.use_enum_values_in_discriminator = config.use_enum_values_in_discriminator
        self.use_msgspec_tagged_unions = config.use_msgspec_tagged_unions
        self.use_msgspec_nullable_enum_alias = config.use_msgspec_nullable_enum_alias
        self.use_typed_nullable_map_values = config.use_typed_nullable_map_values
        self.use_single_value_enum_as_const = config.use_single_value_enum_as_const
        self.known_third_party = config.known_third_party
        self.custom_formatter = config.custom_formatters
        self.custom_formatters_kwargs = config.custom_formatters_kwargs
        self.treat_dot_as_module = config.treat_dot_as_module
        self.strict_dotted_module_names = config.strict_dotted_module_names
        self.default_field_extras: dict[str, Any] | None = config.default_field_extras
        self.formatters: list[Formatter] | None = config.formatters
        self.builtin_format_line_length: int | None = config.builtin_format_line_length
        self.defer_formatting: bool = config.defer_formatting
        self._import_overrides: dict[str, str] | None = config.import_overrides or None
        self.type_mappings: dict[tuple[str, str], str] = Parser._parse_type_mappings(config.type_mappings)
        self.type_overrides: dict[str, str] = config.type_overrides or {}
        self._unavailable_imports: dict[tuple[str | None, str], str] = {}
        self._type_override_imports: dict[str, Import] = {
            key: Import.from_full_path(self._get_emitted_python_type_path(value))
            for key, value in self.type_overrides.items()
        }
        self._model_type_override_imports: dict[str, Import] = {
            key: import_ for key, import_ in self._type_override_imports.items() if "." not in key
        }
        self._reuse_optimization_context = _ReuseOptimizationContext.from_type_overrides(self.type_overrides)
        self.read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = config.read_only_write_only_model_type
        self.use_frozen_field: bool = config.use_frozen_field
        self.use_serialization_alias: bool = config.use_serialization_alias
        self.use_default_factory_for_optional_nested_models: bool = (
            config.use_default_factory_for_optional_nested_models
        )
        self.field_type_collision_strategy: FieldTypeCollisionStrategy | None = config.field_type_collision_strategy

    def _data_model_field_common_kwargs(self) -> dict[str, Any]:
        return self._data_model_field_common_kwargs_cache

    def _get_target_python_type_path(self, path: str) -> str:
        """Return a user-named type path for the target, naming a newer typing name by its backport.

        A standard-library type that the target lacks is an error, as for ``x-python-type``.
        """
        from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
            PythonTypeUnavailableError,
            get_qualified_python_type_import_path,
        )

        try:
            return get_qualified_python_type_import_path(path, self.target_python_version.version_key)
        except PythonTypeUnavailableError as exc:
            raise Error(str(exc)) from exc

    def _get_emitted_python_type_path(self, path: str) -> str:
        """Return a user-named type path for the target, rejecting a type the target lacks only once output imports it.

        A type that an override replaces or that no model uses emits no import, so it is left alone.
        """
        try:
            return self._get_target_python_type_path(path)
        except Error as exc:
            import_ = Import.from_full_path(path)
            self._unavailable_imports[import_.from_, import_.import_] = str(exc)
            return path

    def _reject_unavailable_imports(self, contexts: Sequence[ModuleContext]) -> None:
        """Reject output that imports a type the target lacks, after overrides, unused models and import remapping.

        The first such type in schema order is reported, so the message does not depend on set iteration order.
        """
        all_imports = (self.imports, *(ctx.imports for ctx in contexts))
        for (from_, name), message in self._unavailable_imports.items():
            if any(name in imports.get(from_, ()) for imports in all_imports):
                raise Error(message)

    def _split_field_alias(  # noqa: PLR6301
        self,
        alias: str | list[str] | None,
    ) -> tuple[str | None, list[str] | None]:
        """Split one output alias from multiple validation aliases."""
        match alias:
            case list() as validation_aliases:
                return None, validation_aliases
            case single_alias:
                return single_alias, None
        raise AssertionError  # pragma: no cover

    def _effective_default_state(
        self,
        field_name: str,
        default: Any,
        *,
        has_default: bool,
        required: bool,
        class_name: str | None,
    ) -> tuple[Any, bool, bool]:
        """Resolve an overridden default and its required-field constructor policy."""
        effective_default, effective_has_default = self.model_resolver.resolve_default_value(
            field_name,
            default,
            has_default,
            class_name=class_name,
        )
        return (
            effective_default,
            effective_has_default,
            required and self.apply_default_values_for_required_fields and effective_has_default,
        )

    def _should_preserve_explicit_root_class_name(self, class_name: str) -> bool:
        if not self.allow_leading_underscore_class_name:
            return False
        if class_name != self.class_name:
            return False
        return class_name.startswith("_") and ModelResolver.validate_name(class_name, self.target_python_version)

    @property
    def field_name_model_type(self) -> ModelType:
        """Get the ModelType for field name validation based on data_model_type.

        Returns ModelType.PYDANTIC for Pydantic models (which have reserved attributes
        like 'schema', 'model_fields', etc.), ModelType.MSGSPEC for msgspec Structs
        (whose imported ``field`` must not be shadowed by a field named ``field``), and
        ModelType.CLASS for other model types (TypedDict, dataclass) which don't have
        such constraints.
        """
        return field_name_model_type_of(self.data_model_type)

    def get_serialization_alias(
        self,
        original_field_name: str,
        field_name: str,
        class_name: str | None = None,
    ) -> str | None:
        """Get an explicit serialization alias for a field."""
        if not self.serialization_aliases:
            return None
        keys = []
        if class_name is not None:  # pragma: no branch
            keys.extend((f"{class_name}.{original_field_name}", f"{class_name}.{field_name}"))
        keys.extend((original_field_name, field_name))
        for key in keys:
            if key in self.serialization_aliases:
                return self.serialization_aliases[key]
        return None

    def _apply_final_class_field_aliases(
        self, fields: list[DataModelFieldBase], class_name: str, original_class_name: str
    ) -> None:
        """Apply final class scopes after name allocation, retaining already resolved fallback aliases."""
        if class_name == original_class_name or not (aliases := self.config.aliases):
            return
        prefix = f"{class_name}."
        if not any(key.startswith(prefix) for key in aliases):
            return
        scoped_aliases = {
            field.original_name
            for field in fields
            if field.original_name is not None
            and (isinstance(alias_value := aliases.get(f"{class_name}.{field.original_name}"), str) or alias_value)
        }
        if not scoped_aliases:
            return
        reserved_names = {field.name for field in fields if field.original_name not in scoped_aliases and field.name}
        resolved_aliases: dict[str, tuple[str, str | None, list[str] | None]] = {}
        for field in fields:
            if (original_name := field.original_name) is None or original_name not in scoped_aliases:
                continue
            if original_name not in resolved_aliases:
                field_name, alias = self.model_resolver.get_valid_field_name_and_alias(
                    original_name,
                    excludes=reserved_names,
                    model_type=self.field_name_model_type,
                    class_name=class_name,
                )
                field_alias, validation_aliases = self._split_field_alias(alias)
                resolved_aliases[original_name] = field_name, field_alias, validation_aliases
                reserved_names.add(field_name)
            field.name, field.alias, field.validation_aliases = resolved_aliases[original_name]
            if (
                self.serialization_aliases
                and (
                    field.serialization_alias is None
                    or f"{class_name}.{original_name}" in self.serialization_aliases
                    or f"{class_name}.{field.name}" in self.serialization_aliases
                )
                and (serialization_alias := self.get_serialization_alias(original_name, field.name, class_name))
                is not None
            ):
                field.serialization_alias = serialization_alias
            field.__dict__[_ALIAS_RESOLUTION_CLASS_NAME_KEY] = class_name

    @staticmethod
    def _parse_type_mappings(type_mappings: list[str] | None) -> dict[tuple[str, str], str]:
        """Parse type mappings from CLI format to internal format.

        Supports two formats:
        - "type+format=target" (e.g., "string+binary=string")
        - "format=target" (e.g., "binary=string", assumes type="string")

        Returns a dict mapping (type, format) tuples to target type names.
        """
        if not type_mappings:
            return {}

        result: dict[tuple[str, str], str] = {}
        for mapping in type_mappings:
            if "=" not in mapping:
                msg = f"Invalid type mapping format: {mapping!r}. Expected 'type+format=target' or 'format=target'."
                raise ValueError(msg)

            source, target = mapping.split("=", 1)
            if "+" in source:
                type_, format_ = source.split("+", 1)
            else:
                # Default to "string" type if only format is specified
                type_ = "string"
                format_ = source

            result[type_, format_] = target

        return result

    @property
    def iter_source(self) -> Iterator[Source]:
        """Iterate over all source files to be parsed."""
        if self._cache_local_sources:
            if (cached_sources := self._local_source_cache) is None:
                cached_sources = tuple(self._iter_source_uncached())
                self._local_source_cache = cached_sources
            for source in cached_sources:
                if source.raw_data is None:
                    yield Source(path=source.path, text=source.text)
                else:  # pragma: no cover
                    yield source.model_copy(deep=True)
            return
        yield from self._iter_source_uncached()

    def _iter_source_uncached(self) -> Iterator[Source]:
        match self.source:
            case str():
                yield Source(path=Path(), text=self.source)
            case dict():
                yield Source.from_dict(self.source)
            case Path() as path:
                if path.is_dir():
                    if (input_filter := self._source_context.directory_input_filter) is not None:
                        for source_path, data in input_filter.iter_files(
                            sorted(path.rglob("*"), key=lambda p: p.name), self.encoding
                        ):
                            yield self._source_from_path(source_path, data=data)
                        return
                    for p in sorted(path.rglob("*"), key=lambda p: p.name):
                        if p.is_file():
                            yield self._source_from_path(p)
                else:
                    yield self._source_from_path(path)
            case list() as paths:  # pragma: no cover
                for path in paths:
                    yield self._source_from_path(path)
            case _:
                yield Source(
                    path=Path(self.source.path),
                    text=self.remote_text_cache.get_or_put(
                        self.source.geturl(), default_factory=self._get_text_from_url
                    ),
                )

    def _source_from_path(self, path: Path, *, data: bytes | None = None) -> Source:
        try:
            prefetched_source = self.run_context.prefetched_source
            if data is None and prefetched_source is not None and path == prefetched_source[0]:
                data = prefetched_source[1]
            if self._use_parsed_source_cache:
                return Source.from_cached_path(
                    path, self.base_path, self.encoding, keep_text=self.validation, data=data
                )
            return Source.from_path(path, self.base_path, self.encoding, data=data)
        except FileNotFoundError as exc:
            msg = f"File not found: {path}"
            raise Error(msg) from exc

    def _source_path_for_diagnostics(self, source_path: Path | None = None) -> str:
        """Return source context without changing parser path semantics."""
        if source_path is not None and source_path.parts:
            return source_path.as_posix()
        if (diagnostic_source_path := self.run_context.diagnostic_source_path) is not None:
            return diagnostic_source_path.as_posix()
        return "<input>"

    def _append_additional_imports(self, additional_imports: list[str] | None) -> None:
        if not additional_imports:
            return

        from datamodel_code_generator.base_config import _validate_additional_import_paths  # noqa: PLC0415

        for additional_import_string in _validate_additional_import_paths(additional_imports) or []:
            new_import = Import.from_full_path(additional_import_string)
            self._import_ledger.append(self.imports, new_import, _ADDITIONAL_IMPORTS)

    def _resolve_base_class(
        self, class_name: str, custom_base_path: str | list[str] | None = None
    ) -> str | list[str] | None:
        """Resolve base class(es) with priority: base_class_map > customBasePath > base_class."""

        def normalize(value: str | list[str] | None) -> str | list[str] | None:
            if value is None:  # pragma: no cover
                return None
            if isinstance(value, list):
                seen: set[str] = set()
                result: list[str] = []
                for v in value:
                    if isinstance(v, str) and v and v not in seen:
                        seen.add(v)
                        result.append(v)
                if not result:
                    return None
                return result[0] if len(result) == 1 else result
            return value or None

        if self.base_class_map and class_name in self.base_class_map:
            return normalize(self.base_class_map[class_name])
        if custom_base_path:
            return normalize(custom_base_path)
        return self.base_class or None

    def _get_text_from_url(self, url: str) -> str:
        def fetch(remote_url: str) -> str:
            from datamodel_code_generator.http import DEFAULT_HTTP_TIMEOUT, _HTTPFetchSession  # noqa: PLC0415

            if (session := self._http_fetch_session) is None:
                self._http_fetch_session = session = _HTTPFetchSession(
                    self.http_backend,
                    response_observer=self._remote_response_observer,
                )
            timeout = self.http_timeout if self.http_timeout is not None else DEFAULT_HTTP_TIMEOUT
            return session.get_body(
                remote_url,
                self.http_headers,
                self.http_ignore_tls,
                self.http_query_parameters,
                timeout,
                allow_private_network=self.allow_private_network,
                encoding=self.encoding,
            )

        return self.remote_text_cache.get_or_put(
            url,
            default_factory=fetch,
        )

    @classmethod
    def get_url_path_parts(cls, url: ParseResult) -> list[str]:
        """Split URL into scheme/host and path components."""
        return [
            f"{url.scheme}://{url.netloc}",
            *url.path.split("/")[1:],
        ]

    @property
    def data_type(self) -> type[DataType]:
        """Get the DataType class from the type manager."""
        return self.data_type_manager.data_type

    @abstractmethod
    def parse_raw(self) -> None:
        """Parse the raw schema source. Must be implemented by subclasses."""
        raise NotImplementedError

    def _get_duplicate_root_reference(
        self,
        model: DataModel,
        root_data_type: DataType,
        models_set: set[DataModel],
    ) -> Reference | None:
        if not (root_reference := root_data_type.reference) or root_data_type.is_dict or root_data_type.is_list:
            return None
        if root_reference.source not in models_set:
            return None
        expected_name = self.model_resolver.get_class_name(model.reference.original_name, unique=False).name
        if root_reference.name != expected_name:
            return None
        return root_reference

    def _model_shapes(self, row: ShapeRow) -> ModelShapes[DataModel, Reference]:
        """Return the keys of one comparison pass of the dedup or reuse user of a row."""
        return ModelShapes(row, _ModelShapeSource(self.target_python_version), self._reuse_union_tags)

    def __renders_as_duplicate(
        self, model: DataModel, original_model: DataModel, shapes: ModelShapes[DataModel, Reference]
    ) -> bool:
        """Return whether a model renders like the first model of its class name.

        A copy of a schema merged from another document compares with referenced names masked, since the
        nested models it names only become the shared ones as they collapse in the same pass.
        """
        return shapes.key(model) == shapes.key(original_model) or (
            not self._merged_copies.isdisjoint((model, original_model))
            and shapes.shape(model)[0] == shapes.shape(original_model)[0]
        )

    def __delete_duplicate_models(self, models: list[DataModel]) -> None:  # noqa: PLR0912
        if not models:
            return

        model_class_names: dict[str, DataModel] = {}
        shapes = self._model_shapes(SAME_NAME)
        model_to_duplicate_models: defaultdict[DataModel, list[DataModel]] = defaultdict(list)
        # Use set for O(1) membership checks and collect removals for batch processing
        models_set = set(models)
        models_to_remove: set[DataModel] = set()
        reuse_constraint = (
            self._reuse_optimization_context.allows_model
            if self._reuse_optimization_context.type_override_model_names
            else None
        )
        for model in models:
            if model in models_to_remove:  # pragma: no cover
                continue
            reuse_allowed = reuse_constraint is None or reuse_constraint(model)
            if self._is_root_model(model):
                root_data_type = model.fields[0].data_type

                # backward compatible
                # Remove duplicated root model
                if reuse_allowed and (
                    root_reference := self._get_duplicate_root_reference(model, root_data_type, models_set)
                ):
                    self.generation_store.redirect_reference_users(model.reference, root_reference)
                    models_to_remove.add(model)
                    self.generation_store.detach_model_data_type_refs(model)
                    continue

                # Remove self from all DataModel children's base_classes
                for child in model.reference.iter_data_model_children():
                    self.generation_store.set_base_classes(
                        child,
                        [
                            bc
                            for bc in child.base_classes
                            if not _keyed_answer(
                                "removed base class",
                                bc.reference is model.reference,
                                lambda bc=bc, model=model: bc.reference == model.reference,
                            )
                        ],
                    )
                    if not child.base_classes:  # pragma: no cover
                        self.generation_store.reset_base_classes(child)

            class_name = model.duplicate_class_name or model.class_name
            if (
                reuse_allowed
                and (original_model := model_class_names.get(class_name)) is not None
                and self._reuse_optimization_context.allows_model(original_model)
                and self.__renders_as_duplicate(model, original_model, shapes)
            ):
                if shapes.references_match(original_model, model):
                    model_to_duplicate_models[original_model].append(model)
                continue
            model_class_names[class_name] = model
        for model, duplicate_models in model_to_duplicate_models.items():
            for duplicate_model in duplicate_models:
                self.generation_store.redirect_reference_users(duplicate_model.reference, model.reference)
                # Deduplicate base_classes in all DataModel children
                for child in duplicate_model.reference.iter_data_model_children():
                    self.generation_store.set_base_classes(
                        child,
                        _distinct_bases(shapes.row, child.base_classes),
                    )
                models_to_remove.add(duplicate_model)

        if self.reuse_model and self.collapse_reuse_models:
            max_iterations, iteration = len(models), 0
            shapes = self._model_shapes(COLLAPSE_REUSE)
            while True:
                iteration += 1
                if iteration > max_iterations:  # pragma: no cover
                    msg = f"Deduplication exceeded max iterations ({max_iterations})"
                    raise RuntimeError(msg)

                shapes.memo.clear()
                content_key_to_models: dict[tuple[Any, ...], list[DataModel]] = defaultdict(list)
                for model in self._reuse_optimization_context.eligible_models(models):
                    if model in models_to_remove or (
                        skips_root_model(shapes.row, collapse_root_models=self.collapse_root_models)
                        and self._is_root_model(model)
                    ):
                        continue
                    content_key_to_models[shapes.key(model)].append(model)

                if not (
                    duplicates := [
                        (canonical, dup)
                        for group in content_key_to_models.values()
                        if len(group) > 1
                        for canonical, dup in shapes.iter_matching_duplicates(
                            self._typed_map_values_last(group), keyed=False
                        )
                        if dup not in models_to_remove
                    ]
                ):
                    break

                for canonical, duplicate in duplicates:
                    redirect_collapsed_reuse_users(
                        self.generation_store,
                        self._collapsed_reuse_models,
                        self._collapsed_reuse_users,
                        duplicate,
                        canonical,
                    )
                    for child in duplicate.reference.iter_data_model_children():
                        self.generation_store.set_base_classes(
                            child,
                            _distinct_bases(shapes.row, child.base_classes),
                        )
                    models_to_remove.add(duplicate)

        # Batch removal: O(n) instead of O(n²)
        if models_to_remove:
            models[:] = [m for m in models if m not in models_to_remove]

    def _names_have_class_keyed_options(self, names: set[str]) -> bool:
        """Return whether an option keyed by class name, or by a class-scoped field, names one of ``names``.

        These options are looked up while models are created, under the names models have then.
        """
        config = self.config
        class_keys = {*(config.base_class_map or ()), *(config.extra_template_data or ()), *(config.validators or ())}
        scoped_keys = (
            *(config.aliases or ()),
            *(config.serialization_aliases or ()),
            *(config.default_value_overrides or ()),
        )
        return bool(names & class_keys) or any(key.rpartition(".")[0] in names for key in scoped_keys)

    def _typed_map_values_last(self, models: list[DataModel]) -> list[DataModel]:
        """Order models of typed map values after the others, so the others stay canonical and keep their names."""
        if typed_map_values := self._typed_map_value_reference_ids:
            return sorted(models, key=lambda model: id(model.reference) in typed_map_values)
        return models

    def __replace_duplicate_name_in_module(self, models: list[DataModel]) -> None:
        """Make class names unique within a module.

        Renames change the rendered type hints of referencing models, so cached dedup keys of the
        module are dropped whenever a model is renamed. Models of typed map values come last, so the other
        models keep the names they had before values were typed.
        """
        models = self._typed_map_values_last(models)
        scoped_model_resolver = ModelResolver(
            exclude_names={i.alias or i.import_ for m in models for i in m.imports},
            duplicate_name_suffix="Model",
            custom_class_name_generator=(lambda name: name) if self.custom_class_name_generator else None,
            target_python_version=self.target_python_version,
        )

        model_names: dict[str, DataModel] = {}
        renamed = False
        for model in models:
            class_name: str = model.class_name
            generated_name: str = scoped_model_resolver.add(
                [model.path],
                class_name,
                unique=True,
                class_name=True,
                preserve_class_name=self._should_preserve_explicit_root_class_name(class_name),
            ).name
            if class_name != generated_name:
                self.generation_store.rename_model(model, class_name=generated_name)
                renamed = True
            model_names[model.class_name] = model

        for model in models:
            duplicate_name = model.duplicate_class_name
            # check only first desired name
            if duplicate_name and duplicate_name not in model_names:
                del model_names[model.class_name]
                self.generation_store.rename_model(model, class_name=duplicate_name)
                model_names[duplicate_name] = model
                renamed = True

        if not renamed:
            return
        for model in models:
            model.invalidate_render_caches()

    def __change_from_import(  # noqa: PLR0913
        self,
        models: list[DataModel],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        *,
        init: bool,
        internal_modules: set[tuple[str, ...]] | None = None,
        model_path_to_module_name: dict[str, str] | None = None,
    ) -> None:
        models = self.__module_members(models)
        members = keyed_models(models, "module members")
        internal_modules = internal_modules or set()
        model_path_to_module_name = model_path_to_module_name or {}

        ledger = self._import_ledger
        for model in models:
            scoped_model_resolver.add([model.path], model.class_name)
        for model in models:
            producer = ImportProducer("change-from-import", model)
            before_import = model.imports
            ledger.append(imports, before_import, producer)
            current_module_name = _get_model_module_name(model, model_path_to_module_name)
            # Some imports are derived from type hints, so aliases can affect them.
            import_sensitive_alias_changed = False
            for data_type in model.all_data_types:
                match data_type.reference:
                    case None:
                        continue
                    case reference if members.key_of(reference) in members.models:
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
                    treat_dot_as_module=self.treat_dot_as_module,
                    import_ledger=self._import_ledger,
                ):
                    data_type.alias = alias
                    import_sensitive_alias_changed = True
            if import_sensitive_alias_changed:  # pragma: no cover
                model.clear_imports_cache()
                after_import = model.imports
                if before_import != after_import:
                    ledger.remove(imports, before_import, producer)
                    ledger.append(imports, after_import, producer)

    def __module_members(self, models: list[DataModel]) -> list[DataModel]:
        """Return the models of a module that stay in it: a root replaced by its inner model leaves at the decision."""
        if not (replaced_root_models := self._replaced_root_models):
            return models
        return [model for model in models if id(model) not in replaced_root_models]

    def __names_left_by_replaced_roots(self, models: list[DataModel], members: KeyedModels) -> set[str]:
        """Return the class names that roots replaced by their inner models leave free in a module.

        A root keeps its name for the inner model when the inner model, which takes the root's path, is in the same
        module.
        """
        replaced_root_models = self._replaced_root_models
        return {
            model.class_name
            for model in models
            if (replacement := replaced_root_models.get(id(model))) is not None
            and not _keyed_answer(
                "names left by replaced roots",
                model_key_of(replacement[1].reference) in members.models,
                lambda model=model: model.path in members.keys_by_path,
            )
        }

    def __set_reference_expression(  # noqa: PLR0913
        self,
        data_type: DataType,
        current_module_name: str,
        target_full_name: str,
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        *,
        same_module: bool,
        init: bool,
        internal_modules: set[ModulePath],
        producer: ImportProducer,
    ) -> None:
        """Write how a data type names its reference in the current module.

        A model the module emits is written by its class name; any other is imported, and written by the expression
        the import gives when that is not the class name.
        """
        data_type.alias = (
            None
            if same_module
            else register_reference_import(
                cast("Reference", data_type.reference),
                current_module_name,
                target_full_name,
                imports,
                scoped_model_resolver,
                init=init,
                internal_modules=internal_modules,
                base_class=isinstance(data_type, BaseClassDataType),
                producer=producer,
                treat_dot_as_module=self.treat_dot_as_module,
                import_ledger=self._import_ledger,
            )
        )

    def __import_removed_reuse_canonicals(
        self,
        contexts: list[ModuleContext],
        *,
        internal_modules: set[ModulePath],
        model_path_to_module_name: dict[str, str],
    ) -> None:
        """Import the kept model where a module processed earlier imported a duplicate that reuse removed."""
        if not (users := self._removed_reuse_users):
            return
        for ctx in contexts:
            if not (
                stale_imports := {
                    path: import_
                    for _, paths in users.values()
                    for path in paths
                    if (import_ := ctx.imports.reference_paths.get(path))
                }
            ):
                continue
            members = keyed_models(ctx.models, "module members")
            for model in ctx.models:
                for data_type in model.all_data_types:
                    user, paths = users.get(id(data_type), (None, ()))
                    if user is not data_type or not (
                        stale_import := next(filter(None, map(stale_imports.get, paths)), None)
                    ):
                        continue
                    producer = ImportProducer("import-removed-reuse-canonicals", model)
                    self._import_ledger.remove(ctx.imports, stale_import, producer)
                    reference = cast("Reference", data_type.reference)
                    self.__set_reference_expression(
                        data_type,
                        _get_model_module_name(model, model_path_to_module_name),
                        _get_data_type_target_full_name(data_type, reference, model_path_to_module_name),
                        ctx.imports,
                        ctx.scoped_model_resolver,
                        same_module=members.key_of(reference) in members.models,
                        init=ctx.is_init,
                        internal_modules=internal_modules,
                        producer=producer,
                    )

    def __record_renamed_type(self, model: DataModel, renamed_type: _RenamedType) -> None:
        """Record a class name that ``model`` had before a rename, under its model key and its current path."""
        self._renamed_types.setdefault(model_key_of(model.reference), []).append(renamed_type)
        self._renamed_type_paths.setdefault(model.path, []).append(renamed_type)

    def __renamed_types_of(self, reference: Reference) -> list[_RenamedType] | None:
        """Return the class names that the model ``reference`` names had before it was renamed, or None."""
        by_path = self._renamed_type_paths.get(reference.path)
        by_key = by_path if reference.source is None else self._renamed_types.get(model_key_of(reference))
        return _keyed_answer("renamed types", by_key, lambda: by_path)

    def __import_renamed_types(
        self,
        contexts: list[ModuleContext],
        *,
        internal_modules: set[ModulePath],
        model_path_to_module_name: dict[str, str],
    ) -> None:
        """Import a type by its new name where a module processed before the type's module imported the old one.

        A field of the type's module can hide the type, which then gets a new name after modules that come first
        built their imports of it, and the ``parent`` strategy of root-model collapse gives an inner model the name
        and path of the root model it replaces. Every name the type had is recorded, as a module processed between two
        renames imported the name in between. The stale import is the one added under the type's path at that name.
        It releases the name it binds for the new one, found by name, as neither the import of a package ``__init__``
        nor a ``from .`` path leads back to the path it was added under.
        """
        if not self._renamed_types:
            return
        for ctx in contexts:
            members = self.__module_members(ctx.models)
            keyed_members = keyed_models(members, "module members")
            stale_users = [
                (data_type, reference, model, import_)
                for model in members
                for data_type in model.all_data_types
                if (reference := data_type.reference) is not None
                and (renamed_types := self.__renamed_types_of(reference)) is not None
                and keyed_members.key_of(reference) not in keyed_members.models
                and (import_ := _stale_renamed_import(ctx.imports, data_type, renamed_types)) is not None
            ]
            if not stale_users:
                continue
            for _, _, model, import_ in stale_users:
                self._import_ledger.remove(ctx.imports, import_, ImportProducer("import-renamed-types", model))
            ctx.scoped_model_resolver.delete_names(
                {import_.binding_name for *_, import_ in stale_users}.union(
                    self.__names_left_by_replaced_roots(ctx.models, keyed_members)
                )
            )
            for data_type, reference, model, _ in stale_users:
                self.__set_reference_expression(
                    data_type,
                    _get_model_module_name(model, model_path_to_module_name),
                    _get_data_type_target_full_name(data_type, reference, model_path_to_module_name),
                    ctx.imports,
                    ctx.scoped_model_resolver,
                    same_module=False,
                    init=ctx.is_init,
                    internal_modules=internal_modules,
                    producer=ImportProducer("import-renamed-types", model),
                )

    def __import_enum_member_literal_classes(
        self,
        contexts: list[ModuleContext],
        *,
        internal_modules: set[ModulePath],
        model_path_to_module_name: dict[str, str],
    ) -> None:
        """Name the enum class of each enum member literal as its module writes it, importing it from another module.

        A literal of the enum's own module takes the enum's final name, which a type renamed for a field it hid
        changes after the literal is made.
        """
        for ctx in contexts:
            members = keyed_models(ctx.models, "module members")
            for model in ctx.models:
                for data_type in model.all_data_types:
                    if (reference := data_type._enum_member_literal_reference) is None or data_type.alias:  # noqa: SLF001
                        continue
                    enum_class = (
                        reference.short_name
                        if members.key_of(reference) in members.models
                        else register_reference_import(
                            reference,
                            _get_model_module_name(model, model_path_to_module_name),
                            _module_qualified_name(reference, model_path_to_module_name),
                            ctx.imports,
                            ctx.scoped_model_resolver,
                            init=ctx.is_init,
                            internal_modules=internal_modules,
                            producer=ImportProducer("import-enum-member-literal-classes", model),
                            treat_dot_as_module=self.treat_dot_as_module,
                            import_ledger=self._import_ledger,
                        )
                        or reference.short_name
                    )
                    data_type.enum_member_literals = [
                        (enum_class, member) for _, member in data_type.enum_member_literals
                    ]

    @classmethod
    def __extract_inherited_enum(cls, models: list[DataModel]) -> None:
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

    def __type_const_narrowed_values(
        self,
        models: list[DataModel],
        imports: Imports,
        *,
        can_retain_cache: bool,
    ) -> None:
        """Type each value whose model is built from an enum that a const narrows by that const.

        Runs once the referenced enum is final: after reuse and collapse redirect the value and after member and
        discriminator names are settled. A value of a named enum whose root types no enum, as when its const already
        makes it a Literal or None, keeps its type. The values that keep the enum type are warned about once msgspec
        tags are final. A value that collapse shares between the root model and its holders is replaced in each holder.
        """
        if not self._const_narrowed_values:
            return
        narrowed: dict[int, DataType] = {}
        for model in models:
            for field in model.fields:
                for owner, data_type in _value_positions(field):
                    entry = self._const_narrowed_values.get(id(data_type))
                    if entry is None or entry[0] is not data_type or data_type.reference is None:
                        continue
                    narrowing = entry[1]
                    if (enum := _named_enum(cast("DataModel", data_type.reference.source))) is None:
                        continue
                    if (new_data_type := self.__const_narrowed_data_type(data_type, narrowing, enum)) is None:
                        self._enum_admits_other_values.append((field, narrowing))
                        continue
                    narrowed[id(data_type)] = data_type
                    if owner is field:
                        self.generation_store.replace_field_type(field, new_data_type)
                    elif (parent := cast("DataType", owner)).dict_key is data_type:
                        self.generation_store.replace_nested_data_type(parent, data_type, new_data_type)
                    else:
                        self.generation_store.set_nested_data_types(
                            parent, [new_data_type if nested is data_type else nested for nested in parent.data_types]
                        )
                    self._import_ledger.append(
                        imports, new_data_type.imports, ImportProducer("const-narrowed-values", model, field)
                    )
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
        for data_type in narrowed.values():
            self.generation_store.detach_data_type_ref(data_type)

    def __carry_const_narrowings(self, source: DataType, copied: DataType) -> None:
        """Give each node of ``copied`` that collapse inlines the const narrowing of the ``source`` node it copies.

        A copy has the shape of its source; a node that the copy shares with its source already has its narrowing.
        """
        if not self._const_narrowed_values:
            return
        pending = [(source, copied)]
        while pending:
            source_node, copied_node = pending.pop()
            if (entry := self._const_narrowed_values.get(id(source_node))) is not None and entry[0] is source_node:
                self._const_narrowed_values[id(copied_node)] = (copied_node, entry[1])
            pending.extend(zip(source_node.data_types, copied_node.data_types, strict=True))
            if (key := source_node.dict_key) is not None:
                pending.append((key, cast("DataType", copied_node.dict_key)))

    def __type_dict_keys_by_root_types(
        self,
        models: list[DataModel],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        model_path_to_module_name: dict[str, str],
        *,
        can_retain_cache: bool,
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
                        or (root_type := self.__root_key_data_type(root_model)) is None
                    ):
                        continue
                    self.generation_store.detach_data_type_ref(data_type)
                    if parent.dict_key is data_type:
                        self.generation_store.replace_nested_data_type(parent, data_type, root_type)
                    else:
                        self.generation_store.set_nested_data_types(
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
                                ledger=self._import_ledger,
                                producer=producer,
                            )
                    self._import_ledger.append(imports, root_type.imports, producer)
                    pending.append((root_type, parent, expanded | {model_key_of(root_model.reference)}))
                    changed = True
            if changed:
                self.generation_store.note_changed_models(model)
                model.refresh_field_config()
                _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __root_key_data_type(self, root_model: DataModel) -> DataType | None:
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
        string_type = self.data_type_manager.get_data_type(Types.string)
        if any((
            root_type.is_dict,
            root_type.is_mapping,
            root_type.is_list,
            root_type.is_sequence,
            root_type.is_set,
        )) or (keywords and (root_type.type, root_type.import_) != (string_type.type, string_type.import_)):
            return None
        if keywords:
            return self.data_type_manager.get_data_type(
                Types.string,
                use_annotated=self.use_annotated,
                target_pydantic_version=self.target_pydantic_version,
                **keywords,
            )
        key_type = _copy_data_type(root_type)
        if self._model_type_override_imports:
            self._apply_override_to_data_type(key_type)
        return key_type

    def __const_narrowed_data_type(
        self, data_type: DataType, narrowing: _ConstNarrowing, enum: Enum
    ) -> DataType | None:
        """Return the type of a value narrowed to a const: None, ``Literal[<Enum>.<member>]``, or None to keep it."""
        if narrowing.const is None:
            return self.data_type_manager.get_data_type(Types.null)
        if not narrowing.as_member or (member := enum.find_member(narrowing.const)) is None:
            return None
        new_data_type = self.data_type(
            enum_member_literals=[(enum.reference.short_name, cast("str", member.field.name))],
            is_optional=data_type.is_optional,
        )
        new_data_type._set_enum_member_literal_reference(enum.reference)  # noqa: SLF001
        return new_data_type

    def __warn_enums_admitting_other_values(self) -> None:
        """Warn once per schema location that a value typed by its enum accepts the values its const excludes.

        Runs once msgspec tags are final, as a field that becomes its struct's tag is no value and admits only its tag.
        """
        values = (narrowing for field, narrowing in self._enum_admits_other_values if not field.is_class_var)
        for narrowing in {narrowing.location: narrowing for narrowing in values}.values():
            warn(
                f"The schema at {narrowing.location} admits only its const {narrowing.const!r} of its enum values, but "
                "its values are typed as its enum class, which accepts the other values too.",
                stacklevel=2,
            )

    def _create_discriminator_data_type(
        self,
        enum_source: Enum | None,
        discriminator_values: list[DiscriminatorValue],
        discriminator_model: DataModel,  # noqa: ARG002
        imports: Imports,  # noqa: ARG002
    ) -> DataType:
        """Create a data type for discriminator field, using enum literals if available."""
        if enum_source:
            if self.use_enum_values_in_discriminator:
                enum_class_name = enum_source.reference.short_name
                enum_member_literals: list[tuple[str, str]] = []
                for value in discriminator_values:
                    member = enum_source.find_member(value, coerce_strings=True)
                    if member and member.field.name:
                        enum_member_literals.append((enum_class_name, member.field.name))
                    else:  # pragma: no cover
                        enum_member_literals.append((enum_class_name, _semantic_value_text(value)))
                data_type = self.data_type(enum_member_literals=enum_member_literals)
                data_type._set_enum_member_literal_reference(enum_source.reference)  # noqa: SLF001
            else:
                data_type = self.data_type(
                    literals=[
                        Parser._get_enum_discriminator_literal(enum_source, value) for value in discriminator_values
                    ]
                )
        else:
            data_type = self.data_type(literals=discriminator_values)
        return data_type

    @staticmethod
    def _get_enum_discriminator_literal(enum_source: Enum, value: DiscriminatorValue) -> DiscriminatorValue:
        member = enum_source.find_member(value, coerce_strings=True)
        return enum_tag_literal(value, member.value) if member else value

    def __set_tag_literal_default(
        self,
        model: DataModel,
        discriminator_field: DataModelFieldBase,
        literal: DiscriminatorValue | Member | None,
        source: Literal["tag", "written-tag"],
        *,
        can_retain_cache: bool,
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
            force_optional=self.force_optional_for_required_fields,
            missing_sentinel=discriminator_field.use_missing_sentinel,
            discriminator_argument=discriminator_field.SUPPORTS_DISCRIMINATOR,
        )
        _apply_literal_default(discriminator_field, decision)
        if decision.writes_default:
            _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __set_nested_discriminator_literal_defaults(
        self,
        tag_fields: Iterable[tuple[DataModelFieldBase, Sequence[DiscriminatorValue]]],
        *,
        can_retain_cache: bool,
    ) -> None:
        """Keep the single-literal tags of variants that nested choices give a union valid like the direct ones."""
        for tag_field, values in tag_fields:
            if tag_field.data_type.literals == [*values]:
                self.__set_tag_literal_default(
                    cast("DataModel", tag_field.parent), tag_field, values[0], "tag", can_retain_cache=can_retain_cache
                )

    def _get_discriminator_field_name(
        self, data_types: Iterable[DataType], property_name: str, default_name: str
    ) -> str:
        """Resolve the shared Python field name without changing wire aliases."""
        common_name: str | None = None
        for data_type, _, _ in _iter_discriminator_data_types(data_types):
            if not data_type.reference:
                continue
            variant = cast("DataModel", data_type.reference.source)
            for variant_field in chain(variant.fields, variant.iter_all_fields()):
                if _tag_field_matches(
                    variant_field, "original-or-unaliased-python-name", property_name, python_name=default_name
                ):
                    variant_name = variant_field.name
                    break
            else:
                variant_name, _ = self.model_resolver.get_valid_field_name_and_alias(
                    property_name, model_type=self.field_name_model_type, class_name=variant.class_name
                )
            if common_name is not None and common_name != variant_name:
                msg = (
                    f"Discriminator {property_name!r} resolves to different field names "
                    f"{common_name!r} and {variant_name!r}; use the same alias for all variants."
                )
                raise Error(msg)
            common_name = variant_name
        return common_name or default_name

    def __apply_discriminator_type(  # noqa: PLR0912, PLR0914, PLR0915
        self,
        models: list[DataModel],
        imports: Imports,
        *,
        can_retain_cache: bool,
    ) -> None:
        tagged_unions = self.use_msgspec_tagged_unions
        unions: list[_DiscriminatorUnion] = []
        for model in models:  # noqa: PLR1702
            for field in model.fields:
                match field.extras.get("discriminator"):
                    case {"propertyName": str() as property_name} as discriminator if property_name:
                        pass
                    case _:
                        continue
                field_name, alias = self.model_resolver.get_valid_field_name_and_alias(
                    field_name=property_name, model_type=self.field_name_model_type
                )
                mapping = discriminator.get("mapping", {})
                discriminator = {**discriminator, "propertyName": field_name}
                self.generation_store.set_field_extra(field, "discriminator", discriminator)
                # Any type cannot be a discriminated union variant (Pydantic v2 rejects it)
                has_any_variant = any(_is_any_variant(dt) for dt in field.data_type.data_types)
                if has_any_variant:  # pragma: no cover
                    self.generation_store.drop_field_discriminator(field)
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
                    continue
                if not _discriminator_variants_are_valid(
                    field.data_type.data_types,
                    field_name,
                    mapping,
                    original_name=property_name if field.SUPPORTS_DISCRIMINATOR else None,
                    enum_member_tags=self.use_enum_values_in_discriminator,
                ):
                    self.generation_store.drop_field_discriminator(field)
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
                    continue
                tag_fields: list[tuple[DataModelFieldBase, Sequence[DiscriminatorValue]]] = [
                    *_iter_nested_discriminator_tag_fields(
                        field.data_type.data_types,
                        property_name if field.SUPPORTS_DISCRIMINATOR else field_name,
                        field_name_mode="source-name" if field.SUPPORTS_DISCRIMINATOR else "python-or-original-name",
                    )
                ]
                self.__set_nested_discriminator_literal_defaults(tag_fields, can_retain_cache=can_retain_cache)

                if field.SUPPORTS_DISCRIMINATOR:
                    field_name = self._get_discriminator_field_name(
                        field.data_type.data_types, property_name, field_name
                    )
                    discriminator["propertyName"] = field_name
                    if field_name != property_name and alias is None:
                        alias = property_name

                tags: list[_DiscriminatorTag] = []
                union_name = model.class_name if self._is_root_model(model) else f"{model.class_name}.{field.name}"
                collapsed_unions = self.__get_collapsed_unions(model, field)
                field_name_mode: Literal["python-name", "python-or-original-name"] = (
                    "python-name" if field.SUPPORTS_DISCRIMINATOR else "python-or-original-name"
                )
                has_reused_variant = any(
                    isinstance(source := data_type.reference.source, DataModel) and source.reused_reference is not None
                    for data_type in field.data_type.data_types
                    if data_type.reference
                )
                for index, data_type in enumerate(field.data_type.data_types):
                    if not data_type.reference:  # pragma: no cover
                        continue
                    discriminator_model = data_type.reference.source
                    if (
                        not isinstance(discriminator_model, DataModel)
                        or not discriminator_model.SUPPORTS_DISCRIMINATOR
                        or _is_discriminator_wrapper(discriminator_model)
                    ):  # pragma: no cover
                        continue

                    annotates_new_tag = has_reused_variant and not (
                        tagged_unions and discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR
                    )
                    # A resolved Python name can equal a different field's wire name.
                    discriminator_values = _get_discriminator_values(
                        discriminator_model,
                        field_name,
                        mapping,
                        field_name_mode=field_name_mode,
                    )
                    self.__record_variant_discriminator_tags(
                        discriminator_model,
                        union_name,
                        property_name=property_name,
                        field_name=field_name,
                        mapping=mapping,
                        values=discriminator_values,
                        field_name_mode=field_name_mode,
                    )
                    for collapsed_name, collapsed_types, collapsed_mapping in (
                        (union_name, field.data_type.data_types, mapping),
                        *collapsed_unions,
                    ):
                        self.__record_collapsed_variant_tags(
                            discriminator_model,
                            collapsed_name,
                            collapsed_types[index],
                            property_name=property_name,
                            field_name=field_name,
                            mapping=collapsed_mapping,
                            values=discriminator_values,
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
                                self.__record_reused_variant_tag_field(discriminator_field, annotates_tag=False)
                            match discriminator_model:
                                case _ if discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
                                    tags.append((discriminator_model, discriminator_field, literal))
                                case _:
                                    self.__set_tag_literal_default(
                                        discriminator_model,
                                        discriminator_field,
                                        literal,
                                        "tag",
                                        can_retain_cache=can_retain_cache,
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
                        if self.use_enum_values_in_discriminator:
                            enum_source = enum_source or _get_enum_from_base(
                                discriminator_model, field_name, use_field_name=field.SUPPORTS_DISCRIMINATOR
                            )

                        for field_data_type in discriminator_field.data_type.all_data_types:
                            if field_data_type.reference:  # pragma: no cover
                                self.generation_store.detach_data_type_ref(field_data_type)

                        new_discriminator_data_type = self._create_discriminator_data_type(
                            enum_source,
                            discriminator_values,
                            discriminator_model,
                            imports,
                        )
                        self.generation_store.replace_field_type(discriminator_field, new_discriminator_data_type)
                        discriminator_field.data_type.parent = discriminator_field
                        tag_fields.append((discriminator_field, discriminator_values))
                        self.__record_reused_variant_tag_field(discriminator_field, annotates_tag=annotates_new_tag)
                        literal_default = None
                        if tagged_unions and discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
                            tags.append((
                                discriminator_model,
                                discriminator_field,
                                _get_mapped_discriminator_tag(discriminator_values, enum_source),
                            ))
                        elif self.force_optional_for_required_fields:
                            literal_default = _get_single_discriminator_default(
                                new_discriminator_data_type, enum_source, expected_value
                            )
                        self.__set_tag_literal_default(
                            discriminator_model,
                            discriminator_field,
                            literal_default,
                            "written-tag",
                            can_retain_cache=can_retain_cache,
                        )
                        self._import_ledger.append(
                            imports,
                            discriminator_field.imports,
                            ImportProducer("discriminator", discriminator_model, discriminator_field),
                        )
                        has_one_literal = True
                    if not has_one_literal:
                        new_data_type = self._create_discriminator_data_type(
                            base_enum_source := _get_enum_from_base(
                                discriminator_model, field_name, use_field_name=field.SUPPORTS_DISCRIMINATOR
                            ),
                            discriminator_values,
                            discriminator_model,
                            imports,
                        )
                        # Handle multiple aliases (Pydantic v2 AliasChoices)
                        single_alias, validation_aliases = self._split_field_alias(alias)
                        self.generation_store.append_field(
                            discriminator_model,
                            discriminator_field := self.data_model_field_type(
                                name=field_name,
                                data_type=new_data_type,
                                required=True,
                                alias=single_alias,
                                validation_aliases=validation_aliases,
                                serialization_alias=self.get_serialization_alias(
                                    property_name, field_name, discriminator_model.name
                                ),
                                use_serialization_alias=self.use_serialization_alias,
                                **self._data_model_field_common_kwargs(),
                            ),
                        )
                        if self.config.aliases:
                            discriminator_field.__dict__[_ALIAS_RESOLUTION_CLASS_NAME_KEY] = (
                                discriminator_model.class_name
                            )
                            self.model_resolver.prepare_explicit_field_aliases(
                                [discriminator_field], discriminator_model.reference, self.field_name_model_type
                            )
                        self.__record_reused_variant_tag_field(discriminator_field, annotates_tag=annotates_new_tag)
                        if tagged_unions and discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
                            tags.append((
                                discriminator_model,
                                discriminator_field,
                                _get_mapped_discriminator_tag(discriminator_values, base_enum_source),
                            ))
                _drop_discriminator_tag_patterns(self.generation_store, tag_fields)
                if not tags:
                    continue
                if tagged_unions:
                    unions.append((model, tags, property_name, mapping))
                else:
                    _apply_discriminator_tags(tags, property_name, can_retain_cache=can_retain_cache)
        if (deferred := self._deferred_tagged_unions) or unions:
            self._deferred_tagged_unions = _tag_discriminator_unions(
                [*deferred, *unions],
                _derived_models({id(model): model for model in chain(self.results, models)}.values()),
                can_retain_cache=can_retain_cache or bool(deferred),
                defers_related=self._defers_tagged_unions,
            )

    def _create_set_from_list(self, data_type: DataType) -> DataType | None:
        """Return a set copy of a list type, or ``data_type`` after its nested lists became sets in place.

        A nested list is swapped for its set copy at once; the facts see the swap at the next refresh.
        """
        if data_type.is_list:
            new_data_type = data_type.model_copy(update={"is_list": False, "is_set": True})
            for data_type_ in new_data_type.data_types:
                data_type_.parent = new_data_type
            return new_data_type
        if data_type.data_types:  # pragma: no cover
            for nested_data_type in data_type.data_types[:]:
                set_data_type = self._create_set_from_list(nested_data_type)
                if set_data_type:  # pragma: no cover
                    self.generation_store.swap_data_type(nested_data_type, set_data_type, refresh="deferred")
            return data_type
        return None  # pragma: no cover

    def __replace_unique_list_to_set(self, models: list[DataModel], *, can_retain_cache: bool) -> None:
        changed = False
        for model in models:
            for model_field in model.fields:
                if (
                    not self.use_unique_items_as_set
                    or not _declares_unique_items(model_field)
                    or _unique_items_tuple(model_field.data_type) is not None
                ):
                    continue
                set_data_type = self._create_set_from_list(model_field.data_type)
                if set_data_type:  # pragma: no cover
                    # Check if default list elements are hashable before converting type
                    if isinstance(model_field.default, list):
                        try:
                            converted_default = set(model_field.default)
                        except TypeError:
                            # Keep the unhashable default unchanged. Nested list types may
                            # already have been converted in place, so invalidate their imports.
                            self.generation_store.note_changed_models(model)
                            _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
                            continue
                        self.generation_store.set_field_default(model_field, converted_default)
                    self.generation_store.replace_field_type(model_field, set_data_type)
                    changed = True
        if changed and not can_retain_cache:
            _clear_model_imports_cache(models)

    @classmethod
    def __collect_set_item_references(cls, models: list[DataModel]) -> list[Reference]:
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

    def __mark_set_item_models_hashable(self, models: list[DataModel]) -> None:
        """Mark models used as set/frozenset items with hash flag for __hash__ generation, found by model key."""
        set_item_references = self.__collect_set_item_references(models)
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
            if self.config.additional_imports
            else self.data_model_type.get_native_hash_model_paths(set_item_models)
        )
        for model in set_item_models:
            if model.reference.path in native_hash_paths:
                continue
            model.enable_identity_hash()

    def __set_reference_default_value_to_field(
        self,
        models: list[DataModel],
        *,
        can_retain_cache: bool,
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
                    self.generation_store.set_field_default(model_field, model_field.data_type.reference.source.default)
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __require_update_action(
        self, models: Sequence[DataModel], require_update_action_models: list[str] | None = None
    ) -> None:
        """Add the models that are not type aliases to the models needing a reference update action.

        ``require_update_action_models`` is the list of their paths that ``_process_single_module`` takes.
        """
        recursion_facts = self._recursion_facts
        for model in models:
            add_model_path_to_list(require_update_action_models, model)
        self._recursion_facts = RecursionFacts(
            require_update=recursion_facts["require_update"].union(
                model_key_of(model.reference) for model in models if not model.is_alias
            ),
            circular_roots=recursion_facts["circular_roots"],
        )

    def __reuse_model(self, models: list[DataModel], require_update_action_models: list[str]) -> None:
        if not self.reuse_model or self.reuse_scope == ReuseScope.Tree:
            return
        duplicates = []
        require_update = self._recursion_facts["require_update"]
        updated_models: list[DataModel] = []
        reuse_candidates = (
            model
            for model in self._reuse_optimization_context.eligible_models(models.copy())
            if not (
                skips_root_model(REUSE_MODEL, collapse_root_models=self.collapse_root_models)
                and self._is_root_model(model)
            )
        )
        typed_map_values = self._typed_map_value_reference_ids
        canonical_models: dict[int, DataModel] = {}
        shapes = self._model_shapes(REUSE_MODEL)
        for first_model, duplicate_model in shapes.iter_matching_duplicates(reuse_candidates, keyed=True):
            cached_model = canonical_models.get(id(first_model), first_model)
            model = duplicate_model
            if id(cached_model.reference) in typed_map_values and id(model.reference) not in typed_map_values:
                cached_model, model = model, cached_model
                canonical_models[id(first_model)] = cached_model
            cached_model_reference = cached_model.reference
            if (
                id(model.reference) in typed_map_values
                and not self.collapse_reuse_models
                and self.generation_store.has_users_in_other_modules(model)
            ):
                # A module written earlier names the value, as a field of a collapsed root model does, so it stays.
                continue
            if isinstance(model, Enum) or self.collapse_reuse_models:
                self.__redirect_removed_reuse_users(model, cached_model)
                duplicates.append(model)
            elif id(model.reference) in typed_map_values:
                self.generation_store.redirect_model_reference_users(model, models, cached_model_reference)
                duplicates.append(model)
            else:
                inherited_model = model.create_reuse_model(cached_model_reference)
                self.generation_store.redirect_model_reference_users(model, models, inherited_model.reference)
                if model_key_of(cached_model_reference) in require_update:
                    updated_models.append(inherited_model)
                replace_model_in_list(models, model, inherited_model)

        for duplicate in duplicates:
            models.remove(duplicate)
        if updated_models:
            self.__require_update_action(updated_models, require_update_action_models)

    def __redirect_removed_reuse_users(self, duplicate: DataModel, canonical: DataModel) -> None:
        """Redirect every user of a duplicate that reuse removes, remembering what each user referred to.

        Modules processed earlier already imported the duplicate, so their imports are corrected after processing.
        """
        users = self._removed_reuse_users
        for user in redirect_collapsed_reuse_users(
            self.generation_store, self._collapsed_reuse_models, self._collapsed_reuse_users, duplicate, canonical
        ):
            users.setdefault(id(user), (user, []))[1].append(duplicate.reference.path)

    def __find_duplicate_models_across_modules(
        self,
        module_models: list[tuple[tuple[str, ...], list[DataModel]]],
    ) -> list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]]:
        """Find duplicate models across all modules by comparing render output, imports and referenced models.

        Type hints render references by name before imports are resolved, so equal render output from
        different modules can still reference different models that share a name.
        """
        model_modules = {
            model: module
            for module, models in module_models
            for model in self._reuse_optimization_context.eligible_models(models)
        }
        shapes = self._model_shapes(TREE_SCOPE)
        return [
            (model_modules[model], model, model_modules[canonical], canonical)
            for canonical, model in shapes.iter_matching_duplicates(
                self._typed_map_values_last(list(model_modules)), keyed=True
            )
        ]

    def __replace_typed_map_value_duplicates(
        self,
        module_models: list[tuple[tuple[str, ...], list[DataModel]]],
        duplicates: list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]],
    ) -> list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]]:
        """Replace typed map values by the other models they duplicate, leaving the rest to the shared module.

        Those other models stay where they are, as they did before map values were typed.
        """
        typed_map_values = self._typed_map_value_reference_ids
        module_lists = dict(module_models)
        remaining = []
        for duplicate in duplicates:
            duplicate_module, duplicate_model, _, canonical = duplicate
            if id(duplicate_model.reference) in typed_map_values and id(canonical.reference) not in typed_map_values:
                self.generation_store.redirect_reference_users(duplicate_model.reference, canonical.reference)
                module_lists[duplicate_module].remove(duplicate_model)
            else:
                remaining.append(duplicate)
        return remaining

    def __validate_shared_module_name(
        self,
        module_models: list[tuple[tuple[str, ...], list[DataModel]]],
    ) -> None:
        """Validate that the shared module name doesn't conflict with existing modules."""
        shared_module = self.shared_module_name
        existing_module_names = {module[0] for module, _ in module_models if module}
        if shared_module in existing_module_names:
            msg = (
                f"Schema file or directory '{shared_module}' conflicts with the shared module name. "
                f"Use --shared-module-name to specify a different name."
            )
            raise Error(msg)

    def __create_shared_module_from_duplicates(  # noqa: PLR0912
        self,
        module_models: list[tuple[tuple[str, ...], list[DataModel]]],
        duplicates: list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]],
        reference_models: list[DataModel] | None,
    ) -> tuple[tuple[str, ...], list[DataModel]]:
        """Create shared module with canonical models and replace duplicates with inherited models.

        A duplicate that is removed rather than replaced hands every user to the shared model, since
        users in other modules would otherwise import it from a module that no longer defines it.
        """
        shared_module = self.shared_module_name

        shared_models: list[DataModel] = []
        canonical_to_shared_ref: dict[DataModel, Reference] = {}
        canonical_models_seen: set[DataModel] = set()

        # Process in order of first appearance in duplicates to ensure stable ordering
        for _, _, _, canonical in duplicates:
            if canonical in canonical_models_seen:
                continue
            canonical_models_seen.add(canonical)
            self.generation_store.update_model_reference(canonical, new_file_path=Path(f"{shared_module}.py"))
            canonical_to_shared_ref[canonical] = canonical.reference
            shared_models.append(canonical)

        supports_inheritance = self.data_model_type.SUPPORTS_TREE_SCOPE_REUSE_MODEL_INHERITANCE
        require_update = self._recursion_facts["require_update"]
        updated_models: list[DataModel] = []

        module_models_sets: dict[tuple[str, ...], set[DataModel]] = {
            module: set(models) for module, models in module_models
        }
        models_to_remove: dict[tuple[str, ...], set[DataModel]] = defaultdict(set)

        for duplicate_module, duplicate_model, _, canonical_model in duplicates:
            shared_ref = canonical_to_shared_ref[canonical_model]
            models_set = module_models_sets.get(duplicate_module)
            if not models_set or duplicate_model not in models_set:  # pragma: no cover
                msg = f"Duplicate model {duplicate_model.name} not found in module {duplicate_module}"
                raise RuntimeError(msg)

            for module, models in module_models:  # pragma: no branch
                if module != duplicate_module:
                    continue
                if isinstance(duplicate_model, Enum) or not supports_inheritance or self.collapse_reuse_models:
                    redirect_collapsed_reuse_users(
                        self.generation_store,
                        self._collapsed_reuse_models,
                        self._collapsed_reuse_users,
                        duplicate_model,
                        canonical_model,
                    )
                    models_to_remove[module].add(duplicate_model)
                else:
                    inherited_model = duplicate_model.create_reuse_model(shared_ref)
                    self.generation_store.redirect_model_reference_users(
                        duplicate_model,
                        reference_models if reference_models is not None else models,
                        inherited_model.reference,
                    )
                    if model_key_of(shared_ref) in require_update:
                        updated_models.append(inherited_model)
                    replace_model_in_list(models, duplicate_model, inherited_model)
                break

        for canonical in canonical_models_seen:
            for module, models_set in module_models_sets.items():
                if canonical in models_set:
                    models_to_remove[module].add(canonical)
                    break
            else:  # pragma: no cover
                msg = f"Canonical model {canonical.name} not found in any module"
                raise RuntimeError(msg)

        for module, models in module_models:
            to_remove = models_to_remove.get(module)
            if to_remove:
                models[:] = [m for m in models if m not in to_remove]

        if reference_models is not None:
            # Relocation can introduce forward references after the initial sort.
            keyed = keyed_models(shared_models, "shared module forward references")
            later_shared_keys: set[ModelKey] = set()
            for model in reversed(shared_models):
                if self.generation_store.index.referenced_keys_for_model(model, keyed) & later_shared_keys:
                    updated_models.append(model)
                later_shared_keys.add(model_key_of(model.reference))
        if updated_models:
            self.__require_update_action(updated_models)

        return (shared_module,), shared_models

    def __reuse_model_tree_scope(
        self,
        module_models: list[tuple[tuple[str, ...], list[DataModel]]],
        module_split_mode: ModuleSplitMode | None,
    ) -> tuple[tuple[str, ...], list[DataModel]] | None:
        """Deduplicate models across all modules, placing shared models in shared.py."""
        if not self.reuse_model or self.reuse_scope != ReuseScope.Tree:
            return None

        duplicates = self.__find_duplicate_models_across_modules(module_models)
        if self._typed_map_value_reference_ids:
            duplicates = self.__replace_typed_map_value_duplicates(module_models, duplicates)
        if not duplicates:
            return None

        self.__validate_shared_module_name(module_models)
        # Single-model splitting puts users outside the duplicate's original module.
        reference_models = (
            [model for _, models in module_models for model in models]
            if module_split_mode == ModuleSplitMode.Single
            else None
        )
        return self.__create_shared_module_from_duplicates(module_models, duplicates, reference_models)

    def __collapse_root_models(  # noqa: PLR0913
        self,
        models: list[DataModel],
        unused_models: list[DataModel],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        model_path_to_module_name: dict[str, str] | None = None,
        *,
        is_init: bool = False,
        internal_modules: set[ModulePath] | None = None,
    ) -> None:
        if not self.collapse_root_models:
            return

        with self.generation_store._collapse_root_reference_scope():  # noqa: SLF001
            self.__collapse_root_models_in_scope(
                models,
                unused_models,
                imports,
                scoped_model_resolver,
                {} if model_path_to_module_name is None else model_path_to_module_name,
                is_init=is_init,
                internal_modules=internal_modules or set(),
            )

    def _keeps_root_model_named(self, model: DataModel, circular_root_models: Collection[ModelKey]) -> bool:
        """Return whether collapsing keeps a root model named wherever it is referenced.

        A circular root model cannot be fully inlined, runtime rules the model owns would be lost with it, and
        flattening it would discard constraints that live on a nested array or union alias, or on a mapping.
        """
        if (
            not model.fields
            or model_key_of(model.reference) in circular_root_models
            or model.has_runtime_object_validation
        ):
            return True
        if not self.field_constraints and _has_preservable_root_constraints(model):
            return True
        root_type_field = model.fields[0]
        return (
            isinstance(constraints := root_type_field.constraints, ConstraintsBase)
            and constraints.has_constraints
            and (root_type_field.data_type.is_dict or root_type_field.data_type.is_mapping)
        )

    def __collapse_root_models_in_scope(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
        self,
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
        schema gives, so that every holder follows the same decision.
        """
        generation_store = self.generation_store
        generation_index = generation_store.index
        circular_root_models = self._recursion_facts["circular_roots"]
        is_root_model = self._is_root_model
        collapses_list_item_root_models = self._collapses_list_item_root_models
        redirected_holders: list[tuple[DataModel, DataType, Import | None]] = []

        for model in models:  # noqa: PLR1702
            for model_field in model.fields:
                for data_type in model_field.data_type.all_data_types:
                    reference = data_type.reference
                    if (
                        not reference
                        or not is_root_model(root_type_model := cast("DataModel", reference.source))
                        or self._keeps_root_model_named(root_type_model, circular_root_models)
                    ):
                        continue
                    root_type_field = root_type_model.fields[0]
                    if (
                        self.field_constraints
                        and _has_root_field_constraints(root_type_field)
                        and any(
                            data_type.is_dict or data_type.is_union or data_type.is_list
                            for data_type in model_field.data_type.all_data_types
                        )
                    ):
                        continue

                    if root_type_field.data_type.reference:
                        if self.collapse_root_models_name_strategy is None:
                            continue

                        inner_reference = root_type_field.data_type.reference
                        inner_model = cast("DataModel", inner_reference.source)

                        if (
                            self.collapse_root_models_name_strategy == CollapseRootModelsNameStrategy.Parent
                            and id(root_type_model) not in self._replaced_root_models
                        ):
                            root_model_wrappers, direct_refs = generation_index.root_collapse_reference_usage(
                                inner_reference,
                                excluded_model=root_type_model,
                                is_root_model=is_root_model,
                            )

                            if len(root_model_wrappers) > 1:
                                warn(
                                    f"Cannot apply 'parent' strategy for '{inner_model.class_name}' - "
                                    f"it is referenced by multiple root models: "
                                    f"{[m.class_name for m in root_model_wrappers]}. Skipping collapse.",
                                    stacklevel=2,
                                )
                                continue

                            if direct_refs:
                                warn(
                                    f"Cannot apply 'parent' strategy for '{inner_model.class_name}' - "
                                    f"it is directly referenced by non-wrapper models. Skipping collapse.",
                                    stacklevel=2,
                                )
                                continue

                            self.__replace_root_model_by_inner_model(
                                root_type_model, inner_model, model_path_to_module_name
                            )

                        if (
                            has_remaining_root_references := generation_store._root_collapse_has_data_type_references(  # noqa: SLF001
                                root_type_model.reference,
                                excluded_data_type=data_type,
                            )
                        ) is None:
                            has_remaining_root_references = generation_index.has_data_type_references_other_than(
                                root_type_model.reference,
                                data_type,
                            )
                        generation_store.collapse_root_data_type(data_type, inner_reference)
                        self.__carry_const_narrowings(root_type_field.data_type, data_type)

                        redirected_holders.append((
                            model,
                            data_type,
                            imports.reference_paths.get(root_type_model.path),
                        ))
                        if not has_remaining_root_references:
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
                        self._copy_model_type(root_type_field.data_type)
                        if source_module_name != target_module_name
                        else root_type_field.data_type.model_copy()
                    )
                    self.__carry_const_narrowings(root_type_field.data_type, copied_data_type)
                    if (
                        has_remaining_root_references := generation_store._root_collapse_has_data_type_references(  # noqa: SLF001
                            root_type_model.reference,
                            excluded_data_type=data_type,
                        )
                    ) is None:
                        has_remaining_root_references = generation_index.has_data_type_references_other_than(
                            root_type_model.reference,
                            data_type,
                        )

                    replacement_context = None
                    if isinstance(field_ := data_type.parent, self.data_model_field_type):
                        # for field
                        # override empty field by root-type field
                        generation_store.set_field_extras(model_field, {**root_type_field.extras, **model_field.extras})
                        if root_type_field.runtime_expression_imports:
                            model_field._set_runtime_expression_imports(  # noqa: SLF001
                                (
                                    *model_field.runtime_expression_imports,
                                    *runtime_expression_imports(model_field.extras),
                                )
                            )
                        generation_store.apply_const(model_field, refresh="deferred")

                        if self.field_constraints:
                            generation_store.set_field_constraints(
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
                        if self.field_constraints:
                            generation_store.set_field_constraints(
                                model_field,
                                ConstraintsBase.merge_constraints(root_type_field.constraints, model_field.constraints),
                            )
                        discriminator = root_type_field.extras.get("discriminator")
                        if discriminator and root_type_field.SUPPORTS_DISCRIMINATOR:
                            has_any_variant = any(_is_any_variant(dt) for dt in copied_data_type.data_types)
                            if not has_any_variant:  # pragma: no branch
                                prop_name = (
                                    discriminator.get("propertyName")
                                    if isinstance(discriminator, dict)
                                    else discriminator
                                )
                                mapping = discriminator.get("mapping", {}) if isinstance(discriminator, dict) else {}
                                field_name, _ = self.model_resolver.get_valid_field_name_and_alias(
                                    field_name=prop_name,
                                    model_type=self.field_name_model_type,
                                )
                                if _discriminator_variants_are_valid(
                                    copied_data_type.data_types,
                                    field_name,
                                    mapping,
                                    enum_member_tags=self.use_enum_values_in_discriminator,
                                ):
                                    copied_data_type.discriminator = field_name
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
                        root_type_field, whole_type=isinstance(field_, self.data_model_field_type)
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
                                ledger=self._import_ledger,
                                producer=producer,
                            )

                        original_field = get_most_of_parent(data_type, DataModelFieldBase)
                        if original_field:  # pragma: no cover
                            # TODO: Improve detection of reference type
                            # Use list instead of set because Import is not hashable
                            excluded_imports = [IMPORT_OPTIONAL, IMPORT_UNION]
                            field_imports = [i for i in original_field.imports if i not in excluded_imports]
                            self._import_ledger.append(
                                imports, field_imports, ImportProducer("collapse-root-models", model, original_field)
                            )

                    self._import_ledger.remove_referenced_imports(imports, root_type_model.path, producer)
                    if not has_remaining_root_references:
                        unused_models.append(root_type_model)

        self.__import_collapsed_inner_models(
            models,
            redirected_holders,
            imports,
            scoped_model_resolver,
            model_path_to_module_name,
            is_init=is_init,
            internal_modules=internal_modules,
        )

    def __emitting_module_name(self, model: DataModel, model_path_to_module_name: dict[str, str]) -> str:
        """Return the name of the module whose live model list holds a model, found by identity.

        A model that a module's processing puts into its list after modules are structured, such as a reuse subclass,
        is looked up in the live lists and remembered.
        """
        if (module_name := self._emitting_module_names.get(id(model))) is not None:
            return module_name
        for module_name, module_models in self._emitting_module_lists:
            if any(module_model is model for module_model in module_models):
                self._emitting_module_names[id(model)] = module_name
                return module_name
        return _get_model_module_name(model, model_path_to_module_name)

    def __take_recursion_facts(self, model: DataModel, path_owner: DataModel) -> None:
        """Give ``model``, which takes the path of ``path_owner``, the recursion facts of ``path_owner``, not its own.

        Legacy row ``legacy-recursion-follows-path``: the facts were reference paths that only the relocation into
        ``_internal`` modules followed, so a model that took another model's path took that model's facts.
        """
        recursion_facts = self._recursion_facts
        key, owner_key = model_key_of(model.reference), model_key_of(path_owner.reference)

        def taken(keys: frozenset[ModelKey]) -> frozenset[ModelKey]:
            if (key in keys) == (owner_key in keys):
                return keys
            return keys ^ {key}

        self._recursion_facts = RecursionFacts(
            require_update=taken(recursion_facts["require_update"]),
            circular_roots=taken(recursion_facts["circular_roots"]),
        )

    def __replace_root_model_by_inner_model(
        self,
        root_model: DataModel,
        inner_model: DataModel,
        model_path_to_module_name: dict[str, str],
    ) -> None:
        """Give the inner model the class name of the root model it replaces, in the module that emits it.

        The inner model also takes the root model's path, which tag values read, as the root model leaves the
        output. Modules processed earlier imported the inner model by its old name under its old path.
        """
        old_class_name, old_path = inner_model.class_name, inner_model.path
        inner_module_name = self.__emitting_module_name(inner_model, model_path_to_module_name)
        self.generation_store.rename_model(
            inner_model,
            class_name=root_model.class_name,
            new_path=root_model.reference.path,
        )
        self._replaced_root_models[id(root_model)] = root_model, inner_model
        _share_path_identity(root_model, inner_model)
        self.__take_recursion_facts(inner_model, root_model)
        _assign_module_name(model_path_to_module_name, inner_model, inner_module_name)
        _assign_module_name(model_path_to_module_name, root_model, inner_module_name)
        if inner_model.class_name != old_class_name:
            self.__record_renamed_type(inner_model, _RenamedType(old_class_name, old_path))

    def __import_collapsed_inner_models(  # noqa: PLR0913
        self,
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
        self._import_ledger.remove(imports, root_imports, _COLLAPSE_ROOT_MODELS)
        scoped_model_resolver.delete_names(
            {root_import.binding_name for root_import in root_imports}.difference(_iter_import_bindings(imports)).union(
                self.__names_left_by_replaced_roots(
                    models, keyed_models(self.__module_members(models), "module members")
                )
            )
        )
        for holder, data_type, _ in redirected_holders:
            reference = cast("Reference", data_type.reference)
            holder_module_name = _get_model_module_name(holder, model_path_to_module_name)
            inner_module_name = (
                holder_module_name
                if (inner_model := reference.source) is None
                else self.__emitting_module_name(cast("DataModel", inner_model), model_path_to_module_name)
            )
            self.__set_reference_expression(
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

    def __set_circular_root_model_paths(
        self,
        module_models: ModuleModels,
        *,
        require_dict_key_edge: bool = False,
    ) -> None:
        """Record the root models in circular components before collapsing them."""
        snapshot = GraphSnapshot.CIRCULAR_ROOT_MODELS
        keyed = keyed_models(
            (model for _, models in module_models for model in models if self._is_root_model(model)), snapshot.value
        )
        index = self.generation_store.index
        edges = {
            EdgeKind.ANY_REFERENCE: {
                key: index.referenced_keys_for_model(model, keyed, all_dict_keys=True)
                for key, model in keyed.models.items()
            }
        }
        if require_dict_key_edge:
            edges[EdgeKind.MAPPING_KEY] = {
                key: keyed.keys_of(self.__root_dict_key_references(model)) for key, model in keyed.models.items()
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
                lambda key: silent.keys_of(self.__root_dict_key_references(keyed.models[key])),
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
        recursion_facts = self._recursion_facts
        self._recursion_facts = RecursionFacts(
            require_update=recursion_facts["require_update"], circular_roots=circular_roots
        )

    def __root_dict_key_references(self, model: DataModel) -> list[Reference]:  # noqa: PLR6301
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

    def __set_default_enum_member(
        self,
        models: list[DataModel],
        *,
        can_retain_cache: bool,
    ) -> None:
        """Convert matching defaults to enum members while preserving unmatched list values."""
        if not self.set_default_enum_member and DefaultValueType.Enum not in self.deserialize_default_value_types:
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
                self.generation_store.set_field_default(model_field, default)
                _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __set_validate_default_on_fields(
        self,
        models: list[DataModel],
        *,
        can_retain_cache: bool,
    ) -> None:
        """Set validate_default=True on fields with structured defaults needing validation."""
        deserialized_default = False
        for model in models:
            if isinstance(model, Enum):
                continue
            for model_field in model.fields:
                if model_field.required and not model_field.use_default_with_required:
                    continue
                if model_field.default is None or model_field.default is UNDEFINED:
                    continue
                if isinstance(model_field.default, Member):
                    continue
                deserialized_default = (
                    self.__deserialize_default_value(
                        model,
                        model_field,
                        can_retain_cache=can_retain_cache,
                    )
                    or deserialized_default
                )
                if (
                    _needs_validate_default(model_field.data_type)
                    and model_field.enable_structured_default_validation()
                ):
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
        if deserialized_default:
            normalize_default_value_constraints(
                self, models, data_type_manager=self.data_type_manager, import_overrides=self._import_overrides
            )

    def __deserialize_default_value(
        self,
        model: DataModel,
        field: DataModelFieldBase,
        *,
        can_retain_cache: bool,
    ) -> bool:
        """Deserialize one backend-declared scalar default or record its warning."""
        if (
            not model.SUPPORTS_DESERIALIZED_DEFAULT_VALUES
            or field.has_default_factory
            or isinstance(field.default, (PythonCode, PythonRuntimeExpression))
        ):
            return False
        if (resolved := resolve_default_value_descriptor(self.data_type_manager, field.data_type)) is None:
            return False
        annotation_import, descriptor = resolved
        if has_import_override(self._import_overrides, annotation_import, descriptor.constructor_import):
            return False
        match descriptor.recipe:
            case DefaultValueRecipe.Decimal:
                return self.__deserialize_decimal_default(
                    model,
                    field,
                    descriptor,
                    can_retain_cache=can_retain_cache,
                )
        return False  # pragma: no cover - future backend recipe

    def __deserialize_decimal_default(
        self,
        model: DataModel,
        field: DataModelFieldBase,
        descriptor: DefaultValueDescriptor,
        *,
        can_retain_cache: bool,
    ) -> bool:
        """Deserialize one Decimal recipe after backend classification."""
        default_type = type(field.default)
        if default_type.__module__ == "decimal" and default_type.__name__ == "Decimal":
            # Retain the constructor identity for alias resolution.
            value = str(field.default)
        elif descriptor.option_kind not in self.deserialize_default_value_types:
            self.__record_decimal_default_warning(model, field)
            return False
        else:
            from decimal import Decimal, InvalidOperation  # noqa: PLC0415

            try:
                value = str(Decimal(str(field.default)))
            except (InvalidOperation, TypeError, ValueError):
                self.__record_decimal_default_warning(model, field)
                return False
        self.generation_store.set_field_default(
            field, PythonRuntimeExpression.from_import_call(descriptor.constructor_import, repr(value), value=value)
        )
        field._set_runtime_expression_imports((descriptor.constructor_import,))  # noqa: SLF001
        self._register_runtime_expression()
        _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
        return True

    def __record_decimal_default_warning(self, model: DataModel, field: DataModelFieldBase) -> None:
        """Record a bounded example set without retaining every affected field."""
        self._decimal_default_warning_count += 1
        examples = self._decimal_default_warning_examples
        if examples is None:
            self._decimal_default_warning_examples = [f"{model.class_name}.{field.name or '<root>'}"]
        elif len(examples) < _DECIMAL_WARNING_EXAMPLE_LIMIT:
            examples.append(f"{model.class_name}.{field.name or '<root>'}")

    def __warn_about_decimal_defaults(self) -> None:
        """Emit one actionable warning for all Decimal defaults in this generation."""
        if not (count := self._decimal_default_warning_count):
            return
        examples = ", ".join(self._decimal_default_warning_examples or ())
        remainder = (
            f" and {count - len(self._decimal_default_warning_examples or ())} more"
            if count > _DECIMAL_WARNING_EXAMPLE_LIMIT
            else ""
        )
        plural = "s" if count != 1 else ""
        verb = "were" if count != 1 else "was"
        if DefaultValueType.Decimal in self.deserialize_default_value_types:
            message = (
                f"{count} Decimal default value{plural} could not be deserialized and {verb} kept serialized "
                f"to keep generated modules importable: {examples}{remainder}."
            )
        else:
            message = (
                f"{count} Decimal default value{plural} {verb} emitted as serialized data instead of Decimal: "
                f"{examples}{remainder}. Generated output is unchanged. "
                "Use --deserialize-default-values decimal to enable Decimal deserialization."
            )
        self._decimal_default_warning_count = 0
        self._decimal_default_warning_examples = None
        warn(message, DefaultValueTypeWarning, stacklevel=2)

    def __record_variant_discriminator_tags(  # noqa: PLR0913
        self,
        variant: DataModel,
        union_name: str,
        *,
        property_name: str,
        field_name: str,
        mapping: dict[str, str],
        values: list[DiscriminatorValue],
        field_name_mode: TagFieldMatch,
    ) -> None:
        """Remember the tags unions map a variant to, unless the variant declares its own tag.

        The values of a later union come from the tag an earlier union wrote, so the tags it maps the variant to are
        derived again from its mapping or the schema name.
        """
        if (recorded := self._variant_discriminator_tags.get(key := (id(variant), property_name))) is not None:
            if not same_tags(values, recorded.kept, "legacy-python-equality"):
                recorded.others.setdefault(recorded.owner, recorded.kept)
                recorded.kept, recorded.owner = values, union_name
                recorded.others = {
                    union: tags
                    for union, tags in recorded.others.items()
                    if not same_tags(tags, values, "legacy-python-equality")
                }
            elif not same_tags(
                tags := _get_union_discriminator_values(variant, field_name, mapping, field_name_mode=field_name_mode),
                values,
                "legacy-python-equality",
            ):
                recorded.others.setdefault(union_name, tags)
            return
        if _find_discriminator_value(variant.fields, field_name, field_name_mode=field_name_mode) is None:
            self._variant_discriminator_tags[key] = _VariantDiscriminatorTags(
                variant, property_name, values, union_name, {}
            )

    def __get_collapsed_unions(
        self, model: DataModel, field: DataModelFieldBase
    ) -> list[tuple[str, list[DataType], dict[str, str]]]:
        """Return the discriminated unions that collapsed reuse removed as duplicates of a union field of a model.

        Users of a removed duplicate use the kept model, so their union decodes only the tags the kept one gives.
        """
        unions: list[tuple[str, list[DataType], dict[str, str]]] = []
        if not (duplicates := self._collapsed_reuse_models.get(id(model))):
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
                if self._is_root_model(duplicate)
                else f"{duplicate.class_name}.{duplicate_field.name}"
            )
            unions.append((name, data_types, discriminator.get("mapping", {})))
        return unions

    def __record_reused_variant_tag_field(self, field: DataModelFieldBase, *, annotates_tag: bool) -> None:
        """Remember a field that a union with a reused variant annotates with a new tag, else forget it.

        Such unions were left untagged before, so the ``Literal`` that only their tags import must not rename a model.
        """
        if annotates_tag:
            self._reused_variant_tag_fields[id(field)] = field
        else:
            self._reused_variant_tag_fields.pop(id(field), None)

    def __record_collapsed_variant_tags(  # noqa: PLR0913
        self,
        variant: DataModel,
        union_name: str,
        data_type: DataType,
        *,
        property_name: str,
        field_name: str,
        mapping: dict[str, str],
        values: list[DiscriminatorValue],
        field_name_mode: TagFieldMatch,
    ) -> None:
        """Remember the tags a union maps a variant to when collapsed reuse replaced the model the union names.

        The union maps the removed duplicate to the tags its own schema, the mapping or its name give, which the kept
        model need not carry. They replace the tags derived from the kept model for that union.
        """
        user, reference = self._collapsed_reuse_users.get(id(data_type), (None, None))
        if user is not data_type or reference is None or not isinstance(removed := reference.source, DataModel):
            return
        key = (id(variant), property_name)
        if not same_tags(
            tags := _get_discriminator_values(removed, field_name, mapping, field_name_mode=field_name_mode),
            values,
            "legacy-python-equality",
        ):
            self._variant_discriminator_tags.setdefault(
                key, _VariantDiscriminatorTags(variant, property_name, values, union_name, {})
            ).others[union_name] = tags
        elif (recorded := self._variant_discriminator_tags.get(key)) is not None:
            recorded.others.pop(union_name, None)

    def __warn_about_dropped_constraints(self, contexts: Sequence[ModuleContext]) -> None:
        """Warn once per field whose constraints the output model leaves out on its type, and keep each warning."""
        fields: set[tuple[ModulePath, str]] = set()
        for ctx in contexts:
            for model in ctx.models:
                for field in model.fields:
                    if message := " ".join(
                        filter(None, (field.dropped_constraints_warning, self.__tuple_unique_items_warning(field)))
                    ):
                        warn(message, stacklevel=2)
                        fields.add((ctx.module, message))
        self._dropped_constraint_fields = frozenset(fields)

    def __tuple_unique_items_warning(self, field: DataModelFieldBase) -> str | None:
        """Return a warning for a unique array written as a tuple, which --use-unique-items-as-set cannot make a set.

        Runtime schema validators check uniqueItems on tuples too, so nothing is left out with them.
        """
        if (
            not self.use_unique_items_as_set
            or self.generate_schema_validators
            or not _declares_unique_items(field)
            or (tuple_type := _unique_items_tuple(field.data_type)) is None
        ):
            return None
        return field._generated_without_constraints(  # ruff: ignore[private-member-access]
            f"--use-unique-items-as-set writes a uniqueItems array as a set, which would lose the positions of "
            f"{tuple_type.type_hint}",
            1,
        )

    def __record_pydantic_floor(self, contexts: Sequence[ModuleContext]) -> None:
        """Record the Pydantic release that the emitted models need above the explicit target, for the generation.

        Every fact the constructs read is final here, after the last writer before rendering. A model or field owns
        each construct, named with its module when the parse writes more than one. Output that Pydantic does not
        build has no floor to state.
        """
        if (mode := check_mode(CheckArea.GENERATED_CODE_REQUIREMENTS)) is not None and (
            unlisted := self.data_model_type._CONFIG_KEYS_WITHOUT_REQUIREMENT()  # noqa: SLF001
        ):
            mode.violation(Error, f"config keys without a row in _requirements.py: {', '.join(unlisted)}")
        if (record := _PYDANTIC_FLOORS.get()) is None or self.target_pydantic_version is None:
            return
        if not self.data_model_type._READ_BY_PYDANTIC:  # noqa: SLF001
            record.record(self, None)
            return
        used: list[tuple[PydanticConstruct, str]] = []
        for ctx in contexts:
            prefix = (
                f"{module_name}."
                if len(contexts) > 1 and (module_name := _module_name_from_module_path(ctx.module))
                else ""
            )
            for model in ctx.models:
                owner = f"{prefix}{model.class_name}"
                used.extend(
                    (construct, owner if field_name is None else f"{owner}.{field_name}")
                    for construct, field_name in model._pydantic_constructs()  # noqa: SLF001
                )
        record.record(self, pydantic_floor(self.target_pydantic_version, used))

    def record_dropped_constraints(self) -> None:
        """Record this parse's dropped-constraint warnings as those of the generation that returns its models."""
        if (record := _DROPPED_CONSTRAINTS.get()) is not None:
            record.warnings = self._dropped_constraint_fields

    def __warn_about_variant_discriminator_tags(self) -> None:
        """Warn once per variant whose generated tag differs from a tag a union maps it to."""
        for recorded in self._variant_discriminator_tags.values():
            for message in recorded.warning_messages():
                warn(message, stacklevel=2)
        self._variant_discriminator_tags.clear()

    def __warn_about_unmatched_model_name_keys(self) -> None:
        """Warn about the ``--model-name-map`` keys that matched no model, after parsing has named every model."""
        if _UNMATCHED_MODEL_NAME_KEYS_REPORTED.get() and (
            unmatched_keys := sorted({redact_reference(key) for key in self.model_resolver.unmatched_model_name_keys()})
        ):
            listed = ", ".join(map(repr, unmatched_keys))
            warn(
                f"--model-name-map: {len(unmatched_keys)} key{'' if len(unmatched_keys) == 1 else 's'} "
                f"matched no generated model: {listed}",
                stacklevel=2,
            )

    def __warn_about_msgspec_decoding(self, contexts: Sequence[ModuleContext]) -> None:
        """Warn once per union and enum of msgspec output that msgspec refuses to decode."""
        if not self.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
            return
        from datamodel_code_generator.model.union import _DataTypeUnionBase  # noqa: PLC0415

        models = [model for context in contexts for model in context.models]
        named = {model.class_name: model for model in models}
        for model in models:
            if isinstance(model, Enum):
                if not self.use_msgspec_nullable_enum_alias and (message := _msgspec_null_enum_message(model)):
                    warn(message, stacklevel=2)
                continue
            if isinstance(model, _DataTypeUnionBase):
                if (refusal := _msgspec_refused_structs(named.get(field.name) for field in model.fields))[0]:
                    warn(_msgspec_union_message(model.class_name, refusal, discriminated=False), stacklevel=2)
                continue
            for field in model.fields:
                if field.is_class_var:
                    continue
                member = _literal_member(field.data_type)
                discriminated = bool(field.extras.get("discriminator"))
                nested = [*field.data_type.all_data_types][::-1]
                refusal = next(
                    filter(operator.itemgetter(0), map(_msgspec_refused_union_structs, nested)), _NO_MSGSPEC_REFUSAL
                )
                refused = bool(refusal[0]) and (
                    refusal[1] is not None or not (discriminated and self.use_msgspec_tagged_unions)
                )
                clashes = _msgspec_field_enum_clashes(nested)
                if member is None and not refused and not clashes:
                    continue
                name = model.class_name if self._is_root_model(model) else f"{model.class_name}.{field.name}"
                if member is not None:
                    warn(_msgspec_literal_member_message(name, member), stacklevel=2)
                if refused:
                    warn(_msgspec_union_message(name, refusal, discriminated=discriminated), stacklevel=2)
                for clash in clashes.items():
                    warn(_msgspec_enum_union_message(name, clash), stacklevel=2)

    def _apply_inherited_field_default(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        class_name: str,
    ) -> None:
        """Resolve an inherited schema default in the derived class scope."""
        apply_inherited_field_default(
            field,
            inherited_field,
            class_name=class_name,
            model_resolver=self.model_resolver,
            apply_default_values_for_required_fields=self.apply_default_values_for_required_fields,
        )

    def __sort_models(
        self,
        models: list[DataModel],
        imports: Imports,
        *,
        use_deferred_annotations: bool,
    ) -> None:
        if not self.keep_model_order:
            return

        _reorder_models_keep_model_order(
            models,
            imports,
            pydantic_v2_root_model_type=self.pydantic_v2_root_model_type,
            use_deferred_annotations=use_deferred_annotations,
        )

    def __change_field_name(  # noqa: PLR0912
        self,
        models: list[DataModel],
        *,
        can_retain_cache: bool,
    ) -> None:
        if not (self.data_model_type.SUPPORTS_FIELD_RENAMING and self.data_model_type.NORMALIZES_FIELD_NAMES):
            return

        rename_type = (
            self.field_type_collision_strategy == FieldTypeCollisionStrategy.RenameType
            and self.data_model_type.SUPPORTS_FIELD_TYPE_RENAMING
        )
        all_class_names = (
            {cast("str", model.class_name) for model in models if model.class_name} if rename_type else set()
        )
        resolver: ModelResolver | None = None

        for model in models:
            if "Enum" in model.base_class or (not model.BASE_CLASS and not isinstance(model, self.data_model_type)):
                continue

            for field in (field for field in model.fields if field.name != self.data_model_type.TYPED_EXTRA_FIELD_NAME):
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
                    and (self.data_model_type.NORMALIZES_FIELD_NAMES or field_name in reference_type_names)
                ):
                    resolver = resolver or ModelResolver(
                        snake_case_field=self.snake_case_field,
                        remove_suffix_number=True,
                        target_python_version=self.target_python_version,
                    )
                    if colliding_reference is not None:
                        resolver._reset_for_reuse(all_class_names.copy())  # noqa: SLF001
                        source = cast("DataModel", colliding_reference.source)
                        resolver.exclude_names.add(cast("str", field_name))
                        new_class_name = resolver.add(["type"], cast("str", source.class_name)).name  # ty: ignore[redundant-cast]
                        self.__record_renamed_type(source, _RenamedType(source.class_name, colliding_reference.path))
                        self.generation_store.rename_model(source, class_name=new_class_name)
                        all_class_names.add(new_class_name)
                    else:
                        resolver._reset_for_reuse(reference_type_names)  # noqa: SLF001
                        new_field_name = resolver._get_unique_field_name(cast("str", field_name))  # noqa: SLF001
                        if field_name != new_field_name:
                            _rename_field_preserving_alias(field, new_field_name)
                            _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

                if (
                    self.data_model_type.NORMALIZES_FIELD_NAMES
                    and (current_name := field.name) in self.builtin_names
                    and any(_is_builtin_type_collision(current_name, dt) for dt in field.data_type.all_data_types)
                ):
                    if field.alias is None:
                        field.alias = field_name
                    field.name = f"{current_name}_"
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __same_named_fields(self, model: DataModel) -> list[DataModelFieldBase]:
        """Return the fields of the model named like a type that an annotation of the model refers to."""
        model_type = self.data_model_type
        if "Enum" in model.base_class or (not model.BASE_CLASS and not isinstance(model, model_type)):
            return []
        type_names = {
            reference.short_name
            for field in model.fields
            for data_type in field.data_type.all_data_types
            if (reference := _annotated_reference(data_type)) is not None
        }
        return [
            field
            for field in model.fields
            if field.name != model_type.TYPED_EXTRA_FIELD_NAME and field.name in type_names
        ]

    def __hides_named_type(
        self,
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
            "python.deferred_annotations", self.target_python_version.version_key
        )
        assigned = (
            model.field_binds_class_attribute(field)
            or (
                (inherited := self.__get_inherited_constructor_info(model)) is not None
                and self.__get_constructor_field_adjustment(
                    model,
                    field,
                    inherited,
                    supports_inherited_override=model.SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT,
                )
                is not None
            )
            or (
                self.use_annotated
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

    def __rename_named_type(
        self,
        field: DataModelFieldBase,
        reference: Reference,
        class_names: set[str],
        all_models: Collection[DataModel],
        derived_models: Mapping[ModelKey, list[DataModel]],
    ) -> None:
        """Rename the type that the field hides to a name that no field of a family using the type has."""
        source = cast("DataModel", reference.source)
        resolver = ModelResolver(
            snake_case_field=self.snake_case_field,
            remove_suffix_number=True,
            target_python_version=self.target_python_version,
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
        self.__record_renamed_type(source, _RenamedType(source.class_name, reference.path))
        self.generation_store.rename_model(source, class_name=resolver.add(["type"], source.class_name).name)
        class_names.add(source.class_name)

    def __family_hides_named_type(
        self,
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
        if (hidden := self._hidden_named_types.get((model_key_of(model.reference), field_name))) is not None:
            return hidden
        family = _model_family(model, derived_models)
        hidden = any(
            field.name == field_name
            and self.__hides_named_type(member, field, positions, use_deferred_annotations=use_deferred_annotations)
            for member in family.values()
            for field in self.__same_named_fields(member)
        )
        self._hidden_named_types.update(dict.fromkeys(((key, field_name) for key in family), hidden))
        return hidden

    def __rename_fields_hiding_types(
        self, models: list[DataModel], *, can_retain_cache: bool, use_deferred_annotations: bool
    ) -> None:
        """Rename a field named like a type of its class's annotations where the finished class hides that type.

        A model that normalizes field names has renamed every field named like its own type already. The classes of
        one family write the field under one new name, which no field of the family has as its name or alias.
        """
        model_type = self.data_model_type
        if not model_type.SUPPORTS_FIELD_RENAMING:
            return
        declarations = [(model, fields) for model in models if (fields := self.__same_named_fields(model))]
        if not declarations:
            return
        positions = _ModelPositions(models, "hidden named types")
        all_models = {id(model): model for model in chain(self.results, models)}.values()
        derived_models = _derived_models(all_models)
        rename_type = (
            self.field_type_collision_strategy == FieldTypeCollisionStrategy.RenameType
            and model_type.SUPPORTS_FIELD_TYPE_RENAMING
        )
        class_names = {model.class_name for model in models} if rename_type else set()
        for model, fields in declarations:
            for field in fields:
                field_name = field.name
                if (
                    not self.__family_hides_named_type(
                        model, field_name, derived_models, positions, use_deferred_annotations=use_deferred_annotations
                    )
                    or (reference := _same_named_reference(model, field_name, skips_aliases=rename_type)) is None
                ):
                    continue
                if rename_type:
                    self.__rename_named_type(field, reference, class_names, all_models, derived_models)
                    continue
                if (new_name := self._hiding_field_names.get((model_key_of(model.reference), field_name))) is None:
                    family = _model_family(model, derived_models)
                    resolver = ModelResolver(
                        exclude_names=_family_field_names(family),
                        snake_case_field=self.snake_case_field,
                        remove_suffix_number=True,
                        target_python_version=self.target_python_version,
                    )
                    new_name = resolver._get_unique_field_name(cast("str", field_name))  # noqa: SLF001
                    self._hiding_field_names.update(dict.fromkeys(((key, field_name) for key in family), new_name))
                _rename_field_preserving_alias(field, new_name)
                _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __set_one_literal_on_default(self, models: list[DataModel], *, can_retain_cache: bool) -> None:
        """Write the default, required and nullability one literal gives its field (``decide_literal_default``)."""
        if not self.use_one_literal_as_default:
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
                if decision.writes_default:
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __restore_inherited_field_names_for_unique_aliases(self, models: list[DataModel]) -> None:  # noqa: PLR6301
        """Reserve inherited Python names for output models with unique wire aliases."""
        for model in models:
            if model.REQUIRES_UNIQUE_FIELD_ALIASES and _restore_inherited_field_names_for_unique_aliases(
                model.fields,
                get_inherited_fields(_find_base_classes(model)),
            ):
                model.clear_imports_cache()

    @classmethod
    def __fix_tag_opt_out_field_ordering(cls, opted_out: list[DataModel], models: list[DataModel]) -> None:
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
            if get_constructor_ordering_conflicts(model, effective_fields, set()):
                model.enable_model_keyword_only()

    def __fix_constructor_field_ordering(self, models: list[DataModel]) -> None:
        """Fix constructor field ordering after inherited defaults are resolved."""
        for model in models:
            restored_inherited_state = False
            for field in model.fields:
                restored_inherited_state = (
                    model.restore_required_inherited_field_state(field) or restored_inherited_state
                )

            if (inherited := self.__get_inherited_constructor_info(model)) is None:
                order_key = self.__get_local_constructor_order_key(model)
                if self.__has_local_constructor_ordering_conflict(model, order_key):
                    model.clear_imports_cache()
                    self.generation_store.set_fields(model, sorted(model.fields, key=order_key))
                elif restored_inherited_state:  # pragma: no cover - marker requires a generated base
                    model.clear_imports_cache()
                continue
            field_adjustments: Iterator[tuple[DataModelFieldBase, _ConstructorFieldAdjustment]] = (
                (field, adjustment)
                for field in model.fields
                if (
                    adjustment := self.__get_constructor_field_adjustment(
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
                    model.clear_imports_cache()
                    self.generation_store.set_fields(model, sorted(model.fields, key=_assigned_key(model)))
                continue

            _apply_constructor_field_adjustments(
                model,
                first_adjustment,
                field_adjustments,
            )
            model.clear_imports_cache()
            self.generation_store.set_fields(model, sorted(model.fields, key=_assigned_key(model)))

    def __get_local_constructor_order_key(  # noqa: PLR6301
        self,
        model: DataModel,
    ) -> Callable[[DataModelFieldBase], tuple[bool, bool]]:
        """Return the positional order key that keeps constructor defaults last.

        A required field rendered as ``= Field(...)`` is a dataclass default but required in the validator's
        signature, so it also precedes fields with real defaults when the output model asks for that order.
        Otherwise only the assignments the output model keeps among the required fields precede them.
        """
        if model.orders_required_assignments_before_defaults():
            return lambda field: ((facts := model._field_assignment_facts(field)).assigned, facts.has_default)  # noqa: SLF001
        return lambda field: (
            assigned := model._field_assignment_facts(field).assigned,  # noqa: SLF001
            assigned and not model.keeps_assignment_with_required_fields(field),
        )

    @classmethod
    def __warn_required_assignments_after_defaults(cls, models: list[DataModel]) -> None:
        """Warn once per model whose signature lists a required ``= Field(...)`` field after a default.

        Such a field, its own or inherited, is a dataclass default, so the class is created, but its validator
        signature cannot be read on Pydantic 2.13+, which Python 3.10 does while creating a subclass.
        """
        for model in models:
            if (
                model.REQUIRED_ASSIGNMENT_COUNTS_AS_CONSTRUCTOR_DEFAULT
                and not model.orders_required_assignments_before_defaults()
                and cls.__has_required_assignment_after_default(model)
            ):
                warn(
                    f"{model.class_name} has a required field after a field with a default, in its own fields or "
                    "those of its bases: Pydantic 2.13 and later cannot read its signature, and on Python 3.10 its "
                    "subclasses fail at import. Pass --target-pydantic-version 2.14 to write required fields first.",
                    stacklevel=2,
                )

    @classmethod
    def __has_required_assignment_after_default(cls, model: DataModel) -> bool:
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

    def __has_local_constructor_ordering_conflict(  # noqa: PLR6301
        self,
        model: DataModel,
        order_key: Callable[[DataModelFieldBase], tuple[bool, bool]],
    ) -> bool:
        """Return whether a local required field follows a positional default."""
        if not model.SUPPORTS_KW_ONLY:
            return False
        previous_key = (False, False)
        for field in model.fields:
            if not model._field_assignment_facts(field).participates_in_constructor:  # noqa: SLF001
                continue
            kw_only = field.constructor_keyword_only
            if kw_only is True or (kw_only is None and model.has_keyword_only_definition()):
                continue
            if (key := order_key(field)) < previous_key:
                return True
            previous_key = key
        return False

    def __get_inherited_constructor_info(self, model: DataModel) -> _InheritedConstructorInfo | None:
        """Return inherited value leaks and exact positional ordering conflicts."""
        if not model.SUPPORTS_KW_ONLY:
            return None
        if not model.base_classes:
            return None

        inherited_models = [
            base
            for base in linearize_data_models(direct_base_models(model, self._base_stand_ins), self._base_stand_ins)
            if base is not self._generic_base_model
        ]
        if not inherited_models:
            return None
        record_ordering_inputs(model, inherited_models, self._base_stand_ins)

        supports_inherited_override = model.SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT
        required_assignment_names: set[str] = set()
        effective_fields: dict[str, tuple[DataModelFieldBase, DataModel, bool | None]] = {}
        for inherited_model in reversed(inherited_models):
            for field in inherited_model.fields:
                if not (field_name := field.name):
                    continue
                has_default: bool | None = None
                if supports_inherited_override:
                    has_default, has_value_default = model._field_assignment_facts(field).default_info  # noqa: SLF001
                    if has_value_default or (has_default and model.REQUIRES_EXPLICIT_INHERITED_FACTORY_OVERRIDE):
                        required_assignment_names.add(field_name)
                effective_fields[field_name] = field, inherited_model, has_default
        for field in model.fields:
            if field.name:
                effective_fields[field.name] = field, model, None

        ordering_conflicts = get_constructor_ordering_conflicts(model, effective_fields, required_assignment_names)
        return _InheritedConstructorInfo(
            frozenset(required_assignment_names),
            ordering_conflicts,
        )

    def __get_constructor_field_adjustment(  # noqa: PLR6301
        self,
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
        if field.required and supports_inherited_override and field.name in inherited.required_assignment_names:
            return "assignment"
        return None

    def __remove_overridden_models(self, models: list[DataModel]) -> list[DataModel]:
        """Remove models that are being overridden by custom types (model-level only).

        Only model-level overrides (keys without dots) cause model removal.
        Scoped overrides (ClassName.field) only affect specific fields.
        """
        if not self._model_type_override_imports:
            return models
        return [m for m in models if m.class_name not in self._model_type_override_imports]

    def __apply_field_type_overrides(self, models: list[DataModel]) -> None:
        """Replace field type references with custom import types, before constructor ordering reads the field types.

        Supports two key formats:
        - Model-level: {"CustomType": "my_app.Type"} - applies to all references
        - Scoped: {"User.field": "my_app.Type"} - applies to specific field only

        Scoped overrides take priority over model-level overrides.
        """
        if not self._type_override_imports:
            return
        for model in models:
            fields_overridden = False
            for field in model.fields:
                # Check scoped override first: "ClassName.field_name"
                scoped_key = f"{model.class_name}.{field.name}"
                match self._type_override_imports.get(scoped_key):
                    case Import() as override_import:
                        self._apply_override_to_field(field, override_import)
                        fields_overridden = True
                    case None if self._model_type_override_imports:
                        # Apply model-level overrides to nested types
                        fields_overridden = self._apply_override_to_data_type(field.data_type) or fields_overridden
                    case None:
                        pass
            if fields_overridden:
                model.clear_imports_cache()

    def __apply_base_class_type_overrides(self, models: list[DataModel]) -> None:
        """Replace base classes that model-level overrides name with custom import types, before constructor ordering.

        Each replaced base keeps the generated model it referenced as its stand-in: constructor ordering, after the
        decision phase, reads it as the only description of the class that replaces it.
        """
        if not self._model_type_override_imports:
            return
        for model in models:
            base_classes_overridden = False
            for base_class in model.base_classes:
                reference = base_class.reference
                if self._apply_override_to_data_type(base_class):
                    self._base_stand_ins[id(base_class)] = reference
                    base_classes_overridden = True
                    for import_ in base_class.all_imports:
                        self.generation_store.add_model_import(model, import_)
            if base_classes_overridden:
                model.clear_imports_cache()

    def _apply_override_to_field(self, field: DataModelFieldBase, override_import: Import) -> None:
        """Apply override to entire field's data_type, keeping type-dependent constraints that the new type takes.

        The arguments of a ``con*`` call stay on an override the generator does not know, which may be a factory such
        as ``pydantic.conint``; a known class takes none, so they are left out, and so are those of the nested types the
        override replaces. An output model that writes ``Field()`` arguments warns about the ones it leaves out.
        """
        from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
            is_known_python_type_path,
        )

        data_type = _copy_data_type(field.data_type)
        data_type.import_ = override_import
        data_type.alias = override_import.import_
        path = (
            f"{override_import.from_}.{override_import.import_}" if override_import.from_ else override_import.import_
        )
        if data_type.kwargs and is_known_python_type_path(path, self.target_python_version.version_key):
            data_type.kwargs = None
        if self.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS and (
            dropped := [
                name
                for nested in field.data_type.all_data_types
                if nested is not field.data_type or data_type.kwargs is None
                for name in nested.kwargs or ()
                if name != "strict"
            ]
        ):
            field.record_overridden_constraints(field.data_type.type_hint, len(dropped))
        self._decide_overridden_constraints(field, path)
        self.generation_store.replace_field_type(field, data_type)
        self.generation_store.detach_data_type_ref(field.data_type)
        self.generation_store.set_nested_data_types(field.data_type, [])

    def _decide_overridden_constraints(self, field: DataModelFieldBase, path: str) -> None:
        """Keep the constraints that the emitted type decides only when the override type at ``path`` takes them.

        An output model that writes constraints as ``Field()`` arguments also decides the number constraints of a
        declared number and the string constraints the replaced type left out this way, and remembers the ones it
        leaves out to warn about them. The untaken constraints of the members, items and values that collapsing inlines
        into the field leave with the type the override replaces.
        """
        type_dependent = field.__dict__.get(_TYPE_DEPENDENT_CONSTRAINTS_KEY) or {}
        constraints = field.constraints
        is_model = isinstance(constraints, ConstraintsBase)
        values = {
            key: value
            for key, value in (constraints.model_dump(by_alias=True) if is_model else constraints or {}).items()
            if value is not None
        }
        declared = (
            overridden_declared_constraints(
                values, type_dependent=type_dependent, untaken=field.pop_untaken_constraints()
            )
            if self.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS
            else {}
        )
        if not type_dependent and not declared:
            return
        target = self.target_python_version.version_key
        values, untaken = resplit_overridden_constraints(
            values,
            type_dependent=type_dependent,
            declared=declared,
            taken=self.data_type_manager.type_constraint_keywords(path, target),
            declared_taken=self.data_type_manager.type_constraint_keywords(
                path, target, number_values=True, target_model_version=self.target_pydantic_version
            ),
        )
        self._mark_untaken_constraints(
            field,
            untaken,
            lambda minimum: self.data_type_manager.type_constraint_keywords(
                path, target, number_values=True, target_model_version=minimum
            ),
        )
        self.generation_store.set_field_constraints(
            field, type(constraints).model_validate(values) if is_model else values
        )

    def _mark_untaken_constraints(
        self,
        field: DataModelFieldBase,
        untaken: dict[str, Any],
        keywords_for: Callable[[str], frozenset[str]],
    ) -> None:
        """Remember the declared constraints a field leaves out, and the target version that would take each.

        ``keywords_for`` returns the number keywords the field's type takes for an explicit target model version.
        """
        if not untaken:
            return
        field.record_untaken_constraints(
            untaken,
            untaken_minimums(
                untaken,
                (
                    (minimum, keywords_for(minimum))
                    for minimum in sorted(
                        {
                            REQUIREMENTS[construct].minimum
                            for construct, _ in self.data_type_manager.TARGET_NUMBER_KEYWORDS.values()
                        },
                        key=_pydantic_version_key,
                    )
                ),
            ),
        )

    def _apply_override_to_data_type(self, data_type: DataType) -> bool:
        """Recursively apply model-level overrides to a DataType."""
        overridden = False
        if data_type.reference and (override_import := self._model_type_override_imports.get(data_type.reference.name)):
            self.generation_store.override_type_import(data_type, override_import)
            data_type.alias = override_import.import_
            self.generation_store.detach_data_type_ref(data_type)
            overridden = True
        # Handle nested types (List[CustomType], Optional[CustomType], etc.)
        for nested in data_type.data_types:
            overridden = self._apply_override_to_data_type(nested) or overridden
        if data_type.dict_key:
            overridden = self._apply_override_to_data_type(data_type.dict_key) or overridden
        return overridden

    @staticmethod
    def __disable_union_operator_for_forward_ref(data_type: DataType, *, use_typing_generics: bool = False) -> None:
        """Keep a quoted name out of ``|`` and, with ``use_typing_generics``, out of builtin generics."""
        data_type.use_union_operator = False
        parent = data_type.parent
        while isinstance(parent, DataType):
            if parent.is_union:
                parent.use_union_operator = False
            if use_typing_generics:
                parent.use_standard_collections = False
            parent = parent.parent

    @staticmethod
    def __forward_references(
        field: DataModelFieldBase,
        model: DataModel,
        model_index: Mapping[str, int],
        index: int,
        hidden_names: Collection[str | None] = (),
    ) -> list[tuple[DataType, str]]:
        """Collect the field's references to same-module models declared at or after the model.

        A model whose name is in ``hidden_names`` counts as declared later.
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
            if isinstance(source, TypeStatement):
                continue  # pragma: no cover
            source_index = model_index.get(name)
            if source_index is not None and source_index >= index:
                forward_references.append((data_type, name))
        return forward_references

    @classmethod
    def __deferrable_alias_cycles(cls, models: Sequence[DataModel], model_index: Mapping[str, int]) -> dict[int, int]:
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
                forward = {id(data_type) for data_type, _ in cls.__forward_references(field, model, model_index, index)}
                if any(len(kinds) > 1 for *_, kinds in _iter_cycle_containers(field.data_type, cycle, cycles, forward)):
                    unresolvable.add(cycle)
        return {alias: cycle for alias, cycle in cycles.items() if cycle not in unresolvable}

    @classmethod
    def __defer_recursive_containers(  # noqa: PLR0913
        cls,
        field: DataModelFieldBase,
        model: DataModel,
        *,
        alias_cycles: Mapping[int, int],
        model_index: Mapping[str, int],
        index: int,
        generation_store: GenerationStore,
    ) -> None:
        """Quote each outermost container that forward-references the model's cycle, with its names unquoted.

        Collapsed root models share nested types between their sites, so the field first gets its own copy.
        A string cannot be an operand of ``|``, so the container and its wrappers stop using the union operator.
        """
        if (cycle := alias_cycles.get(id(model))) is None:
            return
        forward = {id(data_type) for data_type, _ in cls.__forward_references(field, model, model_index, index)}
        if not any(
            True in kinds for *_, kinds in _iter_cycle_containers(field.data_type, cycle, alias_cycles, forward)
        ):
            return
        generation_store.replace_field_type(field, _copy_data_type(field.data_type))
        forward = {id(data_type) for data_type, _ in cls.__forward_references(field, model, model_index, index)}
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
                    nested.alias = reference.short_name
        for root in deferred[0][1][:1]:
            _flatten_union_members(root)

    @classmethod
    def __quote_forward_enum_member_literals(
        cls, field: DataModelFieldBase, positions: _ModelPositions, index: int, *, use_typing_generics: bool
    ) -> bool:
        """Write each enum member literal of the field whose enum is defined later as a string; return whether any."""
        member_literals = _forward_enum_member_literals(field, positions, index)
        for member_literal in member_literals:
            member_literal._set_forward_reference(True)  # noqa: FBT003, SLF001
            cls.__disable_union_operator_for_forward_ref(member_literal, use_typing_generics=use_typing_generics)
        return bool(member_literals)

    @classmethod
    def __update_type_aliases(  # noqa: PLR0913
        cls,
        models: list[DataModel],
        pydantic_v2_root_model_type: type[DataModel] | None,
        *,
        use_deferred_annotations: bool = True,
        defers_recursive_containers: bool,
        generation_store: GenerationStore,
        can_retain_cache: bool,
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
        by a field of the model.
        An enum member literal whose enum the module defines later is written as a string wherever a forward name
        is quoted, and with ``quotes_forward_enum_member_literals`` in every model, as no deferred annotation holds it.
        ``use_typing_generics`` tells that ``typing`` cannot resolve ``list["Name"]``, so a hidden type, and any
        quoted name of a model that asks for it, is written as ``List["Name"]``.
        """
        model_index: dict[str, int] = {m.class_name: i for i, m in enumerate(models)}
        positions = _ModelPositions(models, "type aliases")
        alias_cycles: Mapping[int, int] | None = None
        quotes_in_typing_generics = use_typing_generics and not use_deferred_annotations

        for i, model in enumerate(models):
            is_type_alias_or_root = isinstance(model, TypeAliasBase) or _is_pydantic_v2_root_model(
                model,
                pydantic_v2_root_model_type,
            )
            # When annotations are deferred (from __future__ or native PEP-649) only
            # TypeAliasBase / RootModel need quoting; regular DataModels are fine as-is.
            # Typed extra annotations are an exception: class-body __annotations__ dicts
            # are evaluated immediately, and Pydantic can force native deferred annotations
            # while constructing the class, so their forward references must stay quoted.
            process_all_fields = is_type_alias_or_root or not use_deferred_annotations
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
            for field in model.fields:
                if quotes_member_literals and cls.__quote_forward_enum_member_literals(
                    field,
                    positions,
                    i,
                    use_typing_generics=quotes_in_typing_generics and model.WRITES_QUOTED_NAMES_IN_TYPING_GENERICS,
                ):
                    has_aliased_forward_ref = True
                if (
                    hidden_only := not process_all_fields and not field.requires_immediate_forward_reference_resolution
                ) and not hidden_names:
                    continue
                forward_references = cls.__forward_references(field, model, model_index, i, hidden_names)
                if hidden_only:
                    forward_references = [reference for reference in forward_references if reference[1] in hidden_names]
                if not forward_references:
                    continue
                for data_type, name in forward_references:
                    data_type.alias = f'"{name}"'
                    cls.__disable_union_operator_for_forward_ref(
                        data_type,
                        use_typing_generics=quotes_in_typing_generics
                        and (model.WRITES_QUOTED_NAMES_IN_TYPING_GENERICS or name in hidden_names),
                    )
                if defers_recursive_containers and model.DEFERS_RECURSIVE_TYPE_HINT:
                    if alias_cycles is None:
                        alias_cycles = cls.__deferrable_alias_cycles(models, model_index)
                    cls.__defer_recursive_containers(
                        field,
                        model,
                        alias_cycles=alias_cycles,
                        model_index=model_index,
                        index=i,
                        generation_store=generation_store,
                    )
                has_aliased_forward_ref = True

            if has_aliased_forward_ref:
                model.has_forward_reference = model.has_forward_reference or process_all_fields
                _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    @property
    def _result_modules_postprocessor(self) -> Callable[..., dict[tuple[str, ...], Result]]:
        """Resolve the legacy postprocessor dynamically for each render."""
        return self.__postprocess_result_modules

    @classmethod
    def __postprocess_result_modules(
        cls, results: dict[tuple[str, ...], Result], *, empty_init: bool = False
    ) -> dict[tuple[str, ...], Result]:

        results = {_expand_result_module_path(k): v for k, v in results.items()}

        init_result = Result(body="") if empty_init else next(v for k, v in results.items() if k[-1] == "__init__.py")
        folders = {t[:-1] if t[-1].endswith(".py") else t for t in results}
        for folder in folders:
            for i in range(len(folder)):
                subfolder = folder[: i + 1]
                init_file = (*subfolder, "__init__.py")
                results.setdefault(init_file, init_result)
        return results

    def __change_imported_model_name(
        self,
        models: list[DataModel],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
    ) -> bool:
        """Rename local models shadowing imports and report whether a rename occurred."""
        imported_names = {
            imports.alias[from_][i] if i in imports.alias[from_] and i != imports.alias[from_][i] else i
            for from_, import_ in imports.items()
            for i in import_
        }
        renamed = False
        for model in models:
            if model.class_name not in imported_names:  # pragma: no cover
                continue
            if model.class_name == IMPORT_LITERAL.import_ and self.__alias_reused_variant_literal(models, imports):
                continue

            self.generation_store.rename_model(  # pragma: no cover
                model,
                reference_name=scoped_model_resolver.add(
                    path=get_special_path("imported_name", model.path.split("/")),
                    original_name=model.reference.name,
                    unique=True,
                    class_name=True,
                ).name,
            )
            renamed = True
        return renamed

    def __alias_reused_variant_literal(self, models: list[DataModel], imports: Imports) -> bool:
        """Import ``typing.Literal`` under an alias when only the tags of unions with reused variants need it.

        Such unions were left untagged before, so a model named ``Literal`` kept its name, which importers rely on.
        The alias takes no name that the module binds or its class decorators name, additional imports included.
        """
        tag_fields = self._reused_variant_tag_fields
        fields = [field for model in models for field in model.fields]
        annotated = [field for field in fields if tag_fields.get(id(field)) is field]
        bindings = {
            (from_, name): imports.get_effective_name(from_, name) for from_, names in imports.items() for name in names
        }
        if (
            not annotated
            or [key for key, binding in bindings.items() if binding == IMPORT_LITERAL.import_]
            != [(IMPORT_LITERAL.from_, IMPORT_LITERAL.import_)]
            or any(IMPORT_LITERAL in field.imports for field in fields if tag_fields.get(id(field)) is not field)
        ):
            return False
        reserved_names = {
            *(model.class_name for model in models),
            *(field.name for field in fields if field.name is not None),
            *bindings.values(),
            *(self.imports.get_effective_name(from_, name) for from_, names in self.imports.items() for name in names),
            *(
                name
                for model in models
                for decorator in model.decorators
                for name in _string_or_identifier_pattern(decorator, self.target_python_version).findall(decorator)
                if name
            ),
        }
        aliased_import = Import(
            from_=IMPORT_LITERAL.from_,
            import_=IMPORT_LITERAL.import_,
            alias=_reserve_import_alias(IMPORT_LITERAL.import_, reserved_names),
        )
        self._import_ledger.apply_alias(imports, aliased_import, _REUSED_VARIANT_LITERAL)
        for field in annotated:
            for data_type in field.data_type.all_data_types:
                data_type._set_literal_import(aliased_import)  # noqa: SLF001
            field.invalidate_semantic_caches()
        return True

    def _register_runtime_expression(self) -> None:
        """Mark a parser-owned expression created after the initial import scan."""
        register_runtime_expression(self)

    def __apply_generic_base_class(  # noqa: PLR0912, PLR0914, PLR0915
        self,
        processed_models: Sequence[ModuleContext],
    ) -> None:
        if not self.use_generic_base_class or not self.generic_base_class_config:
            return

        all_target_models: set[DataModel] = set()
        modules_with_targets: list[tuple[tuple[str, ...], list[DataModel], list[DataModel], Imports]] = []

        for module, _mod_key, models, _init, imports, _scoped_model_resolver in processed_models:
            if not models:  # pragma: no cover
                continue

            target_models = [m for m in models if m.SUPPORTS_GENERIC_BASE_CLASS and not self._is_root_model(m)]

            if target_models:
                modules_with_targets.append((module, models, target_models, imports))
                all_target_models.update(target_models)

        if not modules_with_targets:
            return

        root_modules: list[tuple[tuple[str, ...], list[DataModel], list[DataModel], Imports]] = []
        for module_entry in modules_with_targets:
            _module, _models, target_models, _imports = module_entry
            has_root_model = False
            for model in target_models:
                parent_refs = [bc.reference for bc in model.base_classes if bc.reference]
                has_target_model_parent = any(ref.source in all_target_models for ref in parent_refs)
                if not has_target_model_parent:
                    has_root_model = True
                    break
            if has_root_model:
                root_modules.append(module_entry)

        if not root_modules:  # pragma: no cover
            root_modules = [modules_with_targets[0]]

        first_root_module, first_root_models, first_root_target_models, _first_root_imports = root_modules[0]
        first_root_file_path = first_root_target_models[0].file_path if first_root_target_models else None

        base_class_ref = Reference(path=GENERIC_BASE_CLASS_PATH, name=GENERIC_BASE_CLASS_NAME)

        base_class_model = self.data_model_type.create_base_class_model(
            config=self.generic_base_class_config,
            reference=base_class_ref,
            custom_template_dir=self.custom_template_dir,
            keyword_only=self.keyword_only,
            treat_dot_as_module=self.treat_dot_as_module,
        )

        if base_class_model is None:
            return

        base_class_model.file_path = first_root_file_path
        self._generic_base_model = base_class_model
        first_root_models.insert(0, base_class_model)

        base_class_dt = BaseClassDataType(type=base_class_ref.name, reference=base_class_ref)

        original_base_class = self.data_model_type.BASE_CLASS
        original_import = Import.from_full_path(original_base_class) if original_base_class else None
        first_root_module_name = ".".join(first_root_module[:-1]) if first_root_module else ""
        for module, models, target_models, imports in modules_with_targets:
            current_module_name = ".".join(module[:-1]) if module else ""
            is_first_root = module == first_root_module
            can_retain_cache = _can_retain_model_imports_cache(
                models,
                configured_types_are_builtin=self._configured_generation_types_are_builtin,
            )
            for model in target_models:
                if original_import:  # pragma: no branch
                    additional_imports = model._additional_imports  # noqa: SLF001
                    if can_retain_cache:
                        if original_import in additional_imports:  # pragma: no branch
                            model._additional_imports = [  # noqa: SLF001
                                i for i in additional_imports if i != original_import
                            ]
                            model.clear_imports_cache()
                    else:
                        model._additional_imports = [  # noqa: SLF001
                            i for i in additional_imports if i != original_import
                        ]
                parent_refs = [bc.reference for bc in model.base_classes if bc.reference]
                has_target_model_parent = any(ref.source in all_target_models for ref in parent_refs)
                if has_target_model_parent:
                    pass
                elif parent_refs:  # pragma: no cover
                    self.generation_store.set_base_classes(model, [base_class_dt, *model.base_classes])
                else:
                    self.generation_store.set_base_classes(model, [base_class_dt])
            if not is_first_root and original_import:
                self._import_ledger.remove(imports, original_import, _GENERIC_BASE_CLASS)
                from_ = relative(current_module_name, first_root_module_name)[0]
                from_ = (
                    f"{from_}{first_root_module[-1].replace('.py', '')}"
                    if from_.endswith(".")
                    else f"{from_}.{first_root_module[-1].replace('.py', '')}"
                )
                self._import_ledger.append(
                    imports, Import(from_=from_, import_=base_class_ref.name), _GENERIC_BASE_CLASS
                )

    @classmethod
    def _collect_exports_for_init(
        cls,
        module: tuple[str, ...],
        processed_models: Sequence[ModuleContext],
        scope: AllExportsScope,
    ) -> list[tuple[str, tuple[str, ...], str]]:
        """Collect exports for __init__.py based on scope."""
        exports: list[tuple[str, tuple[str, ...], str]] = []
        normalized_module = tuple(part.replace("-", "_") for part in module)
        base = normalized_module[:-1] if normalized_module[-1] == "__init__.py" else normalized_module
        base_len = len(base)

        for proc_module, _, proc_models, _, _, _ in processed_models:
            normalized_proc_module = tuple(part.replace("-", "_") for part in proc_module)
            if not proc_models or normalized_proc_module == normalized_module:
                continue
            last = normalized_proc_module[-1]
            prefix = normalized_proc_module[:-1] if last == "__init__.py" else (*normalized_proc_module[:-1], last[:-3])
            if prefix[:base_len] != base or (depth := len(prefix) - base_len) < 1:
                continue
            if scope == AllExportsScope.Children and depth != 1:
                continue
            rel = prefix[base_len:]
            exports.extend(
                (ref.short_name, rel, ".".join(rel))
                for m in proc_models
                if (ref := m.reference) and not ref.short_name.startswith("_")
            )
        return exports

    @classmethod
    def _resolve_export_collisions(
        cls,
        exports: list[tuple[str, tuple[str, ...], str]],
        strategy: AllExportsCollisionStrategy | None,
        reserved: set[str] | None = None,
        target_python_version: PythonVersion | None = None,
    ) -> dict[str, list[tuple[str, tuple[str, ...], str]]]:
        """Resolve name collisions in exports based on strategy, titling module names as the target does."""
        reserved = reserved or set()
        by_name: dict[str, list[tuple[str, tuple[str, ...], str]]] = {}
        for item in exports:
            by_name.setdefault(item[0], []).append(item)

        if not (colliding := {n for n, items in by_name.items() if len(items) > 1 or n in reserved}):
            return dict(by_name)
        if (effective := strategy or AllExportsCollisionStrategy.Error) == AllExportsCollisionStrategy.Error:
            cls._raise_collision_error(by_name, colliding)

        used: set[str] = {n for n in by_name if n not in colliding} | reserved
        result = {n: items for n, items in by_name.items() if n not in colliding}

        for name in sorted(colliding):
            for item in sorted(by_name[name], key=lambda x: len(x[1])):
                new_name = cls._make_prefixed_name(
                    item[0],
                    item[1],
                    used,
                    minimal=effective == AllExportsCollisionStrategy.MinimalPrefix,
                    target_python_version=target_python_version,
                )
                if new_name in reserved:
                    msg = (
                        f"Cannot resolve collision: '{new_name}' conflicts with __init__.py model. "
                        "Please rename one of the models."
                    )
                    raise Error(msg)
                result[new_name] = [item]
                used.add(new_name)
        return result

    @classmethod
    def _raise_collision_error(
        cls,
        by_name: dict[str, list[tuple[str, tuple[str, ...], str]]],
        colliding: set[str],
    ) -> None:
        """Raise an error with collision details."""
        details = []
        for n in sorted(colliding):
            if len(items := by_name[n]) > 1:
                details.append(f"  '{n}' is defined in: {', '.join(f'.{s}' for _, _, s in items)}")
            else:
                details.append(f"  '{n}' conflicts with a model in __init__.py")
        raise Error(
            "Name collision detected with --all-exports-scope:\n"
            + "\n".join(details)
            + "\n\nUse --all-exports-collision-strategy minimal-prefix or full-prefix to prefix the colliding names "
            "with their module names."
        )

    @staticmethod
    def _make_prefixed_name(
        name: str,
        path: tuple[str, ...],
        used: set[str],
        *,
        minimal: bool,
        target_python_version: PythonVersion | None = None,
    ) -> str:
        """Generate a prefixed name, using minimal or full prefix."""
        prefixes = [title(part, target_python_version).replace("_", "") for part in path]
        if minimal:
            for depth in range(1, len(path) + 1):
                if (candidate := "".join(prefixes[-depth:]) + name) not in used:
                    return candidate
        return "".join(prefixes) + name

    @classmethod
    def _build_all_exports_code(
        cls,
        resolved: dict[str, list[tuple[str, tuple[str, ...], str]]],
    ) -> Imports:
        """Build import statements from resolved exports."""
        export_imports = Imports()
        for export_name, items in resolved.items():
            for orig, _, short in items:
                export_imports.append(
                    Import(from_=f".{short}", import_=orig, alias=export_name if export_name != orig else None)
                )
        return export_imports

    @classmethod
    def _collect_used_names_from_models(
        cls,
        models: list[DataModel],
        model_imports: Mapping[DataModel, tuple[Import, ...]] | None = None,
    ) -> set[str]:
        """Collect identifiers referenced by models before rendering."""
        names: set[str] = set()

        def add(name: str | None) -> None:
            if not name:
                return
            names.add(name.split(".")[0])

        def collect_data_type_names(data_type: DataType) -> None:
            if data_type.alias:
                add(data_type.alias)
            elif data_type.python_type:
                from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
                    iter_python_type_expr_names,
                    iter_python_type_expr_qualified_names,
                )

                expression = data_type.python_type.expression
                for name in iter_python_type_expr_names(expression):
                    add(name)
                for qualified_name in iter_python_type_expr_qualified_names(expression):
                    add(qualified_name)
            else:
                add(data_type.type)
                for enum_class, _ in data_type.enum_member_literals:
                    add(enum_class)
            if data_type.reference:
                add(data_type.reference.short_name)

        for model in models:
            add(model.class_name)
            add(model.duplicate_class_name)
            for base in model.base_classes:
                add(base.type_hint)
            imports = model_imports[model] if model_imports is not None else model.imports
            for import_ in imports:
                add(import_.alias or import_.import_.split(".")[-1])
            for field in model.fields:
                if field.is_class_var:
                    continue
                add(field.name)
                add(field.alias)
                field.data_type.walk(collect_data_type_names)
        return names

    def __generate_forwarder_content(  # noqa: PLR6301
        self,
        original_module: tuple[str, ...],
        internal_module: tuple[str, ...],
        class_mappings: list[tuple[str, str]],
        *,
        is_init: bool = False,
    ) -> str:
        """Generate forwarder module content that re-exports classes from _internal.

        Args:
            original_module: The original module tuple (e.g., ("issuing",) or ())
            internal_module: The _internal module tuple (e.g., ("_internal",))
            class_mappings: List of (original_name, new_name) tuples, sorted by original_name
            is_init: True if this is a package __init__.py, False for regular .py files

        Returns:
            The forwarder module content as a string
        """
        original_str = ".".join(original_module)
        internal_str = ".".join(internal_module)
        from_dots, module_name = relative(original_str, internal_str, reference_is_module=True, current_is_init=is_init)
        relative_import = f"{from_dots}{module_name}"

        imports = Imports()
        for original_name, new_name in class_mappings:
            if original_name == new_name:
                imports.append(Import(from_=relative_import, import_=new_name))
            else:
                imports.append(Import(from_=relative_import, import_=new_name, alias=original_name))

        return f"{imports.dump()}\n\n{imports.dump_all()}\n"

    def __compute_internal_module_path(  # noqa: PLR6301
        self,
        scc_modules: set[tuple[str, ...]],
        existing_modules: set[tuple[str, ...]],
        *,
        base_name: str = "_internal",
    ) -> tuple[str, ...]:
        """Compute the internal module path for an SCC."""
        directories = [get_module_directory(m) for m in sorted(scc_modules)]

        if not directories or any(not d for d in directories):
            prefix: tuple[str, ...] = ()
        else:
            path_strings = ["/".join(d) for d in directories]
            common = os.path.commonpath(path_strings)
            prefix = tuple(common.split("/")) if common else ()

        base_module = (base_name,) if not prefix else (*prefix, base_name)

        if base_module in existing_modules:
            counter = 1
            while True:
                candidate = (*prefix, f"{base_name}_{counter}") if prefix else (f"{base_name}_{counter}",)
                if candidate not in existing_modules:
                    return candidate
                counter += 1

        return base_module

    def __collect_scc_models(  # noqa: PLR6301
        self,
        scc: set[tuple[str, ...]],
        result_modules: dict[tuple[str, ...], list[DataModel]],
    ) -> tuple[list[DataModel], dict[int, tuple[str, ...]]]:
        """Collect all models from SCC modules.

        Returns:
            - List of all models in the SCC
            - Mapping from model id to its original module
        """
        all_models: list[DataModel] = []
        model_to_module: dict[int, tuple[str, ...]] = {}
        for scc_module in sorted(scc):
            for model in result_modules[scc_module]:
                all_models.append(model)
                model_to_module[id(model)] = scc_module
        return all_models, model_to_module

    def __rename_and_relocate_scc_models(
        self,
        all_scc_models: list[DataModel],
        model_to_original_module: dict[int, tuple[str, ...]],
        internal_module: tuple[str, ...],
        internal_path: Path,
    ) -> defaultdict[tuple[str, ...], list[tuple[str, str]]]:
        """Rename duplicate classes and relocate models to internal module.

        Class names follow ``_merged_class_names``; forwarders re-export each model under its original name.

        Returns:
            Mapping from original module to list of (original_name, new_name) tuples.
        """
        internal_module_str = ".".join(internal_module)
        module_class_mappings: defaultdict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
        original_class_names = [model.class_name for model in all_scc_models]
        new_class_names = _merged_class_names(original_class_names)

        with self.generation_store.defer_refresh():
            for model, original_class_name, new_class_name in zip(
                all_scc_models, original_class_names, new_class_names, strict=True
            ):
                original_module = model_to_original_module[id(model)]
                new_path = f"{internal_module_str}.{new_class_name}"
                self.generation_store.rename_model(
                    model,
                    reference_name=new_class_name,
                    new_path=new_path,
                    new_file_path=internal_path,
                )

                module_class_mappings[original_module].append((original_class_name, new_class_name))

        return module_class_mappings

    def __module_dependency_cycles(
        self,
        module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
    ) -> list[set[tuple[str, ...]]]:
        """Find the modules that import each other, from the snapshot of the references between their models."""
        snapshot = GraphSnapshot.MODULE_DEPENDENCIES
        keyed = keyed_models((model for _, models in module_models_list for model in models), snapshot.value)
        module_of: dict[ModelKey, tuple[str, ...]] = {}
        for module, models in module_models_list:
            for model in models:
                module_of[model_key_of(model.reference)] = module
        index = self.generation_store.index
        graph = reference_graph(
            snapshot,
            keyed.models,
            {
                EdgeKind.REFERENCE: {
                    key: index.referenced_keys_for_model(model, keyed) for key, model in keyed.models.items()
                }
            },
        )

        def old() -> list[set[tuple[str, ...]]]:
            silent = unchecked(keyed)
            module_graph: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
            for module, models in module_models_list:
                module_graph[module] = set()
                for model in models:
                    for reference_key in index.referenced_keys_for_model(model, silent):
                        if (target_module := module_of.get(reference_key)) is not None and target_module != module:
                            module_graph[module].add(target_module)
            return find_circular_sccs(module_graph)

        return graph_answer(snapshot, find_circular_sccs(graph.quotient(module_of, (EdgeKind.REFERENCE,))), old)

    def __resolve_circular_imports(
        self,
        module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
    ) -> tuple[
        list[tuple[tuple[str, ...], list[DataModel]]],
        set[tuple[str, ...]],
        dict[tuple[str, ...], tuple[tuple[str, ...], list[tuple[str, str]]]],
    ]:
        """Resolve circular imports by merging all SCCs into _internal.py modules.

        Uses Tarjan's algorithm to find strongly connected components (SCCs) in the
        module dependency graph. All modules in each SCC are merged into a single
        _internal.py module to break import cycles. Original modules become thin
        forwarders that re-export their classes from _internal.

        Returns:
            - Updated module_models_list with models moved to _internal modules
            - Set of _internal modules created
            - Forwarder map: original_module -> (internal_module, [(original_name, new_name)])
        """
        circular_sccs = self.__module_dependency_cycles(module_models_list)

        forwarder_map: dict[tuple[str, ...], tuple[tuple[str, ...], list[tuple[str, str]]]] = {}

        if not circular_sccs:
            return module_models_list, set(), forwarder_map

        # All circular SCCs are problematic and should be merged into _internal.py
        # to break the import cycles.
        problematic_sccs = circular_sccs

        existing_modules = {module for module, _ in module_models_list}
        internal_modules_created: set[tuple[str, ...]] = set()

        result_modules: dict[tuple[str, ...], list[DataModel]] = {
            module: list(models) for module, models in module_models_list
        }

        for scc in problematic_sccs:
            internal_module = self.__compute_internal_module_path(scc, existing_modules | internal_modules_created)
            internal_modules_created.add(internal_module)
            internal_path = Path("/".join(internal_module))

            all_scc_models, model_to_original_module = self.__collect_scc_models(scc, result_modules)
            module_class_mappings = self.__rename_and_relocate_scc_models(
                all_scc_models, model_to_original_module, internal_module, internal_path
            )
            all_scc_models = _sort_models_by_class_body_references(
                all_scc_models,
                self.pydantic_v2_root_model_type,
                _runtime_validation_reference_keys(all_scc_models, same_module=False),
            )

            for scc_module in scc:
                if scc_module in result_modules:  # pragma: no branch
                    result_modules[scc_module] = []
                if scc_module in module_class_mappings:  # pragma: no branch
                    sorted_mappings = sorted(module_class_mappings[scc_module], key=operator.itemgetter(0))
                    forwarder_map[scc_module] = (internal_module, sorted_mappings)
            result_modules[internal_module] = all_scc_models

        new_module_models: list[tuple[tuple[str, ...], list[DataModel]]] = [
            (internal_module, result_modules[internal_module])
            for internal_module in sorted(internal_modules_created)
            if internal_module in result_modules  # pragma: no branch
        ]

        for module, _ in module_models_list:
            if module not in internal_modules_created:  # pragma: no branch
                new_module_models.append((module, result_modules.get(module, [])))

        return new_module_models, internal_modules_created, forwarder_map

    def __get_resolve_reference_action_parts(
        self,
        models: list[DataModel],
        require_update: Collection[ModelKey],
        *,
        use_deferred_annotations: bool,
    ) -> list[str]:
        """Return the trailing rebuild/update calls for the given module's models."""
        if self.dump_resolve_reference_action is None:
            return []

        keyed = keyed_models(models, "reference update actions")
        required_keys_in_module = {key for key in keyed.models if key in require_update}

        if (
            use_deferred_annotations
            and required_keys_in_module
            and get_resolve_reference_action_capabilities(self.dump_resolve_reference_action).filter_forward_references
        ):
            module_positions = {m.reference.short_name: i for i, m in enumerate(models) if m.reference}
            module_model_names = set(module_positions)

            forward_needed: set[ModelKey] = set()
            for model in models:
                if (key := model_key_of(model.reference)) not in required_keys_in_module:
                    continue
                name = model.reference.short_name
                pos = module_positions[name]
                refs = {
                    t.reference.short_name
                    for f in model.fields
                    for t in f.data_type.all_data_types
                    if t.reference and t.reference.short_name in module_model_names
                }
                if name in refs or any(module_positions.get(r, -1) > pos for r in refs):
                    forward_needed.add(key)

            # Propagate requirement through inheritance.
            changed = True
            required_filtered = set(forward_needed)
            while changed:
                changed = False
                for model in models:
                    if (key := model_key_of(model.reference)) in required_filtered:
                        continue
                    base_keys = {keyed.key_of(b.reference) for b in model.base_classes if b.reference}
                    if base_keys & required_filtered:
                        required_filtered.add(key)
                        changed = True

            required_keys_in_module = required_filtered

        return [
            "\n",
            self.dump_resolve_reference_action(
                m.reference.short_name for m in models if model_key_of(m.reference) in required_keys_in_module
            ),
        ]

    def _uses_deferred_annotations(
        self,
        with_import: bool | None,  # noqa: FBT001
        disable_future_imports: bool,  # noqa: FBT001
    ) -> bool:
        """Return whether generated annotations use deferred evaluation."""
        if (
            disable_future_imports or not with_import
        ) and self.data_model_type.REQUIRES_EXPLICIT_DEFERRED_ANNOTATIONS_FOR_FORWARD_REFS:
            return False
        return bool(
            python_target_meets("python.deferred_annotations", self.target_python_version.version_key)
            or (with_import and not disable_future_imports)
        )

    def _has_forward_references(self, models: list[DataModel]) -> bool:  # noqa: PLR6301
        """Return whether a module contains a self or forward model reference.

        A type hidden by a field of its class counts as one: the library has to resolve it from a string.
        This remains an instance method because ``snooper_to_methods`` does not
        preserve staticmethod descriptors on parser subclasses.
        """
        positions = _ModelPositions(models, "forward references")
        for index, model in enumerate(models):
            hidden_names = _hidden_type_names(model)
            for field in model.fields:
                for data_type in field.data_type.all_data_types:
                    if (
                        (reference := data_type.reference)
                        and (reference_index := positions.get(reference)) is not None
                        and (reference_index >= index or (reference.short_name in hidden_names and not data_type.alias))
                    ):
                        return True
        return False

    def _requires_explicit_deferred_annotations(self, models: list[DataModel], config: ParseConfig) -> bool:
        """Return whether a module needs the future annotations import on a target with native deferred annotations.

        A type hidden by a field of its class needs it too: ``dataclasses`` evaluates a native annotation while the
        class still binds the field's name, and ``typing`` resolves a string from the module first. So does an enum
        member literal whose enum the module defines later, which a native annotation cannot hold.
        """
        if not (config.with_import and config.use_deferred_annotations):
            return False
        if self.data_model_type.REQUIRES_EXPLICIT_DEFERRED_ANNOTATIONS_FOR_FORWARD_REFS:
            return self._has_forward_references(models)
        if self._quotes_hidden_types:
            return False
        if any(model.resolves_hidden_types_from_strings() and _hidden_type_names(model) for model in models):
            return True
        positions = _ModelPositions(models, "forward enum member literals")
        return any(
            _forward_enum_member_literals(field, positions, index)
            for index, model in enumerate(models)
            for field in model.fields
        )

    def _get_module_future_imports(
        self,
        ctx: ModuleContext,
        config: ParseConfig,
        future_imports_str: str,
    ) -> str:
        """Return future imports required by a single generated module."""
        if not (
            python_target_meets("python.deferred_annotations", self.target_python_version.version_key)
            and self._requires_explicit_deferred_annotations(ctx.models, config)
        ):
            return future_imports_str
        self._import_ledger.append(ctx.imports, IMPORT_ANNOTATIONS, _DEFERRED_ANNOTATIONS)
        future_imports = self._import_ledger.extract_future(ctx.imports, _FUTURE_IMPORTS)
        return "\n".join(import_ for import_ in (future_imports_str, str(future_imports)) if import_)

    def _set_typed_extra_annotation_mode(self, *, use_deferred_annotations: bool) -> None:
        """Let the selected output configure its annotation representation."""
        self.data_model_type.configure_annotations(
            self.extra_template_data,
            target_python_version=self.target_python_version,
            use_deferred_annotations=use_deferred_annotations,
        )

    def _prepare_parse_config(
        self,
        with_import: bool | None,  # noqa: FBT001
        disable_future_imports: bool,  # noqa: FBT001
        all_exports_scope: AllExportsScope | None,
        all_exports_collision_strategy: AllExportsCollisionStrategy | None,
        module_split_mode: ModuleSplitMode | None,
    ) -> ParseConfig:
        """Prepare configuration for the parse operation."""
        use_deferred_annotations = self._uses_deferred_annotations(with_import, disable_future_imports)
        self._quotes_hidden_types = disable_future_imports or not with_import

        if (
            with_import
            and not disable_future_imports
            and not python_target_meets("python.deferred_annotations", self.target_python_version.version_key)
        ):
            self._import_ledger.append(self.imports, IMPORT_ANNOTATIONS, _DEFERRED_ANNOTATIONS)

        return ParseConfig(
            with_import=bool(with_import),
            use_deferred_annotations=use_deferred_annotations,
            code_formatter=None,
            module_split_mode=module_split_mode,
            all_exports_scope=all_exports_scope,
            all_exports_collision_strategy=all_exports_collision_strategy,
        )

    def _build_code_formatter(
        self,
        settings_path: Path | None,
        *,
        is_multi_module_output: bool,
    ) -> CodeFormatter:
        from datamodel_code_generator.format import CodeFormatter, resolve_use_type_checking_imports  # noqa: PLC0415

        effective_use_type_checking_imports = resolve_use_type_checking_imports(
            self.use_type_checking_imports,
            is_multi_module_output=is_multi_module_output,
            formatters=self.formatters,
            requires_runtime_imports_with_ruff_check=self.data_model_type.REQUIRES_RUNTIME_IMPORTS_WITH_RUFF_CHECK,
        )
        return CodeFormatter(
            self.target_python_version,
            settings_path,
            self.wrap_string_literal,
            skip_string_normalization=not self.use_double_quotes,
            known_third_party=self.known_third_party,
            custom_formatters=self.custom_formatter,
            custom_formatters_kwargs=self.custom_formatters_kwargs,
            encoding=self.encoding,
            formatters=self.formatters,
            builtin_format_line_length=self.builtin_format_line_length,
            use_type_checking_imports=effective_use_type_checking_imports,
            defer_formatting=self.defer_formatting,
            formatter_cwd=self.run_context.formatter_cwd,
        )

    def _find_invalid_inferred_modules(self, sorted_data_models: SortedDataModels) -> set[ModulePath]:
        """Return non-canonical module paths created by automatic dotted-name inference."""
        invalid_modules: set[ModulePath] = set()
        for model in sorted_data_models.values():
            original_name = model.reference.original_name
            if model.file_path is not None or "." not in original_name:
                continue
            module = tuple(model.module_path)
            if (
                module
                and split_module_name(
                    original_name,
                    treat_dot_as_module=None,
                    strict_dotted_module_names=True,
                    target_python_version=self.target_python_version,
                )
                is None
            ):
                invalid_modules.add(module)
        return invalid_modules

    @staticmethod
    def _stdout_model_fingerprint(model: DataModel) -> HashableComparable:
        """Return the final rendered definition without using the pre-processing dedup cache."""
        return stdout_fingerprint(model.render(class_name=model.class_name), model.imports)

    @staticmethod
    def _find_shadowed_stdout_models(
        contexts: list[ModuleContext],
        results: dict[ModulePath, Result],
        binding_context: StdoutBindingContext,
    ) -> set[DataModel]:
        """Return generated models hidden by a later import in concatenation order."""
        context_by_module = {ctx.module: ctx for ctx in contexts}
        generated_names = binding_context.models_by_name.keys()
        last_model_binding: dict[str, DataModel | None] = {}
        normalized_to_module = {
            _normalize_result_module_path(
                module,
                treat_dot_as_module=binding_context.treat_dot_as_module,
            ): module
            for module in results
        }
        for normalized_module in sorted(normalized_to_module):
            module = normalized_to_module[normalized_module]
            if (ctx := context_by_module.get(module)) is None:
                continue
            for name in chain(
                _iter_import_bindings(binding_context.common_imports),
                _iter_import_bindings(ctx.imports),
            ):
                if name in generated_names:
                    last_model_binding[name] = None
            for model in ctx.models:
                last_model_binding[model.class_name] = model

        return {
            model
            for name, binding in last_model_binding.items()
            if binding is None
            for _, model in binding_context.models_by_name[name]
        }

    @staticmethod
    def _find_stdout_defect_models(
        contexts: list[ModuleContext],
        results: dict[ModulePath, Result],
        common_imports: Imports,
        *,
        with_import: bool,
        treat_dot_as_module: bool | None,
    ) -> set[DataModel]:
        """Return final models involved in an unusable concatenated stdout result."""
        nonempty_contexts = [ctx for ctx in contexts if ctx.models]
        live_models = [model for ctx in nonempty_contexts for model in ctx.models]
        defect_models: set[DataModel] = set()
        models_by_name: defaultdict[str, list[tuple[ModulePath, DataModel]]] = defaultdict(list)
        for ctx in nonempty_contexts:
            for model in ctx.models:
                models_by_name[model.class_name].append((ctx.module_key, model))

        if len(nonempty_contexts) > 1:
            for bindings in models_by_name.values():
                if len({module for module, _ in bindings}) == 1:
                    continue
                if len({Parser._stdout_model_fingerprint(model) for _, model in bindings}) > 1:
                    defect_models.update(model for _, model in bindings)

            # Concatenated modules share one namespace. Reject a result when a
            # later import hides a generated model binding.
            if with_import:
                defect_models.update(
                    Parser._find_shadowed_stdout_models(
                        nonempty_contexts,
                        results,
                        StdoutBindingContext(
                            common_imports,
                            models_by_name,
                            treat_dot_as_module,
                        ),
                    )
                )

        result_bodies = [result.body for result in results.values() if result.body]
        if (
            any(_TOP_LEVEL_RELATIVE_IMPORT_PATTERN.search(body) for body in result_bodies)
            or sum(bool(_TOP_LEVEL_FUTURE_IMPORT_PATTERN.search(body)) for body in result_bodies) > 1
        ):
            defect_models.update(live_models)
        return defect_models

    def _get_source_data_fingerprint(self) -> bytes | None:
        """Hash parsed root and reference data without allocating one serialized copy."""
        from contextlib import suppress  # noqa: PLC0415
        from hashlib import sha256  # noqa: PLC0415
        from pickle import Pickler  # noqa: PLC0415, S403

        digest = sha256()

        class DigestWriter:
            __slots__ = ()

            @staticmethod
            def write(data: bytes, /) -> int:
                digest.update(data)
                return len(data)

        source_data = (
            getattr(self, "raw_obj", None),
            sorted(getattr(self, "remote_object_cache", {}).items()),
            sorted((getattr(self, "_python_type_expressions", None) or {}).items()),
        )
        # Parsed YAML may contain mixed or non-JSON scalar mapping keys. Pickle preserves
        # their types and streams directly into the digest; unsupported extension objects
        # simply disable the optional retry so a completed legacy result is never lost.
        with suppress(Exception):
            Pickler(DigestWriter(), protocol=5).dump(source_data)
            return digest.digest()
        return None

    def _inspect_invalid_dotted_stdout(
        self,
        contexts: list[ModuleContext],
        sorted_data_models: SortedDataModels,
        config: ParseConfig,
        results: dict[ModulePath, Result],
    ) -> None:
        """Record a narrow repair plan after every module transformation has completed."""
        if not (self.repair_invalid_dotted_stdout or self.forced_invalid_dotted_stdout_repair_modules):
            return
        invalid_modules = (
            set()
            if self.forced_invalid_dotted_stdout_repair_modules
            else self._find_invalid_inferred_modules(sorted_data_models)
        )
        if self.repair_invalid_dotted_stdout and not invalid_modules:
            return

        defect_models = Parser._find_stdout_defect_models(
            contexts,
            results,
            self.imports,
            with_import=config.with_import,
            treat_dot_as_module=self.treat_dot_as_module,
        )
        if (
            getattr(self, "openapi_include_info_version", False)
            and getattr(self, "openapi_info_version", None) is not None
            and any(
                _normalize_result_module_path(module, treat_dot_as_module=self.treat_dot_as_module)
                > _normalize_result_module_path(("__init__.py",), treat_dot_as_module=self.treat_dot_as_module)
                and _TOP_LEVEL_FUTURE_IMPORT_PATTERN.search(result.body)
                for module, result in results.items()
            )
        ):
            defect_models.update(model for ctx in contexts for model in ctx.models)
        self.stdout_result_usable = not defect_models
        if self.forced_invalid_dotted_stdout_repair_modules:
            self.generated_model_inventory = self._model_inventory([model for ctx in contexts for model in ctx.models])
            self.source_data_fingerprint = self._get_source_data_fingerprint()
            return
        if not defect_models or config.module_split_mode is not None:
            return

        repair_modules: set[ModulePath] = set()
        for model in defect_models:
            module = tuple(model.module_path)
            matching_module: ModulePath = ()
            for invalid_module in invalid_modules:
                if len(invalid_module) > len(matching_module) and module[: len(invalid_module)] == invalid_module:
                    matching_module = invalid_module
            if matching_module:
                repair_modules.add(matching_module)

        live_models = [model for ctx in contexts for model in ctx.models]
        if not repair_modules or any(model.file_path is not None for model in live_models):
            return
        if (source_fingerprint := self._get_source_data_fingerprint()) is None:
            return
        # The retry flattens every stdout model. Pass every non-canonical inferred
        # module so canonical modules reserve their existing names before any of them.
        self.invalid_dotted_stdout_repair_modules = tuple(sorted(invalid_modules))
        self.generated_model_inventory = self._model_inventory(live_models)
        self.source_data_fingerprint = source_fingerprint

    def _model_inventory(self, models: list[DataModel]) -> tuple[str, ...]:
        """Return the paths of the models other than typed map values, which a retry with untyped values omits."""
        typed_map_values = self._typed_map_value_reference_ids
        return tuple(sorted(model.path for model in models if id(model.reference) not in typed_map_values))

    def _flatten_invalid_modules(
        self,
        live_models: list[DataModel],
        affected_models: list[DataModel],
    ) -> bool:
        """Flatten selected inferred modules while preserving all unaffected names."""
        resolver = self.model_resolver
        affected_model_ids = {id(model) for model in affected_models}
        unavailable_names = resolver.exclude_names | {
            model.class_name for model in live_models if id(model) not in affected_model_ids
        }
        unavailable_names.update(import_.alias or import_.import_ for model in live_models for import_ in model.imports)
        scoped_resolver = ModelResolver(
            exclude_names=unavailable_names,
            duplicate_name_suffix="Model",
            custom_class_name_generator=lambda name: name,
            duplicate_name_suffix_map=resolver.duplicate_name_suffix_map,
            target_python_version=self.target_python_version,
        )
        new_names: dict[DataModel, str] = {}
        for index, model in enumerate(affected_models):
            new_names[model] = scoped_resolver.add(
                [str(index)],
                model.class_name,
                class_name=True,
                model_type="enum" if isinstance(model, Enum) else "model",
                preserve_class_name=True,
            ).name

        generation_index = self.generation_store.index
        models_to_clear = set(affected_models)
        for model in affected_models:
            models_to_clear.update(
                owner_model
                for fact in generation_index.data_type_facts_for_reference(model.reference)
                if (owner_model := generation_index.owner_model_for_data_type(fact.data_type)) is not None
            )

        for model, new_name in new_names.items():
            self.generation_store.rename_model(model, reference_name=new_name, clear_duplicate_name=True)
        resolver.refresh_reference_names()
        for model in models_to_clear:
            model.clear_imports_cache()
            model._dedup_key_cache.clear()  # noqa: SLF001
        return True

    def _apply_forced_invalid_dotted_stdout_repair(
        self,
        module_models: ModuleModels,
        module_split_mode: ModuleSplitMode | None,
    ) -> bool:
        """Apply the repair roots proven necessary by a completed legacy pass."""
        if module_split_mode is not None or not (affected_modules := self.forced_invalid_dotted_stdout_repair_modules):
            return False
        live_models = [model for _, models in module_models for model in models]
        originally_affected_models = [
            model
            for model in live_models
            if any(tuple(model.module_path)[: len(module)] == module for module in affected_modules)
        ]
        if not originally_affected_models or any(model.file_path is not None for model in live_models):
            return False  # pragma: no cover - first-pass inventory and source checks make this a safety fallback.
        originally_affected_ids = {id(model) for model in originally_affected_models}
        # Preserve existing root/canonical names first, then assign conflict-free flat
        # names to the invalid roots that made the completed legacy output unusable.
        unaffected_models = [model for model in live_models if id(model) not in originally_affected_ids]
        flatten_order = [model for model in unaffected_models if not model.module_path]
        flatten_order.extend(model for model in unaffected_models if model.module_path)
        flatten_order.extend(originally_affected_models)
        return self._flatten_invalid_modules(live_models, flatten_order)

    def _build_module_structure(
        self,
        sorted_data_models: SortedDataModels,
        module_split_mode: ModuleSplitMode | None,
    ) -> tuple[
        ModuleModels,
        set[ModulePath],
        ForwarderMap,
        dict[DataModel, tuple[ModulePath, list[DataModel]]],
        dict[str, str],
    ]:
        """Build module structure from sorted models.

        Reuse compares the tags of discriminated unions as their mappings give them before any model is merged,
        since merging redirects the variants of a union to models that its mapping does not name.
        """
        state = _StructureState(sorted_data_models, module_split_mode)
        self.__runner().run_steps(Parser.__STRUCTURE_BODY, self, state)
        return (
            state.module_models,
            state.internal_modules,
            state.forwarder_map,
            state.model_to_module_models,
            state.model_path_to_module_name,
        )

    def __group_models_by_module(self, state: _StructureState) -> None:
        """Group the sorted models into output modules and index them."""
        state.module_models = _group_models_by_module(
            state.sorted_data_models.values(), state.module_split_mode, self.target_python_version
        )
        state.model_to_module_models, state.model_path_to_module_name = _index_module_models(
            state.module_models, state.module_split_mode
        )

    def __reset_collapsed_reuse_state(self, _state: _StructureState) -> None:
        """Forget the models collapsed reuse removed in an earlier parse."""
        self._collapsed_reuse_models.clear()
        self._collapsed_reuse_users.clear()

    def __snapshot_union_tags(self, state: _StructureState) -> None:
        """Record the tags of each discriminated union before reuse merges any model."""
        self._reuse_union_tags = (
            {model: tags for _, models in state.module_models for model in models if (tags := _union_tags(model))}
            if self.reuse_model
            else {}
        )

    def __apply_forced_invalid_dotted_stdout_repair(self, state: _StructureState) -> None:
        """Apply the repair of invalid dotted stdout modules, and group the models again when it applied."""
        if not self._apply_forced_invalid_dotted_stdout_repair(state.module_models, state.module_split_mode):
            return
        live_model_ids = {id(model) for _, models in state.module_models for model in models}
        state.module_models = _group_models_by_module(
            (model for model in state.sorted_data_models.values() if id(model) in live_model_ids),
            state.module_split_mode,
            self.target_python_version,
        )
        state.model_to_module_models, state.model_path_to_module_name = _index_module_models(
            state.module_models, state.module_split_mode
        )

    def __apply_reuse_model_tree_scope(self, state: _StructureState) -> None:
        """Move the duplicates of tree-scope reuse into a shared module first in the module list."""
        if not (shared_module_entry := self.__reuse_model_tree_scope(state.module_models, state.module_split_mode)):
            return
        state.module_models.insert(0, shared_module_entry)
        if state.module_split_mode == ModuleSplitMode.Single:
            state.model_to_module_models, state.model_path_to_module_name = _index_module_models(
                state.module_models, state.module_split_mode
            )

    def __resolve_module_circular_imports(self, state: _StructureState) -> None:
        """Break the import cycles between modules."""
        state.module_models, state.internal_modules, state.forwarder_map = self.__resolve_circular_imports(
            state.module_models
        )
        if state.internal_modules:
            state.model_path_to_module_name.forget(
                model for module, models in state.module_models if module in state.internal_modules for model in models
            )

    def _process_single_module(  # noqa: PLR0913, PLR0917
        self,
        module_: ModulePath,
        models: list[DataModel],
        results: dict[ModulePath, Result],
        config: ParseConfig,
        internal_modules: set[ModulePath],
        model_path_to_module_name: dict[str, str],
        require_update_action_models: list[str],
        unused_models: list[DataModel],
    ) -> ModuleContext:
        """Process a single module and return its context."""
        imports = Imports(self.use_exact_imports)
        module, is_init = _resolve_module_file(module_, results)
        module_name = _module_name_from_module_path(module_)
        # Tree-scope reuse creates synthetic models without a file_path. Keep
        # their ownership in this module for relative import planning without
        # changing the file path used by generated filename headers.
        for model in models:
            _assign_module_name(model_path_to_module_name, model, module_name, keep=True)
        can_retain_cache = _can_retain_model_imports_cache(
            models,
            configured_types_are_builtin=self._configured_generation_types_are_builtin,
        )

        all_module_fields = {field.name for model in models for field in model.fields if field.name is not None}
        scoped_model_resolver = ModelResolver(
            exclude_names=all_module_fields, target_python_version=self.target_python_version
        )
        state = _ModuleState(
            is_init=is_init,
            model_list=models,
            imports=imports,
            scoped_model_resolver=scoped_model_resolver,
            all_module_fields=all_module_fields,
            can_retain_cache=can_retain_cache,
            config=config,
            internal_modules=internal_modules,
            model_path_to_module_name=model_path_to_module_name,
            require_update_action_models=require_update_action_models,
            unused_models=unused_models,
        )
        self.__runner().run_module_body(Parser.__MODULE_BODY, self, state)
        self._prepared_modules.append(_PreparedModule.of(state))
        return ModuleContext(module, module_, state.model_list, is_init, imports, scoped_model_resolver)

    def __record_decimal_alias_models(self, state: _ModuleState) -> None:
        """Remember the models of a module where a field named ``Decimal`` may shadow the type."""
        self._constrained_decimal_alias_model_ids.update(map(id, state.model_list))

    def __change_module_from_import(self, state: _ModuleState) -> None:
        """Import the referenced models of other modules into the module."""
        self.__change_from_import(
            state.model_list,
            state.imports,
            state.scoped_model_resolver,
            init=state.is_init,
            internal_modules=state.internal_modules,
            model_path_to_module_name=state.model_path_to_module_name,
        )

    def __refresh_import_cache_retention(self, state: _ModuleState) -> None:
        """Keep the imports cache of the module only while its models still allow it."""
        state.can_retain_cache = state.can_retain_cache and _can_retain_model_imports_cache(
            state.model_list,
            configured_types_are_builtin=self._configured_generation_types_are_builtin,
        )

    def __mark_unused_models_start(self, state: _ModuleState) -> None:  # noqa: PLR6301
        """Remember where the unused models this module adds will start."""
        state.unused_models_start = len(state.unused_models)

    def __collapse_module_root_models(self, state: _ModuleState) -> None:
        """Inline the root models of the module; reaching the recursion limit raises a retryable error."""
        try:
            self.__collapse_root_models(
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
                mode.violation(
                    Error, "collapsing root models reached the recursion limit instead of stopping at a cycle"
                )
            raise _CollapseRootModelsRecursionError from exc

    def __remove_module_overridden_models(self, state: _ModuleState) -> None:
        """Keep the models a model-level type override replaces out of the module, and the list before it."""
        state.model_list_before_override_removal = state.model_list
        state.model_list = self.__remove_overridden_models(state.model_list)

    def __type_module_dict_keys_by_root_types(self, state: _ModuleState) -> None:
        """Type the dict keys of the module that name root models by their root types."""
        self.__type_dict_keys_by_root_types(
            state.model_list,
            state.imports,
            state.scoped_model_resolver,
            state.model_path_to_module_name,
            can_retain_cache=state.can_retain_cache,
        )

    def __snapshot_module_unused_models(self, state: _ModuleState) -> None:  # noqa: PLR6301
        """Take the unused models the module added, as they are now."""
        if state.unused_models_start != len(state.unused_models):
            state.current_unused_models = state.unused_models[state.unused_models_start :]

    def __assign_forward_reference_fields(self, models: list[DataModel], *, can_retain_cache: bool) -> None:
        """Let each model render fields by assignment when ``Field()`` inside ``Annotated`` would be unsafe.

        A model that writes fields by assignment may reorder them.
        """
        positions = {model.path: index for index, model in enumerate(models)}
        for index, model in enumerate(models):
            if self.generation_store.use_assignment_for_forward_references(model, positions, index, refresh="deferred"):
                _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __unbind_hidden_type_fields(self, state: _PreparedModule) -> None:  # noqa: PLR6301
        """Leave fields unbound where they hide a type of their class's annotations and the model can."""
        for model in state.model_list:
            if (names := _hidden_type_names(model)) and model.unbind_fields(names):
                _clear_model_imports_cache_if_retained(model, can_retain_cache=state.can_retain_cache)

    def __update_module_type_aliases(self, state: _PreparedModule) -> None:
        """Quote forward references and defer recursive containers; without future imports a hidden type is a string."""
        self.__update_type_aliases(
            state.model_list,
            self.pydantic_v2_root_model_type,
            use_deferred_annotations=state.config.use_deferred_annotations,
            defers_recursive_containers=pydantic_target_meets(
                "pydantic.type_alias_module_namespace", self.target_pydantic_version
            ),
            generation_store=self.generation_store,
            can_retain_cache=state.can_retain_cache,
            hidden_type_names=_hidden_type_names if self._quotes_hidden_types else _no_hidden_type_names,
            quotes_forward_enum_member_literals=self._quotes_hidden_types,
            use_typing_generics=not python_target_meets(
                "python.builtin_generic_strings", self.target_python_version.version_key
            ),
        )

    def __collect_live_models(self, state: _PreparedModule) -> None:  # noqa: PLR6301
        """Take the models of the module that are not unused."""
        if not (unused_models := state.current_unused_models):
            state.live_models = state.model_list
            return
        unused_model_ids = {id(model) for model in unused_models}
        state.live_models = [model for model in state.model_list if id(model) not in unused_model_ids]

    def _finalize_structured_imports(self, contexts: list[ModuleContext]) -> None:
        """Resolve aliases introduced after generic base classes are applied."""
        has_structured_imports = self._has_bound_python_types or self._has_runtime_expressions
        if not has_structured_imports and not self._constrained_decimal_alias_model_ids:
            return
        if models := [
            model for ctx in contexts for model in ctx.models if id(model) in self._constrained_decimal_alias_model_ids
        ]:
            has_structured_imports = (
                normalize_default_value_constraints(
                    self, models, data_type_manager=self.data_type_manager, import_overrides=self._import_overrides
                )
                or has_structured_imports
            )
        if not has_structured_imports:
            return
        for ctx in contexts:
            all_module_fields = {field.name for model in ctx.models for field in model.fields if field.name is not None}
            alias_shadowed_imports(
                self,
                ctx.models,
                all_module_fields,
                can_retain_cache=_can_retain_model_imports_cache(
                    ctx.models,
                    configured_types_are_builtin=self._configured_generation_types_are_builtin,
                ),
                module_imports=ctx.imports,
            )

    def _merge_runtime_expression_imports(
        self,
        contexts: list[ModuleContext],
        model_imports: dict[DataModel, tuple[Import, ...]],
    ) -> None:
        """Merge producer-registered imports only on the runtime-expression path."""
        for ctx in contexts:
            for model in ctx.models:
                runtime_imports: list[Import] = []
                for field in model.fields:
                    runtime_imports.extend(field.runtime_expression_imports)
                    for data_type in field.data_type.all_data_types:
                        runtime_imports.extend(data_type.runtime_expression_imports)
                if runtime_imports:
                    model_imports[model] = (*model_imports[model], *runtime_imports)
                    self._import_ledger.append(
                        ctx.imports, runtime_imports, ImportProducer("runtime-expressions", model)
                    )

    def _prepare_schema_runtime_validation_module_code(self, contexts: list[ModuleContext]) -> None:
        """Plan opt-in module helpers before their imports are collected."""
        for ctx in contexts:
            self.data_model_type.prepare_module_code(ctx.models)

    def _sync_schema_runtime_validation_module_imports(
        self,
        contexts: list[ModuleContext],
        model_imports: dict[DataModel, tuple[Import, ...]],
    ) -> None:
        """Merge imports added by module planning into finalized module imports."""
        for ctx in contexts:
            for model in ctx.models:
                prepared_imports = model.imports
                self._import_ledger.append(
                    ctx.imports,
                    [import_ for import_ in prepared_imports if import_ not in model_imports[model]],
                    ImportProducer("schema-validators", model),
                )
                model_imports[model] = prepared_imports

    def __prepare_type_alias_fields(self, contexts: list[ModuleContext], unused_models: list[DataModel]) -> None:
        """Prepare alias bodies once every module has collapsed its roots.

        A type alias has no model config to select Python's regex engine, so its patterns carry it,
        and its constraints use ``Annotated`` because type checkers reject ``con*`` calls in aliases.
        Module imports drop the replaced ``con*`` imports; finalization adds the annotation imports.
        """
        if (prepare_field := self.data_model_field_type.PREPARE_TYPE_ALIAS_FIELD) is None:
            return
        prepared_patterns: dict[str, PythonRuntimeExpression] = {}
        unused_model_ids = {id(model) for model in unused_models}
        replace_field_type = self.generation_store.replace_field_type
        for ctx in contexts:
            for model in ctx.models:
                if not isinstance(model, TypeAliasBase) or id(model) in unused_model_ids or not model.fields:
                    continue
                previous_imports = model.imports
                if prepare_field(field := model.fields[0], prepared_patterns, replace_field_type):
                    field.invalidate_semantic_caches()
                    current_imports = model.imports
                    self._import_ledger.remove(
                        ctx.imports,
                        [import_ for import_ in previous_imports if import_ not in current_imports],
                        ImportProducer("type-alias-fields", model, field),
                    )
                    self._register_runtime_expression()

    def _finalize_modules(
        self,
        contexts: list[ModuleContext],
        unused_models: list[DataModel],
        model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]],
        module_to_import: dict[ModulePath, Imports],
    ) -> None:
        """Finish the render preparation of every module and collect the imports of each module.

        The pipeline runs it after the generic base class, the decision phase and the render preparation of each
        module, which ``_process_single_module`` hands over.
        """
        state = _FinalizeState(contexts, unused_models, model_to_module_models, module_to_import)
        self.__runner().run_steps(Parser.__FINALIZE_SEGMENTS, self, state)

    def __decide(self, runner: Runner, run: _FinishRun) -> None:
        """Seal the facts final at B1 and run the decisions of the registry on them, when it holds any."""
        if not (decisions := self._DECISION_PASSES):
            return
        unused = {id(model) for model in run.unused_models}
        modules = [(ctx.module_key, [model for model in ctx.models if id(model) not in unused]) for ctx in run.contexts]
        facts = self.generation_store.seal_facts(modules, decision_views())
        writer = _DecisionWriter(self.generation_store)
        for decision in decision_order(decisions):
            runner.run_decision(decision, facts, writer)
        run.decisions = writer.outcome(model for _, models in modules for model in models)

    def __collect_all_models(self, state: _FinalizeState) -> None:  # noqa: PLR6301
        """Take the models of every module, before unused models leave them."""
        state.all_models = [model for ctx in state.contexts for model in ctx.models]

    def __clear_default_factory_import_caches(self, state: _FinalizeState) -> None:  # noqa: PLR6301
        """Forget the imports models cached: inherited defaults may have changed since a factory was first queried."""
        _clear_model_imports_cache(state.all_models)

    def __finalize_sequence_interface(self, state: _FinalizeState) -> None:  # noqa: PLR6301
        """Let each root model decide whether it renders the sequence interface."""
        for model in state.all_models:
            model.finalize_sequence_interface()

    def __snapshot_model_imports(self, state: _FinalizeState) -> None:  # noqa: PLR6301
        """Collect the imports of every model once."""
        state.model_imports = {model: model.imports for ctx in state.contexts for model in ctx.models}

    def __append_model_imports(self, state: _FinalizeState) -> None:
        """Add the imports of every model to its module."""
        for ctx in state.contexts:
            for model in ctx.models:
                self._import_ledger.append(
                    ctx.imports, state.model_imports[model], ImportProducer("model-imports-snapshot", model)
                )

    def __remove_unused_models(self, state: _FinalizeState) -> None:
        """Remove the unused models from their modules, and their imports from the module imports."""
        replaced_root_modules = (
            {
                id(model): (ctx.module_key, ctx.models)
                for ctx in state.contexts
                for model in ctx.models
                if id(model) in replaced_root_models
            }
            if (replaced_root_models := self._replaced_root_models)
            else {}
        )
        for unused_model in state.unused_models:
            module, models = (
                replaced_root_modules.pop(id(unused_model), None) or state.model_to_module_models[unused_model]
            )
            if unused_model in models:  # pragma: no branch
                self._import_ledger.remove(
                    state.module_to_import[module],
                    state.model_imports.get(unused_model, unused_model.imports),
                    ImportProducer("unused-models", unused_model),
                )
                models.remove(unused_model)

    def __validate_explicit_field_aliases(self, state: _FinalizeState) -> None:
        """Reject the explicit field aliases that no field name can carry."""
        for ctx in state.contexts:
            for model in ctx.models:
                self.model_resolver.validate_explicit_field_aliases(
                    model.fields, self.field_name_model_type, self.target_python_version
                )

    def __remove_unused_imports(self, state: _FinalizeState) -> None:
        """Remove the module imports that no model of the module uses."""
        for ctx in state.contexts:
            used_names = self._collect_used_names_from_models(ctx.models, state.model_imports)
            self._import_ledger.remove_unused(ctx.imports, used_names, _REMOVE_UNUSED)

    def __resolve_module_import_conflicts(self, state: _FinalizeState) -> None:
        """Let the backend resolve the imports of each module that conflict with its names."""
        for ctx in state.contexts:
            with self._import_ledger.external(ctx.imports, _IMPORT_CONFLICTS):
                self.data_model_type.resolve_module_import_conflicts(ctx.models, state.model_imports, ctx.imports)

    def __bind_module_field_names(self, state: _FinalizeState) -> None:
        """Bind the field names of each module that its imports shadow."""
        for ctx in state.contexts:
            _bind_module_field_names(ctx.models, ctx.imports, self._import_ledger)

    def __change_module_imported_model_names(self, state: _FinalizeState) -> None:
        """Rename the models whose names an import of their module takes; remember whether any was renamed."""
        for ctx in state.contexts:
            state.renamed_models = (
                self.__change_imported_model_name(ctx.models, ctx.imports, ctx.scoped_model_resolver)
                or state.renamed_models
            )

    def __name_final_classes_of_additional_properties(self, state: _FinalizeState) -> None:  # noqa: PLR6301
        """Name the final classes that ``extra_items`` and additional properties reference."""
        for model in state.additional_properties_models:
            model._name_final_classes()  # noqa: SLF001

    def __refresh_sequence_interface_after_renames(self, state: _FinalizeState) -> None:  # noqa: PLR6301
        """Decide the sequence interface again after models were renamed."""
        for model in state.all_models:
            model.finalize_sequence_interface()

    def __replan_schema_validators_after_renames(self, state: _FinalizeState) -> None:
        """Plan the schema validator helpers of every module again after models were renamed.

        Helper-name reservations include referenced models from other modules, so a rare import collision
        invalidates every module plan; renaming only changes synthetic helper names, and the snapshots of their
        imports are kept synchronized defensively.
        """
        for ctx in state.contexts:
            self.data_model_type.invalidate_module_code_cache(ctx.models)
        self._prepare_schema_runtime_validation_module_code(state.contexts)
        self._sync_schema_runtime_validation_module_imports(state.contexts, state.model_imports)

    def __remap_import_overrides(self, state: _FinalizeState) -> None:
        """Route the shared and module imports through the modules ``--import-overrides`` names."""
        overrides = cast("Mapping[str, str]", self._import_overrides)
        _remap_imports(self._import_ledger, self.imports, overrides)
        for ctx in state.contexts:
            _remap_imports(self._import_ledger, ctx.imports, overrides)

    def _check_model_inheritance(self, contexts: Sequence[ModuleContext]) -> None:  # noqa: PLR0912, PLR0914, PLR0915
        """Reject invalid builtin inheritance; an existing topological order needs no DFS."""
        backend = self.data_model_type.__module__
        ordinary_backends = {
            "datamodel_code_generator.model.pydantic_v2.base_model",
            "datamodel_code_generator.model.pydantic_v2.dataclass",
            "datamodel_code_generator.model.dataclass",
        }
        typed_dict = backend == "datamodel_code_generator.model.typed_dict"
        struct = backend == "datamodel_code_generator.model.msgspec"
        if backend not in ordinary_backends and not typed_dict and not struct:
            return
        if (
            typed_dict
            and (custom_template_dir := self.custom_template_dir) is not None
            and cached_path_exists(custom_template_dir / "TypedDictClass.jinja2")
        ):
            return

        def builtin(model: DataModel) -> bool:
            return type(model) is self.data_model_type and not (
                self.custom_template_dir is not None and model._uses_custom_root_template  # noqa: SLF001
            )

        def emitted_bases(model: DataModel) -> list[BaseClassDataType]:
            if not builtin(model) or (typed_dict and getattr(model, "is_functional_syntax", False)):
                return []
            return model.base_classes

        if not any(base.reference for ctx in contexts for model in ctx.models for base in emitted_bases(model)):
            return

        models = [model for ctx in contexts for model in ctx.models]
        positions = [(ctx_index, index) for ctx_index, ctx in enumerate(contexts) for index in range(len(ctx.models))]
        identities = {id(model): index for index, model in enumerate(models)}
        for index, model in enumerate(models):
            references = [base.reference for base in emitted_bases(model) if base.reference]
            if len(references) > 1 or (references and identities.get(id(references[0].source), index) >= index):
                break
        else:
            if struct:
                for ctx in contexts:
                    self.data_model_type.render_module_code(ctx.models)
            return

        paths: dict[str, list[DataModel]] = defaultdict(list)
        for model in models:
            paths[model.path].append(model)
        parents: list[list[int]] = [[] for _ in models]
        closed = [backend in ordinary_backends and builtin(model) for model in models]
        overrides = self._import_overrides or {}
        ordered = True
        for index, model in enumerate(models):
            ctx_index, _ = positions[identities[id(model)]]
            ctx = contexts[ctx_index]
            for base in emitted_bases(model):
                reference = base.reference
                if reference is None:
                    terminal = base.import_
                    closed[index] = closed[index] and (
                        backend == "datamodel_code_generator.model.pydantic_v2.base_model"
                        and terminal is not None
                        and terminal.from_ == "pydantic"
                        and terminal.import_ == "BaseModel"
                        and base.type_hint == (terminal.alias or terminal.import_)
                    )
                    continue
                candidates = paths.get(reference.path, [])
                if len(candidates) != 1 or reference.source is not candidates[0]:
                    closed[index] = False
                    continue
                parent = candidates[0]
                parent_index = identities[id(parent)]
                parent_ctx_index, _ = positions[parent_index]
                imported = ctx.imports.reference_paths.get(reference.path)
                binding = (
                    parent.class_name
                    if ctx_index == parent_ctx_index
                    else (imported.alias or imported.import_)
                    if imported is not None and imported.import_ not in overrides
                    else None
                )
                if base.type_hint != binding:
                    closed[index] = False
                    continue
                parents[index].append(parent_index)
                ordered = ordered and parent_index < index

        if struct:
            for ctx in contexts:
                self.data_model_type.render_module_code(ctx.models)

        def label(index: int) -> str:
            model = models[index]
            return f"{model.class_name} ({redact_reference(model.path)})"

        postorder = list(range(len(models))) if ordered else []
        if not ordered:
            completed: set[int] = set()
            for start in range(len(models)):
                if start in completed:
                    continue
                active = {start: 0}
                stack = [(start, iter(parents[start]))]
                while stack:
                    index, remaining = stack[-1]
                    if (parent := next(remaining, None)) is None:
                        stack.pop()
                        active.pop(index)
                        completed.add(index)
                        postorder.append(index)
                    elif parent in active:
                        cycle = [item for item, _ in stack[active[parent] :]]
                        cycle.append(parent)
                        msg = "Generated model inheritance cycle: " + " -> ".join(map(label, cycle))
                        raise Error(msg)
                    elif parent not in completed:
                        active[parent] = len(stack)
                        stack.append((parent, iter(parents[parent])))

            for index, direct in enumerate(parents):
                ctx_index, position = positions[identities[id(models[index])]]
                for parent in direct:
                    parent_ctx_index, parent_position = positions[parent]
                    if ctx_index == parent_ctx_index and parent_position >= position:
                        msg = (
                            f"Generated model {label(index)} requires local base {label(parent)} before its definition."
                        )
                        raise Error(msg)

        for index in postorder:
            closed[index] = closed[index] and all(closed[parent] for parent in parents[index])
        joins = [index for index in postorder if closed[index] and len(parents[index]) > 1]
        if not joins:
            return
        join_set = set(joins)
        uses: Counter[int] = Counter()
        for index in joins:
            for direct_parent in parents[index]:
                parent = direct_parent
                while parent not in join_set and parents[parent]:
                    parent = parents[parent][0]
                if parent in join_set:
                    uses[parent] += 1
        linearizations: dict[int, list[int]] = {}
        for index in joins:
            sequences: list[list[int]] = []
            consumed: list[int] = []
            for direct_parent in parents[index]:
                parent = direct_parent
                sequence: list[int] = []
                while parent not in join_set:
                    sequence.append(parent)
                    if not parents[parent]:
                        break
                    parent = parents[parent][0]
                if parent in join_set:
                    sequence.extend(linearizations[parent])
                    consumed.append(parent)
                sequences.append(sequence)
            sequences.append(parents[index])
            merged = c3_merge([sequence.copy() for sequence in sequences], key=str)
            order = {item: position for position, item in enumerate(merged)}
            if any(
                len(sequence) != len(set(sequence))
                or any(order[left] >= order[right] for left, right in pairwise(sequence))
                for sequence in sequences
            ):
                msg = (
                    f"Generated model {label(index)} has inconsistent method resolution order for generated bases "
                    + ", ".join(map(label, parents[index]))
                    + "."
                )
                raise Error(msg)
            if uses[index]:
                linearizations[index] = [index, *merged]
            for parent in consumed:
                uses[parent] -= 1
                if not uses[parent]:
                    del linearizations[parent]

    def _set_nested_model_default_factory_metadata(
        self,
        contexts: list[ModuleContext],
        require_update: Collection[ModelKey],
    ) -> None:
        """Record declaration order and recursive model components for default factories."""
        recursive_keys_by_model: dict[ModelKey, frozenset[ModelKey]] = {}
        if require_update:
            snapshot = GraphSnapshot.NESTED_MODEL_DEFAULT_FACTORIES
            keyed = keyed_models((model for ctx in contexts for model in ctx.models), snapshot.value)
            index = self.generation_store.index
            graph = reference_graph(
                snapshot,
                keyed.models,
                {
                    EdgeKind.REFERENCE: {
                        key: index.referenced_keys_for_model(model, keyed) for key, model in keyed.models.items()
                    }
                },
            )

            def old() -> list[set[ModelKey]]:
                silent = unchecked(keyed)
                return find_circular_sccs({
                    key: index.referenced_keys_for_model(model, silent) & keyed.models.keys()
                    for key, model in keyed.models.items()
                })

            for component in graph_answer(snapshot, graph.circular_components((EdgeKind.REFERENCE,)), old):
                recursive_keys = frozenset(component)
                recursive_keys_by_model.update(dict.fromkeys(recursive_keys, recursive_keys))

        for module_index, ctx in enumerate(contexts):
            _set_nested_model_default_factory_order(ctx.models, module_index, recursive_keys_by_model)

    def _generate_module_output(  # noqa: PLR0912, PLR0913, PLR0917
        self,
        ctx: ModuleContext,
        config: ParseConfig,
        contexts: list[ModuleContext],
        forwarder_map: ForwarderMap,
        require_update: Collection[ModelKey],
        future_imports_str: str,
    ) -> Result | None:
        """Generate output for a single module."""
        result: list[str] = []
        export_imports: Imports | None = None
        module_future_imports_str = self._get_module_future_imports(ctx, config, future_imports_str)
        if (mode := check_mode(CheckArea.MODULE_IMPORTS)) is not None:
            self._check_module_imports(mode, ctx)

        if config.all_exports_scope is not None and ctx.module[-1] == "__init__.py":
            child_exports = self._collect_exports_for_init(ctx.module, contexts, config.all_exports_scope)
            if child_exports:
                local_model_names = {
                    m.reference.short_name
                    for m in ctx.models
                    if m.reference and not m.reference.short_name.startswith("_")  # pragma: no branch
                }
                resolved_exports = self._resolve_export_collisions(
                    child_exports,
                    config.all_exports_collision_strategy,
                    local_model_names,
                    self.target_python_version,
                )
                export_imports = self._build_all_exports_code(resolved_exports)

        if ctx.models:
            if config.with_import:
                module_imports = self._import_ledger.rendered(ctx.imports)
                import_parts = [s for s in [module_future_imports_str, str(self.imports), str(module_imports)] if s]
                result += [*import_parts, "\n"]

            if export_imports:
                result += [str(export_imports), ""]
                for m in ctx.models:
                    if m.reference and not m.reference.short_name.startswith("_"):  # pragma: no branch
                        export_imports.add_export(m.reference.short_name)
                result += [export_imports.dump_all(multiline=True) + "\n"]

            module_code = self.data_model_type.render_module_code(ctx.models)
            if module_code:
                module_code_insertion_index = self.data_model_type.get_module_code_insertion_index(ctx.models)
                if module_code_insertion_index:
                    result += [
                        dump_templates(ctx.models[:module_code_insertion_index]),
                        "",
                        "",
                        module_code,
                        "",
                        dump_templates(ctx.models[module_code_insertion_index:]),
                    ]
                else:
                    result += [module_code, "", dump_templates(ctx.models)]
            else:
                result += [dump_templates(ctx.models)]

            result += self.__get_resolve_reference_action_parts(
                ctx.models,
                require_update,
                use_deferred_annotations=config.use_deferred_annotations,
            )

        if not result and ctx.module_key in forwarder_map:
            internal_module, class_mappings = forwarder_map[ctx.module_key]
            forwarder_content = self.__generate_forwarder_content(
                ctx.module_key, internal_module, class_mappings, is_init=ctx.is_init
            )
            result = [forwarder_content]

        if not result and not ctx.is_init:
            return None

        body = "\n".join(result)
        if config.code_formatter:
            body = _format_body_safe(
                body,
                config.code_formatter,
                generated_code=self._uses_standard_generation_templates,
            )

        return Result(
            body=body,
            future_imports=module_future_imports_str,
            source=ctx.models[0].file_path if ctx.models else None,
        )

    def _generate_empty_init_exports(
        self,
        results: dict[ModulePath, Result],
        contexts: list[ModuleContext],
        config: ParseConfig,
        future_imports_str: str,
    ) -> None:
        """Generate exports for empty __init__.py files."""
        if config.all_exports_scope is None:  # pragma: no cover
            return
        processed_init_modules = {ctx.module for ctx in contexts if ctx.module[-1] == "__init__.py"}
        for init_module, init_result in list(results.items()):
            if init_module[-1] != "__init__.py" or init_module in processed_init_modules or init_result.body:
                continue
            child_exports = self._collect_exports_for_init(init_module, contexts, config.all_exports_scope)
            if child_exports:
                resolved = self._resolve_export_collisions(
                    child_exports, config.all_exports_collision_strategy, set(), self.target_python_version
                )
                export_imports = self._build_all_exports_code(resolved)
                import_parts = [s for s in [future_imports_str, str(self.imports)] if s] if config.with_import else []
                parts = import_parts + (["\n"] if import_parts else [])
                parts += [str(export_imports), "", export_imports.dump_all(multiline=True)]
                body = "\n".join(parts)
                if config.code_formatter:
                    body = _format_body_safe(
                        body,
                        config.code_formatter,
                        generated_code=self._uses_standard_generation_templates,
                    )
                results[init_module] = Result(
                    body=body,
                    future_imports=future_imports_str,
                )

    @staticmethod
    def _field_metadata(field: DataModelFieldBase, *, is_root_model: bool = False) -> ModelFieldMetadata:
        source_name = field.original_name
        if source_name is None:
            source_name = field.alias
        if source_name is None:
            source_name = field.name
        if source_name is None:
            source_name = ""
        if is_root_model:
            # RootModel renderers use root rather than the IR's synthetic field name.
            name = alias = "root"
        else:
            name = field.name if field.name is not None else source_name
            alias = field.alias if field.alias is not None else source_name
        return {
            "name": name,
            "alias": alias,
            "original_name": field.original_name,
            "type": field.type_hint,
            "required": field.required,
        }

    @classmethod
    def _model_metadata(cls, model: DataModel, module: ModulePath, identity: str) -> GeneratedModelMetadata:
        """Describe a generated model by the JSON Pointer location of the schema object it is built from.

        A model without a location (GraphQL input, or a schema object a custom parser built) keeps its reference path.
        """
        title = model.extra_template_data.get("title")
        if (location := location_of(model.reference)) is None:
            source_ref = redact_reference(identity)
            source_path = _source_path_from_reference_path(source_ref)
        else:
            source_ref = redact_reference(str(location))
            source_path = list(location.tokens)
        return {
            "class_name": model.class_name,
            "name": model.name,
            "module": _module_name_from_module_path(module),
            "source_ref": source_ref,
            "source_path": source_path,
            "title": title if isinstance(title, str) else None,
            "fields": [cls._field_metadata(field, is_root_model=model.IS_ROOT_MODEL) for field in model.fields],
        }

    @classmethod
    def _build_model_metadata(
        cls,
        contexts: list[ModuleContext],
        model_identities: Mapping[DataModel, str],
    ) -> tuple[ModelMetadata, tuple[str, ...]]:
        """Return the metadata of the emitted models and, in the same order, their redacted identity paths."""
        models = [(model, ctx.module) for ctx in contexts for model in ctx.models if model in model_identities]
        return (
            {
                "version": 1,
                "models": [cls._model_metadata(model, module, model_identities[model]) for model, module in models],
            },
            tuple(redact_reference(model_identities[model]) for model, _ in models),
        )

    def _check_module_imports(self, mode: CheckMode, ctx: ModuleContext) -> None:
        """Report the shared imports and the module's imports where they differ from the replay of their ledger.

        Such a difference is a write that did not go through ``parser/_imports_plan.py:ModuleImportLedger``.
        """
        for label, imports in (("shared imports", self.imports), (f"imports of {'/'.join(ctx.module)}", ctx.imports)):
            if differences := self._import_ledger.differences(imports):
                mode.violation(
                    Error,
                    f"{label} differ from the replay of their ledger in {', '.join(differences)}",
                    imports=label,
                    parts=list(differences),
                )

    def _check_schema_locations(self, mode: CheckMode) -> None:
        """Report the models parsed from a JSON document without a schema location (the copy step lost it).

        A model whose Reference several schema objects reach has none by rule and is not reported.
        """
        if missing := [
            model
            for model in self.results
            if location_of(model.reference) is None and not model.reference.__dict__.get(SCHEMA_LOCATION_CONFLICT_KEY)
        ]:
            paths = [(model.class_name, redact_reference(model.reference.path)) for model in missing]
            names = ", ".join(f"{name} ({path})" for name, path in paths)
            mode.violation(
                Error,
                f"models without a schema location: {names}",
                models=[{"class_name": name, "path": path} for name, path in paths],
            )

    def _check_model_owners(self, mode: CheckMode) -> None:
        """Report the models parsed from a JSON document that have no owner and are not roots (`is_root_or_owned`)."""
        if unowned := [model for model in self.results if not is_root_or_owned(model.reference)]:
            paths = [(model.class_name, redact_reference(model.reference.path)) for model in unowned]
            names = ", ".join(f"{name} ({path})" for name, path in paths)
            mode.violation(
                Error,
                f"models without an owner that are not roots: {names}",
                models=[{"class_name": name, "path": path} for name, path in paths],
            )

    def _close_http_fetch_session(self) -> None:
        """Close and discard the parser-scoped HTTP session without masking parser results."""
        if (session := self._http_fetch_session) is None:
            return
        self._http_fetch_session = None
        with contextlib.suppress(Exception):
            session.close()

    def _dispose(self) -> None:
        """Break reference cycles in the parsed object graph.

        Models, fields, data types, and references point at each other in
        cycles, which keeps the whole graph alive until a full garbage
        collection pass. Severing the back references lets ordinary reference
        counting reclaim the graph as soon as the parser is dropped, which
        matters for processes that call generate() repeatedly.
        """
        self._close_http_fetch_session()
        self.generation_store._dispose(self.model_resolver.references.values())  # noqa: SLF001
        self.model_resolver.references.clear()
        self._reset_local_source_cache()
        self._schema_graph = None

    def dispose(self) -> None:
        """Release parser-owned resources after a facade-managed run."""
        self._dispose()

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether model uses the configured root model representation."""
        return isinstance(model, self.data_model_root_type)

    def _reset_local_source_cache(self) -> None:
        self._cache_local_sources = False
        self._local_source_cache = None

    def _report_parse_diagnostics(self) -> None:
        """Report diagnostics collected while parsing the input schema."""

    def _run_with_suppressed_warnings(self, callback: Callable[[], None]) -> None:  # noqa: PLR6301
        """Invoke one retry hook without repeating user-visible warnings."""
        import warnings  # noqa: PLC0415

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            callback()

    def parse(  # noqa: PLR0913, PLR0917
        self,
        with_import: bool | None = True,  # noqa: FBT001, FBT002
        format_: bool | None = True,  # noqa: FBT001, FBT002
        settings_path: Path | None = None,
        disable_future_imports: bool = False,  # noqa: FBT001, FBT002
        all_exports_scope: AllExportsScope | None = None,
        all_exports_collision_strategy: AllExportsCollisionStrategy | None = None,
        module_split_mode: ModuleSplitMode | None = None,
        collect_model_metadata: bool = False,  # noqa: FBT001, FBT002
    ) -> str | dict[tuple[str, ...], Result]:
        """Parse schema and generate code, returning single file or module dict."""
        run_context = self._run_context
        run = _PipelineRun(
            _ParseArgs(
                with_import,
                format_,
                settings_path,
                disable_future_imports,
                all_exports_scope,
                all_exports_collision_strategy,
                module_split_mode,
                collect_model_metadata,
            ),
            suppress_parse_warnings=run_context is not None and run_context.suppress_parse_warnings,
        )
        token = SCHEMA_GRAPH.set(self._schema_graph)
        caller = anchor_parse_caller()
        try:
            with read_check_flags(), parse_checks(self, Error):
                self.__runner().run_steps(Parser.__PIPELINE, self, run)
        finally:
            release_parse_caller(caller)
            SCHEMA_GRAPH.reset(token)
        return run.output

    def __runner(self) -> Runner:
        """Return a runner of the pipeline tables for this parser."""
        return Runner(self.__enter_phase, {Boundary.PARSED: self.__enter_parsed})

    def __enter_phase(self, phase: Phase) -> None:
        """Mark that the pipeline enters ``phase`` on the generation store."""
        self.generation_store.enter_phase(phase)

    def __enter_parsed(self) -> None:
        """Run the check-mode checks of L1 and arm the write guards."""
        if self._schema_graph is not None and (mode := check_mode(CheckArea.SCHEMA_LOCATIONS)) is not None:
            self._check_schema_locations(mode)
        if self._schema_graph is not None and (mode := check_mode(CheckArea.MODEL_GRAPH)) is not None:
            self._check_model_owners(mode)
        if self._schema_graph is not None and (mode := check_mode(CheckArea.SCHEMA_GRAPH)) is not None:
            for (site, origin), (plain, carried) in self._schema_graph.ref_site_counts().items():
                mode.record(
                    f"{CheckArea.SCHEMA_GRAPH.value}: {site} read {carried} references of origin {origin.value} with "
                    f"their edge and {plain} as plain strings",
                    kind="ref-sites",
                    site=site,
                    origin=origin.value,
                    plain=plain,
                    carried=carried,
                )
        arm_parse_checks(self.generation_store, "Parser.parse")

    def __parse_raw(self, run: _SettleRun) -> None:
        """Run L0 and L1, without repeating warnings on a retry; the HTTP session closes either way.

        A parse that exceeds the recursion limit on deeply nested schemas stops with an error, not a partial result.
        """
        try:
            if run.suppress_parse_warnings:
                self._run_with_suppressed_warnings(self.parse_raw)
            else:
                self.parse_raw()
        except RecursionError:
            msg = (
                "Schema nesting is too deep to generate: parsing the nested schemas exceeded the Python recursion "
                f"limit ({sys.getrecursionlimit()})."
            )
            raise Error(msg) from None
        finally:
            self._close_http_fetch_session()

    def __index_const_narrowed_values(self, _run: _SettleRun) -> None:
        """Index the data types whose value a const narrows, by data type."""
        self._const_narrowed_values = {
            id(fact.data_type): (fact.data_type, narrowing)
            for reference, narrowing in self._const_narrowed_references
            for fact in self.generation_store.index.data_type_facts_for_reference(reference)
            if fact.role != "base"
        }

    def __settle_yielding_names(self, _run: _SettleRun) -> None:
        """Give the names that yield to ``--model-name-map`` their final form."""
        self._typed_map_value_reference_ids = self.model_resolver.settle_yielding_names()
        if (changes := self.model_resolver.yielding_name_changes) and self._names_have_class_keyed_options(changes):
            self.yielding_names_to_assign = tuple(self.model_resolver.final_yielding_names.items())

    def __report_parse_diagnostics(self, run: _SettleRun) -> None:
        """Report the parse diagnostics, without repeating them on a retry."""
        if run.suppress_parse_warnings:
            self._run_with_suppressed_warnings(self._report_parse_diagnostics)
        else:
            self._report_parse_diagnostics()

    def __prepare_run_parse_config(self, run: _SettleRun) -> None:
        """Prepare the configuration of output modules from the arguments of ``parse()``."""
        run.config = self._prepare_parse_config(
            run.args.with_import,
            run.args.disable_future_imports,
            run.args.all_exports_scope,
            run.args.all_exports_collision_strategy,
            run.args.module_split_mode,
        )

    def __sort_data_models(self, run: _SettleRun) -> None:
        """Sort the parsed models so that each follows the models it needs."""
        _, run.sorted_data_models, require_update = sort_data_models(
            self.results,
            generation_index=self.generation_store.index,
            pydantic_v2_root_model_type=self.pydantic_v2_root_model_type,
            runtime_validation_references=(
                None if self.keep_model_order else _runtime_validation_reference_keys(self.results, same_module=True)
            ),
        )
        self._recursion_facts = RecursionFacts(require_update=frozenset(require_update), circular_roots=frozenset())

    def __record_source_reference_paths(self, run: _SettleRun) -> None:  # noqa: PLR6301
        """Record the reference path each model was parsed from, for the model metadata."""
        run.model_identities = {model: model.reference.path for model in run.sorted_data_models.values()}

    def __build_structure(self, run: _StructuredRun) -> None:
        """Group the models into output modules through ``_build_module_structure``."""
        (
            run.module_models,
            run.internal_modules,
            run.forwarder_map,
            run.model_to_module_models,
            run.model_path_to_module_name,
        ) = self._build_module_structure(run.sorted_data_models, run.args.module_split_mode)

    def __configure_code_formatter(self, run: _StructuredRun) -> None:
        """Build the code formatter of the run, unless the builtin defaults need none."""
        match self.formatters:
            case [] if (
                not self.custom_formatter
                and "_build_code_formatter" not in self.__dict__
                and type(self)._build_code_formatter is Parser._build_code_formatter  # noqa: SLF001
            ):
                pass
            case _:
                run.config = run.config._replace(
                    code_formatter=self._build_code_formatter(
                        run.args.settings_path,
                        is_multi_module_output=self.defer_formatting or len(run.module_models) > 1,
                    ),
                )

    def __detect_standard_generation_templates(self, run: _StructuredRun) -> None:
        """Decide whether the run renders with the standard templates and the builtin formatter only."""
        self._uses_standard_generation_templates = bool(
            (code_formatter := run.config.code_formatter)
            and code_formatter.use_builtin_formatter
            and self._configured_generation_types_are_builtin
            and not (parser_config := self.config).custom_template_dir
            and not any((
                parser_config.additional_imports,
                parser_config.class_decorators,
                parser_config.base_class,
                parser_config.base_class_map,
                parser_config.extra_template_data,
                parser_config.validators,
                parser_config.generate_schema_validators,
                parser_config.alias_generator,
                parser_config.custom_class_name_generator,
                parser_config.dump_resolve_reference_action is not None
                and not get_resolve_reference_action_capabilities(
                    parser_config.dump_resolve_reference_action
                ).generated_formatter_safe,
                parser_config.type_mappings,
                parser_config.type_overrides,
                parser_config.import_overrides,
            ))
        )

    def __reset_per_run_state(self, _run: _StructuredRun) -> None:
        """Forget what the passes of an earlier parse recorded on the parser."""
        self._hidden_named_types.clear()
        self._hiding_field_names.clear()
        self._variant_discriminator_tags.clear()
        self._deferred_tagged_unions.clear()
        self._prepared_modules.clear()
        self._base_stand_ins.clear()
        self._generic_base_model = None
        self._removed_reuse_users.clear()
        self._renamed_types.clear()
        self._renamed_type_paths.clear()
        self._replaced_root_models.clear()

    def __unconstrain_union_members(self, run: _StructuredRun) -> None:
        """Drop the constraints of union members, which tagged unions cannot carry."""
        for _, models in run.module_models:
            for field in (field for model in models for field in model.fields):
                self.generation_store.drop_union_member_constraints(field, refresh="deferred")

    def __record_emitting_modules(self, run: _StructuredRun) -> None:
        """Record the module that writes each model, for ``--collapse-root-models-name-strategy``."""
        self._emitting_module_lists = (
            [(_module_name_from_module_path(module_), models) for module_, models in run.module_models]
            if self.collapse_root_models and self.collapse_root_models_name_strategy is not None
            else []
        )
        self._emitting_module_names = {
            id(model): module_name for module_name, models in self._emitting_module_lists for model in models
        }

    def __process_each_module(self, run: _ModulesRun) -> None:
        """Process every output module through ``_process_single_module``, in module order."""
        module_models = run.module_models
        require_update = self._recursion_facts["require_update"]
        require_update_action_models = [
            model.path
            for _, models in module_models
            for model in models
            if model_key_of(model.reference) in require_update
        ]
        for position, (module_, models) in enumerate(module_models, 1):
            self._defers_tagged_unions = position < len(module_models)
            with pass_scope("Parser._process_single_module", Phase.MODULES), module_checkpoint(models):
                ctx = self._process_single_module(
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
        self.__settle_update_actions(require_update_action_models, _context_models(run.contexts))

    def __settle_update_actions(self, listed_paths: Iterable[str], models: Iterable[DataModel]) -> None:
        """Give the reference update action to the models at the paths that ``_process_single_module`` listed.

        The parser lists the paths of the models needing the action. A listed path that none of them holds, as one a
        parser extension lists, names the model at it (row ``legacy-keyless-reference-by-path``).
        """
        keyed = keyed_models(models, "listed reference update actions")
        recursion_facts = self._recursion_facts
        require_update = recursion_facts["require_update"]
        held = {keyed.models[key].path for key in require_update & keyed.models.keys()}
        self._recursion_facts = RecursionFacts(
            require_update=require_update.union(
                keyed.resolve(path for path in listed_paths if path not in held) - {UNRESOLVED_MODEL_KEY}
            ),
            circular_roots=recursion_facts["circular_roots"],
        )

    def __tag_fields_of_tagged_bases(self, run: _FinishRun) -> None:  # noqa: PLR6301
        """Make the fields of tagged msgspec bases tags, and remember the models that opt out."""
        run.opted_out = _tag_fields_of_tagged_bases(_context_models(run.contexts))

    def __record_generated_class_names(self, run: _FinishRun) -> None:
        """Record the class name of every generated model."""
        self.generated_class_names = tuple(
            (model.class_name, model.duplicate_class_name) for ctx in run.contexts for model in ctx.models
        )

    def __add_root_init_result(self, run: _FinishRun) -> None:  # noqa: PLR6301
        """Add an empty root ``__init__.py`` when the output has several top-level packages."""
        root_init: ModulePath = ("__init__.py",)
        if root_init not in run.module_results:
            top_level_dirs = {k[0] for k in run.module_results if len(k) >= 2}  # noqa: PLR2004
            if len(top_level_dirs) > 1:
                run.module_results[root_init] = Result(body="")

    def __render(self, run: _FinishRun) -> None:
        """Render every module into the output of ``parse()``."""
        run.output = self.__render_modules(
            run.module_results,
            contexts=run.contexts,
            sorted_data_models=run.sorted_data_models,
            model_identities=run.model_identities,
            config=run.config,
            forwarder_map=run.forwarder_map,
            require_update=self._recursion_facts["require_update"],
        )

    __STRUCTURE_BODY: ClassVar[tuple[StepLike[Parser[Any, Any], _StructureState], ...]] = (
        PassSpec(decls.GROUP_MODELS_BY_MODULE, lambda self, s: self.__group_models_by_module(s)),
        PassSpec(decls.RESET_COLLAPSED_REUSE_STATE, lambda self, s: self.__reset_collapsed_reuse_state(s)),
        PassSpec(decls.SNAPSHOT_UNION_TAGS, lambda self, s: self.__snapshot_union_tags(s)),
        ForEachModule(
            "deduplicate_module_models",
            lambda _, s: (models for _, models in s.module_models),
            (
                PassSpec(
                    decls.DELETE_DUPLICATE_MODELS,
                    lambda self, models: self.__delete_duplicate_models(models),
                    checked_models=lambda _, models: models,
                ),
                PassSpec(
                    decls.REPLACE_DUPLICATE_NAME_IN_MODULE,
                    lambda self, models: self.__replace_duplicate_name_in_module(models),
                    checked_models=lambda _, models: models,
                ),
            ),
        ),
        PassSpec(
            decls.APPLY_FORCED_INVALID_DOTTED_STDOUT_REPAIR,
            lambda self, s: self.__apply_forced_invalid_dotted_stdout_repair(s),
        ),
        PassSpec(decls.REUSE_MODEL_TREE_SCOPE, lambda self, s: self.__apply_reuse_model_tree_scope(s)),
        PassSpec(decls.RESOLVE_CIRCULAR_IMPORTS, lambda self, s: self.__resolve_module_circular_imports(s)),
    )
    """The passes of ``_build_module_structure``."""

    __MODULE_BODY: ClassVar[tuple[StepLike[Parser[Any, Any], _ModuleState], ...]] = (
        PassSpec(
            decls.RECORD_DECIMAL_ALIAS_MODELS,
            lambda self, s: self.__record_decimal_alias_models(s),
            when=lambda _, s: "Decimal" in s.all_module_fields,
            checked_models=_module_models,
        ),
        PassSpec(
            decls.ALIAS_SHADOWED_IMPORTS,
            lambda self, s: alias_shadowed_imports(
                self, s.model_list, s.all_module_fields, can_retain_cache=s.can_retain_cache
            ),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.OVERRIDE_REQUIRED_FIELD,
            lambda self, s: override_required_field(self, s.model_list, can_retain_cache=s.can_retain_cache),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.REPLACE_UNIQUE_LIST_TO_SET,
            lambda self, s: self.__replace_unique_list_to_set(s.model_list, can_retain_cache=s.can_retain_cache),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.CHANGE_FROM_IMPORT,
            lambda self, s: self.__change_module_from_import(s),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.EXTRACT_INHERITED_ENUM,
            lambda self, s: self.__extract_inherited_enum(s.model_list),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.REFRESH_IMPORT_CACHE_RETENTION,
            lambda self, s: self.__refresh_import_cache_retention(s),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.MARK_UNUSED_MODELS_START,
            lambda self, s: self.__mark_unused_models_start(s),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.SET_REFERENCE_DEFAULT_VALUE_TO_FIELD,
            lambda self, s: self.__set_reference_default_value_to_field(
                s.model_list, can_retain_cache=s.can_retain_cache
            ),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.REUSE_MODEL,
            lambda self, s: self.__reuse_model(s.model_list, s.require_update_action_models),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.COLLAPSE_ROOT_MODELS,
            lambda self, s: self.__collapse_module_root_models(s),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.SET_DEFAULT_ENUM_MEMBER,
            lambda self, s: self.__set_default_enum_member(s.model_list, can_retain_cache=s.can_retain_cache),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.SORT_MODELS,
            lambda self, s: self.__sort_models(
                s.model_list, s.imports, use_deferred_annotations=s.config.use_deferred_annotations
            ),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.CHANGE_FIELD_NAME,
            lambda self, s: self.__change_field_name(s.model_list, can_retain_cache=s.can_retain_cache),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.RESTORE_INHERITED_FIELD_NAMES_FOR_UNIQUE_ALIASES,
            lambda self, s: self.__restore_inherited_field_names_for_unique_aliases(s.model_list),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.APPLY_DISCRIMINATOR_TYPE,
            lambda self, s: self.__apply_discriminator_type(
                s.model_list, s.imports, can_retain_cache=s.can_retain_cache
            ),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.TYPE_CONST_NARROWED_VALUES,
            lambda self, s: self.__type_const_narrowed_values(
                s.model_list, s.imports, can_retain_cache=s.can_retain_cache
            ),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.SET_ONE_LITERAL_ON_DEFAULT,
            lambda self, s: self.__set_one_literal_on_default(s.model_list, can_retain_cache=s.can_retain_cache),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.REFRESH_FIELD_CONFIG,
            lambda _, s: _refresh_field_config(s.model_list),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.RENAME_FIELDS_HIDING_TYPES,
            lambda self, s: self.__rename_fields_hiding_types(
                s.model_list,
                can_retain_cache=s.can_retain_cache,
                use_deferred_annotations=s.config.use_deferred_annotations,
            ),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.REMOVE_OVERRIDDEN_MODELS,
            lambda self, s: self.__remove_module_overridden_models(s),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.APPLY_FIELD_TYPE_OVERRIDES,
            lambda self, s: self.__apply_field_type_overrides(s.model_list),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.TYPE_DICT_KEYS_BY_ROOT_TYPES,
            lambda self, s: self.__type_module_dict_keys_by_root_types(s),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.SNAPSHOT_MODULE_UNUSED_MODELS,
            lambda self, s: self.__snapshot_module_unused_models(s),
            checked_models=_module_models,
        ),
        PassSpec(
            decls.APPLY_BASE_CLASS_TYPE_OVERRIDES,
            lambda self, s: self.__apply_base_class_type_overrides(s.model_list),
            checked_models=_module_models,
        ),
    )
    """The passes of ``_process_single_module``, for one output module."""

    __MODULE_RENDER_PREPARATION_BODY: ClassVar[tuple[PassSpec[Parser[Any, Any], _PreparedModule], ...]] = (
        PassSpec(
            decls.FIX_CONSTRUCTOR_FIELD_ORDERING,
            lambda self, s: self.__fix_constructor_field_ordering(s.model_list_before_override_removal),
            checked_models=lambda _, s: s.model_list_before_override_removal,
        ),
        PassSpec(
            decls.UNBIND_HIDDEN_TYPE_FIELDS,
            lambda self, s: self.__unbind_hidden_type_fields(s),
            checked_models=_prepared_models,
        ),
        PassSpec(
            decls.UPDATE_TYPE_ALIASES,
            lambda self, s: self.__update_module_type_aliases(s),
            checked_models=_prepared_models,
        ),
        PassSpec(
            decls.COLLECT_LIVE_MODELS,
            lambda self, s: self.__collect_live_models(s),
            checked_models=_prepared_models,
        ),
        PassSpec(
            decls.SET_VALIDATE_DEFAULT_ON_FIELDS,
            lambda self, s: self.__set_validate_default_on_fields(s.live_models, can_retain_cache=s.can_retain_cache),
            checked_models=lambda _, s: s.live_models,
        ),
        PassSpec(
            decls.ASSIGN_FORWARD_REFERENCE_FIELDS,
            lambda self, s: self.__assign_forward_reference_fields(s.model_list, can_retain_cache=s.can_retain_cache),
            when=lambda self, s: self.use_annotated and s.config.use_deferred_annotations,
            checked_models=_prepared_models,
        ),
        PassSpec(
            decls.WARN_REQUIRED_ASSIGNMENTS_AFTER_DEFAULTS,
            lambda self, s: self.__warn_required_assignments_after_defaults(s.live_models),
            checked_models=lambda _, s: s.live_models,
        ),
        PassSpec(
            decls.CLEAR_MODULE_IMPORTS_CACHE,
            lambda _, s: _clear_model_imports_cache(s.model_list),
            when=lambda _, s: not s.can_retain_cache,
            checked_models=_prepared_models,
        ),
    )
    """The render preparation of one output module, after the decision phase: constructor order, then the rest."""

    __FINALIZE_SEGMENTS: ClassVar[tuple[StepLike[Parser[Any, Any], _FinalizeState], ...]] = (
        Segment(
            "finalize_render_preparation",
            Phase.RENDER_PREPARATION,
            _same_state,
            (
                PassSpec(decls.COLLECT_ALL_MODELS, lambda self, s: self.__collect_all_models(s)),
                PassSpec(
                    decls.PREPARE_TYPE_ALIAS_FIELDS,
                    lambda self, s: self.__prepare_type_alias_fields(s.contexts, s.unused_models),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.FINALIZE_TYPE_ALIAS_USERS,
                    lambda self, s: self.data_model_type.finalize_type_alias_users(s.all_models),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.MARK_SET_ITEM_MODELS_HASHABLE,
                    lambda self, s: self.__mark_set_item_models_hashable(s.all_models),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.FINALIZE_STRUCTURED_IMPORTS,
                    lambda self, s: self._finalize_structured_imports(s.contexts),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.CLEAR_DEFAULT_FACTORY_IMPORT_CACHES,
                    lambda self, s: self.__clear_default_factory_import_caches(s),
                    when=lambda self, _: self.use_default_factory_for_optional_nested_models,
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.FINALIZE_SEQUENCE_INTERFACE,
                    lambda self, s: self.__finalize_sequence_interface(s),
                    when=lambda self, _: self.use_root_model_sequence_interface,
                    checked_models=_all_models,
                ),
            ),
            enters_phase=False,
        ),
        Segment(
            "imports",
            Phase.IMPORTS,
            _same_state,
            (
                PassSpec(
                    decls.SNAPSHOT_MODEL_IMPORTS,
                    lambda self, s: self.__snapshot_model_imports(s),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.APPEND_MODEL_IMPORTS,
                    lambda self, s: self.__append_model_imports(s),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.REMOVE_UNUSED_MODELS,
                    lambda self, s: self.__remove_unused_models(s),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.VALIDATE_EXPLICIT_FIELD_ALIASES,
                    lambda self, s: self.__validate_explicit_field_aliases(s),
                    when=lambda self, _: bool(self.config.aliases),
                ),
                PassSpec(
                    decls.PREPARE_SCHEMA_RUNTIME_VALIDATION_MODULE_CODE,
                    lambda self, s: self._prepare_schema_runtime_validation_module_code(s.contexts),
                    when=lambda self, _: self.generate_schema_validators,
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.SYNC_SCHEMA_RUNTIME_VALIDATION_MODULE_IMPORTS,
                    lambda self, s: self._sync_schema_runtime_validation_module_imports(s.contexts, s.model_imports),
                    when=lambda self, _: self.generate_schema_validators,
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.MERGE_RUNTIME_EXPRESSION_IMPORTS,
                    lambda self, s: self._merge_runtime_expression_imports(s.contexts, s.model_imports),
                    when=lambda self, _: self._has_runtime_expressions,
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.REMOVE_UNUSED_IMPORTS,
                    lambda self, s: self.__remove_unused_imports(s),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.RESOLVE_MODULE_IMPORT_CONFLICTS,
                    lambda self, s: self.__resolve_module_import_conflicts(s),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.BIND_MODULE_FIELD_NAMES,
                    lambda self, s: self.__bind_module_field_names(s),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.CHANGE_IMPORTED_MODEL_NAME,
                    lambda self, s: self.__change_module_imported_model_names(s),
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.NAME_FINAL_CLASSES_OF_ADDITIONAL_PROPERTIES,
                    lambda self, s: self.__name_final_classes_of_additional_properties(s),
                    when=lambda _, s: s.collect_additional_properties_models(),
                    checked_models=lambda _, s: s.additional_properties_models,
                ),
                PassSpec(
                    decls.REFRESH_SEQUENCE_INTERFACE_AFTER_RENAMES,
                    lambda self, s: self.__refresh_sequence_interface_after_renames(s),
                    when=lambda self, s: self.use_root_model_sequence_interface and s.renamed_models,
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.REPLAN_SCHEMA_VALIDATORS_AFTER_RENAMES,
                    lambda self, s: self.__replan_schema_validators_after_renames(s),
                    when=lambda self, s: self.generate_schema_validators and s.renamed_models,
                    checked_models=_all_models,
                ),
                PassSpec(
                    decls.REMAP_IMPORT_OVERRIDES,
                    lambda self, s: self.__remap_import_overrides(s),
                    when=lambda self, _: self._import_overrides is not None,
                ),
                PassSpec(
                    decls.REJECT_UNAVAILABLE_IMPORTS,
                    lambda self, s: self._reject_unavailable_imports(s.contexts),
                    when=lambda self, _: bool(self._unavailable_imports),
                ),
            ),
            enter=Boundary.RENDER_READY,
        ),
    )
    """The segments of ``_finalize_modules``: the render preparation over every module, then B2 and imports."""

    __PIPELINE: ClassVar[tuple[StepLike[Parser[Any, Any], _PipelineRun], ...]] = (
        Segment(
            "parse",
            Phase.PARSE,
            _as_settle_run,
            (
                PassSpec(
                    decls.REFRESH_CUSTOM_TEMPLATE_PATHS,
                    lambda self, _: _refresh_custom_template_paths(cast("Path", self.custom_template_dir)),
                    when=lambda self, _: self.custom_template_dir is not None,
                ),
                PassSpec(
                    decls.SET_TYPED_EXTRA_ANNOTATION_MODE,
                    lambda self, run: self._set_typed_extra_annotation_mode(
                        use_deferred_annotations=self._uses_deferred_annotations(
                            run.args.with_import, run.args.disable_future_imports
                        )
                    ),
                ),
                PassSpec(decls.PARSE_RAW, lambda self, run: self.__parse_raw(run)),
                PassSpec(decls.INDEX_CONST_NARROWED_VALUES, lambda self, run: self.__index_const_narrowed_values(run)),
            ),
            enters_phase=False,
        ),
        Segment(
            "settle",
            Phase.SETTLE,
            _as_settle_run,
            (
                PassSpec(decls.SETTLE_YIELDING_NAMES, lambda self, run: self.__settle_yielding_names(run)),
                PassSpec(decls.REPORT_PARSE_DIAGNOSTICS, lambda self, run: self.__report_parse_diagnostics(run)),
                PassSpec(
                    decls.WARN_ABOUT_UNMATCHED_MODEL_NAME_KEYS,
                    lambda self, _: self.__warn_about_unmatched_model_name_keys(),
                    when=lambda _, run: not run.suppress_parse_warnings,
                ),
                PassSpec(decls.PREPARE_PARSE_CONFIG, lambda self, run: self.__prepare_run_parse_config(run)),
                PassSpec(decls.SORT_DATA_MODELS, lambda self, run: self.__sort_data_models(run)),
                PassSpec(
                    decls.RECORD_SOURCE_REFERENCE_PATHS,
                    lambda self, run: self.__record_source_reference_paths(run),
                    when=lambda _, run: run.args.collect_model_metadata,
                ),
                PassSpec(
                    decls.SORT_BASE_CLASSES_FOR_MRO,
                    lambda self, run: sort_base_classes_for_mro(run.sorted_data_models, self.generation_store),
                ),
            ),
            enter=Boundary.PARSED,
        ),
        Segment(
            "structure",
            Phase.STRUCTURE,
            _as_structured_run,
            (HookCall("build_module_structure", lambda self, run: self.__build_structure(run), __STRUCTURE_BODY),),
            scope_name="Parser._build_module_structure",
        ),
        Segment(
            "output_setup",
            Phase.STRUCTURE,
            _as_structured_run,
            (
                PassSpec(
                    decls.CONFIGURE_CODE_FORMATTER,
                    lambda self, run: self.__configure_code_formatter(run),
                    when=lambda _, run: bool(run.args.format_),
                ),
                PassSpec(
                    decls.DETECT_STANDARD_GENERATION_TEMPLATES,
                    lambda self, run: self.__detect_standard_generation_templates(run),
                ),
            ),
            enters_phase=False,
        ),
        Segment(
            "plan",
            Phase.PLAN,
            _as_structured_run,
            (
                PassSpec(decls.RESET_PER_RUN_STATE, lambda self, run: self.__reset_per_run_state(run)),
                PassSpec(
                    decls.FLATTEN_CONFLICTING_BASE_LAYOUTS,
                    lambda self, run: self.__flatten_conflicting_base_layouts(run.module_models),
                ),
                PassSpec(
                    decls.UNCONSTRAIN_UNION_MEMBERS,
                    lambda self, run: self.__unconstrain_union_members(run),
                    when=lambda self, _: self.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR,
                ),
                PassSpec(
                    decls.SET_CIRCULAR_ROOT_MODEL_PATHS,
                    lambda self, run: self.__set_circular_root_model_paths(
                        run.module_models, require_dict_key_edge=not self.run_context.preserve_circular_root_models
                    ),
                    when=lambda self, _: self.collapse_root_models,
                ),
                PassSpec(decls.RECORD_EMITTING_MODULES, lambda self, run: self.__record_emitting_modules(run)),
            ),
            scope_name="Parser.parse",
        ),
        Segment(
            "modules",
            Phase.MODULES,
            _as_modules_run,
            (
                HookCall(
                    "process_single_module",
                    lambda self, run: self.__process_each_module(run),
                    __MODULE_BODY,
                    per_module=True,
                ),
            ),
        ),
        Segment(
            "cross_module",
            Phase.CROSS_MODULE,
            _as_finish_run,
            (
                PassSpec(
                    decls.TAG_FIELDS_OF_TAGGED_BASES,
                    lambda self, run: self.__tag_fields_of_tagged_bases(run),
                    when=lambda self, _: self.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR,
                    checked_models=lambda _, run: _context_models(run.contexts),
                ),
                PassSpec(
                    decls.WARN_ENUMS_ADMITTING_OTHER_VALUES,
                    lambda self, _: self.__warn_enums_admitting_other_values(),
                ),
                PassSpec(
                    decls.IMPORT_REMOVED_REUSE_CANONICALS,
                    lambda self, run: self.__import_removed_reuse_canonicals(
                        run.contexts,
                        internal_modules=run.internal_modules,
                        model_path_to_module_name=run.model_path_to_module_name,
                    ),
                ),
                PassSpec(
                    decls.IMPORT_RENAMED_TYPES,
                    lambda self, run: self.__import_renamed_types(
                        run.contexts,
                        internal_modules=run.internal_modules,
                        model_path_to_module_name=run.model_path_to_module_name,
                    ),
                ),
                PassSpec(
                    decls.IMPORT_ENUM_MEMBER_LITERAL_CLASSES,
                    lambda self, run: self.__import_enum_member_literal_classes(
                        run.contexts,
                        internal_modules=run.internal_modules,
                        model_path_to_module_name=run.model_path_to_module_name,
                    ),
                ),
            ),
            scope_name="Parser.parse",
        ),
        Segment(
            "generic_base_class",
            Phase.CROSS_MODULE,
            _as_finish_run,
            (
                PassSpec(
                    decls.APPLY_GENERIC_BASE_CLASS, lambda self, run: self.__apply_generic_base_class(run.contexts)
                ),
            ),
            enters_phase=False,
            scope_name="Parser.parse",
        ),
        Segment(
            "decide",
            Phase.DECIDE,
            _as_finish_run,
            (DecisionPhase("decision_passes", lambda runner, self, run: self.__decide(runner, run)),),
            enter=Boundary.FACTS_FINAL,
            scope_name="Parser.parse",
        ),
        Segment(
            "render_preparation",
            Phase.RENDER_PREPARATION,
            _as_finish_run,
            (
                ForEachModule(
                    "prepare_module_rendering",
                    lambda self, _: self._prepared_modules,
                    __MODULE_RENDER_PREPARATION_BODY,
                ),
                PassSpec(
                    decls.FIX_TAG_OPT_OUT_FIELD_ORDERING,
                    lambda self, run: self.__fix_tag_opt_out_field_ordering(
                        run.opted_out, _context_models(run.contexts)
                    ),
                    when=lambda self, _: self.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR,
                    checked_models=lambda _, run: _context_models(run.contexts),
                ),
                HookCall(
                    "finalize_modules",
                    lambda self, run: self._finalize_modules(
                        run.contexts, run.unused_models, run.model_to_module_models, run.module_to_import
                    ),
                    __FINALIZE_SEGMENTS,
                ),
            ),
            enter=Boundary.DECIDED,
            scope_name="Parser.parse",
        ),
        Segment(
            "finish",
            Phase.IMPORTS,
            _as_finish_run,
            (
                PassSpec(
                    decls.RECORD_GENERATED_CLASS_NAMES,
                    lambda self, run: self.__record_generated_class_names(run),
                    when=lambda _, __: collecting_class_names(),
                ),
                PassSpec(
                    decls.RECORD_MODEL_PLACEMENT,
                    lambda self, run: self.__record_model_placement(run.contexts, bool(run.internal_modules)),
                    when=lambda self, _: self.typed_nullable_maps or self.run_context.untyped_nullable_maps,
                ),
                PassSpec(
                    decls.CHECK_MODEL_INHERITANCE,
                    lambda self, run: self._check_model_inheritance(run.contexts),
                ),
                PassSpec(
                    decls.WARN_ABOUT_DROPPED_CONSTRAINTS,
                    lambda self, run: self.__warn_about_dropped_constraints(run.contexts),
                ),
                PassSpec(decls.WARN_ABOUT_DECIMAL_DEFAULTS, lambda self, _: self.__warn_about_decimal_defaults()),
                PassSpec(
                    decls.WARN_ABOUT_VARIANT_DISCRIMINATOR_TAGS,
                    lambda self, _: self.__warn_about_variant_discriminator_tags(),
                ),
                PassSpec(
                    decls.WARN_ABOUT_MSGSPEC_DECODING,
                    lambda self, run: self.__warn_about_msgspec_decoding(run.contexts),
                ),
                PassSpec(
                    decls.SET_NESTED_MODEL_DEFAULT_FACTORY_METADATA,
                    lambda self, run: self._set_nested_model_default_factory_metadata(
                        run.contexts, self._recursion_facts["require_update"]
                    ),
                    when=lambda self, _: self.use_default_factory_for_optional_nested_models,
                ),
                PassSpec(decls.RECORD_PYDANTIC_FLOOR, lambda self, run: self.__record_pydantic_floor(run.contexts)),
                PassSpec(decls.ADD_ROOT_INIT_RESULT, lambda self, run: self.__add_root_init_result(run)),
            ),
            enters_phase=False,
            scope_name="Parser.parse",
        ),
        Segment(
            "render",
            Phase.RENDER,
            _as_finish_run,
            (PassSpec(decls.RENDER_MODULES, lambda self, run: self.__render(run)),),
            scope_name="Parser.__render_modules",
        ),
    )
    """The passes of ``parse()``, in execution order."""

    def __flatten_conflicting_base_layouts(self, module_models: ModuleModels) -> None:
        """Make the models whose generated bases the output cannot combine declare every field without them.

        Bases are handled before the models inheriting from them, as their own fields then give the layout. The
        layouts are seen as the processing of each module may leave them: a discriminator field may become a msgspec
        tag, which takes no slot, and reuse may make a duplicate an empty subclass of the model it duplicates, whose
        layout it then has. A model that this processing leaves conflicting keeps failing the final check of the
        output. A model that a type override replaces has a layout the generator does not know, as a configured base
        class has.
        """
        if not self.data_model_type.FLATTENS_CONFLICTING_BASE_LAYOUTS:
            return
        models = [model for _, models in module_models for model in models]
        if not (joins := [model for model in models if len(model.base_classes) > 1]):
            return
        duplicates = self.__find_possible_reuse_duplicates(module_models)
        layout_models: dict[int, DataModel] = {}
        pending = joins.copy()
        while pending:
            if id(model := pending.pop()) in layout_models:
                continue
            layout_models[id(model)] = model
            pending.extend(_find_base_classes(model))
            if (duplicated := duplicates.get(id(model))) is not None:
                pending.append(duplicated)
        tag_names = self.__find_possible_tag_names(models, list(layout_models.values()))
        overrides = self._model_type_override_imports

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
                model.flatten_conflicting_base_layouts(view, self.__flatten_base_layouts)
            elif not complete and id(model) not in visited:
                visited.add(id(model))
                visits.append((model, True))
                visits.extend((base, False) for base in reversed(_find_base_classes(model)))

    def __flatten_base_layouts(self, model: DataModel) -> None:
        """Make a model declare the fields of its generated bases and drop them.

        The required names the model and its bases take from their own bases are resolved first, as only the bases
        give their fields. A field becomes required as a required name makes it.
        """
        override_required_field(self, linearize_data_models([model])[::-1])
        own_fields = {id(field) for field in model.fields}

        def adopt(field: DataModelFieldBase, required: bool) -> DataModelFieldBase:  # noqa: FBT001
            if id(field) not in own_fields:
                field = self._copy_model_field(field)
            if required and not field.required:
                self.generation_store.require_field(
                    field,
                    use_default_with_required=self.apply_default_values_for_required_fields and field.has_default,
                )
            return field

        fields = model.flatten_base_layouts(adopt)
        with self.generation_store.defer_refresh():
            for base_class in model.base_classes:
                self.generation_store.detach_data_type_ref(base_class)
            self.generation_store.set_fields(model, fields)
            self.generation_store.reset_base_classes(model)

    def __find_possible_tag_names(
        self, models: list[DataModel], layout_models: list[DataModel]
    ) -> dict[int, frozenset[str]]:
        """Return, by model whose layout counts, the names of the fields that the discriminator step may make tags.

        It tags the variants that discriminated unions list, however nested or aliased, or that their mappings name,
        including those of the unions collapsed reuse removed, by the property name or the field name it resolves to.
        A struct inheriting from a tagged struct takes its tag field as a tag too. The mapped paths are indexed once
        by every key a model path can match them by.
        """
        listed: defaultdict[int, set[str]] = defaultdict(set)
        mapped: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
        for model in chain(models, chain.from_iterable(self._collapsed_reuse_models.values())):
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
                    self.model_resolver.get_valid_field_name_and_alias(
                        field_name=property_name, model_type=self.field_name_model_type, class_name=class_name
                    )[0],
                )
            }
        return {
            id(model): frozenset(
                chain.from_iterable(variant_names.get(id(ancestor), ()) for ancestor in linearize_data_models([model]))
            )
            for model in layout_models
        }

    def __find_possible_reuse_duplicates(self, module_models: ModuleModels) -> dict[int, DataModel]:
        """Return, by model, the earlier model of its module that reuse may find it a duplicate of.

        Reuse compares rendered models, so a duplicate has the type, the bases and the field names of the model it
        duplicates.
        """
        duplicates: dict[int, DataModel] = {}
        if not self.reuse_model or self.reuse_scope == ReuseScope.Tree:
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

    def __record_model_placement(
        self,
        contexts: list[ModuleContext],
        has_internal_modules: bool,  # noqa: FBT001
    ) -> None:
        """Record where models other than typed map values are written, and whether the typed values could move them.

        Typed map values move other models only through a module of their own, an internal module of an import
        cycle, or a module shared by reused models, and keep a model only as a nullable map they make recursive,
        which ``--collapse-root-models`` would otherwise collapse.
        """
        typed_map_values = self._typed_map_value_reference_ids
        placement = {ctx.module: ctx.models for ctx in contexts}
        self.model_placement = {
            module: frozenset(model.class_name for model in models if id(model.reference) not in typed_map_values)
            for module, models in placement.items()
        }
        self.typed_map_values_may_move_models = (
            has_internal_modules
            or (self.reuse_model and self.reuse_scope == ReuseScope.Tree)
            or any(
                models and all(id(model.reference) in typed_map_values for model in models)
                for models in placement.values()
            )
            or (
                self.collapse_root_models
                and any(
                    id(model.reference) in self.typed_nullable_map_reference_ids
                    for models in placement.values()
                    for model in models
                )
            )
        )

    def __render_modules(  # noqa: PLR0913
        self,
        results: dict[ModulePath, Result],
        *,
        contexts: list[ModuleContext],
        sorted_data_models: SortedDataModels,
        model_identities: Mapping[DataModel, str] | None,
        config: ParseConfig,
        forwarder_map: ForwarderMap,
        require_update: Collection[ModelKey],
    ) -> str | dict[tuple[str, ...], Result]:
        """Render the shared result mapping and apply final output-only transformations."""
        future_imports = self._import_ledger.extract_future(self.imports, _FUTURE_IMPORTS)
        future_imports_str = str(future_imports)
        if forwarder_map:
            forwarder_map = _forward_defined_models(contexts, forwarder_map)

        # Export depth and collision prefixes must follow the final package layout.
        if (
            self.treat_dot_as_module
            and config.all_exports_scope is not None
            and any(_expand_export_module_path(ctx.module) != ctx.module for ctx in contexts)
        ):
            contexts = [ctx._replace(module=_expand_export_module_path(ctx.module)) for ctx in contexts]

        for ctx in contexts:
            result = self._generate_module_output(
                ctx, config, contexts, forwarder_map, require_update, future_imports_str
            )
            if result is not None:
                results[ctx.module] = result

        if config.all_exports_scope is not None and not self.treat_dot_as_module:
            self._generate_empty_init_exports(results, contexts, config, future_imports_str)

        self._inspect_invalid_dotted_stdout(contexts, sorted_data_models, config, results)

        if model_identities is not None:
            self.model_metadata, self.model_metadata_identities = self._build_model_metadata(contexts, model_identities)
        else:
            self.model_metadata, self.model_metadata_identities = None, ()

        if [*results] == [("__init__.py",)]:
            single_result = results["__init__.py",]
            return single_result.body

        results = {
            _normalize_result_module_path(module, treat_dot_as_module=self.treat_dot_as_module): result
            for module, result in results.items()
        }
        if self.treat_dot_as_module:
            results = self._result_modules_postprocessor(results, empty_init=config.all_exports_scope is not None)
            if config.all_exports_scope is not None:
                self._generate_empty_init_exports(results, contexts, config, future_imports_str)
        return results
