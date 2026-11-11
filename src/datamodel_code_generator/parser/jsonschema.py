"""JSON Schema parser implementation.

Handles parsing of JSON Schema, JSON, YAML, Dict, and CSV inputs to generate
Python data models. Supports draft-04 through draft-2020-12 schemas.
"""

from __future__ import annotations

import json
import math  # Preserve the established parser.jsonschema export.
import re
from collections import (
    Counter,  # Preserve the established parser.jsonschema export.
    defaultdict,
    deque,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from contextlib import (
    ExitStack,  # noqa: F401  # Preserve the established parser.jsonschema export.
    contextmanager,
    nullcontext,
    suppress,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from copy import (
    copy,  # noqa: F401  # Preserve the established parser.jsonschema export.
    deepcopy,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from dataclasses import replace
from functools import (
    cached_property,
    lru_cache,
    partial,
    reduce,
)
from itertools import chain, compress  # noqa: F401  # Preserve the established parser.jsonschema export.
from operator import and_, is_not  # noqa: F401  # Preserve the established parser.jsonschema export.
from pathlib import Path
from string import digits  # noqa: F401  # Preserve the established parser.jsonschema export.
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,  # noqa: F401  # Preserve the established parser.jsonschema export.
    Literal,
    NamedTuple,  # noqa: F401  # Preserve the established parser.jsonschema export.
    Optional,
    TypeAlias,  # noqa: F401  # Preserve the established parser.jsonschema export.
    TypeVar,
    Union,  # noqa: F401  # Preserve the established parser.jsonschema export.
    cast,
)
from urllib.parse import (
    ParseResult,
    unquote,
    urljoin,  # noqa: F401  # Preserve the established parser.jsonschema export.
    urlparse,
)
from warnings import (
    catch_warnings,  # noqa: F401  # Preserve the established parser.jsonschema export.
    simplefilter,  # noqa: F401  # Preserve the established parser.jsonschema export.
    warn,
)

from pydantic import (
    ConfigDict,  # noqa: F401  # Preserve the established parser.jsonschema export.
    Field,  # noqa: F401  # Preserve the established parser.jsonschema export.
    ValidationInfo,  # noqa: F401  # Preserve the established parser.jsonschema export.
    field_validator,  # noqa: F401  # Preserve the established parser.jsonschema export.
    model_validator,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from pydantic_core import core_schema  # noqa: F401  # Preserve the established parser.jsonschema export.
from typing_extensions import (
    Sentinel,  # noqa: F401  # Preserve the established parser.jsonschema export.
    Unpack,
    final,  # noqa: F401  # Preserve the established parser.jsonschema export.
    override,
)

from datamodel_code_generator import (
    AllOfClassHierarchy,  # noqa: F401  # Preserve the established parser.jsonschema export.
    AllOfMergeMode,  # noqa: F401  # Preserve the established parser.jsonschema export.
    DanglingRefWarning,
    Error,
    InputFileType,
    InvalidClassNameError,  # noqa: F401  # Preserve the established parser.jsonschema export.
    InvalidFileFormatError,
    ReadOnlyWriteOnlyModelType,
    SchemaParseError,
    SchemaResourceRefWarning,
    VersionMode,
    snooper_to_methods,
)
from datamodel_code_generator._alias_generators import generate_alias  # noqa: F401  # Preserve the established parser.jsonschema export.
from datamodel_code_generator._constraint_applicability import (
    LENGTH_KEYWORD_ORDER,
    LITERAL_DECIDABLE_KEYWORDS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    NO_KEYWORDS,
    NUMBER_KEYWORD_ORDER,  # noqa: F401  # Preserve the established parser.jsonschema export.
    PROPERTY_COUNT_KEYWORDS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    STRING_KEYWORD_ORDER,
    VALUES_AS_WRITTEN_KEY,
    DeclaredKinds,  # noqa: F401  # Preserve the established parser.jsonschema export.
    all_of_root_keyword_kinds,  # noqa: F401  # Preserve the established parser.jsonschema export.
    constrained_type_keyword_order,
    declared_keywords,  # noqa: F401  # Preserve the established parser.jsonschema export.
    declared_string_keywords_taken,  # noqa: F401  # Preserve the established parser.jsonschema export.
    enforced_keywords,  # noqa: F401  # Preserve the established parser.jsonschema export.
    holds_number_format,  # noqa: F401  # Preserve the established parser.jsonschema export.
    keyword_kinds,  # noqa: F401  # Preserve the established parser.jsonschema export.
    length_keywords_by_type,  # noqa: F401  # Preserve the established parser.jsonschema export.
    literal_check,  # noqa: F401  # Preserve the established parser.jsonschema export.
    literal_enforces_every_keyword,  # noqa: F401  # Preserve the established parser.jsonschema export.
    other_kind_keywords,
    property_consumed_keywords,
    split_declared_keywords,  # noqa: F401  # Preserve the established parser.jsonschema export.
    undeclared_keywords_taken,  # noqa: F401  # Preserve the established parser.jsonschema export.
    untaken_values,  # noqa: F401  # Preserve the established parser.jsonschema export.
    writes_beside_literal,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator._container_kinds import (
    ARRAYS,
    CONTAINER_FLAGS,
    GIVEN_CONTAINERS,
    ITEM_CONTAINERS,
    MAPPINGS,
    SEQUENCES,
    ContainerSlot,
)
from datamodel_code_generator._document_reader import (
    ArrayIndexOutOfRangeError,  # noqa: F401  # Preserve the established parser.jsonschema export.
    MergedRefSiteRecursionError,
    string_keyed,
)
from datamodel_code_generator._format_types import (
    DatetimeClassType,
)
from datamodel_code_generator._json_pointer import (
    escape_token,  # noqa: F401  # Preserve the established parser.jsonschema export.
    fragment_pointer,
    join_pointer,  # noqa: F401  # Preserve the established parser.jsonschema export.
    reference_tokens,
    unescape_token,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator._json_schema import (
    DEFINITION_KEYS,
    REF_PRE_PASS_FIELDS,
    REF_PRE_PASS_POSITIONS,
    SCHEMA_MAP_KEYS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    SCHEMA_POSITIONS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    SCHEMA_SHAPES,  # noqa: F401  # Preserve the established parser.jsonschema export.
    SCHEMA_VALUE_KEYS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    SINGLE_SCHEMA_SHAPES,  # noqa: F401  # Preserve the established parser.jsonschema export.
    definition_ref,
    non_null_types,
    schema_positions,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator._message_text import english_list, for_count
from datamodel_code_generator._model_keys import (
    FIELDLESS_SITE_KEY,
    ITEM_COUNTS_SITE_KEY,
    REF_BRANCH_SCHEMA_KEY,
    model_key_of,
)
from datamodel_code_generator._module_paths import dotted_name
from datamodel_code_generator._mro import (
    linearize,  # noqa: F401  # Preserve the established parser.jsonschema export.
    sort_descendants_first,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator._option_rules import implied_options
from datamodel_code_generator._paths import file_uri_path
from datamodel_code_generator._python_syntax import subscript_text
from datamodel_code_generator._records import record
from datamodel_code_generator._regex_syntax import (
    conjoin_patterns,
    is_portable_pattern,  # noqa: F401  # Preserve the established parser.jsonschema export.
    literal_pattern_value,  # noqa: F401  # Preserve the established parser.jsonschema export.
    lookahead_patterns,
)
from datamodel_code_generator._requirements import python_target_meets
from datamodel_code_generator._scc import (
    strongly_connected_components,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator._shared_types import DefaultPutDict
from datamodel_code_generator._source import (
    YamlValue,
    load_data,  # noqa: F401  # Preserve the established parser.jsonschema export.
    load_data_from_path,
)
from datamodel_code_generator._url_redaction import named_location, redact_path, redact_reference, redact_url
from datamodel_code_generator._user_keys import ClassScope, class_key, template_data_keys
from datamodel_code_generator._user_keys import class_scope as _class_scope_of
from datamodel_code_generator._warning_control import first_report, quiet
from datamodel_code_generator.deprecations import warn_deprecated
from datamodel_code_generator.enums import (
    AliasGenerator,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator.imports import IMPORT_ANY, Import, Imports
from datamodel_code_generator.model import (  # Preserve the established parser.jsonschema export.
    DataModel,
    DataModelFieldBase,
)
from datamodel_code_generator.model._decisions.enum_kind import (
    decide_enum_kind,
    enum_as_literal,  # noqa: F401  # Preserve the established parser.jsonschema export.
    holds_inherited_member_value,  # noqa: F401  # Preserve the established parser.jsonschema export.
    holds_member_value,  # noqa: F401  # Preserve the established parser.jsonschema export.
    is_literal_kind,  # noqa: F401  # Preserve the established parser.jsonschema export.
    literal_value_kind,  # noqa: F401  # Preserve the established parser.jsonschema export.
    values_no_literal_holds,
)
from datamodel_code_generator.model._decisions.literal_default import const_type  # noqa: F401  # Preserve the established parser.jsonschema export.
from datamodel_code_generator.model._decisions.tag_value import (
    FieldTag,  # noqa: F401  # Preserve the established parser.jsonschema export.
    distinct_tags,  # noqa: F401  # Preserve the established parser.jsonschema export.
    field_tag,  # noqa: F401  # Preserve the established parser.jsonschema export.
    tagged_union_discriminator,  # noqa: F401  # Preserve the established parser.jsonschema export.
    union_tag_values,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator.model.base import (
    ALL_MODEL,
    UNDEFINED,
    _field_source_name,
    _mark_forbids_unknown_fields,
    get_inherited_fields,  # noqa: F401  # Preserve the established parser.jsonschema export.
    sanitize_module_name,
)
from datamodel_code_generator.model.enum import (
    NULL_ENUM_MEMBER_VALUE,
    SUBCLASS_BASE_CLASSES,
    Enum,
    EnumMemberValue,
    IntEnum,  # noqa: F401  # Preserve the established parser.jsonschema export.
    StrEnum,  # noqa: F401  # Preserve the established parser.jsonschema export.
    _enum_class,
)
from datamodel_code_generator.model.output import (
    OutputModelContext,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator.model.runtime_validation import (
    UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP,  # noqa: F401  # Preserve the established parser.jsonschema export.
    UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP,  # noqa: F401  # Preserve the established parser.jsonschema export.
    UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP,  # noqa: F401  # Preserve the established parser.jsonschema export.
    UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP,  # noqa: F401  # Preserve the established parser.jsonschema export.
    ConditionalRequiredRule,
    IndependentDeclaredPatternPropertiesRule,  # noqa: F401  # Preserve the established parser.jsonschema export.
    IndependentModelPatternPropertiesRule,  # noqa: F401  # Preserve the established parser.jsonschema export.
    PatternPropertiesRule,
    PropertyCountRule,
    RequiredGroupsRule,
    SchemaRuntimeValidation,
    UniqueItemsPath,
    UniqueItemsPathStep,  # noqa: F401  # Preserve the established parser.jsonschema export.
    UniqueItemsRule,
    _is_internal_schema_runtime_validation,
    _make_internal_schema_runtime_validation,
)
from datamodel_code_generator.parser import (
    _all_of_values,
    _field_rows,
    _inherited_schemas,
    _lightweight_types,
    _object_maps,
    _rw_split,
    _schema_documents,
    _schema_refs,
    _schema_validators,
    _type_overrides,
    _union_tags,
    _value_keywords,
)
from datamodel_code_generator.parser._all_of_values import (
    _ALL_OF_PARTS,
    _ALL_OF_VALUE_ITEM_KEYWORDS,
    _DERIVED_FROM_REF_KEY,
    _SITE_CONSTRAINTS_SCHEMA_KEY,
)
from datamodel_code_generator.parser._field_rows import uses_default_with_required  # noqa: F401  # Preserve the established parser.jsonschema export.
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._inherited_schemas import (
    _INHERITED_MATERIALIZED_TYPE_SHAPE_KEY,
    _INHERITED_OVERRIDE_KEY,
)
from datamodel_code_generator.parser._intersection import (
    BRANCH_COMBINATION_LIMIT,
    INHERITED_SCHEMA_KEYWORDS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    INTERSECTED_KEYWORDS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    LEGACY_ROWS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    POSITIONAL_SCHEMA_KEYWORDS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    KeywordIntersection,
    all_of_member_row,
    bounds_admit_no_value,  # noqa: F401  # Preserve the established parser.jsonschema export.
    branch_combinations,
    common_types,  # noqa: F401  # Preserve the established parser.jsonschema export.
    concatenating_row,
    declared_types,
    inherited_override_row,  # noqa: F401  # Preserve the established parser.jsonschema export.
    inherited_positional_tail,  # noqa: F401  # Preserve the established parser.jsonschema export.
    inherits_keyword,  # noqa: F401  # Preserve the established parser.jsonschema export.
    intersect_constraint,  # noqa: F401  # Preserve the established parser.jsonschema export.
    intersect_enum_values,
    intersect_keywords,  # noqa: F401  # Preserve the established parser.jsonschema export.
    intersect_multiple_of,  # noqa: F401  # Preserve the established parser.jsonschema export.
    intersect_types,
    intersects_all_of_constraints,
    kept_legacy_rows,
    merge_inherited_keyword,  # noqa: F401  # Preserve the established parser.jsonschema export.
    merge_inherited_value,  # noqa: F401  # Preserve the established parser.jsonschema export.
    warn_unsatisfiable,
    warn_untaken_patterns,
)
from datamodel_code_generator.parser._intersection import json_values_equal as _json_literal_values_equal
from datamodel_code_generator.parser._naming import UNRECORDED, AllOfPart, Composed, NamingScope, composed_name
from datamodel_code_generator.parser._null_facts import (
    NullFacts,
    NullMode,
    NullSite,
    NullSources,
    dump_null_sources,  # noqa: F401  # Preserve the established parser.jsonschema export.
    null_schema,
    resolve_null_facts,
)
from datamodel_code_generator.parser._object_maps import _schema_assertions, _with_null
from datamodel_code_generator.parser._origins import (
    with_enum_literal,
    with_field_origin,
    with_type_origin,
    with_unique_items,
)
from datamodel_code_generator.parser._passes._keys import (
    _DERIVED_FROM_KEY,
    _RAW_SCHEMA_DEFAULT_UNDEFINED,
    declares_type_list_unique_items,
    with_type_list_unique_items,
)
from datamodel_code_generator.parser._passes._reference_users import (
    type_additional_properties_users,
    type_reference_users,
)
from datamodel_code_generator.parser._pipeline import stacklevel_at_parse_caller
from datamodel_code_generator.parser._schema_dispatch import (
    Default,
    DispatchTable,
    Row,
    SchemaSite,
    Step,
    TypedChild,
)
from datamodel_code_generator.parser._schema_documents import (
    _find_json_schema_anchor_pointer,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator.parser._schema_graph import (
    SCHEMA_GRAPH,  # noqa: F401  # Preserve the established parser.jsonschema export.
    ConvertedRefs,  # noqa: F401  # Preserve the established parser.jsonschema export.
    DocumentId,  # noqa: F401  # Preserve the established parser.jsonschema export.
    RefEdge,  # noqa: F401  # Preserve the established parser.jsonschema export.
    RefKey,  # noqa: F401  # Preserve the established parser.jsonschema export.
    RefReading,
    ReservedTarget,  # noqa: F401  # Preserve the established parser.jsonschema export.
    ResolvedRef,
    SchemaGraphBuilder,
    _TargetNode,
    canonical_ref_spelling,  # noqa: F401  # Preserve the established parser.jsonschema export.
    keep_resolved_ref,
    walk_schema_resources,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator.parser._schema_object import (
    _CONDITIONAL_SCHEMA_KEYWORDS,
    _COPIED_NULLABLE_KEY,
    _NULLABLE_TYPE_LIST_KEY,
    DEFAULT_FIELD_KEYS,
    EMPTY_ENUM_CONTEXT,
    EXCLUDE_FIELD_KEYS,  # noqa: F401  # Preserve the established parser.jsonschema export.
    EXCLUDE_FIELD_KEYS_IN_JSON_SCHEMA,  # noqa: F401  # Preserve the established parser.jsonschema export.
    IGNORED_REF_SIBLINGS_CONTEXT,
    PATTERN_READING_CONTEXT,
    READ_UNEVALUATED_PROPERTIES_CONTEXT,
    Discriminator,  # noqa: F401  # Preserve the established parser.jsonschema export.
    JSONReference,
    JsonSchemaLiteral,
    JsonSchemaObject,
    _has_map_key_keywords,
    _is_false_all_of,
    _locate,
    _located,
    _named_path,
    _read_type,
    _source_location,
    _type_null_sources,
    get_ref_type,
    reads_ref_alone,
)
from datamodel_code_generator.parser._schema_reading import (
    _ARRAY_ITEMS_CONSTRAINT_FIELDS,
    _LIGHTWEIGHT_MAX_DEPTH,
    _LIGHTWEIGHT_MAX_UNION_ELEMENTS,
    _MISSING_JSON_POINTER,
    _NAMED_ENTRY_MAP_KEYWORDS,
    _RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY,
    _AllOfItemGroups,
    _constraint_values,
    _declares_key_keywords,
    _enum_without_positions,
    _get_json_value_type,  # noqa: F401  # Preserve the established parser.jsonschema export.
    _get_model_by_decoded_path,
    _intersect_parts,
    _merges_ref_siblings,
    _numeric_type_domain,
    _split_json_pointer,
    get_model_by_path,
    split_json_pointer,  # noqa: F401  # Preserve the established parser.jsonschema export.
    unescape_json_pointer_segment,
    unescaped_slash_pointer,
)
from datamodel_code_generator.parser._schema_refs import (
    _CONJUNCTION_PARTS_KEY,
    _MERGED_REF_TARGET_KEY,
    _REPORTED_VALUE_CONFLICT_KEY,
    _DynamicEdges,
    _DynamicSpecialization,  # noqa: F401  # Preserve the established parser.jsonschema export.
    _DynamicTarget,
    _ref_path_parts,
    _Worklist,
)
from datamodel_code_generator.parser._union_tags import (
    TaggedUnionValue,  # noqa: F401  # Preserve the established parser.jsonschema export.
    _get_union_variant_name,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator.parser._value_keywords import (
    _LITERAL_PARTS,
    _keep_values_as_written,
    _literal_constraints,
    _literal_met,
)
from datamodel_code_generator.parser.base import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
    SPECIAL_PATH_FORMAT,  # noqa: F401  # Preserve the established parser.jsonschema export.
    Parser,
    Source,
    _ConstNarrowing,
    get_special_path,
    title_to_class_name,  # noqa: F401  # Preserve the established parser.jsonschema export.
)
from datamodel_code_generator.parser.schema_version import (
    _KIND_READINGS,
    DocumentDialect,
    JsonSchemaFeatures,
    SchemaKind,
    document_dialect,
    get_data_formats,
)
from datamodel_code_generator.python_literal import (
    _safe_non_finite_value,
    _semantic_value_text,
    stable_repr,
)
from datamodel_code_generator.reference import (
    _ALIAS_RESOLUTION_CLASS_NAME_KEY,
    BINARY_CONTENT_KEY,
    SCHEMA_LOCATION_CONFLICT_KEY,
    SCHEMA_LOCATION_KEY,
    SPECIAL_PATH_MARKER,  # noqa: F401  # Preserve the established parser.jsonschema export.
    ModelType,
    Reference,
    SchemaLocation,
    _is_special_path,
    get_inferred_module_name,  # noqa: F401  # Preserve the established parser.jsonschema export.
    is_url,
    location_of,
    subtype_ref_of,
)
from datamodel_code_generator.types import (
    ANY,
    NONE,  # noqa: F401  # Preserve the established parser.jsonschema export.
    DataType,
    EmptyDataType,
    FloatConstraint,  # noqa: F401  # Preserve the established parser.jsonschema export.
    Types,
    UnionIntFloat,
    build_union,
)
from datamodel_code_generator.util import BaseModel, get_yaml_parse_errors  # noqa: F401  # Preserve the established parser.jsonschema export.
from datamodel_code_generator.validators import _validate_schema_python_import_path

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Generator, Iterable, Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager

    from typing_extensions import TypeIs

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr, PythonTypeRuntimeSymbol
    from datamodel_code_generator._types import JSONSchemaParserConfigDict
    from datamodel_code_generator.config import JSONSchemaParserConfig
    from datamodel_code_generator.parser._inherited_schemas import InheritedParentRow, ModelDeclarations
    from datamodel_code_generator.parser._naming import Claim
    from datamodel_code_generator.parser._rw_split import _Site
    from datamodel_code_generator.parser._schema_reading import SlashNames
    from datamodel_code_generator.parser.base import DocumentRoles

JsonSchemaConstraintKey = Literal[
    "minimum",
    "maximum",
    "multipleOf",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "pattern",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
]
JsonSchemaConstraintValue = int | float | str | bool
JsonSchemaDataTypeKwargValue = JsonSchemaConstraintValue


def _update_false_schema_refs(
    false_schema_refs: set[str] | None,
    resolved_ref: str,
    *,
    is_false: bool,
) -> set[str] | None:
    """Record only false refs; the regular fact cache proves all other refs were validated."""
    match is_false:
        case True:
            if false_schema_refs is None:
                false_schema_refs = set()
            false_schema_refs.add(resolved_ref)
        case _:
            if false_schema_refs is not None:
                false_schema_refs.discard(resolved_ref)
    return false_schema_refs


def _is_counted_map(item: JsonSchemaObject) -> bool:
    """Return whether a schema is a map whose property counts the type alias of a nested site holds.

    An object or a ``propertyNames`` schema that no array type joins; ``_has_effective_constraints`` gives the counts
    and leaves out one with properties, pattern properties or required names, which a model or a union of maps types.
    """
    return (item.is_object or item.propertyNames is not None) and not item.is_array


_EXTERNAL_REF_MAPPING_IMPORT = ImportProducer("external-ref-mapping")
_X_PYTHON_IMPORT = ImportProducer("x-python-import")
_EXTRA_FIELDS_KEYS = ("extra_fields", "allow_extra_fields")
_DEFAULT_LEFT_OFF_KEY = "_default_left_off"
_BRANCH_REFERENCE_KEY = "_branch_reference"
"""Marks the ``$ref`` branch ``_parse_ref_branch_model`` reads as an allOf member with the class its site types it as,
the specialization of its dynamic scope included, which ``_parse_all_of_item`` takes as the base class."""
_RECORDED_TYPE_NULL_SITES: frozenset[NullSite] = frozenset({
    "object-model",
    "root-model",
    "type-union",
    "multiple-types-root",
    "combined-branch",
    "inherited-child",
})
"""The sites that read the answer ``JsonSchemaObject.type_has_null`` recorded when it was first asked."""

_PYTHON_UNION_BASE_TYPES = frozenset({"Union", "Optional"})
_PYTHON_TYPE_FLAGS: dict[str, str] = {
    name: CONTAINER_FLAGS[given.slot] for name, given in GIVEN_CONTAINERS.items() if given.slot not in {"list", "dict"}
}
"""The container flag each class an x-python-type names gives its data type, but a list's and a dict's (the callers'
default)."""
_RootTypeRow = Literal[
    "ref",
    "custom-type",
    "constrained-type-union",
    "array",
    "combined",
    "all-of",
    "object-map",
    "enum",
    "typed-nullable-map",
    "type",
    "any",
]
"""The rows that build the root type of a schema (``JsonSchemaParser._root_type_row``)."""
_ROOT_MAP_ROWS: frozenset[_RootTypeRow] = frozenset({"object-map", "typed-nullable-map"})
"""The rows that build the root type of a schema as an object map."""


@record
class _ConstraintSite:
    """How a field site assembles its constraints, where an object property and a root field differ.

    ``JsonSchemaParser._assemble_field_constraints`` is the one assembly; ``_PROPERTY_SITE`` is the field of an object
    property (``get_object_field``), ``_ROOT_SITE`` the field of a root model (``_parse_root_type_with_context``).
    """

    holds_schema: bool
    """The field is the root of the model its schema becomes, so under ``--field-constraints`` (or as a preserved root)
    it reads the keywords beside a ``$ref``, a combinator or an object too; an object property leaves them to the model
    or the class its schema becomes (``is_constraints_field``)."""
    numbers_keep_bounds: bool
    """A type list of number kinds keeps the bounds its branches share on the field (#3971, 0.80.0), as the property
    builds its type without them (``_build_field_shared_numeric_constraints``); a root's type carries them."""
    consumed_at_property: bool
    """The keywords a zero-bound class enforces are left out (row ``legacy-consumed-at-object-property``)."""
    keeps_tuple_lengths: bool
    """Under ``--field-constraints`` (or as a preserved root) the lengths its schema declares for a fixed tuple stay on
    the field, with no item counts added to it, where an object property and the array builder leave them out (the
    tuple has them): row ``legacy-root-tuple-lengths``."""
    counts_beside_models: bool
    """The item counts that ``contains`` and boolean ``items`` imply stay on a field whose type is a model, for the
    type the output writes to decide: a type alias of an array or a collapsed array takes them, a model whose value
    takes none leaves them out with a warning (``DataTypeManager.model_value_constraint_keywords``, msgspec's
    ``Meta``); a root field writes none there, as in 0.88.0: row ``legacy-property-counts-beside-models``."""
    records_by_option: bool
    """It records the keywords it leaves out and those its type decides only under ``--field-constraints`` (#4997,
    0.87.0), where a property records them once it assembled any: row ``legacy-root-records-by-option``."""
    empty_record: bool
    """It holds a constraints record even when it takes none (an empty one), as root models always did."""


_PROPERTY_SITE = _ConstraintSite(
    holds_schema=False,
    numbers_keep_bounds=True,
    consumed_at_property=True,
    keeps_tuple_lengths=False,
    counts_beside_models=True,
    records_by_option=False,
    empty_record=False,
)
_ROOT_SITE = _ConstraintSite(
    holds_schema=True,
    numbers_keep_bounds=False,
    consumed_at_property=False,
    keeps_tuple_lengths=True,
    counts_beside_models=False,
    records_by_option=True,
    empty_record=True,
)


@record
class _AssembledConstraints:
    """The constraints a field takes, those it leaves out and the property counts its map type dropped."""

    constraints: Any
    untaken: dict[str, Any]
    dropped_counts: dict[str, int]
    records: bool
    """Whether the field records the keywords its type decides (``mark_type_dependent_constraints``)."""
    type_list_unique: bool = False
    """Whether the field takes the ``uniqueItems`` of a type list (``type_list_keywords``)."""


@record
class _MapValue:
    """How the value of a mapping is built (``JsonSchemaParser._additional_properties_value``).

    ``parse_item`` builds ``schema`` with ``parent`` as its parent, unless it is ``constrained``: then the root model
    that preserves its constraints is built from ``schema`` (a ``$ref`` merged with its siblings, or an allOf of
    primitives, ``preserve_root_model``), marked as an item-count site (``ITEM_COUNTS_SITE_KEY``) where only its item
    counts make it one (``item_counts_site``).
    """

    schema: JsonSchemaObject
    parent: JsonSchemaObject | None = None
    constrained: bool = False
    preserve_root_model: bool = False
    item_counts_site: bool = False


class _RefSiteRecursionError(RecursionError):
    """A recursion limit exceeded in the parse in place of one `$ref` with sibling keywords, which no other one holds.

    The `$ref` site that holds it turns it into ``MergedRefSiteRecursionError``: only a chain of such sites nests a
    parse per hop, which --use-one-model-per-ref-site removes.
    """


_ValidatedT = TypeVar("_ValidatedT")


def _then_parse_refs(build: Callable[[JsonSchemaParser, _Site], object]) -> Callable[[JsonSchemaParser, _Site], None]:
    """Return the build of a definition row: ``build``, then the references of the site's schema."""

    def build_definition(parser: JsonSchemaParser, site: _Site) -> None:
        build(parser, site)
        parser.parse_ref(site.obj, site.path)

    return build_definition


def _array_applies(parser: JsonSchemaParser, site: _Site) -> bool:  # noqa: ARG001
    """Return whether the schema of a site is an array."""
    return site.obj.is_array


def _all_of_applies(parser: JsonSchemaParser, site: _Site) -> bool:  # noqa: ARG001
    """Return whether the schema of a site has allOf."""
    return bool(site.obj.allOf)


def _one_of_any_of_applies(parser: JsonSchemaParser, site: _Site) -> bool:  # noqa: ARG001
    """Return whether the schema of a site has oneOf or anyOf."""
    return bool(site.obj.oneOf or site.obj.anyOf)


def _all_of_beside_one_of_applies(parser: JsonSchemaParser, site: _Site) -> bool:  # noqa: ARG001
    """Return whether the schema of a site has allOf beside oneOf or anyOf: the union, each branch with the allOf."""
    return bool(site.obj.allOf and (site.obj.oneOf or site.obj.anyOf))


def _ref_applies(parser: JsonSchemaParser, site: _Site) -> bool:  # noqa: ARG001
    """Return whether the schema of a site has a ``$ref``."""
    return bool(site.obj.ref)


def _ref_siblings_applies(parser: JsonSchemaParser, site: _Site) -> bool:  # noqa: ARG001
    """Return whether the schema of a site reads the keywords beside its ``$ref`` with the schema it names."""
    return _merges_ref_siblings(site.obj)


def _schema_validator_object_applies(parser: JsonSchemaParser, site: _Site) -> bool:
    """Return whether the schema of a site is parsed as an object model for its schema validators."""
    return bool(
        parser.generate_schema_validators and parser._should_parse_object_with_schema_validators(site.obj)  # noqa: SLF001
    )


def _parse_root_type_site(parser: JsonSchemaParser, site: _Site) -> DataType:
    """Parse the schema of a site into a root model."""
    return parser.parse_root_type(site.name, site.obj, site.path)


def _parse_all_of_site(parser: JsonSchemaParser, site: _Site) -> DataType:
    """Parse the allOf schema of a site into its model."""
    return parser.parse_all_of(site.name, site.obj, site.path)


def _get_ref_data_type_site(parser: JsonSchemaParser, site: _Site) -> DataType:
    """Type a site by the ``$ref`` of its schema."""
    return parser.get_ref_data_type(cast("str", site.obj.ref))


def _parse_object_model_site(parser: JsonSchemaParser, site: _Site) -> DataType:
    """Parse the object schema of a site into its model."""
    return parser._parse_object_model(site.name, site.obj, site.path)  # noqa: SLF001


def _parse_combined_root(parser: JsonSchemaParser, site: _Site, *, const_enum: bool = False) -> DataType:
    """Parse the oneOf or anyOf schema of a site into a root model, and its properties into a model when it types none.

    With ``const_enum`` (a definition) a oneOf or anyOf of consts is the Enum or Literal root model of the site's name.
    """
    obj, name, path = site.obj, site.name, site.path
    if (
        const_enum
        and (
            const_enum_type := parser._parse_combined_const_enum(  # noqa: SLF001
                name, obj, obj.oneOf or obj.anyOf, path, literal_as_root=True
            )
        )
        is not None
    ):
        return const_enum_type
    data_type = parser.parse_root_type(name, obj, path)
    if isinstance(data_type, EmptyDataType) and obj.properties:
        parser.parse_object(name, obj, path)
    return data_type


def _field_union_row(
    name: str, keyword: Literal["anyOf", "oneOf"]
) -> Row[JsonSchemaParser, JsonSchemaObject, DataType]:
    """Return the field row of a union keyword: its consts as one Enum or Literal, else the union of its members."""

    def applies(parser: JsonSchemaParser, site: _Site) -> bool:  # noqa: ARG001
        return bool(getattr(site.obj, keyword))

    def build(parser: JsonSchemaParser, site: _Site) -> DataType:
        if combined_const_enum := parser._parse_combined_const_enum(  # noqa: SLF001
            site.name,
            site.obj,
            getattr(site.obj, keyword),
            get_special_path("enum", site.path),
            singular_name=site.singular_name,
        ):
            return combined_const_enum
        parse_members = parser.parse_any_of if keyword == "anyOf" else parser.parse_one_of
        return build_union(parser.data_type, parse_members(site.name, site.obj, get_special_path(keyword, site.path)))

    return Row(name, applies, build, keywords=frozenset({keyword}))


def _parse_no_object_kind(parser: JsonSchemaParser, site: _Site) -> None:
    """Parse nothing: the schema of the site is not of an object kind."""


def _without_nullable(dump: dict[str, Any]) -> dict[str, Any]:
    """Return a dumped schema without its ``nullable``."""
    return {key: value for key, value in dump.items() if key != "nullable"}


def _mark_copied_nullable(
    branch: JsonSchemaObject,
    parent: dict[str, Any],
    written: JsonSchemaObject | None,
    *,
    default_left_off: bool = False,
) -> JsonSchemaObject:
    """Mark a branch of a combined schema whose ``nullable`` the merge copied from ``parent``.

    ``written`` is the branch schema as written; a ``nullable`` it writes itself is its own and stays unmarked.
    ``default_left_off`` marks a branch whose parent default stays with the parent, so its root requires its value.
    """
    if "nullable" in parent and (written is None or "nullable" not in written.model_fields_set):
        branch.__dict__[_COPIED_NULLABLE_KEY] = True
    if default_left_off:
        branch.__dict__[_DEFAULT_LEFT_OFF_KEY] = True
    return branch


def _external_ref_mapping_key(file_part: str, base_path: Path) -> str:
    """Return the key of a file in ``--external-ref-mapping``: a URL as written, else the absolute resolved path."""
    return file_part if is_url(file_part) else str((base_path / file_part).resolve())


def _shifted_enum_entries_message(class_name: str, obj: JsonSchemaObject, named_values: list[Any]) -> str | None:
    """Describe the 0.83.0 shift of enum names or descriptions that give null an entry beside the named values.

    The entries are matched to the values without null, so a list with one entry per enum value, null included,
    gives each value after null the entry of the value before it; an entry equal to the value's own is no shift.
    """
    if len(named_values) == len(obj.enum):
        return None
    names_key = "x-enum-varnames" if obj.x_enum_varnames else "x-enumNames"
    positions = [position for position, value in enumerate(obj.enum) if value is not None]
    shifts = [
        (key, noun, kind, value, entries[index], entries[position])
        for key, noun, kind, entries in (
            (names_key, "names", "named", obj.x_enum_varnames or obj.x_enum_names),
            ("x-enum-descriptions", "descriptions", "described", obj.x_enum_descriptions),
        )
        if len(entries) == len(obj.enum)
        for index, (position, value) in enumerate(zip(positions, named_values, strict=True))
        if index != position and entries[index] != entries[position]
    ]
    if not shifts:
        return None
    lists = {key: noun for key, noun, *_ in shifts}
    _, _, kind, value, taken, own = shifts[0]
    return (
        f"{class_name} keeps the shifted member {english_list(lists.values(), 'and', 'each')} of 0.83.0: "
        f"{english_list(lists, 'and', 'each')} {for_count(len(lists), 'gives', 'give')} null an entry, but the entries "
        f"are matched to the values without null, so {stable_repr(value)} is {kind} {stable_repr(taken)} instead of "
        f"{stable_repr(own)}. Leave the entry of null out of the list."
    )


def __getattr__(name: str) -> Any:
    """Return compatibility model classes without importing them on parser load."""
    match name:
        case "TypedDictModel":
            from datamodel_code_generator.model.typed_dict import TypedDict as TypedDictModel  # noqa: PLC0415

            return TypedDictModel
    raise AttributeError(name)


def _null_sources(
    obj: JsonSchemaObject, site: NullSite, type_has_null: Callable[[JsonSchemaObject], bool]
) -> NullSources:
    """Return the keywords of a schema object that can admit null, as written (``parser/_null_facts.py``).

    Only the sites that read it ask ``type_has_null`` (``JsonSchemaParser._type_has_null``), which records its first
    answer.
    """
    sources = _type_null_sources(obj)
    return replace(sources, type_has_null=type_has_null(obj)) if site in _RECORDED_TYPE_NULL_SITES else sources


def _type_excludes_member_null(obj: JsonSchemaObject, mode: NullMode) -> bool:
    """Return whether the ``type`` of ``obj`` excludes the null of its members (row ``type-excludes-member-null``).

    Never without --intersect-allof-keywords (``mode``).
    """
    return (
        mode.type_excludes_member_null
        and resolve_null_facts(_type_null_sources(obj), "schema-type", mode).rule == "type-excludes-member-null"
    )


def _excluded_null_members(
    obj: JsonSchemaObject, members: Sequence[JsonSchemaObject | bool], mode: NullMode
) -> list[JsonSchemaObject]:
    """Return the members typed only null that the ``type`` beside them excludes (row ``type-excludes-member-null``).

    None of them when every member is one: the combined schema keeps them rather than admit no value. None of them
    without --intersect-allof-keywords (``mode``).
    """
    if not _type_excludes_member_null(obj, mode):
        return []
    null_members = [
        member
        for member in members
        if isinstance(member, JsonSchemaObject)
        and not member.ref
        and member.type in ("null", ["null"])
        and not (member.anyOf or member.oneOf)
    ]
    return null_members if len(null_members) < len(members) else []


def _expanded_variants(data_type: DataType) -> list[tuple[str, Reference]] | None:
    """Return the ``$ref`` and the reference of each subtype a discriminator expanded a ``$ref`` branch's type to.

    The type is the union the OpenAPI parser built for a ``$ref`` to a discriminated schema, each member recording the
    ``$ref`` of the subtype it stands for (``subtype_ref_of``); None for any other type.
    """
    variants = [
        (ref, member.reference)
        for member in data_type.data_types
        if member.reference is not None and (ref := subtype_ref_of(member)) is not None
    ]
    return variants if data_type.reference is None and variants and len(variants) == len(data_type.data_types) else None


def _declared_value_types(schema: JsonSchemaObject) -> tuple[str, ...]:
    """Return the types a schema's ``type`` declares, with null where its ``nullable`` is true.

    ``nullable`` adds null to a declared ``type`` only, so a schema without one declares none and admits every type.
    """
    types = declared_types(schema.type)
    return (*types, "null") if types and schema.nullable is True and "null" not in types else types


def _types_exclude(types: Sequence[str], branch: JsonSchemaObject | bool) -> bool:  # noqa: FBT001
    """Return whether an inline ``branch`` declares a ``type`` that admits none of ``types`` (``intersect_types``)."""
    return (
        isinstance(branch, JsonSchemaObject)
        and not branch.ref
        and bool(branch_types := _declared_value_types(branch))
        and not intersect_types(types, branch_types)
    )


def _is_directory_read_error(exc: OSError, path: Path) -> bool:
    """Recognize the platform-specific errors raised when reading a directory."""
    return isinstance(exc, IsADirectoryError) or (isinstance(exc, PermissionError) and path.is_dir())


def _validate_external_ref(ref: str) -> None:
    """Reject references with more than one fragment delimiter."""
    if ref.count("#") <= 1:
        return
    msg = f"Invalid external $ref: {redact_url(ref)}"
    raise Error(msg)


json_schema_data_formats: dict[str, dict[str, Types]] = get_data_formats(is_openapi=True)


def _get_type(
    type_: str,
    format__: str | None = None,
    data_formats: dict[str, dict[str, Types]] | None = None,
) -> Types:
    """Get the appropriate Types enum for a given JSON Schema type and format."""
    if data_formats is None:  # pragma: no cover
        data_formats = json_schema_data_formats
    if type_ not in data_formats:
        return Types.any
    if (type_format := data_formats[type_].get("default" if format__ is None else format__)) is not None:
        return type_format

    warn(f"format of {stable_repr(format__)} not understood for {stable_repr(type_)} - using default", stacklevel=2)
    return data_formats[type_]["default"]


ALLOWED_DEFAULT_FACTORIES: frozenset[str] = frozenset({"dict", "list", "set"})


def _validate_default_factory(default_factory: Any) -> str:
    if isinstance(default_factory, str) and default_factory in ALLOWED_DEFAULT_FACTORIES:
        return default_factory
    allowed_values = ", ".join(sorted(ALLOWED_DEFAULT_FACTORIES))
    msg = f"default_factory must be one of: {allowed_values}"
    raise Error(msg)


DEFAULT_MODEL_EXTRA_KEYS: set[str] = {
    "contentEncoding",
    "contentMediaType",
    "contentSchema",
    "externalDocs",
    "xml",
}


def _without_x_prefix(key: str) -> str:
    """Return an extra key without its ``x-`` prefix: the prefix only, never the ``x`` or ``-`` after it."""
    return key.removeprefix("x-")


_DEFAULT_SCHEMA_PATHS = ("#/definitions", "#/$defs")


def _set_schema_location(reference: Reference, location: SchemaLocation | None) -> None:
    """Record where the model of a reference is built from; two different schema objects leave it without one.

    Schema objects whose parse paths collide reach one Reference, and the location of either can disagree with the
    model's content, so the Reference keeps none and is marked as conflicting. The location check skips it.
    """
    if location is not None and reference.__dict__.setdefault(SCHEMA_LOCATION_KEY, location) != location:
        _mark_location_conflict(reference)


def _mark_location_conflict(reference: Reference) -> None:
    """Leave a Reference that several schema objects reach without a location."""
    reference.__dict__[SCHEMA_LOCATION_KEY] = None
    reference.__dict__[SCHEMA_LOCATION_CONFLICT_KEY] = True


def _ends_with(tokens: tuple[str, ...], container: tuple[str, ...]) -> bool:
    """Return whether ``tokens`` name an entry of ``container``, its tokens right before the last one."""
    return len(tokens) > len(container) and tokens[-1 - len(container) : -1] == container


@snooper_to_methods()  # noqa: PLR0904
class JsonSchemaParser(Parser["JSONSchemaParserConfig", "JsonSchemaFeatures"]):
    """Parser for JSON Schema, JSON, YAML, Dict, and CSV formats."""

    SCHEMA_PATHS: ClassVar[list[str]] = list(_DEFAULT_SCHEMA_PATHS)
    SCHEMA_OBJECT_TYPE: ClassVar[type[JsonSchemaObject]] = JsonSchemaObject
    REQUIRED_ONLY_SCHEMA_ALLOWED_FIELDS: ClassVar[frozenset[str]] = frozenset({"required", "type", "extras"})
    CONDITIONAL_SCHEMA_ALLOWED_FIELDS: ClassVar[frozenset[str]] = REQUIRED_ONLY_SCHEMA_ALLOWED_FIELDS | {"properties"}
    STRING_PROPERTY_NAME_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "type",
        "anyOf",
        "pattern",
        "minLength",
        "maxLength",
        "title",
        "description",
        "default",
    })
    _cache_local_sources_during_parse: ClassVar[bool] = True
    _cache_parsed_sources_from_path: ClassVar[bool] = True
    _input_file_type: ClassVar[InputFileType] = InputFileType.JsonSchema
    _non_dict_source_is_invalid: ClassVar[bool] = False
    _named_entry_maps: ClassVar[frozenset[str]] = _NAMED_ENTRY_MAP_KEYWORDS
    _component_schemas_are_resources: ClassVar[bool] = False
    _schema_kind: ClassVar[SchemaKind] = "jsonschema"

    COMPATIBLE_PYTHON_TYPES: ClassVar[dict[str, frozenset[str]]] = {
        "string": frozenset({"str", "String"}),
        "integer": frozenset({"int", "Integer"}),
        "number": frozenset({"float", "int", "Number"}),
        "boolean": frozenset({"bool", "Boolean"}),
        "array": frozenset(name for name, given in GIVEN_CONTAINERS.items() if given.slot in ITEM_CONTAINERS.slots),
        "object": frozenset({
            *(name for name, given in GIVEN_CONTAINERS.items() if given.slot in MAPPINGS.slots),
            "TypedDict",
        }),
    }

    # Types that require x-python-type override regardless of schema type
    PYTHON_TYPE_OVERRIDE_ALWAYS: ClassVar[frozenset[str]] = frozenset({
        "Callable",
        "Type",
        # collections types that have no JSON Schema equivalent
        "defaultdict",
        "OrderedDict",
        "Counter",
        "deque",
        "ChainMap",
    })

    _config_class_name: ClassVar[str] = "JSONSchemaParserConfig"

    def __init__(  # noqa: PLR0915
        self,
        source: str | Path | list[Path] | ParseResult,
        *,
        config: JSONSchemaParserConfig | None = None,
        **options: Unpack[JSONSchemaParserConfigDict],
    ) -> None:
        """Initialize the JSON Schema parser with configuration options."""
        if config is None and options.get("target_datetime_class") is None:
            options["target_datetime_class"] = DatetimeClassType.Awaredatetime
        super().__init__(source=source, config=config, **options)

        self._python_type_expressions: Mapping[str, PythonTypeExpr] | None = None
        self.remote_object_cache: DefaultPutDict[str, dict[str, YamlValue]] = DefaultPutDict()
        self.raw_obj: dict[str, YamlValue] = {}
        self._all_of_root_value_ref_stack: set[tuple[_TargetNode, ...]] | None = None
        self._root_pattern_string_constraints: JsonSchemaObject | None = None
        self._init_schema_resources()
        self._pattern_validation_document_root: JsonSchemaObject | None = None
        self._root_id: Optional[str] = None  # noqa: UP045
        self._root_id_base_path: Optional[str] = None  # noqa: UP045

        self._external_ref_mapping: dict[str, str] = {
            _external_ref_mapping_key(file_path, self.base_path): python_package
            for file_path, python_package in (self._source_context.external_ref_mapping or {}).items()
        }
        self.reserved_refs: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
        self._loads_only_used_references = False
        self._definitions_without_model: dict[str, Reference] = {}
        self._mapped_definition_imports: dict[str, Import] = {}
        self._mapped_references: dict[str, tuple[str, str]] = {}
        self._python_import_values: dict[str, YamlValue] = {}
        self._dangling_refs: set[tuple[str, str]] = set()
        self._unescaped_slash_pointers: dict[tuple[str, str], str] = {}
        """Under --read-escaped-ref-pointers, the pointer of the key a dangling reference's unescaped ``/`` splits."""
        self._empty_enum_locations: set[SchemaLocation] = set()
        self._unread_patterns: dict[SchemaLocation, str] = {}
        self._documents_as_written: dict[int, tuple[dict[str, YamlValue], dict[str, YamlValue]]] = {}
        self._held_value: tuple[YamlValue, SchemaLocation] | None = None
        self._inexact_object_maps: set[str] = set()
        self._closed_pattern_properties: set[str] = set()
        self._dynamic_anchor_index: dict[tuple[str, ...], dict[str, str]] = {}
        self._recursive_anchor_index: dict[tuple[str, ...], list[str]] = {}
        self._ref_data_type_facts: dict[str, tuple[Any, bool]] = {}
        self._python_imports: tuple[dict[str, Import], set[str], list[str]] | None = None
        self._generic_python_import_expressions: dict[str, PythonTypeExpr] | None = None
        self._false_schema_refs: set[str] | None = None
        self._inherited_schema_cache: dict[str, JsonSchemaObject] = {}
        self._inherited_schema_ancestor_cache: dict[str, frozenset[str]] = {}
        self._inherited_schema_linearization_cache: dict[tuple[str, ...], tuple[str, ...]] = {}
        self._inherited_required_cache: dict[tuple[str, ...], frozenset[str]] = {}
        self._ref_target_dumps: dict[tuple[_TargetNode, str], tuple[dict[str, Any], str]] = {}
        self._unsatisfiable_schemas: set[tuple[str, tuple[str, ...]]] = set()
        self._untaken_pattern_schemas: set[tuple[str, tuple[str, ...]]] = set()
        self._inherited_parent_property_cache: dict[
            tuple[str | int, str, frozenset[_TargetNode]],
            tuple[
                JsonSchemaObject,
                tuple[JsonSchemaObject, str, frozenset[_TargetNode]],
            ],
        ] = {}
        self._raw_inherited_fields_cache: dict[str, tuple[DataModelFieldBase, ...]] = {}
        self._raw_inherited_own_names_cache: dict[str, frozenset[str]] = {}
        self._request_response_fields: dict[str, tuple[DataModelFieldBase, ...]] = {}
        self._rw_model_field_facts_cache: dict[str, tuple[bool, bool, bool, bool]] = {}
        self._rw_model_field_facts_in_progress: set[str] = set()
        self._rw_model_references_cache: dict[str, tuple[bool, tuple[str, ...]]] = {}
        self._rw_model_variant_requirement_cache: dict[tuple[str, str], bool] = {}
        self._rw_model_variant_references: dict[tuple[str, str], Reference] = {}
        self._local_ref_path_cache: dict[Path, Path] = {}
        self._tag_key_variants: dict[SchemaLocation | None, tuple[str, str]] = {}
        if self.generate_schema_validators:
            self._property_count_rule_cache: dict[int, tuple[JsonSchemaObject, PropertyCountRule | None]] = {}
        self.field_keys: set[str] = {
            *DEFAULT_FIELD_KEYS,
            *self.field_extra_keys,
            *self.field_extra_keys_without_x_prefix,
        }
        self._circular_ref_cache: dict[str, bool] = {}
        self._unsatisfiable_const_locations: set[str] = set()

        if self.data_model_field_type.can_have_extra_keys:
            self.get_field_extra_key: Callable[[str], str] = lambda key: (
                self.model_resolver.get_valid_field_name_and_alias(key, model_type=self.field_name_model_type)[0]
            )

        else:
            self.get_field_extra_key = lambda key: key

    @classmethod
    def _from_python_type_expressions(
        cls,
        source: dict[str, YamlValue],
        python_type_expressions: Mapping[str, PythonTypeExpr],
        *,
        config: JSONSchemaParserConfig,
    ) -> JsonSchemaParser:
        """Preserve the historical private token-table construction path."""
        parser = cls(source=cast("str | Path | list[Path] | ParseResult", source), config=config)
        parser._python_type_expressions = python_type_expressions
        return parser

    def _externalize_schema_extra(self, key: str, value: Any) -> Any:
        """Render neutral type IR only when it crosses into generated metadata."""
        if key != "x-python-type":
            return value
        from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415
        from datamodel_code_generator.input_model_result import PythonTypeSchemaAnnotation  # noqa: PLC0415

        if isinstance(value, PythonTypeSchemaAnnotation):
            return render_python_type_expr(value.expression)
        if (
            isinstance(value, str)
            and self._python_type_expressions is not None
            and (expression := self._python_type_expressions.get(value)) is not None
        ):
            return render_python_type_expr(expression)
        return value

    def get_field_extras(self, obj: JsonSchemaObject) -> dict[str, Any]:
        """Extract extra field metadata from a JSON Schema object."""
        extras = {
            self.get_field_extra_key(
                _without_x_prefix(k) if k in self.field_extra_keys_without_x_prefix else k
            ): self._externalize_schema_extra(k, v)
            for k, v in obj.extras.items()
            if self.field_include_all_keys or k in self.field_keys
        }
        if isinstance(discriminator := extras.get("discriminator"), dict) and isinstance(
            mapping := discriminator.get("mapping"), dict
        ):
            refs = _union_tags.discriminator_mapping_refs(self, mapping)
            extras["discriminator"] = {
                **discriminator,
                "mapping": _union_tags.located_mapping_paths(self, mapping, refs),
            }
        if self.default_field_extras:
            extras.update(self.default_field_extras)
        if (default_factory := extras.get("default_factory", UNDEFINED)) is not UNDEFINED:
            extras["default_factory"] = _validate_default_factory(default_factory)
        return extras

    @cached_property
    def _data_formats(self) -> dict[str, dict[str, Types]]:
        """Get data format mappings for this parser type.

        Returns all formats for backward compatibility.
        OpenAPI-specific formats will be separated in Strict mode (future).
        """
        return json_schema_data_formats

    def _get_type_with_mappings(self, type_: str, format_: str | None = None) -> Types:
        """Get the Types enum for a given type and format, applying custom type mappings.

        Custom mappings from --type-mappings are checked first, then falls back to
        the parser's data format mappings.
        """
        data_formats = self._data_formats
        if self.type_mappings and format_ is not None and (type_, format_) in self.type_mappings:
            target_format = self.type_mappings[type_, format_]
            for type_formats in data_formats.values():
                if target_format in type_formats:
                    return type_formats[target_format]
            if target_format in data_formats:
                return data_formats[target_format]["default"]

        return _get_type(type_, format_, data_formats)

    def _get_schema_type(self, obj: JsonSchemaObject, type_: str, format_: str) -> Types:
        """Get the Types enum for a type and format of ``obj``, applying custom type mappings.

        Under --use-bytes-for-content-media-type a string of plain text format that holds raw octets for its content
        media type (``_holds_content_media_type_octets``) is bytes, as a ``format: binary`` string is. Row
        ``legacy-content-media-type-string-is-text``: without it the string is ``str``.
        """
        types = self._get_type_with_mappings(type_, format_)
        if (
            self.use_bytes_for_content_media_type
            and types == Types.string
            and self._holds_content_media_type_octets(obj, type_)
        ):
            return Types.binary
        return types

    def _get_format_data_type(self, obj: JsonSchemaObject, type_: str) -> DataType:
        """Map a type and the format of ``obj`` without constraints."""
        format_ = obj.format or "default"
        types = self._get_schema_type(obj, type_, format_)
        data_type = self.data_type_manager.get_data_type(types, **self._get_format_type_kwargs(type_, format_, types))
        return self._with_binary_content(data_type, obj, type_, format_, types)

    def _with_binary_content(
        self, data_type: DataType, obj: JsonSchemaObject, type_: str, format_: str, types: Types
    ) -> DataType:
        """Record BINARY_CONTENT on the type of a string of ``obj`` that the specification reads as raw octets.

        A string holds raw octets when its format maps to bytes, unless they are bytes mapped from base64 text, or,
        where a content media type reads so (``_content_media_type_is_octets``), when it names a content media type
        without a content encoding and its format maps to plain text.
        """
        if type_ == "string" and (
            self._format_bytes(types, type_, format_) == "raw"
            if types == Types.binary
            else types == Types.string and self._holds_content_media_type_octets(obj, type_)
        ):
            data_type.__dict__[BINARY_CONTENT_KEY] = True
        return data_type

    def _holds_content_media_type_octets(self, obj: JsonSchemaObject, type_: str) -> bool:
        """Whether a string of ``obj`` names a content media type without a content encoding where that is raw octets.

        It is where the specification reads so (``_content_media_type_is_octets``).
        """
        return (
            type_ == "string"
            and "contentMediaType" in obj.extras
            and "contentEncoding" not in obj.extras
            and self._content_media_type_is_octets
        )

    @property
    def _content_media_type_is_octets(self) -> bool:
        """Whether a string with a content media type and no content encoding holds raw octets: never in JSON Schema.

        A JSON Schema string is text; ``contentMediaType`` only describes it.
        """
        return False

    def _get_format_type_kwargs(self, type_: str, format_: str, types: Types) -> dict[str, bool]:
        """Keep bytes mapped from base64 text decoded, and string time formats out of float strictness."""
        if self._format_bytes(types, type_, format_) == "base64":
            return {"base64_encoded": True}
        if types == Types.time and type_ == "string":
            return {"string_format": True}
        return {}

    def _format_bytes(self, types: Types, type_: str, format_: str) -> Literal["raw", "base64"] | None:
        """Return which bytes the models type a type and format as that the type mapping gives ``types``.

        A format that maps to bytes holds raw bytes, unless they are bytes mapped from base64 text, which the models
        write as base64 text; any other format holds no bytes.
        """
        if types != Types.binary:
            return None
        return "base64" if self._is_base64_encoded_binary_mapping(type_, format_) else "raw"

    def _is_base64_encoded_binary_mapping(self, type_: str, format_: str) -> bool:
        if type_ != "string" or format_ != "byte" or not self.type_mappings:
            return False
        return self.type_mappings.get((type_, format_)) == "binary"

    @cached_property
    def schema_paths(self) -> list[tuple[str, list[str]]]:
        """Get schema paths for definitions and defs.

        For JsonSchema, uses the definitions_key of the schema features to determine
        the primary path, with fallback to the alternative in Lenient mode.
        OpenAPI subclass uses its own SCHEMA_PATHS (#/components/schemas).
        """
        return self._schema_paths_of(None)

    def _schema_paths_at(self, at: SchemaLocation | None) -> list[tuple[str, list[str]]]:
        """Return the schema paths of the document a location is in, None: the document being parsed.

        They are those of its schema version under --read-schema-version-per-document, else ``schema_paths``, of the
        document parsed at the first read (row ``legacy-first-read-dialect``).
        """
        if (dialect := self._dialect_at(at)) is None:
            return self.schema_paths
        return self._schema_paths_of(dialect.features)

    def _schema_paths_of(self, features: JsonSchemaFeatures | None) -> list[tuple[str, list[str]]]:
        """Return the schema paths of a document read with ``features`` (None: ``schema_features``), primary first."""
        # OpenAPI and other subclasses use their own SCHEMA_PATHS
        if list(_DEFAULT_SCHEMA_PATHS) != self.SCHEMA_PATHS:
            return [(s, s.lstrip("#/").split("/")) for s in self.SCHEMA_PATHS]

        # JsonSchema: use definitions_key from the schema features
        primary_key = (features or self.schema_features).definitions_key
        primary_path = f"#/{primary_key}"
        fallback_key = "$defs" if primary_key == "definitions" else "definitions"
        fallback_path = f"#/{fallback_key}"

        # Strict mode: only use version-specific path
        if self.config.schema_version_mode == VersionMode.Strict:
            return [(str(primary_path), [str(primary_key)])]

        # Lenient mode (default): check both paths, primary first
        return [
            (str(primary_path), [str(primary_key)]),
            (str(fallback_path), [str(fallback_key)]),
        ]

    @cached_property
    @override
    def schema_features(self) -> JsonSchemaFeatures:
        """Get schema features based on config or detected version, of the document parsed at the first read."""
        return self._schema_features_for(self.raw_obj)

    def _schema_features_for(self, root: dict[str, YamlValue]) -> JsonSchemaFeatures:
        """Get the schema features of a root document based on config or its detected version."""
        return self._read_dialect(root).features

    def _read_dialect(
        self, raw: dict[str, Any], *, kind: SchemaKind | None = None, inherited: DocumentDialect | None = None
    ) -> DocumentDialect:
        """Return the dialect a document of ``kind`` (the parser's by default) is read by.

        The configured version unless it is ``auto``, else the version the document declares, else that of
        ``inherited``, the dialect of the document that loads it; without one the document is read by itself.
        """
        kind = kind or self._schema_kind
        return document_dialect(
            raw, kind, configured=getattr(self.config, _KIND_READINGS[kind].config_field, None), inherited=inherited
        )

    @cached_property
    def _ref_sibling_keywords_enabled(self) -> bool:
        """Return whether the document parsed at the first read evaluates validation beside ``$ref``.

        Every parser class reads that document as JSON Schema (row ``legacy-first-read-dialect``).
        """
        return self._read_dialect(self.raw_obj, kind="jsonschema").features.ref_sibling_keywords

    @property
    def _ref_siblings_are_parts(self) -> bool:
        """Whether the current document's dialect reads the keywords beside a ``$ref`` as parts of the schema.

        It reads the document being parsed and leaves ``_ref_sibling_keywords_enabled`` alone: that flag is cached on
        the parser at its first read, so for a directory of documents in different dialects the document parsed at
        that read decides it for all of them. Reading it here, earlier than 0.86.0 did, made a draft-07 document
        decide it for the 2020-12 documents after it; the per-document dialect for every read comes later as an
        opt-in.
        """
        return self._read_dialect(self.raw_obj, kind="jsonschema").features.ref_sibling_keywords

    @property
    def _inherited_parent_row(self) -> InheritedParentRow:
        """The order the allOf parents of an inherited schema are read in: their class's under the option.

        Row ``class-bases`` under --read-allof-parents-in-mro-order, else row ``legacy-document-order``
        (``_inherited_schemas.inherited_schema_parents``).
        """
        return "class-bases" if self.config.read_allof_parents_in_mro_order else "legacy-document-order"

    @property
    def _pointer_slash_names(self) -> SlashNames:
        """How a JSON pointer reads a ``/`` unescaped inside a name: as a separator under --read-escaped-ref-pointers.

        Row ``uri`` joins the parts only into a name that holds ``://``; without the option, row ``joined``, into any
        key the object holds (``SlashNames``).
        """
        return "uri" if self.config.read_escaped_ref_pointers else "joined"

    @cached_property
    def _reads_schema_version_per_document(self) -> bool:
        """Whether --read-schema-version-per-document reads each schema in the version of its own document.

        A parser class from outside this package whose ``schema_features`` decides the version keeps that reading for
        every document, with a warning, as the option would bypass it.
        """
        if not self.config.read_schema_version_per_document:
            return False
        owner = next(cls for cls in type(self).__mro__ if "schema_features" in cls.__dict__)
        if owner.__module__.startswith("datamodel_code_generator."):
            return True
        warn(
            f"--read-schema-version-per-document has no effect with {type(self).__name__}: "
            f"{owner.__name__}.schema_features decides the schema version of every document",
            stacklevel=2,
        )
        return False

    def _dialect_at(self, at: JsonSchemaObject | SchemaLocation | None) -> DocumentDialect | None:
        """Return the dialect of the document a schema object or location is in, None: the document being parsed.

        It is read so under --read-schema-version-per-document only; without the option it is None, and the readers
        keep their readings (row ``legacy-first-read-dialect``). A document no load recorded reads as the document
        being parsed does by itself.
        """
        if not self._reads_schema_version_per_document:
            return None
        location = at.schema_location if isinstance(at, JsonSchemaObject) else at
        parts = [location.document] if location is not None else list(self.model_resolver.current_root)
        return self._document_dialects.get(
            _schema_documents.schema_resource_document(self, parts)
        ) or self._read_dialect(self.raw_obj)

    def _features_at(self, at: JsonSchemaObject | SchemaLocation | None) -> JsonSchemaFeatures:
        """Return the schema features that read a schema object, or the schema value at a location.

        None reads the document being parsed. Under --read-schema-version-per-document they are those of the version
        of its document; row ``legacy-first-read-dialect``: without it they are the features of the document parsed at
        their first read (``schema_features``), for every document of the parse.
        """
        if (dialect := self._dialect_at(at)) is not None:
            return dialect.features
        return self.schema_features

    def _ref_siblings_apply_to(
        self,
        schema: JsonSchemaObject | SchemaLocation | None,
        *,
        legacy: Literal["first-read", "parsed-document", "as-written"],
    ) -> bool:
        """Return whether the keywords beside the ``$ref`` of ``schema`` apply with it, as its version reads them.

        ``schema`` is a schema object or the location of one.

        Under --read-schema-version-per-document the version is that of its document. Without it, row
        ``legacy-first-read-dialect`` keeps the reading each site had: ``first-read``, the JSON Schema draft of the
        document parsed at the first read, for every parser class (``_ref_sibling_keywords_enabled``);
        ``parsed-document``, the version of the input document being parsed (``_ref_siblings_are_parts``), also for
        the schemas of a document that a reference loads; and row ``legacy-count-ref-member-as-written``:
        ``as-written``, every version.
        """
        if (dialect := self._dialect_at(schema)) is not None:
            return dialect.features.ref_sibling_keywords
        if legacy == "first-read":
            return self._ref_sibling_keywords_enabled
        return legacy == "as-written" or self._ref_siblings_are_parts

    def _ref_siblings_are_parts_of(self, schema: JsonSchemaObject) -> bool:
        """Return whether the keywords beside the ``$ref`` of ``schema`` are parts of it.

        They are in a dialect that reads them so, and in every dialect where an inherited property override wrote
        them beside the inherited ``$ref`` (marked ``_INHERITED_OVERRIDE_KEY``), since an override is an allOf part, or
        where they are parts of a conjunction the document writes around it (marked ``_CONJUNCTION_PARTS_KEY``).
        """
        return (
            self._ref_siblings_apply_to(schema, legacy="parsed-document")
            or _INHERITED_OVERRIDE_KEY in schema.__dict__
            or _CONJUNCTION_PARTS_KEY in schema.__dict__
        )

    @property
    def root_id(self) -> str | None:
        """Get the root $id from the model resolver."""
        return self.model_resolver.root_id

    @root_id.setter
    def root_id(self, value: str | None) -> None:
        """Set the root $id in the model resolver."""
        self.model_resolver.set_root_id(value)

    def should_parse_enum_as_literal(
        self,
        obj: JsonSchemaObject,
        property_name: str | None = None,
        property_obj: JsonSchemaObject | None = None,
    ) -> bool:
        """Determine if an enum should be parsed as a literal type (``enum_as_literal``).

        Priority (highest to lowest):
        1. x-enum-field-as-literal on the property schema
        2. enum_field_as_literal_map matching Model.field or field
        3. Global enum_field_as_literal setting
        """
        return _value_keywords.should_parse_enum_as_literal(self, obj, property_name, property_obj)

    def _parse_enum_class(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        *,
        property_obj: JsonSchemaObject | None = None,
        singular_name: bool = False,
    ) -> DataType:
        """Parse an enum that ``should_parse_enum_as_literal`` leaves a class, as ``parse_enum`` does.

        The classes it builds are remembered when ``--enum-field-as-literal all`` would write the enum as a Literal,
        so that the warnings about enums msgspec cannot decode recommend that option for them only.
        """
        built = len(self.results)
        data_type = self.parse_enum(name, obj, path, singular_name=singular_name)
        if _value_keywords.enum_as_literal_under(self, "all", obj, name, property_obj):
            self._literal_under_all_enum_reference_ids.update(
                model_key_of(model.reference) for model in self.results[built:] if isinstance(model, Enum)
            )
        return data_type

    def _admitted_or_warn(
        self, obj: JsonSchemaObject, met: tuple[bool, ...], kept: tuple[bool, ...], keywords: tuple[str, ...]
    ) -> tuple[bool, ...] | None:
        """Return whether the field keeps each value (``kept``), None when it keeps all.

        When no value meets every constraint the check decides (``met``), the schema admits no value: the
        unsatisfiable warning names the ``keywords`` and the values stay as written (None).
        """
        if not any(met):
            self._warn_unsatisfiable(
                named_location(obj.schema_location, "schema"),
                keywords,
                _LITERAL_PARTS,
            )
            return None
        return None if all(kept) else kept

    def _literal_view(self, obj: JsonSchemaObject, written: frozenset[str] = NO_KEYWORDS) -> JsonSchemaObject:
        """Return an enum schema with only the values that meet the constraints its field leaves out (not ``written``).

        The values a const beside the enum leaves decide whether the schema admits any value; the schema itself when
        every value meets them or none is admitted (``_admitted_or_warn``). The copy keeps the values as written.
        Without --filter-literal-values the schema itself.
        """
        if not self.checks_literal_values or not _literal_constraints(obj):
            return obj
        decided, met, kept = _literal_met(obj, obj.enum, written)
        if excluded := self._enum_positions_outside_const(obj):
            decided, met, _ = _literal_met(
                obj, [value for position, value in enumerate(obj.enum) if position not in excluded], NO_KEYWORDS
            )
        if (admitted := self._admitted_or_warn(obj, met, kept, ("const" if excluded else "enum", *decided))) is None:
            return obj
        return _keep_values_as_written(
            _enum_without_positions(obj, {position for position, keeps in enumerate(admitted) if not keeps}), obj.enum
        )

    def _enum_literal(self, obj: JsonSchemaObject, written: frozenset[str] = NO_KEYWORDS) -> DataType:
        """Return the Literal type of the enum values that meet the constraints its field leaves out.

        A type of only some of the values (``_literal_view``) keeps them as written.
        """
        return _value_keywords.enum_literal(self, obj, written)

    def is_constraints_field(self, obj: JsonSchemaObject, data_type: DataType | None = None) -> bool:
        """Check if a field should include constraints; ``data_type`` is the type the field holds, if built."""
        return _value_keywords.is_constraints_field(self, obj, data_type)

    def _has_effective_constraints(self, obj: JsonSchemaObject) -> bool:
        """Return whether direct or derived constraints require a generated field."""
        return _value_keywords.has_effective_constraints(self, obj)

    def _split_constraint_values(
        self, obj: JsonSchemaObject, data_type: DataType | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the constraint values of the type-dependent keywords that a field takes, and those it leaves out.

        The keywords of a kind a declared schema type holds (number, string or array) follow ``_keywords_taken``;
        the emitted Python type decides the others. The left-out ones are the declared keywords the type does not
        take, to warn about, ``minItems`` and ``maxItems`` of a declared string as the lengths they are written as.
        """
        return _value_keywords.split_constraint_values(self, obj, data_type)

    def _is_fixed_length_tuple(self, obj: JsonSchemaObject) -> bool:
        """Check if an array field represents a fixed-length tuple."""
        return _value_keywords.is_fixed_length_tuple(self, obj)

    def _get_fixed_length_homogeneous_tuple_length(self, obj: JsonSchemaObject) -> int | None:
        """Return the length of an opted-in homogeneous fixed-length tuple."""
        return _value_keywords.get_fixed_length_homogeneous_tuple_length(self, obj)

    def _get_array_item_schemas(
        self,
        obj: JsonSchemaObject,
        *,
        include_true_tail_schema: bool = False,
        force_prefix_items: bool = False,
    ) -> tuple[list[JsonSchemaObject | bool], bool, bool, int | None]:
        """Return item schemas plus tuple/constraint flags and homogeneous tuple length."""
        return _value_keywords.get_array_item_schemas(
            self, obj, include_true_tail_schema=include_true_tail_schema, force_prefix_items=force_prefix_items
        )

    @staticmethod
    def _property_names_forbids_all_keys(property_names: JsonSchemaObject | bool | None) -> bool:  # noqa: FBT001
        """Return whether a propertyNames schema rejects every JSON object key."""
        if property_names is False:
            return True
        if not isinstance(property_names, JsonSchemaObject):
            return False
        return (
            bool(property_names.enum and not any(isinstance(value, str) for value in property_names.enum))
            or ("const" in property_names.extras and not isinstance(property_names.extras["const"], str))
            or (property_names.type is not None and "string" not in declared_types(property_names.type))
        )

    @classmethod
    def _get_array_max_items_constraints(cls, obj: JsonSchemaObject) -> list[int]:
        return _value_keywords.get_array_max_items_constraints(cls, obj)

    @classmethod
    def _get_array_items_constraints(cls, obj: JsonSchemaObject) -> dict[str, int]:
        """Return array length constraints derived from boolean items."""
        # Only these fields can add derived array-length constraints: items,
        # additionalItems, prefixItems, unevaluatedItems, and contains-related
        # extras. Keep explicit null or empty fields on the conventional path,
        # where subclasses can retain their existing semantics.
        if (
            cls is JsonSchemaParser
            and type(obj) is JsonSchemaObject
            and not obj.extras
            and obj.model_fields_set.isdisjoint(_ARRAY_ITEMS_CONSTRAINT_FIELDS)
        ):
            return {}

        min_items: list[int] = []
        max_items = cls._get_array_max_items_constraints(obj)
        if _value_keywords.contains_matches_every_item(cls, obj):
            contains_min_items, contains_max_items = _value_keywords.get_contains_count_constraints(cls, obj)
            if contains_min_items is not None:
                min_items.append(contains_min_items)
            if contains_max_items is not None:
                max_items.append(contains_max_items)
        elif _value_keywords.contains_false_requires_match(cls, obj):
            min_items.append(1)
            max_items.append(0)

        constraints: dict[str, int] = {}
        if min_items:
            if obj.minItems is not None:
                min_items.append(obj.minItems)
            constraints["minItems"] = max(min_items)
        if max_items:
            if obj.maxItems is not None:
                max_items.append(obj.maxItems)
            constraints["maxItems"] = min(max_items)
        return constraints

    def _get_fixed_positional_items(
        self,
        obj: JsonSchemaObject,
        canonical_ref: Callable[[str], str] | None = None,
    ) -> list[JsonSchemaObject | bool] | None:
        """Return the positions of a positional array written as a tuple, or None when it is written as a list.

        Equal explicit ``minItems`` and ``maxItems`` make a tuple. Otherwise the effective length bounds must fix the
        length, and the tuple is written only where the list form would lose positions: a list is kept when its items
        and every allowed position accept the same values, so the list and its length constraints are exact.
        ``canonical_ref`` reads the ``$ref`` values of ``obj``; by default they belong to the document being parsed.
        Without ``--use-tuple-for-closed-arrays`` only equal explicit ``minItems`` and ``maxItems`` make a tuple.
        """
        return _value_keywords.get_fixed_positional_items(self, obj, canonical_ref)

    def _get_data_type_from_json_value(self, value: object) -> DataType:
        """Infer a normal Python type from a JSON value."""
        return _value_keywords.get_data_type_from_json_value(self, value)

    def _get_const_data_type(self, const: object, obj: JsonSchemaObject | None = None) -> DataType:
        """Return a DataType for a JSON Schema const value: its Literal (``const_type``) or its value's type.

        With the schema ``obj``, a Literal const that does not meet the constraints beside it is kept as written, with
        the unsatisfiable warning. A value type keeps the const it is written in place of (``_widened``).
        """
        return self._widened(_value_keywords.get_const_data_type(self, const, obj), (const,))

    def _widened(self, data_type: DataType, values: Sequence[object]) -> DataType:
        """Return the type of enum or const values, keeping on it those it is written in place of, if any.

        They are the values no Literal of the output holds (``values_no_literal_holds``), whose types admit others.
        """
        if widened := values_no_literal_holds(
            values, bool_literals=self._output_model_context.supports_boolean_literals
        ):
            self._graph_builder.record_widened_values(data_type, widened)
        return data_type

    def _collect_inherited_fields_for_request_response(
        self,
        base_classes: list[Reference],
        active: frozenset[str] = frozenset(),
    ) -> list[DataModelFieldBase]:
        """Collect canonical inherited fields in declaration order with C3 winners."""
        return _rw_split.collect_inherited_fields_for_request_response(self, base_classes, active)

    def _unregister_temporary_field_references(
        self,
        fields: Iterable[DataModelFieldBase],
    ) -> None:
        """Keep temporary raw fields out of the live reverse-reference graph."""
        return _rw_split.unregister_temporary_field_references(self, fields)

    def _temporary_constraint_values(self, obj: JsonSchemaObject, data_type: DataType | None) -> dict[str, Any]:
        """Return the constraint values ``obj`` writes beside ``data_type``, a type built only to read them."""
        return _rw_split.temporary_constraint_values(self, obj, data_type)

    def _clear_inherited_field_caches(self) -> None:
        """Release temporary inheritance state after parsing."""
        return _rw_split.clear_inherited_field_caches(self)

    def _merge_inherited_field_overrides(
        self,
        inherited_fields: list[DataModelFieldBase],
        fields: list[DataModelFieldBase],
    ) -> list[DataModelFieldBase]:
        """Overlay declarations on inherited fields while resolving deferred partial types."""
        return _rw_split.merge_inherited_field_overrides(self, inherited_fields, fields)

    def _prepare_required_inherited_field(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        overriding_field: DataModelFieldBase | None = None,
    ) -> None:
        """Remove inherited constructor defaults while retaining explicit child metadata."""
        return _rw_split.prepare_required_inherited_field(
            self, field, inherited_field, overriding_field=overriding_field
        )

    def _get_raw_inherited_fields(
        self,
        reference: Reference,
        active: frozenset[str],
    ) -> list[DataModelFieldBase]:
        """Recursively materialize one raw schema's effective fields without live reverse edges."""
        return _rw_split.get_raw_inherited_fields(self, reference, active)

    @cached_property
    def _legacy_rw_variants(self) -> frozenset[str]:
        """The rows of the request/response split kept: every row without --dedupe-request-response-variants.

        ``legacy-rw-parsed-facts``: the read/write facts and graph node of a key come from a temporary parse of its
        fields, the walk reads every reference of a key, and every model collects its inherited fields to decide its
        split. ``legacy-rw-unowned-parent-fields``: the inline models of an allOf parent's own fields are parsed at no
        owner path.
        """
        return _rw_split.legacy_rw_variants(self)

    def _ref_schema_generates_variant(
        self,
        resolved_ref: str,
        suffix: Literal["Request", "Response"],
    ) -> bool:
        """Check whether a referenced schema generates the requested read/write variant."""
        return _rw_split.ref_schema_generates_variant(self, resolved_ref, suffix)

    def _request_response_ref_schema_generates_variant(
        self,
        resolved_ref: str,
    ) -> bool:
        """Resolve Request/Response variant reachability once per graph node."""
        return _rw_split.request_response_ref_schema_generates_variant(self, resolved_ref)

    def _get_rw_model_variant_graph_node(self, resolved_ref: str) -> tuple[bool, tuple[str, ...]]:
        """Return whether the raw schema at a key declares a flagged property, and else the keys it references.

        A special path has no raw schema of its own: its split is the one recorded when its model is parsed. Under
        ``legacy-rw-parsed-facts``, the 0.86.0 node: the facts of a temporary parse of the key also split it,
        and the models its parsed fields reference are edges too.
        """
        return _rw_split.get_rw_model_variant_graph_node(self, resolved_ref)

    @cached_property
    def _writes_extra_items_type(self) -> bool:
        """Whether TypedDict writes the type of additional properties as extra_items, under --use-closed-typed-dict.

        That type is the lightweight type of additionalProperties (``_build_lightweight_type``), which names models
        and generates none.
        """
        return _rw_split.writes_extra_items_type(self)

    def _get_ref_schema_rw_model_reference_facts(
        self,
        resolved_ref: str,
    ) -> tuple[bool, tuple[str, ...]]:
        """Return whether the raw schema at a key declares a flagged property, and else the keys its references name.

        The walk asks the dispatch tables what the model of the key types (``_DEFINITION_TABLE``,
        ``_typed_model_children``) and follows what that types in turn. It stops at the first flagged property: the
        references of a node that needs variants are never read (under ``legacy-rw-parsed-facts`` it reads them all,
        as 0.86.0 did). A key that names no loadable schema has neither.
        """
        return _rw_split.get_ref_schema_rw_model_reference_facts(self, resolved_ref)

    def _typed_model_children(self, site: _Site) -> Iterator[TypedChild[JsonSchemaObject]]:  # noqa: PLR0912
        """Return what the model built from a schema types: the children and keys of the definition table's sites.

        ``site.path`` holds the key the schema is defined under; with a ``parent``, the site is an inline allOf member
        merged into the model built from ``parent``. A schema is read as the one its model is built from
        (``_merged_ref_model_schemas``), and its properties, its references and its children all from that schema.
        patternProperties and propertyNames type the values and keys of an object map, propertyNames only where no
        patternProperties give the keys; a schema with properties or allOf, and each inline allOf member merged into it,
        builds a model whose fields hold properties alone, so its map keywords type nothing. Under
        --generate-schema-validators the properties and the patternProperties its rules name are children (not a value
        that is false written as an allOf, ``_is_false_all_of``: the rules forbid its keys), and so are the conditional
        branches. The item positions of an array are those array parsing types (``_get_array_item_schemas``), not the
        entries it leaves untyped. A model types the additionalProperties its builder types: those the patternProperties
        rules pick (``_pattern_rule_additional_properties``), as its values where it is a map
        (``_schema_builds_map_model``) except where patternProperties give the values and ``_other_keys_schema`` types
        no other key, and in its typed extra field otherwise (``_typed_extra_field_schema``); those of an inline allOf
        member type something only where the allOf merges into a map. TypedDict also records those of the key's model
        and of each inline model with fields, unless an inline allOf member closes it
        (``_model_additional_properties``), as their extra_items type (``set_additional_properties``); that type
        generates no model, so the site names the keys of the models it names (``_extra_items_type``, row
        ``legacy-rw-extra-items-as-written``: as written, its false allOf values not read). Each property, item, value
        and branch is a field site, typed as the row ``parse_item`` chooses for it declares (``typed_field_site``): a
        property reads a $ref beside sibling keywords merged with them before the table chooses, the other sites
        after its python-type row. A value is the site ``_parse_additional_properties_value`` builds
        (``_typed_additional_properties_value``) and a oneOf or anyOf member the branch ``parse_combined_schema``
        parses (``_typed_combined_members``).
        """
        schema, defining_ref, into = site.obj, site.path[0], site.parent
        if into is None:
            is_key_model = schema is self._load_inherited_schema_object(defining_ref)
            schema, model_schema = map(
                self._read_false_all_of_values, _rw_split.merged_ref_model_schemas(self, schema, defining_ref)
            )
            into = model_schema
            builds_map = (
                self._root_type_row(model_schema) in _ROOT_MAP_ROWS
                if site.root
                else _rw_split.schema_builds_map_model(self, model_schema, defining_ref)
            )
            types_map = not (model_schema.allOf or (model_schema.properties and not site.root))
            extras: JsonSchemaObject | bool | None = schema.additionalProperties
            with self._inherited_ref_context(defining_ref):
                if self._has_pattern_properties_validator(model_schema):
                    rule = _schema_validators.pattern_rule_additional_properties(self, model_schema)
                    extras = None if rule is None else extras if rule[1] is model_schema else rule[2]
                elif (
                    (site.root or _object_maps.typed_extra_field_schema(self, model_schema) is None)
                    if not builds_map
                    else model_schema.patternProperties and self._other_keys_schema(model_schema) is None
                ):
                    extras = None
            if (
                (is_key_model or not builds_map)
                and self._writes_extra_items_type
                and isinstance(extra_items := self._model_additional_properties(model_schema), JsonSchemaObject)
            ):
                with self._inherited_ref_context(defining_ref), quiet():
                    keys = _rw_split.lightweight_type_keys(
                        self, self._extra_items_type(extra_items, reads_false_all_of=False)
                    )
                yield from (TypedChild("key", reference=key) for key in keys)
        else:
            model_schema = schema
            builds_map = _rw_split.schema_builds_map_model(self, into, defining_ref)
            types_map = False
            extras = schema.additionalProperties if builds_map else None
        if not site.root:
            yield TypedChild("fields", schema)
        yield from (
            TypedChild("ref", reference=ref) for ref in (schema.ref, schema.recursiveRef, schema.dynamicRef) if ref
        )
        if types_map and not model_schema.patternProperties:
            property_name_refs, property_name_schemas = self._get_rw_property_name_sources(
                property_names=schema.propertyNames, defining_ref=defining_ref
            )
            yield from (TypedChild("ref", reference=ref) for ref in property_name_refs)
            yield from (TypedChild("model", item) for item in property_name_schemas)
        pattern_properties = schema.patternProperties
        if self.generate_schema_validators and not types_map and pattern_properties:
            pattern_properties = {
                pattern: value for pattern, value in pattern_properties.items() if not _is_false_all_of(value)
            }
        for mapping, merges_ref_first in (
            ((schema.properties, True), (pattern_properties, False))
            if self.generate_schema_validators
            else ((pattern_properties, False),)
            if types_map
            else ((schema.properties, True),)
        ):
            for item in (mapping or {}).values():
                yield from _rw_split.typed_field_site(self, item, defining_ref, merges_ref_first=merges_ref_first)
        if schema.is_array:
            with self._inherited_ref_context(defining_ref):
                positions = {id(item): item for item in self._get_array_item_schemas(schema)[0]}
            for item in positions.values():
                yield from _rw_split.typed_field_site(self, item, defining_ref, parent=schema)
        yield from self._typed_additional_properties_value(extras, defining_ref, schema)
        yield from self._typed_combined_members(schema, defining_ref)
        if site.root and self._root_type_row(schema, row="legacy-rw-allof-before-custom-type") == "all-of":
            with self._inherited_ref_context(defining_ref), quiet():
                keys = _rw_split.lightweight_type_keys(self, self._build_lightweight_type(schema))
            yield from (TypedChild("key", reference=key) for key in keys)
        else:
            yield from (
                TypedChild("member", item, into=into)
                for item in _inherited_schemas.all_of_members(self, schema)
                if isinstance(item, JsonSchemaObject)
            )
        if self.generate_schema_validators:
            yield from (
                TypedChild("model", item) for item in _schema_validators.iter_conditional_branches(self, schema)
            )

    def _typed_combined_members(
        self, schema: JsonSchemaObject, defining_ref: str
    ) -> Iterator[TypedChild[JsonSchemaObject]]:
        """Return what the oneOf and anyOf members of a schema type, as ``parse_combined_schema`` parses them.

        The members are the branches it parses (``_combined_schema_members``), each mixed with the keywords of the
        schema, which is their parent as it is at ``parse_item``.
        """
        for keyword in ("oneOf", "anyOf"):
            if getattr(schema, keyword):
                with self._inherited_ref_context(defining_ref):
                    members, _ = self._combined_schema_members(schema, keyword)
                for member in members:
                    yield from _rw_split.typed_field_site(self, member, defining_ref, parent=schema)

    def _typed_additional_properties_value(
        self,
        value: JsonSchemaObject | bool | None,  # noqa: FBT001
        defining_ref: str,
        parent: JsonSchemaObject,
    ) -> Iterable[TypedChild[JsonSchemaObject]]:
        """Return what the value of an additionalProperties types, as ``_parse_additional_properties_value`` builds it.

        The decision is ``_additional_properties_value``: a value built as a constrained root model types that root
        model, any other the field site ``parse_item`` gets with its parent.
        """
        if not isinstance(value, JsonSchemaObject):
            return ()
        with self._inherited_ref_context(defining_ref):
            built = self._additional_properties_value("", value, parent)
            if built.constrained and self._constrained_value_row(built.schema) == "root":
                return (TypedChild("root", built.schema),)
        return _rw_split.typed_field_site(self, built.schema, defining_ref, parent=built.parent)

    def _resolve_rw_model_reference(self, ref: str, defining_ref: str) -> str | None:
        """Resolve a type-producing ref while keeping configured imports as graph leaves."""
        return _rw_split.resolve_rw_model_reference(self, ref, defining_ref)

    def _get_rw_property_name_sources(
        self,
        *,
        property_names: JsonSchemaObject | bool | None,
        defining_ref: str,
    ) -> tuple[tuple[str, ...], tuple[JsonSchemaObject, ...]]:
        """Mirror propertyNames key parsing without following ignored nested shapes."""
        return _rw_split.get_rw_property_name_sources(self, property_names=property_names, defining_ref=defining_ref)

    def _preload_property_refs_for_rw_models(self, obj: JsonSchemaObject) -> None:
        """Preload property refs needed for readOnly/writeOnly model splitting."""
        return _rw_split.preload_property_refs_for_rw_models(self, obj)

    def _get_rw_model_variant_reference(
        self,
        base_reference: Reference,
        suffix: Literal["Request", "Response"],
        *,
        loaded: bool = False,
    ) -> Reference:
        """Create a variant reference, reserving request/response paths for recursive uses.

        A variant asked for as loaded is built: while L1 records naming frames, it stands for its base in them.
        """
        reference = _rw_split.get_rw_model_variant_reference(self, base_reference, suffix, loaded=loaded)
        if loaded and (frames := self._naming_frames) is not None:
            frames.variant(reference, base_reference, suffix)
        return reference

    def _update_variant_additional_properties_metadata(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        suffix: Literal["Request", "Response"],
    ) -> None:
        """Rewrite TypedDict extra-item metadata to the matching model variant."""
        metadata = self.extra_template_data[reference_path]
        if (
            not self._output_model_context.has_additional_properties_type(metadata)
            or not isinstance(obj.additionalProperties, JsonSchemaObject)
            or (additional_type := self._build_lightweight_type(obj.additionalProperties)) is None
        ):
            return
        _rw_split.update_data_type_ref_for_variant(self, additional_type, suffix)
        _rw_split.store_extra_items_type(self, metadata, additional_type, reference_classes_required=True)
        _rw_split.unregister_temporary_type(self, additional_type)

    def _copy_schema_runtime_validation_for_variant(  # noqa: PLR0913
        self,
        source_path: str,
        target_path: str,
        fields: Sequence[DataModelFieldBase],
        suffix: Literal["Request", "Response"],
        *,
        obj: JsonSchemaObject,
        source_fields: Sequence[DataModelFieldBase] = (),
        is_root_model: bool = False,
    ) -> None:
        """Copy schema runtime rules and retarget their model references."""
        source = self.extra_template_data[source_path].get("schema_runtime_validation")
        if not _is_internal_schema_runtime_validation(source) or not source:
            return

        available_names = {name for field in fields for name in self._field_input_names(field)}
        declared_names = {name for field in source_fields for name in self._field_input_names(field)}

        def keep_names(input_names: tuple[str, ...]) -> bool:
            return bool(available_names.intersection(input_names)) or not declared_names.intersection(input_names)

        def filter_groups(
            groups: tuple[tuple[tuple[str, ...], ...], ...],
        ) -> tuple[tuple[tuple[str, ...], ...], ...]:
            return tuple(tuple(input_names for input_names in group if keep_names(input_names)) for group in groups)

        def retarget(data_type: DataType) -> DataType:
            copied_type = self._copy_model_type(data_type)
            _rw_split.update_data_type_ref_for_variant(self, copied_type, suffix)
            return copied_type

        pattern_properties: list[PatternPropertiesRule] = [
            type(rule)(
                declared_properties=tuple(name for name in rule.declared_properties if name in available_names),
                pattern_properties=tuple(
                    (pattern, retarget(data_type)) for pattern, data_type in rule.pattern_properties
                ),
                rejected_patterns=rule.rejected_patterns,
                additional_property_type=None
                if rule.additional_property_type is None
                else retarget(rule.additional_property_type),
                allow_unmatched=rule.allow_unmatched,
            )
            for rule in source.pattern_properties
        ]

        required_groups: list[RequiredGroupsRule] = []
        for rule in source.required_groups:
            groups = (
                tuple(group for group in rule.groups if all(keep_names(input_names) for input_names in group))
                if rule.keyword == "not"
                else filter_groups(rule.groups)
            )
            if groups:
                required_groups.append(RequiredGroupsRule(keyword=rule.keyword, groups=groups))
        conditional_required = [
            ConditionalRequiredRule(
                condition=rule.condition,
                then_groups=filter_groups(rule.then_groups),
                else_groups=filter_groups(rule.else_groups),
            )
            for rule in source.conditional_required
            if all(keep_names(input_names) for input_names, _ in rule.condition)
        ]

        target = _make_internal_schema_runtime_validation(
            pattern_properties=pattern_properties,
            required_groups=required_groups,
            conditional_required=conditional_required,
            property_count=source.property_count,
            unique_items=[],
        )
        if target:
            self.extra_template_data[target_path]["schema_runtime_validation"] = target
        self._add_unique_items_validator(target_path, obj, fields, [], is_root_model=is_root_model)

    def _generate_forced_base_models(self) -> None:
        """Retain the late parser extension hook used by schema subclasses."""

    def _create_variant_model(  # noqa: PLR0913
        self,
        base_reference: Reference,
        suffix: Literal["Request", "Response"],
        model_fields: list[DataModelFieldBase],
        obj: JsonSchemaObject,
        data_model_type_class: type[DataModel],
        *,
        source_fields: Sequence[DataModelFieldBase] = (),
    ) -> None:
        """Create a Request or Response model variant."""
        return _rw_split.create_variant_model(
            self, base_reference, suffix, model_fields, obj, data_model_type_class, source_fields=source_fields
        )

    def get_object_field(  # noqa: PLR0913
        self,
        *,
        field_name: str | None,
        field: JsonSchemaObject | None,
        required: bool,
        field_type: DataType,
        alias: str | list[str] | None,
        original_field_name: str | None,
        effective_default: Any = None,
        effective_has_default: bool | None = None,
        use_default_with_required: bool = False,
        class_name: str | None = None,
        output_names: tuple[str, ...] = (),
    ) -> DataModelFieldBase:
        """Build an output field using the shared JSON Schema field policy; without a schema, without its keywords.

        Its serialization alias is keyed by ``class_name``, then by the names the class has in the output
        (``output_names``).

        The field, and its type where no nested build recorded one (a const the field types itself), record the
        location of ``field`` as their origin (``reference.field_origin_of``, ``reference.type_origin_of``).
        """
        scope = _class_scope_of(class_name, output_names)
        if field is None:
            model_field = self._build_field(
                "property",
                field_type,
                name=field_name,
                alias=alias,
                source_name=original_field_name,
                scope=scope,
                default=effective_default,
                required=required,
                original_name=original_field_name,
                has_default=effective_has_default is True,
                use_default_with_required=use_default_with_required,
            )
            return with_field_origin(model_field, "property", None)

        default_value = effective_default if effective_has_default is not None else field.default
        has_default = effective_has_default if effective_has_default is not None else field.has_default
        assembled = self._assemble_field_constraints(field, field_type, _PROPERTY_SITE)
        constraints = assembled.constraints
        nullable = self._null_facts(field, "field", required=required, has_default=has_default).admits_null
        extras = self.get_field_extras(field)
        read_only, write_only = _rw_split.resolve_field_flags(self, field)
        model_field = self._build_field(
            "property",
            field_type,
            name=field_name,
            alias=alias,
            source_name=original_field_name,
            scope=scope,
            default=default_value,
            required=required,
            constraints=constraints,
            nullable=nullable,
            extras=extras,
            original_name=original_field_name,
            has_default=has_default,
            type_has_null=self._type_has_null(field),
            read_only=read_only,
            write_only=write_only,
            use_default_with_required=use_default_with_required,
        )
        location = field.schema_location
        with_type_origin(with_field_origin(model_field, "property", location).data_type, location)
        if self.omit_incomplete_model_defaults and has_default and not required:
            self._graph_builder.record_nullable_without_default(
                model_field, self._null_facts(field, "field").admits_null
            )
        if self.model_resolver.default_value_overrides:
            model_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] = (
                field.default if field.has_default else _RAW_SCHEMA_DEFAULT_UNDEFINED
            )
        self._mark_assembled_constraints(model_field, assembled, field, field_type)
        if _RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY in field.__dict__:
            model_field.__dict__[_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY] = field.__dict__[
                _RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY
            ]
        return model_field

    def get_data_type(self, obj: JsonSchemaObject) -> DataType:
        """Get the data type for a JSON Schema object."""
        return self._get_data_type(
            obj, localize_constraints=self._should_skip_root_field_constraints_for_multiple_types(obj)
        )

    def _get_data_type(self, obj: JsonSchemaObject, *, localize_constraints: bool) -> DataType:
        """Build scalar types while retaining constraints owned by their actual context.

        The null facts of the ``scalar`` site are read once: a ``type`` list returns before the null beside one
        ``type`` is read, so the mark of ``legacy-nullable-as-type-list`` leaves the facts read for it as they are.
        """
        null_facts = self._null_facts(obj, "scalar")
        if null_facts.rule == "legacy-nullable-as-type-list":
            obj.__dict__[_NULLABLE_TYPE_LIST_KEY] = True
        python_type_override = self._get_python_type_override(obj)
        if python_type_override:  # pragma: no cover
            return python_type_override

        if "const" in obj.extras:
            return self._get_const_data_type(obj.extras["const"], obj)

        if (schema_type := _read_type(obj)) is None:
            return self.data_type_manager.get_data_type(
                Types.any,
            )

        field_constraints = self.field_constraints and not localize_constraints
        if isinstance(schema_type, list):
            return self._get_type_array_data_type(
                schema_type,
                [
                    self._get_constrained_data_type(obj, type_, field_constraints=field_constraints)
                    for type_ in non_null_types(schema_type)
                ],
            )
        data_type = self._get_constrained_data_type(obj, schema_type, field_constraints=field_constraints)
        if null_facts.admits_null:
            return build_union(self.data_type, [data_type], nullable=True)
        return data_type

    def _get_constrained_data_type(self, obj: JsonSchemaObject, type_: str, *, field_constraints: bool) -> DataType:
        """Map one type of ``obj`` and its format, with the constraints ``obj`` writes beside it.

        Under ``field_constraints`` the field writes the constraints, so the type takes none of them. Under
        ``--read-length-keywords-by-type`` it takes no string or array keyword that ``type_`` does not hold
        (``other_kind_keywords``), as the field forms leave them out, also where ``--type-mappings`` maps ``type_`` to
        a string.
        """
        format_ = obj.format or "default"
        types = self._get_schema_type(obj, type_, format_)
        by_type = _value_keywords.reads_length_keywords_by_type(self)
        keyword_order = constrained_type_keyword_order(
            types.name,
            encoded_str=field_constraints and self.data_type_manager.encodes_constrained_base64,
            by_type=by_type,
        )
        if by_type:
            other = other_kind_keywords(_value_keywords.declared_kinds(self, type_, obj.format), by_type=True)
            keyword_order = tuple(keyword for keyword in keyword_order if keyword not in other)
        kwargs: dict[str, JsonSchemaDataTypeKwargValue] = {
            **_constraint_values(obj, keyword_order, as_numbers=True),
            **self._get_format_type_kwargs(type_, format_, types),
        }
        data_type = self._with_binary_content(
            self.data_type_manager.get_data_type(types, field_constraints=field_constraints, **kwargs),
            obj,
            type_,
            format_,
            types,
        )
        if (
            not field_constraints
            and (dropped := self.data_type_manager._non_finite_multiple_of(types, kwargs)) is not None  # noqa: SLF001
        ):
            self._graph_builder.record_dropped_multiple_of(data_type, dropped)
        return data_type

    def _get_type_array_data_type(self, types: list[str], data_types: list[DataType]) -> DataType:
        """Return the type of a type array from the types of its members other than null.

        The dict of an array of object and null is the type itself, admitting the null, so the property counts of
        the object stay on it, unless row ``legacy-map-null-left-to-field`` is kept.
        """
        match data_types:
            case []:
                return self.data_type_manager.get_data_type(Types.null)
            case [map_type] if (
                "legacy-map-null-left-to-field" not in self._legacy_object_maps
                and map_type.is_dict
                and set(types) <= {"object", "null"}
            ):
                return _with_null(map_type) if "null" in types else map_type
        return build_union(self.data_type, data_types, nullable=len(data_types) != len(types))

    def _resolve_external_ref_mapping(self, ref: str) -> tuple[str, str] | None:
        """Resolve a ref and return mapped package + fragment if configured; a ref with an edge maps as its key does."""
        if not self._external_ref_mapping:
            return None
        if isinstance(ref, ResolvedRef):
            return _schema_refs.ref_key(self, ref).mapping

        candidate_refs = [ref]
        resolved_ref = self.model_resolver.resolve_ref(ref)
        if resolved_ref not in candidate_refs:
            candidate_refs.append(resolved_ref)

        for candidate_ref in candidate_refs:
            if "#" not in candidate_ref:
                continue
            file_part, fragment = candidate_ref.split("#", maxsplit=1)
            if not file_part:
                continue
            lookup_key = _external_ref_mapping_key(file_part, self.model_resolver.current_base_path or self.base_path)
            if python_package := self._external_ref_mapping.get(lookup_key):
                return python_package, fragment

        return None

    def _check_external_ref_mapping(self, ref: str) -> DataType | None:
        """Check if a $ref matches an external ref mapping and return an import-based DataType.

        Splits the ref into file path + JSON pointer fragment, resolves the file path
        to absolute, and checks against the normalized mapping. If matched, constructs
        an import from the mapped package and the class name extracted from the fragment.

        Returns None if no mapping matches, allowing the caller to fall through
        to normal ref resolution.
        """
        mapped = self._resolve_external_ref_mapping(ref)
        if mapped is None or (import_ := self._external_ref_mapping_import(*mapped)) is None:
            return None
        self._import_ledger.append(self.imports, import_, _EXTERNAL_REF_MAPPING_IMPORT)
        return self.data_type.from_import(import_)

    def _external_ref_mapping_import(self, python_package: str, fragment: str) -> Import | None:
        """Return the import of the class a mapped fragment names, or None when the fragment names no schema."""
        # Extract and normalize class name from fragment to match generated model naming.
        raw_name = unescape_json_pointer_segment(fragment.rstrip("/").rsplit("/", maxsplit=1)[-1])
        if not raw_name:
            return None
        class_name = self.model_resolver.get_class_name(raw_name, unique=False).name

        # Construct import — same pattern as x-python-import
        return Import.from_full_path(f"{python_package}.{class_name}")

    def _get_x_python_import_path(self, x_python_import: dict[str, Any]) -> str | None:
        module = x_python_import.get("module")
        type_name = x_python_import.get("name")
        if not module and not type_name:
            return None
        if not module or not type_name:
            msg = "x-python-import requires both module and name"
            raise Error(msg)
        self._refuse_untrusted_code("x-python-import", x_python_import)
        return _type_overrides.get_emitted_python_type_path(
            self,
            _validate_schema_python_import_path(f"{module}.{type_name}", "x-python-import", self.target_python_version),
        )

    def _runtime_symbol(self, module: str, qualname: str, keyword: str) -> PythonTypeRuntimeSymbol:
        """Return the runtime symbol ``module.qualname``, refusing a path whose names the target does not bind."""
        from datamodel_code_generator._python_type_annotation import (  # ruff: ignore[import-outside-top-level]
            PythonTypeRuntimeSymbol,
        )

        _validate_schema_python_import_path(
            f"{module}.{qualname}" if module else qualname, keyword, self.target_python_version
        )
        return PythonTypeRuntimeSymbol(module, tuple(qualname.split(".")))

    def _get_target_runtime_symbol(self, module: str, qualname: str, keyword: str) -> PythonTypeRuntimeSymbol:
        """Bind ``module.qualname`` through ``import module``, which import overrides cannot move.

        The type the target must have is therefore checked here rather than in the emitted imports.
        """
        symbol = self._runtime_symbol(module, qualname, keyword)
        _type_overrides.get_target_python_type_path(self, f"{module}.{symbol.qualname_parts[0]}")
        return symbol

    def _get_x_python_runtime_symbol(self, x_python_import: dict[str, Any]) -> PythonTypeRuntimeSymbol | None:
        """Retain nested runtime module boundaries."""
        if (qualname := x_python_import.get("qualname")) is None:
            return None
        return self._get_target_runtime_symbol(
            x_python_import["module"],
            _validate_schema_python_import_path(qualname, "x-python-import qualname", self.target_python_version),
            "x-python-import",
        )

    def _get_x_python_import_expression(
        self, x_python_import: dict[str, Any], resolved_ref: str
    ) -> PythonTypeExpr | None:
        """Bind a specialized import while retaining its runtime module boundaries."""
        path = x_python_import.get("native-field")
        if not path and not x_python_import.get("x-python-type"):
            return self._get_x_python_runtime_symbol(x_python_import)
        if self._generic_python_import_expressions is None:
            self._generic_python_import_expressions = {}
        if (cached := self._generic_python_import_expressions.get(resolved_ref)) is not None:
            return cached
        if path:
            from datamodel_code_generator._python_type_annotation import (  # ruff: ignore[import-outside-top-level]
                PythonTypeModelField,
            )

            expression: PythonTypeExpr = self._get_target_runtime_symbol(
                path["module"], path["qualname"], "x-python-import native-field"
            )
            for field in path["fields"]:
                expression = PythonTypeModelField(
                    expression, field["name"], tuple(field["arguments"]), field["pydantic"]
                )
            self._generic_python_import_expressions[resolved_ref] = expression
            return expression
        from datamodel_code_generator._python_type_annotation import (  # ruff: ignore[import-outside-top-level]
            PythonTypeQualifiedName,
            rewrite_python_type_expr,
        )

        expression = cast("PythonTypeExpr", self._get_x_python_type(self._schema_object(x_python_import)))
        if symbol_definitions := x_python_import.get("symbols"):
            symbols = {
                name: self._runtime_symbol(symbol["module"], symbol["qualname"], "x-python-import symbols")
                for name, symbol in symbol_definitions.items()
            }
            expression = rewrite_python_type_expr(
                expression,
                lambda item: (
                    symbols.get(".".join(item.parts), item) if isinstance(item, PythonTypeQualifiedName) else item
                ),
            )
        self._generic_python_import_expressions[resolved_ref] = expression
        return expression

    def _get_x_python_import(self, full_path: str) -> Import:
        """Disambiguate imports of distinct runtime types sharing a class name."""
        if self._python_imports is None:
            self._python_imports = ({}, set(), [])
        imports, binding_names, pending_bindings = self._python_imports
        if import_ := imports.get(full_path):
            return import_
        import_ = Import.from_full_path(full_path)
        if import_.import_ in binding_names:
            # Preserve reservation timing without revisiting already reserved imports.
            self.model_resolver.exclude_names.update(pending_bindings)
            pending_bindings.clear()
            alias = self.model_resolver.get_class_name(import_.import_).name
            self.model_resolver.exclude_names.add(alias)
            import_ = Import(from_=import_.from_, import_=import_.import_, alias=alias)
        else:
            pending_bindings.append(import_.binding_name)
        imports[full_path] = import_
        binding_names.add(import_.binding_name)
        return import_

    @cached_property
    def _null_mode(self) -> NullMode:
        """The options that change how parse sites read null (``parser/_null_facts.py``)."""
        return NullMode(
            strict_nullable=self.strict_nullable,
            use_missing_sentinel=self.use_missing_sentinel,
            force_optional=self.force_optional_for_required_fields,
            type_excludes_member_null="legacy-all-of-null-as-written" not in self._legacy_intersection,
            read_nullable_beside_type_list=self.config.read_nullable_beside_type_list,
        )

    @cached_property
    def _legacy_intersection(self) -> frozenset[str]:
        """The rows of ``parser/_intersection.py:LEGACY_ROWS`` kept: those of each of its options that is off."""
        options = {
            "intersect_allof_keywords": self.intersects_allof_keywords,
            "intersect_combined_schema_keywords": self.intersects_combined_schema_keywords,
            "distribute_allof_over_unions": self.config.distribute_allof_over_unions,
        }
        return kept_legacy_rows({**options, **implied_options(options)})

    def _type_has_null(self, obj: JsonSchemaObject) -> bool:
        """Return ``JsonSchemaObject.type_has_null``, false where a ``type`` excludes the null of its members.

        A ``type`` that is neither null nor a list holding null, beside a null member of its ``anyOf`` or ``oneOf``,
        admits no null (row ``type-excludes-member-null``) unless ``legacy-all-of-null-as-written`` is kept.
        """
        return False if _type_excludes_member_null(obj, self._null_mode) else obj.type_has_null

    def _null_facts(
        self,
        obj: JsonSchemaObject,
        site: NullSite,
        *,
        required: bool = False,
        has_default: bool = False,
        parts: Sequence[NullSources] = (),
    ) -> NullFacts:
        """Return what ``site`` reads from where the null of ``obj`` comes from (``parts``: of its typed parts)."""
        return resolve_null_facts(
            _null_sources(obj, site, self._type_has_null),
            site,
            self._null_mode,
            required=required,
            has_default=has_default,
            parts=parts,
        )

    def _object_nullable(self, obj: JsonSchemaObject) -> bool:
        """Return whether an object model's schema admits null, by its types or by nullable under strict nullability."""
        return bool(self._null_facts(obj, "object-model").admits_null)

    def _cache_ref_data_type_facts(self, resolved_ref: str, obj: JsonSchemaObject) -> None:
        self._store_ref_data_type_facts(resolved_ref, obj)

    def _store_ref_data_type_facts(self, resolved_ref: str, obj: JsonSchemaObject) -> tuple[Any, bool]:
        """Store and return what a reference to ``obj`` reads: its x-python-import and whether it admits null."""
        facts = self._ref_data_type_facts[resolved_ref] = (
            obj.extras.get("x-python-import"),
            bool(self._null_facts(obj, "ref-target").admits_null),
        )
        self._false_schema_refs = _update_false_schema_refs(
            self._false_schema_refs, resolved_ref, is_false=obj.is_boolean_schema_false
        )
        return facts

    def get_ref_data_type(self, ref: str) -> DataType:
        """Get a data type from a reference string.

        The referenced schema only contributes its x-python-import extra and
        null/nullable flags here, so those facts are cached per resolved ref to
        avoid re-validating the same schema for every occurrence of the ref.
        """
        _validate_external_ref(ref)

        # Check external ref mapping before loading the schema
        mapped = self._check_external_ref_mapping(ref)
        if mapped is not None:
            return mapped

        key = _schema_refs.ref_key(self, ref, embedded=False)
        ref = keep_resolved_ref(ref, key.spelling)
        resolved_ref = key.key
        if (facts := self._ref_data_type_facts.get(resolved_ref)) is None:
            facts = self._store_ref_data_type_facts(resolved_ref, self._load_ref_schema_object(ref))
        x_python_import, is_optional = facts
        if isinstance(x_python_import, dict) and (full_path := self._get_x_python_import_path(x_python_import)):
            if runtime_symbol := self._get_x_python_import_expression(x_python_import, resolved_ref):
                return _type_overrides.python_type_data_type(self, runtime_symbol)
            import_ = self._get_x_python_import(full_path)
            self._import_ledger.append(self.imports, import_, _X_PYTHON_IMPORT)
            return self.data_type(type=import_.import_, import_=import_, alias=import_.alias)
        if not self._schema_resource_registry.dynamic_anchors or (
            (reference := _schema_refs.dynamic_specialization_reference(self, ref, resolved_ref)) is None
        ):
            reference = self._add_ref(ref)
        data_type = self.data_type(reference=reference, is_optional=is_optional)
        if is_optional and self.omit_redundant_reference_null:
            self._graph_builder.mark_target_null(data_type)
        return data_type

    def _model_additional_properties(self, obj: JsonSchemaObject) -> JsonSchemaObject | bool | None:
        """Return the additionalProperties of the model built from ``obj``.

        An inline allOf member that admits no property but its own (``inline_sources_close``) closes the combined model.
        """
        if obj.allOf and obj.additionalProperties is not False and _all_of_values.inline_sources_close(self, (obj,)):
            return False
        return obj.additionalProperties

    def _extra_items_type(
        self, additional_properties: JsonSchemaObject, *, reads_false_all_of: bool = True
    ) -> DataType | None:
        """Return the type TypedDict writes as extra_items for schema-valued additionalProperties.

        It is their lightweight type (``_build_lightweight_type``), which names models and generates none, with the
        positions that are false written as an allOf read as false (``_read_false_all_of_values``) unless
        ``reads_false_all_of`` is off (row ``legacy-rw-extra-items-as-written`` of the request/response walk).
        """
        if reads_false_all_of:
            additional_properties = self._read_false_all_of_values(additional_properties, lightweight=True)
        return self._build_lightweight_type(additional_properties)

    def set_additional_properties(self, path: str, obj: JsonSchemaObject) -> None:
        """Set additional properties flag in extra template data.

        For TypedDict with PEP 728 support:
        - additionalProperties: false -> closed=True
        - additionalProperties: { type: X } -> extra_items=X

        This is controlled by use_closed_typed_dict option. When disabled,
        the additionalProperties constraint is not converted to PEP 728 syntax; a boolean one is stored all the same,
        for the outputs that read it as their closedness (the TypedDict reads the option).
        The additionalProperties are those of the model (``_model_additional_properties``). Under
        --use-msgspec-forbid-unknown-fields a model whose schema or an inline allOf member of it admits no property but
        its own (``inline_sources_close``) is marked for msgspec's forbid_unknown_fields.
        """
        if self.use_msgspec_forbid_unknown_fields and _all_of_values.inline_sources_close(self, (obj,)):
            _mark_forbids_unknown_fields(self.extra_template_data[path])
        additional_properties = self._model_additional_properties(obj)
        if isinstance(additional_properties, bool):
            self._output_model_context.store_additional_properties_value(
                self.extra_template_data[path],
                value=additional_properties,
                target_python_version=self.target_python_version,
            )
        elif isinstance(additional_properties, JsonSchemaObject):
            self._output_model_context.store_additional_properties_value(
                self.extra_template_data[path],
                value=True,
            )
            if not self.use_closed_typed_dict:
                return
            if additional_props_type := self._extra_items_type(additional_properties):  # pragma: no branch
                context = self._output_model_context
                _rw_split.store_extra_items_type(
                    self,
                    self.extra_template_data[path],
                    additional_props_type,
                    reference_classes_required=context.requires_additional_properties_reference_classes,
                    target_python_version=self.target_python_version,
                )

    def _warn_closed_pattern_properties(self, path: str, obj: JsonSchemaObject) -> None:
        """Warn once per schema object whose closed model rejects the keys its patternProperties admit.

        A model with fields (not a dict root) keeps the closedness of the schema's additionalProperties, final at L1,
        so the output that writes it (``extra='forbid'``, ``closed=True``) rejects every key the patterns admit. Only
        pydantic_v2.BaseModel output checks them exactly, with --generate-schema-validators. Patterns whose schemas are
        all ``false`` admit no key, so the caller warns only where one pattern's schema is not ``false``.
        """
        data_model_type = self.data_model_type
        if (
            not data_model_type.WRITES_CLOSED_ADDITIONAL_PROPERTIES
            or (data_model_type.SUPPORTS_TYPED_DICT_TOTAL_FALSE and not self.use_closed_typed_dict)
            or self.generate_schema_validators
            or any(
                self.extra_template_data.get(key, {}).get(name)
                for key in (ALL_MODEL, path)
                for name in _EXTRA_FIELDS_KEYS
            )
        ):
            return
        where = named_location(obj.schema_location, path)
        with first_report(self._closed_pattern_properties, where) as first:
            if first:
                hint = (
                    " With --output-model-type pydantic_v2.BaseModel, "
                    "--generate-schema-validators writes the pattern key rule that accepts them."
                    if data_model_type.SUPPORTS_SCHEMA_RUNTIME_VALIDATION
                    else ""
                )
                warn(
                    f"{where}: additionalProperties is false beside patternProperties, so the generated model rejects "
                    f"the keys the patterns admit.{hint}",
                    stacklevel=2,
                )

    def set_unevaluated_properties(self, path: str, obj: JsonSchemaObject) -> None:
        """Set unevaluated properties flag in extra template data."""
        if isinstance(obj.unevaluatedProperties, bool):
            self.extra_template_data[path]["unevaluatedProperties"] = obj.unevaluatedProperties
        elif isinstance(obj.unevaluatedProperties, JsonSchemaObject) and obj.additionalProperties is None:
            self.extra_template_data[path]["unevaluatedProperties"] = True

    def set_title(self, path: str, obj: JsonSchemaObject) -> None:
        """Set title in extra template data."""
        if obj.title:
            self.extra_template_data[path]["title"] = obj.title

    def set_schema_id(self, path: str, obj: JsonSchemaObject) -> None:
        """Set $id in extra template data."""
        if obj.id:
            self.extra_template_data[path]["schema_id"] = obj.id

    def _record_schema_location(self, reference: Reference, location: SchemaLocation | None) -> None:
        """Record where the model of ``reference`` is built from (L1), and the naming frames open while L1 records them.

        Both are kept on the Reference object, so they stay valid when later passes change ``Reference.path``.
        """
        _set_schema_location(reference, location)
        if (frames := self._naming_frames) is not None:
            frames.record(reference, definition=self._is_definition_location(location))

    def _model_frame(self) -> NamingScope:
        """Return the naming frame a model builder runs its body in, opened only while L1 records naming frames.

        The frame takes the model the builder returns (``NamingScope.built``) when that model was recorded inside it, so
        the innermost such frame of another model names the model whose parse built it (``BuildRecord.owner``).
        """
        return UNRECORDED if (frames := self._naming_frames) is None else NamingScope(frames)

    def _claiming(self, claim: Claim | None) -> NamingScope:
        """Return the naming frame that makes ``claim`` for the model its block builds, while L1 records naming frames.

        No claim opens no frame.
        """
        return UNRECORDED if claim is None or (frames := self._naming_frames) is None else NamingScope(frames, claim)

    def _detached_naming_frames(self) -> AbstractContextManager[None]:
        """Return the context of a document parse: no naming frame is open, so the document's models are roots."""
        return nullcontext() if (frames := self._naming_frames) is None else frames.detached()

    def _is_definition_location(self, location: SchemaLocation | None) -> bool:
        """Return whether ``location`` is an entry of a definitions container of its document's schema paths.

        A container is one at the document root or in such an entry, so a ``$defs`` entry of a definition is one too.
        """
        if location is None or not (tokens := location.tokens):
            return False
        containers = tuple(tuple(split) for _, split in self._schema_paths_at(location))
        while tokens:
            if (container := next((split for split in containers if _ends_with(tokens, split)), None)) is None:
                return False
            tokens = tokens[: -1 - len(container)]
        return True

    def _record_schema_object(self, reference: Reference, obj: JsonSchemaObject) -> None:
        """Record the schema object the model of ``reference`` is built from, and take its template data and marks."""
        self._record_schema_location(reference, obj.schema_location)
        self._record_derived_from(reference, obj)
        if obj.__dict__.get(FIELDLESS_SITE_KEY):
            reference.__dict__[FIELDLESS_SITE_KEY] = True
        if obj.__dict__.get(ITEM_COUNTS_SITE_KEY):
            reference.__dict__[ITEM_COUNTS_SITE_KEY] = True
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)

    def _record_derived_from(self, reference: Reference, obj: JsonSchemaObject) -> None:
        """Record the target a site schema is built from (``_derived_site``) on the Reference of its model."""
        if (ref := obj.__dict__.get(_DERIVED_FROM_REF_KEY)) is None:
            return
        data_type = self.get_ref_data_type(ref)
        reference.__dict__[_DERIVED_FROM_KEY] = data_type.reference
        _rw_split.unregister_temporary_type(self, data_type)

    def _set_schema_metadata(self, path: str, obj: JsonSchemaObject) -> None:
        """Set title, $id, additionalProperties and unevaluatedProperties in extra template data."""
        self.set_title(path, obj)
        self.set_schema_id(path, obj)
        self.set_additional_properties(path, obj)
        self.set_unevaluated_properties(path, obj)
        self.set_deprecated(path, obj)

    def set_deprecated(self, path: str, obj: JsonSchemaObject) -> None:
        """Set deprecated flag in extra template data."""
        if obj.extras.get("deprecated") is True:
            self.extra_template_data[path]["deprecated"] = True

    def set_schema_extensions(self, path: str, obj: JsonSchemaObject) -> None:
        """Set schema extensions (x-* fields) in extra template data."""
        extensions = {k: self._externalize_schema_extra(k, v) for k, v in obj.extras.items() if k.startswith("x-")}
        if extensions:
            self.extra_template_data[path]["extensions"] = extensions

        if obj.extras.get("x-is-base-class"):
            self.extra_template_data[path]["is_base_class"] = True

        # Process model-level metadata and model_extra_keys for json_schema_extra in ConfigDict
        model_extras: dict[str, Any] = {
            k: self._externalize_schema_extra(k, v) for k, v in obj.extras.items() if k in DEFAULT_MODEL_EXTRA_KEYS
        }
        if self.model_extra_keys or self.model_extra_keys_without_x_prefix:
            for k, v in obj.extras.items():
                if self.model_extra_keys and k in self.model_extra_keys:
                    model_extras[k] = self._externalize_schema_extra(k, v)
                elif self.model_extra_keys_without_x_prefix and k in self.model_extra_keys_without_x_prefix:
                    model_extras[_without_x_prefix(k)] = self._externalize_schema_extra(k, v)
        if model_extras:
            self.extra_template_data[path]["model_extras"] = model_extras

    def _get_python_type_flags(self, obj: JsonSchemaObject) -> dict[str, bool]:
        """Get container type flags from x-python-type extension.

        Returns a dict with flags like is_set, is_frozen_set, is_mapping, is_sequence
        that can be passed to data_type() to override the default container type: those of the slot a class the
        x-python-type names, or the first member of a union that names one, stands in (``GIVEN_CONTAINERS``), but the
        list's and the dict's, which the callers default to.

        Note: This is an instance method (not static) due to the snooper_to_methods
        class decorator which does not preserve staticmethod descriptors.
        """
        if (python_type := self._get_x_python_type(obj)) is None:
            return {}

        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            is_union_python_type_expr,
            python_type_expr_arguments,
            python_type_expr_base_name,
        )

        base_type = python_type_expr_base_name(python_type)
        if base_type in _PYTHON_TYPE_FLAGS:
            return {_PYTHON_TYPE_FLAGS[base_type]: True}

        if is_union_python_type_expr(python_type):
            for argument in python_type_expr_arguments(python_type):
                arg_base = python_type_expr_base_name(argument)
                if arg_base in _PYTHON_TYPE_FLAGS:
                    return {_PYTHON_TYPE_FLAGS[arg_base]: True}

        return {}

    def _container_flags(self, obj: JsonSchemaObject, slot: ContainerSlot) -> dict[str, Any]:
        """Return the container flags of the data type of a schema read as a container in ``slot`` (a list or a dict).

        They are the flags its x-python-type gives it (``_get_python_type_flags``), else those of ``slot``. Under
        --use-x-python-type-as-written a container class the x-python-type names, compatible with the schema's type and
        in the slot the data type is in, is also given to the data type (``given_container``), which writes it around
        the schema's item, key and value types; row ``legacy-x-python-type-container-takes-slot-class``: without it the
        slot's class is written.
        """
        flags: dict[str, Any] = self._get_python_type_flags(obj) or {CONTAINER_FLAGS[slot]: True}
        if not self.use_x_python_type_as_written or (python_type := self._get_x_python_type(obj)) is None:
            return flags

        from datamodel_code_generator._python_type_annotation import python_type_expr_base_name  # noqa: PLC0415

        if (
            (given := GIVEN_CONTAINERS.get(base_type := python_type_expr_base_name(python_type))) is not None
            and CONTAINER_FLAGS[given.slot] in flags
            and self._x_python_type_fits(obj, python_type)
        ):
            return {**flags, "given_container": base_type}
        return flags

    def _is_compatible_python_type(self, schema_type: str | None, python_type: PythonTypeExpr) -> bool:
        """Check if x-python-type is compatible with the JSON Schema type.

        A compatible x-python-type leaves the schema's type to the field; any other one types it. A union spelled
        ``X | Y`` is never compatible. Row ``legacy-spelled-union-takes-schema-type``: without
        --use-x-python-type-as-written a union spelled ``Optional[...]`` or ``Union[...]`` is compatible with every
        declared type, so the field takes the schema's type.
        """
        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            is_union_python_type_expr,
            iter_python_type_expr_names,
            python_type_expr_base_name,
        )

        base_type = python_type_expr_base_name(python_type)
        if any(name in self.PYTHON_TYPE_OVERRIDE_ALWAYS for name in iter_python_type_expr_names(python_type)):
            return False
        if schema_type is None:
            return not is_union_python_type_expr(python_type)
        if base_type in _PYTHON_UNION_BASE_TYPES:
            return not self.use_x_python_type_as_written
        compatible = self.COMPATIBLE_PYTHON_TYPES.get(schema_type, frozenset())
        return base_type in compatible

    def _get_x_python_type(self, obj: JsonSchemaObject) -> PythonTypeExpr | None:
        """Resolve internal IR or parse external text at the schema boundary."""
        x_python_type = obj.extras.get("x-python-type")
        if not x_python_type:
            return None
        if not isinstance(x_python_type, str):
            from datamodel_code_generator.input_model_result import PythonTypeSchemaAnnotation  # noqa: PLC0415

            return x_python_type.expression if isinstance(x_python_type, PythonTypeSchemaAnnotation) else None
        if (
            self._python_type_expressions is not None
            and (expression := self._python_type_expressions.get(x_python_type)) is not None
        ):
            return expression
        self._refuse_untrusted_code("x-python-type", x_python_type, obj.schema_location)
        from datamodel_code_generator.input_model_result import is_legacy_python_type_token  # noqa: PLC0415

        if is_legacy_python_type_token(x_python_type):
            msg = "Internal x-python-type context is unavailable"
            raise Error(msg)

        # This is an external-text boundary. Keep the runtime AST/token codec
        # unloaded for schemas that do not opt in to x-python-type.
        from datamodel_code_generator._python_type_annotation_codec import (  # noqa: PLC0415
            parse_python_type_annotation,
        )

        if (expression := parse_python_type_annotation(x_python_type)) is not None:
            return expression
        msg = "x-python-type must be a valid Python type annotation"
        raise Error(msg)

    @override
    def _resolve_type_import_from_defs(self, type_name: str) -> Import | PythonTypeExpr | None:
        """Resolve import for a type name from $defs with x-python-import."""
        try:
            spelled = definition_ref("$defs", type_name)
            ref = _schema_refs.derived_ref(
                self,
                spelled,
                _schema_documents.document_root(self, self.model_resolver.current_root),
                "x-python-type",
                spelled,
            )
            ref_schema = self._load_ref_schema_object(ref)
            x_python_import = ref_schema.extras.get("x-python-import")
            if isinstance(x_python_import, dict) and (full_path := self._get_x_python_import_path(x_python_import)):
                return self._get_x_python_import_expression(
                    x_python_import, self._ref_path(ref)
                ) or self._get_x_python_import(full_path)
        except (Error, RecursionError):
            raise
        except Exception:  # noqa: BLE001, S110
            pass
        return None

    def _overriding_python_type(self, obj: JsonSchemaObject) -> PythonTypeExpr | None:
        """Return the x-python-type of a schema when it is incompatible with the schema type, so it types the schema."""
        if (python_type := self._get_x_python_type(obj)) is None:
            return None
        return None if self._x_python_type_fits(obj, python_type) else python_type

    def _x_python_type_fits(self, obj: JsonSchemaObject, python_type: PythonTypeExpr) -> bool:
        """Return whether a schema's x-python-type is compatible with its type (``_is_compatible_python_type``)."""
        return self._is_compatible_python_type(
            type_ if isinstance(type_ := _read_type(obj), str) else None, python_type
        )

    def _get_python_type_override(self, obj: JsonSchemaObject) -> DataType | None:
        """Get DataType from x-python-type if it's incompatible with schema type."""
        if (python_type := self._overriding_python_type(obj)) is None:
            return None
        return _type_overrides.python_type_data_type(self, python_type)

    def _apply_title_as_name(self, name: str, obj: JsonSchemaObject) -> str:
        """Apply title as name if use_title_as_name is enabled."""
        if self.use_title_as_name and obj.title:
            return sanitize_module_name(obj.title, treat_dot_as_module=self.treat_dot_as_module)
        return name

    def _get_single_literal_value(
        self,
        obj: JsonSchemaObject,
        seen_refs: set[_TargetNode] | None = None,
    ) -> JsonSchemaLiteral | None:
        """Return the one value a property schema admits as a tag (``parser/_union_tags.py``)."""
        return _union_tags.get_single_literal_value(self, obj, seen_refs)

    def _infer_union_variant_names(
        self,
        name: str,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
        variant_keys: dict[int, str],
    ) -> list[str | None] | None:
        """Name the branches of a union after their tags (``parser/_union_tags.py``)."""
        return _union_tags.infer_union_variant_names(self, name, obj, combined_schemas, variant_keys)

    def _get_ref_raw_schema(self, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
        return _schema_refs.read_ref_value(self, resolved_ref)

    def _ref_reading(self, root: Literal["root", "object"]) -> RefReading:
        """Return how ``SchemaGraphBuilder.with_ref_edges`` reads a document (``root``) or a schema (``object``)."""
        return _schema_refs.ref_reading(self, root)

    def _ref_schema_exists(self, resolved_ref: str) -> bool:
        """Return whether a resolved JSON pointer identifies a source schema."""
        return _schema_refs.ref_schema_exists(self, resolved_ref)

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Load a JsonSchemaObject from a $ref using standard resolve/load pipeline."""
        return _schema_refs.load_ref_schema_object(self, ref)

    @property
    def _graph(self) -> SchemaGraphBuilder:
        """Return the schema graph of the parse."""
        return cast("SchemaGraphBuilder", self._schema_graph)

    def _key_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node at a reference key, or the key when the graph does not locate its value.

        The value is read as ``_load_ref_schema_object`` reads it, without validating it.
        """
        return _schema_refs.key_node(self, resolved_ref)

    def _shared_schema_object(self, raw: dict[str, YamlValue] | YamlValue, path: list[str]) -> JsonSchemaObject:
        """Return the schema object a value of a loaded document validates to, validated once and shared by its readers.

        ``path`` names the reference in the error of a value that does not validate.
        """
        return _schema_refs.shared_schema_object(self, raw, path)

    def _shared_object(self, raw: object, kind: type[_ValidatedT], validate: Callable[[], _ValidatedT]) -> _ValidatedT:
        """Return the ``kind`` object a value of a loaded document validates to, validated once by the schema graph."""
        return _schema_refs.shared_object(self, raw, kind, validate)

    def _discriminator_mapping_ref(self, name: str, value: str, mapping: SchemaLocation | None) -> str:
        """Return a discriminator mapping value as a reference, with the edge of the mapping located at ``mapping``."""
        return _schema_refs.derived_ref(self, value, mapping, name, value)

    def _reference_location(self, documents: Container[str], holder: str, ref: str) -> SchemaLocation | None:
        """Return the schema a `$ref` or `$dynamicRef` of a loaded schema names, as the parser resolves it.

        The reference resolves by its edge, or, without one, in ``holder``, the document that holds it. None is a schema
        in none of ``documents``.
        """
        with self._home_context(holder):
            document, _, fragment = _schema_refs.ref_key(self, ref).key.partition("#")
        tokens = reference_tokens(fragment)
        return SchemaLocation(document, tokens) if document in documents and tokens is not None else None

    @contextmanager
    def _home_context(self, document: str) -> Generator[None, None, None]:
        """Resolve references as a loaded document resolves its own: from its location, its root `$id` and `$self`.

        ``document`` names the document as reference paths do.
        """
        path_parts = _ref_path_parts(document)
        prepared = self._schema_resource_cache.get(name := _schema_documents.schema_resource_document(self, path_parts))
        with _schema_refs.schema_document_context(self, path_parts):
            if prepared is None:
                yield
                return
            with self.root_id_context(prepared[1]), self._document_self_context(name, prepared[1]):
                yield

    def _document_self_context(  # noqa: PLR6301
        self,
        document: str,  # noqa: ARG002
        raw: dict[str, Any],  # noqa: ARG002
    ) -> AbstractContextManager[None]:
        """Return the context in which a loaded document, by registry name, names itself other than by its `$id`."""
        return nullcontext()

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names: its edge's key, or a plain string's ``resolve_ref``."""
        return _schema_refs.ref_path(self, ref)

    def _add_ref(self, ref: str) -> Reference:
        """Return the Reference at the key of a reference's edge, named after the edge's spelling if it is new.

        A plain string is added as ``ModelResolver.add_ref`` adds it in the current context. The package and fragment
        that ``--external-ref-mapping`` maps a reference to are recorded for its Reference (``_mapped_references``).
        """
        if isinstance(ref, ResolvedRef):
            key = _schema_refs.ref_key(self, ref)
            reference, mapping = self.model_resolver._add_ref_at(key.key, key.spelling), key.mapping  # noqa: SLF001
        else:
            reference, mapping = self.model_resolver.add_ref(ref), self._resolve_external_ref_mapping(ref)
        if mapping is not None:
            self._mapped_references[reference.path] = mapping
        return reference

    def _is_local_ref(self, ref: str) -> bool:
        """Return whether a reference is a fragment of the document parsed now: written as one there, by its edge."""
        return _schema_refs.is_local_ref(self, ref)

    def _uses_builtin_false_ref_facts(self) -> bool:
        """Return whether cached local false-ref facts preserve parser hooks."""
        if self.SCHEMA_OBJECT_TYPE is not JsonSchemaObject:
            return False
        if getattr(self._cache_ref_data_type_facts, "__func__", None) is not _BUILTIN_REF_FACT_CACHER:
            return False
        if getattr(self._get_ref_raw_schema, "__func__", None) is not _BUILTIN_REF_RAW_SCHEMA_LOADER:
            return False
        if getattr(self._load_ref_schema_object, "__func__", None) is not _BUILTIN_REF_SCHEMA_LOADER:
            return False
        return getattr(self._validate_schema_object, "__func__", None) is _BUILTIN_SCHEMA_VALIDATOR

    def _is_local_ref_false_schema(
        self,
        ref: str,
        *,
        use_builtin_facts: bool,
    ) -> bool:
        """Return whether a local ref targets false without repeating default validation."""
        return _schema_refs.is_local_ref_false_schema(self, ref, use_builtin_facts=use_builtin_facts)

    def _schema_location(self, resolved_ref: str) -> str:
        """Return the registry location of a resolved reference."""
        return _schema_refs.schema_location(self, resolved_ref)

    @contextmanager
    def _ref_document_context(self, file_part: str) -> Generator[tuple[str, dict[str, Any]], None, None]:
        """Enter the resolution context of the document that a resolved reference points into."""
        document = _schema_documents.schema_resource_document(self, _ref_path_parts(file_part))
        path_parts = self._schema_resource_path_parts.get(document) or _ref_path_parts(file_part)
        with _schema_documents.referenced_document_context(
            self, _schema_refs.schema_document_context(self, path_parts)
        ):
            raw = _schema_documents.prepare_schema_resources(
                self, _schema_refs.ref_file_document(self, file_part), path_parts
            )
            with self.root_id_context(raw):
                yield document, raw

    @contextmanager
    def _schema_location_context(self, location: str) -> Generator[str, None, None]:
        """Yield a reference to a registered schema location that resolves from the current document.

        Locations in other documents are referenced by their resolved form from the input base, so a
        remote or piped generic can bind $dynamicRef to an anchor of the schema that referenced it.
        """
        target_document, _, pointer = location.partition("#")
        if target_document == _schema_documents.current_document(self):
            yield f"#{pointer}"
            return
        with _schema_refs.schema_document_context(self, []), self.root_id_context({}):
            yield _schema_refs.location_ref(self, location)

    def _parse_dynamic_specializations(self) -> None:
        """Parse pending specialized models iteratively, outside of any specialization being parsed."""
        return _schema_refs.parse_dynamic_specializations(self)

    @contextmanager
    def _dynamic_target_ref(self, target: _DynamicTarget) -> Generator[str, None, None]:
        """Yield the reference to a target of rule DS that resolves from the current document."""
        if target.location is not None:
            with self._schema_location_context(target.location) as ref:
                yield ref
            return
        yield target.ref

    @contextmanager
    def _dynamic_scope_context(
        self,
        path: Sequence[str],
        bindings: dict[str, str],
        pointer: str | None = None,
        document: str | None = None,
    ) -> Generator[None, None, None]:
        """Track the model root being parsed and the $dynamicAnchor bindings of its dynamic scope.

        The pointer and document locate the schema; they differ from the model path and the current root
        only for schemas parsed in place of another, such as specialized models and merged $ref targets.
        """
        previous = self._dynamic_document, self._dynamic_scope_path, self._dynamic_scope_pointer, self._dynamic_bindings
        self._dynamic_document = _schema_documents.current_document(self) if document is None else document
        self._dynamic_scope_path = tuple(path)
        self._dynamic_scope_pointer = (
            unquote(self.model_resolver.join_path(tuple(path)).partition("#")[2]) if pointer is None else pointer
        )
        self._dynamic_bindings = bindings
        try:
            yield
        finally:
            (
                self._dynamic_document,
                self._dynamic_scope_path,
                self._dynamic_scope_pointer,
                self._dynamic_bindings,
            ) = previous

    def _ref_target_dump(self, ref: str, resolved_ref: str) -> tuple[dict[str, Any], str]:
        """Return a fresh dump of a referenced schema for the current document, and the reference it ends at.

        A $recursiveRef inside a schema from another document is pinned to its target there (``_pin_recursive_refs``),
        and a referenced schema that is itself a $ref is merged with its own target. The dump depends only on the schema
        graph node the reference names and the current document, so each parse computes it once per pair and hands out
        copies. A chain of such aliases is loaded first and merged from its far end, so each merge finds the dump of the
        next alias computed.
        """
        return _schema_refs.ref_target_dump(self, ref, resolved_ref)

    def _intersect_ref_siblings_schema(
        self, target: dict[str, Any], siblings: dict[str, Any], where: str, *, siblings_are_parts: bool
    ) -> JsonSchemaObject:
        """Return the schema of the intersection of a referenced schema's dump and the sibling keywords of its $ref."""
        return _schema_refs.intersect_ref_siblings_schema(
            self, target, siblings, where, siblings_are_parts=siblings_are_parts
        )

    def _warn_unsatisfiable(self, where: str, keywords: tuple[str, ...], *parts: str) -> None:
        """Warn once per parse for each schema whose parts have no value in common for some keywords."""
        if not keywords:
            return
        with first_report(self._unsatisfiable_schemas, (where, keywords)) as first:
            if first:
                warn_unsatisfiable(where, keywords, *parts)

    def _warn_untaken_patterns(self, where: str, pattern: str, untaken: tuple[str, ...]) -> None:
        """Warn once per parse for each schema whose written pattern leaves out patterns of its parts."""
        if not untaken:
            return
        with first_report(self._untaken_pattern_schemas, (where, (pattern, *untaken))) as first:
            if first:
                warn_untaken_patterns(where, pattern, untaken)

    def _warn_intersection(self, where: str, intersection: KeywordIntersection, *parts: str) -> None:
        """Warn about the keywords of an intersection whose parts have no value in common, and its untaken patterns."""
        self._warn_unsatisfiable(where, intersection.unsatisfiable, *parts)
        if intersection.untaken_patterns:
            self._warn_untaken_patterns(where, intersection.keywords["pattern"], intersection.untaken_patterns)

    def _conjoined_pattern(self, patterns: Sequence[str], where: str) -> str:
        """Return the conjoined pattern (``conjoin_patterns``), warning about the patterns it leaves out.

        Under ``legacy-lookahead-patterns`` the lookaheads 0.86.0 wrote (``lookahead_patterns``), without a warning.
        """
        if "legacy-lookahead-patterns" in self._legacy_intersection:
            return lookahead_patterns(patterns)
        conjunction = conjoin_patterns(patterns)
        self._warn_untaken_patterns(where, conjunction.pattern, conjunction.untaken)
        return conjunction.pattern

    def _mark_reported_value_conflict(self, schema: JsonSchemaObject, keywords: Sequence[str]) -> None:
        """Mark a merged schema whose ``enum`` or ``const`` conflict the intersection warning already reports."""
        return _schema_refs.mark_reported_value_conflict(self, schema, keywords)

    def _merge_ref_with_schema(self, obj: JsonSchemaObject, where: str | None = None) -> JsonSchemaObject:
        """Merge $ref schema with current schema's additional keywords.

        JSON Schema 2020-12 allows $ref alongside other keywords, which apply together with the referenced schema:
        the merged schema is their intersection, keyword by keyword (``_intersect_ref_siblings``).

        A local contains does not replace the referenced one: both contains then apply, so the referenced contains
        keywords become the item bounds they imply and intersect with the local bounds. A referenced schema that is
        itself a $ref is merged with its own target, so aliases keep the definition they name. A warning for literals
        no value meets names ``where``, by default the location of ``obj``.
        """
        return _schema_refs.merge_ref_with_schema(self, obj, where)

    def _is_ref_circular(self, resolved_ref: str) -> bool:
        """Check if a resolved $ref target contains a circular reference (cached).

        A target whose references reach a document or pointer that cannot be loaded counts as circular.
        """
        return _schema_refs.is_ref_circular(self, resolved_ref)

    @contextmanager
    def _ref_file_context(self, file_part: str) -> Generator[None, None, None]:
        """Resolve references relative to the file or URL a reference key names, or to the current one without it."""
        if file_part and is_url(file_part):
            base_path = None
            root_path = [file_part]
        else:
            base_path = Path(file_part).parent if file_part else self.model_resolver.current_base_path
            root_path = file_part.split("/") if file_part else self.model_resolver.current_root
        with (
            self.model_resolver.current_base_path_context(base_path),
            self.model_resolver.base_url_context(file_part or self.model_resolver.base_url),
            _schema_documents.referenced_document_context(self, self.model_resolver.current_root_context(root_path)),
        ):
            yield

    def _merge_primitive_schemas(self, items: list[JsonSchemaObject]) -> JsonSchemaObject:
        """Merge primitive schemas into the intersection of their constraints (``parser/_lightweight_types.py``)."""
        return _lightweight_types.merge_primitive_schemas(self, items)

    def _merge_primitive_schemas_for_allof(self, items: list[JsonSchemaObject]) -> JsonSchemaObject | None:
        """Merge primitive schemas for allOf under ``allof_merge_mode`` (``parser/_lightweight_types.py``)."""
        return _lightweight_types.merge_primitive_schemas_for_allof(self, items)

    @staticmethod
    def _first_typed_schema_dict(items: list[JsonSchemaObject]) -> dict[str, Any]:
        base_dict = next(
            (item.model_dump(exclude_unset=True, by_alias=True) for item in items if item.type),
            {},
        )
        if (original_domain := _numeric_type_domain(original_type := base_dict.get("type", ""))) is None or any(
            item.type and item.type != original_type and _numeric_type_domain(item.type) is None for item in items
        ):
            return base_dict
        others = (declared_types(item.type) for item in items if item.type and item.type != original_type)
        types = reduce(intersect_types, others, declared_types(original_type))
        if types and _numeric_type_domain(list(types)) != original_domain:
            base_dict["type"] = types[0] if len(types) == 1 else list(types)
        return base_dict

    def _merge_schema_constraints(
        self,
        base_dict: dict[str, Any],
        items: list[JsonSchemaObject],
        *,
        intersect: bool,
    ) -> None:
        _lightweight_types.merge_schema_constraints(self, base_dict, items, intersect=intersect)

    def _group_allof_items(
        self,
        allof_items: Sequence[JsonSchemaObject],
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
    ) -> _AllOfItemGroups | None:
        """Group allOf members by the part of the lightweight type each one gives, or None when one gives none."""
        groups = _AllOfItemGroups([], [], [], [], [])
        for item in allof_items:
            if item.ref:
                groups.refs.append(item)
            elif item.type and item.type != "object" and not isinstance(item.type, list):
                groups.primitives.append(item)
            elif item.properties or item.additionalProperties or item.type == "object":
                groups.objects.append(item)
            elif item.allOf or item.anyOf or item.oneOf:
                nested_type = self._build_lightweight_type(item, depth + 1, visited, max_depth, max_union_elements)
                if nested_type is None:  # pragma: no cover
                    return None
                if nested_type.reference:  # pragma: no cover
                    groups.ref_data_types.append(nested_type)
                else:
                    _rw_split.unregister_temporary_type(self, nested_type)
                    groups.primitives.append(item)
            elif item.enum:  # pragma: no cover
                groups.primitives.append(item)
            elif item.has_constraint:
                groups.constraint_only.append(item)
        return groups

    def _build_allof_type(  # noqa: PLR0911, PLR0912, PLR0913, PLR0917
        self,
        allof_items: Sequence[JsonSchemaObject | bool],
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
        warn_on_ref_constraints: bool = True,  # noqa: FBT001, FBT002
    ) -> DataType | None:
        """Build a DataType from allOf schema items."""
        if self._contains_false_schema(allof_items):
            return None
        allof_effective_items = [item for item in allof_items if isinstance(item, JsonSchemaObject)]
        if not allof_effective_items:
            return DataType(type=ANY, import_=IMPORT_ANY)
        if len(allof_effective_items) == 1:
            item = allof_effective_items[0]
            if item.ref:
                return self.get_ref_data_type(item.ref)
            return self._build_lightweight_type(item, depth + 1, visited, max_depth, max_union_elements)

        if (
            groups := self._group_allof_items(allof_effective_items, depth, visited, max_depth, max_union_elements)
        ) is None:
            return None
        ref_items, ref_data_types, primitive_items, constraint_only_items, object_items = groups

        if ref_items or ref_data_types:
            ignored_count = len(primitive_items) + len(constraint_only_items)
            if warn_on_ref_constraints and (primitive_items or object_items) and ignored_count > 0:
                warn(
                    f"allOf combines $ref with {ignored_count} constraint(s) that will be ignored "
                    f"in inherited field type resolution. Consider defining constraints in the referenced schema.",
                    stacklevel=3,
                )
            return ref_data_types[0] if ref_data_types else self.get_ref_data_type(cast("str", ref_items[0].ref))

        if primitive_items and not object_items:
            all_primitives = primitive_items + constraint_only_items
            merged_schema = self._merge_primitive_schemas(all_primitives)
            return self._build_lightweight_type(merged_schema, depth + 1, visited, max_depth, max_union_elements)

        if object_items:
            additional_props_types: list[DataType] = []

            for obj_item in object_items:
                if isinstance(obj_item.additionalProperties, JsonSchemaObject):
                    ap_type = self._build_lightweight_type(
                        obj_item.additionalProperties, depth + 1, visited, max_depth, max_union_elements
                    )
                    if ap_type:
                        additional_props_types.append(ap_type)

            if additional_props_types:
                best_type = additional_props_types[0]
                for ap_type in additional_props_types[1:]:  # pragma: no branch
                    is_better = best_type.type == ANY and ap_type.type != ANY
                    is_better = is_better or (ap_type.reference and not best_type.reference)
                    if is_better:  # pragma: no cover
                        best_type = ap_type
                return self.data_type(data_types=[best_type], is_dict=True)

            return self.data_type(data_types=[DataType(type=ANY, import_=IMPORT_ANY)], is_dict=True)

        return None

    def _build_lightweight_item_type(
        self,
        item_schema: JsonSchemaObject | bool,  # noqa: FBT001
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
    ) -> DataType | None:
        if item_schema is False:
            return None  # pragma: no cover
        if item_schema is True:
            return DataType(type=ANY, import_=IMPORT_ANY)
        if item_schema.ref:
            return self.get_ref_data_type(item_schema.ref)
        return self._build_lightweight_type(item_schema, depth + 1, visited, max_depth, max_union_elements) or DataType(
            type=ANY, import_=IMPORT_ANY
        )

    def _is_false_schema_item(self, item: JsonSchemaObject | bool) -> bool:  # noqa: FBT001
        return _lightweight_types.is_false_schema_item(self, item)

    def _contains_false_schema(self, items: Iterable[JsonSchemaObject | bool]) -> bool:
        return _lightweight_types.contains_false_schema(self, items)

    def _unvisited_ref_target(
        self, ref: str, visited_refs: frozenset[_TargetNode]
    ) -> tuple[JsonSchemaObject, frozenset[_TargetNode]] | None:
        """Return the schema ``ref`` names and its node, None once visited (``parser/_lightweight_types.py``)."""
        return _lightweight_types.unvisited_ref_target(self, ref, visited_refs)

    def _schema_requires_model_type(
        self,
        item: JsonSchemaObject,
        *,
        resolve_ref: bool = False,
        visited_refs: frozenset[_TargetNode] | None = None,
    ) -> bool:
        """Return whether a schema describes a model-shaped object."""
        return _all_of_values.schema_requires_model_type(self, item, resolve_ref=resolve_ref, visited_refs=visited_refs)

    def _schema_has_own_value_keywords(self, schema: JsonSchemaObject) -> bool:
        return _all_of_values.schema_has_own_value_keywords(self, schema)

    def _without_allof_keywords(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        return _all_of_values.without_allof_keywords(self, schema)

    def _enforced_value_keywords(
        self, schema: JsonSchemaObject
    ) -> tuple[frozenset[str], bool, dict[str, Any], dict[str, Any]]:
        """Return what a value schema enforces: its types, null, the constraints its type takes and its other keywords.

        The other keywords (``format``, ``enum`` and item schemas) are compared as written.
        """
        types = frozenset(declared_types(schema.type))
        constraints = self._temporary_constraint_values(schema, self._build_lightweight_type(schema))
        written = schema.model_dump(
            include={"format", "enum", *_ALL_OF_VALUE_ITEM_KEYWORDS}, exclude_unset=True, by_alias=True
        )
        return (
            types,
            bool(self._null_facts(schema, "enforced-value").admits_null),
            {key: value.value if isinstance(value, UnionIntFloat) else value for key, value in constraints.items()},
            written,
        )

    def _build_lightweight_type(  # noqa: PLR0911, PLR0912
        self,
        schema: JsonSchemaObject,
        depth: int = 0,
        visited: frozenset[int] | None = None,
        max_depth: int = _LIGHTWEIGHT_MAX_DEPTH,
        max_union_elements: int = _LIGHTWEIGHT_MAX_UNION_ELEMENTS,
    ) -> DataType | None:
        """Build a DataType from schema without generating models."""
        if depth > max_depth:  # pragma: no cover
            return None
        if visited is None:
            visited = frozenset()

        schema_id = id(schema)
        if schema_id in visited:  # pragma: no cover
            return None
        visited |= {schema_id}

        if schema.ref:
            return self.get_ref_data_type(schema.ref)

        if "const" in schema.extras:
            return self._get_const_data_type(schema.extras["const"], schema)

        if schema.enum:
            return self._enum_literal(schema)

        if schema.is_array:
            return _lightweight_types.build_lightweight_array_type(
                self, schema, depth, visited, max_depth, max_union_elements
            )

        if schema.allOf:
            return self._build_allof_type(
                _all_of_values.allof_lightweight_items(self, schema), depth, visited, max_depth, max_union_elements
            )

        if isinstance(schema.type, list) or (schema.type and schema.type != "object"):
            return self.get_data_type(schema)

        combined_items = schema.anyOf or schema.oneOf
        if combined_items:
            if len(combined_items) > max_union_elements:  # pragma: no cover
                return None
            data_types: list[DataType] = []
            for item in combined_items:
                if self._is_false_schema_item(item):
                    continue
                if item is True:
                    data_types.append(DataType(type=ANY, import_=IMPORT_ANY))
                    continue
                if not isinstance(item, JsonSchemaObject):  # pragma: no cover
                    continue
                if item.ref:  # pragma: no cover
                    data_types.append(self.get_ref_data_type(item.ref))
                else:
                    item_type = self._build_lightweight_type(item, depth + 1, visited, max_depth, max_union_elements)
                    if item_type is None:  # pragma: no cover
                        return None
                    data_types.append(item_type)
            if not data_types:
                return None
            if len(data_types) == 1:  # pragma: no cover
                return data_types[0]
            return build_union(self.data_type, data_types)

        if isinstance(schema.additionalProperties, JsonSchemaObject):  # pragma: no cover
            value_type = self._build_lightweight_type(
                schema.additionalProperties, depth + 1, visited, max_depth, max_union_elements
            )
            if value_type is None:
                value_type = DataType(type=ANY, import_=IMPORT_ANY)
            return self.data_type(data_types=[value_type], is_dict=True)

        if schema.properties or schema.type == "object":
            return self.data_type(data_types=[DataType(type=ANY, import_=IMPORT_ANY)], is_dict=True)

        return None

    def _is_list_with_any_item_type(self, data_type: DataType | None) -> bool:
        """Return True when data_type represents List[Any], nested lists included (``parser/_lightweight_types.py``)."""
        return _lightweight_types.is_list_with_any_item_type(self, data_type)

    def _get_inherited_schema_types(
        self,
        schema: JsonSchemaObject,
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> frozenset[str]:
        """Infer JSON instance types accepted by an inherited schema."""
        return _inherited_schemas.get_inherited_schema_types(self, schema, parent_ref, active)

    def _select_inherited_distributed_shape(
        self,
        child: dict[str, Any],
        distributed_fields: frozenset[str],
        parent_types: frozenset[str],
    ) -> dict[str, Any]:
        """Select child keywords that apply to one inherited JSON type branch."""
        return _inherited_schemas.select_inherited_distributed_shape(self, child, distributed_fields, parent_types)

    def _remove_inherited_distributed_shape(
        self,
        child: dict[str, Any],
        distributed_fields: frozenset[str],
    ) -> dict[str, Any]:
        """Remove distributed keywords while preserving unrelated extension values."""
        return _inherited_schemas.remove_inherited_distributed_shape(self, child, distributed_fields)

    def _drop_incompatible_inherited_constraints(
        self,
        child: dict[str, Any],
        parent_types: frozenset[str],
    ) -> dict[str, Any]:
        """Avoid applying schema constraints to incompatible Python value types."""
        return _inherited_schemas.drop_incompatible_inherited_constraints(self, child, parent_types)

    def _is_unconstrained_inherited_schema(self, schema: object) -> bool:
        """Return whether a raw subschema adds no validation to an inherited type."""
        return _inherited_schemas.is_unconstrained_inherited_schema(self, schema)

    def _remove_unconstrained_compositions(self, schema_dict: dict[str, Any]) -> dict[str, Any]:
        """Remove composition branches that make an inherited override universally valid."""
        return _inherited_schemas.remove_unconstrained_compositions(self, schema_dict)

    def _get_flattenable_inherited_constraint_items(
        self,
        keyword: str,
        raw_items: list[Any],
    ) -> list[dict[str, Any]] | None:
        """Return normalized constraint-only branches when composition flattening is equivalent."""
        return _inherited_schemas.get_flattenable_inherited_constraint_items(self, keyword, raw_items)

    def _normalize_inherited_constraint_compositions(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        """Flatten constraint-only compositions before resolving their inherited type."""
        return _inherited_schemas.normalize_inherited_constraint_compositions(self, schema)

    def _is_inherited_type_narrowing(
        self,
        child_types: frozenset[str],
        parent_types: frozenset[str],
    ) -> bool:
        """Return whether explicit child JSON types are contained by the inherited types."""
        return _inherited_schemas.is_inherited_type_narrowing(self, child_types, parent_types)

    def _is_partial_inherited_property(
        self,
        child: JsonSchemaObject,
        parent: JsonSchemaObject,
        context: tuple[str, frozenset[_TargetNode]] = ("", frozenset()),
    ) -> bool:
        """Return whether a child schema only narrows metadata/constraints of its inherited type."""
        return _inherited_schemas.is_partial_inherited_property(self, child, parent, context)

    def _is_partial_inherited_nested_value(
        self,
        child: object,
        parent: object,
        *,
        parent_tail: object = True,
        context: tuple[str, frozenset[_TargetNode]] = ("", frozenset()),
        schema_map: bool = False,
    ) -> bool:
        """Compare one nested schema keyword using JSON Schema intersection semantics."""
        return _inherited_schemas.is_partial_inherited_nested_value(
            self, child, parent, parent_tail=parent_tail, context=context, schema_map=schema_map
        )

    def _mark_inherited_materialized_type_shapes(self, schema: JsonSchemaObject) -> None:
        """Mark synthesized schemas whose container constraints need a root wrapper."""
        return _inherited_schemas.mark_inherited_materialized_type_shapes(self, schema)

    def _resolve_inherited_parent_property(
        self,
        schema: JsonSchemaObject,
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> tuple[JsonSchemaObject, str, frozenset[_TargetNode]]:
        """Resolve pure property references before comparing nested override shapes.

        A reference is cached by the key it resolves to, which its edge gives wherever the schema is read.
        """
        return _inherited_schemas.resolve_inherited_parent_property(self, schema, parent_ref, active)

    def _merge_inherited_union_branch(
        self,
        parent: object,
        child: dict[str, Any],
        distributed_fields: frozenset[str],
        context: tuple[str, frozenset[_TargetNode]],
        *,
        excludes_null: bool,
    ) -> object:
        """Apply type-specific child keywords to one compatible inherited branch."""
        return _inherited_schemas.merge_inherited_union_branch(
            self, parent, child, distributed_fields, context, excludes_null=excludes_null
        )

    def _merge_inherited_type_shape_dict(
        self,
        parent: dict[str, Any],
        child: dict[str, Any],
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> dict[str, Any]:
        """Fill missing type-shape keywords while keeping child validation precedence."""
        return _inherited_schemas.merge_inherited_type_shape_dict(self, parent, child, parent_ref, active)

    def _inherited_override_row(self, parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """Return ``parser/_intersection.py:inherited_override_row`` under the merge mode of this parse.

        An override drops the inherited ``nullable`` by the null rule ``inherited-typed-without-null``, read from its
        own ``type`` and ``nullable`` (``parser/_null_facts.py:dump_null_sources``).
        """
        return _inherited_schemas.override_row(self, parent, child)

    def _inherited_override_schema(
        self, parent: JsonSchemaObject, child: JsonSchemaObject, parent_ref: str, where: str
    ) -> JsonSchemaObject:
        """Return the schema of a property ``child`` that overrides the inherited property ``parent``.

        The row of ``--allof-merge-mode`` decides: ``none`` writes the child's constraints on the inherited type shape
        (``_merge_inherited_type_shape_dict``) where the child only narrows it, and keeps the child otherwise; ``all``
        and ``constraints`` give the intersection of both (``intersect_keywords``) over their presentation row
        (``parser/_intersection.py:inherited_override_row``).
        """
        return _inherited_schemas.inherited_override_schema(self, parent, child, parent_ref, where)

    def _get_deferred_inherited_property_names(
        self,
        source_obj: JsonSchemaObject,
        parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]],
    ) -> frozenset[str]:
        """Find partial properties that need canonical generated-type resolution."""
        return _inherited_schemas.get_deferred_inherited_property_names(self, source_obj, parent_properties)

    def _get_deferred_inherited_parse_object(
        self,
        source_obj: JsonSchemaObject,
        deferred_property_names: frozenset[str],
    ) -> JsonSchemaObject:
        """Strip nested type work that late canonical inheritance will replace."""
        return _inherited_schemas.get_deferred_inherited_parse_object(self, source_obj, deferred_property_names)

    def _sanitize_untyped_boolean_inherited_property(
        self,
        child: JsonSchemaObject,
    ) -> JsonSchemaObject | None:
        """Drop type-specific constraints when a boolean parent supplies no usable type."""
        return _inherited_schemas.sanitize_untyped_boolean_inherited_property(self, child)

    def _merge_properties_with_parent_constraints(
        self,
        child_obj: JsonSchemaObject,
        base_classes: list[Reference],
        parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]] | None = None,
        deferred_property_names: frozenset[str] | None = None,
    ) -> JsonSchemaObject:
        """Merge child properties with parent property constraints for allOf inheritance."""
        return _inherited_schemas.merge_properties_with_parent_constraints(
            self, child_obj, base_classes, parent_properties, deferred_property_names
        )

    @contextmanager
    def _inherited_ref_context(self, resolved_ref: str) -> Generator[None, None, None]:
        """Resolve nested references relative to the inherited schema's own file and schema resource.

        The inherited schema joins the dynamic scope of the schema inheriting it, so its $dynamicRef
        follow the $dynamicAnchor bindings of that scope.
        """
        bindings = (
            _schema_refs.current_dynamic_scope(self)
            if self._has_embedded_schema_resources or self._schema_resource_registry.dynamic_anchors
            else None
        )
        with (
            self._ref_file_context(resolved_ref.partition("#")[0]),
            nullcontext() if bindings is None else self._dynamic_scope_context([resolved_ref], bindings),
        ):
            yield

    def _get_allof_parent_references(
        self,
        schema: JsonSchemaObject,
        *,
        inherited: Iterable[Reference] = (),
        defining_ref: str | None = None,
    ) -> list[Reference]:
        """Collect effective allOf parents once, preserving declaration precedence."""
        return _inherited_schemas.get_allof_parent_references(
            self, schema, inherited=inherited, defining_ref=defining_ref
        )

    def _linearize_inherited_schema_refs(self, base_classes: list[Reference]) -> tuple[str, ...]:
        """Return raw inherited schemas in the same C3 order as generated models.

        The parents of each schema are those ``_inherited_schemas.inherited_schema_parents`` reads.
        """
        return _inherited_schemas.linearize_inherited_schema_refs(self, base_classes)

    def _get_inherited_property_map(
        self,
        base_classes: list[Reference],
    ) -> dict[str, tuple[JsonSchemaObject | bool, str]]:
        """Build the effective raw property map once in inherited C3 order."""
        return _inherited_schemas.get_inherited_property_map(self, base_classes)

    def _adds_required_names(
        self, names: Iterable[str], base_required: frozenset[str] | Callable[[], frozenset[str]]
    ) -> bool:
        """Return whether a site requires a name that the one model its allOf names does not, so it is no alias of it.

        ``base_required`` holds the names that model requires, or reads them. False under ``--force-optional``, which
        writes no required field, and under ``legacy-all-of-required-names-dropped``, where the site is that model.
        """
        if (
            "legacy-all-of-required-names-dropped" in self._legacy_intersection
            or self.force_optional_for_required_fields
            or not (wanted := set(names))
        ):
            return False
        return not wanted <= (base_required if isinstance(base_required, frozenset) else base_required())

    def _ref_required_names(self, ref: str) -> frozenset[str]:
        """Return the names the schema ``ref`` names requires, as its model requires them as a base."""
        return _inherited_schemas.required_names_at(self, self._ref_path(ref))

    def _get_inherited_required_names(self, base_classes: list[Reference]) -> frozenset[str]:
        """Collect required names across the effective inherited allOf graph."""
        return _inherited_schemas.get_inherited_required_names(self, base_classes)

    def _get_inline_required_names(self, schema: JsonSchemaObject) -> tuple[str, ...]:
        """Collect required names in declaration order from one schema and inline allOf items."""
        return _inherited_schemas.get_inline_required_names(self, schema)

    def _get_inline_property_names(self, schema: JsonSchemaObject) -> frozenset[str]:
        """Collect properties declared directly by one schema and its inline allOf items."""
        return _inherited_schemas.get_inline_property_names(self, schema)

    def _get_inherited_property_order(self, base_classes: list[Reference]) -> tuple[str, ...]:
        """Return raw inherited property names in ancestor-first declaration order."""
        return _inherited_schemas.get_inherited_property_order(self, base_classes)

    def _parse_inherited_schema_fields(
        self,
        reference: Reference,
        schema: JsonSchemaObject,
        parent_refs: list[Reference],
        parent_fields: list[DataModelFieldBase],
    ) -> list[DataModelFieldBase]:
        """Parse one forward schema's own fields in its defining scope, at its own path.

        The inline models the fields name are the ones the schema's own parse builds, under the same names.
        """
        return _inherited_schemas.parse_inherited_schema_fields(self, reference, schema, parent_refs, parent_fields)

    def _iter_inherited_schema_objects(
        self, base_classes: list[Reference], visited: frozenset[_TargetNode]
    ) -> Iterator[tuple[JsonSchemaObject, frozenset[_TargetNode], str]]:
        """Yield inherited schema objects not visited yet, with the visited schema nodes they add to."""
        return _inherited_schemas.iter_inherited_schema_objects(self, base_classes, visited)

    def _load_inherited_schema_object(self, resolved_ref: str) -> JsonSchemaObject:
        """Load and cache a schema used only for inherited field inspection."""
        return _inherited_schemas.load_inherited_schema_object(self, resolved_ref)

    def _get_inherited_field_type(
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[_TargetNode] | None = None
    ) -> DataType | None:
        """Get the data type for an inherited property from parent schemas.

        Recursively traverses the inheritance chain when a parent property
        doesn't have type information but the parent itself inherits from another schema.
        """
        if inherited_field := _inherited_schemas.get_inherited_field(self, prop_name, base_classes):
            return self._copy_model_type(inherited_field.data_type)

        if visited is None:
            visited = frozenset()

        for parent_schema, next_visited, parent_ref in self._iter_inherited_schema_objects(base_classes, visited):
            result: DataType | None = None
            if parent_schema.properties:
                prop_schema = parent_schema.properties.get(prop_name)
                if isinstance(prop_schema, JsonSchemaObject):
                    with self._inherited_ref_context(parent_ref):
                        result = self._build_lightweight_type(prop_schema)
            # In case of a missing type, continue searching up the inheritance chain
            if result is not None and not (result.type == ANY or self._is_list_with_any_item_type(result)):
                return result

            parent_result: DataType | None = None
            if parent_schema.allOf:
                grandparent_refs = [
                    self.model_resolver.add_ref(self._ref_path(ref), resolved=True)
                    for ref in _inherited_schemas.iter_allof_refs(self, parent_schema)
                ]
                if grandparent_refs:
                    parent_result = self._get_inherited_field_type(prop_name, grandparent_refs, next_visited)
                    if parent_result is not None:
                        return parent_result
                    return result

        return None

    def _get_inherited_field_schema(
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[_TargetNode] | None = None
    ) -> JsonSchemaObject | None:
        """Get the schema for an inherited property from parent schemas."""
        return _inherited_schemas.get_inherited_field_schema(self, prop_name, base_classes, visited)

    def _merge_all_of_object(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Merge allOf items when they share object properties to avoid duplicate models.

        Skip merging when there is exactly one $ref (inheritance with property overrides).
        Continue merging when multiple $refs have conflicting property definitions to avoid MRO issues.
        Child property overrides (obj.properties) are not considered conflicts.
        """
        return _all_of_values.merge_all_of_object(self, obj)

    def _merge_all_of_root_validation_keywords(self, merged: dict[str, Any], sources: list[JsonSchemaObject]) -> None:
        """Preserve literal and item intersections while materializing a value reference.

        ``merged`` stands for the last source: the allOf holder, or the node whose keywords win. Literals that no value
        meets in every source keep those of the first source that has some, with a warning.
        """
        literal_sets = [values for source in sources if (values := self._schema_literal_values(source)) is not None]
        if literal_sets:
            literal_values = reduce(intersect_enum_values, literal_sets[1:], list(literal_sets[0]))
            if not literal_values:
                self._warn_unsatisfiable(
                    named_location(sources[-1].schema_location, "allOf"),
                    tuple(
                        keyword
                        for keyword in ("enum", "const")
                        if any(
                            keyword in (source.model_fields_set if keyword == "enum" else source.extras)
                            for source in sources
                        )
                    ),
                    _ALL_OF_PARTS,
                )
                literal_values = list(next((values for values in literal_sets if values), ()))
            merged["enum"] = literal_values
        item_schemas = [source.items for source in sources if source.items is not None]
        if len(item_schemas) > 1:
            if False not in item_schemas and (
                positional_lengths := [len(child) for child in item_schemas if isinstance(child, list)]
            ):
                positional_items = []
                for index in range(max(positional_lengths) + 1):
                    positional_sources = []
                    for source in sources:
                        child = source.items
                        if isinstance(child, list):
                            child = child[index] if index < len(child) else source.additionalItems
                        positional_sources.append(self._schema_object({"items": True if child is None else child}))
                    positional_schema: dict[str, Any] = {}
                    self._merge_all_of_root_validation_keywords(positional_schema, positional_sources)
                    positional_items.append(positional_schema["items"])
                merged["items"] = positional_items[:-1]
                merged["additionalItems"] = positional_items[-1]
                return
            merged["items"] = _all_of_values.merge_all_of_root_value_nodes(
                self, cast("list[JsonSchemaObject | bool]", item_schemas)
            )

    def _is_all_of_mapping_item(self, schema: JsonSchemaObject) -> bool:
        """Return whether an allOf item is an object map, which a mapping-shaped allOf merges into one root map."""
        return _all_of_values.is_all_of_mapping_item(self, schema)

    def _merge_all_of_root_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Unwrap one value schema or merge mapping-shaped allOf items into a root schema."""
        return _all_of_values.merge_all_of_root_schema(self, obj)

    def _merge_value_schemas(self, schemas: list[JsonSchemaObject]) -> JsonSchemaObject | None:
        """Merge schemas that constrain one value, unless their types, formats or literals disagree."""
        types = [schema.type for schema in schemas if schema.type]
        if (
            not (
                all(type_ == types[0] for type_ in types)
                or all(isinstance(type_, str) and type_ in {"integer", "number"} for type_ in types)
            )
            or sum(self._schema_literal_values(schema) is not None for schema in schemas) > 1
            or len({schema.format for schema in schemas if schema.format}) > 1
        ):
            return None
        merged: dict[str, Any] = {}
        for schema in schemas:
            merged = concatenating_row(
                merged,
                schema.model_dump(
                    exclude={"allOf", *JsonSchemaObject.__constraint_fields__}, exclude_unset=True, by_alias=True
                ),
            )
        if types:
            merged["type"] = JsonSchemaParser._first_typed_schema_dict(schemas)["type"]
        self._merge_schema_constraints(
            merged, schemas, intersect=intersects_all_of_constraints(self.allof_merge_mode.value)
        )
        return self._schema_object(merged)

    def _satisfiable_all_of_value_parts(self, obj: JsonSchemaObject) -> list[JsonSchemaObject] | None:
        """Return the parts of an allOf site whose members each describe one value, references resolved, else None.

        A site whose parts no value meets in common gets None, and a warning unless ``legacy-all-of-site-form`` is kept:
        it keeps the form it is written in, and a form that cannot be generated takes its first part.
        """
        if (parts := _all_of_values.all_of_value_parts(self, obj)) is None:
            return None
        unsatisfiable = _all_of_values.unsatisfiable_value_keywords(self, parts)
        if "legacy-all-of-site-form" not in self._legacy_intersection:
            self._warn_unsatisfiable(
                named_location(obj.schema_location, "allOf"),
                unsatisfiable,
                _ALL_OF_PARTS,
            )
        return None if unsatisfiable else parts

    def _read_false_all_of_values(self, obj: JsonSchemaObject, *, lightweight: bool = False) -> JsonSchemaObject:
        """Return the site ``obj`` with the schema that types its values written ``false`` where it is a false allOf.

        That schema is the additionalProperties of a map that types values with it: an object without properties or
        patternProperties, the keys no pattern matches under --use-single-dict-for-object-maps (``_other_keys_schema``)
        where no patternProperties validator owns them, and a type array of object under
        --use-typed-nullable-map-values (``_typed_nullable_map_value_schema``). It is also the items of an array,
        unless the array is a tuple of no items under --use-tuple-for-fixed-length-arrays
        (``_get_fixed_length_homogeneous_tuple_length``). Where it is ``false`` written as an allOf
        (``_is_false_all_of``), the site is read as the same site with ``false``: the map admits no key it types and the
        array no item past its prefixItems. Where no value is typed with it, it keeps its output. The positions of an
        array (``_read_false_all_of_array_positions``) and the patternProperties and propertyNames of a map
        (``_read_false_all_of_map_schemas``) are read the same way. ``lightweight`` reads the array positions as the
        lightweight type of a TypedDict ``extra_items`` types them (``_build_lightweight_array_type``). A site reads
        none of them while no loaded document holds an ``allOf`` that lists ``false``
        (``SchemaGraphBuilder.holds_false_all_of``).
        """
        return _all_of_values.read_false_all_of_values(self, obj, lightweight=lightweight)

    def _site_schema(self, obj: JsonSchemaObject, *, merge_ref_siblings: bool = False) -> JsonSchemaObject:
        """Return ``obj`` as a site reads it: an array ``allOf`` as one value schema, a false allOf as ``false``.

        With ``merge_ref_siblings``, a ``$ref`` whose sibling keywords are parts of it is merged with them first.
        """
        return _all_of_values.site_schema(self, obj, merge_ref_siblings=merge_ref_siblings)

    def _combined_schema_members(
        self, obj: JsonSchemaObject, target_attribute_name: str
    ) -> tuple[list[JsonSchemaObject], list[int]]:
        """Return the branches ``parse_combined_schema`` parses for the members of a combined schema, and the refs.

        A member is mixed with the keywords of its parent (``all_of_member_row``), so it is read with them where it is
        parsed (``parse_item`` gets the parent as its parent), and a member that is a ``$ref`` is merged with its
        siblings first and stays as it is. A member that is false, a null the ``type`` beside it excludes, a member the
        other combinator beside it leaves no branch of (``_combined_branch_outers``) or a local ``$ref`` to false is no
        branch. The request/response walk reads the same branches (``_typed_model_children``).
        """
        default_left_off = self.config.keep_field_defaults_off_branch_roots and obj.has_default
        base_object = obj.model_dump(
            exclude={
                target_attribute_name,
                "title",
                *(("default",) if self.config.keep_field_defaults_off_branch_roots else ()),
            },
            exclude_unset=True,
            by_alias=True,
        )
        combined_schemas: list[JsonSchemaObject] = []
        refs = []
        use_builtin_false_ref_facts: bool | None = None
        items = getattr(obj, target_attribute_name, [])
        members = [item for item in items if not self._is_false_schema_item(item)]
        dropped = {id(member) for member in _excluded_null_members(obj, members, self._null_mode)}
        outers = self._combined_branch_outers(
            obj, target_attribute_name, [member for member in members if id(member) not in dropped], base_object
        )
        dropped.update(member_id for member_id, outer in outers.items() if outer is None)
        dropped_before = 0
        for index, target_attribute in enumerate(items):
            if self._is_false_schema_item(target_attribute):
                continue
            if id(target_attribute) in dropped:
                dropped_before += 1
                continue
            ref_index = index - dropped_before
            if target_attribute is True:
                combined_schemas.append(
                    _mark_copied_nullable(
                        self._schema_object(base_object), base_object, None, default_left_off=default_left_off
                    )
                )
                continue
            member = target_attribute
            if target_attribute.ref:
                if get_ref_type(target_attribute.ref) == JSONReference.LOCAL:
                    if use_builtin_false_ref_facts is None:
                        use_builtin_false_ref_facts = self._uses_builtin_false_ref_facts()
                    if self._is_local_ref_false_schema(
                        target_attribute.ref,
                        use_builtin_facts=use_builtin_false_ref_facts,
                    ):
                        continue
                if _merges_ref_siblings(target_attribute):
                    member = self._merge_ref_with_schema(target_attribute)
                    _inherited_schemas.preserve_inherited_materialized_type_shape(self, target_attribute, member)
                if member.ref and (beside := self._value_ref_branch_with_site(base_object, member)) is not None:
                    member = beside
                if member.ref:
                    combined_schemas.append(member)
                    refs.append(ref_index)
                    continue
            combined_schemas.append(
                _mark_copied_nullable(
                    _locate(
                        _inherited_schemas.preserve_inherited_materialized_type_shape(
                            self,
                            target_attribute,
                            self._schema_object(
                                self._combined_branch_row(
                                    outers.get(id(target_attribute)) or base_object,
                                    member.model_dump(exclude_unset=True, by_alias=True),
                                    named_location(target_attribute.schema_location, target_attribute_name),
                                    target_attribute_name,
                                ),
                            ),
                        ),
                        target_attribute.schema_location,
                    ),
                    base_object,
                    member,
                    default_left_off=default_left_off,
                )
            )
        return combined_schemas, refs

    def _value_ref_branch_with_site(self, outer: dict[str, Any], branch: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return a ``$ref`` branch read with the keywords beside its ``anyOf`` or ``oneOf``, where they are its parts.

        They are where the site writes value keywords and the branch names no object model, in every dialect: they
        are parts of the conjunction, not keywords beside the ``$ref`` in the document. The branch is then the schema
        it names merged with them as their intersection (``_CONJUNCTION_PARTS_KEY``), so a keyword of both keeps the
        tighter value, which ``_combined_schema_members`` reads as an inline branch. None under
        ``legacy-ref-branch-without-site-parts``, and where the merge keeps the reference.
        """
        if (
            "legacy-ref-branch-without-site-parts" in self._legacy_intersection
            or not self._schema_has_own_value_keywords(site := self._schema_object(outer))
            or self._schema_requires_model_type(branch, resolve_ref=True)
        ):
            return None
        conjunction = site.model_copy(update={"ref": branch.ref})
        conjunction.__dict__[_CONJUNCTION_PARTS_KEY] = True
        merged = self._merge_ref_with_schema(conjunction)
        return None if merged.ref else merged

    def _combined_branch_outers(
        self,
        obj: JsonSchemaObject,
        keyword: str,
        members: Sequence[JsonSchemaObject | bool],
        outer: dict[str, Any],
    ) -> dict[int, dict[str, Any] | None]:
        """Return, by member id, the keywords beside ``keyword`` that a member's row reads where they are not ``outer``.

        A schema with both ``anyOf`` and ``oneOf`` admits the values both admit, so the row of a member holds the other
        one beside it (``_combined_branch_row``). Unless ``legacy-combined-branch-row`` is kept, a branch of it whose
        ``type`` admits none of the types the member's ``type`` declares (``_types_exclude``) has no value in common
        with the member, and the member's row keeps the other branches. A member left with no branch that holds a value
        is no branch (None), unless every member is one: the schema keeps them rather than admit no value, as
        ``_excluded_null_members`` does. A member that writes its own ``anyOf`` or ``oneOf``, a ``$ref`` or no
        ``type``, and one whose kept branches are all ``$ref`` beside false ones, keep ``outer``.
        """
        other = "oneOf" if keyword == "anyOf" else "anyOf"
        if "legacy-combined-branch-row" in self._legacy_intersection or not (branches := getattr(obj, other)):
            return {}
        outers: dict[int, dict[str, Any] | None] = {}
        for member in members:
            if (
                not isinstance(member, JsonSchemaObject)
                or member.ref
                or member.anyOf
                or member.oneOf
                or not (types := _declared_value_types(member))
            ):
                continue
            kept = [position for position, branch in enumerate(branches) if not _types_exclude(types, branch)]
            if len(kept) == len(branches):
                continue
            holders = [branches[position] for position in kept if not self._is_false_schema_item(branches[position])]
            if not holders:
                outers[id(member)] = None
            elif any(isinstance(holder, bool) or not holder.ref for holder in holders):
                outers[id(member)] = {**outer, other: [outer[other][position] for position in kept]}
        if len(outers) == len(members) and all(value is None for value in outers.values()):
            return {}
        return outers

    def _combined_branch_row(
        self, outer: dict[str, Any], branch: dict[str, Any], where: str, keyword: str
    ) -> dict[str, Any]:
        """Return the schema a branch of a combined schema is parsed as, from the keywords beside it and its own.

        The row is their merge (``all_of_member_row`` without bounds), in which the branch's value of a keyword wins.
        Unless ``legacy-combined-branch-row`` is kept, the branch is the intersection of both over that row, keyword
        by keyword, with a warning naming ``where`` for keywords without a common value; neither is the site's own, so
        a ``type`` keeps null only where each of them that has a ``type`` declares it. ``nullable`` is no part of it:
        the row's value stays, so the union reads the null of a copied ``nullable`` as the null rule does (row
        ``combined-branch`` of ``parser/_null_facts.py``).
        """
        row = all_of_member_row(outer, branch, intersect_bounds=False)
        if "legacy-combined-branch-row" in self._legacy_intersection:
            return row
        intersection = _intersect_parts(
            self,
            [_without_nullable(outer), _without_nullable(branch)],
            _without_nullable(row),
            where=where,
            parts=(f"the keywords beside its {keyword} and its own keywords",),
            own_last=False,
        )
        return intersection.keywords | ({"nullable": row["nullable"]} if "nullable" in row else {})

    def parse_combined_schema(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        target_attribute_name: str,
    ) -> list[DataType]:
        """Parse combined schema (anyOf, oneOf, allOf) into a list of data types.

        Each member records the location of the branch it is parsed from as its origin (``reference.type_origin_of``),
        also the member that stands for a referenced branch. The model paths of ``$ref`` branches number the variants
        in union order, a branch a discriminator expanded counting its subtypes where the site distributes its
        ``allOf`` members.
        """
        combined_schemas, refs = self._combined_schema_members(obj, target_attribute_name)
        variant_keys: dict[int, str] = {}
        variant_names = (
            self._infer_union_variant_names(name, obj, combined_schemas, variant_keys)
            if self.infer_union_variant_names
            else None
        )
        if self._output_model_context.requires_tagged_union_discriminator:
            _union_tags.set_tagged_union_discriminator(self, obj, combined_schemas)
        parsed_schemas = _union_tags.parse_combined_schema_items(
            self, name, obj, path, combined_schemas, variant_names, variant_keys
        )
        if not parsed_schemas:
            _all_of_values.raise_unsatisfiable_schema(self, obj, path, target_attribute_name)
        special_path = get_special_path(f"{target_attribute_name}Common", path)
        distributes_expansions = (
            bool(obj.allOf) and "legacy-ref-branch-without-site-parts" not in self._legacy_intersection
        )
        data_types: list[DataType] = []
        position = 0
        for i, d in enumerate(parsed_schemas):
            branch = combined_schemas[i]
            location = branch.schema_location
            if i in refs and d.reference:
                data_types.append(
                    with_type_origin(
                        self._parse_ref_branch_model(
                            name,
                            obj.with_schema_location(location),
                            [*special_path, str(position)],
                            branch,
                            d.reference,
                        ),
                        location,
                    )
                )
                position += 1
            elif i in refs and distributes_expansions and (variants := _expanded_variants(d)):
                data_types.append(
                    with_type_origin(
                        build_union(
                            self.data_type,
                            [
                                with_type_origin(
                                    self._parse_ref_branch_model(
                                        name,
                                        obj.with_schema_location(location),
                                        [*special_path, str(position + j)],
                                        branch.model_copy(update={"ref": ref}),
                                        reference,
                                    ),
                                    location,
                                )
                                for j, (ref, reference) in enumerate(variants)
                            ],
                        ),
                        location,
                    )
                )
                position += len(variants)
            else:
                data_types.append(d)
                position += 1
        return data_types

    def _parse_ref_branch_model(
        self, name: str, obj: JsonSchemaObject, path: list[str], branch: JsonSchemaObject, reference: Reference
    ) -> DataType:
        """Parse the model of a ``$ref`` branch of ``obj``'s ``anyOf`` or ``oneOf``: the branch with the site's parts.

        It is the class of the branch with the ``properties`` and ``required`` of the site, and, unless
        ``legacy-ref-branch-without-site-parts`` is kept, the ``allOf`` members beside the union as further bases, as
        an inline branch merged with the site takes them: the ``allOf`` of the branch and those members with the
        site's other keywords (``parse_all_of``), the branch's class ``reference`` (``_BRANCH_REFERENCE_KEY``), a
        dynamic scope's specialization included, its base. A branch the site adds nothing to is the branch's own class;
        the reference of any other class records the branch's (``REF_BRANCH_SCHEMA_KEY``), whose schema path and
        mapping keys give the tag the class takes.
        ``parse_combined_schema`` calls it, where the site distributes its ``allOf`` members, for each subtype a
        discriminator expanded a ``$ref`` branch to (``_expanded_variants``), numbering the models in union order.
        """
        if obj.allOf and "legacy-ref-branch-without-site-parts" not in self._legacy_intersection:
            member = branch.model_copy()
            member.__dict__[_BRANCH_REFERENCE_KEY] = reference
            data_type = self.parse_all_of(
                name,
                obj.model_copy(update={"allOf": [member, *obj.allOf], "anyOf": [], "oneOf": []}),
                path,
                ignore_duplicate_model=True,
            )
        else:
            data_type = self._parse_object_common_part(
                name, obj, path, ignore_duplicate_model=True, fields=[], base_classes=[reference], required=[]
            )
        if (model_reference := data_type.reference) is not None and model_reference is not reference:
            model_reference.__dict__[REF_BRANCH_SCHEMA_KEY] = reference
        return data_type

    def parse_any_of(self, name: str, obj: JsonSchemaObject, path: list[str]) -> list[DataType]:
        """Parse anyOf schema into a list of data types."""
        return self.parse_combined_schema(name, obj, path, "anyOf")

    def parse_one_of(self, name: str, obj: JsonSchemaObject, path: list[str]) -> list[DataType]:
        """Parse oneOf schema into a list of data types."""
        return self.parse_combined_schema(name, obj, path, "oneOf")

    def _is_required_only_schema(
        self,
        item: JsonSchemaObject | bool,  # noqa: FBT001
        *,
        allow_empty: bool = False,
    ) -> TypeIs[JsonSchemaObject]:
        """Return whether a combined-schema branch is only a property presence rule."""
        return _schema_validators.is_required_only_schema(self, item, allow_empty=allow_empty)

    def _has_pattern_properties_validator(self, obj: JsonSchemaObject) -> bool:
        return _schema_validators.has_pattern_properties_validator(self, obj)

    def _has_property_count_validator(self, obj: JsonSchemaObject) -> bool:
        return _schema_validators.has_property_count_validator(self, obj)

    def _get_property_count_rule(self, obj: JsonSchemaObject) -> PropertyCountRule | None:
        return _schema_validators.get_property_count_rule(self, obj)

    def _should_parse_object_with_schema_validators(self, obj: JsonSchemaObject) -> bool:
        return _schema_validators.should_parse_object_with_schema_validators(self, obj)

    def _merge_conditional_properties(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        return _schema_validators.merge_conditional_properties(self, obj)

    def _field_input_names(self, field: DataModelFieldBase) -> tuple[str, ...]:
        return _schema_validators.field_input_names(self, field)

    def _get_input_names_by_property(
        self,
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
    ) -> dict[str, tuple[str, ...]]:
        return _schema_validators.get_input_names_by_property(self, fields, base_classes)

    def _required_group_input_names(
        self,
        groups: Sequence[Sequence[str]],
        names_by_property: dict[str, tuple[str, ...]],
    ) -> tuple[tuple[tuple[str, ...], ...], ...]:
        return _schema_validators.required_group_input_names(self, groups, names_by_property)

    def _schema_runtime_validation(self, reference_path: str) -> SchemaRuntimeValidation:
        runtime_validation = self.extra_template_data[reference_path].get("schema_runtime_validation")
        if not _is_internal_schema_runtime_validation(runtime_validation):
            runtime_validation = _make_internal_schema_runtime_validation()
            self.extra_template_data[reference_path]["schema_runtime_validation"] = runtime_validation
        return runtime_validation

    def _iter_schema_validation_sources(
        self,
        obj: JsonSchemaObject,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        *,
        include_references: bool = True,
    ) -> Iterator[JsonSchemaObject]:
        return _schema_validators.iter_schema_validation_sources(
            self, obj, visited_refs, include_references=include_references
        )

    def _schema_item_may_accept_container(
        self,
        item: JsonSchemaObject | bool,  # noqa: FBT001
        container_type: Literal["array", "object"],
        visited_refs: frozenset[_TargetNode],
    ) -> bool:
        return _schema_validators.schema_item_may_accept_container(self, item, container_type, visited_refs)

    def _schema_literal_values(self, obj: JsonSchemaObject) -> tuple[object, ...] | None:
        return _schema_validators.schema_literal_values(self, obj)

    def _unique_items_path_container_type(
        self,
        path: UniqueItemsPath,
    ) -> Literal["array", "object"]:
        return _schema_validators.unique_items_path_container_type(self, path)

    def _get_union_unique_items_branch_rules(
        self,
        items: Sequence[JsonSchemaObject | bool],
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode],
        property_input_names: Mapping[str, tuple[str, ...]] | None,
    ) -> tuple[list[tuple[JsonSchemaObject | bool, tuple[UniqueItemsRule, ...]]], list[UniqueItemsRule]]:
        return _schema_validators.get_union_unique_items_branch_rules(
            self, items, path, visited_refs, property_input_names
        )

    def _iter_union_unique_items_rules(
        self,
        items: Sequence[JsonSchemaObject | bool],
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode],
        *,
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
    ) -> Iterator[UniqueItemsRule]:
        return _schema_validators.iter_union_unique_items_rules(
            self, items, path, visited_refs, property_input_names=property_input_names
        )

    def _get_common_executable_unique_items_paths(
        self,
        data_types: Sequence[DataType],
        path: UniqueItemsPath,
        visited: set[int],
        *,
        filter_array_branches: bool = True,
    ) -> set[UniqueItemsPath]:
        return _schema_validators.get_common_executable_unique_items_paths(
            self, data_types, path, visited, filter_array_branches=filter_array_branches
        )

    def _data_type_may_accept_array(
        self,
        data_type: DataType,
        visited: frozenset[int] = frozenset(),
    ) -> bool:
        return _schema_validators.data_type_may_accept_array(self, data_type, visited)

    def _get_executable_unique_items_paths(
        self,
        data_type: DataType,
        path: UniqueItemsPath,
        visited: set[int] | None = None,
    ) -> set[UniqueItemsPath]:
        return _schema_validators.get_executable_unique_items_paths(self, data_type, path, visited)

    def _iter_unique_items_rules(
        self,
        obj: JsonSchemaObject,
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
        *,
        include_properties: bool = False,
    ) -> Iterator[UniqueItemsRule]:
        return _schema_validators.iter_unique_items_rules(
            self, obj, path, visited_refs, property_input_names, include_properties=include_properties
        )

    def _add_unique_items_validator(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
        *,
        is_root_model: bool,
    ) -> None:
        _schema_validators.add_unique_items_validator(
            self, reference_path, obj, fields, base_classes, is_root_model=is_root_model
        )

    def _collect_pattern_property_validators(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> tuple[list[tuple[str, DataType]], list[str], DataType | None, bool]:
        return _schema_validators.collect_pattern_property_validators(self, name, obj, path)

    def _plain_pattern_root_annotations(self, obj: JsonSchemaObject, patterns: list[tuple[str, DataType]]) -> bool:
        """Recognize inert root annotations only in the standard generation context."""
        if type(self) is not JsonSchemaParser or type(obj) is not JsonSchemaObject:
            return False
        if any(
            name not in {"title", "description"}
            and (
                not name.startswith("x-")
                or name.startswith("x-python-")
                or name in {"x-is-base-class", "x-is-classvar"}
            )
            for name in obj.extras
        ):
            return False
        config = self.config
        if any((
            config.model_extra_keys,
            config.model_extra_keys_without_x_prefix,
            config.validators,
            config.class_decorators,
            config.type_mappings,
            config.type_overrides,
            config.import_overrides,
            config.custom_class_name_generator,
            config.alias_generator,
        )):
            return False
        if (check_annotations := self.data_model_type.PLAIN_PATTERN_ROOT_CHECKER) is None:
            return False
        return check_annotations(
            self.data_model_type,
            self.data_model_root_type,
            self.data_model_field_type,
            type(self.data_type_manager),
            (data_type for _, data_type in patterns),
        )

    def _add_required_groups_validator(
        self,
        reference_path: str,
        keyword: Literal["anyOf", "oneOf", "not"],
        groups: Sequence[Sequence[str]],
        names_by_property: dict[str, tuple[str, ...]],
    ) -> None:
        _schema_validators.add_required_groups_validator(self, reference_path, keyword, groups, names_by_property)

    def _add_property_count_validator(self, reference_path: str, obj: JsonSchemaObject) -> None:
        _schema_validators.add_property_count_validator(self, reference_path, obj)

    def _add_conditional_validator(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        names_by_property: dict[str, tuple[str, ...]],
    ) -> None:
        _schema_validators.add_conditional_validator(self, reference_path, obj, names_by_property)

    def _add_schema_validators(  # noqa: PLR0913, PLR0917
        self,
        reference_path: str,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
        *,
        include_property_count: bool = True,
    ) -> None:
        _schema_validators.add_schema_validators(
            self, reference_path, name, obj, path, fields, base_classes, include_property_count=include_property_count
        )

    def _predicted_scope(self, name: str, path: list[str]) -> ClassScope:
        """Return the scope of the model at ``path`` by the name the parser adds it under (row ``predicted``)."""
        return self.model_resolver.class_scope("predicted", name, self.model_resolver.join_path(tuple(path)))

    def _parse_object_common_part(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        *,
        ignore_duplicate_model: bool,
        fields: list[DataModelFieldBase],
        base_classes: list[Reference],
        required: list[str],
        nullable: bool = False,
    ) -> DataType:
        with self._model_frame() as frame:  # noqa: PLR1702
            if self.generate_schema_validators:
                obj = self._merge_conditional_properties(obj)
            self._preload_property_refs_for_rw_models(obj)
            inherited_fields: dict[str, DataModelFieldBase] = {}
            inherited_properties: dict[str, tuple[JsonSchemaObject | bool, str]] = {}
            inherited_required_names: frozenset[str] = frozenset()
            if base_classes:
                base_classes[:] = _inherited_schemas.sort_inherited_base_classes_for_mro(self, base_classes)
                inherited_fields = _inherited_schemas.get_inherited_field_map(self, base_classes)
                inherited_properties = self._get_inherited_property_map(base_classes)
                inherited_required_names = self._get_inherited_required_names(base_classes)
            python_overrides = obj.python_field_overrides
            if python_overrides:
                inherited_properties = {
                    key: value for key, value in inherited_properties.items() if key not in python_overrides
                }
                inherited_required_names = inherited_required_names.difference(python_overrides)
            scope = self._predicted_scope(name, path)
            if obj.properties:
                fields.extend(
                    _inherited_schemas.parse_fields_beside_parents(
                        self, obj, base_classes, inherited_properties, path, scope
                    )
                )
            if base_classes:
                reserved_names = {field.name for field in fields if field.name}
                collected_inherited_fields: list[DataModelFieldBase] | None = None
                for index, field in enumerate(fields):
                    field_name = _field_source_name(field)
                    if field_name in python_overrides:
                        parent_name = python_overrides[cast("str", field_name)]
                        parent_field = inherited_fields.get(parent_name)
                        if parent_field is None and parent_name != field_name:
                            for resolved_ref in self._linearize_inherited_schema_refs(base_classes):
                                parent_schema = self._load_inherited_schema_object(resolved_ref)
                                parent_name = parent_schema.python_field_overrides.get(parent_name, parent_name)
                            if collected_inherited_fields is None:
                                collected_inherited_fields = self._collect_inherited_fields_for_request_response(
                                    base_classes
                                )
                            parent_field = next(
                                (
                                    candidate
                                    for candidate in collected_inherited_fields
                                    if _field_source_name(candidate) == parent_name
                                ),
                                None,
                            )
                        if parent_field is not None:
                            alias = field_name if parent_field.name != field_name else None
                            self._graph_builder.set_field_identity(
                                field, {"name": parent_field.name, "alias": alias}, inherits=True
                            )
                        continue
                    inherited_field = inherited_fields.get(field_name) if field_name is not None else None
                    if (
                        field_name in inherited_required_names
                        or (inherited_field is not None and inherited_field.required)
                    ) and not self.force_optional_for_required_fields:
                        _field_rows.require_field(self, field, inherited=True)
                    if field_name is None or _DEFERRED_INHERITED_TYPE_KEY not in field.__dict__:
                        if inherited_field is not None:
                            self._prepare_required_inherited_field(
                                field,
                                inherited_field,
                                overriding_field=field,
                            )
                        continue
                    if inherited_field is not None:
                        resolved_field = _rw_split.overlay_inherited_field(self, field, inherited_field, reserved_names)
                        if resolved_field is None:  # pragma: no cover
                            continue
                        if self.model_resolver.default_value_overrides:
                            self._apply_inherited_field_default(
                                resolved_field,
                                _rw_split.inherited_default_source(self.allof_merge_mode, field, inherited_field),
                                scope=scope,
                            )
                        fields[index] = resolved_field
                        continue
                    if self.model_resolver.default_value_overrides:
                        field.__dict__[_DEFERRED_INHERITED_CLASS_KEY] = scope
                    self._graph_builder.replace_field_type(field, self.data_type())
            original_class_name = name
            name = self._apply_title_as_name(name, obj)  # pragma: no cover
            reference = self.model_resolver.add(path, name, class_name=True, loaded=True)
            extra_field = _object_maps.get_typed_additional_properties_field(self, reference, reference.name, obj, path)
            # ignore an undetected object
            if (
                ignore_duplicate_model
                and not fields
                and extra_field is None
                and len(base_classes) == 1
                and not self._keeps_schema_validator_model(obj)
            ):
                if not self._adds_required_names([*required, *obj.required], inherited_required_names):
                    with self.model_resolver.current_base_path_context(self.model_resolver._base_path):  # noqa: SLF001
                        self.model_resolver.delete(path)
                        return frame.built(self.data_type(reference=base_classes[0]))
                nullable = nullable or (
                    not any(
                        _types_exclude(("null",), part) for part in _inherited_schemas.inline_all_of_parts(self, obj)
                    )
                    and self._object_nullable(self._load_inherited_schema_object(base_classes[0].path))
                )
            if required_names := list(dict.fromkeys([*required, *obj.required])):
                field_name_to_field = {_field_source_name(field): field for field in fields}
                for required_name in required_names:
                    if self.force_optional_for_required_fields:
                        continue
                    if field := field_name_to_field.get(required_name):
                        _field_rows.require_field(self, field)
                        continue
                    field = _inherited_schemas.build_missing_required_field(
                        self,
                        required_name,
                        excludes={field.name for field in fields if field.name},
                        base_classes=base_classes,
                        scope=scope,
                        inherited_fields=inherited_fields,
                    )
                    fields.append(field)
                    field_name_to_field[required_name] = field
            for field in fields:
                self.data_model_type.finalize_required_inherited_field(field)
            self._apply_final_class_field_aliases(
                fields,
                self.model_resolver.class_scope("reference-name", reference, reference.path),
                class_key("predicted", original_class_name),
                base_classes,
            )
            if extra_field is not None:
                extra_field.target_python_version = self.target_python_version
                fields.insert(0, extra_field)
            self._record_schema_object(reference, obj)
            if self.generate_schema_validators:
                self._add_schema_validators(reference.path, reference.name, obj, path, fields, base_classes)

            _rw_split.register_object_model(
                self, reference, obj, fields, base_classes, model_type=self.data_model_type, nullable=nullable
            )
            return frame.built(self.data_type(reference=reference))

    def _append_missing_required_fields(
        self,
        *,
        required_names: list[str],
        fields: list[DataModelFieldBase],
        base_classes: list[Reference],
        scope: ClassScope,
        declared_property_names: frozenset[str],
    ) -> None:
        """Preserve the source position of required-only fields in inline allOf items."""
        if self.force_optional_for_required_fields:
            return

        existing_field_names = {field_name for field in fields if (field_name := _field_source_name(field)) is not None}
        for required_field_name in required_names:
            if required_field_name in existing_field_names or required_field_name in declared_property_names:
                continue
            field = _inherited_schemas.build_missing_required_field(
                self,
                required_field_name,
                excludes={field.name for field in fields if field.name},
                base_classes=base_classes,
                scope=scope,
            )
            fields.append(field)
            existing_field_names.update({required_field_name, field.name or required_field_name})

    def _parse_all_of_item(  # noqa: PLR0912, PLR0913, PLR0917
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        fields: list[DataModelFieldBase],
        base_classes: list[Reference],
        required: list[str],
        union_parts: list[list[Reference]],
        declared_property_names: frozenset[str],
        inherited_parent_refs: Iterable[Reference] = (),
        *,
        conjunctions: Mapping[str, Sequence[JsonSchemaObject | None]],
        written: Counter[str],
        branch_schemas: dict[str, JsonSchemaObject],
    ) -> None:
        """Parse the members of an allOf into its fields, base classes, required names and union parts.

        Each declaration of a property of ``conjunctions`` is parsed as its entry says, left out where it is None
        (``_inherited_schemas.with_conjoined_properties``); ``written`` counts the declarations parsed so far.
        ``branch_schemas`` takes the branch each model of a union part is parsed from (``_parse_all_of_union_parts``).
        """
        parent_refs = self._get_allof_parent_references(
            obj,
            inherited=inherited_parent_refs,
        )
        parent_properties = self._get_inherited_property_map(parent_refs) if parent_refs else {}

        for all_of_item in _inherited_schemas.all_of_members(self, obj):
            if self._is_false_schema_item(all_of_item):
                _all_of_values.raise_unsatisfiable_schema(self, obj, path, "allOf")
            if not isinstance(all_of_item, JsonSchemaObject):  # pragma: no cover
                continue
            if all_of_item.ref:  # $ref
                ref_schema = self._load_ref_schema_object(all_of_item.ref)
                if ref_schema.is_boolean_schema_false:
                    _all_of_values.raise_unsatisfiable_schema(self, obj, path, "allOf")

                if ref_schema.oneOf or ref_schema.anyOf:
                    self.model_resolver.add(path, name, class_name=True, loaded=True)
                    self._parse_all_of_union_parts(name, ref_schema, path, union_parts, branch_schemas)
                else:
                    ref = all_of_item.__dict__.get(_BRANCH_REFERENCE_KEY) or self._add_ref(all_of_item.ref)
                    if _schema_refs.dynamic_target_bindings(self, ref.path):
                        warn(
                            f"allOf base class {stable_repr(redact_reference(all_of_item.ref))} cannot follow the "
                            "$dynamicAnchor bindings of its dynamic scope; its $dynamicRef resolve within its own "
                            "schema resources.",
                            stacklevel=2,
                        )
                    if ref.path not in {b.path for b in base_classes}:
                        base_classes.append(ref)
                        self.extra_template_data[ref.path]["is_base_class"] = True
            else:
                scope = self._predicted_scope(name, path)
                object_fields = _inherited_schemas.parse_fields_beside_parents(
                    self,
                    _inherited_schemas.with_conjoined_properties(all_of_item, conjunctions, written),
                    parent_refs,
                    parent_properties,
                    path,
                    scope,
                )
                if object_fields:
                    fields.extend(object_fields)
                if all_of_item.required:
                    required.extend(all_of_item.required)
                    if object_fields:
                        self._append_missing_required_fields(
                            required_names=all_of_item.required,
                            fields=fields,
                            base_classes=parent_refs,
                            scope=scope,
                            declared_property_names=declared_property_names,
                        )
                self._parse_all_of_item(
                    name,
                    all_of_item,
                    path,
                    fields,
                    base_classes,
                    required,
                    union_parts,
                    declared_property_names,
                    parent_refs,
                    conjunctions=conjunctions,
                    written=written,
                    branch_schemas=branch_schemas,
                )
                if all_of_item.anyOf or all_of_item.oneOf:
                    self.model_resolver.add(path, name, class_name=True, loaded=True)
                    self._parse_all_of_union_parts(name, all_of_item, path, union_parts, branch_schemas)

    def _parse_all_of_union_parts(
        self,
        name: str,
        schema: JsonSchemaObject,
        path: list[str],
        union_parts: list[list[Reference]],
        branch_schemas: dict[str, JsonSchemaObject],
    ) -> None:
        """Parse the ``anyOf`` and the ``oneOf`` of an allOf member, each one more union part of the allOf.

        A part holds the models of its branches. Under ``legacy-union-parts-joined`` every part is parsed at the site's
        path; else the first part of the site is, and part ``p`` at ``union_part-<p>`` below it, so the branches of two
        parts are different models. ``branch_schemas`` takes, by the path of each model, the branch it is parsed from
        (``_combined_schema_members``, read again without its warnings). Unless ``legacy-union-parts-joined`` is kept, a
        ``$ref`` branch a discriminator expanded holds the models of its subtypes (``_expanded_variants``).
        """
        for keyword, parse in (("anyOf", self.parse_any_of), ("oneOf", self.parse_one_of)):
            if getattr(schema, keyword):
                part_path = (
                    path
                    if not union_parts or "legacy-union-parts-joined" in self._legacy_intersection
                    else get_special_path(f"union_part-{len(union_parts)}", path)
                )
                parsed = parse(name, schema, part_path)
                with quiet():
                    branches, _ = self._combined_schema_members(schema, keyword)
                branch_schemas.update(
                    (data_type.reference.path, branch)
                    for data_type, branch in zip(parsed, branches, strict=True)
                    if data_type.reference
                )
                part: list[Reference] = []
                for data_type in parsed:
                    if data_type.reference:
                        part.append(data_type.reference)
                    elif "legacy-union-parts-joined" not in self._legacy_intersection and (
                        variants := _expanded_variants(data_type)
                    ):
                        part.extend(reference for _, reference in variants)
                union_parts.append(part)

    def _build_all_of_ref_root_model(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        ref_data_type: DataType,
    ) -> DataType:
        reference = self.model_resolver.add(path, name, class_name=True, loaded=True)
        self._record_schema_object(reference, obj)
        field = self._build_field("bare", ref_data_type, name=None, required=True)
        self._register_root_model(
            reference=reference,
            fields=[field],
            obj=obj,
            custom_base_class_name=class_key("predicted", name),
            description=obj.description if self.use_schema_description else None,
        )
        return self.data_type(reference=reference)

    def _register_root_model(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        obj: JsonSchemaObject,
        custom_base_class_name: str,
        description: str | None = None,
        default: Any = UNDEFINED,
    ) -> DataModel:
        return JsonSchemaParser._register_root_model_as(
            self,
            self.data_model_root_type,
            reference=reference,
            fields=fields,
            obj=obj,
            custom_base_class_name=custom_base_class_name,
            description=description,
            default=default,
        )

    @cached_property
    def _root_constraints_fallbacks(
        self,
    ) -> dict[type[DataModel], Callable[[list[DataModelFieldBase]], type[DataModel] | None] | None]:
        """Keep template-origin decisions local to this generation and model type."""
        return {}

    def _create_registered_root_model(  # noqa: PLR0913
        self,
        data_model_root_type: type[DataModel],
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        obj: JsonSchemaObject,
        custom_base_class_name: str,
        description: str | None,
        default: Any,
    ) -> DataModel:
        """Create and register one concrete root model."""
        if (factory := data_model_root_type.ROOT_MODEL_CONSTRAINTS_FALLBACK) is not None:
            fallbacks = self._root_constraints_fallbacks
            if data_model_root_type not in fallbacks:
                fallbacks[data_model_root_type] = factory(self.custom_template_dir)
            if (fallback := fallbacks[data_model_root_type]) is not None and (
                constrained_root_type := fallback(fields)
            ) is not None:
                data_model_root_type = constrained_root_type
        data_model_root = self._new_model(
            data_model_root_type,
            reference=reference,
            fields=fields,
            custom_base_class=self._resolve_base_class(
                ClassScope(custom_base_class_name, self.model_resolver.output_names(reference.path)),
                obj.custom_base_path,
            ),
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
            path=self.current_source_path,
            description=description,
            default=default,
            nullable=bool(self._null_facts(obj, "root-model").admits_null),
            treat_dot_as_module=self.treat_dot_as_module,
        )
        self._apply_root_model_sequence_interface(data_model_root, fields)
        self._graph_builder.register_model(data_model_root)
        return data_model_root

    def _register_root_model_as(  # noqa: PLR0913
        self,
        data_model_root_type: type[DataModel],
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        obj: JsonSchemaObject,
        custom_base_class_name: str,
        description: str | None = None,
        default: Any = UNDEFINED,
    ) -> DataModel:
        """Register a root using an internal alternate output representation.

        Under request-response the root splits where its fields or the types it writes beside them
        (``_rw_model_writes_variant_beside_fields``) name a model with variants, as a model with fields does. The root
        fields, and their types, record ``obj`` as their origin first, so the variants' copies keep it.
        """
        location = obj.schema_location
        for field in fields:
            with_type_origin(with_field_origin(field, "root", location).data_type, location)
        requested_root_model_type = data_model_root_type
        if self.generate_schema_validators:
            self._add_unique_items_validator(
                reference.path,
                obj,
                fields,
                [],
                is_root_model=True,
            )
        data_model_root_type = self._get_runtime_validation_root_model_type(data_model_root_type, reference.path)
        if _rw_split.splits_root_model(self, reference, fields):
            self._request_response_fields[reference.path] = tuple(_rw_split.copy_unregistered_fields(self, fields))
            if "legacy-rw-parsed-facts" in self._legacy_rw_variants:
                self._rw_model_field_facts_cache[reference.path] = _rw_split.get_rw_model_field_facts(self, fields)
            variants: list[DataModel] = []
            for suffix in ("Request", "Response"):
                variant_reference, variant_fields = _rw_split.prepare_rw_model_variant(
                    self, reference, suffix, fields, obj, source_fields=fields, is_root_model=True
                )
                self._rw_model_variant_requirement_cache[reference.path, suffix] = True
                variant_root_model_type = self._get_runtime_validation_root_model_type(
                    requested_root_model_type,
                    variant_reference.path,
                )
                variants.append(
                    JsonSchemaParser._create_registered_root_model(
                        self,
                        variant_root_model_type,
                        reference=variant_reference,
                        fields=variant_fields,
                        obj=obj,
                        custom_base_class_name=class_key("reference-name", variant_reference),
                        description=description,
                        default=default,
                    )
                )
            self._unregister_temporary_field_references(fields)
            return variants[0]

        return JsonSchemaParser._create_registered_root_model(
            self,
            data_model_root_type,
            reference=reference,
            fields=fields,
            obj=obj,
            custom_base_class_name=custom_base_class_name,
            description=description,
            default=default,
        )

    def _get_runtime_validation_root_model_type(
        self,
        data_model_root_type: type[DataModel],
        reference_path: str,
    ) -> type[DataModel]:
        """Keep runtime validators on executable root models instead of aliases."""
        if not data_model_root_type.IS_ALIAS:
            return data_model_root_type
        runtime_validation = self.extra_template_data[reference_path].get("schema_runtime_validation")
        if not _is_internal_schema_runtime_validation(runtime_validation) or not runtime_validation:
            return data_model_root_type
        if (root_model_factory := self.data_model_type.SCHEMA_RUNTIME_VALIDATION_ROOT_MODEL) is None:
            self.extra_template_data[reference_path].pop("schema_runtime_validation")
            return data_model_root_type
        return root_model_factory()

    def _apply_root_model_sequence_interface(
        self,
        data_model_root: DataModel,
        fields: list[DataModelFieldBase],
    ) -> None:
        if not self.use_root_model_sequence_interface or data_model_root.is_alias or not fields:
            return

        root_field = fields[0]
        if not root_field.required or root_field.nullable:
            return

        root_type = self._get_root_model_sequence_type(root_field.data_type)
        if root_type is None:
            return

        add_sequence_interface = getattr(data_model_root, "add_sequence_interface", None)
        if add_sequence_interface is None:
            return

        if not root_type.data_types:
            item_type = ANY
        elif len(root_type.data_types) == 1:
            item_type = root_type.data_types[0].type_hint or ANY
        else:
            item_type = build_union(self.data_type, root_type.data_types).type_hint or ANY
        slice_type = root_type.type_hint or subscript_text("list", (item_type,))
        if "[" not in slice_type:
            slice_type = subscript_text(slice_type, (item_type,))
        add_sequence_interface(item_type, slice_type)

    def _get_root_model_sequence_type(self, data_type: DataType) -> DataType | None:  # noqa: PLR6301
        """Return a sequence data type for RootModel helpers.

        This is an instance method because snooper_to_methods does not preserve
        staticmethod descriptors.
        """
        if data_type.is_optional:
            return None

        root_type = data_type
        if root_type not in SEQUENCES:
            if len(root_type.data_types) != 1:
                return None
            root_type = root_type.data_types[0]
            if root_type.is_optional:
                return None

        if root_type in SEQUENCES:
            return root_type
        return None

    def _parse_all_of_single_ref(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataType | None:
        if len(obj.allOf) != 1 or obj.properties:
            return None

        single_obj = obj.allOf[0]
        if not (
            isinstance(single_obj, JsonSchemaObject)
            and single_obj.ref
            and self._is_local_ref(single_obj.ref)
            and not (
                self._root_validates_literals
                and self._ref_siblings_apply_to(single_obj, legacy="first-read")
                and single_obj.has_ref_with_schema_keywords
            )
        ):
            return None

        referenced_schema = get_model_by_path(self.raw_obj, single_obj.ref[2:].split("/"))
        ref_data_type = self.get_ref_data_type(single_obj.ref)
        if referenced_schema is True:
            return self._build_all_of_ref_root_model(name, obj, path, ref_data_type)
        if referenced_schema is False:
            _all_of_values.raise_unsatisfiable_schema(self, obj, path, "allOf")

        if not (isinstance(referenced_schema, dict) and referenced_schema.get("enum")) or (
            _all_of_values.ref_member_narrows_literals(self, single_obj, referenced_schema["enum"], obj)
        ):
            return None

        full_path = self.model_resolver.join_path(tuple(path))
        existing_ref = self.model_resolver.references.get(full_path)
        if existing_ref is not None and not existing_ref.loaded:
            return self._build_all_of_ref_root_model(name, obj, path, ref_data_type)

        return ref_data_type

    def _parse_all_of_root_value(self, name: str, merged_root: JsonSchemaObject, path: list[str]) -> DataType:
        """Keep literal membership alongside the existing scalar or container representation."""
        literal_values = merged_root.enum if self._reads_enum(merged_root) else []
        literal_validation = cast(
            "Callable[[DataModel, list[object]], None]",
            getattr(self.data_model_root_type, "add_literal_validation", None),
        )
        if literal_values:
            merged_root = merged_root.model_copy(
                update={
                    "enum": [],
                    "extras": {key: value for key, value in merged_root.extras.items() if key != "const"},
                }
            )
        item_type = None
        if (
            merged_root.is_array
            and isinstance(merged_root.items, JsonSchemaObject)
            and self._reads_enum(merged_root.items)
        ):
            item_type = with_type_origin(
                self._parse_all_of_root_value(
                    composed_name(name, "item"), merged_root.items, get_special_path("items", path)
                ),
                merged_root.items.schema_location,
            )
            merged_root = merged_root.model_copy(update={"items": True})
        root_data_type = self._parse_all_of_merged_root(name, merged_root, path)
        reference = cast("Reference", root_data_type.reference)
        variants: tuple[Literal["Request", "Response"] | None, ...] = (
            (None,) if reference.source is not None else ("Request", "Response")
        )
        for variant in variants:
            root_model = cast(
                "DataModel",
                reference.source
                if variant is None
                else self._rw_model_variant_references[reference.path, variant].source,
            )
            if item_type is not None:
                array_owner = root_model
                array_type = next(
                    (data_type for data_type in root_model.fields[0].data_type.all_data_types if data_type in ARRAYS),
                    None,
                )
                if array_type is None:
                    array_owner, array_type = next(
                        (array_model, nested)
                        for data_type in root_model.fields[0].data_type.all_data_types
                        if (array_ref := data_type.reference) is not None
                        and (array_model := cast("DataModel | None", array_ref.source)) is not None
                        and (array_model.IS_ALIAS or array_model.IS_ROOT_MODEL)
                        for nested in array_model.fields[0].data_type.all_data_types
                        if nested in ARRAYS
                    )
                array_type.data_types = [item_type]
                self._graph_builder.note_changed_models(array_owner)
            if literal_values:
                literal_validation(root_model, literal_values)
        return root_data_type

    def _parse_all_of_merged_root(self, name: str, merged_root: JsonSchemaObject, path: list[str]) -> DataType:
        """Parse the schema an allOf merges into like the same schema written without allOf."""
        if self._reads_enum_class(merged_root) and not self.should_parse_enum_as_literal(
            merged_root, property_name=name
        ):
            return self._parse_enum_class(name, self._definition_enum(merged_root), path)
        return (
            self.parse_array(name, merged_root, path)
            if merged_root.is_array
            else self.parse_root_type(name, merged_root, path)
        )

    def parse_all_of(  # noqa: PLR0911, PLR0914
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        ignore_duplicate_model: bool = False,  # noqa: FBT001, FBT002
    ) -> DataType:
        """Parse allOf schema into a single data type with combined properties."""
        with self._model_frame() as frame:
            if self._contains_false_schema(obj.allOf):
                _all_of_values.raise_unsatisfiable_schema(self, obj, path, "allOf")
            if "legacy-all-of-site-form" not in self._legacy_intersection:
                self._satisfiable_all_of_value_parts(obj)

            single_ref_result = self._parse_all_of_single_ref(name, obj, path)
            if single_ref_result is not None:
                return frame.built(single_ref_result)

            if merged_root := self._merge_all_of_root_schema(obj):
                if self.generate_schema_validators and self._root_validates_literals:
                    return frame.built(
                        self._parse_all_of_root_value(name, merged_root.with_schema_location(obj.schema_location), path)
                    )
                return frame.built(
                    self._parse_all_of_merged_root(name, merged_root.with_schema_location(obj.schema_location), path)
                )

            nullable = self._object_nullable(obj)
            merged_all_of_obj = self._merge_all_of_object(obj)
            if merged_all_of_obj:
                return frame.built(
                    self._parse_object_common_part(
                        name,
                        merged_all_of_obj.with_schema_location(obj.schema_location),
                        path,
                        ignore_duplicate_model=ignore_duplicate_model,
                        fields=[],
                        base_classes=[],
                        required=[],
                        nullable=nullable,
                    )
                )

            root_model_result = _all_of_values.handle_allof_root_model_with_constraints(self, name, obj, path)
            if root_model_result is not None:
                return frame.built(root_model_result)
            reads_values = "legacy-value-union-members-unread" not in self._legacy_intersection
            if (
                not (ignore_duplicate_model and not reads_values and self._all_of_names_one_base(obj))
                and (value_union := _all_of_values.all_of_value_union_site(self, obj, objects_admitted=reads_values))
                is not None
            ):
                return frame.built(
                    self._parse_all_of_merged_root(name, value_union.with_schema_location(obj.schema_location), path)
                )

            fields: list[DataModelFieldBase] = []
            base_classes: list[Reference] = []
            required: list[str] = []
            union_parts: list[list[Reference]] = []
            branch_schemas: dict[str, JsonSchemaObject] = {}
            self._parse_all_of_item(
                name,
                obj,
                path,
                fields,
                base_classes,
                required,
                union_parts,
                _inherited_schemas.get_inline_property_names(self, obj),
                conjunctions=_inherited_schemas.property_conjunctions(self, obj),
                written=Counter(),
                branch_schemas=branch_schemas,
            )
            union_models = [model for part in union_parts for model in part]
            if not (fields or required or union_models) and (
                value_root := _all_of_values.merge_all_of_value_schema(self, obj, path, base_classes)
                or (
                    _all_of_values.reference_site_schema(self, obj)
                    if ignore_duplicate_model and len(base_classes) == 1
                    else _all_of_values.all_of_root_value_schema(self, obj)
                    or _all_of_values.all_of_enum_site_schema(self, obj)
                )
            ):
                return frame.built(
                    self._parse_all_of_merged_root(name, value_root.with_schema_location(obj.schema_location), path)
                )
            if not union_models:
                data_type = self._parse_object_common_part(
                    name,
                    obj,
                    path,
                    ignore_duplicate_model=ignore_duplicate_model,
                    fields=fields,
                    base_classes=base_classes,
                    required=required,
                    nullable=nullable,
                )
                if not fields and len(base_classes) == 1 and data_type.reference is base_classes[0]:
                    self._record_site_constraints(obj, data_type)
                return frame.built(data_type)
            reference = self.model_resolver.add(path, name, class_name=True, loaded=True)
            self._record_schema_object(reference, obj)
            values = _all_of_values.all_of_value_variants(self, obj) if reads_values else []
            with self._claiming(AllOfPart(0)) as claimed:
                all_of_data_type = claimed.built(
                    self._parse_object_common_part(
                        name,
                        obj,
                        get_special_path("allOf", path),
                        ignore_duplicate_model=ignore_duplicate_model,
                        fields=fields,
                        base_classes=base_classes,
                        required=required,
                    )
                )
            assert all_of_data_type.reference is not None
            part_declarations = _inherited_schemas.model_declarations(self, obj, base_classes)
            union_members: list[DataType] = []
            for index, branches in enumerate(self._all_of_union_combinations(obj, union_parts)):
                with self._claiming(AllOfPart(index + 1, branches)) as claimed:
                    union_members.append(
                        claimed.built(
                            self._parse_all_of_union_model(
                                name,
                                obj,
                                get_special_path(f"union_model-{index}", path),
                                (*branches, all_of_data_type.reference),
                                ignore_duplicate_model=ignore_duplicate_model,
                                declarations=self._union_model_declarations(
                                    branches, branch_schemas, part_declarations
                                ),
                            )
                        )
                    )
            data_type = build_union(
                self.data_type,
                [
                    *union_members,
                    *(
                        self.parse_any_of(
                            name, self._schema_object({"anyOf": values}), get_special_path("union_values", path)
                        )
                        if values
                        else ()
                    ),
                    *self._all_of_union_null(obj),
                ],
            )
            field = self.get_object_field(
                field_name=None,
                field=obj,
                required=True,
                field_type=data_type,
                alias=None,
                original_field_name=None,
            )
            self._register_root_model(
                reference=reference,
                fields=[field],
                obj=obj,
                custom_base_class_name=class_key("predicted", name),
                description=obj.description if self.use_schema_description else None,
            )
            return frame.built(self.data_type(reference=reference))

    def _all_of_union_combinations(
        self, obj: JsonSchemaObject, union_parts: list[list[Reference]]
    ) -> list[tuple[Reference, ...]]:
        """Return the branch models of each union model of an allOf, which also takes the class of its other parts.

        Under ``legacy-union-parts-joined`` and for one part, a union model per branch of every part. Else a union
        model per combination of one branch of each part that has models, row by row (the first part varying
        slowest), each model once in it and each set of models once (``branch_combinations``); above
        ``BRANCH_COMBINATION_LIMIT`` combinations a site keeps a union model per branch, with a warning.
        """
        joined: list[tuple[Reference, ...]] = [(model,) for part in union_parts for model in part]
        if (
            "legacy-union-parts-joined" in self._legacy_intersection
            or len(parts := [part for part in union_parts if part]) <= 1
        ):
            return joined
        if (combinations := branch_combinations(parts, BRANCH_COMBINATION_LIMIT, key=lambda model: model.path)) is None:
            warn(
                f"{named_location(obj.schema_location, 'allOf')} has {math.prod(map(len, parts))} combinations of the "
                f"branches of its anyOf and oneOf, more than {BRANCH_COMBINATION_LIMIT}; each branch is a union "
                "model with the other parts of the allOf, as without --distribute-allof-over-unions.",
                stacklevel=2,
            )
            return joined
        return combinations

    def _all_of_names_one_base(self, obj: JsonSchemaObject) -> bool:
        """Return whether an allOf site has one base class: one schema that its ``$ref`` members name, and no union.

        The site of a field or an item is that class where the model holds nothing else (``_parse_object_common_part``
        with ``ignore_duplicate_model``), which a schema validator rule of the site does not let it be.
        """
        bases = {
            self._ref_path(ref)
            for ref in _inherited_schemas.iter_allof_refs(self, obj)
            if not ((target := self._load_ref_schema_object(ref)).anyOf or target.oneOf)
        }
        return len(bases) == 1 and not self._keeps_schema_validator_model(obj)

    def _keeps_schema_validator_model(self, obj: JsonSchemaObject) -> bool:
        """Return whether a schema validator rule keeps a site a model of its own (--generate-schema-validators)."""
        return self.generate_schema_validators and _schema_validators.has_schema_validator_constraints(self, obj)

    def _all_of_union_null(self, obj: JsonSchemaObject) -> list[DataType]:
        """Return the None the union of an allOf member's variants takes: where every part of ``obj`` admits null.

        None under ``legacy-all-of-union-null-dropped``.
        """
        if "legacy-all-of-union-null-dropped" in self._legacy_intersection or not self._all_of_part_admits_null(obj):
            return []
        return [self.data_type_manager.get_data_type(Types.null)]

    def _all_of_part_admits_null(self, part: JsonSchemaObject | bool) -> bool:  # noqa: FBT001
        """Return whether a part of an allOf admits null, the site itself included (site ``all-of-union``).

        Its own keywords decide (``parser/_null_facts.py``; a ``const`` only where its version has the keyword, and the
        keywords beside a ``$ref`` only where they are parts of it, ``_ref_siblings_are_parts_of``), and so do the
        target of its ``$ref``, one branch each of its ``anyOf`` and ``oneOf``, and each of its own ``allOf`` members,
        read the same way. A false schema, a reference the walk has passed and one ``--external-ref-mapping`` maps,
        whose document the walk does not read, admit none. The walk reads each reference once, unless a reference it
        had passed cut its answer short.
        """
        known: dict[str, bool] = {}

        def target_admits(ref: str, seen: frozenset[str]) -> tuple[bool, bool]:
            """Return ``admits`` of the schema ``ref`` names, read once per walk unless a cut shortened its answer."""
            if self._resolve_external_ref_mapping(ref) is not None:
                return False, False
            if (path := self._ref_path(ref)) in seen:
                return False, True
            if (answer := known.get(path)) is not None:
                return answer, False
            answer, cut = admits(self._load_ref_schema_object(ref), seen | {path})
            if not cut:
                known[path] = answer
            return answer, cut

        def first(parts: Iterable[JsonSchemaObject | bool], seen: frozenset[str], *, answer: bool) -> tuple[bool, bool]:
            """Return whether one of ``parts`` gives ``answer``, read up to it, and whether a cut shortened a read."""
            cut = False
            for item in parts:
                item_answer, item_cut = admits(item, seen)
                cut = cut or item_cut
                if item_answer is answer:
                    return True, cut
            return False, cut

        def admits(part: JsonSchemaObject | bool, seen: frozenset[str]) -> tuple[bool, bool]:  # noqa: FBT001
            """Return whether ``part`` admits null, and whether a reference the walk had passed cut the answer short."""
            if not isinstance(part, JsonSchemaObject) or part.is_boolean_schema_false:
                return part is True, False
            cut = False
            if part.ref:
                answer, cut = target_admits(part.ref, seen)
                if not answer or not self._ref_siblings_are_parts_of(part):
                    return answer, cut
            sources = _null_sources(part, "all-of-union", self._type_has_null)
            if sources.const_set and not self._features_at(part).const_support:
                sources = replace(sources, const_set=False, const_null=False)
            if not resolve_null_facts(sources, "all-of-union", self._null_mode).admits_null:
                return False, cut
            for branches in (part.anyOf, part.oneOf):
                if branches:
                    found, branch_cut = first(branches, seen, answer=True)
                    cut = cut or branch_cut
                    if not found:
                        return False, cut
            rejected, member_cut = first(part.allOf, seen, answer=False)
            return not rejected, cut or member_cut

        return admits(part, frozenset())[0]

    def _parse_all_of_union_model(  # noqa: PLR0913
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        bases: tuple[Reference, ...],
        *,
        ignore_duplicate_model: bool,
        declarations: Sequence[ModelDeclarations] | None,
    ) -> DataType:
        """Parse the class of one union member of an allOf, located at the member's (first) branch.

        The class of a member whose Reference several schema objects reach is left without a location too. Row
        ``legacy-allof-members-under-holder``: every member is named after the allOf's holder, ``name``, so the members
        are numbered after it (``Event1``, ``Event2``), not named after their branches (#4847). The class declares the
        properties its bases declare with different check keywords (``_inherited_schemas.union_model_properties`` of
        the ``declarations`` of its bases, None where one is not known), so it checks what every declaration does.
        """
        union_model = bases[0]
        site = obj.with_schema_location(location_of(union_model))
        fields: list[DataModelFieldBase] = []
        required: list[str] = []
        if declarations is not None and (
            properties := _inherited_schemas.union_model_properties(self, obj, declarations)
        ):
            own = site.properties or {}
            if own.keys() & properties.keys():
                site = site.model_copy(
                    update={
                        "properties": {
                            key: properties[key][0] if key in properties else value for key, value in own.items()
                        }
                    }
                )
            if added := {key: value for key, value in properties.items() if key not in own}:
                required = [key for key, (_, is_required) in added.items() if is_required]
                fields = _inherited_schemas.parse_fields_beside_parents(
                    self,
                    self._schema_object({"properties": {key: declared for key, (declared, _) in added.items()}}),
                    list(bases),
                    self._get_inherited_property_map(list(bases)),
                    path,
                    self._predicted_scope(name, path),
                )
        data_type = self._parse_object_common_part(
            name,
            site,
            path,
            ignore_duplicate_model=ignore_duplicate_model,
            fields=fields,
            base_classes=list(bases),
            required=required,
        )
        if union_model.__dict__.get(SCHEMA_LOCATION_CONFLICT_KEY) and (reference := data_type.reference) is not None:
            _mark_location_conflict(reference)
        return data_type

    def _union_model_declarations(
        self,
        branches: tuple[Reference, ...],
        branch_schemas: Mapping[str, JsonSchemaObject],
        part_declarations: ModelDeclarations,
    ) -> list[ModelDeclarations] | None:
        """Return the declarations of each base of a union model of an allOf: its branches, then the other parts.

        A branch parsed from an inline schema declares what that schema and its parents do; a branch that is the class
        of a schema, what that schema does (``_inherited_schemas.model_declarations``). None where a branch is a class
        the site builds for a ``$ref`` (``_parse_ref_branch_model``), whose declarations are not read here.
        """
        declarations: list[ModelDeclarations] = []
        for branch in branches:
            if (schema := branch_schemas.get(branch.path)) is not None and not schema.ref:
                declarations.append(
                    _inherited_schemas.model_declarations(self, schema, self._get_allof_parent_references(schema))
                )
            elif not _is_special_path(branch.path):
                declarations.append(_inherited_schemas.model_declarations(self, None, [branch]))
            else:
                return None
        return [*declarations, part_declarations]

    def _get_tag_key_variant_key(self, obj: JsonSchemaObject, path: list[str]) -> str | None:
        """Return the key of the union branch `obj` modeled at `path` when its class name still comes from that key.

        A branch its title names (`--use-title-as-name`) or `--model-name-map` renames does not repeat the key.
        """
        if (location := obj.schema_location) is None or (variant := self._tag_key_variants.get(location)) is None:
            return None
        key, variant_name = variant
        named_after_key = self.model_resolver.named_after(self.model_resolver.join_path(tuple(path)), variant_name)
        return key if named_after_key else None

    def parse_object_fields(  # noqa: PLR0914
        self,
        obj: JsonSchemaObject,
        path: list[str],
        module_name: Optional[str] = None,  # noqa: UP045
        class_name: Optional[str] = None,  # noqa: UP045
        *,
        output_names: tuple[str, ...] = (),
    ) -> list[DataModelFieldBase]:
        """Parse object properties into a list of data model fields.

        A field of a boolean property schema records where ``obj`` declares it, ``properties/<name>``, as its origin.
        The class keys of the options read ``class_name``, then the class's names in the output (``output_names``).
        """
        scope = _class_scope_of(class_name, output_names)
        properties: dict[str, JsonSchemaObject | bool] = {} if obj.properties is None else obj.properties
        requires: set[str] = {*()} if obj.required is None else {*obj.required}
        fields: list[DataModelFieldBase] = []

        exclude_field_names: set[str] = set()
        variant_key = self._get_tag_key_variant_key(obj, path) if self._tag_key_variants else None
        for original_field_name, field in properties.items():
            field_name, alias = self.model_resolver.get_valid_field_name_and_alias(
                original_field_name,
                excludes=exclude_field_names,
                model_type=self.field_name_model_type,
                class_name=class_name,
                output_names=output_names,
            )
            modular_name = dotted_name(module_name, field_name)

            exclude_field_names.add(field_name)
            required = False if self.force_optional_for_required_fields else original_field_name in requires

            if isinstance(field, bool):
                boolean_field = self._build_field(
                    "legacy-boolean-property",
                    self.data_type_manager.get_data_type(Types.any),
                    name=field_name,
                    alias=alias,
                    source_name=original_field_name,
                    scope=scope,
                    required=required,
                    original_name=original_field_name,
                )
                declaration = (
                    None
                    if (location := obj.schema_location) is None
                    else location.child("properties", original_field_name)
                )
                fields.append(with_field_origin(boolean_field, "property", declaration))
                continue

            field = self._site_schema(field, merge_ref_siblings=True)  # noqa: PLW2901

            if original_field_name == variant_key:
                self.model_resolver.name_past_parent([*path, field_name])
            field_type = self.parse_item(modular_name, field, [*path, field_name])

            effective_default, effective_has_default, use_default_with_required = self._effective_default_state(
                original_field_name,
                field.default,
                has_default=field.has_default,
                required=required,
                scope=scope,
            )
            fields.append(
                self.get_object_field(
                    field_name=field_name,
                    field=field,
                    required=required,
                    field_type=field_type,
                    alias=alias,
                    original_field_name=original_field_name,
                    effective_default=effective_default,
                    effective_has_default=effective_has_default,
                    use_default_with_required=use_default_with_required,
                    class_name=class_name,
                    output_names=output_names,
                )
            )
        if self.config.aliases:
            for field in fields:
                field.__dict__[_ALIAS_RESOLUTION_CLASS_NAME_KEY] = scope
        return fields

    @property
    def _root_validates_literals(self) -> bool:
        """Whether the root model class validates the literal values of a root (``add_literal_validation``)."""
        return hasattr(self.data_model_root_type, "add_literal_validation")

    @cached_property
    def _nested_constrained_model_type(self) -> type[DataModel]:
        """Resolve the output-specific model for an inline constrained value."""
        return self._output_model_context.resolve_nested_constrained_model_type()

    def _additional_properties_value(
        self, name: str, additional_properties: JsonSchemaObject, parent: JsonSchemaObject
    ) -> _MapValue:
        """Return how a mapping value schema is built: by ``parse_item``, or as a constrained root model.

        Without Annotated constraints ``parse_item`` gets the parent where the output writes field constraints, so the
        field table (``_FIELD_TABLE``) chooses the root model of an item that holds constraints. With them, the schema
        is built as a root model that preserves its constraints when its $ref merged with its siblings, or its allOf
        of primitives, has effective constraints (``_parse_constrained_additional_properties_value_item``); a parser
        that overrides a parse hook, a titled alias, an enum and a union keep ``parse_item``. The request/response
        walk reads the same decision (``_typed_additional_properties_value``).
        """
        additional_properties = self._site_schema(additional_properties)
        annotated = self._output_model_context.supports_annotated_constraints
        item_counts_site = not annotated and self._writes_item_counts_on_alias(additional_properties)
        if not (annotated or item_counts_site):
            return _MapValue(
                additional_properties,
                parent if self.field_constraints and self.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS else None,
            )
        if (constrained := self._constrained_value_schema(name, additional_properties)) is None:
            return _MapValue(additional_properties)
        value_schema, preserve_root_model = constrained
        return _MapValue(
            value_schema,
            parent,
            constrained=True,
            preserve_root_model=preserve_root_model,
            item_counts_site=item_counts_site,
        )

    def _overrides_parse_hooks(self) -> bool:
        """Return whether the parser or its class overrides a parse hook.

        The hooks are ``parse_item``, ``parse_root_type`` and ``_register_root_model``.
        """
        parser_type = type(self)
        parser_attributes = self.__dict__
        return (
            "parse_item" in parser_attributes
            or "parse_root_type" in parser_attributes
            or "_register_root_model" in parser_attributes
        ) or (
            parser_type is not JsonSchemaParser
            and (
                parser_type.parse_item is not JsonSchemaParser.parse_item
                or parser_type.parse_root_type is not JsonSchemaParser.parse_root_type
                or parser_type._register_root_model is not JsonSchemaParser._register_root_model  # noqa: SLF001
            )
        )

    def _constrained_value_schema(self, name: str, schema: JsonSchemaObject) -> tuple[JsonSchemaObject, bool] | None:
        """Return the schema whose constraints a map value keeps on a type of its own, or None where it keeps none.

        That is the value, its $ref merged with its siblings (without the title of the target), or its allOf of
        primitives merged into one schema, which the second item tells, where it has effective constraints. A parser
        that overrides a parse hook, a titled alias, an enum and a union keep none.
        """
        if (
            self._overrides_parse_hooks()
            or self._should_create_type_alias_for_title(schema, name)
            or schema.enum
            or schema.anyOf
            or schema.oneOf
        ):
            return None

        preserve_root_model = bool(schema.allOf)
        if preserve_root_model:
            if (value_schema := _object_maps._merge_additional_properties_all_of(self, schema)) is None:  # noqa: SLF001
                return None
        else:
            value_schema = schema
        if not preserve_root_model and _merges_ref_siblings(value_schema):
            value_schema = self._merge_ref_with_schema(value_schema)
            if value_schema.title:
                extras = value_schema.extras
                if not schema.title:
                    extras = {key: value for key, value in extras.items() if key != "title"}
                value_schema = value_schema.model_copy(
                    update={
                        "title": None,
                        "extras": extras,
                    }
                )
        if not self._has_effective_constraints(value_schema):
            return None
        return value_schema, preserve_root_model

    def _pattern_value_alias(
        self, name: str, schema: JsonSchemaObject, value_type: DataType, path: list[str]
    ) -> DataType:
        """Return the type alias that writes a patternProperties value with the constraints its type leaves out.

        No field holds the value, so where the output writes constraints in ``Annotated``
        (``supports_internal_annotated_constraints``) the constraints of its schema that ``value_type``, the type
        ``parse_item`` built, leaves out go on an alias of that type, ``<name>PatternProperty``, as a preserved root
        writes them: under --field-constraints those of the schema ``_constrained_value_schema`` reads, and otherwise
        the item counts of an array (``_writes_item_counts_on_alias``). The value keeps its type, so every model it
        builds keeps its name; a type that names a model, an x-python-type and a merged enum take none, and the value
        is written as ``value_type`` itself. The alias gives way to the name of every other model
        (``yielding_new_references``).
        """
        if value_type.reference is not None or not self._output_model_context.supports_internal_annotated_constraints:
            return value_type
        schema = self._site_schema(schema)
        if not (
            (self.field_constraints or self._writes_item_counts_on_alias(schema))
            and (constrained := self._constrained_value_schema(name, schema)) is not None
            and self._constrained_value_row(value_schema := constrained[0]) == "root"
            and self._assemble_field_constraints(value_schema, value_type, _ROOT_SITE, preserve=True).constraints
        ):
            return value_type
        with self.model_resolver.yielding_new_references():
            return JsonSchemaParser._parse_annotated_root(
                self,
                composed_name(name, "pattern-property"),
                value_schema,
                get_special_path("patternProperty", path),
                root_class=self._nested_constrained_model_type,
                root_type=value_type,
            )

    def _parse_additional_properties_value(
        self,
        name: str,
        path: list[str],
        parent: JsonSchemaObject,
        *,
        additional_properties: JsonSchemaObject,
        constrained_name: str | None = None,
    ) -> DataType:
        """Parse a mapping value schema while preserving supported Annotated constraints.

        The schema of a value that only its item counts make a model of its own is marked as an item-count site
        (``ITEM_COUNTS_SITE_KEY``), which the model built from it takes (``_record_schema_object``).
        """
        value = self._additional_properties_value(name, additional_properties, parent)
        if not value.constrained:
            return self.parse_item(name, value.schema, path, parent=value.parent)
        if value.item_counts_site:
            value.schema.__dict__[ITEM_COUNTS_SITE_KEY] = True
        return JsonSchemaParser._parse_constrained_additional_properties_value_item(
            self,
            constrained_name or (name if value.preserve_root_model else composed_name(name, "additional-property")),
            value.schema,
            path,
            parent=parent,
            data_model_root_type=(self._nested_constrained_model_type if not value.preserve_root_model else None),
        )

    @cached_property
    def _legacy_object_maps(self) -> frozenset[str]:
        """The rows of 0.86.0 object maps kept, which each map option leaves out (``parser/_object_maps.py``)."""
        return _object_maps.legacy_object_maps(self)

    @property
    def _types_nullable_map_values(self) -> bool:
        """Whether this run types the values of nullable maps."""
        return self.use_typed_nullable_map_values and not self.run_context.untyped_nullable_maps

    def _typed_nullable_map_value_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the value schema of a nullable map when this run types its values (``parser/_object_maps.py``)."""
        return _object_maps.typed_nullable_map_value_schema(self, obj)

    def _constrained_value_row(self, item: JsonSchemaObject) -> Literal["python-type", "enum", "root"]:
        """Return how a constrained mapping value is built: by its x-python-type, as an enum, else as a root model."""
        if self._overriding_python_type(item) is not None:
            return "python-type"
        return "enum" if item.enum else "root"

    def _parse_constrained_additional_properties_value_item(
        self,
        name: str,
        item: JsonSchemaObject,
        path: list[str],
        *,
        parent: JsonSchemaObject,
        data_model_root_type: type[DataModel] | None,
    ) -> DataType:
        """Parse the constrained-value fast path without changing parser extension hooks."""
        row = self._constrained_value_row(item)
        if row == "python-type":
            return cast("DataType", self._get_python_type_override(item))
        if row == "enum":
            return self.parse_item(name, item, path, parent=parent)
        if self.use_title_as_name and item.title:
            name = sanitize_module_name(item.title, treat_dot_as_module=self.treat_dot_as_module)
        if self._should_create_type_alias_for_title(item, name):
            return JsonSchemaParser._parse_annotated_root(self, name, item, path, root_class=data_model_root_type)

        root_type_path = get_special_path("array", path)
        return JsonSchemaParser._parse_annotated_root(
            self,
            self.model_resolver.add(root_type_path, name, class_name=True).name,
            item,
            root_type_path,
            root_class=data_model_root_type,
        )

    def _add_nullable_combined_schema_branches(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        updates: dict[str, list[JsonSchemaObject | bool]] = {}
        for field_name in ("anyOf", "oneOf"):
            combined_items = getattr(obj, field_name)
            if not combined_items:
                continue

            updated_items: list[JsonSchemaObject | bool] = []
            for item in combined_items:
                updated_items.append(item)
                if isinstance(item, JsonSchemaObject) and self._null_facts(item, "combined-branch").admits_null:
                    updated_items.append(self._schema_object(null_schema()))

            if len(updated_items) != len(combined_items):
                updates[field_name] = updated_items

        return obj.model_copy(update=updates) if updates else obj

    def parse_object(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        unique: bool = True,  # noqa: FBT001, FBT002
    ) -> DataType:
        """Parse object schema into a data model."""
        with self._model_frame() as frame:
            merged_copy = _MERGED_REF_TARGET_KEY in obj.__dict__
            if self.generate_schema_validators:
                obj = self._merge_conditional_properties(obj)
            if not unique:  # pragma: no cover
                warn(
                    f"{self.__class__.__name__}.parse_object() ignore `unique` argument."
                    f"An object name must be unique."
                    f"This argument will be removed in a future version",
                    stacklevel=2,
                )
            name = self._apply_title_as_name(name, obj)
            reference = self.model_resolver.add(
                path,
                name,
                class_name=True,
                singular_name=singular_name,
                loaded=True,
            )
            class_name = class_key("reference-name", reference)
            self.set_title(reference.path, obj)
            self.set_schema_id(reference.path, obj)
            self._preload_property_refs_for_rw_models(obj)
            fields = self.parse_object_fields(
                obj,
                path,
                _inherited_schemas.inferred_module_name(self, class_name),
                class_name=class_name,
                output_names=self.model_resolver.output_names(reference.path),
            )
            has_declared_fields = bool(fields)
            if has_declared_fields and (
                extra_field := _object_maps.get_typed_additional_properties_field(
                    self, reference, class_name, obj, path
                )
            ):
                extra_field.target_python_version = self.target_python_version
                fields.insert(0, extra_field)
            should_parse_dict_root = not has_declared_fields and (
                isinstance(obj.additionalProperties, JsonSchemaObject)
                or _value_keywords.should_parse_empty_object_as_dict(self, obj)
            )
            if not should_parse_dict_root:
                data_model_type_class = self.data_model_type
            else:
                fields.append(_object_maps.get_additional_properties_root_field(self, name, obj, path))
                data_model_type_class = self.data_model_root_type

            self._record_schema_object(reference, obj)
            if (
                obj.additionalProperties is False
                and not should_parse_dict_root
                and any(not self._is_false_schema_item(value) for value in (obj.patternProperties or {}).values())
            ):
                self._warn_closed_pattern_properties(reference.path, obj)
            if self.generate_schema_validators:
                self._add_schema_validators(
                    reference.path,
                    class_name,
                    obj,
                    path,
                    fields,
                    [],
                    include_property_count=data_model_type_class is self.data_model_type,
                )

            model = _rw_split.register_object_model(
                self, reference, obj, fields, None, model_type=data_model_type_class
            )
            if merged_copy and model is not None:
                self._merged_copies.add(model)
            return frame.built(self.data_type(reference=reference))

    def parse_pattern_properties(
        self,
        name: str,
        pattern_properties: dict[str, JsonSchemaObject | bool],
        path: list[str],
        *,
        property_names: JsonSchemaObject | bool | None = None,
    ) -> DataType:
        """Parse patternProperties into one dict whose keys match a pattern and whose value is the union of theirs.

        Each value is parsed where its pattern declares it, in document order; equal values collapse when the
        type is written, and a value that admits anything makes the whole value ``Any``. The patterns are joined
        into one key pattern, intersected with ``property_names``. A ``false`` pattern admits no key. A map whose
        additionalProperties types the keys that match no pattern (``_other_keys_schema``) is not built here.
        Row ``legacy-map-union-of-dicts`` keeps the map of 0.86.0, a union of dicts, one per value type, whose keys
        match its patterns; an output that cannot decode a union of several dicts, such as msgspec, gets the one dict.
        """
        return _object_maps.parse_pattern_properties(
            self, name, pattern_properties, path, property_names=property_names
        )

    def _other_keys_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the additionalProperties schema typing the keys no pattern matches (``parser/_object_maps.py``)."""
        return _object_maps.other_keys_schema(self, obj)

    def _is_object_map_schema(self, obj: JsonSchemaObject) -> bool:  # noqa: PLR6301
        """Return whether the keywords of a schema make an object map.

        These are patternProperties or propertyNames, or an object without properties whose additionalProperties is a
        schema.
        """
        return _declares_key_keywords(obj) or (
            obj.is_object and not obj.properties and isinstance(obj.additionalProperties, JsonSchemaObject)
        )

    def _root_type_row(  # noqa: PLR0911
        self,
        obj: JsonSchemaObject,
        *,
        row: Literal["parse", "legacy-rw-allof-before-custom-type"] = "parse",
    ) -> _RootTypeRow:
        """Return the row that builds the root type of a schema (``_parse_root_type_with_context``).

        The first row that takes the schema builds its root type, in this order: a reference, a custom type, a type
        list whose branches take annotated aliases (``_localizes_constrained_type_union``), an array, an anyOf or
        oneOf and an allOf take it before the map keywords are read, then the map keywords (``_is_object_map_schema``),
        whatever properties the schema holds, which a root model does not read; then an enum the run reads (not under
        --ignore-enum-constraints), a nullable map whose values are typed and a declared type; else the root holds any
        value. A root that builds an object map (``_ROOT_MAP_ROWS``) types its values by its additionalProperties
        (``_parse_object_map``). Row ``legacy-rw-allof-before-custom-type`` (the allOf of a root in the request/response
        walk, ``_typed_model_children``) reads the rows from the array on, so an allOf beside a reference, a custom type
        or such a type list takes the schema there.
        """
        if row == "parse":
            if obj.ref:
                return "ref"
            if obj.custom_type_path:
                return "custom-type"
            if self._localizes_constrained_type_union(obj):
                return "constrained-type-union"
        if obj.is_array:
            return "array"
        if obj.anyOf or obj.oneOf:
            return "combined"
        if obj.allOf:
            return "all-of"
        if self._is_object_map_schema(obj):
            return "object-map"
        if self._reads_enum(obj):
            return "enum"
        if self._typed_nullable_map_value_schema(obj) is not None:
            return "typed-nullable-map"
        return "type" if obj.type else "any"

    def _parse_object_map(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        object_path: list[str],
        *,
        typed_values: JsonSchemaObject | None = None,
    ) -> DataType:
        """Build the type of an object map, a schema object without ``properties`` whose instances may be objects.

        The map is one dict, never a union of dicts: patternProperties give its keys and the union of their values,
        else propertyNames its keys and additionalProperties its values. A type array of object and null types its
        values only under ``--use-typed-nullable-map-values``, whose value schema callers pass as ``typed_values``.
        The dict itself admits the null of a type array of object and null, or of ``nullable`` under
        ``--strict-nullable``, so its property counts stay on it; other types of the type array join it in a union.
        The members of the dict are parsed under ``object_path``, the other types under ``path``.

        Row ``legacy-map-union-of-dicts`` keeps the patternProperties of 0.86.0, a union of dicts, one per value type
        (``parse_pattern_properties``), with no inexact-map warning. Row ``legacy-map-null-left-to-field`` leaves the
        null and the other types of its type array to the field; only the values typed under
        ``--use-typed-nullable-map-values`` make one dict, optional where its type array lists null, and the union with
        the other types of its type array.
        """
        rows = self._legacy_object_maps
        typed = False
        if pattern_properties := obj.patternProperties:
            if "legacy-map-union-of-dicts" not in rows:
                self._warn_inexact_object_map(name, obj, pattern_properties)
            map_type = (
                self.parse_pattern_properties(name, pattern_properties, object_path, property_names=obj.propertyNames)
                if (other_keys := self._other_keys_schema(obj)) is None
                else _object_maps.parse_pattern_map(
                    self, name, pattern_properties, object_path, obj.propertyNames, other_keys
                )
            )
        elif obj.propertyNames is not None:
            map_type = self.parse_property_names(
                name, obj.propertyNames, obj.additionalProperties, object_path, parent_obj=obj
            )
        elif typed_values is not None:
            typed = True
            map_type = _object_maps.parse_nullable_map(self, name, obj, object_path, typed_values)
        elif isinstance(obj.additionalProperties, JsonSchemaObject):
            map_type = _object_maps.parse_additional_properties_map(
                self, name, obj, object_path, obj.additionalProperties
            )
        else:
            map_type = self.data_type_manager.get_data_type(Types.object)
        null_left_to_field = "legacy-map-null-left-to-field" in rows
        if null_left_to_field and not typed:
            return map_type
        if (union := _object_maps.object_map_type_union(self, name, obj, path, map_type)) is not None:
            return union
        if null_left_to_field:
            return build_union(self.data_type, [map_type], nullable=obj.type_has_null)
        return _with_null(map_type) if _object_maps.object_map_admits_null(self, obj) else map_type

    def _warn_inexact_object_map(
        self, name: str, obj: JsonSchemaObject, pattern_properties: dict[str, JsonSchemaObject | bool]
    ) -> None:
        """Warn once per schema object whose patternProperties one dict type cannot express exactly.

        A dict gives all its keys one key type and one value type. It is exact only when the object and the dict admit
        the same keys, and every pattern and the additionalProperties schema that types the other keys
        declare the same value schema, annotations aside.
        """
        admitted = [
            {} if schema is True else _schema_assertions(schema)
            for schema in pattern_properties.values()
            if schema is not False
        ]
        reasons: list[str] = []
        if (other_keys := self._other_keys_schema(obj)) is not None:
            admitted.append(_schema_assertions(other_keys))
            if any(schema is False for schema in pattern_properties.values()):
                reasons.append("admits keys whose pattern rejects every value")
        elif admitted and obj.additionalProperties is not False and not _is_false_all_of(obj.additionalProperties):
            reasons.append("allows keys that match no pattern")
        if any(schema != admitted[0] for schema in admitted):
            reasons.append(
                "gives the keys of its patterns different value schemas"
                if other_keys is None
                else "gives the keys of its patterns and the other keys different value schemas"
            )
        where = named_location(obj.schema_location, name)
        with first_report(self._inexact_object_maps, where) as first:
            if reasons and first:
                warn(
                    f"{where}: one dict type cannot express this object, since it "
                    f"{english_list(reasons, 'and', 'each')}; the generated dict accepts or rejects some objects that "
                    "the schema does not. Pydantic output with "
                    "--generate-schema-validators checks it exactly.",
                    stacklevel=2,
                )

    def _parse_combined_property_names_key_type(
        self, name: str, property_names: JsonSchemaObject, path: list[str]
    ) -> DataType:
        """Build the key type of an anyOf, oneOf or allOf propertyNames schema, parsing it only when needed."""
        if property_names.anyOf:
            key_type, _ = _object_maps.parse_string_property_name_union(self, property_names)
            if key_type is not None:
                return key_type
        key_type = self.parse_item(name, property_names, get_special_path("propertyNames/key", path))
        if property_names.anyOf and not any(item.reference for item in key_type.all_data_types):
            string_key_type, _ = _object_maps.parse_string_property_name_union(
                self, property_names, discard_nonstring=True
            )
            if string_key_type is not None:
                key_type = string_key_type
        if (
            not property_names.allOf
            or self.field_constraints
            or self.collapse_root_models
            or (reference := key_type.reference) is None
            or not isinstance(root_model := reference.source, self.data_model_root_type)
        ):
            return key_type
        if (
            self.custom_template_dir
            or self.enable_faux_immutability
            or self.validators
            or (self.dataclass_arguments and self.dataclass_arguments.get("frozen"))
        ):
            return key_type
        if (
            not root_model.IS_ROOT_MODEL
            or root_model.decorators
            or root_model.has_model_config
            or property_names.model_fields_set - {"allOf", "title", "description"}
            or any(
                not isinstance(item, JsonSchemaObject)
                or item.type != "string"
                or item.model_fields_set - {"type", "pattern", "minLength", "maxLength"}
                for item in property_names.allOf
            )
        ):
            return key_type
        schemas = cast("list[JsonSchemaObject]", property_names.allOf)
        merged = JsonSchemaParser._first_typed_schema_dict(schemas)
        self._merge_schema_constraints(
            merged, schemas, intersect=intersects_all_of_constraints(self.allof_merge_mode.value)
        )
        key_type.unregister_reference()
        return _object_maps.parse_property_name_key_schema(self, self._schema_object(merged))

    def parse_property_names(
        self,
        name: str,
        property_names: JsonSchemaObject | bool,  # noqa: FBT001
        additional_properties: JsonSchemaObject | bool | None,  # noqa: FBT001
        path: list[str],
        parent_obj: JsonSchemaObject | None = None,
    ) -> DataType:
        """Parse propertyNames into a dict whose keys it constrains and whose values additionalProperties types.

        ``parent_obj`` is the schema whose x-python-type the dict takes.
        """
        return _object_maps.parse_property_names(self, name, property_names, additional_properties, path, parent_obj)

    def _should_create_type_alias_for_title(  # noqa: PLR0911
        self, item: JsonSchemaObject, name: str
    ) -> bool:
        """Check if a type alias should be created for an inline type with title.

        When use_title_as_name is enabled and the item has a title, certain inline types
        (array, dict, oneOf/anyOf unions, enum as literal, primitive types) should create
        a type alias instead of being inlined.
        """
        if not (self.use_title_as_name and item.title):
            return False

        if item.is_array:
            return True
        if item.anyOf or item.oneOf:
            combined_items = item.anyOf or item.oneOf
            const_enum_data = _value_keywords.extract_const_enum_from_combined(self, combined_items, item)
            if const_enum_data is None:
                return True
            synthetic_obj = _value_keywords.create_synthetic_enum_obj(self, item, *const_enum_data)
            if self.should_parse_enum_as_literal(synthetic_obj, property_name=name, property_obj=item):
                return True
        if self._is_object_map_schema(item):
            return True
        if self._reads_enum(item) and self.should_parse_enum_as_literal(item, property_name=name):
            return True
        is_primitive = (
            item.type
            and not item.is_array
            and not item.is_object
            and not item.anyOf
            and not item.oneOf
            and not item.allOf
            and not item.ref
            and not self._reads_enum_class(item)
        )
        return bool(is_primitive)

    def _parse_combined_const_enum(  # noqa: PLR0913
        self,
        name: str,
        obj: JsonSchemaObject,
        combined_items: list[JsonSchemaObject | bool],
        enum_path: list[str],
        *,
        singular_name: bool = False,
        writes: bool = False,
        literal_as_root: bool = False,
    ) -> DataType | None:
        """Return the Enum or Literal of a oneOf or anyOf of consts, else None.

        The parent's field writes its constraints beside the values when ``writes``; a definition's Literal is the root
        model of its name (``literal_as_root``).
        """
        const_enum_data = _value_keywords.extract_const_enum_from_combined(self, combined_items, obj, writes=writes)
        if const_enum_data is None:
            return None

        synthetic_obj = _value_keywords.create_synthetic_enum_obj(self, obj, *const_enum_data)
        if self.should_parse_enum_as_literal(synthetic_obj, property_name=name, property_obj=obj):
            if literal_as_root:
                return self.parse_root_type(name, synthetic_obj, enum_path)
            return self.parse_enum_as_literal(synthetic_obj)
        return self._parse_enum_class(name, synthetic_obj, enum_path, property_obj=obj, singular_name=singular_name)

    def parse_item(
        self,
        name: str,
        item: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        parent: JsonSchemaObject | None = None,
    ) -> DataType:
        """Parse a single JSON Schema item into a data type, within the schema resource it declares.

        The row of the field table (``_FIELD_TABLE``), after the steps before it, is built in this frame, so a nested
        schema costs no more frames than the parse methods it reaches. A $ref merged with its sibling keywords is
        built in the dynamic scope of the schema it names. The type returned records the location of ``item`` as its
        origin (``_origins.with_type_origin``).
        """
        location = item.schema_location
        with _schema_refs.NO_SCOPE if (scope := _schema_refs.subschema_scope(self, item)) is None else scope:
            choice = self._FIELD_TABLE.choose(
                self, SchemaSite(name, self._site_schema(item), path, singular_name=singular_name, parent=parent)
            )
            with _schema_refs.NO_SCOPE if (merged_scope := choice.site.scope) is None else merged_scope:
                return with_unique_items(
                    with_type_origin(choice.build(self, choice.site), location), choice.site.obj.uniqueItems
                )

    def _has_materialized_container_constraints(self, item: JsonSchemaObject) -> bool:
        """Return whether an inherited container shape materialized on the item keeps constraints of its own."""
        return bool(
            item.__dict__.get(_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY)
            and (item.is_array or item.is_object or item.propertyNames is not None)
            and self._has_effective_constraints(item)
        )

    def _types_item_by_root_model(self, site: _Site) -> bool:
        """Return whether an item of a parent writes its constraints on a root model of its own."""
        item, parent = site.obj, site.parent
        return bool(
            parent
            and not item.enum
            and not _value_keywords.const_literal_enforces_constraints(self, item)
            and (
                self._has_materialized_container_constraints(item)
                or (item.has_constraint and (parent.has_constraint or self.field_constraints))
            )
        )

    def _parse_constrained_item(self, site: _Site) -> DataType:
        return self._parse_item_root(site, preserve_constraints=self._has_materialized_container_constraints(site.obj))

    def _writes_item_counts_on_alias(self, item: JsonSchemaObject) -> bool:
        """Return whether a nested array or map writes its counts on a type alias of its own, as no field holds them.

        Without --field-constraints, under which a nested array writes them on a root model of its own, an output that
        writes constraints in ``Annotated`` (``supports_internal_annotated_constraints``) gives the array the
        ``Annotated`` form a member of a type list takes, where it declares item counts (``_has_effective_constraints``)
        that its type does not fix itself, as a fixed-length tuple does, and no type list or enum its own build writes.
        A map of no properties (``additionalProperties`` or ``propertyNames``) takes the form for its property counts
        alike, which no output writes elsewhere at such a site, also under --field-constraints.
        """
        return bool(
            ((item.is_array and not self.field_constraints) or _is_counted_map(item))
            and self._output_model_context.supports_internal_annotated_constraints
            and not item.enum
            and not self._get_array_union_non_array_types(item)
            and self._has_effective_constraints(item)
            and not self._is_fixed_length_tuple(item)
        )

    def _types_item_counts_by_alias(self, site: _Site) -> bool:
        """Return whether a union member or the items of an array (a site with a parent) take an item-count alias.

        A map takes one as the items of an array only: a union member that is a map keeps the type it shares with the
        other maps of the union, as a union that holds two maps cannot be decoded by msgspec.
        """
        return (
            site.parent is not None
            and (site.parent.is_array or not _is_counted_map(site.obj))
            and self._writes_item_counts_on_alias(site.obj)
        )

    def _parse_item_counts_alias(self, site: _Site) -> DataType:
        """Parse a nested array as the type alias that holds its item counts, marked as an item-count site.

        The alias and its Request/Response variants take the mark from the site schema (``_record_schema_object``,
        ``ITEM_COUNTS_SITE_KEY``).
        """
        site.obj.__dict__[ITEM_COUNTS_SITE_KEY] = True
        return self._parse_item_root(site, annotated=True)

    def _parse_item_root(self, site: _Site, *, annotated: bool = False, preserve_constraints: bool = False) -> DataType:
        """Parse the schema of an item site as a root model of its own, named after the site.

        ``annotated``: the root of a constrained value (``_parse_annotated_root``).
        """
        root_type_path = get_special_path("array", site.path)
        name = self.model_resolver.add(
            root_type_path,
            site.name,
            class_name=True,
            singular_name=site.singular_name,
        ).name
        if annotated:
            return JsonSchemaParser._parse_annotated_root(
                self, name, site.obj, root_type_path, root_class=self._nested_constrained_model_type
            )
        return self._parse_root_type_with_context(
            name, site.obj, root_type_path, preserve_constraints=preserve_constraints
        )

    def _parse_ref_nullable_only_item(self, site: _Site) -> DataType:
        item = site.obj
        ref_data_type = self.get_ref_data_type(cast("str", item.ref))
        if self._null_facts(item, "ref-nullable-only").admits_null:
            return build_union(self.data_type, [ref_data_type], nullable=True)
        return ref_data_type

    def _parse_custom_type_item(self, site: _Site) -> DataType:
        return self._custom_type(site.obj)

    def _custom_type(self, obj: JsonSchemaObject) -> DataType:
        """Return the type of a schema that ``customTypePath`` gives, the class that dotted path names."""
        self._refuse_untrusted_code("customTypePath", obj.custom_type_path)
        return self.data_type_manager.get_data_type_from_full_path(
            _type_overrides.get_emitted_python_type_path(
                self,
                _validate_schema_python_import_path(
                    cast("str", obj.custom_type_path), "customTypePath", self.target_python_version
                ),
            ),
            is_custom_type=True,
        )

    def _record_site_constraints(self, obj: JsonSchemaObject, data_type: DataType) -> DataType:
        """Keep on ``data_type`` the constraints the allOf site ``obj`` adds to the target of its first ``$ref``.

        ``data_type`` is the type of the site where it names that target alone (``reference_site_constraints``); a
        type that names another model keeps none. Return ``data_type``.
        """
        if (
            (reference := data_type.reference) is not None
            and (first := next((item for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref), None))
            and reference.path == self._ref_path(cast("str", first.ref))
            and (constraints := _all_of_values.reference_site_constraints(self, obj))
        ):
            self._graph_builder.record_site_constraints(data_type, constraints)
        return data_type

    def _parse_all_of_field_item(self, site: _Site) -> DataType:
        item, name, path, singular_name = site.obj, site.name, site.path, site.singular_name
        if self._contains_false_schema(item.allOf):
            _all_of_values.raise_unsatisfiable_schema(self, item, get_special_path("allOf", path), "allOf")
        all_of_items = [sub_item for sub_item in item.allOf if isinstance(sub_item, JsonSchemaObject)]
        if len(all_of_items) == 1 and len(all_of_items) != len(item.allOf) and not item.properties:
            return self.parse_item(name, all_of_items[0], path, singular_name=singular_name, parent=site.parent)
        all_of_path = [self.model_resolver.resolve_ref(get_special_path("allOf", path))]
        if not self._schema_requires_model_type(item) and not _all_of_values.allof_requires_model_type(
            self, item.allOf
        ):
            root_model_name = self.model_resolver.add(
                all_of_path, name, singular_name=singular_name, class_name=True
            ).name
            if "legacy-value-union-members-unread" not in self._legacy_intersection and (
                value_union := _all_of_values.all_of_value_union_site(self, item, objects_admitted=True)
            ):
                return self._parse_all_of_merged_root(
                    root_model_name, value_union.with_schema_location(item.schema_location), all_of_path
                )
            return self.parse_root_type(root_model_name, item, all_of_path)
        if len(item.allOf) == 1 and not item.properties:
            single_item = item.allOf[0]
            if isinstance(single_item, JsonSchemaObject) and single_item.ref:
                if (reference_site := _all_of_values.reference_site_schema(self, item)) is not None:
                    with self.model_resolver.yielding_new_references():
                        site_type = self._parse_all_of_merged_root(
                            self.model_resolver.add(
                                all_of_path, name, singular_name=singular_name, class_name=True
                            ).name,
                            reference_site.with_schema_location(item.schema_location),
                            all_of_path,
                        )
                    self._site_model_keys.add(model_key_of(site_type.reference))
                    return site_type
                if not self._adds_required_names(
                    self._get_inline_required_names(item), partial(self._ref_required_names, single_item.ref)
                ):
                    return self._record_site_constraints(item, self.get_ref_data_type(single_item.ref))
        with (
            self.model_resolver.yielding_new_references()
            if self.config.use_site_models_for_constrained_refs
            else nullcontext()
        ):
            site_name = self.model_resolver.add(all_of_path, name, singular_name=singular_name, class_name=True).name
        return self.parse_all_of(site_name, item, all_of_path, ignore_duplicate_model=True)

    def _parse_object_item(self, site: _Site) -> DataType:
        item, name, path = site.obj, site.name, site.path
        if not item.properties:
            return self._parse_object_map(name, item, path, get_special_path("object", path))
        return self._build_field_schema_validator_object(site)

    def _parse_enum_item(self, site: _Site) -> DataType:
        item, name = site.obj, site.name
        if self._reads_enum_as_const(item):
            return self._enum_as_const_type(item)
        if self.should_parse_enum_as_literal(item, property_name=name):
            return self._enum_value_type(item, self.parse_enum_as_literal(item))
        return self._parse_enum_class(name, item, get_special_path("enum", site.path), singular_name=site.singular_name)

    def _parse_typed_nullable_map_item(self, site: _Site) -> DataType:
        item, name, path = site.obj, site.name, site.path
        return self._parse_object_map(
            name, item, path, get_special_path("object", path), typed_values=self._typed_nullable_map_value_schema(item)
        )

    def _field_dynamic_ref_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool((site.obj.recursiveRef or site.obj.dynamicRef) and not site.obj.ref)

    def _build_field_dynamic_ref(self, site: _Site) -> DataType:
        return _schema_refs.get_dynamic_scope_data_type(self, site.obj, site.path)

    def _field_ref_nullable_only_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.is_ref_with_nullable_only and site.obj.ref)

    def _field_python_type_applies(self, site: _Site) -> bool:
        return self._overriding_python_type(site.obj) is not None

    def _build_field_python_type(self, site: _Site) -> DataType:
        return cast("DataType", self._get_python_type_override(site.obj))

    def _field_title_name_applies(self, site: _Site) -> bool:
        return bool(self.use_title_as_name and site.obj.title)

    def _rewrite_field_title_name(self, site: _Site) -> _Site:
        return replace(
            site,
            name=sanitize_module_name(cast("str", site.obj.title), treat_dot_as_module=self.treat_dot_as_module),
            singular_name=False,
        )

    def _field_title_alias_applies(self, site: _Site) -> bool:
        return self._should_create_type_alias_for_title(site.obj, site.name)

    def _merge_field_ref(self, site: _Site) -> _Site:
        """Merge the `$ref` of a field site with its sibling keywords, and read the values of a false allOf."""
        return self._merge_site_ref(site, read_false_all_of=True)

    def _merge_site_ref(self, site: _Site, *, read_false_all_of: bool) -> _Site:
        """Merge the `$ref` of a site with its sibling keywords, in the dynamic scope the merged schema names.

        ``read_false_all_of``: the merged schema takes the type of the values a false allOf writes
        (``_read_false_all_of_values``), as a field's does.
        """
        if (merged := self._merge_ref_with_schema(site.obj)) is site.obj:
            return site
        return replace(
            site,
            obj=self._read_false_all_of_values(merged) if read_false_all_of else merged,
            scope=_schema_refs.subschema_scope(self, merged),
        )

    def _field_custom_type_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.custom_type_path)

    def _field_constrained_type_union_applies(self, site: _Site) -> bool:
        return not self._has_shared_numeric_union_constraints(site.obj) and self._localizes_constrained_type_union(
            site.obj
        )

    def _build_field_constrained_type_union(self, site: _Site) -> DataType:
        return self._build_constrained_type_union(site.name, site.obj, site.path)

    def _build_field_array(self, site: _Site) -> DataType:
        return self.parse_array_fields(site.name, site.obj, get_special_path("array", site.path)).data_type

    def _field_discriminated_union_item_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(
            site.obj.discriminator and site.parent and site.parent.is_array and (site.obj.oneOf or site.obj.anyOf)
        )

    def _build_field_schema_validator_object(self, site: _Site) -> DataType:
        return self._join_object_type_array(
            site,
            self.parse_object(
                site.name, site.obj, get_special_path("object", site.path), singular_name=site.singular_name
            ),
        )

    def _field_object_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.is_object) or _declares_key_keywords(site.obj)

    def _field_ref_site_applies(self, site: _Site) -> bool:
        """Return whether a field site is an object with properties merged from a `$ref` and its sibling keywords."""
        return bool(
            _MERGED_REF_TARGET_KEY in site.obj.__dict__
            and site.obj.properties
            and site.obj.schema_location is not None
            and self._field_object_applies(site)
        )

    def _build_field_ref_site(self, site: _Site) -> DataType:
        """Build the type of an object `$ref` with sibling keywords.

        Under --use-one-model-per-ref-site the site gets one model, which every copy of it shares
        (``_schema_refs.ref_site_reference``). Without it, row ``legacy-merged-site-per-path``, and for a copy a merge
        changed, each path that reaches the site parses a model of its own in place, one nested parse per hop of a
        chain. A recursion limit exceeded in a site that another site holds names the option
        (``MergedRefSiteRecursionError``), where the option would parse the chain without nesting: without it and
        unless request and response models are split, as the option then parses a site where it is first met too.
        """
        if self.config.use_one_model_per_ref_site:
            if reference := _schema_refs.ref_site_reference(self, site):
                return self._join_object_type_array(site, self.data_type(reference=reference))
            return self._parse_object_item(site)
        if self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
            return self._parse_object_item(site)
        try:
            return self._parse_object_item(site)
        except MergedRefSiteRecursionError:
            raise
        except _RefSiteRecursionError as error:
            raise MergedRefSiteRecursionError(*error.args) from error
        except RecursionError as error:
            raise _RefSiteRecursionError(*error.args) from error

    def _field_validated_ref_site_applies(self, site: _Site) -> bool:
        """Return whether, under --use-one-model-per-ref-site, a `$ref` site's object carries schema validators.

        Such a site is built as the object model of its validators (row ``schema-validator-object``) without the
        option, which builds the same model; with it the site gets its one model as any other site does.
        """
        return bool(
            self.config.use_one_model_per_ref_site
            and _schema_validator_object_applies(self, site)
            and self._field_ref_site_applies(site)
        )

    def _field_enum_applies(self, site: _Site) -> bool:
        return self._reads_enum(site.obj)

    def _field_shared_numeric_constraints_applies(self, site: _Site) -> bool:
        return self._has_shared_numeric_union_constraints(site.obj)

    def _build_field_shared_numeric_constraints(self, site: _Site) -> DataType:
        return self._get_data_type(site.obj, localize_constraints=False)

    def _field_typed_nullable_map_applies(self, site: _Site) -> bool:
        return self._typed_nullable_map_value_schema(site.obj) is not None

    def _build_field_data_type(self, site: _Site) -> DataType:
        return self.get_data_type(site.obj)

    def _types_site_model(self, site: _Site) -> Iterator[TypedChild[JsonSchemaObject]]:  # noqa: PLR6301
        """Type the model built from the schema of a field site, unless its row declares otherwise."""
        yield TypedChild("model", site.obj)

    def _types_nothing(self, site: _Site) -> Iterator[TypedChild[JsonSchemaObject]]:  # noqa: ARG002, PLR6301
        """Type no child and name no model: the site is typed by its customTypePath."""
        yield from ()

    def _types_constrained_item(self, site: _Site) -> tuple[TypedChild[JsonSchemaObject]]:
        """Type the root model that holds the constraints of an item, built from its schema merged with its $ref."""
        return (TypedChild("root", self._merge_field_ref(site).obj),)

    _FIELD_TABLE: ClassVar[DispatchTable[JsonSchemaParser, JsonSchemaObject, DataType]] = DispatchTable(
        (
            Row("python-type", _field_python_type_applies, _build_field_python_type, typed=_types_nothing),
            Step("title-name", _field_title_name_applies, _rewrite_field_title_name, keywords=frozenset({"title"})),
            Row("title-alias", _field_title_alias_applies, _parse_root_type_site, keywords=frozenset({"title"})),
            Step("ref-siblings", _ref_siblings_applies, _merge_field_ref, keywords=frozenset({"ref"})),
            Row(
                "constrained-item",
                _types_item_by_root_model,
                _parse_constrained_item,
                typed=_types_constrained_item,
            ),
            Row(
                "dynamic-ref",
                _field_dynamic_ref_applies,
                _build_field_dynamic_ref,
                keywords=frozenset({"recursiveRef", "dynamicRef"}),
            ),
            Row(
                "ref-nullable-only",
                _field_ref_nullable_only_applies,
                _parse_ref_nullable_only_item,
                keywords=frozenset({"ref"}),
            ),
            Row("ref", _ref_applies, _get_ref_data_type_site, keywords=frozenset({"ref"})),
            Row("custom-type", _field_custom_type_applies, _parse_custom_type_item, typed=_types_nothing),
            Row(
                "constrained-type-union",
                _field_constrained_type_union_applies,
                _build_field_constrained_type_union,
            ),
            Row(
                "item-counts-alias",
                _types_item_counts_by_alias,
                _parse_item_counts_alias,
                typed=_types_constrained_item,
            ),
            Row("array", _array_applies, _build_field_array),
            Row(
                "discriminated-union-item",
                _field_discriminated_union_item_applies,
                _parse_root_type_site,
                keywords=frozenset({"discriminator"}),
            ),
            Row(
                "validated-ref-site",
                _field_validated_ref_site_applies,
                _build_field_ref_site,
                keywords=frozenset({"properties"}),
            ),
            Row(
                "schema-validator-object",
                _schema_validator_object_applies,
                _build_field_schema_validator_object,
            ),
            _field_union_row("any-of", "anyOf"),
            _field_union_row("one-of", "oneOf"),
            Row("all-of", _all_of_applies, _parse_all_of_field_item, keywords=frozenset({"allOf"})),
            Row("ref-site", _field_ref_site_applies, _build_field_ref_site, keywords=frozenset({"properties"})),
            Row("object", _field_object_applies, _parse_object_item),
            Row("enum", _field_enum_applies, _parse_enum_item, keywords=frozenset({"enum"})),
            Row(
                "shared-numeric-constraints",
                _field_shared_numeric_constraints_applies,
                _build_field_shared_numeric_constraints,
            ),
            Row("typed-nullable-map", _field_typed_nullable_map_applies, _parse_typed_nullable_map_item),
        ),
        Default("data-type", _build_field_data_type),
        typed=_types_site_model,
    )

    def _join_object_type_array(self, site: _Site, model_type: DataType) -> DataType:
        """Join the model of an object in a union with the other types of its type array."""
        item = site.obj
        if not (self._joins_other_types(item) and isinstance(item.type, list)):
            return model_type
        return build_union(
            self.data_type, [model_type, *self._iter_non_object_union_types(site.name, item, item.type, site.path)]
        )

    def parse_list_item(
        self,
        name: str,
        target_items: Sequence[JsonSchemaObject | bool],
        path: list[str],
        parent: JsonSchemaObject,
        singular_name: bool = True,  # noqa: FBT001, FBT002
    ) -> list[DataType]:
        """Parse a list of items into data types."""
        return [
            self.data_type_manager.get_data_type(Types.any)
            if item is True
            else self.parse_item(
                name,
                item,
                [*path, str(index)],
                singular_name=singular_name,
                parent=parent,
            )
            for index, item in enumerate(target_items)
            if item is not False
        ]

    def _has_shared_numeric_union_constraints(self, obj: JsonSchemaObject) -> bool:
        """Keep shared numeric bounds on fields whose branches all support those bounds."""
        return (
            self.field_constraints
            and self._output_model_context.writes_constrained_types
            and isinstance(obj.type, list)
            and all(type_ in {"integer", "number", "null"} for type_ in obj.type)
            and all(getattr(obj, key) is None for key in ("minItems", "maxItems", *STRING_KEYWORD_ORDER))
        )

    def _localizes_constrained_type_union(self, obj: JsonSchemaObject) -> bool:
        """Return whether the branches of a type list need annotated aliases instead of constrained scalar types."""
        if (
            not isinstance(obj.type, list)
            or not self._should_skip_root_field_constraints_for_multiple_types(obj)
            or obj.is_array
            or obj.properties
        ):
            return False
        if obj.anyOf or obj.oneOf or obj.allOf or obj.enum or obj.extras.get("x-python-type"):
            return False
        if not self._output_model_context.supports_internal_annotated_constraints or not (
            _inherited_schemas.get_inherited_constraint_fields(self, obj) or obj.propertyNames is not None
        ):
            return False
        if self._output_model_context.writes_constrained_types:
            return "array" in obj.type or "object" in obj.type
        return self._output_model_context.supports_annotated_constraints

    def _build_constrained_type_union(self, name: str, obj: JsonSchemaObject, path: list[str]) -> DataType:
        """Build the union of a type list whose branches take annotated aliases."""
        data_types = []
        for type_ in non_null_types(cast("list[str]", obj.type)):
            if type_ == "object" and _declares_key_keywords(obj):
                branch_schema = self._get_array_union_branch_schema(obj, type_)
                branch_path = get_special_path("type-union-object", path)
                object_name = composed_name(name, "object")
                if obj.patternProperties:
                    data_type = self.parse_item(object_name, branch_schema, branch_path)
                else:
                    data_type = JsonSchemaParser._parse_annotated_root(
                        self, object_name, branch_schema, branch_path, root_class=self._nested_constrained_model_type
                    )
            else:
                data_type = self._type_list_branch(name, obj, path, type_, localize=True)
            data_types.append(data_type)
        return build_union(self.data_type, data_types, nullable=bool(self._null_facts(obj, "type-union").admits_null))

    def _get_array_union_non_array_types(  # noqa: PLR6301
        self,
        obj: JsonSchemaObject,
    ) -> tuple[str, ...]:
        """Return the non-array branches of a heterogeneous array type union."""
        match obj.type:
            case list() as type_list if "array" in type_list:
                return tuple(type_ for type_ in non_null_types(type_list) if type_ != "array")
        return ()

    def _add_array_union_non_array_types(  # noqa: PLR0913
        self,
        data_types: list[DataType],
        obj: JsonSchemaObject,
        non_array_types: tuple[str, ...],
        *,
        name: str,
        path: list[str],
        localize_constraints: bool,
    ) -> int:
        """Add the type-union branches that array parsing does not materialize itself, and return how many."""
        if not non_array_types or self._reads_enum(obj):
            return 0

        count = len(data_types)
        for type_ in non_array_types:
            if type_ == "object" and obj.is_object:
                continue
            data_types.append(self._type_list_branch(name, obj, path, type_, localize=localize_constraints))
        return len(data_types) - count

    def _type_list_branch(
        self, name: str, obj: JsonSchemaObject, path: list[str], type_: str, *, localize: bool
    ) -> DataType:
        """Return the type of the member ``type_`` of the type list of ``obj``, other than null.

        A localized member takes the constraints of ``obj`` in an annotated alias, where they apply to it. Otherwise
        the object member whose map values this run types is that map, and any other member is its type and format
        without constraints.
        """
        typed_map = self._parse_typed_union_map(name, obj, path, type_)
        if localize:
            return self._parse_array_union_constrained_branch(name, obj, path, type_, typed_map)
        return typed_map or self._get_format_data_type(obj, type_)

    def _parse_typed_union_map(self, name: str, obj: JsonSchemaObject, path: list[str], type_: str) -> DataType | None:
        """Return the map type of the object member of a type array whose map values this run types, else None.

        patternProperties or propertyNames make the member the object map of the object branch.
        """
        if type_ != "object" or (typed_values := self._typed_nullable_map_value_schema(obj)) is None:
            return None
        object_path = get_special_path("object", path)
        if _declares_key_keywords(obj):
            return self._parse_object_map(
                name, self._get_array_union_branch_schema(obj, "object"), path, object_path, typed_values=typed_values
            )
        return _object_maps.parse_nullable_map(self, name, obj, object_path, typed_values)

    def _should_localize_array_union_constraints(
        self,
        obj: JsonSchemaObject,
        non_array_types: tuple[str, ...] | None = None,
    ) -> bool:
        """Return whether this output can retain heterogeneous union constraints per branch."""
        if (
            not self._output_model_context.supports_internal_annotated_constraints
            or self._reads_enum(obj)
            or not (non_array_types if non_array_types is not None else self._get_array_union_non_array_types(obj))
        ):
            return False
        return bool(
            _inherited_schemas.get_inherited_constraint_fields(self, obj) or self._get_array_items_constraints(obj)
        )

    def _get_array_union_branch_schema(self, obj: JsonSchemaObject, type_: str) -> JsonSchemaObject:
        """Build a shallow schema retaining only keywords valid for one union branch."""
        schema = obj.model_dump(exclude_unset=True, by_alias=True)
        distributed_fields = _inherited_schemas.get_inherited_distributed_fields(self, obj)
        branch_schema = self._select_inherited_distributed_shape(schema, distributed_fields, frozenset({type_}))
        branch_schema["type"] = type_
        if obj.format is not None:
            branch_schema["format"] = obj.format
        return _locate(self._schema_object(branch_schema), obj.schema_location)

    def _parse_array_union_constrained_branch(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        type_: str,
        fallback_data_type: DataType | None = None,
    ) -> DataType:
        """Parse a constrained union branch through the backend-compatible alias path."""
        branch_schema = self._get_array_union_branch_schema(obj, type_)
        if not self._has_effective_constraints(branch_schema):
            return fallback_data_type or self._get_format_data_type(obj, type_)

        branch_path = get_special_path(f"array-union-{type_}", path)
        branch_name = self.model_resolver.add(
            branch_path,
            composed_name(name, "type", word=type_),
            class_name=True,
        ).name
        return JsonSchemaParser._parse_annotated_root(
            self, branch_name, branch_schema, branch_path, root_class=self._nested_constrained_model_type
        )

    def _mark_root_pattern_string_constraints(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Retain effective string bounds for a witnessed plain root-array pattern."""
        if (
            not self.field_constraints
            or not self.generate_schema_validators
            or self.custom_template_dir
            or self.base_class
        ):
            return
        if (
            self.base_class_map
            or self.config.extra_template_data
            or type(self) is not JsonSchemaParser
            or path[:-1] != list(self.model_resolver.current_root or ["#"])
        ):
            return
        if (
            not self._configured_generation_types_are_builtin
            or not self.data_model_type.SUPPORTS_SCHEMA_RUNTIME_VALIDATION
            or obj.type != "array"
            or not obj.model_fields_set <= {"type", "items", "title", "extras"}
        ):
            return
        if (
            not obj.extras.keys() <= {"$schema", *DEFINITION_KEYS, "title"}
            or not isinstance(item := obj.items, JsonSchemaObject)
            or item.type != "object"
            or not item.model_fields_set <= {"type", "patternProperties"}
            or len(patterns := item.patternProperties or {}) != 1
        ):
            return
        pattern, value = next(iter(patterns.items()))
        if (
            not pattern.startswith("^")
            or not pattern[1:].isascii()
            or not pattern[1:].isalnum()
            or not isinstance(value, JsonSchemaObject)
            or value.type != "string"
        ):
            return
        if (
            not value.model_fields_set <= {"type", "minLength", "maxLength"}
            or not ((value.minLength or 0) > 0 or value.maxLength is not None)
            or (value.maxLength is not None and value.maxLength < (value.minLength or 0))
        ):
            return
        self._root_pattern_string_constraints = value

    def parse_array_fields(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        singular_name: bool = True,  # noqa: FBT001, FBT002
        use_annotated: bool | None = None,  # noqa: FBT001
    ) -> DataModelFieldBase:
        """Parse array schema into a data model field with list type."""
        # Strict mode: check for version-specific array features
        self._check_array_version_features(obj, path)
        self._mark_root_pattern_string_constraints(obj, path)
        use_annotated = self.use_annotated if use_annotated is None else use_annotated

        required, nullable = _value_keywords.resolve_array_field_required_nullable(self, obj)
        items, is_tuple, suppress_item_constraints, tuple_item_count = self._get_array_item_schemas(obj)

        if obj.prefixItems is not None and not is_tuple:
            _value_keywords.warn_unchecked_prefix_positions(self, obj)

        if items:
            item_data_types = self.parse_list_item(
                name,
                items,
                path,
                obj,
                singular_name=singular_name,
            )
        elif not (is_tuple and tuple_item_count == 0):
            item_data_types = [
                self.data_type_manager.data_type(type="object")
                if self.data_type_manager.use_object_type
                else self.data_type_manager.get_data_type(Types.any)
            ]
        else:
            item_data_types = []

        container_flags: dict[str, Any] = {}
        if not is_tuple:
            container_flags = self._container_flags(obj, "list")

        non_array_types = self._get_array_union_non_array_types(obj)
        localize_constraints = self._should_localize_array_union_constraints(obj, non_array_types)
        array_data_type = with_unique_items(
            self.data_type(
                data_types=item_data_types,
                is_tuple=is_tuple,
                tuple_item_count=tuple_item_count,
                **container_flags,
            ),
            obj.uniqueItems,
        )
        if localize_constraints:
            array_data_type = self._parse_array_union_constrained_branch(
                name,
                obj,
                path,
                "array",
                fallback_data_type=array_data_type,
            )
        data_types = [array_data_type]
        non_array_count = self._add_array_union_non_array_types(
            data_types,
            obj,
            non_array_types,
            name=name,
            path=path,
            localize_constraints=localize_constraints,
        )
        # TODO: decide special path word for a combined data model.
        if obj.allOf:
            data_types.append(self.parse_all_of(name, obj, get_special_path("allOf", path)))
        elif obj.is_object:
            data_types.append(self.parse_object(name, obj, get_special_path("object", path)))
        if self._reads_enum(obj):
            data_types.append(
                self._enum_as_const_type(obj)
                if self._reads_enum_as_const(obj)
                else self.parse_enum(name, obj, get_special_path("enum", path))
            )
        constraints = _value_keywords.get_array_constraints(self, obj, array_data_type)
        if non_array_types:
            constraints = _value_keywords.type_list_keywords(obj)
        if suppress_item_constraints:
            _value_keywords.suppress_array_length_constraints(self, constraints, obj)
        return with_type_list_unique_items(
            self._build_field(
                "array-root",
                build_union(
                    self.data_type,
                    data_types,
                    "legacy-non-array-first",
                    nullable=bool(self._null_facts(obj, "type-union").admits_null),
                    roles=(
                        "member",
                        *("non-array",) * non_array_count,
                        *("member",) * (len(data_types) - non_array_count - 1),
                    ),
                ),
                default=obj.default,
                required=required,
                constraints=constraints,
                nullable=nullable,
                extras=self.get_field_extras(obj),
                use_annotated=use_annotated,
                original_name=None,
                has_default=obj.has_default,
            ),
            declares=bool(non_array_types and constraints),
        )

    def parse_array(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        original_name: str | None = None,
    ) -> DataType:
        """Parse array schema into a root model with array type.

        A type list that lists the array beside other types and whose items name the array itself is written as the
        union of a list of those other types and the types (``Union[List[X], X]``). Row
        ``legacy-self-reference-array-untyped`` also rewrites a plain array whose items name it, which has no other
        types, so its items are lost (``RootModel[list]``), and builds the rewritten root field without
        --use-serialize-as-any, as 0.90.0 did; --keep-self-referencing-array-items leaves the row out, except in msgspec
        output (``requires_tagged_union_discriminator``, as ``warn_about_msgspec_decoding`` reads it), whose type alias
        of such an array would name itself through no Struct, which msgspec refuses to decode, so the bare ``list``
        stays there.
        """
        with self._model_frame() as frame:
            name = self._apply_title_as_name(name, obj)
            reference = self.model_resolver.add(path, name, loaded=True, class_name=True)
            self._record_schema_object(reference, obj)
            field = self.parse_array_fields(original_name or name, obj, [*path, name])

            non_array_types = self._get_array_union_non_array_types(obj)
            direct_data_types = field.data_type.data_types
            keeps_items = (
                self.keep_self_referencing_array_items
                and not self._output_model_context.requires_tagged_union_discriminator
            )
            rewrites_plain_array = not (non_array_types or keeps_items)
            if (
                not self._should_localize_array_union_constraints(obj, non_array_types)
                and direct_data_types
                and any(
                    data_type.reference is reference
                    for data_type in direct_data_types[0].all_data_types
                    if data_type.reference
                )
                and (len(direct_data_types) > 1 or rewrites_plain_array)
            ):
                recursive_tuple_item_count = direct_data_types[0].tuple_item_count
                declares_type_list_unique = declares_type_list_unique_items(field)
                field = self._build_field(
                    "array-root" if keeps_items else "root",
                    build_union(
                        self.data_type,
                        [
                            self.data_type(
                                data_types=direct_data_types[1:],
                                is_list=recursive_tuple_item_count is None,
                                is_tuple=recursive_tuple_item_count is not None,
                                tuple_item_count=recursive_tuple_item_count,
                            ),
                            *direct_data_types[1:],
                        ],
                    ),
                    default=field.default,
                    required=field.required,
                    constraints=field.constraints,
                    nullable=field.nullable,
                    extras=field.extras,
                    original_name=None,
                    has_default=field.has_default,
                )
                with_type_list_unique_items(field, declares=declares_type_list_unique)

            self._register_root_model(
                reference=reference,
                fields=[field],
                obj=obj,
                custom_base_class_name=class_key("predicted", name),
                description=obj.description if self.use_schema_description else None,
            )
            return frame.built(self.data_type(reference=reference))

    def parse_root_type(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataType:
        """Parse a root-level type into a root model."""
        with self._model_frame() as frame:
            return frame.built(JsonSchemaParser._parse_root_type_with_context(self, name, obj, path))

    @contextmanager
    def _temporarily_enable_field_constraints(self) -> Generator[None, None, None]:
        """Render an internal annotated alias with Field constraints without bypassing parser hooks."""
        if self.field_constraints:
            yield
            return

        self.field_constraints = True
        try:
            yield
        finally:
            self.field_constraints = False

    def _parse_annotated_root(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        *,
        root_class: type[DataModel] | None,
        root_type: DataType | None = None,
    ) -> DataType:
        """Parse a constrained value as a root model of ``root_class`` that keeps its constraints in ``Annotated``.

        Called as ``JsonSchemaParser._parse_annotated_root(self, ...)``, and it calls the root type parse the same way,
        so a parser extension's private names do not change the constrained values.
        """
        return JsonSchemaParser._parse_root_type_with_context(
            self,
            name,
            obj,
            path,
            data_model_root_type=root_class,
            preserve_constraints=True,
            use_annotated=True,
            root_type=root_type,
        )

    def _parse_root_type_with_context(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        *,
        data_model_root_type: type[DataModel] | None = None,
        preserve_constraints: bool = False,
        use_annotated: bool | None = None,
        root_type: DataType | None = None,
    ) -> DataType:
        """Parse a root type with an internal output representation override; ``root_type`` is a type already built."""
        reference: Reference | None = None
        array_constraints: Any = None
        constraint_obj = obj
        effective_use_annotated = self.use_annotated if use_annotated is None else use_annotated
        writes = (self.field_constraints or preserve_constraints) and not (
            self._should_skip_root_field_constraints_for_multiple_types(obj)
        )
        match "built" if root_type is not None else self._root_type_row(obj):
            case "built":
                data_type: DataType = cast("DataType", root_type)
            case "ref":
                data_type = self.get_ref_data_type(cast("str", obj.ref))
                if constraints := obj.__dict__.get(_SITE_CONSTRAINTS_SCHEMA_KEY):
                    self._graph_builder.record_site_constraints(data_type, constraints)
            case "custom-type":
                data_type = self._custom_type(obj)  # pragma: no cover
            case "constrained-type-union":
                data_type = self._build_constrained_type_union(name, obj, path)
            case "array":
                array_field = self.parse_array_fields(
                    name,
                    obj,
                    get_special_path("array", path),
                    use_annotated=use_annotated,
                )
                data_type = array_field.data_type  # pragma: no cover
                if preserve_constraints:
                    array_constraints = array_field.constraints
            case "combined":
                combined_items = obj.anyOf or obj.oneOf
                if const_enum_type := self._parse_combined_const_enum(name, obj, combined_items, path, writes=writes):
                    data_type = const_enum_type  # pragma: no cover
                else:
                    reference = self.model_resolver.add(path, name, loaded=True, class_name=True)
                    if obj.anyOf:
                        data_types: list[DataType] = self.parse_any_of(name, obj, get_special_path("anyOf", path))
                    else:
                        data_types = self.parse_one_of(name, obj, get_special_path("oneOf", path))

                    if len(data_types) > 1:  # pragma: no cover
                        data_type = build_union(self.data_type, data_types)
                    elif not data_types:  # pragma: no cover
                        return EmptyDataType()
                    else:  # pragma: no cover
                        data_type = data_types[0]
            case "all-of":
                value_obj = obj
                parts: list[JsonSchemaObject] = []
                if (
                    "legacy-all-of-site-form" not in self._legacy_intersection
                    and _all_of_values.collect_all_of_value_parts(self, obj, parts)
                ):
                    value_obj = inline_value = _all_of_values.intersect_all_of_value_parts(
                        self,
                        parts,
                        _all_of_values.all_of_value_row(self, obj),
                        named_location(obj.schema_location, "allOf"),
                    )
                    if not (inline_value.enum or "const" in inline_value.extras):
                        constraint_obj = inline_value
                elif (self.field_constraints or preserve_constraints) and (
                    referenced_value := _all_of_values.all_of_reference_value_schema(self, obj)
                ) is not None:
                    value_obj = constraint_obj = referenced_value
                elif (site_keywords := _all_of_values.legacy_site_keywords(self, obj)) is not None:
                    constraint_obj = site_keywords
                data_type = (
                    self._enum_literal(
                        value_obj, _value_keywords.literal_written(self, constraint_obj, value_obj.enum, writes=writes)
                    )
                    if value_obj.enum and "const" not in value_obj.extras
                    else self._build_lightweight_type(value_obj)
                )
                if data_type is None:  # pragma: no cover
                    data_type = self.data_type_manager.get_data_type(Types.any)
                elif value_obj is obj and data_type.reference is not None:
                    self._record_site_constraints(obj, data_type)
            case "object-map":
                data_type = self._parse_object_map(name, obj, path, path)
            case "enum":
                if self._reads_enum_as_const(obj):
                    data_type = self._enum_as_const_type(obj)
                elif self.should_parse_enum_as_literal(obj, property_name=name):
                    narrowed = self._const_narrowed_enum(obj)
                    data_type = with_enum_literal(
                        self._enum_literal(
                            narrowed, _value_keywords.literal_written(self, narrowed, narrowed.enum, writes=writes)
                        )
                    )
                else:  # pragma: no cover
                    data_type = self.parse_enum(name, obj, path)
            case "typed-nullable-map":
                typed_values = self._typed_nullable_map_value_schema(obj)
                reference = self.model_resolver.add(
                    path, self._apply_title_as_name(name, obj), loaded=True, class_name=True
                )
                self.typed_nullable_map_reference_ids.add(model_key_of(reference))
                data_type = self._parse_object_map(name, obj, path, path, typed_values=typed_values)
            case "type":
                if preserve_constraints and effective_use_annotated:
                    with self._temporarily_enable_field_constraints():
                        data_type = self.get_data_type(obj)
                else:
                    data_type = self.get_data_type(obj)
            case _:
                data_type = self.data_type_manager.get_data_type(
                    Types.any,
                )
        selected_root_type = data_model_root_type or self.data_model_root_type
        is_type_alias = selected_root_type.IS_ALIAS
        root_null = self._null_facts(obj, "root-type")
        if (
            root_null.required is False
            and obj.__dict__.get(_DEFAULT_LEFT_OFF_KEY)
            and not self._null_mode.force_optional
        ):
            required = True
            nullable = root_null.admits_null
            has_default_override = obj.has_default
            default_value = obj.default if obj.has_default else UNDEFINED
        elif root_null.required is False:
            required = False
            nullable = root_null.admits_null
            has_default_override = True
            default_value = obj.default if obj.has_default else None
        elif obj.has_default and not is_type_alias:
            required = False
            nullable = False
            has_default_override = True
            default_value = obj.default
        else:
            required = True
            nullable = None
            has_default_override = obj.has_default
            default_value = obj.default if obj.has_default else UNDEFINED
        name = self._apply_title_as_name(name, obj)
        if not reference:
            reference = self.model_resolver.add(path, name, loaded=True, class_name=True)
        self._record_schema_object(reference, obj)
        self._record_derived_from(reference, constraint_obj)
        assembled = self._assemble_field_constraints(
            constraint_obj, data_type, _ROOT_SITE, preserve=preserve_constraints, assembled=array_constraints
        )
        constraints = assembled.constraints
        fields = [
            self._build_field(
                "root",
                data_type,
                default=default_value,
                required=required,
                constraints=constraints,
                nullable=nullable,
                extras=self.get_field_extras(obj),
                use_annotated=effective_use_annotated,
                original_name=None,
                has_default=has_default_override,
            )
        ]
        self._mark_assembled_constraints(fields[0], assembled, constraint_obj, data_type)
        root_default = default_value if has_default_override else UNDEFINED
        if data_model_root_type is not None:
            JsonSchemaParser._register_root_model_as(
                self,
                selected_root_type,
                reference=reference,
                fields=fields,
                obj=obj,
                custom_base_class_name=class_key("predicted", name),
                default=root_default,
            )
            return self.data_type(reference=reference)

        self._register_root_model(
            reference=reference,
            fields=fields,
            obj=obj,
            custom_base_class_name=class_key("predicted", name),
            default=root_default,
        )
        return self.data_type(reference=reference)

    def _assemble_field_constraints(
        self,
        obj: JsonSchemaObject,
        data_type: DataType,
        site: _ConstraintSite,
        *,
        preserve: bool = False,
        assembled: Any = None,
    ) -> _AssembledConstraints:
        """Assemble the constraints a field of ``site`` writes from ``obj`` for its ``data_type``.

        The declared keywords the field takes, without those its type enforces at an object property, the hostname
        pattern, the property counts of a map, the item counts of an array without the lengths of a fixed tuple (a
        field whose type is no array, such as a string, takes none, and neither does one whose schema declares types
        that admit none, as a ``string`` typed ``datetime.date``); a heterogeneous type list takes none of the
        keywords and item counts, the property counts follow the map type.
        ``preserve`` reads the keywords without ``--field-constraints`` and ``assembled`` (the array builder's
        constraints of a preserved root) replaces the declared ones.
        """
        field_constraints = self.field_constraints
        skip = (
            self._should_skip_root_field_constraints_for_multiple_types(obj)
            and not (site.numbers_keep_bounds and self._has_shared_numeric_union_constraints(obj))
        ) or bool(self._get_array_union_non_array_types(obj))
        constraints: Any = None
        untaken: dict[str, Any] = {}
        type_list_unique = False
        built = not skip and bool(assembled)
        if not skip:
            if built:
                constraints = assembled
            elif self.is_constraints_field(obj, data_type) or (site.holds_schema and (field_constraints or preserve)):
                constraints, untaken = self._split_constraint_values(obj, data_type)
                for key in self._keywords_left_out(obj, data_type, site, reads_declared=field_constraints or preserve):
                    constraints.pop(key, None)
            if constraints is not None and field_constraints and obj.format == "hostname":
                constraints["pattern"] = self.data_type_manager.HOSTNAME_REGEX
        elif kept := _value_keywords.type_list_keywords(obj):
            constraints = kept
            type_list_unique = True
        if data_type in MAPPINGS and (
            property_counts := _value_keywords.get_property_count_constraints(type(self), obj)
        ):
            constraints = constraints or {}
            constraints.update(property_counts)
        keeps_lengths = (
            site.keeps_tuple_lengths and (field_constraints or preserve) and self._is_fixed_length_tuple(obj)
        )
        if not (built or keeps_lengths):
            if (
                not skip
                and (item_counts := self._get_array_items_constraints(obj))
                and _value_keywords.admits_array(obj)
                and _value_keywords.can_hold_array(data_type, models=site.counts_beside_models)
            ):
                constraints = constraints or {}
                constraints.update(item_counts)
            _value_keywords.suppress_array_length_constraints(self, constraints, obj)
        records = (field_constraints and not skip) if site.records_by_option else constraints is not None
        dropped_counts = _value_keywords.dropped_property_counts(self, obj, data_type)
        return _AssembledConstraints(
            {} if constraints is None and site.empty_record else constraints,
            {**(untaken if records else {}), **dropped_counts},
            dropped_counts,
            records,
            type_list_unique,
        )

    def _keywords_left_out(
        self, obj: JsonSchemaObject, data_type: DataType, site: _ConstraintSite, *, reads_declared: bool
    ) -> tuple[str, ...]:
        """Return the keywords a field of ``site`` leaves out of those it takes from ``obj`` itself.

        An object property leaves out the keywords its own type's class enforces (row
        ``legacy-consumed-at-object-property``). A root field that reads the keywords of an array without
        ``--field-constraints`` (``reads_declared``), as the array branch of a union beside ``maxLength``, leaves out
        the string lengths, which apply to strings only, as 0.88.0 did.
        """
        consumed = (
            property_consumed_keywords(self.data_type_manager.CONSTRAINED_TYPE_CONSUMED_KEYS, data_type.type)
            if site.consumed_at_property
            else ()
        )
        lengths = LENGTH_KEYWORD_ORDER if site.holds_schema and not reads_declared and obj.is_array else ()
        return (*consumed, *lengths)

    def _mark_assembled_constraints(
        self, field: DataModelFieldBase, assembled: _AssembledConstraints, obj: JsonSchemaObject, data_type: DataType
    ) -> None:
        """Record on ``field`` the keywords its type decides, those beside a union and the constraints it leaves out.

        Also whether its ``uniqueItems`` is the one of a type list (``with_type_list_unique_items``).
        """
        with_type_list_unique_items(field, declares=assembled.type_list_unique)
        if assembled.records:
            _value_keywords.mark_type_dependent_constraints(self, field, obj)
            if (siblings := _value_keywords.union_sibling_constraints(obj, field.constraints)) is not None:
                self._graph_builder.set_field_constraints(field, field.constraints, union_siblings=siblings)
        dropped_counts = assembled.dropped_counts
        _type_overrides.mark_untaken_constraints(
            self,
            field,
            assembled.untaken,
            lambda minimum: (
                self.data_type_manager.constraint_keywords(
                    data_type,
                    self.target_python_version.version_key,
                    number_values=True,
                    target_model_version=minimum,
                )
                - dropped_counts.keys()
            ),
        )

    def _should_skip_root_field_constraints_for_multiple_types(self, obj: JsonSchemaObject) -> bool:
        """Avoid applying type-specific Field constraints to heterogeneous root unions."""
        if not self.field_constraints or not isinstance(obj.type, list) or not obj.has_multiple_types:
            return False
        return len(non_null_types(obj.type, distinct=True)) > 1

    def _iter_non_object_union_types(
        self, name: str, obj: JsonSchemaObject, type_list: list[str], path: list[str], *, keep_constraints: bool = False
    ) -> Iterator[DataType]:
        """Retain scalar sibling constraints while preserving the existing object-first union order.

        Where they are not localized into the members, the members take them only with ``keep_constraints``, as
        ``get_data_type`` gives them to the other types of a type array.
        """
        localize_constraints = (
            self.field_constraints
            and self._output_model_context.supports_internal_annotated_constraints
            and (
                self._output_model_context.writes_constrained_types
                or self._output_model_context.supports_annotated_constraints
            )
            and _inherited_schemas.get_inherited_constraint_fields(self, obj)
        )
        for type_ in non_null_types(type_list):
            if type_ != "object":
                yield (
                    self._get_constrained_data_type(obj, type_, field_constraints=self.field_constraints)
                    if keep_constraints and not localize_constraints
                    else self._type_list_branch(name, obj, path, type_, localize=bool(localize_constraints))
                )

    def _parse_object_model(self, name: str, obj: JsonSchemaObject, path: list[str]) -> DataType:
        """Parse an object into its model; a type array with other types makes a root model of their union."""
        if self._joins_other_types(obj) and isinstance(obj.type, list):
            return self._parse_multiple_types_with_properties(name, obj, obj.type, path)
        return self.parse_object(name, obj, path)

    def _joins_other_types(self, obj: JsonSchemaObject) -> bool:
        """Return whether the model of an object joins the other types of its type array in a union.

        An object without properties, built as a model for its schema validators, joins them as an object map does,
        unless row ``legacy-validated-pattern-maps`` is kept.
        """
        return obj.has_multiple_types and (
            bool(obj.properties) or "legacy-validated-pattern-maps" not in self._legacy_object_maps
        )

    def _object_kind_object_model_applies(self, site: _Site) -> bool:
        return bool(site.obj.properties or self._should_parse_object_with_schema_validators(site.obj))

    def _object_kind_object_map_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return _has_map_key_keywords(site.obj)

    def _object_kind_object_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return site.obj.type == "object"

    def _build_object_kind_object(self, site: _Site) -> DataType:
        return self.parse_object(site.name, site.obj, site.path)

    def _object_kind_closed_property_names_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return site.obj.propertyNames is False

    _OBJECT_KIND_ROWS: ClassVar[tuple[Row[JsonSchemaParser, JsonSchemaObject, DataType], ...]] = (
        Row("object-model", _object_kind_object_model_applies, _parse_object_model_site),
        Row("object-map", _object_kind_object_map_applies, _parse_root_type_site),
        Row("object", _object_kind_object_applies, _build_object_kind_object),
        Row("closed-property-names", _object_kind_closed_property_names_applies, _parse_root_type_site),
    )
    """The rows of an object-kind schema, as a definition is parsed."""

    _OBJECT_KIND_TABLE: ClassVar[DispatchTable[JsonSchemaParser, JsonSchemaObject, DataType | None]] = DispatchTable(
        _OBJECT_KIND_ROWS, Default("not-object-kind", _parse_no_object_kind)
    )

    def _parse_object_kind_schema(self, name: str, obj: JsonSchemaObject, path: list[str]) -> DataType | None:
        """Parse an object-kind schema into its model as a definition is parsed; ``None`` for other schemas."""
        choice = self._OBJECT_KIND_TABLE.choose(self, SchemaSite(name, obj, path))
        return choice.build(self, choice.site)

    def _parse_multiple_types_with_properties(
        self,
        name: str,
        obj: JsonSchemaObject,
        type_list: list[str],
        path: list[str],
    ) -> DataType:
        """Parse a schema with multiple types including object with properties."""
        data_types: list[DataType] = []

        object_path = get_special_path("object", path)
        object_data_type = self.parse_object(name, obj, object_path)
        data_types.append(object_data_type)

        data_types.extend(self._iter_non_object_union_types(name, obj, type_list, path))

        null_facts = self._null_facts(obj, "multiple-types-root")
        reference = self.model_resolver.add(path, name, loaded=True, class_name=True)
        self._record_schema_object(reference, obj)

        constraints = _value_keywords.get_constraint_values(self, obj) if self.field_constraints else {}
        if self._should_skip_root_field_constraints_for_multiple_types(obj):
            constraints = {}
        self._register_root_model(
            reference=reference,
            fields=[
                self._build_field(
                    "root",
                    build_union(self.data_type, data_types),
                    default=obj.default,
                    required=bool(null_facts.required),
                    constraints=constraints,
                    nullable=null_facts.admits_null,
                    extras=self.get_field_extras(obj),
                    original_name=None,
                    has_default=obj.has_default,
                )
            ],
            obj=obj,
            custom_base_class_name=class_key("predicted", name),
            default=obj.default if obj.has_default else UNDEFINED,
        )
        return self.data_type(reference=reference)

    def _enum_positions_outside_const(self, obj: JsonSchemaObject) -> set[int]:
        """Return the positions of the enum values that the const beside them excludes, when it admits one of them.

        Draft-04 has no const keyword. A const equal to none of the values admits no value: the schema is read as
        written, and a warning says so once per schema location unless the intersection warning already reports it.
        Without --read-const-beside-enum a const beside an enum excludes no value.
        """
        if not self.read_const_beside_enum or "const" not in obj.extras or not self._features_at(obj).const_support:
            return set()
        const = obj.extras["const"]
        excluded = {position for position, value in enumerate(obj.enum) if not _json_literal_values_equal(const, value)}
        if len(excluded) < len(obj.enum):
            return excluded
        if _REPORTED_VALUE_CONFLICT_KEY in obj.__dict__:
            return set()
        location = named_location(obj.schema_location, "None")
        with first_report(self._unsatisfiable_const_locations, location) as first:
            if first:
                warn(
                    f"The schema at {location} admits no value: its const {stable_repr(const)} is none of its enum "
                    "values. It is generated as written.",
                    stacklevel=2,
                )
        return set()

    def _const_narrowed_enum(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        """Return a copy of an enum schema that keeps only the values its const admits, with their x-enum entries.

        The schema itself when the const excludes no value. Each entry stays with its value, matched as ``parse_enum``
        matches them.
        """
        if not (excluded := self._enum_positions_outside_const(obj)):
            return obj
        return _enum_without_positions(obj, excluded)

    def _note_const_narrowing(self, reference: Reference, obj: JsonSchemaObject) -> None:
        """Record the const that narrows the enum ``reference`` is built from, to type the values that name it.

        The enum class keeps every value as written. The values are narrowed to the enum value that the const equals,
        such as ``1`` for a const ``1.0``; one whose type would admit other values, such as a float or an object, leaves
        the values typed by the enum.
        """
        if not self._enum_positions_outside_const(obj) or not (exact := self._exact_const_enum_value(obj)):
            return
        const = exact[0]
        self._const_narrowed_references.append((
            reference,
            _ConstNarrowing(
                const=const,
                location=named_location(obj.schema_location, "None"),
                as_member=self._output_model_context.literal_holds_enum_members
                and self._enum_class_has_value_base(obj),
            ),
        ))

    def _reads_enum(self, obj: JsonSchemaObject) -> bool:
        """Return whether the run reads the enum of a schema: it has one and --ignore-enum-constraints is off."""
        return bool(obj.enum) and not self.ignore_enum_constraints

    def _reads_enum_class(self, obj: JsonSchemaObject) -> bool:
        """Return whether the run reads the enum of a schema as an enum, not as the const beside it."""
        return self._reads_enum(obj) and not self._reads_enum_as_const(obj)

    def _reads_enum_as_const(self, obj: JsonSchemaObject) -> bool:
        """Return whether --use-single-value-enum-as-const reads an enum that admits the const beside it as that const.

        Only with --read-const-beside-enum. The option marks each schema holding a const; without it, a document's own
        marker reads nothing. The enum is read as written beside ``nullable: true`` and a const other than null, as the
        option keeps such a one-value enum, since a const does not carry nullable, and when the const's type would admit
        other values (``_const_type_is_exact``).
        """
        return bool(
            self.read_const_beside_enum
            and self.use_single_value_enum_as_const
            and obj.read_as_const
            and "const" in obj.extras
            and (exact := self._exact_const_enum_value(obj))
            and resolve_null_facts(replace(_type_null_sources(obj), enum_null=exact[0] is None), "enum-as-const").rule
            != "nullable-enum-not-const"
        )

    def _enum_as_const_type(self, obj: JsonSchemaObject) -> DataType:
        """Return the type of an enum read as the const beside it (``_reads_enum_as_const``): that const's type."""
        return self._get_const_data_type(self._exact_const_enum_value(obj)[0], obj)

    def _exact_const_enum_value(self, obj: JsonSchemaObject) -> tuple[Any, ...]:
        """Return the value an enum's const narrows it to, as a one-tuple, or an empty one when it narrows to none.

        The const when its type admits that value only (``_const_type_is_exact``), else the enum value it equals that
        does, such as ``1`` for a const ``1.0``. A const that equals no enum value narrows
        nothing. The schema must hold a const.
        """
        const = obj.extras["const"]
        equal = intersect_enum_values(obj.enum, [const])
        return next(
            ((value,) for value in (const, *equal) if equal and _value_keywords.const_type_is_exact(self, value)), ()
        )

    def _definition_enum(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        """Return the enum schema a model built for the schema object itself holds.

        The enum as written, or under --use-single-value-enum-as-const only the values its const admits, so derived
        classes the const leaves unused are not written.
        """
        return self._const_narrowed_enum(obj) if self.use_single_value_enum_as_const else obj

    def _enum_value_type(self, obj: JsonSchemaObject, enum_type: DataType) -> DataType:
        """Return the type of a value whose enum a route wrote as the Literal ``enum_type``: the const's, if it narrows.

        A const whose type would admit other values, such as a float or an object, keeps the enum type.
        """
        if self._enum_positions_outside_const(obj) and (exact := self._exact_const_enum_value(obj)):
            return self._get_const_data_type(exact[0])
        return enum_type

    def parse_enum_as_literal(self, obj: JsonSchemaObject) -> DataType:
        """Parse enum values as a Literal type, of those that meet the constraints beside them (``_enum_literal``)."""
        return self._enum_literal(obj)

    def _literal_values_type(self, values: list[Any]) -> DataType:
        """Return the Literal type of enum values, joined with the types of values no Literal holds (``_widened``)."""
        literal_values, non_literal_values, value_null = _value_keywords.partition_enum_values(self, values)
        has_null = bool(resolve_null_facts(NullSources(enum_null=value_null), "enum-literal").admits_null)
        if not non_literal_values:
            return self.data_type(
                literals=literal_values,
                is_optional=has_null,
            )

        data_types: list[DataType] = []
        if literal_values:
            data_types.append(self.data_type(literals=literal_values))
        data_types.extend(self._get_data_type_from_json_value(i) for i in non_literal_values)
        if not data_types:  # pragma: no cover
            data_types.append(self.data_type_manager.get_data_type(Types.null))
        return self._widened(build_union(self.data_type, data_types, nullable=has_null), non_literal_values)

    def _get_enum_model_class(self, type_: Types | None, enum_values: list[Any]) -> tuple[type[Enum], Types | None]:
        """Return the enum model class and the schema type whose value type it also subclasses, else None.

        ``decide_enum_kind`` decides both. Up to 0.86.0 the second value was the schema type for every class that is
        not specialized, whether or not the class subclasses it; callers still check ``use_subclass_enum`` for parser
        subclasses that return that.
        """
        kind = decide_enum_kind(
            "schema",
            value_type=SUBCLASS_BASE_CLASSES.get(type_) if type_ else None,
            values=enum_values,
            use_specialized_enum=self.use_specialized_enum,
            use_subclass_enum=self.use_subclass_enum,
            has_strenum=python_target_meets("python.strenum", self.target_python_version.version_key),
            decodes_mixed_values=self._output_model_context.decodes_mixed_enum_values,
            keep_member_values=self.config.keep_enum_member_values,
        )
        return _enum_class(kind), type_ if kind.subclasses_value_type else None

    def _get_enum_type(self, obj: JsonSchemaObject) -> Types | None:
        """Return the type an enum class of the schema may specialize or subclass."""
        return self._get_type_with_mappings(obj.type, obj.format) if isinstance(obj.type, str) else None

    def _enum_class_has_value_base(self, obj: JsonSchemaObject) -> bool:
        """Return whether the enum class of the schema subclasses a value type, whose members cannot be None.

        Such a class needs one ``type`` other than ``string``, which rejects null unless ``nullable`` allows it, so the
        enum drops the null it lists and is not nullable, except that an enum of only null stays that None. The class
        is the one of the members other than null, which a value base must hold. The ``use_subclass_enum`` check is a
        no-op for the built-in ``_get_enum_model_class``; it stays for parser subclasses that return its old second
        value.
        """
        enum_cls, type_ = self._get_enum_model_class(self._get_enum_type(obj), obj.enum)
        return enum_cls is not Enum or (self.use_subclass_enum and type_ in SUBCLASS_BASE_CLASSES)

    def _extra_template_data_for_reference(self, reference: Reference) -> defaultdict[str, dict[str, Any]] | None:
        """Return shared template data only when the enum reference has relevant entries."""
        if not (extra_template_data := self.extra_template_data):
            return None
        if any(extra_template_data.get(key) for key in template_data_keys(reference)):
            return extra_template_data
        return None

    @classmethod
    def _get_field_name_from_dict_enum(cls, enum_part: dict[str, Any], index: int) -> str:
        """Extract field name from dict enum value using title, name, or const keys."""
        if enum_part.get("title"):
            return _semantic_value_text(enum_part["title"])
        if enum_part.get("name"):
            return _semantic_value_text(enum_part["name"])
        if "const" in enum_part:
            return _semantic_value_text(enum_part["const"])
        return f"value_{index}"

    def parse_enum(  # noqa: PLR0912, PLR0914, PLR0915
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        unique: bool = True,  # noqa: FBT001, FBT002
    ) -> DataType:
        """Parse enum schema into an Enum class."""
        with self._model_frame() as frame:
            if not unique:  # pragma: no cover
                warn(
                    f"{self.__class__.__name__}.parse_enum() ignore `unique` argument."
                    f"An object name must be unique."
                    f"This argument will be removed in a future version",
                    stacklevel=2,
                )
            enum_fields: list[DataModelFieldBase] = []

            written_obj, obj = obj, self._literal_view(obj)
            named_values = obj.enum
            msgspec_null_alias = (
                self.use_msgspec_nullable_enum_alias and not self.data_model_type.SUPPORTS_NULL_ENUM_MEMBER
            )
            if (enum_null := self._null_facts(obj, "enum")).rule == "enum-null-as-nullable":
                nullable: bool = True
                enum_times = named_values = [e for e in obj.enum if e is not None]
            elif enum_null.rule == "enum-null-member" and (msgspec_null_alias or self._enum_class_has_value_base(obj)):
                enum_times = [e for e in obj.enum if e is not None]
                nullable = msgspec_null_alias or not enum_times
            else:
                enum_times = obj.enum
                nullable = False

            exclude_field_names: set[str] = set()

            enum_names = obj.x_enum_varnames or obj.x_enum_names
            enum_descriptions = obj.x_enum_descriptions

            for i, enum_part in enumerate(named_values):
                match enum_part:
                    case str():
                        default = EnumMemberValue(enum_part)
                    case None:
                        default = NULL_ENUM_MEMBER_VALUE
                    case _:
                        default = _safe_non_finite_value(enum_part)
                if obj.type == "string" or isinstance(enum_part, str):
                    field_name = (
                        enum_names[i]
                        if enum_names and i < len(enum_names) and enum_names[i]
                        else _semantic_value_text(enum_part)
                    )
                elif enum_names and i < len(enum_names) and enum_names[i]:
                    field_name = enum_names[i]
                elif isinstance(enum_part, dict):
                    field_name = self._get_field_name_from_dict_enum(enum_part, i)
                else:
                    prefix = obj.type if isinstance(obj.type, str) else type(enum_part).__name__
                    field_name = f"{prefix}_{_semantic_value_text(enum_part)}"
                field_name = self.model_resolver.get_valid_field_name(
                    cast("str", field_name), excludes=exclude_field_names, model_type=ModelType.ENUM
                )
                exclude_field_names.add(field_name)
                if enum_part is None and None not in enum_times:
                    continue
                field_extras: dict[str, Any] = {}
                if enum_descriptions and i < len(enum_descriptions) and enum_descriptions[i]:
                    field_extras["description"] = enum_descriptions[i]
                enum_fields.append(
                    self._build_field(
                        "enum-member",
                        self.data_type_manager.get_data_type(
                            Types.any,
                        ),
                        name=field_name,
                        default=default,
                        required=True,
                        has_default=obj.has_default,
                        extras=field_extras,
                        original_name=None,
                    )
                )

            if not enum_fields and not nullable:
                return frame.built(self.data_type_manager.get_data_type(Types.null))

            def create_enum(reference_: Reference) -> DataType:
                """Build the enum model; the ``use_subclass_enum`` check is kept for parser subclasses (see above)."""
                enum_cls, type_ = self._get_enum_model_class(self._get_enum_type(obj), enum_times)
                self._record_schema_object(reference_, obj)

                enum = self._new_model(
                    enum_cls,
                    reference=reference_,
                    fields=enum_fields,
                    path=self.current_source_path,
                    description=obj.description if self.use_schema_description else None,
                    custom_template_dir=self.custom_template_dir,
                    extra_template_data=self._extra_template_data_for_reference(reference_),
                    type_=type_ if self.use_subclass_enum else None,
                    default=obj.default if obj.has_default else UNDEFINED,
                    treat_dot_as_module=self.treat_dot_as_module,
                )
                if (values_as_written := obj.__dict__.get(VALUES_AS_WRITTEN_KEY)) is not None:
                    self._graph_builder.record_values_as_written(enum, values_as_written)
                self._graph_builder.register_model(enum)
                return self.data_type(reference=reference_)

            name = self._apply_title_as_name(name, obj)
            reference = self.model_resolver.add(
                path,
                name,
                class_name=True,
                singular_name=singular_name,
                singular_name_suffix="Enum",
                loaded=True,
                model_type="enum",
            )
            if enum_fields:
                self._note_const_narrowing(reference, written_obj)
                if not nullable:
                    return frame.built(create_enum(reference))

            self._record_schema_object(reference, obj)
            if not enum_fields:
                data_type = self.data_type_manager.get_data_type(Types.null)
                custom_base_class_name = class_key("predicted", name)
            else:
                enum_reference = self.model_resolver.add(
                    [*path, "Enum"],
                    composed_name(reference.name, "enum"),
                    class_name=True,
                    singular_name=singular_name,
                    singular_name_suffix="Enum",
                    loaded=True,
                    model_type="enum",
                )
                if message := _shifted_enum_entries_message(enum_reference.name, obj, named_values):
                    warn(message, stacklevel=2)
                with self._claiming(Composed(reference, "enum")) as claimed:
                    data_type = claimed.built(create_enum(enum_reference))
                custom_base_class_name = class_key("reference-name", reference)

            self._register_root_model(
                reference=reference,
                fields=[
                    self._build_field(
                        "root",
                        data_type,
                        default=obj.default,
                        required=False,
                        nullable=True,
                        extras=self.get_field_extras(obj),
                        original_name=None,
                        has_default=obj.has_default,
                    )
                ],
                obj=obj,
                custom_base_class_name=custom_base_class_name,
                default=obj.default if obj.has_default else UNDEFINED,
            )
            return frame.built(self.data_type(reference=reference))

    @cached_property
    def _input_files(self) -> frozenset[Path]:
        """Return the resolved paths of the files the input names: a file, the input files of a directory, or a list."""
        match self.source:
            case Path() as path if path.is_dir():
                files: Iterable[Path] = (file for file, _ in self._directory_input_files(path))
            case Path() as path:
                files = (path,)
            case list() as paths:
                files = paths
            case _:
                files = ()
        return frozenset(file.resolve() for file in files)

    def _get_ref_body(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Get the body of a reference from URL or remote file.

        Under --untrusted-input only a document of the input itself is read, a file the input names.
        """
        if self.untrusted_input and (
            is_url(resolved_ref) or (self.base_path / resolved_ref).resolve() not in self._input_files
        ):
            msg = (
                f"Reading the $ref document {redact_reference(resolved_ref)} is refused under --untrusted-input.\n"
                "Reason: --untrusted-input reads only the input, so a $ref must name a location in the input, not "
                "another file or a URL."
            )
            raise Error(msg)
        if is_url(resolved_ref):
            url_scheme = urlparse(resolved_ref).scheme
            uses_local_http_path = url_scheme in {"http", "https"} and self.http_local_ref_path is not None
            if not uses_local_http_path:
                if self.allow_remote_refs is False:
                    msg = (
                        f"Fetching remote $ref is disabled: {redact_reference(resolved_ref)}\n"
                        "Reason: --no-allow-remote-refs was set, so external $ref targets are not fetched.\n"
                        "If this schema and all of its remote references are trusted, pass --allow-remote-refs. "
                        "If a trusted remote reference points to an internal schema registry, also pass "
                        "--allow-private-network."
                    )
                    raise Error(msg)
                if self.allow_remote_refs is None and url_scheme in {"http", "https"}:
                    warn_deprecated(
                        "behavior.remote-ref-default",
                        details=(
                            f"Reference: {redact_reference(resolved_ref)}. Pass --allow-remote-refs for trusted remote "
                            "schemas, or --no-allow-remote-refs to block HTTP(S) $ref fetching. Internal network "
                            "targets also require --allow-private-network."
                        ),
                        stacklevel=2,
                    )
            return _schema_documents.prepare_schema_resources(
                self, self._get_ref_body_from_url(resolved_ref), [resolved_ref]
            )
        return _schema_documents.prepare_schema_resources(
            self, self._get_ref_body_from_remote(resolved_ref), [resolved_ref]
        )

    def _normalize_external_ref(self, ref: str) -> str:
        """Resolve an external anchor before falling back to legacy shorthand pointers."""
        return _schema_documents.normalize_external_ref(self, ref)

    def _resolve_local_ref_path(self, path: Path, ref: str) -> Path:
        if cached_path := self._local_ref_path_cache.get(path):
            return cached_path

        base_path = self.base_path.resolve()
        resolved_path = path.resolve()
        if resolved_path.is_relative_to(base_path) or self.allow_remote_refs is True:
            self._local_ref_path_cache[path] = resolved_path
            return resolved_path

        details = (
            f"Reference: {ref}. Reason: the resolved file is outside the input base path. "
            f"Base path: {base_path}. Resolved path: {resolved_path}. "
            "Move trusted referenced schemas under the input directory, pass --allow-remote-refs to allow this "
            "external local file reference without a warning, or pass --no-allow-remote-refs to block it."
        )
        if self.allow_remote_refs is None:
            warn_deprecated("behavior.remote-ref-default", details=details, stacklevel=3)
            return resolved_path

        msg = (
            f"Blocked unsafe local $ref: {ref}\n"
            "Reason: --no-allow-remote-refs was set and the resolved file is outside the input base path.\n"
            f"Base path: {base_path}\n"
            f"Resolved path: {resolved_path}\n"
            "Move trusted referenced schemas under the input directory, or pass --allow-remote-refs only when the "
            "schema and referenced files are trusted."
        )
        raise Error(msg)

    def _document_as_written(self, document: dict[str, YamlValue]) -> dict[str, YamlValue]:
        """Return a document as written, before --use-single-value-enum-as-const read its single-value enums."""
        return _schema_documents.document_as_written(self, document)

    def _load_ref_data_from_path(self, path: Path) -> dict[str, YamlValue]:
        """Load one referenced path and contextualize only its decode failures."""
        try:
            return _schema_documents.read_document(self, load_data_from_path(path, self.encoding), self.raw_obj)
        except (json.JSONDecodeError, TypeError, *get_yaml_parse_errors()) as exc:
            raise InvalidFileFormatError(exc, self._input_file_type, source=path) from exc

    def _get_ref_body_from_url(self, ref: str) -> dict[str, YamlValue]:
        """Get reference body from a URL (HTTP, HTTPS, or file scheme)."""
        if ref.startswith("file://"):
            return self._load_local_ref_document(self._resolve_local_ref_path(file_uri_path(ref), ref), ref)
        if self.http_local_ref_path is not None and urlparse(ref).scheme in {"http", "https"}:
            return _schema_documents.get_ref_body_from_local_http_path(self, ref)
        return self.remote_object_cache.get_or_put(
            ref,
            default_factory=lambda key: _schema_documents.load_ref_data_from_text(
                self, self._get_text_from_url(key), key
            ),
        )

    def _get_ref_body_from_remote(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Get reference body from a remote file path."""
        full_path = self._resolve_local_ref_path(self.base_path / resolved_ref, resolved_ref)
        return self._load_local_ref_document(full_path, resolved_ref)

    def _load_local_ref_document(self, path: Path, ref: str) -> dict[str, YamlValue]:
        """Return the document at the local path a reference ``ref`` names, a file URI or a path alike.

        A missing file and a directory are errors that name the path and the reference.
        """
        try:
            return self.remote_object_cache.get_or_put(
                str(path), default_factory=lambda _: self._load_ref_data_from_path(path)
            )
        except FileNotFoundError:
            raise _schema_refs.RefDocumentNotFoundError(path, _external_ref_mapping_key(ref, self.base_path)) from None
        except (IsADirectoryError, PermissionError) as exc:
            if not _is_directory_read_error(exc, path):
                raise
            msg = f"$ref path is a directory: {ref}"
            raise Error(msg) from None

    def resolve_ref(self, object_ref: str) -> Reference:
        """Resolve a reference by loading and parsing the referenced schema."""
        _validate_external_ref(object_ref)

        key = _schema_refs.ref_key(self, object_ref)
        # If the ref is mapped to an external package, mark as loaded and skip parsing
        if key.mapping is not None:
            reference = self._add_ref(object_ref)
            reference.loaded = True
            return reference

        ref = key.key
        if get_ref_type(ref) == JSONReference.LOCAL or (
            get_ref_type(key.spelling) == JSONReference.LOCAL and not _schema_refs.outside_home(self, object_ref)
        ):
            reference = self.model_resolver.add_ref(ref, resolved=True)
            self.reserved_refs[tuple(self.model_resolver.current_root)].add(ref)
            return reference

        if is_url(ref):
            relative_path, object_path = ref.split("#", 1)
            relative_paths = [relative_path]
            base_path = None
        else:
            if self.model_resolver.is_external_root_ref(ref):
                relative_path, object_path = ref[:-1], ""
            else:
                relative_path, object_path = ref.split("#", 1)
            relative_paths = relative_path.split("/")
            base_path = Path(*relative_paths).parent
        reference = self.model_resolver.add_ref(ref, resolved=True)
        if reference.loaded:
            return reference
        if self.model_resolver.is_after_load(ref):
            self.reserved_refs[tuple(ref.split("#")[0].split("/"))].add(ref)
            return reference

        with (
            self.model_resolver.current_base_path_context(base_path),
            self.model_resolver.base_url_context(relative_path),
        ):
            ref_body = self._get_ref_body(relative_path)
            object_paths: list[str] | None = None
            reference_paths: list[str] | None = None
            if object_path:
                object_paths, reference_paths = _split_json_pointer(
                    ref_body, object_path, names=self._pointer_slash_names
                )
            self._parse_file(
                ref_body,
                self.model_resolver.add_ref(ref, resolved=True).name,
                relative_paths,
                object_paths,
                reference_paths=reference_paths,
                ref=ref,
            )
        reference.loaded = True
        return reference

    def _traverse_schema_objects(
        self,
        obj: JsonSchemaObject,
        path: list[str],
        callback: Callable[[JsonSchemaObject, list[str]], None],
    ) -> None:
        """Traverse schema objects recursively and apply callback, within the schema resources they declare."""
        return _schema_refs.traverse_schema_objects(self, obj, path, callback)

    def _walk_schema_objects(
        self,
        obj: JsonSchemaObject,
        path: list[str],
        callback: Callable[[JsonSchemaObject, list[str]], None],
    ) -> None:
        """Apply callback to a schema object and traverse its child schemas at ``REF_PRE_PASS_POSITIONS``."""
        callback(obj, path)
        if (
            type(self) is JsonSchemaParser
            and type(obj) is JsonSchemaObject
            and obj.model_fields_set.isdisjoint(REF_PRE_PASS_FIELDS)
            and (not self.generate_schema_validators or obj.extras.keys().isdisjoint(_CONDITIONAL_SCHEMA_KEYWORDS))
        ):
            return
        for keyword in REF_PRE_PASS_POSITIONS:
            if keyword in _CONDITIONAL_SCHEMA_KEYWORDS:
                if not self.generate_schema_validators:
                    continue
                value = _schema_validators.get_conditional_schema(self, obj, keyword)
            else:
                value = getattr(obj, keyword)
            match value:
                case JsonSchemaObject():
                    self._traverse_schema_objects(value, [*path, keyword], callback)
                case list():
                    for index, item in enumerate(value):
                        if isinstance(item, JsonSchemaObject):
                            self._traverse_schema_objects(item, [*path, keyword, str(index)], callback)
                case dict():
                    for key, item in value.items():
                        if isinstance(item, JsonSchemaObject):
                            self._traverse_schema_objects(item, [*path, keyword, key], callback)

    def _resolve_ref_callback(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Resolve $ref in schema object, and the targets of $dynamicRef and $recursiveRef as their data types do."""
        return _schema_refs.resolve_ref_callback(self, obj, path)

    def _init_schema_resources(self) -> None:
        """Initialize the schema graph, the document-local resource indexes and the normalization cache.

        The resource indexes are the registry dicts of the schema graph, which fills them as documents load.
        Schema resource root pointers are indexed by document. $dynamicAnchor locations and the embedded
        resources that $id declares are indexed by document and enclosing resource root pointer, so
        documents that share an $id keep their own anchors. The dynamic scope state tracks the model
        root being parsed, the $dynamicAnchor bindings imposed by the schemas that referenced it, and
        the models specialized for those bindings.
        """
        self._schema_graph = graph = SchemaGraphBuilder()
        self._schema_resource_registry = graph.registry
        self._schema_resource_document_aliases: dict[str, str] = {}
        self._schema_resource_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self._document_dialects: dict[str, DocumentDialect] = {}
        self._referring_dialect: DocumentDialect | None = None
        self._has_embedded_schema_resources = False
        self._lenient_documents: dict[str, dict[str, Any]] = {}
        self._implicit_documents: set[str] = set()
        self._document_roles: dict[str, dict[str, None]] = {}
        self._roles_recorded_after_preparation = False
        self._resource_ref_warnings: set[str] = set()
        self._unread_mapped_documents: set[str] = set()
        """The documents --external-ref-mapping maps that do not exist, read as classes without known properties."""
        self._dynamic_scope_pointer = ""
        self._schema_resource_path_parts: dict[str, list[str]] = {}
        self._dynamic_document: str | None = None
        self._dynamic_scope_path: tuple[str, ...] = ()
        self._dynamic_bindings: dict[str, str] = {}
        self._dynamic_specializations: dict[tuple[_TargetNode, frozenset[tuple[str, str]]], Reference] = {}
        self._dynamic_specialization_paths: set[tuple[str, ...]] = set()
        self._dynamic_specialization_worklist = _Worklist()
        self._ref_site_models: dict[
            tuple[SchemaLocation, frozenset[tuple[str, str]]], tuple[Reference, dict[str, Any]]
        ] = {}
        self._ref_site_worklist = _Worklist()
        self._parses_unowned_fields = False
        self._dynamic_anchor_usage: dict[_TargetNode, frozenset[str]] = {}
        self._dynamic_ref_graph: dict[_TargetNode, _DynamicEdges] = {}
        self._dynamic_ref_nodes: dict[str, _TargetNode] = {}

    @property
    @override
    def document_roles_to_seed(self) -> DocumentRoles:
        """Return every role references recorded for each document, once one was recorded after its preparation.

        A run that fails then parses again with these roles from the start (``hold_document_root``); otherwise none.
        """
        if not self._roles_recorded_after_preparation:
            return ()
        return tuple((document, tuple(roles)) for document, roles in self._document_roles.items())

    @override
    def _seed_document_roles(self, document_roles: DocumentRoles) -> None:
        """Start the roles of each document from those an earlier attempt of the run recorded."""
        self._document_roles = {document: dict.fromkeys(roles) for document, roles in document_roles}

    def _index_document(self, raw: dict[str, Any], path_parts: list[str]) -> dict[str, Any]:
        """Return a loaded document as a tree whose JSON objects the schema graph locates (L0).

        The document is named as reference paths name it. A copy of the root keeps the document as written that
        ``_document_as_written`` returns for the loaded one. A mounted converted schema keeps its locations.
        """
        return _schema_documents.index_document(self, raw, path_parts)

    def parse_ref(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Recursively parse all $ref references in a schema object, unless only the used ones are loaded."""
        return _schema_documents.parse_ref(self, obj, path)

    def parse_id(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Recursively parse all $id fields in a schema object."""
        return _schema_documents.parse_id(self, obj, path)

    @contextmanager
    def root_id_context(self, root_raw: dict[str, Any]) -> Generator[None, None, None]:
        """Context manager to temporarily set the root $id during parsing.

        Uses the id_field of the schema features to support both "id" (Draft 4) and "$id" (Draft 6+).
        Falls back to checking both fields for lenient compatibility.
        """
        previous_root_id = self.root_id
        # Try version-specific field first, then fallback to alternative for compatibility
        id_field = self._features_at(None).id_field
        self.root_id = root_raw.get(id_field) or root_raw.get("$id") or root_raw.get("id") or None
        yield
        self.root_id = previous_root_id

    @cached_property
    def _schema_validation_context(self) -> dict[str, object]:
        """Return the validation context of the schema objects.

        It collects the schema objects whose ``enum`` lists no value (``_report_parse_diagnostics``), passes the regex
        engines of the output's models, which read each ``pattern`` and collect those no engine compiles, says whether
        they read ``unevaluatedProperties``, and under --ignore-ref-siblings-by-schema-version whether the version of a
        location ignores the keywords beside a ``$ref`` (``_ref_siblings_apply_to``).
        """
        context: dict[str, object] = {EMPTY_ENUM_CONTEXT: self._empty_enum_locations}
        if engines := self.data_type_manager.REGEX_ENGINES:
            context[PATTERN_READING_CONTEXT] = (engines, self._unread_patterns)
        if self.read_unevaluated_properties:
            context[READ_UNEVALUATED_PROPERTIES_CONTEXT] = True
        if self.config.ignore_ref_siblings_by_schema_version:
            context[IGNORED_REF_SIBLINGS_CONTEXT] = self._ignores_ref_siblings_at
        return context

    def _ignores_ref_siblings_at(self, location: SchemaLocation | None) -> bool:
        """Return whether a ``$ref`` at a location is read alone, without the keywords beside it.

        Only under --ignore-ref-siblings-by-schema-version (``reads_ref_alone``), where the schema version of the
        location ignores them: the version of its document under --read-schema-version-per-document, else that of the
        input document being parsed (row ``legacy-first-read-dialect``). A schema converted from another format, such
        as Avro, XML Schema or Protocol Buffers, keeps them in every version: its converter writes a field's default
        and annotations beside the ``$ref`` to the field's named type.
        """
        return self.config.ignore_ref_siblings_by_schema_version and not (
            self._ref_siblings_apply_to(location, legacy="parsed-document")
            or (location is not None and (graph := self._schema_graph) is not None and graph.is_converted(location))
        )

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw or dumped schema as ``SCHEMA_OBJECT_TYPE`` in the parser's validation context."""
        return self.SCHEMA_OBJECT_TYPE.model_validate(raw, context=self._schema_validation_context)

    def _validate_schema_object(
        self,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
    ) -> JsonSchemaObject:
        """Validate raw data as JsonSchemaObject with path context in errors; a too deep nesting stops the parse."""
        if raw is True:
            return self.SCHEMA_OBJECT_TYPE()
        if raw is False:
            return self.SCHEMA_OBJECT_TYPE(is_boolean_schema_false=True)
        try:
            return self._schema_object(raw)
        except (SchemaParseError, RecursionError):
            raise
        except Exception as e:
            raise SchemaParseError(
                message=f"{type(e).__name__}: {e}",
                path=_named_path(self._schema_value_location(raw), path),
                original_error=e,
            ) from e

    def _schema_value_location(self, raw: object) -> SchemaLocation | None:
        """Return the location of a raw schema value that a message names.

        A JSON object has its own; any other value, such as a boolean schema, has the one its holder gives while the
        code that read it from the holder handles it.
        """
        if isinstance(raw, dict):
            return _source_location(raw)
        return held[1] if (held := self._held_value) is not None and held[0] is raw else None

    @contextmanager
    def _value_held_at(self, raw: YamlValue, location: SchemaLocation | None) -> Generator[None, None, None]:
        """Give a raw schema value that is not a JSON object the location of its holder while it is handled."""
        if isinstance(raw, dict) or location is None:
            yield
            return
        previous, self._held_value = self._held_value, (raw, location)
        try:
            yield
        finally:
            self._held_value = previous

    def _validate_member(self, raw: YamlValue, path: list[str], location: SchemaLocation | None) -> JsonSchemaObject:
        """Validate a raw schema read from its holder at ``location``; a boolean schema is located there."""
        with self._value_held_at(raw, location):
            obj = self._validate_schema_object(raw, path)
        return _locate(obj, location) if isinstance(raw, bool) else obj

    def parse_raw_obj(
        self,
        name: str,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
    ) -> None:
        """Parse a raw dictionary into a JsonSchemaObject and process it."""
        self._parse_raw_or_validated_obj(name, raw, path)

    def _raw_location(self, raw: object) -> SchemaLocation | None:
        """Return the location of a JSON container of a loaded document."""
        return None if (graph := self._schema_graph) is None else graph.locations.get(id(raw))

    def _member_location(self, holder: object, *tokens: str) -> SchemaLocation | None:
        """Return the location of a value below a JSON container, such as a boolean schema the index cannot key."""
        return None if (location := self._raw_location(holder)) is None else location.child(*tokens)

    def _parse_referenced_root(self, name: str, raw: dict[str, YamlValue], ref: str) -> None:
        """Parse the root of a document that ``ref`` names as a whole into the model ``name`` of that reference."""
        self._parse_document_root(name, self._validate_schema_object(raw, [ref]), raw, [ref], referenced=True)

    def _parse_pointer_definition(
        self, raw: dict[str, YamlValue], tokens: Sequence[str], target: YamlValue, name: str, path: list[str]
    ) -> None:
        """Parse the value a JSON pointer names in a document into the model ``name`` at ``path``.

        Its ``x-python-import`` is noted for the reference too (``_note_python_import_target``).
        """
        self.parse_obj(name, self._validate_pointer_target(raw, tokens, target, path), path)
        self._note_python_import_target(target, path)

    def _validate_pointer_target(
        self, document: object, tokens: Sequence[str], target: YamlValue, path: list[str]
    ) -> JsonSchemaObject:
        """Validate the value a JSON pointer names in a document; a boolean schema is located by the pointer."""
        return self._validate_member(
            _schema_refs.read_as_schema(self, target), path, self._member_location(document, *map(str, tokens))
        )

    def _record_boolean_schema_location(self, raw: YamlValue, path: list[str], location: SchemaLocation | None) -> None:
        """Record ``location`` for the model parsed at ``path`` from a boolean schema, whose value cannot carry it."""
        if (
            isinstance(raw, bool)
            and location is not None
            and (reference := self.model_resolver.references.get(self.model_resolver.join_path(tuple(path))))
        ):
            _set_schema_location(reference, location)

    def _parse_raw_at(self, name: str, raw: YamlValue, path: list[str], location: SchemaLocation | None) -> None:
        """Parse a raw schema; a boolean schema gets ``location``."""
        with self._value_held_at(raw, location):
            self.parse_raw_obj(name, raw, path)
        self._record_boolean_schema_location(raw, path, location)

    def _parse_raw_member(self, name: str, holder: Mapping[str, YamlValue], key: str, path: list[str]) -> None:
        """Parse ``holder[key]``; a boolean schema takes its location from its holder."""
        self._parse_raw_at(name, holder[key], path, self._member_location(holder, key))

    def _parse_raw_or_validated_obj(
        self,
        name: str,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
        validated_obj: JsonSchemaObject | None = None,
    ) -> None:
        """Parse a raw schema, reusing a validated object when available.

        A schema with ``x-python-import`` is the class it imports, unless --ignore-ref-siblings-by-schema-version reads
        it as its ``$ref`` alone (``reads_ref_alone``).
        """
        if self._reads_as_python_import(raw, path):
            self.model_resolver.add(path, name, class_name=True, loaded=True)
            self._python_import_values[self.model_resolver.join_path(tuple(path))] = raw["x-python-import"]
            return

        # Strict mode: check for version-specific features before validation
        self._check_version_specific_features(raw, path)

        obj = validated_obj if validated_obj is not None else self._validate_schema_object(raw, path)
        self._cache_ref_data_type_facts(self.model_resolver.join_path(tuple(path)), obj)
        # Build $recursiveAnchor / $dynamicAnchor indexes for this schema
        _schema_refs.build_anchor_indexes(self, obj, path)
        self.parse_obj(name, obj, path)

    def _reads_as_python_import(
        self, raw: dict[str, YamlValue] | YamlValue, path: list[str]
    ) -> TypeIs[dict[str, YamlValue]]:
        """Return whether the schema at ``path`` is the class its ``x-python-import`` imports.

        A stand-in is parsed into its model, and --ignore-ref-siblings-by-schema-version may read the schema as its
        ``$ref`` alone (``reads_ref_alone``).
        """
        return (
            isinstance(raw, dict)
            and "x-python-import" in raw
            and not self._is_base_stand_in_path(path)
            and not reads_ref_alone(raw, self._schema_validation_context)
        )

    def _note_python_import_target(self, target: YamlValue, path: list[str]) -> None:
        """Note the empty model a ``$ref`` target parsed into at ``path`` when its ``x-python-import`` types its uses.

        The target is that of a JSON pointer or the root of a document a ``$ref`` names (``_parse_document_root``). A
        model of no field and no generated base is one nobody can use: only a base that names the schema needs it, so
        unnamed, it is left out with the unused models (#5533). A model with fields or a root type stays.
        """
        if (
            self._reads_as_python_import(target, path)
            and (reference := self.model_resolver.references.get(key := self.model_resolver.join_path(tuple(path))))
            and isinstance(model := reference.source, DataModel)
            and not model.fields
            and not any(base.reference for base in model.base_classes)
        ):
            self._python_import_target_paths.add(key)

    def _parse_document_root(
        self, name: str, obj: JsonSchemaObject, raw: dict[str, Any], path: list[str], *, referenced: bool
    ) -> None:
        """Parse the root of a document into its model, noted as a target where a ``$ref`` names the document.

        The root of an input document is no target: it keeps its model (``_note_python_import_target``).
        """
        self.parse_obj(name, obj, path)
        if referenced:
            self._note_python_import_target(raw, path)

    def _check_version_specific_features(
        self,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
    ) -> None:
        """Check for version-specific features and warn in Strict mode.

        This method checks the raw schema data before Pydantic validation
        to detect features that may not be valid for the declared version.
        """
        if self.config.schema_version_mode != VersionMode.Strict:
            return
        location = self._schema_value_location(raw)
        schema_path = redact_path(_named_path(location, path))

        # Check boolean schemas (Draft 6+)
        if isinstance(raw, bool):
            if not self._features_at(location).boolean_schemas:
                version_name = "Draft 4" if self._features_at(location).id_field == "id" else "this version"
                warn(
                    f"Boolean schemas are not supported in {version_name}. Schema path: {schema_path}",
                    stacklevel=3,
                )
            return

        raw = cast("dict[str, YamlValue]", raw)
        # Check null in type array (JSON Schema / OpenAPI 3.1+)
        type_value = raw.get("type")
        if isinstance(type_value, list) and "null" in type_value and not self._features_at(location).null_in_type_array:
            warn(
                'null in type array (e.g., type: ["string", "null"]) is not supported '
                f"in this schema version. Use nullable: true instead. Schema path: {schema_path}",
                stacklevel=3,
            )

        # Check exclusive min/max format (Draft 4 uses boolean, Draft 6+ uses number)
        as_number = self._features_at(location).exclusive_as_number
        for keyword in ("exclusiveMinimum", "exclusiveMaximum"):
            if (value := raw.get(keyword)) is None or isinstance(value, bool) != as_number:
                continue
            style = (
                "as boolean is Draft 4 style, but schema version uses numeric style"
                if as_number
                else "as number is Draft 6+ style, but schema version is Draft 4"
            )
            warn(f"{keyword} {style}. Schema path: {schema_path}", stacklevel=3)

        for keyword in ("readOnly", "writeOnly"):
            if not self._features_at(location).read_only_write_only and raw.get(keyword) is True:
                warn(
                    f"{keyword} is not supported in this schema version (Draft 7+ only). Schema path: {schema_path}",
                    stacklevel=3,
                )

    def _check_array_version_features(
        self,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> None:
        """Check for version-specific array features and warn in Strict mode.

        Warns when prefixItems is used in versions that don't support it,
        or when items as array (tuple style) is used in Draft 2020-12+.
        """
        if self.config.schema_version_mode != VersionMode.Strict:
            return
        schema_path = redact_path(_named_path(obj.schema_location, path))

        # Check prefixItems usage (Draft 2020-12+ only)
        if obj.prefixItems is not None and not self._features_at(obj).prefix_items:
            warn(
                f"prefixItems is not supported in this schema version. "
                f"Use items as array for tuple validation. Schema path: {schema_path}",
                stacklevel=4,
            )

        # Check items as array usage (deprecated in Draft 2020-12)
        if isinstance(obj.items, list) and self._features_at(obj).prefix_items:
            warn_deprecated(
                "schema.jsonschema-items-array",
                details=f"Schema path: {schema_path}",
                stacklevel=4,
            )

    def _definition_entry_prefixes(self) -> tuple[tuple[str, ...], ...]:
        """Return the path segments after a document root that name a definitions container (``#/$defs``)."""
        return tuple((schema_path,) for schema_path, _ in self._schema_paths_at(None))

    def _is_named_schema_definition_path(self, path: list[str]) -> bool:
        """Check if path points to a named schema entry under a definitions container."""
        current_root = list(self.model_resolver.current_root)
        if path[: len(current_root)] != current_root:
            return False
        entry_path = tuple(path[len(current_root) :])
        return any(
            len(entry_path) > len(prefix) and entry_path[: len(prefix)] == prefix
            for prefix in self._definition_entry_prefixes()
        )

    def parse_obj(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> None:
        """Parse a JsonSchemaObject by dispatching to appropriate parse methods.

        Model roots parsed here are shared definitions, so $dynamicRef resolves without the bindings
        of any referencing schema.
        """
        if not (self._has_embedded_schema_resources or self._schema_resource_registry.dynamic_anchors):
            self._parse_schema_obj(name, obj, path)
            return
        with self._dynamic_scope_context(path, {}):
            self._parse_schema_obj(name, obj, path)

    def _parse_schema_obj(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> None:
        """Dispatch a JsonSchemaObject to the parse method for its schema shape (``_DEFINITION_TABLE``).

        The models of the `$ref` sites its parse met are parsed after it (``_schema_refs.parse_ref_sites``).
        """
        choice = self._DEFINITION_TABLE.choose(self, SchemaSite(name, self._site_schema(obj), path))
        choice.build(self, choice.site)
        _schema_refs.parse_ref_sites(self)

    def _merge_definition_ref(self, site: _Site) -> _Site:
        """Merge the `$ref` of a definition with its sibling keywords (``_merge_site_ref``)."""
        return self._merge_site_ref(site, read_false_all_of=False)

    def _parse_definition_in_scope(self, site: _Site) -> None:
        with cast("AbstractContextManager[None]", site.scope):
            self._parse_schema_obj(site.name, site.obj, site.path)

    def _definition_root_self_reference_applies(self, site: _Site) -> bool:
        return bool(
            _merges_ref_siblings(site.obj)
            and site.obj.ref == "#"
            and site.obj.schema_location == _schema_documents.document_root(self, self.model_resolver.current_root)
        )

    def _rewrite_definition_root_self_reference(self, site: _Site) -> _Site:
        return replace(
            site, obj=self._schema_object(site.obj.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True))
        )

    def _definition_merged_ref_in_own_scope_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return site.scope is not None

    def _definition_ref_of_definition_entry_applies(self, site: _Site) -> bool:
        return bool(
            _merges_ref_siblings(site.obj)
            and site.obj.ref
            and (
                self._is_named_schema_definition_path(site.path)
                or tuple(site.path) in self._dynamic_specialization_paths
            )
        )

    def _build_definition_ref(self, site: _Site) -> None:
        ref = cast("str", site.obj.ref)
        target = self.resolve_ref(ref)
        self._definitions_without_model[self.model_resolver.join_path(tuple(site.path))] = target
        if (mapped := self._resolve_external_ref_mapping(ref)) is not None and (
            import_ := self._external_ref_mapping_import(*mapped)
        ) is not None:
            self._mapped_definition_imports[target.path] = import_
        return self.parse_ref(site.obj, site.path)

    def _type_users_by_import(self, reference: Reference, import_: Import) -> None:
        """Type each user of ``reference`` by ``import_``, as a mapped ``$ref`` is.

        The users in a field, a type or a dict key first, then those in the typed additional properties of each model,
        whose other referenced classes stay.
        """

        def imported_type() -> DataType:
            self._import_ledger.append(self.imports, import_, _EXTERNAL_REF_MAPPING_IMPORT)
            return self.data_type.from_import(import_)

        type_reference_users(self, reference, imported_type)
        for model in self.results:
            if reference.path in (classes := model.additional_properties_reference_classes):
                type_additional_properties_users(
                    self,
                    model,
                    reference,
                    partial(self.data_type.from_import, import_),
                    {path for path in classes if path != reference.path},
                )

    def _type_references_to_definitions_without_model(self) -> None:
        """Redirect the users of a definition parsed as its ``$ref`` alone to the reference that ``$ref`` names.

        Such a definition is a ``$ref`` with sibling keywords that cannot merge into its circular target, and outside
        a definitions container it gets no model. Once every schema is parsed, a reference to it is typed by its
        target (through such definitions in turn), as such a ``$ref`` is at a field, or by the import of a target
        that an external ref mapping names; a definition that got a model elsewhere keeps its users. A used one whose
        chain of such definitions loops back without reaching a model or an import has nothing to type it by, which
        is an error. A target model that names the definition then names itself: the chain it took in is recorded
        (``_folded_ref_chains``) for the error that stops at such a model.
        """
        if not self._definitions_without_model:
            return
        modelled = {model.reference.path: model for model in self.results}
        references = self.model_resolver.references
        for key, named in self._definitions_without_model.items():
            if key in modelled or (reference := references.get(key)) is None:
                continue
            target, seen = named, {key: None}
            while (
                target.path not in modelled
                and target.path not in seen
                and (next_target := self._definitions_without_model.get(target.path))
            ):
                seen[target.path] = None
                target = next_target
            if (import_ := self._mapped_definition_imports.get(target.path)) is not None:
                self._type_users_by_import(reference, import_)
            else:
                if target.path in seen and (
                    self.generation_store.index.has_data_type_references(reference)
                    or any(key in model.additional_properties_reference_classes for model in self.results)
                ):
                    cycle = " -> ".join(redact_reference(path) for path in (*seen, target.path))
                    msg = f"its $ref chain loops back without reaching a schema to type it by: {cycle}"
                    raise SchemaParseError(msg, path=[key])
                for model in self.results:
                    if key in (classes := model.additional_properties_reference_classes):
                        other_classes = {path for path in classes if path != key}
                        self._output_model_context.store_additional_properties_reference_classes(
                            model.extra_template_data, {*other_classes, target.path}
                        )
            if (target_model := modelled.get(target.path)) is not None and any(
                data_type.reference is reference for data_type in target_model.all_data_types
            ):
                self._folded_ref_chains[target.path] = tuple((references[path].name, path) for path in seen)
            self._graph_builder.redirect_reference_users(reference, target)

    def _reject_mapped_references_without_schema(self) -> None:
        """Stop with an error at a model that uses a mapped reference whose fragment names no schema.

        Such a reference, one to the whole document for example, names no class that ``--external-ref-mapping`` could
        import. A reference that no model uses, and one that got a model, such as the root of an input document, keep
        their output.
        """
        if not self._mapped_references:
            return
        modelled = {model.reference.path for model in self.results}
        index = self.generation_store.index
        for path, (python_package, fragment) in self._mapped_references.items():
            if (
                path in modelled
                or self._external_ref_mapping_import(python_package, fragment) is not None
                or not (facts := index.data_type_facts_for_reference(self.model_resolver.references[path]))
            ):
                continue
            owner = cast("DataModel", index.owner_model_for_data_type(facts[0].data_type))
            raise SchemaParseError(
                message=f"$ref {stable_repr(redact_reference(path))} names no schema of the document that "
                f"--external-ref-mapping maps to {stable_repr(python_package)}; a mapped $ref needs a fragment that "
                "names a schema of that document",
                path=_named_path(location_of(owner.reference), [owner.reference.path]),
            )

    def _is_base_stand_in(self, reference: Reference) -> bool:
        """Return whether the model of ``reference`` is a stand-in: never written, never split into variants."""
        return model_key_of(reference) in self._base_stand_in_imports

    def _is_base_stand_in_path(self, path: list[str]) -> bool:
        """Return whether the schema at ``path`` is parsed into a stand-in."""
        return bool(self._base_stand_in_imports) and (
            (reference := self.model_resolver.references.get(self.model_resolver.join_path(tuple(path)))) is not None
            and self._is_base_stand_in(reference)
        )

    def _base_import(self, reference: Reference) -> Import | None:
        """Return the import of the class that types a schema a base names, as it types a field that names it.

        A schema of a document that ``--external-ref-mapping`` maps is the class the mapping imports, a schema with
        ``x-python-import`` the class it names; a type expression in its place (``qualname``, ``native-field``,
        ``x-python-type``) names no class to inherit.
        """
        if (mapping := self._mapped_references.get(reference.path)) is not None:
            return self._external_ref_mapping_import(*mapping)
        if (
            not isinstance(x_python_import := self._python_import_values.get(reference.path), dict)
            or (full_path := self._get_x_python_import_path(x_python_import)) is None
            or self._get_x_python_import_expression(x_python_import, reference.path) is not None
        ):
            return None
        return self._get_x_python_import(full_path)

    def _base_stand_in_targets(self) -> dict[str, tuple[Reference, Import, Path | None]]:
        """Return the schemas models name as bases that got no model and that the output types by an import.

        Each comes with that import and the source of the first model that names it, whose module the stand-in takes.
        """
        modelled = {model.reference.path for model in self.results}
        targets: dict[str, tuple[Reference, Import, Path | None]] = {}
        for model in self.results:
            for base_class in model.base_classes:
                if (
                    (reference := base_class.reference) is None
                    or reference.path in modelled
                    or reference.path in targets
                    or self._is_base_stand_in(reference)
                    or (import_ := self._base_import(reference)) is None
                ):
                    continue
                targets[reference.path] = reference, import_, model.file_path
        return targets

    def _parse_base_stand_ins(self) -> None:
        """Parse each schema a base names and the output types by an import into a stand-in (D1).

        A stand-in is the model of that schema, parsed in its own document under the reference the base names, in the
        module of the first model that names it; it is never written, and once every module is processed the import
        replaces the bases that name it, so that everything before reads it as a generated base in every module. Only
        that schema is a stand-in: the other models its parse adds are written. A stand-in may name further such
        bases, and definitions parsed as their ``$ref`` alone, so the parse repeats until no new stand-in appears.
        Then the import types every other user of a stand-in, one that a reference its document does not map names,
        as it types a mapped ``$ref``. The parse adds nothing to the imports every module shares: a stand-in is never
        written, and the models the parse writes import what their types name.
        """
        if not (targets := self._base_stand_in_targets()):
            return
        stand_ins: list[tuple[Reference, Import]] = []
        shared_imports, self.imports = self.imports, Imports(self.use_exact_imports)
        try:
            while targets:
                for reference, import_, file_path in targets.values():
                    self._base_stand_in_imports[key := model_key_of(reference)] = import_
                    if reference.path not in self._mapped_references:
                        self._declared_class_stand_ins.add(key)
                    stand_ins.append((reference, import_))
                    self._parse_base_stand_in(reference, file_path)
                self._type_references_to_definitions_without_model()
                targets = self._base_stand_in_targets()
            for reference, import_ in stand_ins:
                self._type_users_by_import(reference, import_)
        finally:
            self.imports = shared_imports
        self._reuse_optimization_context = replace(
            self._reuse_optimization_context, base_stand_in_keys=frozenset(self._base_stand_in_imports)
        )

    def _parse_base_stand_in(self, reference: Reference, file_path: Path | None) -> None:
        """Parse the schema at the key of ``reference`` into its model, in its document and in the module of a model.

        A mapped document that does not exist gives the model the schema of an object without properties
        (``_schema_refs.UNREAD_MAPPED_SCHEMA``).
        """
        source_path = self.current_source_path
        if source_path is not None:
            self.current_source_path = file_path
        file_part = reference.path.partition("#")[0]
        try:
            with self._ref_document_context(file_part) as (_, raw):
                path_parts = list(self.model_resolver.current_root)
                self.parse_json_pointer(raw, reference.path, path_parts)
                self._parse_reserved_targets(raw, path_parts)
        except _schema_refs.RefDocumentNotFoundError as exc:
            if not _schema_refs.reads_as_unread_mapped_document(self, exc):
                raise
            with _schema_documents.referenced_document_context(
                self, _schema_refs.schema_document_context(self, _ref_path_parts(file_part))
            ):
                pointer = reference.path.partition("#")[2]
                object_paths, reference_paths = _split_json_pointer({}, pointer, names=self._pointer_slash_names)
                location = SchemaLocation(
                    _schema_documents.schema_resource_document(self, _ref_path_parts(file_part)), tuple(object_paths)
                )
                self._parse_pointer_target(
                    _located(dict(_schema_refs.UNREAD_MAPPED_SCHEMA), location),
                    reference.path,
                    reference_paths,
                    list(self.model_resolver.current_root),
                    location,
                )
        finally:
            self.current_source_path = source_path

    def _end_schema_parsing(self) -> None:
        """Settle definition references, parse stand-ins, reject mapped references that name no schema, run the hook."""
        self._type_references_to_definitions_without_model()
        self._parse_base_stand_ins()
        self._reject_mapped_references_without_schema()
        self._generate_forced_base_models()

    def _build_definition_array(self, site: _Site) -> object:
        return self.parse_array(site.name, site.obj, site.path)

    def _definition_enum_applies(self, site: _Site) -> bool:
        return self._reads_enum_class(site.obj) and not self.should_parse_enum_as_literal(
            site.obj, property_name=site.name
        )

    def _build_definition_enum(self, site: _Site) -> object:
        return self._parse_enum_class(site.name, self._definition_enum(site.obj), site.path)

    _DEFINITION_TABLE: ClassVar[DispatchTable[JsonSchemaParser, JsonSchemaObject, None]] = DispatchTable(
        (
            Step(
                "root-self-reference",
                _definition_root_self_reference_applies,
                _rewrite_definition_root_self_reference,
            ),
            Step("ref-siblings", _ref_siblings_applies, _merge_definition_ref),
            Row("merged-ref-in-own-scope", _definition_merged_ref_in_own_scope_applies, _parse_definition_in_scope),
            Row(
                "ref-of-definition-entry",
                _definition_ref_of_definition_entry_applies,
                _then_parse_refs(_parse_root_type_site),
            ),
            Row("ref", _ref_siblings_applies, _build_definition_ref),
            Row("array", _array_applies, _then_parse_refs(_build_definition_array)),
            Row(
                "all-of-beside-one-of",
                _all_of_beside_one_of_applies,
                _then_parse_refs(_parse_root_type_site),
            ),
            Row("all-of", _all_of_applies, _then_parse_refs(_parse_all_of_site)),
            Row(
                "schema-validator-object",
                _schema_validator_object_applies,
                _then_parse_refs(_parse_object_model_site),
            ),
            Row(
                "one-of-any-of",
                _one_of_any_of_applies,
                _then_parse_refs(partial(_parse_combined_root, const_enum=True)),
            ),
            *(Row(row.name, row.applies, _then_parse_refs(row.build)) for row in _OBJECT_KIND_ROWS),
            Row("enum", _definition_enum_applies, _then_parse_refs(_build_definition_enum)),
        ),
        Default("root-type", _then_parse_refs(_parse_root_type_site)),
        typed=_typed_model_children,
    )

    def _iter_local_source_paths(self) -> Iterator[Path]:
        return _schema_documents.iter_local_source_paths(self)

    @override
    def _source_from_path(self, path: Path, *, data: bytes | None = None) -> Source:
        """Load one source path and contextualize cached JSON/YAML parse failures."""
        try:
            return super()._source_from_path(path, data=data)
        except (json.JSONDecodeError, *get_yaml_parse_errors()) as exc:
            source_path = path.relative_to(self.base_path) if path.is_relative_to(self.base_path) else path
            raise InvalidFileFormatError(exc, self._input_file_type, source=source_path) from exc

    def _load_source_dict(self, source: Source) -> dict[str, YamlValue]:
        """Load a source into a schema dictionary."""
        return _schema_documents.load_source_dict(self, source)

    @contextmanager
    def _parsing_session(self, resolve: Callable[[], None] | None = None) -> Generator[None, None, None]:
        """Parse in the block, then the targets it leaves (``resolve``), end the schema parsing and clear the caches."""
        try:
            yield
            _schema_refs.parse_ref_sites(self)
            (resolve or self._resolve_unparsed_json_pointer)()
            _schema_refs.parse_ref_sites(self)
            self._end_schema_parsing()
        finally:
            self._clear_inherited_field_caches()
            self._reset_local_source_cache()

    @override
    def parse_raw(self) -> None:
        """Parse all raw input sources into data models."""
        with self._parsing_session():
            for source, path_parts in _schema_documents.get_context_source_path_parts(self):
                try:
                    raw_obj = self._load_source_dict(source)
                except TypeError:
                    warn(f"{source.path} is empty or not a dict. Skipping this file", stacklevel=2)
                    continue
                _schema_documents.parse_source(self, source, raw_obj, path_parts)

    def _resolve_unparsed_json_pointer(self) -> None:
        """Parse the targets each source reserves, in key order, until a round over the sources adds no model.

        A target is read from the prepared tree of its source document, the tree the document's models came from, where
        the references an embedded `$id` resolves name their targets in the document.
        """
        while True:
            model_count = len(self.results)
            for source in self.iter_source:
                path_parts = list(source.path.parts)
                if not self.reserved_refs.get(tuple(path_parts)):
                    continue
                if self.current_source_path is not None:
                    self.current_source_path = source.path
                prepared = self._schema_resource_cache[_schema_documents.schema_resource_document(self, path_parts)][1]
                with (
                    self.model_resolver.current_base_path_context(source.path.parent),
                    self.model_resolver.current_root_context(path_parts),
                ):
                    for target in _schema_documents.reserved_targets(
                        self, path_parts, prepared, self._reserved_ref_loaded
                    ):
                        self.raw_obj = prepared
                        self.parse_json_pointer(prepared, target["key"], path_parts)
            if model_count == len(self.results):
                return

    def _reserved_ref_loaded(self, key: str) -> bool:
        """Return whether the model of a reserved key is loaded, adding its reference when it has none."""
        return self.model_resolver.add_ref(key, resolved=True).loaded

    def _reserved_ref_settled(self, key: str) -> bool:
        """Return whether a reserved key has no reference, or names a loaded model."""
        return (reference := self.model_resolver.references.get(key)) is None or reference.loaded

    def parse_json_pointer(self, raw: dict[str, YamlValue], ref: str, path_parts: list[str]) -> None:
        """Parse a JSON pointer reference into a model."""
        path = ref.split("#", 1)[-1]
        object_paths, reference_paths = _split_json_pointer(raw, path, names=self._pointer_slash_names)
        if not object_paths:  # pragma: no cover
            self._parse_referenced_root(self.model_resolver.add_ref(ref).name, raw, ref)
            return
        models = self._get_model_by_json_pointer(raw, object_paths, ref)
        self._parse_pointer_target(models, ref, reference_paths, path_parts, self._member_location(raw, *object_paths))

    def _parse_pointer_target(
        self,
        models: YamlValue,
        ref: str,
        reference_paths: list[str],
        path_parts: list[str],
        location: SchemaLocation | None = None,
    ) -> None:
        """Parse the value a JSON pointer reference names, at the path and with the name its tokens give."""
        tokens = self._pointer_path_tokens(ref, reference_paths)
        self._parse_raw_at(tokens[-1], models, self._pointer_parse_path(path_parts, tokens), location)

    def _pointer_path_tokens(self, ref: str | None, reference_paths: list[str]) -> list[str]:
        """Return the tokens of the pointer of ``ref`` that its target's parse path and model name are made of.

        Under --read-escaped-ref-pointers they are the reference tokens the pointer encodes, the ones its key is made of
        (``EscapedPointerModelResolver``); without it, row ``legacy-ref-spelling-keys``, the parts of the pointer as the
        reference spells them.
        """
        if not self.config.read_escaped_ref_pointers:
            return reference_paths
        return list(reference_tokens((ref or "").partition("#")[2]) or ())

    def _get_model_by_json_pointer(
        self,
        raw: dict[str, YamlValue],
        object_paths: list[str],
        ref: str,
    ) -> YamlValue:
        """Resolve one JSON pointer, preserving the legacy empty-schema fallback when it is missing.

        The fallback stands for the missing target, so it is located where the pointer points.
        """
        model = _get_model_by_decoded_path(raw, object_paths, or_missing=True)
        if model is not _MISSING_JSON_POINTER:
            return cast("YamlValue", model)

        source, _, fragment = ref.partition("#")
        if not source:
            source = self._source_path_for_diagnostics(self.current_source_path)
        self._dangling_refs.add((source, f"#{fragment}"))
        if self.config.read_escaped_ref_pointers and (pointer := unescaped_slash_pointer(raw, object_paths)):
            self._unescaped_slash_pointers[source, f"#{fragment}"] = pointer
        return _located({}, self._member_location(raw, *map(str, object_paths)))

    @override
    def _report_parse_diagnostics(self) -> None:
        """Report each unique dangling local reference and compatibility resolution after schema parsing completes.

        Each schema object of a document whose ``enum`` lists no value is reported too: no value is valid against it,
        while the generated code reads it as if it had no ``enum``. So is each ``pattern`` that no regex engine of the
        output compiles, which the generated code leaves out (``read_pattern``).
        """
        for message in sorted(self._resource_ref_warnings):
            warn(message, SchemaResourceRefWarning, stacklevel=stacklevel_at_parse_caller())
        self._resource_ref_warnings.clear()
        for document in sorted(self._unread_mapped_documents):
            warn(
                f"The document {stable_repr(redact_url(document))} that --external-ref-mapping maps does not exist, so "
                "the models that inherit a class it maps are written from their own keywords only, without the "
                "properties its schema declares. Add the document to read them.",
                DanglingRefWarning,
                stacklevel=stacklevel_at_parse_caller(),
            )
        self._unread_mapped_documents.clear()
        for where in sorted({named_location(location, "schema") for location in self._empty_enum_locations}):
            warn(
                f"The schema at {where} admits no value: its enum is empty. It is generated as if it had no enum.",
                stacklevel=stacklevel_at_parse_caller(),
            )
        self._empty_enum_locations.clear()
        for where, pattern in sorted(
            (named_location(location, "schema"), pattern) for location, pattern in self._unread_patterns.items()
        ):
            warn(
                f"The pattern {stable_repr(pattern)} of the schema at {where} is left out: no regex engine of the "
                "output compiles it, so the generated code does not check it.",
                stacklevel=stacklevel_at_parse_caller(),
            )
        self._unread_patterns.clear()
        if not self._dangling_refs:
            return

        dangling_refs = sorted(self._dangling_refs)
        if self.strict_refs:
            details = "\n".join(
                f"- {redact_url(source)}: {ref}{self._unescaped_slash_hint(source, ref)}"
                for source, ref in dangling_refs
            )
            msg = f"Unresolved local $ref targets:\n{details}"
            raise Error(msg)

        for source, ref in dangling_refs:
            warn(
                f"Unresolved local $ref {stable_repr(ref)} in {redact_url(source)}: JSON pointer was not found"
                f"{self._unescaped_slash_hint(source, ref)}. Generated a fallback Any model; use --strict-refs to fail "
                "instead.",
                DanglingRefWarning,
                stacklevel=stacklevel_at_parse_caller(),
            )
        self._dangling_refs.clear()
        self._unescaped_slash_pointers.clear()

    def _unescaped_slash_hint(self, source: str, ref: str) -> str:
        """Return how to write a dangling reference whose unescaped ``/`` splits a key, or nothing."""
        if (pointer := self._unescaped_slash_pointers.get((source, ref))) is None:
            return ""
        return f" (a '/' in a name is written '~1': {stable_repr(f'#{pointer}')} names the key the pointer splits)"

    @staticmethod
    @lru_cache(maxsize=16)
    def _schema_object_raw_key_sets(
        schema_object_type: type[JsonSchemaObject],
    ) -> tuple[frozenset[str], frozenset[str]]:
        keys = set(DEFINITION_KEYS)
        for name, field in schema_object_type.get_fields().items():
            keys.add(name)
            if alias := getattr(field, "alias", None):
                keys.add(alias)
        metadata_keys = {
            *schema_object_type.__metadata_only_fields__,
            "extras",
            schema_object_type.__extra_key__,
        }
        schema_affecting_keys = {
            *keys,
            *schema_object_type.__schema_affecting_extras__,
        } - metadata_keys
        return frozenset(keys), frozenset(schema_affecting_keys)

    def _known_schema_object_raw_keys(self) -> frozenset[str]:
        return _schema_documents.known_schema_object_raw_keys(self)

    def _has_schema_affecting_keywords(self, raw: dict[str, Any]) -> bool:
        return _schema_documents.has_schema_affecting_keywords(self, raw)

    def _is_version_definition_namespace_name(self, name: str) -> bool:  # noqa: PLR6301
        return re.fullmatch(r"v\d+(?:[._-]\d+)*", name, flags=re.IGNORECASE) is not None

    def _parse_file(  # noqa: PLR0913
        self,
        raw: dict[str, Any],
        obj_name: str,
        path_parts: list[str],
        object_paths: list[str] | None = None,
        reference_paths: list[str] | None = None,
        *,
        preserve_root_class_name: bool = False,
        ref: str | None = None,
    ) -> None:
        """Parse a file containing JSON Schema definitions and references."""
        with self._detached_naming_frames():  # noqa: PLR1702
            object_paths = [o for o in object_paths or [] if o]
            reference_paths = [r for r in reference_paths or [] if r]
            path = (
                self._pointer_parse_path(path_parts, tokens)
                if (tokens := self._pointer_path_tokens(ref, reference_paths or object_paths))
                else path_parts
            )
            with self.model_resolver.current_root_context(path_parts):
                prepared = _schema_documents.prepare_schema_resources(self, raw, path_parts)
                if self.raw_obj is raw:
                    self.raw_obj = prepared
                raw = prepared
                self._schema_resource_path_parts[_schema_documents.schema_resource_document(self, path_parts)] = (
                    path_parts
                )
                obj_name = self.model_resolver.add(
                    path,
                    obj_name,
                    unique=False,
                    class_name=True,
                    preserve_class_name=preserve_root_class_name,
                ).name
                with self.root_id_context(raw):
                    # parse $id before parsing $ref
                    root_obj = self._validate_schema_object(
                        string_keyed(raw) if object_paths else raw, path_parts or ["#"]
                    )
                    if (
                        self.config.generate_schema_validators
                        and raw is self.raw_obj
                        and root_obj.patternProperties
                        and not _schema_validators.customizes_models(self.config)
                    ):
                        self._pattern_validation_document_root = root_obj
                    self._cache_ref_data_type_facts(self.model_resolver.join_path(tuple(path_parts or ["#"])), root_obj)
                    self.parse_id(root_obj, [*path_parts, "#"] if path_parts else ["#"])
                    _schema_refs.build_anchor_indexes(self, root_obj, path_parts)
                    schema_path, definitions = _schema_documents.first_definitions(self, raw, None)
                    definition_metadata_entries = _schema_documents.definition_entries(
                        self, definitions, [*path_parts, schema_path]
                    )
                    definition_entries = definition_metadata_entries[len(definitions) :]
                    seen_definition_metadata_paths: set[tuple[str, ...]] = set()
                    validated_definition_objects: dict[tuple[str, ...], JsonSchemaObject] = {}
                    for _key, model, definition_path in definition_metadata_entries:
                        if (definition_path_key := tuple(definition_path)) in seen_definition_metadata_paths:
                            continue
                        seen_definition_metadata_paths.add(definition_path_key)
                        obj = self._validate_member(
                            model,
                            definition_path,
                            self._member_location(definitions, *definition_path[len(path_parts) + 1 :]),
                        )
                        validated_definition_objects[definition_path_key] = obj
                        self._cache_ref_data_type_facts(self.model_resolver.join_path(tuple(definition_path)), obj)
                        self.parse_id(obj, definition_path)
                        _schema_refs.build_anchor_indexes(self, obj, definition_path)

                    if object_paths:
                        models = (
                            get_model_by_path(raw, object_paths)
                            if ref is None
                            else self._get_model_by_json_pointer(raw, object_paths, ref)
                        )
                        self._parse_pointer_definition(raw, object_paths, models, object_paths[-1], path)
                    elif not self.skip_root_model:
                        self._parse_document_root(
                            obj_name, root_obj, raw, path_parts or ["#"], referenced=ref is not None
                        )
                    for key, model, path in definition_entries:
                        reference = self.model_resolver.references.get(self.model_resolver.join_path(tuple(path)))
                        if reference is None:
                            reference = self.model_resolver.get(path)
                        if not reference or not reference.loaded:
                            with self._value_held_at(
                                model, self._member_location(definitions, *path[len(path_parts) + 1 :])
                            ):
                                self._parse_raw_or_validated_obj(
                                    key,
                                    model,
                                    path,
                                    validated_definition_objects.get(tuple(path)),
                                )

                    self._parse_reserved_targets(raw, path_parts)

    def _parse_reserved_targets(self, raw: dict[str, Any], path_parts: list[str]) -> None:
        """Parse the targets the document at ``path_parts`` reserves from its prepared tree, while it reserves more."""
        taken: set[str] = set()
        while (reserved := self.reserved_refs.get(tuple(path_parts))) and reserved != taken:
            taken = set(reserved)
            for target in _schema_documents.reserved_targets(self, path_parts, raw, self._reserved_ref_settled):
                reserved_path = target["key"]
                reference = self.model_resolver.references[reserved_path]
                object_paths, reference_paths = _split_json_pointer(
                    raw, reserved_path.split("#", 1)[-1], names=self._pointer_slash_names
                )
                if not object_paths:
                    self._parse_referenced_root(reference.name, raw, reserved_path)
                    continue
                models = self._get_model_by_json_pointer(raw, object_paths, reserved_path)
                tokens = self._pointer_path_tokens(reserved_path, reference_paths)
                path = self._pointer_parse_path(path_parts, tokens)
                self._parse_pointer_definition(raw, object_paths, models, tokens[-1], path)

    def _pointer_parse_path(self, root: list[str], tokens: list[str]) -> list[str]:
        """Return the parse path of the target that the ``tokens`` of a JSON pointer name below the path ``root``.

        Under --read-escaped-ref-pointers the tokens are raw and the first one is written encoded into the pointer the
        path opens with (``fragment_pointer``).
        """
        head = fragment_pointer(tokens[:1]) if self.config.read_escaped_ref_pointers else f"/{tokens[0]}"
        return [*root, f"#{head}", *tokens[1:]]


# Snapshot the default hooks after JsonSchemaParser is fully defined. The local
# false-ref fact fast path is disabled for any parser that customizes a loader.
_BUILTIN_REF_RAW_SCHEMA_LOADER = JsonSchemaParser._get_ref_raw_schema  # noqa: SLF001
_BUILTIN_REF_SCHEMA_LOADER = JsonSchemaParser._load_ref_schema_object  # noqa: SLF001
_BUILTIN_SCHEMA_VALIDATOR = JsonSchemaParser._validate_schema_object  # noqa: SLF001
_BUILTIN_REF_FACT_CACHER = JsonSchemaParser._cache_ref_data_type_facts  # noqa: SLF001
