"""Abstract base parser and utilities for schema parsing.

Provides the Parser abstract base class that defines the parsing algorithm,
along with helper functions for model sorting, import resolution, and
code generation.
"""

from __future__ import annotations

import contextlib
import operator
import re
from abc import ABC, abstractmethod
from collections import (
    Counter,  # noqa: F401  # Preserve the established parser.base export.
    defaultdict,
)
from contextvars import ContextVar
from copy import deepcopy  # noqa: F401  # Preserve the established parser.base export.
from dataclasses import dataclass
from functools import partial
from itertools import chain
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    Generic,
    NamedTuple,
    Optional,
    TypeAlias,
    TypeVar,
    cast,
)
from urllib.parse import ParseResult
from warnings import warn

from pydantic import BaseModel, ConfigDict
from typing_extensions import Unpack, final

from datamodel_code_generator import (
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    Error,
    FieldTypeCollisionStrategy,
    ModuleSplitMode,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    cached_path_exists,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator._constraint_applicability import (
    overridden_declared_constraints,  # noqa: F401  # Preserve the established parser.base export.
    resplit_overridden_constraints,  # noqa: F401  # Preserve the established parser.base export.
    untaken_minimums,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator._container_kinds import CONTAINERS
from datamodel_code_generator._context_scope import context_values
from datamodel_code_generator._internal_utils import HashableComparable
from datamodel_code_generator._internal_utils import get_most_of_parent as _get_most_of_parent
from datamodel_code_generator._json_pointer import fragment_tokens
from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator._module_paths import (
    dotted_module,
    expanded_path,
    is_below,
    is_package_init,
    normalized_path,
    written_names,
)
from datamodel_code_generator._mro import c3_merge  # noqa: F401  # Preserve the established parser.base export.
from datamodel_code_generator._name_words import pascal_words
from datamodel_code_generator._parse_memo import PARSE_MEMO, MemoTable
from datamodel_code_generator._parser_context import ParserSourceContext
from datamodel_code_generator._python_target_names import target_builtin_names
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import (
    EdgeKind,
    GraphSnapshot,
)
from datamodel_code_generator._requirements import (
    REQUIREMENTS,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator._shared_types import (
    DefaultPutDict,
    LiteralType,
)
from datamodel_code_generator._source import (
    YamlValue,
    _is_parsed_source_cache_enabled,
    _read_parser_source_data_from_path,
)
from datamodel_code_generator._target_unicode import title
from datamodel_code_generator._template_data import copy_extra_template_data
from datamodel_code_generator._url_redaction import named_location, redact_reference
from datamodel_code_generator._user_keys import (
    ConstructionKeys,
    class_field_key,
    field_keys,
    first_given,
    first_present,
    mapped_base_class,
    model_level_type_override,
)
from datamodel_code_generator._warning_control import quiet
from datamodel_code_generator.enums import (
    ConstructorFieldOrder,
    DefaultValueType,
    StrictTypes,
)
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATIONS,
    Import,
    Imports,
    _iter_import_bindings,
)
from datamodel_code_generator.model._render_plan import RENDER_READY, NestedFactoryMetadata, RenderReady
from datamodel_code_generator.model.base import (
    ALL_MODEL,  # noqa: F401  # Preserve the established parser.base export.
    BaseClassDataType,  # noqa: F401  # Preserve the established parser.base export.
    ConstraintsBase,  # noqa: F401  # Preserve the established parser.base export.
    DataModel,
    DataModelFieldBase,
    _bind_builtin_names,
    _set_nested_model_default_factory_order,
    forget_model_memos,
    get_resolve_reference_action_capabilities,
)
from datamodel_code_generator.model.enum import (
    Enum,
)
from datamodel_code_generator.model.enum import escape_characters as _enum_escape_characters
from datamodel_code_generator.model.output import (
    prepare_output_model_config,
)
from datamodel_code_generator.parser import _inheritance_check, _init_exports, _stdout_repair, _type_overrides
from datamodel_code_generator.parser._field_copies import (
    _copy_data_model_field,
    _copy_resolved_inherited_field,
    _get_inherited_type_modifiers,  # noqa: F401  # Preserve the established parser.base export.
    _merge_data_type_modifiers,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator.parser._field_rows import (
    FIELD_OPTIONS_LEFT_OUT,
    FIELD_ROWS_BUILT_WITHOUT_VALIDATION,
    FIELD_ROWS_WITHOUT_VALIDATION_ALIASES,
)
from datamodel_code_generator.parser._graph_ids import (
    keyed_models,
)
from datamodel_code_generator.parser._held_null import OMIT_HELD_NULL
from datamodel_code_generator.parser._imports_plan import (
    ImportProducer,
    ModuleImportLedger,
)
from datamodel_code_generator.parser._model_shapes import (
    KeySlot,
    ModelShapes,
    ReuseGroups,
    ShapeRow,
    stdout_fingerprint,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator.parser._passes import _shared, _state, settle, structure
from datamodel_code_generator.parser._passes import modules as _modules
from datamodel_code_generator.parser._passes import plans as _plans
from datamodel_code_generator.parser._passes import render_prep as _render_prep
from datamodel_code_generator.parser._passes._import_aliases import alias_shadowed_imports
from datamodel_code_generator.parser._passes._keys import (
    _DEFERRED_INHERITED_CLASS_KEY,  # noqa: F401  # Preserve the established parser.base export.
    _DEFERRED_INHERITED_FIELD_KEY,  # noqa: F401  # Preserve the established parser.base export.
    _DEFERRED_INHERITED_TYPE_KEY,  # noqa: F401  # Preserve the established parser.base export.
    _MODEL_MODULE_PREFIX,
    _RAW_SCHEMA_DEFAULT_KEY,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator.parser._passes._name_allocation import (
    scheme_resolver,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator.parser._passes._shared import (
    _TYPE_DEPENDENT_CONSTRAINTS_KEY,  # noqa: F401  # Preserve the established parser.base export.
    DiscriminatorValue,
    _assign_module_name,
    _data_types_beside_fields,
    _forward_enum_member_literals,
    _has_root_field_constraints,
    _hidden_type_names,
    _join_module,
    _ModelPositions,
    _parsed_rule_data_types,
    _ReuseOptimizationContext,
    _string_or_identifier_pattern,
    add_model_path_to_list,  # noqa: F401  # Preserve the established parser.base export.
    apply_inherited_field_default,
    enum_discriminator_literal,
    enum_discriminator_members,
    enum_site_override_import,  # noqa: F401  # Preserve the established parser.base export.
    field_name_model_type_of,
    normalize_default_value_constraints,
    placeholder_identity,  # noqa: F401  # Preserve the established parser.base export.
    register_runtime_expression,
    relative,
)
from datamodel_code_generator.parser._passes._state import (
    ForwarderMap,
    ModuleModels,
    ModulePath,
    RecursionFacts,
    SortedDataModels,
    _ConstNarrowing,
    _FinalizeState,
    _KeptParentRoot,
    _ModuleState,
    _ParseArgs,
    _PipelineRun,
    _PreparedModule,
    _RenamedType,
    _StructureState,
)
from datamodel_code_generator.parser._passes._tables import FINALIZE_SEGMENTS, MODULE_BODY, PIPELINE, STRUCTURE_BODY
from datamodel_code_generator.parser._passes.imports import _FUTURE_IMPORTS, builtin_name_bindings
from datamodel_code_generator.parser._pipeline import PARSE_CALLER, Runner, parse_caller
from datamodel_code_generator.parser._schema_graph import SCHEMA_GRAPH
from datamodel_code_generator.parser._stdout_repair import _TOP_LEVEL_FUTURE_IMPORT_PATTERN
from datamodel_code_generator.parser._tag_plan import TagCapabilities, TagPlan, tag_capabilities
from datamodel_code_generator.parser.generation import GenerationStore
from datamodel_code_generator.parser.schema_version import SchemaFeaturesT
from datamodel_code_generator.reference import (
    _ALIAS_RESOLUTION_CLASS_NAME_KEY,
    ModelResolver,
    ModelType,
    Reference,
    location_of,
    split_module_name,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator.types import (
    NONE,  # noqa: F401  # Preserve the established parser.base export.
    DataType,
    DataTypeManager,
    _copy_data_type,
    build_union,  # noqa: F401  # Preserve the established parser.base export.
    union_members,  # noqa: F401  # Preserve the established parser.base export.
)
from datamodel_code_generator.util import record_watch_dependency

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Generator, Hashable, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._format_types import Formatter, PythonVersion
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.format import CodeFormatter
    from datamodel_code_generator.http import _HTTPFetchSession
    from datamodel_code_generator.model_metadata import GeneratedModelMetadata, ModelFieldMetadata, ModelMetadata
    from datamodel_code_generator.parser._facts import Phase
    from datamodel_code_generator.parser._field_rows import FieldAliasRow, FieldFacts, FieldIdentity, FieldRow
    from datamodel_code_generator.parser._phases import DecisionPass
    from datamodel_code_generator.parser._schema_graph import SchemaGraphBuilder
    from datamodel_code_generator.parser.generation import GraphBuilder


# Preserve the existing parser.base export while sharing one canonical escape table.
escape_characters = _enum_escape_characters
MAX_RECURSION_COUNT = settle.MAX_RECURSION_COUNT
sort_base_classes_for_mro = settle.sort_base_classes_for_mro
sort_data_models = settle.sort_data_models
get_module_directory = structure.get_module_directory
get_most_of_parent = _get_most_of_parent
modules = _modules
plans = _plans
render_prep = _render_prep
override_required_field = _shared.override_required_field
SPECIAL_PATH_FORMAT: str = _shared.SPECIAL_PATH_FORMAT
get_special_path = _shared.get_special_path
PydanticFloors = _state.PydanticFloors
pydantic_floors_recorded = _state.pydantic_floors_recorded

ParserConfigT = TypeVar("ParserConfigT", bound="ParserConfig")


_TOP_LEVEL_RELATIVE_IMPORT_PATTERN: Final = re.compile(r"(?m)^from \.")


_ADDITIONAL_IMPORTS: Final = ImportProducer("additional-imports")
_DEFERRED_ANNOTATIONS: Final = ImportProducer("deferred-annotations")


def _get_model_field_constructor(
    field_type: type[DataModelFieldBase],
) -> Callable[..., DataModelFieldBase]:
    """Resolve an exact output field's parser construction capability."""
    if constructor := field_type.__dict__.get("PARSER_CONSTRUCTOR"):
        return cast("Callable[..., DataModelFieldBase]", constructor)
    return field_type


def _get_field_dependency_ordering_model_type(model_type: type[DataModel]) -> type[DataModel] | None:
    """Return the configured model type when its fields require dependency ordering."""
    return model_type if model_type.REQUIRES_FIELD_DEPENDENCY_ORDERING else None


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
    with context_values({_DROPPED_CONSTRAINTS: record}):
        yield record


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


def _source_path_from_reference_path(reference_path: str) -> list[str] | None:
    _, separator, fragment = reference_path.partition("#")
    return list(tokens) if separator and (tokens := fragment_tokens(fragment)) is not None else None


@record
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


def dump_templates(templates: list[DataModel]) -> str:
    """Join model templates into a single code string."""
    return "\n\n\n".join(str(m) for m in templates)


ReferenceMapSet = dict[str, set[str]]
_NO_RECURSION: Final = RecursionFacts(require_update=frozenset(), circular_roots=frozenset())


def title_to_class_name(title: str) -> str:
    """Convert a schema title to a valid Python class name."""
    return pascal_words(title, title=str.title)


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


@final
@record
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
        rendered, imports, *written = model._fresh_dedup_key(class_name)  # noqa: SLF001
        return rendered, imports, tuple(written)

    def dedup_key(self, model: DataModel, slot: KeySlot) -> tuple[Any, ...]:  # noqa: PLR6301
        """Return the dedup key the memo keeps for the model in a slot, rendering and keeping it for an empty slot."""
        class_name, use_default = slot
        return model.get_dedup_key(class_name, use_default=use_default)

    def forget_dedup_keys(self, model: DataModel) -> None:  # noqa: PLR6301
        """Drop the dedup keys the parse's memo keeps for the model."""
        forget_model_memos(model, MemoTable.DEDUP_KEYS)

    def mask_pattern(self, text: str) -> re.Pattern[str]:
        """Return the pattern that finds string literals, field declarations and names as the target names them."""
        return _string_or_identifier_pattern(text, self.target_python_version)

    def references(self, model: DataModel) -> list[Reference]:  # noqa: PLR6301
        """Return the references of the model's data types in render order."""
        return [reference for data_type in model.all_data_types if (reference := data_type.reference)]

    def rule_references(self, model: DataModel) -> list[Reference]:  # noqa: PLR6301
        """Return the references of the types that the model's schema validator rules name, in order."""
        return [reference for data_type in _parsed_rule_data_types(model) if (reference := data_type.reference)]

    def model_of(self, reference: Reference) -> DataModel | None:  # noqa: PLR6301
        """Return the model a reference names, or None when it names no model."""
        return source if isinstance(source := reference.source, DataModel) else None

    def identity(self, reference: Reference) -> ModelKey:  # noqa: PLR6301
        """Return what tells apart the models that references name: the model key."""
        return model_key_of(reference)


def _resolve_module_file(module_: ModulePath, results: dict[ModulePath, Result]) -> tuple[ModulePath, bool]:
    """Return the file of a module and whether it is a package ``__init__.py``, adding its missing parent packages.

    A top-level module adds the root ``__init__.py``; a nested one, the ``__init__.py`` of each package above it.
    """
    if not module_:
        return ("__init__.py",), False
    for i in range(len(module_) > 1, len(module_)):
        if (parent := (*module_[:i], "__init__.py")) not in results:
            results[parent] = Result(body="")
    if (*module_, "__init__.py") in results:
        return (*module_, "__init__.py"), True
    return written_names((*module_[:-1], f"{module_[-1]}.py")), False


def _format_body_safe(body: str, code_formatter: CodeFormatter, *, generated_code: bool = False) -> str:
    try:
        return code_formatter._format_generated_code(body) if generated_code else code_formatter.format_code(body)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        warn(
            f"Failed to format code: {exc!r}. Emitting unformatted output.",
            stacklevel=1,
        )
        return body


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
    _DECISION_PASSES: ClassVar[tuple[DecisionPass[Any], ...]] = (OMIT_HELD_NULL,)
    """The decisions of the decision phase, in registration order; ``parser/_phases.py`` says how they run."""
    _http_fetch_session: _HTTPFetchSession | None = None
    _dropped_constraint_fields: DroppedConstraintFields = frozenset()
    _schema_graph: SchemaGraphBuilder | None = None

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The capability through which the passes change the graph of the generation store."""
        return self.generation_store.builder()

    @property
    def _tag_capabilities(self) -> TagCapabilities:
        """The rows of the output's model and field classes that the tag passes read."""
        return tag_capabilities(self.data_model_type, self.data_model_field_type)

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

    @property
    def _field_option_kwargs(self) -> dict[str, Any]:
        """The configured field options; a field ``_build_field`` builds takes each unless its row leaves it out."""
        return {
            "strip_default_none": self.strip_default_none,
            "order_constructor_fields_by_written_defaults": self.order_constructor_fields_by_written_defaults,
            "use_field_description": self.use_field_description,
            "use_field_description_example": self.use_field_description_example,
            "use_inline_field_description": self.use_inline_field_description,
            "use_annotated": self.use_annotated,
            "use_serialize_as_any": self.use_serialize_as_any,
            "use_default_kwarg": self.use_default_kwarg,
            "use_frozen_field": self.use_frozen_field,
            "use_serialization_alias": self.use_serialization_alias,
            "use_default_factory_for_optional_nested_models": self.use_default_factory_for_optional_nested_models,
        }

    def _build_field(
        self,
        row: FieldRow,
        /,
        data_type: DataType,
        *,
        source_name: str | None = None,
        class_name: str | None = None,
        **facts: Unpack[FieldFacts],
    ) -> DataModelFieldBase:
        """Build a field of a model the parser writes, with the configured options its ``row`` takes.

        The field gets exactly the ``facts`` given, which also override the options they name. A given ``alias`` splits
        into the alias and the validation aliases, beside the ``--serialization-aliases`` entry of ``class_name`` for a
        field with a ``name`` and the ``source_name`` it is read from.
        """
        options = self._field_option_kwargs
        if left_out := FIELD_OPTIONS_LEFT_OUT[row]:
            options = {key: value for key, value in options.items() if key not in left_out}
        aliases: dict[str, Any] = {}
        if "alias" in facts:
            aliases["alias"], validation_aliases, aliases["serialization_alias"] = self._field_aliases(
                facts.pop("alias"), name=facts.get("name"), source_name=source_name, class_name=class_name
            )
            if row not in FIELD_ROWS_WITHOUT_VALIDATION_ALIASES:
                aliases["validation_aliases"] = validation_aliases
        constructor = (
            _get_model_field_constructor(self.data_model_field_type)
            if row in FIELD_ROWS_BUILT_WITHOUT_VALIDATION
            else self.data_model_field_type
        )
        return constructor(
            data_type=data_type,
            **{**options, **aliases, **facts},
            **self._data_model_field_common_kwargs(),
        )

    def _field_aliases(
        self,
        alias: str | list[str] | None,
        *,
        name: str | None,
        source_name: str | None,
        class_name: str | None,
    ) -> tuple[str | None, list[str] | None, str | None]:
        """Return the alias, validation aliases and serialization alias of a field ``name`` read from ``source_name``.

        ``alias`` (the output alias or the list of validation aliases) splits into the first two; the third is the
        ``--serialization-aliases`` entry of ``class_name``, looked up only for a field with both names.
        """
        single_alias, validation_aliases = self._split_field_alias(alias)
        return (
            single_alias,
            validation_aliases,
            self.get_serialization_alias(source_name, name, class_name)
            if source_name is not None and name is not None
            else None,
        )

    def _take_field_aliases(  # noqa: PLR0913
        self,
        field: DataModelFieldBase,
        name: str,
        alias: str | list[str] | None,
        *,
        source_name: str,
        class_name: str,
        row: FieldAliasRow,
    ) -> None:
        """Give a field built earlier the name ``name`` and the aliases ``_field_aliases`` derives, by ``row``."""
        single_alias, validation_aliases, serialization_alias = self._field_aliases(
            alias, name=name, source_name=source_name, class_name=class_name
        )
        identity: FieldIdentity = {"name": name, "alias": single_alias}
        if row == "required-copy":
            identity["original_name"] = source_name
            identity["use_serialization_alias"] = self.use_serialization_alias
        if row != "rename":
            identity["validation_aliases"] = validation_aliases
        if row != "final-class" or (
            self.serialization_aliases
            and (
                field.serialization_alias is None
                or not self.serialization_aliases.keys().isdisjoint((
                    class_field_key(class_name, source_name),
                    class_field_key(class_name, name),
                ))
            )
            and serialization_alias is not None
        ):
            identity["serialization_alias"] = serialization_alias
        self._graph_builder.set_field_identity(field, identity)

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
        self._source_context = getattr(config, "_source_context", None) or ParserSourceContext.of(
            config, getattr(config, "remote_lock", None)
        )
        self._has_bound_python_types = False
        self._has_runtime_expressions = False
        self._constrained_decimal_alias_model_ids: set[int] = set()
        self._hidden_named_types: dict[tuple[ModelKey, str | None], bool] = {}
        self._hiding_field_names: dict[tuple[ModelKey, str | None], str] = {}
        self._tag_plan = TagPlan()
        """What the tag passes of the parse recorded: the union tags reuse compares, then the run's tag ledger."""
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
        self._kept_parent_roots: dict[int, _KeptParentRoot] = {}
        """Each root model that the ``parent`` strategy named its inner model after and kept for its later holders,
        by id, until ``settle_kept_parent_roots`` decides it once every module is processed."""
        self._emitting_module_names: dict[int, str] = {}
        self._emitting_module_lists: list[tuple[str, list[DataModel]]] = []
        self._collapsed_reuse_models: dict[int, list[DataModel]] = {}
        self._collapsed_reuse_users: dict[int, tuple[DataType, Reference]] = {}
        self._self_named_root_models: list[DataModel] = []
        """The root models whose root names the model itself, which the deduplication of root models dropped while
        their users still name them, for ``reject_alias_cycles_without_schema``."""
        self._recursion_facts: RecursionFacts = _NO_RECURSION
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
        self.data_type_manager.use_decimal_in_field_forms = config.use_decimal_in_field_forms
        self.data_model_type: type[DataModel] = config.data_model_type
        self.data_model_root_type: type[DataModel] = config.data_model_root_type
        self.pydantic_v2_root_model_type: type[DataModel] | None = _get_field_dependency_ordering_model_type(
            self.data_model_root_type
        )
        self.data_model_field_type: type[DataModelFieldBase] = config.data_model_field_type
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
        self.checks_literal_values = config.filter_literal_values or config.omit_redundant_constraints
        self.intersects_allof_keywords = config.intersect_allof_keywords
        self._data_model_field_common_kwargs_cache: dict[str, Any] = {
            "use_missing_sentinel": self.use_missing_sentinel,
            "target_python_version": self.target_python_version,
        }
        self.reuse_model: bool = config.reuse_model
        self._reuse_groups = ReuseGroups()
        self._const_narrowed_references: list[tuple[Reference, _ConstNarrowing]] = []
        self._const_narrowed_values: dict[int, tuple[DataType, _ConstNarrowing]] = {}
        self._enum_admits_other_values: list[tuple[DataModelFieldBase, _ConstNarrowing]] = []
        self.typed_nullable_maps = False
        self.yielding_names_to_assign: tuple[tuple[str, tuple[str, str | None]], ...] = ()
        self.typed_nullable_map_reference_ids: set[ModelKey] = set()
        self._literal_under_all_enum_reference_ids: set[int] = set()
        self._site_model_keys: set[ModelKey] = set()
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
        self.omit_redundant_reference_null: bool = config.omit_redundant_reference_null
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
        self.use_tuple_for_closed_arrays: bool = config.use_tuple_for_closed_arrays
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
        self._construction_keys = ConstructionKeys.of(
            (config.base_class_map, config.extra_template_data, config.validators),
            (config.aliases, config.serialization_aliases, config.default_value_overrides),
        )
        self.generate_schema_validators: bool = config.generate_schema_validators
        self.check_pattern_maps_strictly: bool = config.check_pattern_maps_strictly
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
            pin_aliases_with_alias_generator=config.pin_aliases_with_alias_generator,
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
        self.collapse_shared_parent_wrappers = config.collapse_shared_parent_wrappers
        self.collapse_reuse_models = config.collapse_reuse_models
        self.skip_root_model = config.skip_root_model
        self.use_root_model_sequence_interface = config.use_root_model_sequence_interface
        self.use_type_alias = config.use_type_alias
        self.capitalise_enum_members = config.capitalise_enum_members
        self.keep_model_order = config.keep_model_order
        self.order_constructor_fields_by_final_names = (
            ConstructorFieldOrder.FinalNames in config.order_constructor_fields_by
        )
        self.order_constructor_fields_by_written_defaults = (
            ConstructorFieldOrder.WrittenDefaults in config.order_constructor_fields_by
        )
        self.use_one_literal_as_default = config.use_one_literal_as_default
        self.use_enum_values_in_discriminator = config.use_enum_values_in_discriminator
        self.use_msgspec_tagged_unions = config.use_msgspec_tagged_unions
        self.tag_msgspec_by_payload_name = config.tag_msgspec_by_payload_name
        self.use_msgspec_nullable_enum_alias = config.use_msgspec_nullable_enum_alias
        self.use_typed_nullable_map_values = config.use_typed_nullable_map_values
        self.use_typed_map_values_beside_other_types = config.use_typed_map_values_beside_other_types
        self.use_single_dict_for_object_maps = config.use_single_dict_for_object_maps
        self.read_unevaluated_properties = config.read_unevaluated_properties
        self.use_single_value_enum_as_const = config.use_single_value_enum_as_const
        self.read_const_beside_enum = config.read_const_beside_enum
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
            key: import_ for key, import_ in self._type_override_imports.items() if model_level_type_override(key)
        }
        self._base_stand_in_imports: dict[ModelKey, Import] = {}
        """The import of the class each stand-in model stands for, by model key: a schema that a base names and the
        output types by an import (``--external-ref-mapping``, ``x-python-import``) is parsed into a model that is
        never written, which the passes read as the generated base until that import replaces the base."""
        self._declared_class_stand_ins: set[ModelKey] = set()
        """The stand-ins of schemas whose ``x-python-import`` declares the class: that class is the base whatever the
        schema describes, so the stand-in is never dropped as a generated root model is."""
        self._reuse_optimization_context = _ReuseOptimizationContext.from_type_overrides(self.type_overrides)
        self.read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = config.read_only_write_only_model_type
        self.dedupe_request_response_variants: bool = config.dedupe_request_response_variants
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
        return _type_overrides.get_target_python_type_path(self, path)

    def _get_emitted_python_type_path(self, path: str) -> str:
        """Return a user-named type path for the target, rejecting a type the target lacks only once output imports it.

        A type that an override replaces or that no model uses emits no import, so it is left alone.
        """
        return _type_overrides.get_emitted_python_type_path(self, path)

    def _reject_unavailable_imports(self, contexts: Sequence[ModuleContext]) -> None:
        """Reject output that imports a type the target lacks, after overrides, unused models and import remapping.

        The first such type in schema order is reported, so the message does not depend on set iteration order.
        """
        _type_overrides.reject_unavailable_imports(self, contexts)

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
        return first_present(self.serialization_aliases, field_keys(class_name, original_field_name, field_name))[0]

    def _apply_final_class_field_aliases(
        self, fields: list[DataModelFieldBase], class_name: str, original_class_name: str
    ) -> None:
        """Apply final class scopes after name allocation, retaining already resolved fallback aliases."""
        if class_name == original_class_name or not (aliases := self.config.aliases):
            return
        prefix = class_field_key(class_name, "")
        if not any(key.startswith(prefix) for key in aliases):
            return
        scoped_aliases = {
            field.original_name
            for field in fields
            if field.original_name is not None
            and first_given(aliases, (class_field_key(class_name, field.original_name),)) is not None
        }
        if not scoped_aliases:
            return
        reserved_names = {field.name for field in fields if field.original_name not in scoped_aliases and field.name}
        resolved_aliases: dict[str, tuple[str, str | list[str] | None]] = {}
        for field in fields:
            if (original_name := field.original_name) is None or original_name not in scoped_aliases:
                continue
            if original_name not in resolved_aliases:
                resolved_aliases[original_name] = self.model_resolver.get_valid_field_name_and_alias(
                    original_name,
                    excludes=reserved_names,
                    model_type=self.field_name_model_type,
                    class_name=class_name,
                )
                reserved_names.add(resolved_aliases[original_name][0])
            self._take_field_aliases(
                field,
                *resolved_aliases[original_name],
                source_name=original_name,
                class_name=class_name,
                row="final-class",
            )
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
                result = list(dict.fromkeys(v for v in value if isinstance(v, str) and v))
                if not result:
                    return None
                return result[0] if len(result) == 1 else result
            return value or None

        mapped, found = mapped_base_class(self.base_class_map, class_name)
        if found:
            return normalize(mapped)
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
        if not (root_reference := root_data_type.reference) or root_data_type in CONTAINERS:
            return None
        if root_reference.source not in models_set or (
            self.config.use_site_models_for_constrained_refs and _has_root_field_constraints(model.fields[0])
        ):
            return None
        expected_name = self.model_resolver.get_class_name(model.reference.original_name, unique=False).name
        if root_reference.name != expected_name:
            return None
        return root_reference

    def _model_shapes(self, row: ShapeRow) -> ModelShapes[DataModel, Reference]:
        """Return the keys of one comparison pass of the dedup or reuse user of a row."""
        return ModelShapes(row, _ModelShapeSource(self.target_python_version), self._tag_plan.reuse_snapshot)

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
                data_type = self.data_type(
                    enum_member_literals=[
                        (enum_class_name, member.field.name)
                        for member in enum_discriminator_members(enum_source, discriminator_values).members
                        if member.field.name
                    ]
                )
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
        return enum_discriminator_literal(enum_source, value)

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
                    self._graph_builder.swap_data_type(nested_data_type, set_data_type, refresh="deferred")
            return data_type
        return None  # pragma: no cover

    def record_dropped_constraints(self) -> None:
        """Record this parse's dropped-constraint warnings as those of the generation that returns its models."""
        if (record := _DROPPED_CONSTRAINTS.get()) is not None:
            record.warnings = self._dropped_constraint_fields

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

    def _resolve_type_import(self, type_name: str) -> Import | None:
        """Resolve a type through target-stable metadata, never host imports."""
        return _type_overrides.resolve_type_import(self, type_name)

    def _is_target_python_builtin_type(self, type_name: str) -> bool:
        """Recognize builtins against the target version, never the host runtime."""
        return _type_overrides.is_target_python_builtin_type(self, type_name)

    def _resolve_qualified_type_import(self, qualified_name: str) -> Import:
        """Resolve known stdlib paths against the target without host imports."""
        return _type_overrides.resolve_qualified_type_import(self, qualified_name)

    def _resolve_type_import_from_defs(  # noqa: PLR6301
        self,
        type_name: str,  # noqa: ARG002
    ) -> Import | PythonTypeExpr | None:
        """Resolve a type name by the ``$defs`` of the document, which only a JSON Schema document has."""
        return None

    def _bind_python_type(self, expression: PythonTypeExpr) -> BoundPythonType:
        """Bind names/imports without returning to runtime-dependent text parsing.

        ``expression`` was parsed only at the external JSON Schema boundary.
        Keep the IR through internal stages: rendering and reparsing here would
        incorrectly make the host AST/tokenizer an authority for target syntax.
        """
        return _type_overrides.bind_python_type(self, expression)

    def _override_annotation(self, override_import: Import, *, is_optional: bool = False) -> DataType | None:
        """Return the type a ``--type-overrides`` value spells as an annotation, or None when it names a class.

        A value with brackets, such as ``typing.Literal[1, 2]``, names no class to import: it is written as
        ``x-python-type`` writes it, with the imports of its names and the keywords that type takes. A value with
        brackets that is no annotation is an error, as it has no import to write either.
        """
        return _type_overrides.override_annotation(self, override_import, is_optional=is_optional)

    def _apply_override_to_field(self, field: DataModelFieldBase, override_import: Import) -> None:
        """Apply override to entire field's data_type, keeping type-dependent constraints that the new type takes.

        A value with brackets is an annotation: it drops the arguments of every ``con*`` call the field held and
        takes the keywords that annotation takes; the field keeps the None its schema admits.

        The arguments of a ``con*`` call stay on an override the generator does not know, which may be a factory such
        as ``pydantic.conint``; a known class takes none, so they are left out, and so are those of the nested types the
        override replaces. An output model that writes ``Field()`` arguments warns about the ones it leaves out.
        """
        _type_overrides.apply_override_to_field(self, field, override_import)

    def _record_overridden_constraints(self, field: DataModelFieldBase, *, keeps_top_kwargs: bool) -> None:
        """Remember how many ``con*`` arguments the override of a field drops, to warn about them.

        The ``con*`` call of the field's own type stays where ``keeps_top_kwargs``; those of its nested types leave.
        """
        _type_overrides.record_overridden_constraints(self, field, keeps_top_kwargs=keeps_top_kwargs)

    def _decide_overridden_constraints(
        self, field: DataModelFieldBase, keywords: Callable[..., frozenset[str]]
    ) -> None:
        """Keep the constraints that the emitted type decides only when the override type takes them.

        ``keywords`` returns what that type takes, as ``DataTypeManager.type_constraint_keywords`` does for its path.

        An output model that writes constraints as ``Field()`` arguments also decides the number constraints of a
        declared number and the string constraints the replaced type left out this way, and remembers the ones it
        leaves out to warn about them. The untaken constraints of the members, items and values that collapsing inlines
        into the field leave with the type the override replaces.
        """
        _type_overrides.decide_overridden_constraints(self, field, keywords)

    def _mark_untaken_constraints(
        self,
        field: DataModelFieldBase,
        untaken: dict[str, Any],
        keywords_for: Callable[[str], frozenset[str]],
    ) -> None:
        """Remember the declared constraints a field leaves out, and the target version that would take each.

        ``keywords_for`` returns the number keywords the field's type takes for an explicit target model version.
        """
        _type_overrides.mark_untaken_constraints(self, field, untaken, keywords_for)

    def _apply_override_to_data_type(self, data_type: DataType) -> bool:
        """Recursively apply model-level overrides to a DataType; a stand-in takes the import it stands for first."""
        return _type_overrides.apply_override_to_data_type(self, data_type)

    @property
    def _result_modules_postprocessor(self) -> Callable[..., dict[tuple[str, ...], Result]]:
        """Resolve the legacy postprocessor dynamically for each render."""
        return self.__postprocess_result_modules

    @classmethod
    def __postprocess_result_modules(
        cls, results: dict[tuple[str, ...], Result], *, empty_init: bool = False
    ) -> dict[tuple[str, ...], Result]:

        results = {expanded_path(k): v for k, v in results.items()}

        init_result = Result(body="") if empty_init else next(v for k, v in results.items() if is_package_init(k[-1]))
        folders = {t[:-1] if t[-1].endswith(".py") else t for t in results}
        for folder in folders:
            for i in range(len(folder)):
                subfolder = folder[: i + 1]
                init_file = (*subfolder, "__init__.py")
                results.setdefault(init_file, init_result)
        return results

    def _register_runtime_expression(self) -> None:
        """Mark a parser-owned expression created after the initial import scan."""
        register_runtime_expression(self)

    @classmethod
    def _collect_exports_for_init(
        cls,
        module: tuple[str, ...],
        processed_models: Sequence[ModuleContext],
        scope: AllExportsScope,
    ) -> list[tuple[str, tuple[str, ...], str]]:
        """Collect exports for __init__.py based on scope."""
        return _init_exports.collect_exports_for_init(cls, module, processed_models, scope)

    @classmethod
    def _resolve_export_collisions(
        cls,
        exports: list[tuple[str, tuple[str, ...], str]],
        strategy: AllExportsCollisionStrategy | None,
        reserved: set[str] | None = None,
        target_python_version: PythonVersion | None = None,
    ) -> dict[str, list[tuple[str, tuple[str, ...], str]]]:
        """Resolve name collisions in exports based on strategy, titling module names as the target does."""
        return _init_exports.resolve_export_collisions(cls, exports, strategy, reserved, target_python_version)

    @classmethod
    def _raise_collision_error(
        cls,
        by_name: dict[str, list[tuple[str, tuple[str, ...], str]]],
        colliding: set[str],
    ) -> None:
        """Raise an error with collision details."""
        _init_exports.raise_collision_error(cls, by_name, colliding)

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
        return _init_exports.build_all_exports_code(cls, resolved)

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
            for data_type in _data_types_beside_fields(model):
                collect_data_type_names(data_type)
        return names

    def __generate_forwarder_content(  # noqa: PLR6301
        self,
        original_module: tuple[str, ...],
        names: Sequence[tuple[tuple[str, ...], str, str]],
        *,
        is_init: bool = False,
    ) -> str:
        """Generate forwarder module content that imports the names it binds from the modules that write them.

        Args:
            original_module: The original module tuple (e.g., ("issuing",) or ())
            names: ``(module, name, bound name)`` of each model it binds (``Forwarder.names``)
            is_init: True if this is a package __init__.py, False for regular .py files

        Returns:
            The forwarder module content as a string
        """
        original_str = ".".join(original_module)
        package = (*original_module, "__init__") if is_init else original_module
        imports = Imports()
        for source, name, bound_name in sorted(names, key=operator.itemgetter(2)):
            if is_below(package, source):
                from_ = "." * (len(package) - len(source))
            else:
                left, right = relative(
                    original_str, ".".join(source), reference_is_module=True, current_is_init=is_init
                )
                from_ = _join_module(left, right)
            imports.append(Import(from_=from_, import_=name, alias=None if bound_name == name else bound_name))

        return f"{imports.dump()}\n\n{imports.dump_all()}\n"

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
            self.target_python_version.has_native_deferred_annotations or (with_import and not disable_future_imports)
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
            self.target_python_version.has_native_deferred_annotations
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
            and not self.target_python_version.has_native_deferred_annotations
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
        return _stdout_repair.find_invalid_inferred_modules(self, sorted_data_models)

    @staticmethod
    def _stdout_model_fingerprint(model: DataModel) -> HashableComparable:
        """Return the final rendered definition without using the pre-processing dedup cache."""
        return _stdout_repair.stdout_model_fingerprint(model)

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
            normalized_path(module, treat_dot_as_module=binding_context.treat_dot_as_module): module
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
        return _stdout_repair.get_source_data_fingerprint(self)

    def _inspect_invalid_dotted_stdout(
        self,
        contexts: list[ModuleContext],
        sorted_data_models: SortedDataModels,
        config: ParseConfig,
        results: dict[ModulePath, Result],
    ) -> None:
        """Record a narrow repair plan after every module transformation has completed."""
        _stdout_repair.inspect_invalid_dotted_stdout(self, contexts, sorted_data_models, config, results)

    def _model_inventory(self, models: list[DataModel]) -> tuple[str, ...]:
        """Return the paths of the models other than typed map values, which a retry with untyped values omits."""
        return _stdout_repair.model_inventory(self, models)

    def _flatten_invalid_modules(
        self,
        live_models: list[DataModel],
        affected_models: list[DataModel],
    ) -> bool:
        """Flatten selected inferred modules while preserving all unaffected names."""
        return _stdout_repair.flatten_invalid_modules(self, live_models, affected_models)

    def _apply_forced_invalid_dotted_stdout_repair(
        self,
        module_models: ModuleModels,
        module_split_mode: ModuleSplitMode | None,
    ) -> bool:
        """Apply the repair roots proven necessary by a completed legacy pass."""
        return _stdout_repair.apply_forced_invalid_dotted_stdout_repair(self, module_models, module_split_mode)

    def _build_module_structure(
        self,
        sorted_data_models: SortedDataModels,
        module_split_mode: ModuleSplitMode | None,
        all_exports_scope: AllExportsScope | None,
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
        state = _StructureState(sorted_data_models, module_split_mode, all_exports_scope)
        self.__runner().run_steps(STRUCTURE_BODY, self, state)
        return (
            state.module_models,
            state.internal_modules,
            state.forwarder_map,
            state.model_to_module_models,
            state.model_path_to_module_name,
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
        module_name = dotted_module(module_)
        # Tree-scope reuse creates synthetic models without a file_path. Keep
        # their ownership in this module for relative import planning without
        # changing the file path used by generated filename headers.
        for model in models:
            _assign_module_name(model_path_to_module_name, model, module_name, keep=True)
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
            config=config,
            internal_modules=internal_modules,
            model_path_to_module_name=model_path_to_module_name,
            require_update_action_models=require_update_action_models,
            unused_models=unused_models,
        )
        self.__runner().run_steps(MODULE_BODY, self, state)
        self._prepared_modules.append(_PreparedModule.of(state))
        return ModuleContext(module, module_, state.model_list, is_init, imports, scoped_model_resolver)

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

    def _finalize_modules(  # noqa: PLR0913, PLR0917
        self,
        contexts: list[ModuleContext],
        unused_models: list[DataModel],
        model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]],
        module_to_import: dict[ModulePath, Imports],
        forwarder_modules: Collection[ModulePath],
        with_import: bool,  # noqa: FBT001
    ) -> None:
        """Finish the render preparation of every module and collect the imports of each module.

        The pipeline runs it after the generic base class, the decision phase and the render preparation of each
        module, which ``_process_single_module`` hands over.
        """
        state = _FinalizeState(
            contexts, unused_models, model_to_module_models, module_to_import, forwarder_modules, with_import
        )
        memo = self.generation_store._memo  # noqa: SLF001
        current = {RENDER_READY: RenderReady(type(self), memo), PARSE_MEMO: memo}
        with context_values({variable: value for variable, value in current.items() if variable.get() is None}):
            self.__runner().run_steps(FINALIZE_SEGMENTS, self, state)

    def _check_model_inheritance(self, contexts: Sequence[ModuleContext]) -> None:
        """Reject invalid builtin inheritance; an existing topological order needs no DFS."""
        _inheritance_check.check_model_inheritance(self, contexts)

    def _nested_model_default_factory_decision(
        self,
        modules: Sequence[Sequence[DataModel]],
        require_update: Collection[ModelKey],
    ) -> NestedFactoryMetadata:
        """Decide the declaration order and the reference cycles that the nested models' default factories read.

        ``modules`` are the model lists the modules write, in order; a model's position is its index in its list.
        """
        recursive_keys_by_model: dict[ModelKey, frozenset[ModelKey]] = {}
        if require_update:
            snapshot = GraphSnapshot.NESTED_MODEL_DEFAULT_FACTORIES
            keyed = keyed_models((model for models in modules for model in models), snapshot.value)
            index = self.generation_store.index
            graph = index.reference_graph(snapshot, keyed)
            for component in graph.circular_components((EdgeKind.REFERENCE,)):
                recursive_keys = frozenset(component)
                recursive_keys_by_model.update(dict.fromkeys(recursive_keys, recursive_keys))
        order = {
            id(model): (module_index, model_index)
            for module_index, models in enumerate(modules)
            for model_index, model in enumerate(models)
        }
        return NestedFactoryMetadata(order, recursive_keys_by_model)

    def _set_nested_model_default_factory_metadata(self, contexts: list[ModuleContext]) -> None:  # noqa: PLR6301
        """Publish the nested-factory decision of render preparation on the models of every module.

        The models that render without frozen plans (GraphQL unions, custom output models) read it from here on (row
        ``legacy.L-NF`` of ``model/_render_plan.py``).
        """
        metadata = cast("RenderReady", RENDER_READY.get()).nested_factories
        for ctx in contexts:
            _set_nested_model_default_factory_order(ctx.models, cast("NestedFactoryMetadata", metadata))

    def _generate_module_output(  # noqa: PLR0913, PLR0917
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

        if (scope := config.all_exports_scope) is not None and is_package_init(ctx.module[-1]):
            export_imports = self._init_export_imports(
                ctx.module, contexts, scope, config, set(_init_exports.exported_names(ctx.models))
            )

        if ctx.models:
            if config.with_import:
                module_imports = self._import_ledger.rendered(ctx.imports)
                import_parts = [s for s in [module_future_imports_str, str(self.imports), str(module_imports)] if s]
                result += [*import_parts, "\n"]

            if export_imports:
                result += [str(export_imports), ""]
                for name in _init_exports.exported_names(ctx.models):
                    export_imports.add_export(name)
                result += [export_imports.dump_all(multiline=True) + "\n"]

            module_code = self.data_model_type.render_module_code(ctx.models)
            if module_code and (bindings := builtin_name_bindings(self._import_ledger, ctx.imports)):
                module_code = _bind_builtin_names(module_code, bindings)
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
            result = [
                self.__generate_forwarder_content(
                    ctx.module_key, forwarder_map[ctx.module_key].names, is_init=ctx.is_init
                )
            ]

        if not result and not ctx.is_init:
            return None

        return Result(
            body=self._formatted_body("\n".join(result), config),
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
        if (scope := config.all_exports_scope) is None:  # pragma: no cover
            return
        processed_init_modules = {ctx.module for ctx in contexts if is_package_init(ctx.module[-1])}
        for init_module, init_result in list(results.items()):
            if not is_package_init(init_module[-1]) or init_module in processed_init_modules or init_result.body:
                continue
            if (export_imports := self._init_export_imports(init_module, contexts, scope, config, set())) is None:
                continue
            import_parts = [s for s in [future_imports_str, str(self.imports)] if s] if config.with_import else []
            parts = import_parts + (["\n"] if import_parts else [])
            parts += [str(export_imports), "", export_imports.dump_all(multiline=True)]
            results[init_module] = Result(
                body=self._formatted_body("\n".join(parts), config),
                future_imports=future_imports_str,
            )

    def _init_export_imports(
        self,
        module: ModulePath,
        contexts: list[ModuleContext],
        scope: AllExportsScope,
        config: ParseConfig,
        reserved: set[str],
    ) -> Imports | None:
        """Return the imports by which the ``__init__.py`` of ``module`` exports its subpackages' models, or None.

        Names that collide with each other or with ``reserved`` are resolved by ``--all-exports-collision-strategy``.
        """
        return _init_exports.init_export_imports(self, module, contexts, scope, config, reserved)

    def _formatted_body(self, body: str, config: ParseConfig) -> str:
        """Return a module body formatted by the run's code formatter, or as it is without one."""
        if not config.code_formatter:
            return body
        return _format_body_safe(body, config.code_formatter, generated_code=self._uses_standard_generation_templates)

    @staticmethod
    def _field_metadata(field: DataModelFieldBase, *, is_root_model: bool = False) -> ModelFieldMetadata:
        source_name = next((name for name in (field.original_name, field.alias, field.name) if name is not None), "")
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
        location = location_of(model.reference)
        source_ref = named_location(location, identity)
        source_path = _source_path_from_reference_path(source_ref) if location is None else list(location.tokens)
        return {
            "class_name": model.class_name,
            "name": model.name,
            "module": dotted_module(module),
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
        from datamodel_code_generator.model_metadata import model_metadata_payload  # noqa: PLC0415

        models = [(model, ctx.module) for ctx in contexts for model in ctx.models if model in model_identities]
        return (
            model_metadata_payload([
                cls._model_metadata(model, module, model_identities[model]) for model, module in models
            ]),
            tuple(redact_reference(model_identities[model]) for model, _ in models),
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
        with quiet():
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
        run: _PipelineRun[ParseConfig, ModuleContext, Result] = _PipelineRun(
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
            default_build_code_formatter=Parser._build_code_formatter,
            result_type=Result,
        )
        with context_values({
            SCHEMA_GRAPH: self._schema_graph,
            RENDER_READY: RenderReady(type(self), self.generation_store._memo),  # noqa: SLF001
            PARSE_MEMO: self.generation_store._memo,  # noqa: SLF001
            PARSE_CALLER: parse_caller(),
        }):
            self.__runner().run_steps(PIPELINE, self, run)
        return run.output

    def __runner(self) -> Runner:
        """Return a runner of the pipeline tables for this parser."""
        return Runner(self.__enter_phase)

    def __enter_phase(self, phase: Phase) -> None:
        """Mark that the pipeline enters ``phase`` on the generation store."""
        self.generation_store.enter_phase(phase)
