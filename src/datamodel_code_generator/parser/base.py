"""Abstract base parser and utilities for schema parsing.

Provides the Parser abstract base class that defines the parsing algorithm,
along with helper functions for model sorting, import resolution, and
code generation.
"""

from __future__ import annotations

import builtins
import contextlib
import operator
import os.path
import re
import sys
from abc import ABC, abstractmethod
from collections import Counter, OrderedDict, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from functools import cache, partial
from itertools import chain, groupby
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

from pydantic import BaseModel, ConfigDict
from typing_extensions import Unpack

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
)
from datamodel_code_generator._format_types import Formatter, PythonVersion
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator._internal_utils import (
    HashableComparable,
    get_most_of_parent,
)
from datamodel_code_generator._parser_context import ParserSourceContext
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
from datamodel_code_generator._template_data import copy_extra_template_data
from datamodel_code_generator.enums import DefaultValueType, StrictTypes
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATIONS,
    IMPORT_LITERAL,
    IMPORT_OPTIONAL,
    IMPORT_UNION,
    Import,
    Imports,
)
from datamodel_code_generator.model.base import (
    ALL_MODEL,  # noqa: F401  # Preserve the established parser.base export.
    GENERIC_BASE_CLASS_NAME,
    GENERIC_BASE_CLASS_PATH,
    UNDEFINED,
    BaseClassDataType,
    ConstraintsBase,
    DataModel,
    DataModelFieldBase,
    _find_base_classes,
    _refresh_custom_template_paths,
    _set_nested_model_default_factory_order,
    get_inherited_fields,
    get_resolve_reference_action_capabilities,
    linearize_data_models,
    sort_data_models_for_mro,
)
from datamodel_code_generator.model.enum import Enum, Member, get_raw_enum_member_value
from datamodel_code_generator.model.enum import escape_characters as _enum_escape_characters
from datamodel_code_generator.model.output import (
    _expression_names,  # noqa: F401  # Preserve the existing parser helper export.
    _model_field_name_collisions,
    prepare_output_model_config,
)
from datamodel_code_generator.model.type_alias import TypeAliasBase, TypeStatement
from datamodel_code_generator.parser._scc import find_circular_sccs, strongly_connected_components
from datamodel_code_generator.parser.generation import GenerationIndex, GenerationStore, set_model_base_classes
from datamodel_code_generator.parser.schema_version import SchemaFeaturesT
from datamodel_code_generator.python_literal import (
    PythonCode,
    PythonRuntimeExpression,
    _semantic_value_text,
    rewrite_runtime_expressions,
    rewrite_runtime_imports,
    runtime_expression_imports,
)
from datamodel_code_generator.reference import (
    _ALIAS_RESOLUTION_CLASS_NAME_KEY,
    ModelResolver,
    ModelType,
    Reference,
    split_module_name,
)
from datamodel_code_generator.types import (
    ANY,
    NONE,
    DataType,
    DataTypeManager,
    DefaultValueDescriptor,
    DefaultValueRecipe,
)
from datamodel_code_generator.util import camel_to_snake, record_watch_dependency

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.format import CodeFormatter
    from datamodel_code_generator.http import _HTTPFetchSession
    from datamodel_code_generator.model_metadata import GeneratedModelMetadata, ModelFieldMetadata, ModelMetadata


# Preserve the existing parser.base export while sharing one canonical escape table.
escape_characters = _enum_escape_characters

ParserConfigT = TypeVar("ParserConfigT", bound="ParserConfig")
_ConstructorFieldAdjustment: TypeAlias = Literal["assignment", "keyword_only"]


# Keep these as module-name checks so non-pydantic-v2 outputs do not import the
# pydantic_v2 generator package and its runtime feature gates.
_PYDANTIC_V2_BASE_MODEL_MODULE: Final = "datamodel_code_generator.model.pydantic_v2.base_model"
_MODEL_MODULE_PREFIX: Final = "datamodel_code_generator.model."
_CLASS_NAME_SEPARATOR_PATTERN: Final = re.compile(r"[^A-Za-z0-9]+")
_STRING_OR_IDENTIFIER_PATTERN: Final = re.compile(
    r"""'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|"""
    r"""^[ \t]*[A-Za-z_]\w*(?=:)|([A-Za-z_]\w*)""",
    re.MULTILINE,
)
_TOP_LEVEL_FUTURE_IMPORT_PATTERN: Final = re.compile(r"(?m)^from __future__ import ")
_TOP_LEVEL_RELATIVE_IMPORT_PATTERN: Final = re.compile(r"(?m)^from \.")

_DEFERRED_INHERITED_CLASS_KEY: Final = "_deferred_inherited_class"
_DEFERRED_INHERITED_FIELD_KEY: Final = "_deferred_inherited_field"
_DEFERRED_INHERITED_TYPE_KEY: Final = "_deferred_inherited_type"
_RAW_SCHEMA_DEFAULT_KEY: Final = "_raw_schema_default"
_RAW_SCHEMA_DEFAULT_UNDEFINED: Final = object()
_SOURCE_REFERENCE_PATH_KEY: Final = "_source_reference_path"
_DECIMAL_WARNING_EXAMPLE_LIMIT: Final = 5


@dataclass(frozen=True, slots=True)
class _InheritedTypeModifiers:
    """Compact deferred state for a partial inherited field."""

    excludes_null: bool
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

_BUILTIN_NAMES: frozenset[str] = frozenset(name for name in builtins.__dict__ if not name.startswith("_"))
_BUILTIN_NAMES_INTRODUCED_IN: dict[PythonVersion, frozenset[str]] = {
    PythonVersion.PY_311: frozenset({"BaseExceptionGroup", "ExceptionGroup"}),
    PythonVersion.PY_313: frozenset({"PythonFinalizationError"}),
}
_BUILTIN_CONTAINER_COLLISION_FLAGS: dict[str, str] = {
    "list": "is_list",
    "dict": "is_dict",
    "set": "is_set",
    "frozenset": "is_frozen_set",
    "tuple": "is_tuple",
}


def _get_builtin_names_for_target(target_python_version: PythonVersion) -> frozenset[str]:
    builtin_names = set(_BUILTIN_NAMES)
    target_key = target_python_version.version_key

    for introduced_version, names in _BUILTIN_NAMES_INTRODUCED_IN.items():
        if target_key >= introduced_version.version_key:
            builtin_names.update(names)
        else:
            builtin_names.difference_update(names)

    return frozenset(builtin_names)


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

ModulePath: TypeAlias = tuple[str, ...]
ModuleModels: TypeAlias = list[tuple[ModulePath, list[DataModel]]]
ForwarderMap: TypeAlias = dict[ModulePath, tuple[ModulePath, list[tuple[str, str]]]]


def _module_key(data_model: DataModel, module_split_mode: ModuleSplitMode | None) -> ModulePath:
    if module_split_mode == ModuleSplitMode.Single:
        return (*data_model.module_path, camel_to_snake(data_model.class_name))
    return tuple(data_model.module_path)


def _group_models_by_module(
    data_models: Iterable[DataModel], module_split_mode: ModuleSplitMode | None
) -> ModuleModels:
    """Group models by output module and include required empty package levels."""

    def sort_key(data_model: DataModel) -> tuple[int, ModulePath]:
        key = _module_key(data_model, module_split_mode)
        return (len(key), key)

    grouped_models = groupby(
        sorted(data_models, key=sort_key, reverse=True),
        key=lambda model: _module_key(model, module_split_mode),
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


def _index_module_models(
    module_models: ModuleModels, module_split_mode: ModuleSplitMode | None
) -> tuple[dict[DataModel, tuple[ModulePath, list[DataModel]]], dict[str, str]]:
    """Build model lookups for already-grouped output modules."""
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]] = {}
    model_path_to_module_name: dict[str, str] = {}
    for module, models in module_models:
        for model in models:
            model_to_module_models[model] = module, models
            if module_split_mode == ModuleSplitMode.Single:
                model_path_to_module_name[model.path] = ".".join(module)
    return model_to_module_models, model_path_to_module_name


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


def _bind_module_field_names(models: list[DataModel], imports: Imports) -> None:
    """Allocate aliases only where a property hides an emitted expression name."""
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
    bindings: dict[str, str] = {}
    for name, (from_, imported_name) in shadowable_imports.items():
        if name not in collisions:
            continue
        alias = f"{name}_aliased"
        suffix = 1
        while alias in reserved_names:
            alias = f"{name}_aliased_{suffix}"
            suffix += 1
        reserved_names.add(alias)
        aliased_import = Import(from_=from_, import_=imported_name, alias=alias)
        bindings[name] = alias
        if from_ == "builtins":
            imports.append(aliased_import)
        else:
            imports.apply_alias(aliased_import)
    for model in models:
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


class _KeepModelOrderDeps(NamedTuple):
    strong: ModelDeps
    all: ModelDeps


class _KeepModelOrderComponents(NamedTuple):
    components: Components
    comp_of: ComponentOf


class _InheritedConstructorInfo(NamedTuple):
    required_assignment_names: frozenset[str]
    ordering_conflicts: frozenset[str]


class _ConstructorFieldPolicy(NamedTuple):
    has_assignment: Callable[[DataModelFieldBase], bool]
    classify_default: Callable[[DataModelFieldBase], tuple[bool, bool]]
    participates: Callable[[DataModelFieldBase], bool]


@dataclass(frozen=True, slots=True)
class ParserRunContext:
    """Immutable parser settings scoped to one facade-managed run."""

    diagnostic_source_path: Path | None = None
    prefetched_source: tuple[Path, bytes] | None = None
    formatter_cwd: Path | None = None
    preserve_circular_root_models: bool = False
    suppress_parse_warnings: bool = False


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


def _collect_keep_model_order_deps(
    model: DataModel,
    *,
    model_names: ModelNames,
    imported: ModelNames,
    pydantic_v2_root_model_type: type[DataModel] | None,
    use_deferred_annotations: bool,
) -> tuple[set[ModelName], set[ModelName]]:
    """Collect (strong_deps, all_deps) used by keep_model_order sorting.

    - strong_deps: base class references (within-module, non-imported)
    - all_deps: base class refs + (optionally) field refs (within-module, non-imported)
    """
    class_name = model.class_name
    base_class_refs = {b.reference.short_name for b in model.base_classes if b.reference}
    field_refs = {t.reference.short_name for f in model.fields for t in f.data_type.all_data_types if t.reference}

    if (
        use_deferred_annotations
        and not isinstance(model, TypeAliasBase)
        and not _is_pydantic_v2_root_model(model, pydantic_v2_root_model_type)
    ):
        field_refs = set()

    strong = {r for r in base_class_refs if r in model_names and r not in imported and r != class_name}
    deps = {r for r in (base_class_refs | field_refs) if r in model_names and r not in imported and r != class_name}
    return strong, deps


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
    for model in models:
        strong, deps = _collect_keep_model_order_deps(
            model,
            model_names=model_names,
            imported=imported,
            pydantic_v2_root_model_type=pydantic_v2_root_model_type,
            use_deferred_annotations=use_deferred_annotations,
        )
        strong_deps[model.class_name] = strong
        all_deps[model.class_name] = deps
    return _KeepModelOrderDeps(strong=strong_deps, all=all_deps)


def _build_keep_model_order_components(
    all_deps: ModelDeps,
    order_index: OrderIndex,
) -> _KeepModelOrderComponents:
    graph: ClassGraph = {(name,): {(dep,) for dep in deps} for name, deps in all_deps.items()}
    sccs = strongly_connected_components(graph)
    components: Components = [sorted((node[0] for node in scc), key=order_index.__getitem__) for scc in sccs]
    components.sort(key=lambda members: min(order_index[n] for n in members))
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
) -> list[ModelName]:
    ordered_names: list[ModelName] = []
    for component_id in ordered_comp_ids:
        members = components[component_id]
        if len(members) > 1:
            strong_edges: dict[ModelName, set[ModelName]] = {n: set() for n in members}
            member_set = set(members)
            for base in members:
                derived_members = {member for member in members if base in strong_deps.get(member, set()) & member_set}
                strong_edges[base].update(derived_members)
            members = stable_toposort(members, strong_edges, key=order_index.__getitem__)
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
    comps = _build_keep_model_order_components(deps.all, order_index)
    comp_edges = _build_keep_model_order_component_edges(deps.all, comps.comp_of, len(comps.components))
    ordered_comp_ids = _build_keep_model_order_component_order(comps.components, comp_edges, order_index)
    ordered_names = _build_keep_model_ordered_names(ordered_comp_ids, comps.components, deps.strong, order_index)
    models[:] = [model_by_name[name] for name in ordered_names]


def _sort_internal_module_models(
    models: list[DataModel],
    pydantic_v2_root_model_type: type[DataModel] | None,
) -> list[DataModel]:
    """Order models moved to _internal.py so runtime base classes are defined first."""
    model_paths = {model.path for model in models}
    order_index = {model.path: index for index, model in enumerate(models)}
    edges: dict[str, set[str]] = {model.path: set() for model in models}

    def add_dependency(model: DataModel, reference_path: str | None) -> None:
        if reference_path in model_paths and reference_path != model.path:
            edges[reference_path].add(model.path)

    for model in models:
        for base_class in model.base_classes:
            add_dependency(model, base_class.reference.path if base_class.reference else None)
        if _is_pydantic_v2_root_model(model, pydantic_v2_root_model_type):
            for field in model.fields:
                for data_type in field.data_type.all_data_types:
                    add_dependency(model, data_type.reference.path if data_type.reference else None)

    sorted_paths = stable_toposort(list(order_index), edges, key=order_index.__getitem__)
    model_by_path = {model.path: model for model in models}
    return [model_by_path[path] for path in sorted_paths]


SPECIAL_PATH_FORMAT: str = "#-datamodel-code-generator-#-{}-#-special-#"


def get_special_path(keyword: str, path: list[str]) -> list[str]:
    """Create a special path marker for internal reference tracking."""
    return [*path, SPECIAL_PATH_FORMAT.format(keyword)]


def dump_templates(templates: list[DataModel]) -> str:
    """Join model templates into a single code string."""
    return "\n\n\n".join(str(m) for m in templates)


def iter_models_field_data_types(
    models: Iterable[DataModel],
) -> Iterator[tuple[DataModel, DataModelFieldBase, DataType]]:
    """Yield (model, field, data_type) for all models, fields, and nested data types."""
    for model in models:
        for field in model.fields:
            for data_type in field.data_type.all_data_types:
                yield model, field, data_type


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


_PythonTypeImportKey: TypeAlias = tuple[str | None, str]


def _ordinary_field_shadow_aliases(
    models: list[DataModel],
    all_model_field_names: set[str],
) -> tuple[dict[_PythonTypeImportKey, Import], bool, bool]:
    """Run the narrow fast path and report whether structured imports exist."""
    aliases: dict[_PythonTypeImportKey, Import] = {}
    has_python_type = False
    has_runtime_expressions = False
    for model in models:
        for field in model.fields:
            if not has_runtime_expressions and field.runtime_expression_imports:
                has_runtime_expressions = True
            for data_type in field.data_type.all_data_types:
                if data_type.python_type:
                    has_python_type = True
                if not has_runtime_expressions and data_type.runtime_expression_imports:
                    has_runtime_expressions = True
                if data_type.import_ and data_type.type in all_model_field_names:
                    key = (data_type.import_.from_, data_type.import_.import_)
                    aliases.setdefault(
                        key,
                        Import(
                            from_=data_type.import_.from_,
                            import_=data_type.import_.import_,
                            alias=f"{data_type.type}_aliased",
                            reference_path=data_type.import_.reference_path,
                        ),
                    )
    return aliases, has_python_type, has_runtime_expressions


def _apply_structured_import_aliases(
    models: list[DataModel],
    aliased_imports: dict[_PythonTypeImportKey, Import],
    *,
    can_retain_cache: bool,
) -> None:
    """Rewrite every identity-carrying structured consumer of selected imports."""
    for model in models:
        changed = False
        for field in model.fields:
            changed = _alias_field_runtime_expressions(field, aliased_imports) or changed
            for data_type in field.data_type.all_data_types:
                changed = _alias_data_type_structured_imports(data_type, aliased_imports) or changed
        if _alias_additional_imports(model, aliased_imports):
            changed = True
        if changed:
            _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    for model in models:
        if _alias_base_class_imports(model, aliased_imports):
            _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
    if not can_retain_cache:
        _clear_model_imports_cache(models)


def _alias_field_runtime_expressions(
    field: DataModelFieldBase,
    aliased_imports: dict[_PythonTypeImportKey, Import],
) -> bool:
    """Rewrite a field only when its producer registered a runtime expression."""
    imports = field.runtime_expression_imports
    if not imports or (rewritten_imports := rewrite_runtime_imports(imports, aliased_imports)) is imports:
        return False
    field.default = rewrite_runtime_expressions(field.default, aliased_imports)
    field.extras = cast("dict[str, Any]", rewrite_runtime_expressions(field.extras, aliased_imports))
    field._set_runtime_expression_imports(rewritten_imports)  # noqa: SLF001
    return True


def _alias_data_type_structured_imports(
    data_type: DataType,
    aliased_imports: dict[_PythonTypeImportKey, Import],
) -> bool:
    """Apply aliases to type, annotation, and producer-registered kwargs expressions."""
    changed = False
    if (
        data_type.import_
        and (aliased_import := aliased_imports.get((data_type.import_.from_, data_type.import_.import_))) is not None
        and aliased_import is not data_type.import_
    ):
        data_type.type = aliased_import.alias
        data_type.import_ = aliased_import
        changed = True
    if data_type.python_type:
        alias_bound_python_type, render_python_type_expr = _python_type_import_alias_helpers()
        bound_type = alias_bound_python_type(data_type.python_type, aliased_imports)
        if bound_type is not data_type.python_type:
            data_type.python_type = bound_type
            data_type.type = render_python_type_expr(bound_type.expression)
            changed = True
    imports = data_type.runtime_expression_imports
    if not imports:
        return changed
    if (rewritten_imports := rewrite_runtime_imports(imports, aliased_imports)) is imports:
        return changed
    data_type.kwargs = cast("dict[str, Any]", rewrite_runtime_expressions(data_type.kwargs, aliased_imports))
    data_type._set_runtime_expression_imports(rewritten_imports)  # noqa: SLF001
    return True


@cache
def _python_type_import_alias_helpers() -> tuple[Any, Any]:
    """Load annotation alias helpers only for the structured annotation path."""
    from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415
    from datamodel_code_generator._python_type_binding import alias_bound_python_type  # noqa: PLC0415

    return alias_bound_python_type, render_python_type_expr


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


def _is_default_value_container(data_type: DataType) -> bool:
    """Return whether a data type wraps a collection default rather than one scalar."""
    return any((
        data_type.is_dict,
        data_type.is_list,
        data_type.is_set,
        data_type.is_frozen_set,
        data_type.is_mapping,
        data_type.is_sequence,
        data_type.is_tuple,
    ))


def _unwrap_default_scalar_data_type(data_type: DataType) -> DataType:
    """Unwrap aliases and nullable scalar wrappers without traversing unions or containers."""
    if data_type.reference and isinstance(data_type.reference.source, TypeAliasBase):
        data_type = _unwrap_type_alias(data_type)
    while (
        data_type.type is None
        and data_type.reference is None
        and len(data_type.data_types) == 1
        and not _is_default_value_container(data_type)
        and not data_type.literals
        and not data_type.enum_member_literals
    ):
        data_type = data_type.data_types[0]
        if data_type.reference and isinstance(data_type.reference.source, TypeAliasBase):
            data_type = _unwrap_type_alias(data_type)
    return data_type


def _resolve_default_scalar_data_type(data_type: DataType) -> DataType | None:
    """Resolve one backend-neutral scalar leaf without traversing unions or containers."""
    if _is_default_value_container(data_type):
        return None
    if data_type.reference or data_type.data_types:
        data_type = _unwrap_default_scalar_data_type(data_type)
    if (
        data_type.reference
        or data_type.data_types
        or _is_default_value_container(data_type)
        or data_type.literals
        or data_type.enum_member_literals
    ):
        return None
    return data_type


def _alias_base_class_imports(
    model: DataModel,
    aliased_imports: dict[tuple[str | None, str], Import],
) -> bool:
    """Apply aliased imports to a model's base classes."""
    changed = False
    for base_class in model.base_classes:
        if not base_class.import_:
            continue
        key = (base_class.import_.from_, base_class.import_.import_)
        if key not in aliased_imports:
            continue
        aliased_import = aliased_imports[key]
        if aliased_import is base_class.import_:
            continue
        base_class.type = aliased_import.alias
        base_class.import_ = aliased_import
        changed = True
    return changed


def _alias_additional_imports(
    model: DataModel,
    aliased_imports: dict[tuple[str | None, str], Import],
) -> bool:
    """Replace every additional import with the module's canonical identity binding."""
    changed = False
    for index, import_ in enumerate(model._additional_imports):  # noqa: SLF001
        if (
            aliased_import := aliased_imports.get((import_.from_, import_.import_))
        ) is None or aliased_import is import_:
            continue
        model._additional_imports[index] = aliased_import  # noqa: SLF001
        changed = True
    return changed


def _clear_model_imports_cache(models: Iterable[DataModel]) -> None:
    """Clear per-model imports caches after import-affecting mutations."""
    # Parser post-processing rewrites fields, data types, and aliases in bulk.
    # Clear at the end of those rewrite steps so later imports are recomputed.
    for model in models:
        model.clear_imports_cache()


def _clear_model_imports_cache_if_retained(model: DataModel, *, can_retain_cache: bool) -> None:
    """Clear a changed built-in model without invoking external cache hooks."""
    if can_retain_cache and type(model).__module__.startswith(_MODEL_MODULE_PREFIX):
        model.clear_imports_cache()


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
SortedDataModels = dict[str, DataModel]

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


def sort_data_models(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
    unsorted_data_models: list[DataModel],
    sorted_data_models: SortedDataModels | None = None,
    require_update_action_models: list[str] | None = None,
    recursion_count: int = MAX_RECURSION_COUNT,
    generation_index: GenerationIndex | None = None,
    *,
    pydantic_v2_root_model_type: type[DataModel] | None = None,
) -> tuple[list[DataModel], SortedDataModels, list[str]]:
    """Sort data models by dependency order for correct forward references."""
    if sorted_data_models is None:
        sorted_data_models = OrderedDict()

    if require_update_action_models is None:
        require_update_action_models = []
    require_update_action_model_paths = set(require_update_action_models)

    def add_require_update_action_model(model: DataModel) -> None:
        if model.is_alias:
            return
        path = model.path
        if path in require_update_action_model_paths:
            return
        require_update_action_models.append(path)
        require_update_action_model_paths.add(path)

    def get_reference_classes(model: DataModel) -> frozenset[str]:
        if generation_index is None:
            return model.reference_classes
        return generation_index.reference_classes_for_model(model)

    sorted_model_count: int = len(sorted_data_models)
    sorted_model_paths = set(sorted_data_models)

    unresolved_references: list[DataModel] = []
    for model in unsorted_data_models:
        reference_classes = get_reference_classes(model)
        if not reference_classes:
            sorted_data_models[model.path] = model
            sorted_model_paths.add(model.path)
        elif model.path in reference_classes and len(reference_classes) == 1:  # only self-referencing
            sorted_data_models[model.path] = model
            sorted_model_paths.add(model.path)
            add_require_update_action_model(model)
        elif not reference_classes - {model.path} - sorted_model_paths:  # reference classes have been resolved
            sorted_data_models[model.path] = model
            sorted_model_paths.add(model.path)
            if model.path in reference_classes:
                add_require_update_action_model(model)
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
                )
            except RecursionError:  # pragma: no cover
                pass

        # sort on base_class dependency
        seen_orderings: set[tuple[str, ...]] = set()
        while True:
            ordered_models: list[tuple[int, DataModel]] = []
            # Build lookup dict for O(1) index access instead of O(n) list.index()
            path_to_index = {m.path: idx for idx, m in enumerate(unresolved_references)}
            for model in unresolved_references:
                if _is_pydantic_v2_root_model(model, pydantic_v2_root_model_type):
                    indexes = [
                        path_to_index[ref_path]
                        for f in model.fields
                        for t in f.data_type.all_data_types
                        if t.reference and (ref_path := t.reference.path) in path_to_index
                    ]
                else:
                    indexes = [
                        path_to_index[b.reference.path]
                        for b in model.base_classes
                        if b.reference and b.reference.path in path_to_index
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

            sig = tuple(m.path for m in sorted_unresolved_models)
            if sig in seen_orderings:
                # Base-class dependency order has no fixed point (e.g. cyclic inheritance with
                # discriminators). Further iterations only permute the list; use stable order.
                unresolved_references.sort(key=lambda m: m.path)
                break

            seen_orderings.add(sig)
            unresolved_references = sorted_unresolved_models

        # circular reference
        unsorted_data_model_names = set(path_to_index.keys())
        for model in unresolved_references:
            reference_classes = get_reference_classes(model)
            unresolved_model = reference_classes - {model.path} - sorted_model_paths
            base_models = [getattr(s.reference, "path", None) for s in model.base_classes]
            update_action_parent = require_update_action_model_paths.intersection(base_models)
            if not unresolved_model:
                sorted_data_models[model.path] = model
                sorted_model_paths.add(model.path)
                if update_action_parent:
                    add_require_update_action_model(model)
                continue

            if not unresolved_model - unsorted_data_model_names:
                sorted_data_models[model.path] = model
                sorted_model_paths.add(model.path)
                add_require_update_action_model(model)
                continue

            # unresolved
            unresolved_classes = ", ".join(
                f"[class: {item.path} references: {get_reference_classes(item)}]" for item in unresolved_references
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
                    else sorted_data_models.get(base_class.reference.path)
                )
            )
            is not None
        ]
        model_order = {
            source_model.path: index for index, source_model in enumerate(sort_data_models_for_mro(source_models))
        }
        sorted_base_classes = sorted(
            base_classes,
            key=lambda base_class: model_order.get(base_class.reference.path, 0) if base_class.reference else 0,
        )
        if all(
            sorted_base_class is base_class
            for sorted_base_class, base_class in zip(sorted_base_classes, base_classes, strict=True)
        ):
            continue
        set_model_base_classes(model, sorted_base_classes, generation_store)


def relative(
    current_module: str,
    reference: str,
    *,
    reference_is_module: bool = False,
    current_is_init: bool = False,
) -> tuple[str, str]:
    """Find relative module path.

    Args:
        current_module: Current module path (e.g., "foo.bar")
        reference: Reference path (e.g., "foo.baz.ClassName" or "foo.baz" if reference_is_module)
        reference_is_module: If True, treat reference as a module path (not module.class)
        current_is_init: If True, treat current_module as a package __init__.py (adds depth)

    Returns:
        Tuple of (from_path, import_name) for constructing import statements
    """
    if current_is_init:
        current_module_path = [*current_module.split("."), "__init__"] if current_module else ["__init__"]
    else:
        current_module_path = current_module.split(".") if current_module else []

    if reference_is_module:
        reference_path = reference.split(".") if reference else []
        name = reference_path[-1] if reference_path else ""
    else:
        *reference_path, name = reference.split(".")

    if current_module_path == reference_path:
        return "", ""

    i = 0
    for x, y in zip(current_module_path, reference_path, strict=False):
        if x != y:
            break
        i += 1

    left = "." * (len(current_module_path) - i)
    right = ".".join(reference_path[i:])

    if not left:
        left = "."
    if not right:
        right = name
    elif "." in right:
        extra, right = right.rsplit(".", 1)
        left += extra

    return left, right


def is_ancestor_package_reference(current_module: str, reference: str) -> bool:
    """Check if reference is in an ancestor package (__init__.py).

    When the reference's module path is an ancestor (prefix) of the current module,
    the reference is in an ancestor package's __init__.py file.

    Args:
        current_module: The current module path (e.g., "v0.mammal.canine")
        reference: The full reference path (e.g., "v0.Animal")

    Returns:
        True if the reference is in an ancestor package, False otherwise.

    Examples:
        - current="v0.animal", ref="v0.Animal" -> True (immediate parent)
        - current="v0.mammal.canine", ref="v0.Animal" -> True (grandparent)
        - current="v0.animal", ref="v0.animal.Dog" -> False (same or child)
        - current="pets", ref="Animal" -> True (root package is immediate parent)
        - current="v0.mammal.canine", ref="Animal" -> True (root package is an ancestor)
    """
    current_path = current_module.split(".") if current_module else []
    *reference_path, _ = reference.split(".")

    if not current_path:
        return False

    # Case 1: Direct parent package (includes root package when reference_path is empty)
    # e.g., current="pets", ref="Animal" -> current_path[:-1]=[] == reference_path=[]
    if current_path[:-1] == reference_path:
        return True

    # Case 2: Deeper ancestor package (reference_path must be a proper prefix)
    # e.g., current="v0.mammal.canine", ref="v0.Animal" -> ["v0"] is prefix of ["v0","mammal","canine"]
    # An empty reference_path is the root package, which is an ancestor of every nested module.
    return len(reference_path) < len(current_path) and current_path[: len(reference_path)] == reference_path


def exact_import(from_: str, import_: str, short_name: str) -> tuple[str, str]:
    """Create exact import path to avoid relative import issues."""
    if from_ == len(from_) * ".":
        # Prevents "from . import foo" becoming "from ..foo import Foo"
        # or "from .. import foo" becoming "from ...foo import Foo"
        # when our imported module has the same parent
        return f"{from_}{import_}", short_name
    return f"{from_}.{import_}", short_name


def _resolve_exact_import(
    current_module: str,
    target_full_name: str,
    from_: str,
    import_: str,
    short_name: str,
) -> tuple[str, str]:
    """Keep package imports intact while resolving exact module imports."""
    if is_ancestor_package_reference(current_module, target_full_name):
        return from_, import_
    return exact_import(from_, import_, short_name)


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


def _has_preservable_root_constraints(model: DataModel, *, seen_models: set[int] | None = None) -> bool:
    """Return whether flattening a root wrapper would discard field-owned constraints."""
    if not model.fields:
        return False
    if seen_models is None:
        seen_models = set()
    if id(model) in seen_models:
        return False
    seen_models.add(id(model))

    root_field = model.fields[0]
    constraints = root_field.constraints
    if isinstance(constraints, ConstraintsBase) and any(
        value is not None for name, value in constraints.model_dump().items() if name != "unique_items"
    ):
        return True

    for data_type in root_field.data_type.all_data_types:
        if (
            data_type.reference
            and isinstance(source_model := data_type.reference.source, DataModel)
            and _has_preservable_root_constraints(source_model, seen_models=seen_models)
        ):
            return True
    return False


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
    excludes_null: bool = False,
) -> _InheritedTypeModifiers:
    """Compress a partial type before replacing its forward placeholder."""
    list_wrapper = (
        _copy_data_type(data_type, register_references=False)
        if not data_type.is_list and len(data_type.data_types) == 1 and data_type.data_types[0].is_list
        else None
    )
    return _InheritedTypeModifiers(
        excludes_null=excludes_null,
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


def _detach_deferred_inherited_field_parents(field: DataModelFieldBase) -> None:
    """Break parent cycles on a deferred field that is about to be discarded."""
    field.parent = None
    for data_type in field.data_type.all_data_types:
        data_type.parent = None
    match field.__dict__.get(_DEFERRED_INHERITED_TYPE_KEY):
        case DataType() as deferred_type:
            for data_type in deferred_type.all_data_types:
                data_type.parent = None
        case _InheritedTypeModifiers(list_wrapper=DataType() as list_wrapper):
            for data_type in list_wrapper.all_data_types:
                data_type.parent = None


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


def _intersect_constraints(
    inherited: ConstraintsBase | None,
    overriding: ConstraintsBase | None,
) -> ConstraintsBase | None:
    """Overlay a partial field's constraints on its inherited field constraints."""
    constraints_class = type(inherited or overriding)
    if not issubclass(constraints_class, ConstraintsBase):
        return None  # pragma: no cover
    inherited_values = inherited.model_dump(by_alias=True, exclude_none=True) if inherited else {}
    overriding_values = overriding.model_dump(by_alias=True, exclude_none=True) if overriding else {}
    merged_values = inherited_values.copy()
    merged_values.update(overriding_values)
    return constraints_class.model_validate(merged_values)


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
                copied_field.constraints = _intersect_constraints(
                    inherited_field.constraints,
                    field.constraints,
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
            else:
                copied_field.name = field.name
        case _:
            return None

    copied_field.__dict__.pop(_DEFERRED_INHERITED_FIELD_KEY, None)
    copied_field.__dict__.pop(_DEFERRED_INHERITED_CLASS_KEY, None)
    copied_field.__dict__.pop(_DEFERRED_INHERITED_TYPE_KEY, None)
    return copied_field


def _restore_inherited_field_names_for_unique_aliases(
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
            colliding_field.name = field.name
            if colliding_field.alias is None:
                colliding_field.alias = colliding_field.original_name
            field.name = inherited_name
            return True
    return False


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


_DedupItem = TypeVar("_DedupItem")
_ModelShape: TypeAlias = tuple[tuple[Any, ...], list[Reference]]


def _iter_first_seen_duplicates(
    items: Iterable[_DedupItem],
    key_fn: Callable[[_DedupItem], tuple[HashableComparable, ...]],
) -> Iterator[tuple[_DedupItem, _DedupItem]]:
    seen: dict[tuple[HashableComparable, ...], _DedupItem] = {}
    for item in items:
        key = key_fn(item)
        if key in seen:
            yield seen[key], item
            continue
        seen[key] = item


def _model_references(model: DataModel) -> list[Reference]:
    """Return the references of a model's data types in render order."""
    return [reference for data_type in model.all_data_types if (reference := data_type.reference)]


def _model_shape(model: DataModel, shapes: dict[DataModel, _ModelShape]) -> _ModelShape:
    """Return a model's dedup key with referenced model names masked, and its references in order.

    The key is rendered afresh, since cached keys keep the names that references had when they were
    cached, and memoized in shapes for one comparison pass. Names are masked outside string literals and
    field declarations only, so defaults, descriptions and field names that spell a referenced model name
    still tell models apart.
    """
    if (shape := shapes.get(model)) is not None:
        return shape
    references = _model_references(model)
    masks: dict[str, str] = {}
    for reference in references:
        masks.setdefault(reference.short_name, f"<{len(masks)}>")
    rendered, imports = model._render_dedup_key("M")  # noqa: SLF001
    masked = _STRING_OR_IDENTIFIER_PATTERN.sub(
        lambda match: masks.get(name, name) if (name := match[1]) else match[0], rendered
    )
    shapes[model] = shape = (type(model), masked, imports, len(references)), references
    return shape


def _referenced_models_match(left: DataModel, right: DataModel, shapes: dict[DataModel, _ModelShape]) -> bool:
    """Return whether equally rendered models also reference models of the same shape.

    Type hints carry only reference names, and references from different sources can share a
    name until module-level renaming, so equal render output does not prove equal field types.
    Referenced models are compared pairwise with the names of their own references masked, so
    models that differ only in how deeper references are named still match. Reference pairs
    already under comparison are assumed to match, which keeps recursive models finite, and models
    that reference the very same models match without rendering them again.
    """
    left_references, right_references = _model_references(left), _model_references(right)
    if len(left_references) == len(right_references) and all(map(operator.is_, left_references, right_references)):
        return True
    pending: list[tuple[object, object]] = [(left, right)]
    assumed_pairs: set[tuple[str, str]] = set()
    while pending:
        left_model, right_model = pending.pop()
        if not (
            isinstance(left_model, DataModel)
            and isinstance(right_model, DataModel)
            and (left_shape := _model_shape(left_model, shapes))[0]
            == (right_shape := _model_shape(right_model, shapes))[0]
        ):
            return False
        for left_reference, right_reference in zip(left_shape[1], right_shape[1], strict=True):
            pair = (left_reference.path, right_reference.path)
            if left_reference is right_reference or pair in assumed_pairs:
                continue
            assumed_pairs.add(pair)
            pending.append((left_reference.source, right_reference.source))
    return True


def _referenced_shapes(model: DataModel, shapes: dict[DataModel, _ModelShape]) -> tuple[object, ...]:
    """Return the shapes of the models a model references in order, which models that match share.

    A reference to anything other than a model matches only itself, so it stands for itself.
    """
    return tuple(
        _model_shape(source, shapes)[0] if isinstance(source := reference.source, DataModel) else id(reference)
        for reference in _model_references(model)
    )


def _iter_matching_duplicates(
    models: Iterable[DataModel], shapes: dict[DataModel, _ModelShape]
) -> Iterator[tuple[DataModel, DataModel]]:
    """Pair each model with the earlier model whose content and references both match.

    Earlier unmatched models never match each other, so at most one matches. A model that references the
    very same models as one of them is paired at once. Otherwise it is compared in full only with those
    whose referenced models render alike, so many equally rendered models with different references are
    not compared pairwise. Referenced models are rendered only once a model is not paired at once.
    """
    by_references: dict[tuple[int, ...], DataModel] = {}
    by_shapes: defaultdict[tuple[object, ...], list[DataModel]] = defaultdict(list)
    unshaped: list[DataModel] = []
    for model in models:
        references = tuple(map(id, _model_references(model)))
        if (canonical := by_references.get(references)) is None and by_references:
            for candidate in unshaped:
                by_shapes[_referenced_shapes(candidate, shapes)].append(candidate)
            unshaped.clear()
            candidates = by_shapes[_referenced_shapes(model, shapes)]
            canonical = next((c for c in candidates if _referenced_models_match(c, model, shapes)), None)
        if canonical is None:
            unshaped.append(model)
            by_references[references] = model
            continue
        yield canonical, model


def _check_discriminator_mapping_paths(
    model: DataModel | Reference,
    mapping: dict[str, str],
    discriminator_values: list[DiscriminatorValue],
) -> None:
    for name, path in mapping.items():
        if "/" not in path and "#" not in path:
            if model.path.rsplit("/", 1)[-1] == path:
                discriminator_values.append(name)
            continue
        if (model.path.split("#/")[-1] != path.split("#/")[-1]) and (
            path.startswith("#/") or model.path[:-1] != path.split("/")[-1]
        ):
            t_path = path[str(path).find("/") + 1 :]
            t_disc = model.path[: str(model.path).find("#")].lstrip("../")  # noqa: B005
            t_disc_2 = "/".join(t_disc.split("/")[1:])
            if t_path not in {t_disc, t_disc_2}:  # pragma: no branch
                continue
        discriminator_values.append(name)


def _get_discriminator_field_value(discriminator_field: DataModelFieldBase) -> DiscriminatorValue | None:
    const_value = discriminator_field.extras.get("const")
    if const_value is not None:
        return const_value

    literals = discriminator_field.data_type.literals
    if len(literals) == 1:
        return literals[0]

    enum_source = discriminator_field.data_type.find_source(Enum)
    if enum_source and len(enum_source.fields) == 1:
        return get_raw_enum_member_value(enum_source.fields[0].default)
    return None


def _find_discriminator_value(
    fields: Iterable[DataModelFieldBase],
    field_name: str,
    *,
    field_name_mode: Literal["original", "resolved", "either"] = "either",
) -> DiscriminatorValue | None:
    for field in fields:
        if field_name_mode == "original":
            source_name = field.original_name if field.original_name is not None else field.alias or field.name
            matches = source_name == field_name
        else:
            matches = field.name == field_name or (field_name_mode == "either" and field.original_name == field_name)
        if matches and (value := _get_discriminator_field_value(field)) is not None:
            return value
    return None


def _get_discriminator_values(
    discriminator_model: DataModel,
    field_name: str,
    mapping: dict[str, str],
    *,
    require_literal: bool = False,
    field_name_mode: Literal["original", "resolved", "either"] = "either",
) -> list[DiscriminatorValue]:
    if (
        value := _find_discriminator_value(discriminator_model.fields, field_name, field_name_mode=field_name_mode)
    ) is not None:
        return [value]

    # Reuse models are created as empty subclasses with a "/reuse" path suffix.
    # Nested choices cannot be updated later, so also accept inherited literals.
    if (require_literal or discriminator_model.path.endswith("/reuse")) and (
        value := _find_discriminator_value(
            discriminator_model.iter_all_fields(),
            field_name,
            field_name_mode=field_name_mode,
        )
    ) is not None:
        return [value]
    if require_literal:
        return []

    discriminator_values: list[DiscriminatorValue] = []
    if mapping:
        _check_discriminator_mapping_paths(discriminator_model, mapping, discriminator_values)
        if not discriminator_values:
            for base_reference in filter(
                None, (base_class.reference for base_class in discriminator_model.base_classes)
            ):
                _check_discriminator_mapping_paths(base_reference, mapping, discriminator_values)

    return discriminator_values or [discriminator_model.path.split("/")[-1]]


def _remove_discriminator(field: DataModelFieldBase) -> None:
    field.extras.pop("discriminator", None)
    field.data_type.discriminator = None


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


def _discriminator_variants_are_valid(
    data_types: Iterable[DataType],
    field_name: str,
    mapping: dict[str, str],
    *,
    original_name: str | None = None,
) -> bool:
    discriminator_value_owners: dict[DiscriminatorValue, int] = {}
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
            field_name_mode="original" if original_name is not None else "either",
        )
        if not discriminator_values:
            return False
        for value in discriminator_values:
            if (previous_owner := discriminator_value_owners.get(value)) is not None and previous_owner != owner:
                return False
            discriminator_value_owners[value] = owner
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
            if base_field.name != field_name and (use_field_name or base_field.original_name != field_name):
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


def _get_model_module_name(model: DataModel, model_path_to_module_name: Mapping[str, str] | None) -> str:
    """Return a generated module name when scope planning supplied one."""
    if model_path_to_module_name is None:
        return model.module_name
    return model_path_to_module_name.get(model.path, model.module_name)


def _get_data_type_target_full_name(
    data_type: DataType,
    reference: Reference,
    model_path_to_module_name: Mapping[str, str],
) -> str:
    if (ref_module_name := model_path_to_module_name.get(reference.path)) is None:
        ref_module_name = data_type.full_name.rsplit(".", 1)[0] if "." in data_type.full_name else ""
    return f"{ref_module_name}.{reference.short_name}" if ref_module_name else reference.short_name


def _register_data_type_import(
    data_type: DataType,
    model: DataModel,
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: dict[str, str] | None = None,
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
    imports.append([
        Import(
            from_=from_,
            import_=import_,
            alias=alias.name,
            reference_path=reference.path,
        )
    ])


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


def _remap_imports(imports: Imports, overrides: Mapping[str, str]) -> None:
    """Convert import override conflicts to a user-facing generator error."""
    if not imports.counter:
        return
    try:
        imports.remap_modules(overrides)
    except ValueError as e:
        raise Error(str(e)) from e


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
    _http_fetch_session: _HTTPFetchSession | None = None

    @property
    def run_context(self) -> ParserRunContext:
        """Return the immutable settings for the current facade-managed run."""
        return self._run_context or _DEFAULT_PARSER_RUN_CONTEXT

    def configure_run_context(
        self,
        *,
        diagnostic_source_path: Path | None = None,
        prefetched_source: tuple[Path, bytes] | None = None,
        formatter_cwd: Path | None = None,
        preserve_circular_root_models: bool = False,
        suppress_parse_warnings: bool = False,
    ) -> None:
        """Configure parser run state without exposing implementation attributes."""
        if (
            diagnostic_source_path is None
            and prefetched_source is None
            and formatter_cwd is None
            and not preserve_circular_root_models
            and not suppress_parse_warnings
        ):
            self._run_context = None
            return
        self._run_context = ParserRunContext(
            diagnostic_source_path=diagnostic_source_path,
            prefetched_source=prefetched_source,
            formatter_cwd=formatter_cwd,
            preserve_circular_root_models=preserve_circular_root_models,
            suppress_parse_warnings=suppress_parse_warnings,
        )

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
            config = self._create_default_config(options)  # ty: ignore[invalid-argument-type]

        self.config = config
        self._source_context = getattr(config, "_source_context", None) or _source_context_from_config(config)
        self._has_bound_python_types = False
        self._has_runtime_expressions = False
        self._constrained_decimal_alias_model_ids: set[int] = set()

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

        self.imports: Imports = Imports(config.use_exact_imports)
        self.use_exact_imports: bool = config.use_exact_imports
        self.use_type_checking_imports: bool | None = config.use_type_checking_imports
        self._append_additional_imports(additional_imports=config.additional_imports)
        self.class_decorators: list[str] = config.class_decorators or []

        self.base_class: str | None = config.base_class
        self.base_class_map: dict[str, str | list[str]] | None = config.base_class_map
        self.target_python_version: PythonVersion = config.target_python_version
        self.builtin_names: frozenset[str] = _get_builtin_names_for_target(self.target_python_version)
        self.generation_store, self.results = self._generation_store_factory()
        self.model_metadata: ModelMetadata | None = None
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
        self._data_model_field_common_kwargs_cache: dict[str, Any] = {"use_missing_sentinel": self.use_missing_sentinel}
        self.reuse_model: bool = config.reuse_model
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
        self.infer_union_variant_names: bool = config.infer_union_variant_names
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
        self._type_override_imports: dict[str, Import] = {
            key: Import.from_full_path(value) for key, value in self.type_overrides.items()
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
        return class_name.startswith("_") and ModelResolver.validate_name(class_name)

    @property
    def field_name_model_type(self) -> ModelType:
        """Get the ModelType for field name validation based on data_model_type.

        Returns ModelType.PYDANTIC for Pydantic models (which have reserved attributes
        like 'schema', 'model_fields', etc.), ModelType.MSGSPEC for msgspec Structs
        (whose imported ``field`` must not be shadowed by a field named ``field``), and
        ModelType.CLASS for other model types (TypedDict, dataclass) which don't have
        such constraints.
        """
        return model_type if (model_type := self.data_model_type.FIELD_NAME_MODEL_TYPE) is not None else ModelType.CLASS

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
            self.imports.append(new_import)

    def _resolve_base_class(
        self, class_name: str, custom_base_path: str | list[str] | None = None
    ) -> str | list[str] | None:
        """Resolve base class(es) with priority: base_class_map > customBasePath > base_class."""

        def normalize(value: str | list[str] | None) -> str | list[str] | None:
            if value is None:  # pragma: no cover
                return None
            if isinstance(value, list):
                seen: set[str] = set()
                result = [v for v in value if isinstance(v, str) and v and v not in seen and not seen.add(v)]
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

    @classmethod
    def _replace_model_in_list(
        cls,
        models: list[DataModel],
        original: DataModel,
        replacement: DataModel,
    ) -> None:
        """Replace model at its position in list."""
        # Use direct assignment instead of insert+remove for O(n) instead of O(2n)
        idx = models.index(original)
        models[idx] = replacement

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

    def __delete_duplicate_models(self, models: list[DataModel]) -> None:  # noqa: PLR0912
        model_class_names: dict[str, DataModel] = {}
        shapes: dict[DataModel, _ModelShape] = {}
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
            if isinstance(model, self.data_model_root_type):
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
                        [bc for bc in child.base_classes if bc.reference != model.reference],
                    )
                    if not child.base_classes:  # pragma: no cover
                        self.generation_store.reset_base_classes(child)

            class_name = model.duplicate_class_name or model.class_name
            if (
                reuse_allowed
                and (original_model := model_class_names.get(class_name)) is not None
                and self._reuse_optimization_context.allows_model(original_model)
                and model.get_dedup_key(model.duplicate_class_name, use_default=False)
                == original_model.get_dedup_key(original_model.duplicate_class_name, use_default=False)
            ):
                if _referenced_models_match(original_model, model, shapes):
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
                        {f"{c.module_name}.{c.type_hint}": c for c in child.base_classes}.values(),
                    )
                models_to_remove.add(duplicate_model)

        if self.reuse_model and self.collapse_reuse_models:
            max_iterations, iteration = len(models), 0
            while True:
                iteration += 1
                if iteration > max_iterations:  # pragma: no cover
                    msg = f"Deduplication exceeded max iterations ({max_iterations})"
                    raise RuntimeError(msg)

                shapes.clear()
                content_key_to_models: dict[tuple[Any, ...], list[DataModel]] = defaultdict(list)
                for model in self._reuse_optimization_context.eligible_models(models):
                    if model in models_to_remove or isinstance(model, self.data_model_root_type):
                        continue
                    model._dedup_key_cache.clear()  # noqa: SLF001
                    content_key_to_models[model.get_dedup_key(None, use_default=True)].append(model)

                if not (
                    duplicates := [
                        (canonical, dup)
                        for group in content_key_to_models.values()
                        if len(group) > 1
                        for canonical, dup in _iter_matching_duplicates(group, shapes)
                        if dup not in models_to_remove
                    ]
                ):
                    break

                for canonical, duplicate in duplicates:
                    self.generation_store.redirect_model_reference_users(duplicate, models, canonical.reference)
                    for child in duplicate.reference.iter_data_model_children():  # pragma: no cover
                        self.generation_store.set_base_classes(
                            child,
                            {c.reference: c for c in child.base_classes}.values(),
                        )
                    models_to_remove.add(duplicate)

        # Batch removal: O(n) instead of O(n²)
        if models_to_remove:
            models[:] = [m for m in models if m not in models_to_remove]

    def __replace_duplicate_name_in_module(self, models: list[DataModel]) -> None:
        """Make class names unique within a module.

        Renames change the rendered type hints of referencing models, so cached dedup keys of the
        module are dropped whenever a model is renamed.
        """
        scoped_model_resolver = ModelResolver(
            exclude_names={i.alias or i.import_ for m in models for i in m.imports},
            duplicate_name_suffix="Model",
            custom_class_name_generator=(lambda name: name) if self.custom_class_name_generator else None,
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
                model.class_name = generated_name
                renamed = True
            model_names[model.class_name] = model

        for model in models:
            duplicate_name = model.duplicate_class_name
            # check only first desired name
            if duplicate_name and duplicate_name not in model_names:
                del model_names[model.class_name]
                model.class_name = duplicate_name
                model_names[duplicate_name] = model
                renamed = True

        if not renamed:
            return
        for model in models:
            model.invalidate_render_caches()

    def __change_from_import(  # noqa: PLR0912, PLR0913, PLR0914
        self,
        models: list[DataModel],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        *,
        init: bool,
        internal_modules: set[tuple[str, ...]] | None = None,
        model_path_to_module_name: dict[str, str] | None = None,
    ) -> None:
        model_paths = {model.path for model in models}
        internal_modules = internal_modules or set()
        model_path_to_module_name = model_path_to_module_name or {}

        for model in models:
            scoped_model_resolver.add([model.path], model.class_name)
        for model in models:
            before_import = model.imports
            imports.append(before_import)
            current_module_name = _get_model_module_name(model, model_path_to_module_name)
            # Some imports are derived from type hints, so aliases can affect them.
            import_sensitive_alias_changed = False
            for data_type in model.all_data_types:
                match data_type.reference:
                    case None:
                        continue
                    case reference if reference.path in model_paths:
                        continue
                    case reference:
                        pass

                target_full_name = _get_data_type_target_full_name(data_type, reference, model_path_to_module_name)

                if isinstance(data_type, BaseClassDataType):
                    left, right = relative(current_module_name, target_full_name)
                    from_ = (
                        left
                        if is_ancestor_package_reference(current_module_name, target_full_name)
                        else (f"{left}{right}" if left.endswith(".") else f"{left}.{right}")
                    )
                    import_ = reference.short_name
                    full_path = from_, import_
                else:
                    from_, import_ = full_path = relative(current_module_name, target_full_name)
                    if imports.use_exact:
                        from_, import_ = full_path = _resolve_exact_import(
                            current_module_name,
                            target_full_name,
                            from_,
                            import_,
                            reference.short_name,
                        )
                    import_ = import_.replace("-", "_")
                    current_module_path = tuple(current_module_name.split(".")) if current_module_name else ()
                    if (  # pragma: no cover
                        len(current_module_path) > 1
                        and current_module_path[-1].count(".") > 0
                        and not self.treat_dot_as_module
                    ):
                        rel_path_depth = current_module_path[-1].count(".")
                        from_ = from_[rel_path_depth:]

                    ref_module = tuple(target_full_name.split(".")[:-1])

                    is_module_class_collision = (
                        ref_module and import_ == reference.short_name and ref_module[-1] == import_
                    )

                    if (
                        from_
                        and not imports.use_exact
                        and (ref_module in internal_modules or is_module_class_collision)
                    ):
                        from_ = f"{from_}{import_}" if from_.endswith(".") else f"{from_}.{import_}"
                        import_ = reference.short_name
                        full_path = from_, import_

                alias = scoped_model_resolver.add(full_path, import_).name

                name = reference.short_name
                if from_ and import_ and alias != name:
                    data_type.alias = alias if reference.short_name == import_ else f"{alias}.{name}"
                    import_sensitive_alias_changed = True

                if init and not target_full_name.startswith(current_module_name + "."):
                    from_ = "." + from_
                imports.append(
                    Import(
                        from_=from_,
                        import_=import_,
                        alias=alias,
                        reference_path=reference.path,
                    ),
                )
            if import_sensitive_alias_changed:  # pragma: no cover
                model.clear_imports_cache()
                after_import = model.imports
                if before_import != after_import:
                    imports.remove(before_import)
                    imports.append(after_import)

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
                merged_enum = enums[0].__class__(
                    fields=[f for e in enums for f in e.fields],
                    description=model.description,
                    reference=model.reference,
                )
                cls._replace_model_in_list(models, model, merged_enum)

    def _create_discriminator_data_type(
        self,
        enum_source: Enum | None,
        discriminator_values: list[DiscriminatorValue],
        discriminator_model: DataModel,
        imports: Imports,
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
                if enum_source.module_path != discriminator_model.module_path:  # pragma: no cover
                    imports.append(Import.from_full_path(enum_source.name))
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
        if not member:
            return value

        member_value = member.value
        if isinstance(member_value, (str, int, bool)):
            return member_value
        return value

    def __set_force_optional_discriminator_literal_default(
        self,
        model: DataModel,
        discriminator_field: DataModelFieldBase,
        literal: DiscriminatorValue | Member,
        *,
        can_retain_cache: bool,
    ) -> None:
        """Keep Pydantic v2 single-literal discriminator fields valid when forced optional."""
        if not self.force_optional_for_required_fields or not discriminator_field.SUPPORTS_DISCRIMINATOR:
            return

        discriminator_field.default = literal
        discriminator_field.required = False
        discriminator_field.nullable = False
        _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

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
                if property_name == variant_field.original_name or (
                    variant_field.original_name is None and default_name == variant_field.name
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
                discriminator["propertyName"] = field_name
                mapping = discriminator.get("mapping", {})
                # Any type cannot be a discriminated union variant (Pydantic v2 rejects it)
                has_any_variant = any(_is_any_variant(dt) for dt in field.data_type.data_types)
                if has_any_variant:  # pragma: no cover
                    _remove_discriminator(field)
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
                    continue
                if not _discriminator_variants_are_valid(
                    field.data_type.data_types,
                    field_name,
                    mapping,
                    original_name=property_name if field.SUPPORTS_DISCRIMINATOR else None,
                ):
                    _remove_discriminator(field)
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
                    continue

                if field.SUPPORTS_DISCRIMINATOR:
                    field_name = self._get_discriminator_field_name(
                        field.data_type.data_types, property_name, field_name
                    )
                    discriminator["propertyName"] = field_name
                    if field_name != property_name and alias is None:
                        alias = property_name

                for data_type in field.data_type.data_types:
                    if not data_type.reference:  # pragma: no cover
                        continue
                    discriminator_model = data_type.reference.source
                    if (
                        not isinstance(discriminator_model, DataModel)
                        or not discriminator_model.SUPPORTS_DISCRIMINATOR
                        or _is_discriminator_wrapper(discriminator_model)
                    ):  # pragma: no cover
                        continue

                    # A resolved Python name can equal a different field's wire name.
                    discriminator_values = _get_discriminator_values(
                        discriminator_model,
                        field_name,
                        mapping,
                        field_name_mode="resolved" if field.SUPPORTS_DISCRIMINATOR else "either",
                    )
                    has_one_literal = False
                    for discriminator_field in discriminator_model.fields:
                        if discriminator_field.name != field_name and (
                            field.SUPPORTS_DISCRIMINATOR or discriminator_field.original_name != field_name
                        ):
                            continue
                        const_value = discriminator_field.extras.get("const")
                        expected_value = discriminator_values[0] if discriminator_values else None

                        const_match = const_value is not None and const_value == expected_value

                        if (
                            len(literals := discriminator_field.data_type.literals) == 1
                            and (literal := literals[0]) == expected_value
                        ):
                            has_one_literal = True
                            match discriminator_model:
                                case _ if discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR:  # pragma: no cover
                                    discriminator_model.apply_discriminator_tag(
                                        discriminator_field,
                                        field_name,
                                        expected_value,
                                    )
                                    _clear_model_imports_cache_if_retained(
                                        discriminator_model, can_retain_cache=can_retain_cache
                                    )
                                case _:
                                    self.__set_force_optional_discriminator_literal_default(
                                        discriminator_model,
                                        discriminator_field,
                                        literal,
                                        can_retain_cache=can_retain_cache,
                                    )
                            # Found the discriminator field, no need to keep looking
                            break

                        # For msgspec with const value but no literal (type: string + const case)
                        if const_match and discriminator_model.REQUIRES_TAGGED_UNION_DISCRIMINATOR:  # pragma: no cover
                            has_one_literal = True
                            discriminator_model.apply_discriminator_tag(
                                discriminator_field,
                                field_name,
                                const_value,
                            )
                            _clear_model_imports_cache_if_retained(
                                discriminator_model, can_retain_cache=can_retain_cache
                            )
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
                        discriminator_field.required = True
                        if (
                            self.force_optional_for_required_fields
                            and (
                                literal_default := _get_single_discriminator_default(
                                    new_discriminator_data_type,
                                    enum_source,
                                    expected_value,
                                )
                            )
                            is not None
                        ):
                            self.__set_force_optional_discriminator_literal_default(
                                discriminator_model,
                                discriminator_field,
                                literal_default,
                                can_retain_cache=can_retain_cache,
                            )
                        imports.append(discriminator_field.imports)
                        has_one_literal = True
                    if not has_one_literal:
                        new_data_type = self._create_discriminator_data_type(
                            _get_enum_from_base(
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
            has_imported_literal = any(import_ == IMPORT_LITERAL for import_ in imports)
            if has_imported_literal:  # pragma: no cover
                imports.append(IMPORT_LITERAL)

    @classmethod
    def _create_set_from_list(cls, data_type: DataType) -> DataType | None:
        if data_type.is_list:
            new_data_type = data_type.model_copy()
            new_data_type.is_list = False
            new_data_type.is_set = True
            for data_type_ in new_data_type.data_types:
                data_type_.parent = new_data_type
            return new_data_type
        if data_type.data_types:  # pragma: no cover
            for nested_data_type in data_type.data_types[:]:
                set_data_type = cls._create_set_from_list(nested_data_type)
                if set_data_type:  # pragma: no cover
                    nested_data_type.swap_with(set_data_type)
            return data_type
        return None  # pragma: no cover

    def __replace_unique_list_to_set(self, models: list[DataModel], *, can_retain_cache: bool) -> None:
        changed = False
        for model in models:
            for model_field in model.fields:
                if not self.use_unique_items_as_set:
                    continue

                match model_field.constraints:
                    case ConstraintsBase(unique_items=True) | {"uniqueItems": True}:
                        pass
                    case _:
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
                            _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
                            continue
                        model_field.default = converted_default
                    self.generation_store.replace_field_type(model_field, set_data_type)
                    changed = True
        if changed and not can_retain_cache:
            _clear_model_imports_cache(models)

    @classmethod
    def __collect_set_item_references(cls, models: list[DataModel]) -> set[str]:
        """Collect reference paths of all types used as set/frozenset items."""
        references: set[str] = set()
        for model in models:
            for field in model.fields:
                for data_type in field.data_type.all_data_types:
                    if data_type.is_set or data_type.is_frozen_set:
                        for item_type in data_type.data_types:
                            references.update(
                                nested.reference.path for nested in item_type.all_data_types if nested.reference
                            )
        return references

    def __mark_set_item_models_hashable(self, models: list[DataModel]) -> None:
        """Mark models used as set/frozenset items with hash flag for __hash__ generation."""
        set_item_references = self.__collect_set_item_references(models)
        if not set_item_references:
            return
        set_item_models = [
            model for model in models if model.reference.path in set_item_references and not isinstance(model, Enum)
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

    @classmethod
    def __set_reference_default_value_to_field(
        cls,
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
                    model_field.default = model_field.data_type.reference.source.default
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __reuse_model(self, models: list[DataModel], require_update_action_models: list[str]) -> None:
        if not self.reuse_model or self.reuse_scope == ReuseScope.Tree:
            return
        duplicates = []
        reuse_candidates = (
            model
            for model in self._reuse_optimization_context.eligible_models(models.copy())
            if not (self.collapse_root_models and isinstance(model, self.data_model_root_type))
        )
        for cached_model, model in _iter_first_seen_duplicates(reuse_candidates, lambda item: item.get_dedup_key()):
            cached_model_reference = cached_model.reference
            if isinstance(model, Enum) or self.collapse_reuse_models:
                self.generation_store.redirect_model_reference_users(model, models, cached_model_reference)
                duplicates.append(model)
            else:
                inherited_model = model.create_reuse_model(cached_model_reference)
                self.generation_store.redirect_model_reference_users(model, models, inherited_model.reference)
                if cached_model_reference.path in require_update_action_models:
                    add_model_path_to_list(require_update_action_models, inherited_model)
                self._replace_model_in_list(models, model, inherited_model)

        for duplicate in duplicates:
            models.remove(duplicate)

    def __find_duplicate_models_across_modules(
        self,
        module_models: list[tuple[tuple[str, ...], list[DataModel]]],
    ) -> list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]]:
        """Find duplicate models across all modules by comparing render output and imports."""
        all_models: list[tuple[tuple[str, ...], DataModel]] = []
        for module, models in module_models:
            all_models.extend((module, model) for model in self._reuse_optimization_context.eligible_models(models))

        duplicates: list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]] = []

        for (canonical_module, canonical_model), (module, model) in _iter_first_seen_duplicates(
            all_models,
            lambda item: item[1].get_dedup_key(),
        ):
            duplicates.append((module, model, canonical_module, canonical_model))

        return duplicates

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
        require_update_action_models: list[str],
        reference_models: list[DataModel] | None,
    ) -> tuple[tuple[str, ...], list[DataModel]]:
        """Create shared module with canonical models and replace duplicates with inherited models."""
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
                referring_models = reference_models if reference_models is not None else models
                if isinstance(duplicate_model, Enum) or not supports_inheritance or self.collapse_reuse_models:
                    self.generation_store.redirect_model_reference_users(duplicate_model, referring_models, shared_ref)
                    models_to_remove[module].add(duplicate_model)
                else:
                    inherited_model = duplicate_model.create_reuse_model(shared_ref)
                    self.generation_store.redirect_model_reference_users(
                        duplicate_model,
                        referring_models,
                        inherited_model.reference,
                    )
                    if shared_ref.path in require_update_action_models:
                        add_model_path_to_list(require_update_action_models, inherited_model)
                    self._replace_model_in_list(models, duplicate_model, inherited_model)
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
            later_shared_paths: set[str] = set()
            for model in reversed(shared_models):
                if self.generation_store.index.reference_classes_for_model(model) & later_shared_paths:
                    add_model_path_to_list(require_update_action_models, model)
                later_shared_paths.add(model.path)

        return (shared_module,), shared_models

    def __reuse_model_tree_scope(
        self,
        module_models: list[tuple[tuple[str, ...], list[DataModel]]],
        require_update_action_models: list[str],
        module_split_mode: ModuleSplitMode | None,
    ) -> tuple[tuple[str, ...], list[DataModel]] | None:
        """Deduplicate models across all modules, placing shared models in shared.py."""
        if not self.reuse_model or self.reuse_scope != ReuseScope.Tree:
            return None

        duplicates = self.__find_duplicate_models_across_modules(module_models)
        if not duplicates:
            return None

        self.__validate_shared_module_name(module_models)
        # Single-model splitting puts users outside the duplicate's original module.
        reference_models = (
            [model for _, models in module_models for model in models]
            if module_split_mode == ModuleSplitMode.Single
            else None
        )
        return self.__create_shared_module_from_duplicates(
            module_models, duplicates, require_update_action_models, reference_models
        )

    def __collapse_root_models(
        self,
        models: list[DataModel],
        unused_models: list[DataModel],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        model_path_to_module_name: dict[str, str] | None = None,
    ) -> None:
        if not self.collapse_root_models:
            return

        with self.generation_store._collapse_root_reference_scope():  # noqa: SLF001
            self.__collapse_root_models_in_scope(
                models,
                unused_models,
                imports,
                scoped_model_resolver,
                model_path_to_module_name,
            )

    def __collapse_root_models_in_scope(  # noqa: PLR0912, PLR0914, PLR0915
        self,
        models: list[DataModel],
        unused_models: list[DataModel],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        model_path_to_module_name: dict[str, str] | None = None,
    ) -> None:
        generation_store = self.generation_store
        generation_index = generation_store.index
        circular_root_model_paths = getattr(self, "_circular_root_model_paths", ())

        for model in models:  # noqa: PLR1702
            for model_field in model.fields:
                for data_type in model_field.data_type.all_data_types:
                    reference = data_type.reference
                    if not reference or not isinstance(reference.source, self.data_model_root_type):
                        # If the data type is not a reference, we can't collapse it.
                        # If it's a reference to a root model type, we don't do anything.
                        continue

                    # Use root-type as model_field type
                    root_type_model = reference.source
                    root_type_field = root_type_model.fields[0]

                    if root_type_model.path in circular_root_model_paths:
                        # A circular root model cannot be fully inlined; keep it named.
                        continue

                    # These runtime rules are owned by the referenced root model;
                    # replacing it with the raw type would discard its validator.
                    if root_type_model.has_runtime_object_validation:
                        continue

                    root_constraints = root_type_field.constraints
                    if not self.field_constraints and _has_preservable_root_constraints(root_type_model):
                        # Constraints can live on a nested array/union type alias.
                        # Flattening this wrapper would silently discard them.
                        continue
                    if isinstance(root_constraints, ConstraintsBase) and root_constraints.has_constraints:
                        if root_type_field.data_type.is_dict or root_type_field.data_type.is_mapping:
                            continue
                        if self.field_constraints and any(
                            data_type.is_dict or data_type.is_union or data_type.is_list
                            for data_type in model_field.data_type.all_data_types
                        ):
                            continue

                    if root_type_field.data_type.reference:
                        if self.collapse_root_models_name_strategy is None:
                            continue

                        inner_reference = root_type_field.data_type.reference
                        inner_model = cast("DataModel", inner_reference.source)

                        if self.collapse_root_models_name_strategy == CollapseRootModelsNameStrategy.Parent:
                            root_model_wrappers, direct_refs = generation_index.root_collapse_reference_usage(
                                inner_reference,
                                excluded_model=root_type_model,
                                root_model_type=self.data_model_root_type,
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

                            generation_store.update_model_reference(
                                inner_model,
                                class_name=root_type_model.class_name,
                                reference_name=root_type_model.class_name,
                                new_path=root_type_model.reference.path,
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

                        imports.remove_referenced_imports(root_type_model.path)
                        if not has_remaining_root_references:
                            unused_models.append(root_type_model)

                        continue

                    # set copied data_type
                    source_module_name = _get_model_module_name(root_type_model, model_path_to_module_name)
                    target_module_name = _get_model_module_name(model, model_path_to_module_name)
                    copied_data_type = (
                        self._copy_model_type(root_type_field.data_type)
                        if source_module_name != target_module_name
                        else root_type_field.data_type.model_copy()
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

                    replacement_context = None
                    if isinstance(field_ := data_type.parent, self.data_model_field_type):
                        # for field
                        # override empty field by root-type field
                        model_field.extras = {
                            **root_type_field.extras,
                            **model_field.extras,
                        }
                        if root_type_field.runtime_expression_imports:
                            model_field._set_runtime_expression_imports(  # noqa: SLF001
                                (
                                    *model_field.runtime_expression_imports,
                                    *runtime_expression_imports(model_field.extras),
                                )
                            )
                        model_field.process_const()

                        if self.field_constraints:
                            model_field.constraints = ConstraintsBase.merge_constraints(
                                root_type_field.constraints, model_field.constraints
                            )

                        replacement_context = generation_store._replace_data_type_and_detach_data_type_ref(  # noqa: SLF001
                            data_type,
                            copied_data_type,
                            owner=field_,
                            replacement_kind="field",
                        )

                    elif isinstance(parent_data_type := data_type.parent, DataType) and parent_data_type.is_list:
                        if self.field_constraints:
                            model_field.constraints = ConstraintsBase.merge_constraints(
                                root_type_field.constraints, model_field.constraints
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
                    with replacement_context:
                        for d in copied_data_type.all_data_types:
                            _register_data_type_import(
                                d,
                                model,
                                imports,
                                scoped_model_resolver,
                                model_path_to_module_name,
                            )

                        original_field = get_most_of_parent(data_type, DataModelFieldBase)
                        if original_field:  # pragma: no cover
                            # TODO: Improve detection of reference type
                            # Use list instead of set because Import is not hashable
                            excluded_imports = [IMPORT_OPTIONAL, IMPORT_UNION]
                            field_imports = [i for i in original_field.imports if i not in excluded_imports]
                            imports.append(field_imports)

                    imports.remove_referenced_imports(root_type_model.path)
                    if not has_remaining_root_references:
                        unused_models.append(root_type_model)

    def __set_circular_root_model_paths(
        self,
        module_models: ModuleModels,
        *,
        require_dict_key_edge: bool = False,
    ) -> None:
        """Cache root-model paths in circular components before collapsing them."""
        root_models = {
            model.path: model
            for _, models in module_models
            for model in models
            if isinstance(model, self.data_model_root_type)
        }
        graph = {
            (path,): {
                (reference_path,)
                for reference_path in self.generation_store.index.reference_classes_for_model_including_dict_keys(model)
                if reference_path in root_models
            }
            for path, model in root_models.items()
        }
        circular_components = find_circular_sccs(graph)
        if not require_dict_key_edge:
            self._circular_root_model_paths = frozenset(
                path for component in circular_components for (path,) in component
            )
            return

        dict_key_edges = {path: self.__root_dict_key_reference_paths(model) for path, model in root_models.items()}
        circular_paths = {path for path in root_models if (path,) in graph[path,]}
        for component in circular_components:
            component_paths = {path for (path,) in component}
            if any(dict_key_edges[path] & component_paths for path in component_paths):
                circular_paths.update(component_paths)
        self._circular_root_model_paths = frozenset(circular_paths)

    def __root_dict_key_reference_paths(self, model: DataModel) -> set[str]:  # noqa: PLR6301
        """Return model-reference paths reached through mapping-key annotations."""
        reference_paths: set[str] = set()
        data_types = [field.data_type for field in model.fields]
        seen_data_types: set[int] = set()
        while data_types:
            data_type = data_types.pop()
            if id(data_type) in seen_data_types:  # pragma: no cover - parser-owned DataType graphs are trees
                continue
            seen_data_types.add(id(data_type))
            if (dict_key := data_type.dict_key) is not None:
                if dict_key.reference:
                    reference_paths.add(dict_key.reference.path)
                data_types.append(dict_key)
            data_types.extend(data_type.data_types)
        return reference_paths

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
                model_field.default = default
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
            self.__normalize_default_value_constraints(models)

    def __normalize_default_value_constraints(self, models: list[DataModel]) -> bool:
        """Keep backend-declared runtime constraint values structured until alias resolution."""
        from decimal import Decimal  # noqa: PLC0415

        normalized = False
        for model, _, data_type in iter_models_field_data_types(models):
            if (
                not model.SUPPORTS_DESERIALIZED_DEFAULT_VALUES
                or not data_type.kwargs
                or (resolved := self.__resolve_default_value_descriptor(data_type)) is None
            ):
                continue
            annotation_import, descriptor = resolved
            if not descriptor.normalize_constraints or self.__has_import_override(
                annotation_import, descriptor.constructor_import
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
                kwargs[key] = PythonRuntimeExpression.from_import_call(
                    descriptor.constructor_import,
                    repr(str(value)),
                    value=str(value),
                )
            if kwargs is data_type.kwargs:
                continue
            data_type.kwargs = kwargs
            runtime_imports = data_type.runtime_expression_imports
            data_type._set_runtime_expression_imports((*runtime_imports, descriptor.constructor_import))  # noqa: SLF001
            self._register_runtime_expression()
            normalized = True
        return normalized

    def __resolve_default_value_descriptor(self, data_type: DataType) -> tuple[Import, DefaultValueDescriptor] | None:
        """Return a backend-declared scalar descriptor and its emitted annotation import."""
        if (scalar_data_type := _resolve_default_scalar_data_type(data_type)) is None:
            return None
        if (annotation_import := scalar_data_type.import_) is None:
            return None
        if (descriptor := self.data_type_manager.get_default_value_descriptor(scalar_data_type)) is None:
            return None
        return annotation_import, descriptor

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
        if (resolved := self.__resolve_default_value_descriptor(field.data_type)) is None:
            return False
        annotation_import, descriptor = resolved
        if self.__has_import_override(annotation_import, descriptor.constructor_import):
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
        field.default = PythonRuntimeExpression.from_import_call(
            descriptor.constructor_import, repr(value), value=value
        )
        field._set_runtime_expression_imports((descriptor.constructor_import,))  # noqa: SLF001
        self._register_runtime_expression()
        _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)
        return True

    def __has_import_override(self, *imports: Import) -> bool:
        """Avoid changing generated defaults when an import policy redirects their bindings."""
        if not self._import_overrides:
            return False
        return any(self._import_overrides.get(import_.import_, import_.from_) != import_.from_ for import_ in imports)

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

    def _apply_inherited_field_default(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        class_name: str,
    ) -> None:
        """Resolve an inherited schema default in the derived class scope."""
        if self.model_resolver.default_value_overrides and _RAW_SCHEMA_DEFAULT_KEY in inherited_field.__dict__:
            raw_default = inherited_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY]
            has_default = raw_default is not _RAW_SCHEMA_DEFAULT_UNDEFINED
            field.default, field.has_default = self.model_resolver.resolve_default_value(
                field.original_name or field.name or "",
                None if not has_default else raw_default,
                has_default,
                class_name=class_name,
            )
            match field.default:
                case dict() | list() | set():
                    field.default = deepcopy(field.default)
        field.use_default_with_required = (
            self.apply_default_values_for_required_fields and field.required and field.has_default
        )

    def __override_required_field(
        self,
        models: list[DataModel],
        *,
        can_retain_cache: bool = False,
    ) -> None:
        pending_models = (
            model
            for model in models
            if not isinstance(model, (Enum, self.data_model_root_type))
            and any(
                field.original_name is not None
                and not field.data_type.data_types
                and not field.data_type.reference
                and not field.data_type.type
                and not field.data_type.literals
                and not field.data_type.dict_key
                for field in model.fields
            )
        )
        if (first_model := next(pending_models, None)) is None:
            return

        self.generation_store.discard_derived_facts()
        changed = False
        for model in chain((first_model,), pending_models):
            resolved_fields: list[DataModelFieldBase] = []
            reserved_names = {field.name for field in model.fields if field.name}
            inherited_fields = get_inherited_fields(_find_base_classes(model))
            pending_fields = model.fields
            pending_fields.reverse()
            self.generation_store.set_fields(model, [])
            while pending_fields:
                model_field = pending_fields.pop()
                model_field.parent = None
                data_type = model_field.data_type
                if (
                    model_field.original_name is None  # noqa: PLR0916
                    or data_type.data_types
                    or data_type.reference
                    or data_type.type
                    or data_type.literals
                    or data_type.dict_key
                ):
                    resolved_fields.append(model_field)
                    continue

                _detach_deferred_inherited_field_parents(model_field)
                original_field = inherited_fields.get(model_field.original_name)
                if not original_field:
                    changed = True
                    continue
                if (
                    copied_original_field := self._copy_inherited_field(
                        model_field,
                        original_field,
                        force_optional=self.force_optional_for_required_fields,
                        partial_merge_mode=self.allof_merge_mode,
                        reserved_names=reserved_names,
                    )
                ) is None:
                    copied_original_field = self._copy_model_field(original_field)
                    copied_original_field.name = model_field.name
                    copied_original_field.original_name = model_field.original_name
                    copied_original_field.alias = model_field.alias
                    copied_original_field.validation_aliases = (
                        list(model_field.validation_aliases) if model_field.validation_aliases is not None else None
                    )
                    copied_original_field.serialization_alias = model_field.serialization_alias
                    copied_original_field.use_serialization_alias = model_field.use_serialization_alias
                    copied_original_field.required = True
                if class_name := model_field.__dict__.get(_DEFERRED_INHERITED_FIELD_KEY):
                    self._apply_inherited_field_default(
                        copied_original_field,
                        original_field,
                        class_name=class_name,
                    )
                elif class_name := model_field.__dict__.get(_DEFERRED_INHERITED_CLASS_KEY):
                    default_source = model_field
                    if self.allof_merge_mode == AllOfMergeMode.All and not (
                        _RAW_SCHEMA_DEFAULT_KEY in model_field.__dict__
                        and model_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] is not _RAW_SCHEMA_DEFAULT_UNDEFINED
                    ):
                        default_source = original_field
                    self._apply_inherited_field_default(
                        copied_original_field,
                        default_source,
                        class_name=class_name,
                    )
                elif self.apply_default_values_for_required_fields and copied_original_field.has_default:
                    copied_original_field.use_default_with_required = True
                resolved_fields.append(copied_original_field)
                changed = True
            if model.REQUIRES_UNIQUE_FIELD_ALIASES:
                _restore_inherited_field_names_for_unique_aliases(resolved_fields, inherited_fields)
            self.generation_store.set_fields(model, resolved_fields)
        if changed and not can_retain_cache:
            _clear_model_imports_cache(models)

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

    def __change_field_name(
        self,
        models: list[DataModel],
        *,
        can_retain_cache: bool,
    ) -> None:
        if not self.data_model_type.SUPPORTS_FIELD_RENAMING:
            return

        rename_type = self.field_type_collision_strategy == FieldTypeCollisionStrategy.RenameType
        all_class_names = {cast("str", m.class_name) for m in models if m.class_name}

        resolver = ModelResolver(
            snake_case_field=self.snake_case_field,
            remove_suffix_number=True,
        )

        for model in models:
            if "Enum" in model.base_class or not model.BASE_CLASS:
                continue

            for field in (field for field in model.fields if field.name != self.data_model_type.TYPED_EXTRA_FIELD_NAME):
                filed_name = field.name
                reference_type_names: set[str] = set()
                colliding_reference: Reference | None = None

                for data_type in field.data_type.all_data_types:
                    if not data_type.reference:
                        continue
                    reference_type_names.add(data_type.reference.short_name)
                    if rename_type and colliding_reference is None and data_type.reference.short_name == filed_name:
                        colliding_reference = data_type.reference

                if colliding_reference is not None:
                    resolver._reset_for_reuse(all_class_names.copy())  # noqa: SLF001
                    source = cast("DataModel", colliding_reference.source)
                    resolver.exclude_names.add(cast("str", filed_name))
                    new_class_name = resolver.add(["type"], cast("str", source.class_name)).name  # ty: ignore[redundant-cast]
                    source.class_name = new_class_name
                    all_class_names.add(new_class_name)
                elif not rename_type:
                    resolver._reset_for_reuse(reference_type_names)  # noqa: SLF001
                    new_filed_name = resolver._get_unique_field_name(cast("str", filed_name))  # noqa: SLF001
                    if filed_name != new_filed_name:
                        _rename_field_preserving_alias(field, new_filed_name)
                        _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

                if (current_name := field.name) in self.builtin_names and any(
                    _is_builtin_type_collision(current_name, dt) for dt in field.data_type.all_data_types
                ):
                    if field.alias is None:
                        field.alias = filed_name
                    field.name = f"{current_name}_"
                    _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __set_one_literal_on_default(self, models: list[DataModel], *, can_retain_cache: bool) -> None:
        if not self.use_one_literal_as_default:
            return
        for model in models:
            for model_field in model.fields:
                if not model_field.required or len(model_field.data_type.literals) != 1:
                    continue
                model_field.default = model_field.data_type.literals[0]
                model_field.required = False
                if model_field.nullable is not True:  # pragma: no cover
                    model_field.nullable = False
                _clear_model_imports_cache_if_retained(model, can_retain_cache=can_retain_cache)

    def __restore_inherited_field_names_for_unique_aliases(self, models: list[DataModel]) -> None:  # noqa: PLR6301
        """Reserve inherited Python names for output models with unique wire aliases."""
        for model in models:
            if model.REQUIRES_UNIQUE_FIELD_ALIASES and _restore_inherited_field_names_for_unique_aliases(
                model.fields,
                get_inherited_fields(_find_base_classes(model)),
            ):
                model.clear_imports_cache()

    def __fix_constructor_field_ordering(self, models: list[DataModel]) -> None:
        """Fix constructor field ordering after inherited defaults are resolved."""
        for model in models:
            restored_inherited_state = False
            for field in model.fields:
                restored_inherited_state = (
                    model.restore_required_inherited_field_state(field) or restored_inherited_state
                )

            if (inherited := self.__get_inherited_constructor_info(model)) is None:
                field_policy = Parser._get_constructor_field_policy(model)
                if self.__has_local_constructor_ordering_conflict(model, field_policy):
                    model.clear_imports_cache()
                    self.generation_store.set_fields(model, sorted(model.fields, key=field_policy.has_assignment))
                elif restored_inherited_state:  # pragma: no cover - marker requires a generated base
                    model.clear_imports_cache()
                continue
            field_policy = Parser._get_constructor_field_policy(model)
            field_adjustments: Iterator[tuple[DataModelFieldBase, _ConstructorFieldAdjustment]] = (
                (field, adjustment)
                for field in model.fields
                if (
                    adjustment := self.__get_constructor_field_adjustment(
                        field,
                        inherited,
                        field_policy,
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
                    self.generation_store.set_fields(model, sorted(model.fields, key=field_policy.has_assignment))
                continue

            _apply_constructor_field_adjustments(
                model,
                first_adjustment,
                field_adjustments,
            )
            model.clear_imports_cache()
            self.generation_store.set_fields(model, sorted(model.fields, key=field_policy.has_assignment))

    def __has_local_constructor_ordering_conflict(  # noqa: PLR6301
        self,
        model: DataModel,
        field_policy: _ConstructorFieldPolicy,
    ) -> bool:
        """Return whether a local required field follows a positional default."""
        if not model.SUPPORTS_KW_ONLY:
            return False
        saw_assignment = False
        for field in model.fields:
            if not field_policy.participates(field):
                continue
            kw_only = field.constructor_keyword_only
            if kw_only is True or (kw_only is None and model.has_keyword_only_definition()):
                continue
            if field_policy.has_assignment(field):
                saw_assignment = True
            elif saw_assignment:
                return True
        return False

    @classmethod
    def __get_inherited_constructor_info(cls, model: DataModel) -> _InheritedConstructorInfo | None:
        """Return inherited value leaks and exact positional ordering conflicts."""
        if not model.SUPPORTS_KW_ONLY:
            return None
        if not model.base_classes:
            return None

        field_policy = cls._get_constructor_field_policy(model)
        inherited_models = linearize_data_models([
            base.reference.source
            for base in model.base_classes
            if base.reference and isinstance(base.reference.source, DataModel)
        ])
        if not inherited_models:
            return None  # pragma: no cover

        supports_inherited_override = model.SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT
        required_assignment_names: set[str] = set()
        effective_fields: dict[str, tuple[DataModelFieldBase, DataModel, bool | None]] = {}
        for inherited_model in reversed(inherited_models):
            for field in inherited_model.fields:
                if not (field_name := field.name):
                    continue
                has_default: bool | None = None
                if supports_inherited_override:
                    has_default, has_value_default = field_policy.classify_default(field)
                    if has_value_default or (has_default and model.REQUIRES_EXPLICIT_INHERITED_FACTORY_OVERRIDE):
                        required_assignment_names.add(field_name)
                effective_fields[field_name] = field, inherited_model, has_default
        for field in model.fields:
            if field.name:
                effective_fields[field.name] = field, model, None

        ordering_conflicts = cls.__get_constructor_ordering_conflicts(
            model,
            effective_fields,
            field_policy,
            required_assignment_names,
        )
        return _InheritedConstructorInfo(
            frozenset(required_assignment_names),
            ordering_conflicts,
        )

    @classmethod
    def __get_constructor_ordering_conflicts(
        cls,
        model: DataModel,
        effective_fields: Mapping[str, tuple[DataModelFieldBase, DataModel, bool | None]],
        field_policy: _ConstructorFieldPolicy,
        required_assignment_names: set[str],
    ) -> frozenset[str]:
        """Return child positional fields that follow an effective positional default."""
        seen_constructor_default = False
        seen_signature_default = False
        ordering_conflicts: set[str] = set()
        for field_name, (field, declaring_model, inherited_has_default) in effective_fields.items():
            if not field_policy.participates(field):
                continue
            kw_only = field.constructor_keyword_only
            if kw_only is True or (kw_only is None and declaring_model.has_keyword_only_definition()):
                continue
            required_assignment_is_constructor_default = (
                model.REQUIRED_ASSIGNMENT_COUNTS_AS_CONSTRUCTOR_DEFAULT
                and field.required
                and not field.use_default_with_required
                and (field_name in required_assignment_names or field_policy.has_assignment(field))
            )
            field_has_signature_default = (
                False
                if required_assignment_is_constructor_default
                else (
                    field_policy.classify_default(field)[0] if inherited_has_default is None else inherited_has_default
                )
            )
            field_has_constructor_default = required_assignment_is_constructor_default or field_has_signature_default
            if declaring_model is model and (
                (seen_constructor_default and not field_has_constructor_default)
                or (seen_signature_default and not field_has_signature_default)
            ):
                ordering_conflicts.add(field_name)
            seen_constructor_default = seen_constructor_default or field_has_constructor_default
            seen_signature_default = seen_signature_default or field_has_signature_default
        return frozenset(ordering_conflicts)

    @staticmethod
    def _get_field_assignment_checker(model: DataModel) -> Callable[[DataModelFieldBase], bool]:
        return type(model).FIELD_ASSIGNMENT_CHECKER

    @classmethod
    def _get_constructor_field_policy(cls, model: DataModel) -> _ConstructorFieldPolicy:
        """Collect constructor policies owned by the output model."""
        model_type = type(model)
        return _ConstructorFieldPolicy(
            cls._get_field_assignment_checker(model),
            model_type.FIELD_DEFAULT_CLASSIFIER,
            model_type.FIELD_PARTICIPATES_IN_CONSTRUCTOR,
        )

    def __get_constructor_field_adjustment(  # noqa: PLR6301
        self,
        field: DataModelFieldBase,
        inherited: _InheritedConstructorInfo,
        field_policy: _ConstructorFieldPolicy,
        *,
        supports_inherited_override: bool,
    ) -> _ConstructorFieldAdjustment | None:
        """Return the exact explicit assignment needed for a required child field."""
        if not field_policy.participates(field):
            return None
        if field.name in inherited.ordering_conflicts:
            return "keyword_only"
        if field_policy.has_assignment(field):
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

    def __apply_type_overrides(self, models: list[DataModel]) -> None:
        """Replace type references with custom import types.

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
            if not self._model_type_override_imports:
                continue
            base_classes_overridden = False
            for base_class in model.base_classes:
                if self._apply_override_to_data_type(base_class):
                    base_classes_overridden = True
                    for import_ in base_class.all_imports:
                        Parser._append_model_import(model, import_)
            if base_classes_overridden:
                model.clear_imports_cache()

    def _apply_override_to_field(self, field: DataModelFieldBase, override_import: Import) -> None:
        """Apply override to entire field's data_type."""
        data_type = deepcopy(field.data_type)
        data_type.import_ = override_import
        data_type.alias = override_import.import_
        self.generation_store.replace_field_type(field, data_type)
        self.generation_store.detach_data_type_ref(field.data_type)
        self.generation_store.set_nested_data_types(field.data_type, [])

    @staticmethod
    def _append_model_import(model: DataModel, import_: Import) -> None:
        """Append an import to a model once."""
        if import_ in model.imports:
            return
        model._additional_imports.append(import_)  # noqa: SLF001

    def _apply_override_to_data_type(self, data_type: DataType) -> bool:
        """Recursively apply model-level overrides to a DataType."""
        overridden = False
        if data_type.reference and (override_import := self._model_type_override_imports.get(data_type.reference.name)):
            data_type.import_ = override_import
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
    def __disable_union_operator_for_forward_ref(data_type: DataType) -> None:
        data_type.use_union_operator = False
        parent = data_type.parent
        while isinstance(parent, DataType):
            if parent.is_union:
                parent.use_union_operator = False
            parent = parent.parent

    @classmethod
    def __update_type_aliases(
        cls,
        models: list[DataModel],
        pydantic_v2_root_model_type: type[DataModel] | None,
        *,
        use_deferred_annotations: bool = True,
        can_retain_cache: bool,
    ) -> None:
        """Update type aliases and RootModels to properly handle forward references per PEP 484.

        When annotations are not deferred (no ``from __future__ import annotations`` and no
        native PEP 649 deferred annotations), self-referencing fields in regular DataModel
        classes also need their type aliased to a quoted string so that static analysers
        (e.g. Ruff F821) and older runtimes do not trip over the forward reference.
        """
        model_index: dict[str, int] = {m.class_name: i for i, m in enumerate(models)}

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
            if not process_all_fields and not any(
                field.requires_immediate_forward_reference_resolution for field in model.fields
            ):
                continue
            if isinstance(model, TypeStatement):
                continue

            has_aliased_forward_ref = False
            for field in model.fields:
                if not process_all_fields and not field.requires_immediate_forward_reference_resolution:
                    continue
                for data_type in field.data_type.all_data_types:
                    if not data_type.reference:
                        continue
                    source = data_type.reference.source
                    if not isinstance(source, DataModel):
                        continue  # pragma: no cover
                    if isinstance(source, TypeStatement):
                        continue  # pragma: no cover
                    if source.module_path != model.module_path:
                        continue
                    name = data_type.reference.short_name
                    source_index = model_index.get(name)
                    if source_index is not None and source_index >= i:
                        data_type.alias = f'"{name}"'
                        cls.__disable_union_operator_for_forward_ref(data_type)
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

            self.generation_store.update_model_reference(  # pragma: no cover
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

    def __alias_shadowed_imports(
        self,
        models: list[DataModel],
        all_model_field_names: set[str],
        *,
        can_retain_cache: bool,
        module_imports: Imports | None = None,
    ) -> None:
        ordinary_aliases, has_python_type, has_runtime_expressions = _ordinary_field_shadow_aliases(
            models,
            all_model_field_names,
        )
        if not (has_python_type or has_runtime_expressions):
            if ordinary_aliases:
                _apply_structured_import_aliases(models, ordinary_aliases, can_retain_cache=can_retain_cache)
            return
        self._has_bound_python_types = self._has_bound_python_types or has_python_type
        if has_runtime_expressions:
            self._register_runtime_expression()
        # Keep structured import machinery lazy: ordinary schemas must not
        # pay its import, allocation, or module-scan cost.
        from datamodel_code_generator.parser._python_type_imports import (  # noqa: PLC0415
            resolve_structured_import_aliases,
        )

        data_types = (data_type for _, _, data_type in iter_models_field_data_types(models))
        aliased_imports = resolve_structured_import_aliases(
            data_types,
            models,
            all_model_field_names,
            (self.imports,) if module_imports is None else (self.imports, module_imports),
        )
        if not aliased_imports:
            return
        _apply_structured_import_aliases(models, aliased_imports, can_retain_cache=can_retain_cache)
        if module_imports is not None:
            for aliased_import in aliased_imports.values():
                module_imports.apply_alias(aliased_import)

    def _register_runtime_expression(self) -> None:
        """Mark a parser-owned expression created after the initial import scan."""
        self._has_runtime_expressions = True

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

            target_models = [
                m for m in models if m.SUPPORTS_GENERIC_BASE_CLASS and not isinstance(m, self.data_model_root_type)
            ]

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
                imports.remove(original_import)
                from_ = relative(current_module_name, first_root_module_name)[0]
                from_ = (
                    f"{from_}{first_root_module[-1].replace('.py', '')}"
                    if from_.endswith(".")
                    else f"{from_}.{first_root_module[-1].replace('.py', '')}"
                )
                imports.append(Import(from_=from_, import_=base_class_ref.name))

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
    ) -> dict[str, list[tuple[str, tuple[str, ...], str]]]:
        """Resolve name collisions in exports based on strategy."""
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
                    item[0], item[1], used, minimal=effective == AllExportsCollisionStrategy.MinimalPrefix
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
    def _raise_collision_error(  # pragma: no cover
        cls,
        by_name: dict[str, list[tuple[str, tuple[str, ...], str]]],
        colliding: set[str],
    ) -> None:
        """Raise an error with collision details."""
        details = []
        for n in colliding:
            if len(items := by_name[n]) > 1:
                details.append(f"  '{n}' is defined in: {', '.join(f'.{s}' for _, _, s in items)}")
            else:
                details.append(f"  '{n}' conflicts with a model in __init__.py")
        raise Error(
            "Name collision detected with --all-exports-scope:\n"
            + "\n".join(details)
            + "\n\nUse --all-exports-collision-strategy to specify how to handle collisions."
        )

    @staticmethod
    def _make_prefixed_name(name: str, path: tuple[str, ...], used: set[str], *, minimal: bool) -> str:
        """Generate a prefixed name, using minimal or full prefix."""
        if minimal:
            for depth in range(1, len(path) + 1):
                if (candidate := "".join(p.title().replace("_", "") for p in path[-depth:]) + name) not in used:
                    return candidate
        return "".join(p.title().replace("_", "") for p in path) + name

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
    ) -> tuple[defaultdict[tuple[str, ...], list[tuple[str, str]]], dict[str, str]]:
        """Rename duplicate classes and relocate models to internal module.

        Returns:
            Tuple of:
            - Mapping from original module to list of (original_name, new_name) tuples.
            - Mapping from old reference paths to new reference paths.
        """
        class_name_counts = Counter(model.class_name for model in all_scc_models)
        class_name_seen: dict[str, int] = {}
        internal_module_str = ".".join(internal_module)
        module_class_mappings: defaultdict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
        path_mapping: dict[str, str] = {}

        with self.generation_store.defer_refresh():
            for model in all_scc_models:
                original_class_name = model.class_name
                original_module = model_to_original_module[id(model)]
                old_path = model.path  # Save old path before updating

                if class_name_counts[original_class_name] > 1:
                    seen_count = class_name_seen.get(original_class_name, 0)
                    new_class_name = f"{original_class_name}_{seen_count}" if seen_count > 0 else original_class_name
                    class_name_seen[original_class_name] = seen_count + 1
                else:
                    new_class_name = original_class_name

                new_path = f"{internal_module_str}.{new_class_name}"
                self.generation_store.update_model_reference(
                    model,
                    reference_name=new_class_name,
                    new_path=new_path,
                    new_file_path=internal_path,
                )

                module_class_mappings[original_module].append((original_class_name, new_class_name))
                path_mapping[old_path] = new_path

        return module_class_mappings, path_mapping

    def __build_module_dependency_graph(
        self,
        module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
    ) -> dict[tuple[str, ...], set[tuple[str, ...]]]:
        """Build a directed graph of module dependencies."""
        path_to_module: dict[str, tuple[str, ...]] = {}
        for module, models in module_models_list:
            for model in models:
                path_to_module[model.path] = module

        graph: dict[tuple[str, ...], set[tuple[str, ...]]] = {}

        def add_cross_module_edge(ref_path: str, source_module: tuple[str, ...]) -> None:
            """Add edge if ref_path points to a different module."""
            if ref_path in path_to_module:
                target_module = path_to_module[ref_path]
                if target_module != source_module:
                    graph[source_module].add(target_module)

        for module, models in module_models_list:
            graph[module] = set()

            for model in models:
                for reference_path in self.generation_store.index.reference_classes_for_model(model):
                    add_cross_module_edge(reference_path, module)

        return graph

    def __resolve_circular_imports(  # noqa: PLR0914
        self,
        module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
    ) -> tuple[
        list[tuple[tuple[str, ...], list[DataModel]]],
        set[tuple[str, ...]],
        dict[tuple[str, ...], tuple[tuple[str, ...], list[tuple[str, str]]]],
        dict[str, str],
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
            - Path mapping: old_reference_path -> new_reference_path
        """
        graph = self.__build_module_dependency_graph(module_models_list)

        circular_sccs = find_circular_sccs(graph)

        forwarder_map: dict[tuple[str, ...], tuple[tuple[str, ...], list[tuple[str, str]]]] = {}
        all_path_mappings: dict[str, str] = {}

        if not circular_sccs:
            return module_models_list, set(), forwarder_map, all_path_mappings

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
            module_class_mappings, path_mapping = self.__rename_and_relocate_scc_models(
                all_scc_models, model_to_original_module, internal_module, internal_path
            )
            all_scc_models = _sort_internal_module_models(all_scc_models, self.pydantic_v2_root_model_type)
            all_path_mappings.update(path_mapping)

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

        return new_module_models, internal_modules_created, forwarder_map, all_path_mappings

    def __get_resolve_reference_action_parts(
        self,
        models: list[DataModel],
        require_update_action_models: list[str],
        *,
        use_deferred_annotations: bool,
    ) -> list[str]:
        """Return the trailing rebuild/update calls for the given module's models."""
        if self.dump_resolve_reference_action is None:
            return []

        require_update_action_model_paths = set(require_update_action_models)
        required_paths_in_module = {m.path for m in models if m.path in require_update_action_model_paths}

        if (
            use_deferred_annotations
            and required_paths_in_module
            and get_resolve_reference_action_capabilities(self.dump_resolve_reference_action).filter_forward_references
        ):
            module_positions = {m.reference.short_name: i for i, m in enumerate(models) if m.reference}
            module_model_names = set(module_positions)

            forward_needed: set[str] = set()
            for model in models:
                if model.path not in required_paths_in_module or not model.reference:
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
                    forward_needed.add(model.path)

            # Propagate requirement through inheritance.
            changed = True
            required_filtered = set(forward_needed)
            while changed:
                changed = False
                for model in models:
                    if not model.reference or model.path in required_filtered:
                        continue
                    base_paths = {b.reference.path for b in model.base_classes if b.reference}
                    if base_paths & required_filtered:
                        required_filtered.add(model.path)
                        changed = True

            required_paths_in_module = required_filtered

        return [
            "\n",
            self.dump_resolve_reference_action(
                m.reference.short_name for m in models if m.reference and m.path in required_paths_in_module
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
            self.target_python_version.has_native_deferred_annotations or (with_import and not disable_future_imports)
        )

    def _has_forward_references(self, models: list[DataModel]) -> bool:  # noqa: PLR6301
        """Return whether a module contains a self or forward model reference.

        This remains an instance method because ``snooper_to_methods`` does not
        preserve staticmethod descriptors on parser subclasses.
        """
        positions = {model.path: index for index, model in enumerate(models)}
        for index, model in enumerate(models):
            for field in model.fields:
                for data_type in field.data_type.all_data_types:
                    if (
                        (reference := data_type.reference)
                        and (reference_index := positions.get(reference.path)) is not None
                        and reference_index >= index
                    ):
                        return True
        return False

    def _requires_explicit_deferred_annotations(self, models: list[DataModel], config: ParseConfig) -> bool:
        """Return whether a module needs the future annotations import."""
        if not (config.with_import and config.use_deferred_annotations):
            return False
        return self._has_forward_references(models)

    def _get_module_future_imports(
        self,
        ctx: ModuleContext,
        config: ParseConfig,
        future_imports_str: str,
    ) -> str:
        """Return future imports required by a single generated module."""
        if not (
            self.data_model_type.REQUIRES_EXPLICIT_DEFERRED_ANNOTATIONS_FOR_FORWARD_REFS
            and self.target_python_version.has_native_deferred_annotations
        ):
            return future_imports_str
        if not self._requires_explicit_deferred_annotations(ctx.models, config):
            return future_imports_str
        ctx.imports.append(IMPORT_ANNOTATIONS)
        return "\n".join(import_ for import_ in (future_imports_str, str(ctx.imports.extract_future())) if import_)

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

        if (
            with_import
            and not disable_future_imports
            and not self.target_python_version.has_native_deferred_annotations
        ):
            self.imports.append(IMPORT_ANNOTATIONS)

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

    def _find_invalid_inferred_modules(  # noqa: PLR6301
        self, sorted_data_models: SortedDataModels
    ) -> set[ModulePath]:
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
                )
                is None
            ):
                invalid_modules.add(module)
        return invalid_modules

    @staticmethod
    def _stdout_model_fingerprint(model: DataModel) -> HashableComparable:
        """Return the final rendered definition without using the pre-processing dedup cache."""
        import_fingerprint = tuple(
            sorted({
                (False, "", import_.import_, "")
                if "." in import_.import_
                else (
                    import_.from_ is not None,
                    import_.from_ or "",
                    import_.import_,
                    "" if not import_.alias or import_.alias == import_.import_ else import_.alias,
                )
                for import_ in model.imports
            })
        )
        return model.render(class_name=model.class_name), import_fingerprint

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
            self.generated_model_inventory = tuple(sorted(model.path for ctx in contexts for model in ctx.models))
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
        self.generated_model_inventory = tuple(sorted(model.path for model in live_models))
        self.source_data_fingerprint = source_fingerprint

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
        require_update_action_models: list[str],
        module_split_mode: ModuleSplitMode | None,
    ) -> tuple[
        ModuleModels,
        set[ModulePath],
        ForwarderMap,
        dict[str, str],
        dict[DataModel, tuple[ModulePath, list[DataModel]]],
        dict[str, str],
    ]:
        """Build module structure from sorted models."""
        module_models = _group_models_by_module(sorted_data_models.values(), module_split_mode)
        model_to_module_models, model_path_to_module_name = _index_module_models(module_models, module_split_mode)
        for _, models in module_models:
            self.__delete_duplicate_models(models)
            self.__replace_duplicate_name_in_module(models)

        if self._apply_forced_invalid_dotted_stdout_repair(module_models, module_split_mode):
            live_model_ids = {id(model) for _, models in module_models for model in models}
            module_models = _group_models_by_module(
                (model for model in sorted_data_models.values() if id(model) in live_model_ids),
                module_split_mode,
            )
            model_to_module_models, model_path_to_module_name = _index_module_models(module_models, module_split_mode)

        shared_module_entry = self.__reuse_model_tree_scope(
            module_models, require_update_action_models, module_split_mode
        )
        if shared_module_entry:
            module_models.insert(0, shared_module_entry)
            if module_split_mode == ModuleSplitMode.Single:
                model_to_module_models, model_path_to_module_name = _index_module_models(
                    module_models, module_split_mode
                )

        module_models, internal_modules, forwarder_map, path_mapping = self.__resolve_circular_imports(module_models)

        if path_mapping:
            require_update_action_models[:] = [path_mapping.get(path, path) for path in require_update_action_models]

        return (
            module_models,
            internal_modules,
            forwarder_map,
            path_mapping,
            model_to_module_models,
            model_path_to_module_name,
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
            model_path_to_module_name.setdefault(model.path, module_name)
        can_retain_cache = _can_retain_model_imports_cache(
            models,
            configured_types_are_builtin=self._configured_generation_types_are_builtin,
        )

        all_module_fields = {field.name for model in models for field in model.fields if field.name is not None}
        scoped_model_resolver = ModelResolver(exclude_names=all_module_fields)

        can_retain_cache = self.__prepare_module_models(
            models,
            all_module_fields=all_module_fields,
            imports=imports,
            scoped_model_resolver=scoped_model_resolver,
            is_init=is_init,
            internal_modules=internal_modules,
            model_path_to_module_name=model_path_to_module_name,
            can_retain_cache=can_retain_cache,
        )
        unused_models_start = len(unused_models)
        models = self.__process_module_models(
            models,
            unused_models=unused_models,
            imports=imports,
            scoped_model_resolver=scoped_model_resolver,
            model_path_to_module_name=model_path_to_module_name,
            require_update_action_models=require_update_action_models,
            use_deferred_annotations=config.use_deferred_annotations,
            can_retain_cache=can_retain_cache,
        )
        current_unused_models: Sequence[DataModel] = ()
        if unused_models_start != len(unused_models):
            current_unused_models = unused_models[unused_models_start:]
        self.__finalize_module_models(
            models,
            unused_models=current_unused_models,
            use_deferred_annotations=config.use_deferred_annotations,
            can_retain_cache=can_retain_cache,
        )

        return ModuleContext(module, module_, models, is_init, imports, scoped_model_resolver)

    def __prepare_module_models(  # noqa: PLR0913
        self,
        models: list[DataModel],
        *,
        all_module_fields: set[str],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        is_init: bool,
        internal_modules: set[ModulePath],
        model_path_to_module_name: dict[str, str],
        can_retain_cache: bool,
    ) -> bool:
        """Prepare aliases, imports, and inherited enums before default processing."""
        if "Decimal" in all_module_fields:
            self._constrained_decimal_alias_model_ids.update(map(id, models))
        self.__alias_shadowed_imports(models, all_module_fields, can_retain_cache=can_retain_cache)
        self.__override_required_field(models, can_retain_cache=can_retain_cache)
        self.__replace_unique_list_to_set(models, can_retain_cache=can_retain_cache)
        self.__change_from_import(
            models,
            imports,
            scoped_model_resolver,
            init=is_init,
            internal_modules=internal_modules,
            model_path_to_module_name=model_path_to_module_name,
        )
        self.__extract_inherited_enum(models)
        return can_retain_cache and _can_retain_model_imports_cache(
            models,
            configured_types_are_builtin=self._configured_generation_types_are_builtin,
        )

    def __process_module_models(  # noqa: PLR0913
        self,
        models: list[DataModel],
        *,
        unused_models: list[DataModel],
        imports: Imports,
        scoped_model_resolver: ModelResolver,
        model_path_to_module_name: dict[str, str],
        require_update_action_models: list[str],
        use_deferred_annotations: bool,
        can_retain_cache: bool,
    ) -> list[DataModel]:
        """Apply defaults and model transforms before final type adjustments."""
        self.__set_reference_default_value_to_field(models, can_retain_cache=can_retain_cache)
        self.__reuse_model(models, require_update_action_models)
        try:
            self.__collapse_root_models(
                models,
                unused_models,
                imports,
                scoped_model_resolver,
                model_path_to_module_name,
            )
        except RecursionError as exc:
            raise _CollapseRootModelsRecursionError from exc
        self.__set_default_enum_member(models, can_retain_cache=can_retain_cache)
        self.__sort_models(models, imports, use_deferred_annotations=use_deferred_annotations)
        self.__change_field_name(models, can_retain_cache=can_retain_cache)
        self.__restore_inherited_field_names_for_unique_aliases(models)
        self.__apply_discriminator_type(models, imports, can_retain_cache=can_retain_cache)
        self.__set_one_literal_on_default(models, can_retain_cache=can_retain_cache)
        self.__fix_constructor_field_ordering(models)

        return self.__remove_overridden_models(models)

    def __finalize_module_models(
        self,
        models: list[DataModel],
        *,
        unused_models: Sequence[DataModel],
        use_deferred_annotations: bool,
        can_retain_cache: bool,
    ) -> None:
        """Apply final type metadata and invalidate imports only when required."""
        self.__apply_type_overrides(models)
        self.__update_type_aliases(
            models,
            self.pydantic_v2_root_model_type,
            use_deferred_annotations=use_deferred_annotations,
            can_retain_cache=can_retain_cache,
        )
        if not unused_models:
            live_models = models
        else:
            unused_model_ids = {id(model) for model in unused_models}
            live_models = [model for model in models if id(model) not in unused_model_ids]
        self.__set_validate_default_on_fields(live_models, can_retain_cache=can_retain_cache)
        if not can_retain_cache:
            _clear_model_imports_cache(models)

    def _finalize_structured_imports(self, contexts: list[ModuleContext]) -> None:
        """Resolve aliases introduced after generic base classes are applied."""
        has_structured_imports = self._has_bound_python_types or self._has_runtime_expressions
        if not has_structured_imports and not self._constrained_decimal_alias_model_ids:
            return
        if models := [
            model for ctx in contexts for model in ctx.models if id(model) in self._constrained_decimal_alias_model_ids
        ]:
            has_structured_imports = self.__normalize_default_value_constraints(models) or has_structured_imports
        if not has_structured_imports:
            return
        for ctx in contexts:
            all_module_fields = {field.name for model in ctx.models for field in model.fields if field.name is not None}
            self.__alias_shadowed_imports(
                ctx.models,
                all_module_fields,
                can_retain_cache=_can_retain_model_imports_cache(
                    ctx.models,
                    configured_types_are_builtin=self._configured_generation_types_are_builtin,
                ),
                module_imports=ctx.imports,
            )

    def _merge_runtime_expression_imports(  # noqa: PLR6301
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
                    ctx.imports.append(runtime_imports)

    def _prepare_schema_runtime_validation_module_code(self, contexts: list[ModuleContext]) -> None:
        """Plan opt-in module helpers before their imports are collected."""
        for ctx in contexts:
            self.data_model_type.prepare_module_code(ctx.models)

    def _sync_schema_runtime_validation_module_imports(  # noqa: PLR6301
        self,
        contexts: list[ModuleContext],
        model_imports: dict[DataModel, tuple[Import, ...]],
    ) -> None:
        """Merge imports added by module planning into finalized module imports.

        This remains an instance method because ``snooper_to_methods`` does not
        preserve inherited static methods on parser subclasses.
        """
        for ctx in contexts:
            for model in ctx.models:
                prepared_imports = model.imports
                ctx.imports.append(import_ for import_ in prepared_imports if import_ not in model_imports[model])
                model_imports[model] = prepared_imports

    def _finalize_modules(  # noqa: PLR0912
        self,
        contexts: list[ModuleContext],
        unused_models: list[DataModel],
        model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]],
        module_to_import: dict[ModulePath, Imports],
    ) -> None:
        """Finalize module processing: apply generic base class and remove unused imports."""
        self.__apply_generic_base_class(contexts)
        all_models = [model for ctx in contexts for model in ctx.models]
        self.__mark_set_item_models_hashable(all_models)
        self._finalize_structured_imports(contexts)
        if self.use_default_factory_for_optional_nested_models:
            # Inherited defaults may have changed since a consumer first queried its factory imports.
            _clear_model_imports_cache(all_models)
        if self.use_root_model_sequence_interface:
            for model in all_models:
                model.finalize_sequence_interface()
        model_imports = {model: model.imports for ctx in contexts for model in ctx.models}

        for ctx in contexts:
            for model in ctx.models:
                ctx.imports.append(model_imports[model])

        for unused_model in unused_models:
            module, models = model_to_module_models[unused_model]
            if unused_model in models:  # pragma: no branch
                imports = module_to_import[module]
                imports.remove(model_imports.get(unused_model, unused_model.imports))
                models.remove(unused_model)

        if self.config.aliases:
            for ctx in contexts:
                for model in ctx.models:
                    self.model_resolver.validate_explicit_field_aliases(model.fields, self.field_name_model_type)

        if self.generate_schema_validators:
            self._prepare_schema_runtime_validation_module_code(contexts)
            self._sync_schema_runtime_validation_module_imports(contexts, model_imports)

        if self._has_runtime_expressions:
            self._merge_runtime_expression_imports(contexts, model_imports)

        for ctx in contexts:
            used_names = self._collect_used_names_from_models(ctx.models, model_imports)
            ctx.imports.remove_unused(used_names)

        for ctx in contexts:
            self.data_model_type.resolve_module_import_conflicts(ctx.models, model_imports, ctx.imports)

        for ctx in contexts:
            _bind_module_field_names(ctx.models, ctx.imports)

        renamed_models = False
        for ctx in contexts:
            renamed_models = (
                self.__change_imported_model_name(ctx.models, ctx.imports, ctx.scoped_model_resolver) or renamed_models
            )
        if self.use_root_model_sequence_interface and renamed_models:
            for model in all_models:
                model.finalize_sequence_interface()
        if self.generate_schema_validators and renamed_models:
            # Helper-name reservations include referenced models from other modules,
            # so a rare import collision must invalidate every module plan.
            for ctx in contexts:
                self.data_model_type.invalidate_module_code_cache(ctx.models)
            self._prepare_schema_runtime_validation_module_code(contexts)
            # Renaming only changes synthetic helper names; capabilities and their
            # import set stay invariant. Keep snapshots synchronized defensively.
            self._sync_schema_runtime_validation_module_imports(contexts, model_imports)

        match self._import_overrides:
            case None:
                return
            case overrides:
                _remap_imports(self.imports, overrides)
                for ctx in contexts:
                    _remap_imports(ctx.imports, overrides)
        return

    def _set_nested_model_default_factory_metadata(
        self,
        contexts: list[ModuleContext],
        require_update_action_models: list[str],
    ) -> None:
        """Record declaration order and recursive model components for default factories."""
        recursive_paths_by_model: dict[str, frozenset[str]] = {}
        if require_update_action_models:
            models = [model for ctx in contexts for model in ctx.models]
            live_paths = {model.path for model in models}
            graph = {
                (model.path,): {
                    (reference_path,)
                    for reference_path in self.generation_store.index.reference_classes_for_model(model)
                    if reference_path in live_paths
                }
                for model in models
            }
            for component in find_circular_sccs(graph):
                recursive_paths = frozenset(node[0] for node in component)
                recursive_paths_by_model.update((path, recursive_paths) for path in recursive_paths)

        for module_index, ctx in enumerate(contexts):
            _set_nested_model_default_factory_order(ctx.models, module_index, recursive_paths_by_model)

    def _generate_module_output(  # noqa: PLR0912, PLR0913, PLR0917
        self,
        ctx: ModuleContext,
        config: ParseConfig,
        contexts: list[ModuleContext],
        forwarder_map: ForwarderMap,
        require_update_action_models: list[str],
        future_imports_str: str,
    ) -> Result | None:
        """Generate output for a single module."""
        result: list[str] = []
        export_imports: Imports | None = None
        module_future_imports_str = self._get_module_future_imports(ctx, config, future_imports_str)

        if config.all_exports_scope is not None and ctx.module[-1] == "__init__.py":
            child_exports = self._collect_exports_for_init(ctx.module, contexts, config.all_exports_scope)
            if child_exports:
                local_model_names = {
                    m.reference.short_name
                    for m in ctx.models
                    if m.reference and not m.reference.short_name.startswith("_")  # pragma: no branch
                }
                resolved_exports = self._resolve_export_collisions(
                    child_exports, config.all_exports_collision_strategy, local_model_names
                )
                export_imports = self._build_all_exports_code(resolved_exports)

        if ctx.models:
            if config.with_import:
                import_parts = [s for s in [module_future_imports_str, str(self.imports), str(ctx.imports)] if s]
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
                require_update_action_models,
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
                resolved = self._resolve_export_collisions(child_exports, config.all_exports_collision_strategy, set())
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
    def _model_metadata(
        cls,
        model: DataModel,
        module: ModulePath,
        source_reference_paths: Mapping[DataModel, str],
    ) -> GeneratedModelMetadata:
        source_ref = source_reference_paths[model]
        title = model.extra_template_data.get("title")
        return {
            "class_name": model.class_name,
            "name": model.name,
            "module": _module_name_from_module_path(module),
            "source_ref": source_ref,
            "source_path": _source_path_from_reference_path(source_ref),
            "title": title if isinstance(title, str) else None,
            "fields": [cls._field_metadata(field, is_root_model=model.IS_ROOT_MODEL) for field in model.fields],
        }

    @classmethod
    def _build_model_metadata(
        cls,
        contexts: list[ModuleContext],
        source_reference_paths: Mapping[DataModel, str],
    ) -> ModelMetadata:
        return {
            "version": 1,
            "models": [
                cls._model_metadata(model, ctx.module, source_reference_paths)
                for ctx in contexts
                for model in ctx.models
                if model in source_reference_paths
            ],
        }

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

    def dispose(self) -> None:
        """Release parser-owned resources after a facade-managed run."""
        self._dispose()

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
        return self.__prepare_parse(
            with_import=with_import,
            format_=format_,
            settings_path=settings_path,
            disable_future_imports=disable_future_imports,
            all_exports_scope=all_exports_scope,
            all_exports_collision_strategy=all_exports_collision_strategy,
            module_split_mode=module_split_mode,
            collect_model_metadata=collect_model_metadata,
        )

    def __prepare_parse(  # noqa: PLR0913
        self,
        *,
        with_import: bool | None,
        format_: bool | None,
        settings_path: Path | None,
        disable_future_imports: bool,
        all_exports_scope: AllExportsScope | None,
        all_exports_collision_strategy: AllExportsCollisionStrategy | None,
        module_split_mode: ModuleSplitMode | None,
        collect_model_metadata: bool,
    ) -> str | dict[tuple[str, ...], Result]:
        """Prepare parsed models and formatting before processing output modules."""
        if (custom_template_dir := self.custom_template_dir) is not None:
            _refresh_custom_template_paths(custom_template_dir)
        self._set_typed_extra_annotation_mode(
            use_deferred_annotations=self._uses_deferred_annotations(with_import, disable_future_imports)
        )
        run_context = self._run_context
        suppress_parse_warnings = run_context is not None and run_context.suppress_parse_warnings
        try:
            if suppress_parse_warnings:
                self._run_with_suppressed_warnings(self.parse_raw)
            else:
                self.parse_raw()
        finally:
            self._close_http_fetch_session()
        if suppress_parse_warnings:
            self._run_with_suppressed_warnings(self._report_parse_diagnostics)
        else:
            self._report_parse_diagnostics()

        config = self._prepare_parse_config(
            with_import,
            disable_future_imports,
            all_exports_scope,
            all_exports_collision_strategy,
            module_split_mode,
        )

        _, sorted_data_models, require_update_action_models = sort_data_models(
            self.results,
            generation_index=self.generation_store.index,
            pydantic_v2_root_model_type=self.pydantic_v2_root_model_type,
        )
        source_reference_paths: Mapping[DataModel, str] | None = (
            {
                model: model.__dict__.get(_SOURCE_REFERENCE_PATH_KEY, model.reference.path)
                for model in sorted_data_models.values()
            }
            if collect_model_metadata
            else None
        )
        sort_base_classes_for_mro(sorted_data_models, self.generation_store)

        (
            module_models,
            internal_modules,
            forwarder_map,
            _path_mapping,
            model_to_module_models,
            model_path_to_module_name,
        ) = self._build_module_structure(sorted_data_models, require_update_action_models, module_split_mode)

        if format_:
            match self.formatters:
                case [] if (
                    not self.custom_formatter
                    and "_build_code_formatter" not in self.__dict__
                    and type(self)._build_code_formatter is Parser._build_code_formatter  # noqa: SLF001
                ):
                    pass
                case _:
                    config = config._replace(
                        code_formatter=self._build_code_formatter(
                            settings_path,
                            is_multi_module_output=self.defer_formatting or len(module_models) > 1,
                        ),
                    )

        self._uses_standard_generation_templates = bool(
            (code_formatter := config.code_formatter)
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

        return self.__process_modules(
            module_models,
            internal_modules=internal_modules,
            forwarder_map=forwarder_map,
            model_to_module_models=model_to_module_models,
            model_path_to_module_name=model_path_to_module_name,
            require_update_action_models=require_update_action_models,
            sorted_data_models=sorted_data_models,
            source_reference_paths=source_reference_paths,
            config=config,
        )

    def __process_modules(  # noqa: PLR0913
        self,
        module_models: ModuleModels,
        *,
        internal_modules: set[ModulePath],
        forwarder_map: ForwarderMap,
        model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]],
        model_path_to_module_name: dict[str, str],
        require_update_action_models: list[str],
        sorted_data_models: SortedDataModels,
        source_reference_paths: Mapping[DataModel, str] | None,
        config: ParseConfig,
    ) -> str | dict[tuple[str, ...], Result]:
        """Process every module into one shared result mapping before rendering."""
        results: dict[ModulePath, Result] = {}
        unused_models: list[DataModel] = []
        module_to_import: dict[ModulePath, Imports] = {}
        contexts: list[ModuleContext] = []

        if self.collapse_root_models:
            self.__set_circular_root_model_paths(
                module_models,
                require_dict_key_edge=not self.run_context.preserve_circular_root_models,
            )

        for module_, models in module_models:
            ctx = self._process_single_module(
                module_,
                models,
                results,
                config,
                internal_modules,
                model_path_to_module_name,
                require_update_action_models,
                unused_models,
            )
            module_to_import[module_] = ctx.imports
            contexts.append(ctx)

        self._finalize_modules(contexts, unused_models, model_to_module_models, module_to_import)
        self.__warn_about_decimal_defaults()
        if self.use_default_factory_for_optional_nested_models:
            self._set_nested_model_default_factory_metadata(contexts, require_update_action_models)

        root_init: ModulePath = ("__init__.py",)
        if root_init not in results:
            top_level_dirs = {k[0] for k in results if len(k) >= 2}  # noqa: PLR2004
            if len(top_level_dirs) > 1:
                results[root_init] = Result(body="")

        return self.__render_modules(
            results,
            contexts=contexts,
            sorted_data_models=sorted_data_models,
            source_reference_paths=source_reference_paths,
            config=config,
            forwarder_map=forwarder_map,
            require_update_action_models=require_update_action_models,
        )

    def __render_modules(  # noqa: PLR0913
        self,
        results: dict[ModulePath, Result],
        *,
        contexts: list[ModuleContext],
        sorted_data_models: SortedDataModels,
        source_reference_paths: Mapping[DataModel, str] | None,
        config: ParseConfig,
        forwarder_map: ForwarderMap,
        require_update_action_models: list[str],
    ) -> str | dict[tuple[str, ...], Result]:
        """Render the shared result mapping and apply final output-only transformations."""
        future_imports = self.imports.extract_future()
        future_imports_str = str(future_imports)

        # Export depth and collision prefixes must follow the final package layout.
        if (
            self.treat_dot_as_module
            and config.all_exports_scope is not None
            and any(_expand_export_module_path(ctx.module) != ctx.module for ctx in contexts)
        ):
            contexts = [ctx._replace(module=_expand_export_module_path(ctx.module)) for ctx in contexts]

        for ctx in contexts:
            result = self._generate_module_output(
                ctx, config, contexts, forwarder_map, require_update_action_models, future_imports_str
            )
            if result is not None:
                results[ctx.module] = result

        if config.all_exports_scope is not None and not self.treat_dot_as_module:
            self._generate_empty_init_exports(results, contexts, config, future_imports_str)

        self._inspect_invalid_dotted_stdout(contexts, sorted_data_models, config, results)

        if source_reference_paths is not None:
            self.model_metadata = self._build_model_metadata(contexts, source_reference_paths)
        else:
            self.model_metadata = None

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
