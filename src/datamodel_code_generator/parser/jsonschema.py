"""JSON Schema parser implementation.

Handles parsing of JSON Schema, JSON, YAML, Dict, and CSV inputs to generate
Python data models. Supports draft-04 through draft-2020-12 schemas.
"""

from __future__ import annotations

import enum as _enum
import json
import math
import re
from collections import Counter, defaultdict, deque
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from copy import copy, deepcopy
from dataclasses import dataclass, replace
from functools import cached_property, lru_cache, partial, reduce, wraps
from itertools import chain, compress
from operator import and_, is_not
from pathlib import Path
from string import digits
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, NamedTuple, Optional, TypeAlias, TypeVar, Union, cast
from urllib.parse import ParseResult, unquote, urljoin, urlparse
from warnings import catch_warnings, simplefilter, warn

from pydantic import (
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import core_schema
from typing_extensions import Sentinel, Unpack, final, override

from datamodel_code_generator import (
    AllOfClassHierarchy,
    AllOfMergeMode,
    DanglingRefWarning,
    Error,
    InputFileType,
    InvalidClassNameError,
    InvalidFileFormatError,
    JsonSchemaVersion,
    ReadOnlyWriteOnlyModelType,
    SchemaParseError,
    SchemaResourceRefWarning,
    VersionMode,
    snooper_to_methods,
)
from datamodel_code_generator._alias_generators import to_camel, to_pascal, to_snake
from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._constraint_applicability import (
    LITERAL_DECIDABLE_KEYWORDS,
    NO_KEYWORDS,
    NUMBER_KEYWORD_ORDER,
    PROPERTY_COUNT_KEYWORDS,
    STRING_KEYWORD_ORDER,
    VALUES_AS_WRITTEN_KEY,
    DeclaredKinds,
    all_of_root_keyword_kinds,
    constrained_type_keyword_order,
    declared_keywords,
    declared_string_keywords_taken,
    holds_number_format,
    keyword_kinds,
    literal_check,
    property_consumed_keywords,
    split_declared_keywords,
    undeclared_keywords_taken,
    untaken_values,
)
from datamodel_code_generator._format_types import (
    DatetimeClassType,
)
from datamodel_code_generator._json_schema import SCHEMA_MAP_KEYS, SCHEMA_VALUE_KEYS
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._regex_syntax import (
    conjoin_patterns,
    is_portable_pattern,
    literal_pattern_value,
    lookahead_patterns,
)
from datamodel_code_generator._requirements import python_target_meets
from datamodel_code_generator._shared_types import DefaultPutDict
from datamodel_code_generator._source import (
    YamlValue,
    _load_parser_source_data_from_path_bytes,
    load_data,
    load_data_from_path,
)
from datamodel_code_generator._url_redaction import redact_path, redact_reference, redact_url
from datamodel_code_generator.deprecations import warn_deprecated
from datamodel_code_generator.enums import AliasGenerator
from datamodel_code_generator.imports import IMPORT_ANY, Import, Imports
from datamodel_code_generator.model import DataModel, DataModelFieldBase
from datamodel_code_generator.model._decisions.enum_kind import (
    decide_enum_kind,
    enum_as_literal,
    holds_inherited_member_value,
    holds_member_value,
    is_literal_kind,
    literal_value_kind,
)
from datamodel_code_generator.model._decisions.literal_default import const_type
from datamodel_code_generator.model._decisions.tag_value import (
    FieldTag,
    distinct_tags,
    field_tag,
    tagged_union_discriminator,
    union_tag_values,
)
from datamodel_code_generator.model.base import (
    UNDEFINED,
    _keyed_answer,
    c3_merge,
    get_inherited_fields,
    sanitize_module_name,
)
from datamodel_code_generator.model.enum import (
    NULL_ENUM_MEMBER_VALUE,
    SUBCLASS_BASE_CLASSES,
    Enum,
    EnumMemberValue,
    IntEnum,
    StrEnum,
    _enum_class,
)
from datamodel_code_generator.model.output import OutputModelContext
from datamodel_code_generator.model.runtime_validation import (
    UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP,
    ConditionalRequiredRule,
    IndependentDeclaredPatternPropertiesRule,
    IndependentModelPatternPropertiesRule,
    PatternPropertiesRule,
    PropertyCountRule,
    RequiredGroupsRule,
    SchemaRuntimeValidation,
    UniqueItemsPath,
    UniqueItemsPathStep,
    UniqueItemsRule,
    _is_internal_schema_runtime_validation,
    _make_internal_schema_runtime_validation,
)
from datamodel_code_generator.parser._graph_ids import BuildFrames
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._intersection import (
    ENUM_METADATA_KEYWORDS,
    KeywordIntersection,
    bounds_admit_no_value,
    enum_metadata,
    intersect_constraint,
    intersect_enum_values,
    intersect_keywords,
    intersect_multiple_of,
    warn_unsatisfiable,
    warn_untaken_patterns,
)
from datamodel_code_generator.parser._intersection import json_values_equal as _json_literal_values_equal
from datamodel_code_generator.parser._null_facts import (
    NullFacts,
    NullMode,
    NullSite,
    NullSources,
    resolve_null_facts,
)
from datamodel_code_generator.parser._passes._keys import _DERIVED_FROM_KEY, _RAW_SCHEMA_DEFAULT_UNDEFINED
from datamodel_code_generator.parser._passes._shared import _detach_deferred_inherited_field_parents
from datamodel_code_generator.parser._pipeline import stacklevel_at_parse_caller
from datamodel_code_generator.parser._schema_dispatch import (
    Default,
    DispatchTable,
    Row,
    SchemaSite,
    Step,
)
from datamodel_code_generator.parser._schema_graph import (
    SCHEMA_GRAPH,
    ConvertedRefs,
    DocumentId,
    RefEdge,
    RefKey,
    RefOrigin,
    RefReading,
    ReservedTarget,
    ResolvedRef,
    SchemaGraphBuilder,
    canonical_ref_spelling,
    keep_resolved_ref,
    same_values,
    walk_schema_resources,
)
from datamodel_code_generator.parser.base import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
    _TYPE_DEPENDENT_CONSTRAINTS_KEY,
    SPECIAL_PATH_FORMAT,
    Parser,
    Source,
    _ConstNarrowing,
    _copy_data_model_field,  # noqa: F401  # Preserve the existing parser helper export.
    _copy_data_type,  # noqa: F401  # Preserve the existing parser helper export.
    _copy_resolved_inherited_field,  # noqa: F401  # Preserve the existing parser helper export.
    _get_inherited_type_modifiers,
    _override_path,
    get_special_path,
    title_to_class_name,
)
from datamodel_code_generator.parser.schema_version import (
    JsonSchemaFeatures,
    _detect_declared_jsonschema_version,
    detect_jsonschema_version,
    get_data_formats,
)
from datamodel_code_generator.python_literal import (
    _make_referencing_type_expression,
    _safe_non_finite_value,
    _semantic_value_text,
)
from datamodel_code_generator.reference import (
    _ALIAS_RESOLUTION_CLASS_NAME_KEY,
    SCHEMA_LOCATION_CONFLICT_KEY,
    SCHEMA_LOCATION_KEY,
    SCHEMA_OWNER_FRAMES_KEY,
    SPECIAL_PATH_MARKER,
    ModelType,
    Reference,
    SchemaLocation,
    get_inferred_module_name,
    is_url,
    location_of,
)
from datamodel_code_generator.types import (
    ANY,
    NONE,
    DataType,
    EmptyDataType,
    FloatConstraint,
    Types,
    UnionIntFloat,
    _is_value_literal,
    build_union,
)
from datamodel_code_generator.util import BaseModel, get_yaml_parse_errors

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Container, Generator, Iterable, Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager
    from typing import TypeGuard

    from pydantic import GetCoreSchemaHandler, ValidatorFunctionWrapHandler
    from pydantic_core import CoreSchema
    from typing_extensions import Self, TypeIs

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr, PythonTypeRuntimeSymbol
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator._types import JSONSchemaParserConfigDict
    from datamodel_code_generator.config import JSONSchemaParserConfig
    from datamodel_code_generator.model._decisions.enum_kind import LiteralSetting, LiteralValueKind
    from datamodel_code_generator.parser._schema_graph import SchemaNodeId

JsonSchemaLiteral = Union[bool, int, str]  # noqa: UP007
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
TaggedUnionValue = Union[int, str]  # noqa: UP007
_CONST_ENUM_TYPES: Final[Mapping[LiteralValueKind, str]] = {
    "str": "string",
    "bool": "boolean",
    "int": "integer",
    "float": "number",
}
"""The ``type`` a merged enum of const branches takes from the kind of the first const that has one."""
_LIGHTWEIGHT_MAX_DEPTH: Final = 3
_LIGHTWEIGHT_MAX_UNION_ELEMENTS: Final = 5
_ARRAY_ITEMS_CONSTRAINT_FIELDS = frozenset({"items", "additionalItems", "prefixItems", "unevaluatedItems"})


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


_MIN_UNION_VARIANT_LITERAL_VALUES = 2
_EXTERNAL_REF_MAPPING_IMPORT = ImportProducer("external-ref-mapping")
_X_PYTHON_IMPORT = ImportProducer("x-python-import")
_ALL_OF_METADATA_FIELDS = ("nullable", "description", "default", "example", "examples", "readOnly", "writeOnly")
_ANNOTATION_KEYWORDS = frozenset({
    "$comment",
    "default",
    "deprecated",
    "description",
    "example",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
})
_NON_ASSERTING_KEYWORDS = _ANNOTATION_KEYWORDS | {"contentEncoding", "contentMediaType", "contentSchema", "format"}
_FALSE_ALL_OF_KEYWORDS = _ANNOTATION_KEYWORDS | {"allOf"}
_INHERITED_NESTED_SCHEMA_FIELDS = (
    "items",
    "additionalItems",
    "prefixItems",
    "additionalProperties",
    "unevaluatedItems",
    "unevaluatedProperties",
    "properties",
    "patternProperties",
    "propertyNames",
)
_INHERITED_POSITIONAL_SCHEMA_FIELDS = frozenset({"items", "prefixItems"})
_INHERITED_SCHEMA_MAP_FIELDS = frozenset({"patternProperties", "properties"})
_ALLOF_BOUND_CONSTRAINT_FIELDS = frozenset({
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    *PROPERTY_COUNT_KEYWORDS,
})
_INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS = frozenset({"contains", "maxContains", "minContains"})
_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY = "_raw_schema_explicit_field_extras"
_INHERITED_TYPE_SHAPE_FIELDS = frozenset({
    "$dynamicRef",
    "$recursiveRef",
    "$ref",
    "additionalItems",
    "additionalProperties",
    "allOf",
    "anyOf",
    "customBasePath",
    "customTypePath",
    "discriminator",
    "items",
    "oneOf",
    "patternProperties",
    "prefixItems",
    "properties",
    "propertyNames",
    "required",
    "type",
    "unevaluatedItems",
    "unevaluatedProperties",
})
_INHERITED_CONTAINER_TYPE_FIELDS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (
        frozenset({"array"}),
        frozenset({"additionalItems", "items", "prefixItems", "unevaluatedItems"}),
    ),
    (
        frozenset({"object"}),
        frozenset({
            "additionalProperties",
            "patternProperties",
            "properties",
            "propertyNames",
            "required",
            "unevaluatedProperties",
        }),
    ),
)
_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY = "_inherited_materialized_type_shape"
_MERGED_REF_TARGET_KEY = "_merged_ref_target"
_COPIED_NULLABLE_KEY = "_copied_nullable"
_DEFAULT_LEFT_OFF_KEY = "_default_left_off"
_NULLABLE_TYPE_LIST_KEY = "_nullable_type_list"
_RECORDED_TYPE_NULL_SITES: frozenset[NullSite] = frozenset({
    "object-model",
    "root-model",
    "type-union",
    "multiple-types-root",
    "combined-branch",
    "inherited-child",
})
"""The sites that read the answer ``JsonSchemaObject.type_has_null`` recorded when it was first asked."""
_INHERITED_OVERRIDE_KEY = "_inherited_override"
_DERIVED_FROM_REF_KEY = "_derived_from_ref"
_REPORTED_VALUE_CONFLICT_KEY = "_reported_value_conflict"
_VALUE_CONFLICT_KEYWORDS = frozenset({"enum", "const"})
_ALL_OF_VALUE_PART_EXCLUDED_EXTRAS = frozenset({
    "contains",
    "dependentRequired",
    "dependentSchemas",
    "else",
    "if",
    "not",
    "then",
    "x-python-type",
})
_NO_INHERITED_SCHEMA_MERGE = Sentinel("_NO_INHERITED_SCHEMA_MERGE")

_PYTHON_UNION_BASE_TYPES = frozenset({"Union", "Optional"})
_QUALIFIED_PYTHON_TYPE_IMPORT_ALIASES = {
    "pydantic.main.BaseModel": Import.from_full_path("pydantic.BaseModel"),
}
_RW_MODEL_VARIANT_SPECIAL_MARKERS = frozenset({
    SPECIAL_PATH_FORMAT.format("read-write-request"),
    SPECIAL_PATH_FORMAT.format("read-write-response"),
})
_REF_SIBLING_KEYWORDS_DISABLED_VERSIONS = frozenset({
    JsonSchemaVersion.Draft4,
    JsonSchemaVersion.Draft6,
    JsonSchemaVersion.Draft7,
})
_PYTHON_FIELD_OVERRIDES_KEY = "__python_field_overrides"
_READ_AS_CONST_KEY = "x-datamodel-code-generator-read-as-const"


_SchemaT = TypeVar("_SchemaT", bound="JsonSchemaObject")
_ValidatedT = TypeVar("_ValidatedT")
_TargetNode: TypeAlias = "SchemaNodeId | str"
"""The schema a walk reaches through a reference: its graph node, or its key when the graph does not locate it."""


class _LocatedDict(dict):  # noqa: FURB189 - pydantic validates it as the dict it is
    """The dump of a located schema object, which remembers the location of the object it was dumped from."""

    __slots__ = ("schema_location",)

    schema_location: SchemaLocation


def _located(data: dict[str, Any], location: SchemaLocation | None) -> dict[str, Any]:
    """Return a dumped schema object that remembers ``location``."""
    if location is None:
        return data
    result = _LocatedDict(data)
    result.schema_location = location
    return result


def _schema_node(schema: JsonSchemaObject, resolved_ref: str) -> _TargetNode:
    """Return the schema graph node a loaded schema object stands for, or its key when it has no location."""
    return resolved_ref if (location := schema.schema_location) is None else cast("SchemaNodeId", location)


def _dump_location(data: object) -> SchemaLocation | None:
    """Return the location a dumped schema object remembers, if it is one."""
    return data.schema_location if isinstance(data, _LocatedDict) else None


def _source_location(values: object) -> SchemaLocation | None:
    """Return the location of the input a model is validated from: a located dump or a document object."""
    return (
        values.schema_location
        if isinstance(values, _LocatedDict)
        else None
        if (graph := SCHEMA_GRAPH.get()) is None
        else graph.locations.get(id(values))
    )


def _take_input_location(values: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """Validate the fields of a located model; the instance takes the location of the input they come from."""
    fields = handler(values)
    if (location := _source_location(values)) is not None:
        fields[0][SCHEMA_LOCATION_KEY] = location
    return fields


def _located_model_schema(source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
    """Return the core schema of a model whose instances take the location of the input they are validated from.

    The locating validator wraps the field validation inside the model schema, where pydantic's error locations do not
    name it, so they keep the model's name (a model validator in wrap mode would replace it there). Every schema that
    holds the model, the fields a subclass adds and ``TypeAdapter`` roots included, validates it this way.
    """
    schema = handler(source)
    model: Any = handler.resolve_ref_schema(schema)
    while model["type"] != "model":
        model = model["schema"]
    inner = model["schema"]
    if inner["type"] != "function-wrap" or inner["function"]["function"] is not _take_input_location:
        model["schema"] = core_schema.no_info_wrap_validator_function(_take_input_location, inner)
    return schema


_Site: TypeAlias = "SchemaSite[JsonSchemaObject]"


def _merges_ref_siblings(obj: JsonSchemaObject) -> bool:
    """Return whether a definition reads the sibling keywords of its `$ref` with the schema it names."""
    return obj.has_ref_with_schema_keywords and not obj.is_ref_with_nullable_only


def _has_map_keywords(obj: JsonSchemaObject) -> bool:
    """Return whether a schema has keywords that type the keys or values of an object map."""
    return bool(
        obj.patternProperties or obj.propertyNames is not None or isinstance(obj.additionalProperties, JsonSchemaObject)
    )


def _then_parse_refs(build: Callable[[JsonSchemaParser, _Site], object]) -> Callable[[JsonSchemaParser, _Site], None]:
    """Return the build of a definition row: ``build``, then the references of the site's schema."""

    def build_definition(parser: JsonSchemaParser, site: _Site) -> None:
        build(parser, site)
        parser.parse_ref(site.obj, site.path)

    return build_definition


def _named_path(location: SchemaLocation | None, path: list[str]) -> list[str]:
    """Return the path a message names: the schema location, or the parse path when the location is unknown."""
    return path if location is None else [str(location)]


def _locate(obj: _SchemaT, location: SchemaLocation | None) -> _SchemaT:
    """Give a schema object that a copy step created the location of the schema object it stands for, if it has one."""
    obj.__dict__[SCHEMA_LOCATION_KEY] = location
    return obj


def _dump_without_keywords(schema: JsonSchemaObject, keywords: Container[str]) -> dict[str, Any]:
    """Dump the keywords a schema object sets, leaving out ``keywords`` also among its extension keywords."""
    schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
    for key in [key for key in schema_dict if key in keywords]:
        del schema_dict[key]
    if isinstance(extras := schema_dict.get(JsonSchemaObject.__extra_key__), dict):
        if meaningful_extras := {key: value for key, value in extras.items() if key not in keywords}:
            schema_dict[JsonSchemaObject.__extra_key__] = meaningful_extras
        else:
            schema_dict.pop(JsonSchemaObject.__extra_key__, None)
    return schema_dict


def _is_false_all_of(schema: object) -> bool:
    """Return whether a schema is ``false`` written as an allOf that holds false, beside annotations and ``x-`` keys.

    The schema is a schema object or, before validation, the dict it is validated from.
    """
    match schema:
        case JsonSchemaObject() if any(item is False for item in schema.allOf):
            remaining = _dump_without_keywords(schema, _FALSE_ALL_OF_KEYWORDS)
            extras = remaining.pop(JsonSchemaObject.__extra_key__, {})
            return not any(not key.startswith("x-") for key in (*remaining, *extras))
        case {"allOf": list() as all_of} if any(item is False for item in all_of):
            plain_keys = {key for key in schema if not (isinstance(key, str) and key.startswith("x-"))}
            return plain_keys <= _FALSE_ALL_OF_KEYWORDS
    return False


def _validation_keywords(
    value: dict[str, Any] | bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    canonical_ref: Callable[[str], str],
) -> object:
    """Return the keywords of a dumped schema that decide what it accepts, to compare schemas by what they accept.

    Keywords that only annotate (meta-data, ``format``, content, ``$comment``) and ``x-`` extension keys are left out at
    every level, ``true`` reads as ``{}``, a ``type`` list as its sorted members (one member as that type) and every
    ``$ref`` as its canonical reference key.
    """
    if isinstance(value, bool):
        return {} if value else False

    def schema_value(member: object) -> object:
        return _validation_keywords(member, canonical_ref) if isinstance(member, (dict, bool)) else member

    keywords: dict[str, Any] = {}
    extras = value.get(JsonSchemaObject.__extra_key__)
    for key, item in {**(extras if isinstance(extras, dict) else {}), **value}.items():
        if key in _NON_ASSERTING_KEYWORDS or key == JsonSchemaObject.__extra_key__ or key.startswith("x-"):
            continue
        if key == "$ref" and isinstance(item, str):
            keywords[key] = canonical_ref(item)
        elif key == "type" and isinstance(item, list):
            types = sorted({str(member) for member in item})
            keywords[key] = types[0] if len(types) == 1 else types
        elif key in SCHEMA_VALUE_KEYS:
            keywords[key] = [schema_value(member) for member in item] if isinstance(item, list) else schema_value(item)
        elif key in SCHEMA_MAP_KEYS and isinstance(item, dict):
            keywords[key] = {name: schema_value(member) for name, member in item.items()}
        else:
            keywords[key] = item
    return keywords


_INTERSECTED_KEYWORDS = frozenset({
    "exclusiveMaximum",
    "exclusiveMinimum",
    "enum",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "nullable",
    "pattern",
    "required",
    "type",
    "uniqueItems",
})
_IMPLIED_SUBSCHEMA_KEYWORDS = frozenset({"items", "propertyNames"})
_UNMATCHED_SCHEMA_KEYWORDS = frozenset({
    "additionalItems",
    "additionalProperties",
    "unevaluatedItems",
    "unevaluatedProperties",
})
_PROPERTY_NAME_KEYWORDS = ("properties", "patternProperties")


def _schema_dump(schema: JsonSchemaObject | bool) -> object:  # ruff: ignore[boolean-type-hint-positional-argument]
    """Return the keywords a schema object sets as a dict, or a boolean schema itself."""
    return schema.model_dump(exclude_unset=True, by_alias=True) if isinstance(schema, JsonSchemaObject) else schema


def _schema_keywords(schema: object) -> dict[str, Any] | None:
    """Return the dumped keywords of a schema that assert something (``true`` as ``{}``), or None for ``false``."""
    if schema is True:
        return {}
    if not isinstance(schema, dict):
        return None
    return {key: value for key, value in schema.items() if key not in _ANNOTATION_KEYWORDS}


def _writes_reference(keywords: dict[str, Any]) -> bool:
    """Return whether dumped schema keywords write a reference, whose target depends on the document read."""
    return any(keyword in keywords for keyword in ("$ref", "$dynamicRef", "$recursiveRef"))


def _schema_implies(declared: object, other: object, *, same_document: bool) -> bool:
    """Return whether every value the dumped schema ``declared`` accepts is shown to meet the dumped schema ``other``.

    ``intersect_keywords`` decides the keywords it intersects (bounds, ``multipleOf``, ``uniqueItems``, ``required``,
    ``pattern``, ``type``, null and ``enum``): ``other`` adds nothing there when the intersection is ``declared``'s
    own value. ``items``, ``propertyNames`` and the schemas of ``properties`` and ``patternProperties`` follow this rule
    recursively, and ``additionalProperties`` where both name the same properties and patterns. ``other`` adds
    nothing with any other keyword applied to the values the others leave (``additionalItems``,
    ``unevaluatedItems``, ``unevaluatedProperties``) only when it is ``true``, and with every other keyword only
    when ``declared`` writes the same value. A schema whose own bounds admit no value adds that to ``declared``,
    and a ``$ref`` of ``other`` is the same reference only when both schemas are read from one document
    (``same_document``).
    """
    if (other_keywords := _schema_keywords(other)) == {} or declared is False:
        return True
    if other_keywords is None or (declared_keywords := _schema_keywords(declared)) is None:
        return False
    if (not same_document and _writes_reference(other_keywords)) or (
        bounds_admit_no_value(other_keywords) and not bounds_admit_no_value(declared_keywords)
    ):
        return False
    intersection = intersect_keywords(
        [declared_keywords, other_keywords],
        {**other_keywords, **declared_keywords},
        extras_key=JsonSchemaObject.__extra_key__,
    )
    return (
        not intersection.unsatisfiable
        and not intersection.untaken_patterns
        and intersection.keywords == declared_keywords
        and all(
            _keyword_implies(keyword, declared_keywords, other_keywords, same_document=same_document)
            for keyword in other_keywords
            if keyword not in _INTERSECTED_KEYWORDS
        )
    )


def _keyword_implies(keyword: str, declared: dict[str, Any], other: dict[str, Any], *, same_document: bool) -> bool:
    """Return whether the value of one keyword of ``other``, which ``declared`` also writes, adds no assertion."""
    declared_value = declared[keyword]
    other_value = other[keyword]
    if keyword in SCHEMA_MAP_KEYS and isinstance(other_value, dict):
        return isinstance(declared_value, dict) and all(
            _schema_implies(declared_value[name], schema, same_document=same_document)
            if name in declared_value
            else _schema_keywords(schema) == {}
            for name, schema in other_value.items()
        )
    if keyword in _IMPLIED_SUBSCHEMA_KEYWORDS and not isinstance(other_value, list):
        return _schema_implies(declared_value, other_value, same_document=same_document)
    if keyword in _UNMATCHED_SCHEMA_KEYWORDS:
        return _schema_keywords(other_value) == {} or (
            keyword == "additionalProperties"
            and all(set(declared.get(names) or ()) == set(other.get(names) or ()) for names in _PROPERTY_NAME_KEYWORDS)
            and _schema_implies(declared_value, other_value, same_document=same_document)
        )
    if keyword == JsonSchemaObject.__extra_key__ and isinstance(other_value, dict):
        return isinstance(declared_value, dict) and all(
            key in declared_value and _json_literal_values_equal(declared_value[key], value)
            for key, value in other_value.items()
        )
    return _json_literal_values_equal(declared_value, other_value)


def _searches_any(pattern: str, names: Iterable[str]) -> bool:
    """Return whether ``pattern`` searches one of ``names`` as the generated validator does; an invalid one, none."""
    try:
        return any(re.search(pattern, name) for name in names)
    except re.error:
        return False


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


def _locate_dump(obj: JsonSchemaObject, data: dict[str, Any]) -> dict[str, Any]:
    """Give the dump of a schema object, and the dumps of its schema children, the locations of their sources.

    The correspondence is the object's own structure (field, key, index), never a match across merge inputs.
    """
    fields = obj.__dict__
    for name in obj.model_fields_set.intersection(_LOCATED_CHILD_FIELDS):
        value, dumped = fields[name], data.get(name)
        if isinstance(dumped, dict):
            if isinstance(value, JsonSchemaObject):
                data[name] = _locate_dump(value, dumped)
            else:
                data[name] = {
                    key: _locate_dump(item, sub)
                    if isinstance(item := value.get(key), JsonSchemaObject) and isinstance(sub, dict)
                    else sub
                    for key, sub in dumped.items()
                }
        elif isinstance(dumped, list):
            data[name] = [
                _locate_dump(item, sub) if isinstance(item, JsonSchemaObject) and isinstance(sub, dict) else sub
                for item, sub in zip(value, dumped, strict=True)
            ]
    return _located(data, fields.get(SCHEMA_LOCATION_KEY))


_NUMERIC_TYPE_DOMAINS = {
    "integer": frozenset({"integer"}),
    "number": frozenset({"integer", "number"}),
    "null": frozenset({"null"}),
}


def _json_schema_types(schema_type: str | list[str]) -> list[str]:
    """Return the JSON Schema type keyword as a list."""
    return [schema_type] if isinstance(schema_type, str) else schema_type


def _intersect_json_schema_types(left: list[str], right: list[str]) -> list[str]:
    """Return the JSON Schema types both lists admit, in the order of ``left``, where number admits integer."""
    right_types = {type_ for item in right for type_ in ((item, "integer") if item == "number" else (item,))}
    common = dict.fromkeys(
        type_ for item in left for type_ in (("integer", item) if item == "number" else (item,)) if type_ in right_types
    )
    if "number" in common:
        common.pop("integer", None)
    return list(common)


def _numeric_type_domain(schema_type: str | list[str]) -> frozenset[str] | None:
    """Expand number to its numeric subtypes within the numeric/null domain."""
    if isinstance(schema_type, str):
        return _NUMERIC_TYPE_DOMAINS.get(schema_type)
    types = frozenset(schema_type)
    if not types.issubset(_NUMERIC_TYPE_DOMAINS):
        return None
    return types | _NUMERIC_TYPE_DOMAINS["integer"] if "number" in types else types


def _field_source_name(field: DataModelFieldBase) -> str | None:
    return field.original_name if field.original_name is not None else field.name


def _json_literal_may_accept_container(
    value: Any,
    container_type: Literal["array", "object"],
) -> bool:
    """Return whether a JSON literal has the requested raw container shape."""
    return isinstance(value, list if container_type == "array" else dict)


def _json_schema_type_may_accept_container(
    schema_type: str | list[str],
    container_type: Literal["array", "object"],
) -> bool:
    """Return whether a JSON Schema type keyword permits the raw container shape."""
    return schema_type == container_type if isinstance(schema_type, str) else container_type in schema_type


def _intersect_all_of_enum(parent: list[Any], child: list[Any]) -> list[Any]:
    """Intersect JSON enum values while retaining order and already-correct enum aliases."""
    intersection = intersect_enum_values(parent, child)
    if not intersection or (
        len(intersection) == len(parent) and len(intersect_enum_values(child, parent)) == len(child)
    ):
        return parent + child
    return intersection


def _align_all_of_enum_metadata(parent: dict[str, Any], child: dict[str, Any], result: dict[str, Any]) -> None:
    """Keep annotations attached to their retained enum values, preferring parent annotations."""
    retained_ids = {id(value) for value in result["enum"]}
    indices = [index for index, value in enumerate(parent["enum"]) if id(value) in retained_ids]
    result.update({
        key: [enum_metadata((parent, child), key, index) for index in indices]
        for key in ENUM_METADATA_KEYWORDS
        if parent.get(key) or child.get(key)
    })


def _is_rw_model_variant_path(path: str) -> bool:
    """Return whether a path belongs to an internal Request/Response variant."""
    return any(marker in path for marker in _RW_MODEL_VARIANT_SPECIAL_MARKERS)


def _get_rw_model_variant_source_path(
    base_reference: Reference,
    suffix: Literal["Request", "Response"],
) -> str:
    """Return the legacy logical source path for a generated variant.

    A model built from a member of a document root joins its path to the document with ``/`` (``doc.json/p#...``); its
    source path names the location in that document (``doc.json#/p#...``), as those of the members of other schemas do.
    """
    path = base_reference.path
    if (location := location_of(base_reference)) is not None and path.startswith(prefix := f"{location.document}/"):
        path = f"{location.document}#/{path.removeprefix(prefix)}"
    source_prefix, separator, source_name = path.rpartition("/")
    return f"{source_prefix}{separator}{source_name}{suffix}"


def _get_json_value_type(value: object) -> str:
    """Return a JSON Schema primitive type name for a concrete value."""
    value_type = ""
    match value:
        case bool():
            value_type = "boolean"
        case int():
            value_type = "integer"
        case float():
            value_type = "number"
        case str():
            value_type = "string"
        case list():
            value_type = "array"
        case dict():
            value_type = "object"
        case None:
            value_type = "null"
        case _:
            pass
    return value_type


def _parse_override_annotation(value: str) -> PythonTypeExpr | None:
    """Return the annotation a ``--type-overrides`` value spells, or None when it is no annotation."""
    from datamodel_code_generator._python_type_annotation_codec import (  # noqa: PLC0415
        parse_python_type_annotation,
    )

    return parse_python_type_annotation(value)


def _qualified_python_type_import(qualified_name: str) -> Import:
    return _QUALIFIED_PYTHON_TYPE_IMPORT_ALIASES.get(qualified_name) or Import.from_full_path(qualified_name)


# Keep this in sync with _traverse_schema_objects(). Only schemas that did not
# explicitly receive one of these fields can skip the child traversal below.
_SCHEMA_OBJECT_CHILD_FIELDS = frozenset({
    "items",
    "prefixItems",
    "additionalProperties",
    "unevaluatedProperties",
    "unevaluatedItems",
    "patternProperties",
    "propertyNames",
    "anyOf",
    "allOf",
    "oneOf",
    "properties",
})
_CONDITIONAL_SCHEMA_KEYWORDS = frozenset({"if", "then", "else"})
_LOCATED_CHILD_FIELDS = _SCHEMA_OBJECT_CHILD_FIELDS | {"additionalItems"}


def _get_data_type_constraint_kwargs(
    obj: JsonSchemaObject,
    type_: Types,
) -> dict[str, JsonSchemaConstraintValue]:
    return {
        key: value.value if isinstance(value, UnionIntFloat) else value
        for key in constrained_type_keyword_order(type_.name)
        if (value := getattr(obj, key, None)) is not None
    }


_LITERAL_PARTS: Final = "its values and the constraints beside them"
"""The parts the unsatisfiable warning names for a const or an enum none of whose values meets its constraints."""
_ALL_OF_PARTS: Final = "its allOf members and its own keywords"
"""The parts the unsatisfiable warning names for an allOf site no value satisfies."""
_ALL_OF_MEMBER_ANNOTATIONS: Final = frozenset({"description", "default", "examples", "deprecated"})
"""The annotations an allOf site takes from its members when it does not write them itself."""


def _bare_ref_members(obj: JsonSchemaObject) -> list[str]:
    """Return the references of the ``$ref`` members of an allOf, or none when one of them has sibling keywords.

    A member written with sibling keywords is the site's own inline schema (decision 4), so the site is no reference
    with more assertions.
    """
    members = [item for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref]
    if any(member.has_ref_with_schema_keywords for member in members):
        return []
    return [ref for member in members if (ref := member.ref)]


def _derived_site(site: JsonSchemaObject, refs: Sequence[str]) -> JsonSchemaObject:
    """Mark a site schema built from the parts of its one ``$ref`` target without the reference, and return it.

    The model built from it records that target (``JsonSchemaParser._record_derived_from``), so a model-level type
    override of the target applies to it as well.
    """
    if len(refs) == 1 and not site.ref and not site.allOf:
        site.__dict__[_DERIVED_FROM_REF_KEY] = refs[0]
    return site


def _enum_narrowed_by_part_consts(
    intersection: JsonSchemaObject, parts: Sequence[JsonSchemaObject], *, const_support: bool
) -> JsonSchemaObject:
    """Return the intersection of allOf value parts with its enum narrowed to the const of each part without an enum.

    A dialect without ``const`` (draft-04, OpenAPI 3.0) reads it as an unknown keyword, so nothing narrows there.

    Such a const is a part of its own that a value must equal, as the one-value enum --use-single-value-enum-as-const
    reads as that const is, so it narrows the enum the same way and the enum then stands for it. The intersection is
    returned as it is without such a const, and where no enum value equals every such const (no value meets the parts).
    """
    if (
        not const_support
        or not intersection.enum
        or not (consts := [part.extras["const"] for part in parts if _is_enumless_const(part)])
    ):
        return intersection
    excluded = {
        position
        for position, value in enumerate(intersection.enum)
        if not all(_json_literal_values_equal(const, value) for const in consts)
    }
    if not excluded or len(excluded) == len(intersection.enum):
        return intersection
    return _enum_without_positions(intersection, excluded).model_copy(
        update={"extras": {key: value for key, value in intersection.extras.items() if key != "const"}}
    )


def _is_enumless_const(part: JsonSchemaObject) -> bool:
    """Return whether a value part holds a const and no enum."""
    return "const" in part.extras and not part.enum


def _enum_without_positions(obj: JsonSchemaObject, excluded: Collection[int]) -> JsonSchemaObject:
    """Return a copy of an enum schema without the values at the ``excluded`` positions, with their x-enum entries.

    Each entry stays with its value, matched as ``parse_enum`` matches them.
    """
    skip_null = _enum_entries_skip_null(obj)
    entry_positions = (position for position, value in enumerate(obj.enum) if value is not None or not skip_null)
    dropped = {index for index, position in enumerate(entry_positions) if position in excluded}
    return obj.model_copy(
        update={
            "enum": [value for position, value in enumerate(obj.enum) if position not in excluded],
            **{
                field: [entry for index, entry in enumerate(entries) if index not in dropped]
                for field in _ENUM_ENTRY_FIELDS
                if (entries := getattr(obj, field))
            },
        }
    )


def _keep_values_as_written(obj: JsonSchemaObject, values: list[Any]) -> JsonSchemaObject:
    """Keep on a schema copy that holds only some enum values those as written, unless it keeps earlier ones."""
    obj.__dict__.setdefault(VALUES_AS_WRITTEN_KEY, values)
    return obj


def _literal_constraints(obj: JsonSchemaObject, written: frozenset[str] = NO_KEYWORDS) -> dict[str, float | str]:
    """Return the limits of the keywords a literal check decides that a schema sets and its field does not write."""
    return {
        key: value.value if isinstance(value, UnionIntFloat) else value
        for key in obj.__constraint_field_order__
        if key in LITERAL_DECIDABLE_KEYWORDS and key not in written and (value := getattr(obj, key)) is not None
    }


def _literal_met(
    obj: JsonSchemaObject, values: Sequence[object], written: frozenset[str]
) -> tuple[tuple[str, ...], tuple[bool, ...], tuple[bool, ...]]:
    """Return the keywords of a schema a literal check decides on values, and whether each value meets them.

    The second result checks every keyword, the third only those the field leaves out (all but ``written``).
    """
    decided, met = literal_check(values, _literal_constraints(obj))
    return decided, met, literal_check(values, _literal_constraints(obj, written))[1] if written else met


def _carried_constraint_keywords(data_type: DataType) -> frozenset[str]:
    """Return the keywords that a type, or a member of a union it is, consumed itself, which its field leaves out."""
    if data_type.is_list or data_type.is_dict or data_type.is_tuple or data_type.is_set or data_type.is_frozen_set:
        return data_type._consumed_constraints  # noqa: SLF001
    return data_type._consumed_constraints.union(  # noqa: SLF001
        *(member._consumed_constraints for member in data_type.data_types)  # noqa: SLF001
    )


def _enforced_constraint_keywords(
    data_type: DataType, zero_bound_classes: Mapping[str, tuple[str, ...]], constraints: Mapping[str, float | str]
) -> frozenset[str]:
    """Return the keywords of a schema that the type of its field enforces itself.

    ``--omit-redundant-constraints`` leaves them out of the field. A zero-bound class enforces its bound
    (``zero_bound_classes``), the ``Literal`` of a const or an enum every keyword of ``constraints`` that all its
    values meet (``_literal_meets``), and a union the keywords every member but ``None`` enforces; a container
    enforces none of its own.
    """
    if data_type.is_list or data_type.is_dict or data_type.is_tuple or data_type.is_set or data_type.is_frozen_set:
        return NO_KEYWORDS
    if members := [member for member in data_type.data_types if member.type != NONE or member.data_types]:
        return frozenset.intersection(
            *(_enforced_constraint_keywords(member, zero_bound_classes, constraints) for member in members)
        )
    if _is_value_literal(data_type):
        return _literal_meets(data_type.literals, constraints)
    return frozenset(zero_bound_classes.get(data_type.type, ())) if data_type.type else NO_KEYWORDS


def _literal_meets(values: Sequence[object], constraints: Mapping[str, float | str]) -> frozenset[str]:
    """Return the keywords of ``constraints`` that the literal check decides and every one of the values meets.

    The values of a literal no value of which meets a keyword stay as written (the unsatisfiable warning), so the
    literal does not enforce that keyword.
    """
    decided, met = literal_check(values, constraints)
    if all(met):
        return frozenset(decided)
    return frozenset(key for key in decided if all(literal_check(values, {key: constraints[key]})[1]))


def _imported_type_paths(data_type: DataType) -> Iterator[str]:
    """Yield the path of every type that a type and its members import, as a type or inside an annotation."""
    for nested in data_type.all_data_types:
        for import_ in (nested.import_, *(nested.python_type.imports if nested.python_type else ())):
            if import_ is not None and import_.from_:
                yield f"{import_.from_}.{import_.import_}"


def _get_discriminator_property_name(obj: JsonSchemaObject) -> str | None:
    """Return the discriminator property name from either JSON Schema or OpenAPI shape."""
    discriminator = obj.discriminator
    if isinstance(discriminator, Discriminator):
        return discriminator.propertyName
    if isinstance(discriminator, str):
        return discriminator
    return None


@final
@dataclass(frozen=True, slots=True)
class _SchemaTagFacts:
    """The facts of a property schema that ``field_tag`` reads: its ``const``, its ``enum`` and its ``$ref``."""

    parser: JsonSchemaParser
    obj: JsonSchemaObject
    seen_refs: set[_TargetNode] | None

    def has_const(self) -> bool:
        """Return whether the schema has a ``const``, null included."""
        return "const" in self.obj.extras

    def const(self) -> Any:
        """Return the schema's ``const``."""
        return self.obj.extras["const"]

    def literals(self) -> tuple[()]:  # noqa: PLR6301
        """Return no ``Literal`` values: a schema has no ``Literal`` type."""
        return ()

    def enum_member(self) -> tuple[int, Any]:
        """Return how many values the schema's ``enum`` holds, and the first one."""
        return len(self.obj.enum), self.obj.enum[0] if self.obj.enum else None

    def referenced_tag(self) -> FieldTag | None:
        """Return the tag of the schema the ``$ref`` names."""
        value = self.parser._get_referenced_single_literal_value(self.obj, self.seen_refs)  # noqa: SLF001
        return None if value is None else FieldTag(value, "referenced-schema")


def _get_union_variant_name(name: str, literal: JsonSchemaLiteral) -> str | None:
    module_name, separator, class_name = name.rpartition(".")
    if isinstance(literal, str):
        literal_text = literal
    elif isinstance(literal, bool):
        literal_text = f"bool_{str(literal).lower()}"
    else:
        literal_text = f"int_{literal}"
    literal_name = sanitize_module_name(literal_text, treat_dot_as_module=False)
    if not literal_name:
        return None
    variant_name = f"{class_name or name}_{literal_name}"
    return f"{module_name}{separator}{variant_name}" if module_name else variant_name


_ENUM_ENTRY_FIELDS = ("x_enum_varnames", "x_enum_names", "x_enum_descriptions")


def _enum_entries_skip_null(obj: JsonSchemaObject) -> bool:
    """Return whether the x-enum entries of a schema are matched to its enum values without null.

    The class of a string or nullable enum leaves out the null the enum lists, and its entries skip it too.
    """
    return resolve_null_facts(_type_null_sources(obj), "enum").rule == "enum-null-as-nullable"


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
        f"{class_name} keeps the shifted member {' and '.join(lists.values())} of 0.83.0: {' and '.join(lists)} "
        f"{'give' if len(lists) > 1 else 'gives'} null an entry, but the entries are matched to the values without "
        f"null, so {value!r} is {kind} {taken!r} instead of {own!r}. Leave the entry of null out of the list."
    )


def __getattr__(name: str) -> Any:
    """Return compatibility model classes without importing them on parser load."""
    match name:
        case "TypedDictModel":
            from datamodel_code_generator.model.typed_dict import TypedDict as TypedDictModel  # noqa: PLC0415

            return TypedDictModel
    raise AttributeError(name)


def unescape_json_pointer_segment(segment: str) -> str:
    """Decode one URI-fragment JSON Pointer token in RFC evaluation order."""
    return unquote(segment).replace("~1", "/").replace("~0", "~")


_JSON_POINTER_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")
_MISSING_JSON_POINTER = Sentinel("_MISSING_JSON_POINTER")


class _JSONPointerArrayIndexOutOfRangeError(Error):
    """Identify a syntactically valid JSON pointer index beyond an array's bounds."""


def _resolve_json_pointer_array_index(sequence: list[YamlValue], segment: object) -> YamlValue:
    """Resolve a JSON-pointer segment against a list per the RFC 6901 array-index grammar."""
    text = str(segment)
    if not _JSON_POINTER_ARRAY_INDEX.fullmatch(text):
        msg = f"Invalid JSON pointer array index {text!r}: expected a non-negative integer."
        raise Error(msg)
    try:
        index = int(text)
    except ValueError as exc:
        msg = f"Invalid JSON pointer array index {text!r}: integer string is too long to parse."
        raise Error(msg) from exc
    if index >= len(sequence):
        msg = f"JSON pointer array index {index} is out of range (array length {len(sequence)})."
        raise _JSONPointerArrayIndexOutOfRangeError(msg)
    return sequence[index]


def _resolve_json_pointer_array_index_or_missing(
    sequence: list[YamlValue],
    segment: object,
) -> YamlValue | object:
    """Resolve an array index while marking only an unavailable valid index as missing."""
    try:
        return _resolve_json_pointer_array_index(sequence, segment)
    except _JSONPointerArrayIndexOutOfRangeError:
        return _MISSING_JSON_POINTER


def get_model_by_path(schema: dict[str, YamlValue] | list[YamlValue], keys: list[str] | list[int]) -> YamlValue:
    """Retrieve a model from schema by traversing raw JSON Pointer keys."""
    return _get_model_by_decoded_path(
        schema, [unescape_json_pointer_segment(key) if isinstance(key, str) else key for key in keys]
    )


def _get_model_by_decoded_path(schema: dict[str, YamlValue] | list[YamlValue], keys: Sequence[str | int]) -> YamlValue:
    """Retrieve a model after JSON Pointer tokens have already been decoded."""
    if not keys:
        if isinstance(schema, dict):
            return schema
        msg = f"Does not support json pointer to array. schema={schema}, key={keys}"  # pragma: no cover
        raise NotImplementedError(msg)  # pragma: no cover
    key = keys[0]
    if isinstance(schema, dict):
        value = schema.get(str(key), {})
    elif isinstance(schema, list):
        value = _resolve_json_pointer_array_index(schema, key)
    else:
        msg = f"Cannot traverse non-container schema. schema={schema}, key={key}"
        raise NotImplementedError(msg)
    if len(keys) == 1:
        return value
    if isinstance(value, (dict, list)):
        return _get_model_by_decoded_path(value, keys[1:])
    msg = f"Cannot traverse non-container value. schema={schema}, key={keys}"  # pragma: no cover
    raise NotImplementedError(msg)  # pragma: no cover


def _get_model_by_path_or_missing(
    schema: dict[str, YamlValue] | list[YamlValue],
    keys: list[str],
) -> YamlValue | object:
    """Resolve decoded JSON Pointer keys with one lookup per segment and a missing sentinel."""
    current: YamlValue = schema
    last_index = len(keys) - 1
    for index, key in enumerate(keys):
        if isinstance(current, dict):
            value = current.get(key, _MISSING_JSON_POINTER)
            if value is _MISSING_JSON_POINTER:
                return value
        elif isinstance(current, list):
            if (value := _resolve_json_pointer_array_index_or_missing(current, key)) is _MISSING_JSON_POINTER:
                return value
        else:  # pragma: no cover - guarded before assigning current
            raise TypeError(type(current))
        if index == last_index:
            return value
        if not isinstance(value, (dict, list)):
            msg = f"Cannot traverse non-container value. schema={current}, key={keys[index:]}"  # pragma: no cover
            raise NotImplementedError(msg)  # pragma: no cover
        current = value
    raise AssertionError  # pragma: no cover


def _reserved_value(tree: dict[str, YamlValue], key: str) -> YamlValue | object:
    """Return the value a reserved reference key names in a document tree, as parsing it reads that value."""
    object_paths = _split_json_pointer(tree, key.split("#", 1)[-1])[0]
    return _get_model_by_path_or_missing(tree, object_paths) if object_paths else tree


def split_json_pointer(schema: dict[str, YamlValue] | list[YamlValue], pointer: str) -> list[str]:
    """Split a JSON pointer into path parts, preserving slash-containing dict keys."""
    return _split_json_pointer(schema, pointer)[0]


def _split_json_pointer(schema: dict[str, YamlValue] | list[YamlValue], pointer: str) -> tuple[list[str], list[str]]:
    """Split a JSON pointer into lookup and reference path parts."""
    raw_parts = pointer.lstrip("/").split("/") if pointer else []
    if "://" not in pointer and "~1" not in pointer:
        return [unescape_json_pointer_segment(part) for part in raw_parts], raw_parts

    parts: list[str] = []
    reference_parts: list[str] = []
    current: YamlValue = schema
    index = 0
    while index < len(raw_parts):
        if isinstance(current, dict):
            direct_key = unescape_json_pointer_segment(raw_parts[index])
            if direct_key in current:
                parts.append(direct_key)
                reference_parts.append(raw_parts[index])
                current = current.get(direct_key, {})
                index += 1
                continue

            matched_key: str | None = None
            matched_end = index
            for end in range(len(raw_parts), index, -1):
                key = unescape_json_pointer_segment("/".join(raw_parts[index:end]))
                if key in current:
                    matched_key = key
                    matched_end = end
                    break
            if matched_key is None:  # pragma: no cover
                matched_key = unescape_json_pointer_segment(raw_parts[index])
                matched_end = index + 1
            parts.append(matched_key)
            reference_parts.append("/".join(raw_parts[index:matched_end]))
            current = current.get(matched_key, {})
            index = matched_end
            continue
        part = unescape_json_pointer_segment(raw_parts[index])
        parts.append(part)
        reference_parts.append(raw_parts[index])
        if isinstance(current, list):  # pragma: no branch
            if (resolved := _resolve_json_pointer_array_index_or_missing(current, part)) is _MISSING_JSON_POINTER:
                parts.extend(unescape_json_pointer_segment(part) for part in raw_parts[index + 1 :])
                reference_parts.extend(raw_parts[index + 1 :])
                return parts, reference_parts
            current = cast("YamlValue", resolved)
        index += 1
    return parts, reference_parts


_JSON_SCHEMA_MAP_KEYWORDS = frozenset({
    "$defs",
    "definitions",
    "dependencies",
    "dependentSchemas",
    "properties",
    "patternProperties",
})
_JSON_SCHEMA_SEQUENCE_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_JSON_SCHEMA_SINGLE_KEYWORDS = frozenset({
    "additionalItems",
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})
_JSON_SCHEMA_SINGLE_OR_SEQUENCE_KEYWORDS = frozenset({"extends", "items"})
_NON_SCHEMA_KEYWORDS = frozenset({
    "const",
    "default",
    "enum",
    "example",
    "examples",
    "links",
    "security",
    "server",
    "serverVariables",
    "servers",
})
_NAMED_ENTRY_MAP_KEYWORDS = _JSON_SCHEMA_MAP_KEYWORDS | {
    "callbacks",
    "channels",
    "content",
    "encoding",
    "messages",
    "operations",
    "parameters",
    "responses",
    "webhooks",
}
_DocumentEntries = Literal["object", "names", "maps"]


def _iter_ref_walk_children(data: dict[str, Any]) -> Iterator[dict[str, Any] | list[Any]]:
    """Yield the subschemas of a raw schema in keyword order, leaving instance values and metadata out."""
    for keyword, value in data.items():
        if keyword in _JSON_SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            yield from (schema for schema in value.values() if isinstance(schema, dict))
        elif (
            keyword in _JSON_SCHEMA_SINGLE_KEYWORDS
            or keyword in _JSON_SCHEMA_SEQUENCE_KEYWORDS
            or keyword in _JSON_SCHEMA_SINGLE_OR_SEQUENCE_KEYWORDS
        ) and isinstance(value, (dict, list)):
            yield value


def _document_child_entries(
    entries: _DocumentEntries, key: object, named_entry_maps: frozenset[str]
) -> _DocumentEntries | None:
    """Return how the value under a key of a document object is read, or None when it holds no schemas."""
    if entries == "names":
        return "object"
    if key in _NON_SCHEMA_KEYWORDS or (isinstance(key, str) and key.startswith("x-") and key != "x-propertyNames"):
        return None
    child: _DocumentEntries = "object"
    match entries, key:
        case "maps", _:
            child = "names"
        case "object", "components":
            child = "maps"
        case "object", str() if key in named_entry_maps:
            child = "names"
    return child


_RefEdgeEntries = Literal["root", "object", "names", "maps"]
_REF_EDGE_NAME_MAPS = frozenset({"bindings", "paths"})
_REF_EDGE_KEYWORDS = (
    _JSON_SCHEMA_MAP_KEYWORDS
    | _JSON_SCHEMA_SEQUENCE_KEYWORDS
    | _JSON_SCHEMA_SINGLE_KEYWORDS
    | _JSON_SCHEMA_SINGLE_OR_SEQUENCE_KEYWORDS
    | _NAMED_ENTRY_MAP_KEYWORDS
    | _REF_EDGE_NAME_MAPS
    | {
        "additionalOperations",
        "channel",
        "components",
        "delete",
        "get",
        "head",
        "headers",
        "message",
        "messageTraits",
        "operationTraits",
        "options",
        "patch",
        "payload",
        "pathItems",
        "post",
        "publish",
        "put",
        "query",
        "reply",
        "requestBodies",
        "requestBody",
        "schema",
        "schemas",
        "itemSchema",
        "subscribe",
        "trace",
        "traits",
        "x-propertyNames",
    }
)
"""The keywords of a schema, path item, operation or message object under which another such object is read."""


def _ref_edge_child_entries(
    entries: _RefEdgeEntries, key: object, named_entry_maps: frozenset[str]
) -> _DocumentEntries | None:
    """Return how the value under a key of a document object is read for `$ref` edges, or None for data.

    Below the root, only the keywords that hold schemas or the objects around them are read; names of OAuth scopes,
    discriminator mappings and unknown keywords are data, so a `$ref` key there is not a reference. The keys of the
    root are read as written, since a referenced document names its schemas there.
    """
    if entries == "object" and key not in _REF_EDGE_KEYWORDS:
        return None
    read: _DocumentEntries = "object" if entries == "root" else entries
    return _document_child_entries(read, key, named_entry_maps | _REF_EDGE_NAME_MAPS)


def _single_value_enum_as_const(
    value: YamlValue, named_entry_maps: frozenset[str], entries: _DocumentEntries = "object"
) -> YamlValue:
    """Return a document with the enum of each schema that lists one value written as the const it equals.

    The values of instance data keywords, extensions other than x-propertyNames, links, security requirements and
    servers hold no schemas and stay as written. Those words are names, not keywords, in maps of named entries such
    as properties, $defs, responses, webhooks, channels or the component maps. A schema with nullable true beside a
    value other than null keeps its enum, since a const does not carry nullable. Each schema read so that holds a
    const is marked, so that the const branches the option reads are known wherever the parser copies them to.
    Unchanged parts are shared, so cached documents stay intact.
    """
    if isinstance(value, list):
        items = [_single_value_enum_as_const(item, named_entry_maps) for item in value]
        return items if any(map(is_not, items, value)) else value
    if not isinstance(value, dict):
        return value
    changed = {
        key: updated
        for key, child in value.items()
        if (child_entries := _document_child_entries(entries, key, named_entry_maps)) is not None
        and (updated := _single_value_enum_as_const(child, named_entry_maps, child_entries)) is not child
    }
    result = {**value, **changed} if changed else value
    if entries != "object":
        return result
    if (
        isinstance(enum := value.get("enum"), list)
        and len(enum) == 1
        and "const" not in value
        and (enum[0] is None or value.get("nullable") is not True)
    ):
        result = {
            ("const" if key == "enum" else key): enum[0] if key == "enum" else item for key, item in result.items()
        }
    return {**result, _READ_AS_CONST_KEY: True} if "const" in result else result


def _lists_null(type_: str | list[str] | None) -> bool:
    """Return whether a ``type`` keyword is a list that holds null."""
    return isinstance(type_, list) and "null" in type_


def _read_type(obj: JsonSchemaObject) -> str | list[str] | None:
    """Return the ``type`` of a schema object, as a list holding null where OpenAPI reads ``nullable`` so.

    ``JsonSchemaParser._get_data_type`` marks the schema objects whose ``nullable`` it reads as a ``type`` list
    (``parser/_null_facts.py``, row ``legacy-nullable-as-type-list``).
    """
    if obj.__dict__.get(_NULLABLE_TYPE_LIST_KEY) and isinstance(obj.type, str):
        return [obj.type, "null"]
    return obj.type


def _null_sources(
    obj: JsonSchemaObject, site: NullSite, type_has_null: Callable[[JsonSchemaObject], bool]
) -> NullSources:
    """Return the keywords of a schema object that can admit null, as written (``parser/_null_facts.py``).

    Only the sites that read it ask ``type_has_null`` (``JsonSchemaParser._type_has_null``), which records its first
    answer.
    """
    sources = _type_null_sources(obj)
    return replace(sources, type_has_null=type_has_null(obj)) if site in _RECORDED_TYPE_NULL_SITES else sources


def _type_null_sources(obj: JsonSchemaObject) -> NullSources:
    """Return the null sources of a schema object without the answer ``JsonSchemaObject.type_has_null`` recorded."""
    type_ = _read_type(obj)
    is_list = isinstance(type_, list)
    return NullSources(
        type_null=type_ == "null",
        type_list=is_list,
        type_lists_null=is_list and "null" in type_,
        type_lists_object=is_list and "object" in type_,
        typed=type_ is not None,
        type_string=type_ == "string",
        member_null=any(
            isinstance(item, JsonSchemaObject) and (item.type == "null" or _lists_null(item.type))
            for item in (*obj.oneOf, *obj.anyOf)
        ),
        nullable=obj.nullable,
        copied_nullable=bool(obj.__dict__.get(_COPIED_NULLABLE_KEY)),
        enum_null=None in obj.enum,
    )


def _excluded_null_members(
    obj: JsonSchemaObject, members: Sequence[JsonSchemaObject | bool], mode: NullMode
) -> list[JsonSchemaObject]:
    """Return the members typed only null that the ``type`` beside them excludes (row ``type-excludes-member-null``).

    None of them when every member is one: the combined schema keeps them rather than admit no value. None of them
    without --intersect-allof-keywords (``mode``).
    """
    if resolve_null_facts(_type_null_sources(obj), "schema-type", mode).rule != "type-excludes-member-null":
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


def _non_null_types(type_: str | list[str] | None) -> str | list[str] | None:
    """Return a ``type`` keyword without null in its list."""
    return [item for item in type_ if item != "null"] if isinstance(type_, list) else type_


def _admits_arrays(type_: object) -> bool:
    """Return whether a ``type`` keyword names arrays."""
    return type_ == "array" or (isinstance(type_, list) and "array" in type_)


def _is_object_only_type(type_: str | list[str] | None) -> bool:
    """Return whether a schema type permits only object values."""
    match type_:
        case None | "object" | ["object"]:
            return True
    return False


def _nullable_map_value_schema(obj: JsonSchemaObject, *, beside_other_types: bool) -> JsonSchemaObject | None:
    """Return the additionalProperties schema of a type array that lists object, beside other types only if asked."""
    return (
        obj.additionalProperties
        if isinstance(obj.type, list)
        and "object" in obj.type
        and (beside_other_types or not obj.has_multiple_types)
        and isinstance(obj.additionalProperties, JsonSchemaObject)
        and "const" not in obj.extras
        else None
    )


def _object_map_other_types(obj: JsonSchemaObject) -> list[str] | None:
    """Return the type array of an object map whose types other than object and null join the dict in a union."""
    match obj.type:
        case list() as types if "object" in types and not set(types) <= {"object", "null"}:
            return types
    return None


def _with_null(data_type: DataType) -> DataType:
    """Return a type equal to ``data_type``, which this parse has just built and nothing holds yet, admitting null.

    The constructor builds it, so the types nested in it take it as their parent.
    """
    fields = {
        name: getattr(data_type, name) for name in type(data_type).model_fields if name not in {"parent", "children"}
    }
    return type(data_type)(**{**fields, "is_optional": True})


def _is_plain_any(data_type: DataType) -> bool:
    """Return whether a type admits any value and nothing else qualifies it."""
    return (
        data_type.type == ANY
        and not data_type.is_optional
        and not data_type.data_types
        and data_type.reference is None
        and data_type.dict_key is None
    )


def _schema_assertions(schema: JsonSchemaObject) -> dict[str, Any]:
    """Return a schema without the keywords that only annotate it, to compare it with another; ``true`` is ``{}``."""
    annotations = JsonSchemaObject.__metadata_only_fields__
    assertions = {key: value for key, value in schema.model_dump(exclude_unset=True).items() if key not in annotations}
    if extras := {name: item for name, item in assertions.pop("extras", {}).items() if name not in annotations}:
        assertions["extras"] = extras
    return assertions


def _find_json_schema_anchor_pointer(schema: YamlValue, anchor: str) -> str | None:
    """Return the JSON pointer for an anchor within one schema document."""
    pending: list[tuple[YamlValue, tuple[str, ...]]] = [(schema, ())]
    while pending:
        value, path = pending.pop()
        if not isinstance(value, dict):
            continue
        # An embedded $id starts a distinct schema resource. This search is scoped
        # to the resource supplied as ``schema``, so its anchors must not leak into
        # the parent resource's anchor namespace.
        if path and "$id" in value:
            continue
        if value.get("$anchor") == anchor:
            return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in path)
        for keyword, child in value.items():
            match keyword:
                case _ if keyword in _JSON_SCHEMA_MAP_KEYWORDS and isinstance(child, dict):
                    pending.extend(
                        (subschema, (*path, keyword, str(name)))
                        for name, subschema in child.items()
                        if isinstance(subschema, dict)
                    )
                case _ if keyword in _JSON_SCHEMA_SEQUENCE_KEYWORDS and isinstance(child, list):
                    pending.extend(
                        (subschema, (*path, keyword, str(index)))
                        for index, subschema in enumerate(child)
                        if isinstance(subschema, dict)
                    )
                case _ if keyword in _JSON_SCHEMA_SINGLE_KEYWORDS and isinstance(child, dict):
                    pending.append((child, (*path, keyword)))
                case _ if keyword in _JSON_SCHEMA_SINGLE_OR_SEQUENCE_KEYWORDS:
                    if isinstance(child, dict):
                        pending.append((child, (*path, keyword)))
                    elif isinstance(child, list):
                        pending.extend(
                            (subschema, (*path, keyword, str(index)))
                            for index, subschema in enumerate(child)
                            if isinstance(subschema, dict)
                        )
    return None


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


class JSONReference(_enum.Enum):
    """Define types of JSON references."""

    LOCAL = "LOCAL"
    REMOTE = "REMOTE"
    URL = "URL"


class Discriminator(BaseModel):
    """Represent OpenAPI discriminator object.

    This is an OpenAPI-specific concept for supporting polymorphism.
    It identifies which schema applies based on a property value.
    Kept in jsonschema.py to avoid circular imports with openapi.py.
    """

    propertyName: str  # noqa: N815
    mapping: Optional[dict[str, str]] = None  # noqa: UP045


_IN_PLACE_APPLICATOR_KEYWORDS: Final = frozenset({
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "dependentSchemas",
    "dependencies",
    "$ref",
    "$dynamicRef",
    "$recursiveRef",
})
READ_UNEVALUATED_PROPERTIES_CONTEXT: Final = "read_unevaluated_properties"


_PROPERTY_EVALUATING_KEYWORDS: Final = frozenset({
    *_IN_PLACE_APPLICATOR_KEYWORDS,
    "then",
    "else",
    "properties",
    "patternProperties",
    "additionalProperties",
    "unevaluatedProperties",
})


def _is_document_object(values: object) -> TypeIs[dict[str, Any]]:
    """Return whether ``values`` is a JSON object of a document the running parse loaded, not a dump or a merge."""
    return isinstance(values, dict) and (graph := SCHEMA_GRAPH.get()) is not None and id(values) in graph.locations


def _evaluates_no_property(schema: object) -> bool:
    """Return whether a raw subschema evaluates no property of the object it applies to."""
    return isinstance(schema, bool) or (isinstance(schema, dict) and _PROPERTY_EVALUATING_KEYWORDS.isdisjoint(schema))


def _applicator_evaluates_no_property(keyword: str, value: object) -> bool:
    """Return whether the in-place applicator ``keyword`` with its raw ``value`` evaluates no property.

    ``not`` never evaluates a property: the annotations of a failed subschema do not count. Only ``allOf``,
    ``anyOf`` and ``oneOf`` whose subschemas evaluate none qualify besides; any other applicator may.
    """
    match keyword:
        case "allOf" | "anyOf" | "oneOf":
            return isinstance(value, list) and all(_evaluates_no_property(item) for item in value)
        case "not":
            return True
    return False


class JsonSchemaObject(BaseModel):  # noqa: PLR0904
    """Represent a JSON Schema object with validation and parsing capabilities."""

    if not TYPE_CHECKING:  # pragma: no branch

        @classmethod
        def get_fields(cls) -> dict[str, Any]:
            """Get fields for Pydantic v2 models."""
            return cls.model_fields

    __constraint_fields__: set[str] = {  # noqa: RUF012
        "exclusiveMinimum",
        "minimum",
        "exclusiveMaximum",
        "maximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "uniqueItems",
    }
    __constraint_field_order__: ClassVar[tuple[str, ...]] = (
        "exclusiveMinimum",
        "minimum",
        "exclusiveMaximum",
        "maximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "uniqueItems",
    )
    __extra_key__: str = SPECIAL_PATH_FORMAT.format("extras")
    __metadata_only_fields__: set[str] = {  # noqa: RUF012
        "title",
        "description",
        "id",
        "$id",
        "$anchor",
        "$schema",
        "$comment",
        "examples",
        "example",
        "x_enum_varnames",
        "x_enum_descriptions",
        "x_enum_field_as_literal",
        "read_as_const",
        "definitions",
        "$defs",
        "default",
        "readOnly",
        "writeOnly",
        "deprecated",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "externalDocs",
        "xml",
        "$recursiveRef",
        "recursiveRef",
        "$recursiveAnchor",
        "recursiveAnchor",
        "$dynamicRef",
        "dynamicRef",
        "$dynamicAnchor",
        "dynamicAnchor",
    }

    __schema_affecting_extras__: set[str] = {  # noqa: RUF012
        "const",
    }

    @model_validator(mode="before")
    @classmethod
    def validate_exclusive_maximum_and_exclusive_minimum(cls, values: Any) -> Any:
        """Validate and convert boolean exclusive maximum and minimum to numeric values."""
        if not isinstance(values, dict):
            return values
        exclusive_maximum: int | float | bool | None = values.get("exclusiveMaximum")
        exclusive_minimum: int | float | bool | None = values.get("exclusiveMinimum")
        if not isinstance(exclusive_maximum, bool) and not isinstance(exclusive_minimum, bool):
            return values

        values = dict(values)
        match exclusive_maximum:
            case True:
                if "maximum" in values:
                    values["exclusiveMaximum"] = values.pop("maximum")
                else:
                    del values["exclusiveMaximum"]
            case False:
                del values["exclusiveMaximum"]
        match exclusive_minimum:
            case True:
                if "minimum" in values:
                    values["exclusiveMinimum"] = values.pop("minimum")
                else:
                    del values["exclusiveMinimum"]
            case False:
                del values["exclusiveMinimum"]
        return values

    @model_validator(mode="before")
    @classmethod
    def collect_extra_fields(cls, values: Any) -> Any:
        """Collect raw schema extension fields without overriding known schema fields."""
        if not isinstance(values, dict):
            return values
        alias_extras = values.get(cls.__extra_key__, {})
        raw_extras = {k: v for k, v in values.items() if k not in EXCLUDE_FIELD_KEYS}
        if (overrides := getattr(values.get("allOf"), "_python_field_overrides", None)) is not None:
            raw_extras[_PYTHON_FIELD_OVERRIDES_KEY] = overrides
        if not alias_extras and not raw_extras:
            return values
        extras = {**alias_extras, **raw_extras}
        if "const" in alias_extras:  # pragma: no cover
            extras["const"] = alias_extras["const"]
        return {**values, cls.__extra_key__: extras}

    @model_validator(mode="before")
    @classmethod
    def read_unevaluated_properties_as_additional(cls, values: Any, info: ValidationInfo) -> Any:
        """Read a schema-valued ``unevaluatedProperties`` as ``additionalProperties`` when nothing applies in place.

        Only under ``--read-unevaluated-properties``, which the parser passes in the validation context; nested schema
        objects are validated with the same context. The schema object of the document decides, as written: an
        applicator that evaluates no property leaves the keys ``unevaluatedProperties`` sees those of
        ``additionalProperties``. A dump or a merge the parser validates again keeps that decision, also where it
        drops the applicators. Defined last, it runs before the other validators and sees the document object. A
        value that is ``false`` written as an allOf (``_is_false_all_of``), of a schema object without properties, is
        read as ``false``, which stays where it is.
        """
        if (
            not (info.context or {}).get(READ_UNEVALUATED_PROPERTIES_CONTEXT)
            or not _is_document_object(values)
            or not isinstance(unevaluated_properties := values.get("unevaluatedProperties"), dict)
            or "additionalProperties" in values
            or not all(
                _applicator_evaluates_no_property(keyword, values[keyword])
                for keyword in _IN_PLACE_APPLICATOR_KEYWORDS.intersection(values)
            )
        ):
            return values
        if not values.get("properties") and _is_false_all_of(unevaluated_properties):
            return {**values, "unevaluatedProperties": False}
        return {**values, "additionalProperties": unevaluated_properties}

    @property
    def python_field_overrides(self) -> Mapping[str, str]:
        """Read only converter-owned replacement metadata."""
        if (annotation := self.extras.get(_PYTHON_FIELD_OVERRIDES_KEY)) is None:
            return {}
        from datamodel_code_generator.input_model_result import PythonFieldOverrides  # noqa: PLC0415

        return annotation.fields if isinstance(annotation, PythonFieldOverrides) else {}

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: Any) -> Any:
        """Validate and normalize $ref values (``canonical_ref_spelling``)."""
        return canonical_ref_spelling(value) if isinstance(value, str) else value

    @field_validator("ref", "dynamicRef", mode="wrap")
    @classmethod
    def _keep_resolved_ref(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
        """Keep the edge of a reference keyword of a loaded document, also when validation normalizes its value."""
        return keep_resolved_ref(value, handler(value))

    @field_validator("required", mode="before")
    @classmethod
    def validate_required(cls, value: Any) -> Any:
        """Validate and normalize required field values."""
        if value is None:
            return []
        if isinstance(value, list):  # pragma: no branch  # noqa: PLR1702
            # Filter to only include valid strings, excluding invalid objects
            required_fields: list[str] = []
            for item in value:
                if isinstance(item, str):
                    required_fields.append(item)

                # In some cases, the required field can include "anyOf", "oneOf", or "allOf" as a dict (#2297)
                elif isinstance(item, dict):  # pragma: no branch
                    for key, val in item.items():
                        if isinstance(val, list):  # pragma: no branch
                            # If 'anyOf' or "oneOf" is present, we won't include it in required fields
                            if key in {"anyOf", "oneOf"}:
                                continue

                            if key == "allOf":  # pragma: no branch
                                # If 'allOf' is present, we include them as required fields
                                required_fields.extend(sub_item for sub_item in val if isinstance(sub_item, str))

            value = required_fields

        return value

    @field_validator("type", mode="before")
    @classmethod
    def validate_null_type(cls, value: Any) -> Any:
        """Validate and convert unquoted null type to string "null"."""
        # TODO[openapi]: This should be supported only for OpenAPI 3.1+
        # See: https://github.com/koxudaxi/datamodel-code-generator/issues/2477#issuecomment-3192480591
        if value is None:
            value = "null"
        if isinstance(value, list) and None in value:
            value = [v if v is not None else "null" for v in value]
        return value

    items: Optional[Union[list[Union[JsonSchemaObject, bool]], JsonSchemaObject, bool]] = None  # noqa: UP007, UP045
    additionalItems: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    prefixItems: Optional[list[Union[JsonSchemaObject, bool]]] = None  # noqa: N815, UP007, UP045
    uniqueItems: Optional[bool] = None  # noqa: N815, UP045
    type: Optional[Union[str, list[str]]] = None  # noqa: UP007, UP045
    format: Optional[str] = None  # noqa: UP045
    pattern: Optional[str] = None  # noqa: UP045
    minLength: Optional[int] = None  # noqa:  N815,UP045
    maxLength: Optional[int] = None  # noqa:  N815,UP045
    minimum: Optional[UnionIntFloat] = None  # noqa:  UP045
    maximum: Optional[UnionIntFloat] = None  # noqa:  UP045
    minItems: Optional[int] = None  # noqa:  N815,UP045
    maxItems: Optional[int] = None  # noqa:  N815,UP045
    minProperties: Optional[int] = None  # noqa: N815, UP045
    maxProperties: Optional[int] = None  # noqa: N815, UP045
    multipleOf: Optional[FloatConstraint] = None  # noqa: N815, UP045
    exclusiveMaximum: Optional[Union[FloatConstraint, bool]] = None  # noqa: N815, UP007, UP045
    exclusiveMinimum: Optional[Union[FloatConstraint, bool]] = None  # noqa: N815, UP007, UP045
    additionalProperties: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    unevaluatedProperties: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    unevaluatedItems: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    patternProperties: Optional[dict[str, Union[JsonSchemaObject, bool]]] = None  # noqa: N815, UP007, UP045
    propertyNames: Optional[Union[JsonSchemaObject, bool]] = None  # noqa: N815, UP007, UP045
    oneOf: list[Union[JsonSchemaObject, bool]] = Field(default_factory=list)  # noqa: N815, UP007
    anyOf: list[Union[JsonSchemaObject, bool]] = Field(default_factory=list)  # noqa: N815, UP007
    allOf: list[Union[JsonSchemaObject, bool]] = Field(default_factory=list)  # noqa: N815, UP007
    enum: list[Any] = Field(default_factory=list)
    writeOnly: Optional[bool] = None  # noqa: N815, UP045
    readOnly: Optional[bool] = None  # noqa: N815, UP045
    properties: Optional[dict[str, Union[JsonSchemaObject, bool]]] = None  # noqa: UP007, UP045
    required: list[str] = Field(default_factory=list)
    ref: Optional[str] = Field(default=None, alias="$ref")  # noqa: UP045
    recursiveRef: Optional[str] = Field(default=None, alias="$recursiveRef")  # noqa: N815, UP045
    recursiveAnchor: Optional[bool] = Field(default=None, alias="$recursiveAnchor")  # noqa: N815, UP045
    dynamicRef: Optional[str] = Field(default=None, alias="$dynamicRef")  # noqa: N815, UP045
    dynamicAnchor: Optional[str] = Field(default=None, alias="$dynamicAnchor")  # noqa: N815, UP045
    nullable: Optional[bool] = None  # noqa: UP045
    x_enum_varnames: list[str | None] = Field(default_factory=list, alias="x-enum-varnames")
    x_enum_descriptions: list[str | None] = Field(default_factory=list, alias="x-enum-descriptions")
    x_enum_names: list[str | None] = Field(default_factory=list, alias="x-enumNames")
    x_enum_field_as_literal: Optional[bool] = Field(default=None, alias="x-enum-field-as-literal")  # noqa: UP045
    description: Optional[str] = None  # noqa: UP045
    title: Optional[str] = None  # noqa: UP045
    example: Any = None
    examples: Any = None
    default: Any = None
    id: Optional[str] = Field(default=None, alias="$id")  # noqa: UP045
    anchor: Optional[str] = Field(default=None, alias="$anchor")  # noqa: UP045
    custom_type_path: Optional[str] = Field(default=None, alias="customTypePath")  # noqa: UP045
    custom_base_path: str | list[str] | None = Field(default=None, alias="customBasePath")
    is_boolean_schema_false: bool = Field(default=False, exclude=True)
    read_as_const: bool = Field(default=False, alias=_READ_AS_CONST_KEY)
    extras: dict[str, Any] = Field(alias=__extra_key__, default_factory=dict)
    discriminator: Optional[Union[Discriminator, str]] = None  # noqa: UP007, UP045
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ignored_types=(cached_property,),
        defer_build=True,
    )

    @classmethod
    @override
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler, /) -> CoreSchema:
        """Validate schema objects so that each takes the location of the document object or located dump it is from."""
        return _located_model_schema(source, handler)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Dump the object; during a parse, each dumped schema object remembers the location of its source."""
        data = super().model_dump(**kwargs)
        if SCHEMA_GRAPH.get() is None or kwargs.get("mode", "python") != "python":
            return data
        return _locate_dump(self, data)

    @property
    def schema_location(self) -> SchemaLocation | None:
        """Return the location of the schema object this object stands for."""
        return self.__dict__.get(SCHEMA_LOCATION_KEY)

    def with_schema_location(self, location: SchemaLocation | None) -> Self:
        """Return a shallow copy that stands for the schema object at ``location``; this object is never written."""
        if location is None or self.__dict__.get(SCHEMA_LOCATION_KEY) == location:
            return self
        copied = self.model_copy()
        copied.__dict__[SCHEMA_LOCATION_KEY] = location
        return copied

    def model_post_init(self, __context: Any, /) -> None:
        """Apply post-validation compatibility handling for extension metadata."""
        # Support x-propertyNames extension for OpenAPI 3.0
        if "x-propertyNames" in self.extras and self.propertyNames is None:
            x_prop_names = self.extras.pop("x-propertyNames")
            if isinstance(x_prop_names, bool):
                self.propertyNames = x_prop_names
            elif isinstance(x_prop_names, dict):
                self.propertyNames = JsonSchemaObject.model_validate(x_prop_names, context=__context)

    @cached_property
    def is_object(self) -> bool:
        """Check if the schema represents an object type."""
        return self.properties is not None or (
            self.type == "object" and not self.allOf and not self.oneOf and not self.anyOf and not self.ref
        )

    @cached_property
    def is_array(self) -> bool:
        """Check if the schema represents an array type."""
        return self.items is not None or self.prefixItems is not None or self.type == "array"

    @cached_property
    def ref_object_name(self) -> str:  # pragma: no cover
        """Extract the object name from the reference path."""
        return (self.ref or "").rsplit("/", 1)[-1]

    @field_validator("items", mode="before")
    @classmethod
    def validate_items(cls, values: Any) -> Any:
        """Validate items field, converting empty dicts to None."""
        # this condition expects empty dict
        return None if values == {} else values

    @field_validator("custom_base_path", mode="before")
    @classmethod
    def validate_custom_base_path(cls, value: Any) -> Any:
        """Validate schema-controlled custom base class import paths."""
        match value:
            case None:
                return None
            case list():
                for item in value:
                    _validate_schema_python_import_path(item, "customBasePath")
            case _:
                _validate_schema_python_import_path(value, "customBasePath")
        return value

    @cached_property
    def has_default(self) -> bool:
        """Check if the schema has a default value or default factory."""
        return "default" in self.model_fields_set or "default_factory" in self.extras

    @cached_property
    def has_constraint(self) -> bool:
        """Check if the schema has any constraint fields set."""
        return bool(self.__constraint_fields__ & self.model_fields_set)

    @cached_property
    def ref_type(self) -> JSONReference | None:
        """Get the reference type (LOCAL, REMOTE, or URL)."""
        if self.ref:
            return get_ref_type(self.ref)
        return None

    @cached_property
    def type_has_null(self) -> bool:
        """Check if the type list or oneOf/anyOf contains null.

        A ``type`` that excludes null does not drop the null of its members here: that is
        ``JsonSchemaParser._type_has_null``, with --intersect-allof-keywords.
        """
        return bool(resolve_null_facts(_type_null_sources(self), "schema-type").admits_null)

    @cached_property
    def has_multiple_types(self) -> bool:
        """Check if the type is a list with multiple non-null types."""
        if not isinstance(self.type, list):
            return False
        non_null_types = [t for t in self.type if t != "null"]
        return len(non_null_types) > 1

    @cached_property
    def has_ref_with_schema_keywords(self) -> bool:
        """Check if schema has $ref combined with schema-affecting keywords.

        Metadata-only keywords (title, description, etc.) are excluded
        as they don't affect the schema structure. OpenAPI/JSON Schema
        extension fields (x-*) are also excluded as they are vendor
        extensions and don't affect the core schema structure.
        """
        if not self.ref:
            return False
        other_fields = self.model_fields_set - {"ref"}
        schema_affecting_fields = other_fields - self.__metadata_only_fields__ - {"extras"}
        if self.extras:
            schema_affecting_extras = {k for k in self.extras if k in self.__schema_affecting_extras__}
            if schema_affecting_extras:
                schema_affecting_fields |= {"extras"}
        return bool(schema_affecting_fields)

    @cached_property
    def is_ref_with_nullable_only(self) -> bool:
        """Check if schema has $ref with only nullable: true (no other schema-affecting keywords).

        This is used to avoid creating duplicate models when a $ref is combined
        with nullable: true. In such cases, the reference should be used directly
        with Optional type annotation instead of merging schemas.
        """
        if not self.ref or self.nullable is not True:
            return False
        other_fields = self.model_fields_set - {"ref", "nullable"} - self.__metadata_only_fields__ - {"extras"}
        if other_fields:
            return False
        if self.extras:
            schema_affecting_extras = {k for k in self.extras if k in self.__schema_affecting_extras__}
            if schema_affecting_extras:
                return False
        return True


@lru_cache
def get_ref_type(ref: str) -> JSONReference:
    """Determine the type of reference (LOCAL, REMOTE, or URL)."""
    if ref[0] == "#":
        return JSONReference.LOCAL
    if is_url(ref):
        return JSONReference.URL
    return JSONReference.REMOTE


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

    warn(f"format of {format__!r} not understood for {type_!r} - using default", stacklevel=2)
    return data_formats[type_]["default"]


JsonSchemaObject.model_rebuild()

DEFAULT_FIELD_KEYS: set[str] = {
    "example",
    "examples",
    "description",
    "discriminator",
    "title",
    "const",
    "default_factory",
    "deprecated",
    "contentEncoding",
    "contentMediaType",
    "contentSchema",
    "externalDocs",
    "xml",
}

ALLOWED_DEFAULT_FACTORIES: frozenset[str] = frozenset({"dict", "list", "set"})


def _validate_default_factory(default_factory: Any) -> str:
    if isinstance(default_factory, str) and default_factory in ALLOWED_DEFAULT_FACTORIES:
        return default_factory
    allowed_values = ", ".join(sorted(ALLOWED_DEFAULT_FACTORIES))
    msg = f"default_factory must be one of: {allowed_values}"
    raise Error(msg)


def _validate_schema_python_import_path(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        msg = f"{field_name} must be a dotted Python identifier path: {value!r}"
        raise Error(msg)
    from datamodel_code_generator.validators import _validate_dotted_python_identifier_path  # noqa: PLC0415

    try:
        return _validate_dotted_python_identifier_path(value)
    except ValueError as exc:
        msg = f"{field_name} {exc}"
        raise Error(msg) from None


DEFAULT_MODEL_EXTRA_KEYS: set[str] = {
    "contentEncoding",
    "contentMediaType",
    "contentSchema",
    "externalDocs",
    "xml",
}

EXCLUDE_FIELD_KEYS_IN_JSON_SCHEMA: set[str] = {
    "readOnly",
    "writeOnly",
}

EXCLUDE_FIELD_KEYS = (set(JsonSchemaObject.get_fields()) - DEFAULT_FIELD_KEYS - EXCLUDE_FIELD_KEYS_IN_JSON_SCHEMA) | {
    "$id",
    "$ref",
    "$recursiveRef",
    "$recursiveAnchor",
    "$dynamicRef",
    "$dynamicAnchor",
    "self",
    _READ_AS_CONST_KEY,
    JsonSchemaObject.__extra_key__,
}


_ALL_OF_VALUE_ITEM_KEYWORDS: Final = ("items", "additionalItems", "unevaluatedItems")
"""The item schemas an allOf value part writes, which the intersection of the parts keeps."""
_ALL_OF_VALUE_KEYWORDS: Final = frozenset({
    "type",
    "format",
    "enum",
    "nullable",
    *_ALL_OF_VALUE_ITEM_KEYWORDS,
    *JsonSchemaObject.__constraint_fields__,
})
"""The keywords of an allOf site that its parts' intersection writes, not its own annotations."""
_DEFAULT_SCHEMA_PATHS = ("#/definitions", "#/$defs")
_ALL_OF_MEMBER_SUFFIX: Final = re.compile(r"/allOf/\d+$")
"""The end of an allOf member's location, which a warning about the site leaves out."""


def _is_empty_property_schema(item: JsonSchemaObject, *, allowed_keywords: frozenset[str] = frozenset()) -> bool:
    """Return whether a schema contains only metadata and explicitly handled keywords."""
    if item.dynamicRef is not None or item.recursiveRef is not None:
        return False
    other_fields = item.model_fields_set - item.__metadata_only_fields__ - {"extras"} - allowed_keywords
    if other_fields:
        return False
    return not any(
        key not in item.__metadata_only_fields__ and key not in allowed_keywords and not key.startswith("x-")
        for key in item.extras
    )


def _get_conditional_property_values(item: JsonSchemaObject | bool | None) -> tuple[object, ...] | None:  # noqa: FBT001
    """Extract a presence predicate or literals without discarding property constraints."""
    if item is None or item is True:
        return ()
    if not isinstance(item, JsonSchemaObject):
        return None
    if "const" in item.extras:
        keyword, values = "const", (item.extras["const"],)
    elif item.enum:
        keyword, values = "enum", tuple(item.enum)
    else:
        return () if _is_empty_property_schema(item) else None
    if not _is_empty_property_schema(item, allowed_keywords=frozenset({keyword, "type"})):
        return None
    if item.type is None:
        return values
    schema_types = (item.type,) if isinstance(item.type, str) else item.type
    return (
        tuple(
            value
            for value in values
            if (value_type := _get_json_value_type(value)) in schema_types
            or (value_type == "integer" and "number" in schema_types)
            or (isinstance(value, float) and value.is_integer() and "integer" in schema_types)
        )
        or None
    )


def _ref_path_parts(file_part: str) -> list[str]:
    """Split the document part of a resolved reference into model resolver root parts."""
    if is_url(file_part):
        return [file_part]
    return file_part.split("/") if file_part else []


def _is_plain_name_fragment(ref: str) -> bool:
    """Return whether a reference is a plain-name fragment, which names an anchor instead of a location."""
    return ref.startswith("#") and ref[1:2] not in {"", "/"}


def _document_location(location: str, document: str) -> str:
    """Return a registry location as a reference from a document, local when it lies in the document."""
    target_document, _, pointer = location.partition("#")
    return f"#{pointer}" if target_document == document else location


def _file_uri_path(uri: str) -> Path:
    """Return the local path that a file URI names."""
    from urllib.request import url2pathname  # noqa: PLC0415

    return Path(url2pathname(uri[5:]))


def _pointer_ref(parts: Sequence[str]) -> str:
    """Return the local reference of a JSON pointer given as decoded parts."""
    return "#" + "".join(f"/{part.replace('~', '~0').replace('/', '~1')}" for part in parts)


def _nearest_recursive_anchor(anchors: Iterable[str], current_ref: str) -> str:
    """Return the longest $recursiveAnchor reference enclosing a location reference, or the document root."""
    nearest = "#"
    for anchor in anchors:
        if (
            len(anchor) > len(nearest)
            and current_ref.startswith(anchor)
            and current_ref[len(anchor) : len(anchor) + 1] in {"", "/"}
        ):
            nearest = anchor
    return nearest


def _unanchored_resource_ref(raw: dict[str, Any], parts: Sequence[str]) -> str | None:
    """Return the nearest schema along a pointer that declares an $id resource, when it has no $recursiveAnchor.

    A $recursiveRef initially resolves to the root of its schema resource, and stays there without one.
    """
    for index in range(len(parts), 0, -1):
        match _get_model_by_path_or_missing(raw, list(parts[:index])):
            case {"$id": str() as identifier} as schema if not identifier.startswith("#"):
                return None if schema.get("$recursiveAnchor") is True else _pointer_ref(parts[:index])
    return None


@final
@dataclass(frozen=True, slots=True)
class _DynamicEdges:
    """What the `$dynamicRef` analysis reads of a referenced schema (``JsonSchemaParser._dynamic_ref_edges``)."""

    node: _TargetNode
    """The schema graph node of the schema, or its key when the graph does not locate it."""
    anchors: frozenset[str]
    """The `$dynamicAnchor` names its `$dynamicRef` initially resolve to."""
    targets: tuple[str, ...]
    """The keys of the schemas its `$ref` and those `$dynamicAnchor` name."""


class _DynamicSpecialization(NamedTuple):
    """A referenced schema to parse again with the $dynamicAnchor bindings of its dynamic scope."""

    target: str
    bindings: dict[str, str]
    reference: Reference
    path: tuple[str, ...]


@final
@dataclass(frozen=True, slots=True)
class _DynamicTarget:
    """Where rule DS (``JsonSchemaParser._dynamic_scope_target``) resolves a reference in the dynamic scope."""

    ref: str = "#"
    """The reference to resolve from the current document, a plain string: it has no edge."""
    location: str | None = None
    """The registry location of the `$dynamicAnchor` the dynamic scope binds the reference to, which is resolved
    instead of ``ref`` (``_schema_location_context``)."""


class _AllOfItemGroups(NamedTuple):
    """The allOf members of one schema by the part of its lightweight type each one gives."""

    refs: list[JsonSchemaObject]
    ref_data_types: list[DataType]
    primitives: list[JsonSchemaObject]
    constraint_only: list[JsonSchemaObject]
    objects: list[JsonSchemaObject]


_BuilderT = TypeVar("_BuilderT", bound="Callable[..., Any]")


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


def _builds_model(method: _BuilderT) -> _BuilderT:
    """Run a model builder in a frame whose cell becomes the reference of the model it returns, if it built it.

    A model records the frames it is built in, so the innermost filled frame of another model names the model whose
    parse built it (`owner_of`). A returned model recorded outside the frame, such as a `$ref` target, leaves the cell
    empty.
    """

    @wraps(method)
    def build(self: JsonSchemaParser, *args: Any, **kwargs: Any) -> Any:
        cell: list[Reference | None] = [None]
        self._model_frames.append(cell)
        try:
            result = method(self, *args, **kwargs)
        finally:
            self._model_frames.pop()
        if (
            isinstance(result, DataType)
            and (reference := result.reference) is not None
            and (record := reference.__dict__.get(SCHEMA_OWNER_FRAMES_KEY)) is not None
            and any(frame is cell for frame in record.frames)
        ):
            cell[0] = reference
        return result

    return cast("_BuilderT", build)


def _parses_document(method: _BuilderT) -> _BuilderT:
    """Run a document parse with no model builder frame open, so its models are roots whatever parse reached it."""

    @wraps(method)
    def parse(self: JsonSchemaParser, *args: Any, **kwargs: Any) -> Any:
        frames, self._model_frames = self._model_frames, []
        try:
            return method(self, *args, **kwargs)
        finally:
            self._model_frames = frames

    return cast("_BuilderT", parse)


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

    COMPATIBLE_PYTHON_TYPES: ClassVar[dict[str, frozenset[str]]] = {
        "string": frozenset({"str", "String"}),
        "integer": frozenset({"int", "Integer"}),
        "number": frozenset({"float", "int", "Number"}),
        "boolean": frozenset({"bool", "Boolean"}),
        "array": frozenset({
            "list",
            "List",
            "set",
            "Set",
            "frozenset",
            "FrozenSet",
            "Sequence",
            "MutableSequence",
            "tuple",
            "Tuple",
            "AbstractSet",
            "MutableSet",
        }),
        "object": frozenset({"dict", "Dict", "Mapping", "MutableMapping", "TypedDict"}),
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
        self._output_model_context = OutputModelContext.from_generation_types(
            data_model_type=self.data_model_type,
            data_model_root_type=self.data_model_root_type,
            data_model_field_type=self.data_model_field_type,
            data_type_manager_type=type(self.data_type_manager),
            configured_types_are_builtin=self._configured_generation_types_are_builtin,
            use_annotated=self.use_annotated,
        )

        # Normalize external ref mapping paths to absolute for reliable matching
        raw_mapping = self._source_context.external_ref_mapping
        self._external_ref_mapping: dict[str, str] = {}
        if raw_mapping:
            for file_path, python_package in raw_mapping.items():
                if is_url(file_path):
                    self._external_ref_mapping[file_path] = python_package
                else:
                    abs_path = str((self.base_path / file_path).resolve())
                    self._external_ref_mapping[abs_path] = python_package
        self.reserved_refs: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
        self._loads_only_used_references = False
        self._definitions_without_model: dict[str, Reference] = {}
        self._mapped_definition_imports: dict[str, Import] = {}
        self._mapped_references: dict[str, tuple[str, str]] = {}
        self._python_import_values: dict[str, YamlValue] = {}
        self._dangling_refs: set[tuple[str, str]] = set()
        self._documents_as_written: dict[int, tuple[dict[str, YamlValue], dict[str, YamlValue]]] = {}
        self._held_value: tuple[YamlValue, SchemaLocation] | None = None
        self._inexact_object_maps: set[str] = set()
        self._model_frames: list[list[Reference | None]] = []
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
                k.removeprefix("x-") if k in self.field_extra_keys_without_x_prefix else k
            ): self._externalize_schema_extra(k, v)
            for k, v in obj.extras.items()
            if self.field_include_all_keys or k in self.field_keys
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

    def _get_format_data_type(self, type_: str, format_: str) -> DataType:
        """Map a type and format without constraints."""
        types = self._get_type_with_mappings(type_, format_)
        return self.data_type_manager.get_data_type(types, **self._get_format_type_kwargs(type_, format_, types))

    def _get_format_type_kwargs(self, type_: str, format_: str, types: Types) -> dict[str, bool]:
        """Keep bytes mapped from base64 text decoded, and string time formats out of float strictness."""
        if types == Types.binary and self._is_base64_encoded_binary_mapping(type_, format_):
            return {"base64_encoded": True}
        if types == Types.time and type_ == "string":
            return {"string_format": True}
        return {}

    def _is_base64_encoded_binary_mapping(self, type_: str, format_: str) -> bool:
        if type_ != "string" or format_ != "byte" or not self.type_mappings:
            return False
        return self.type_mappings.get((type_, format_)) == "binary"

    @cached_property
    def schema_paths(self) -> list[tuple[str, list[str]]]:
        """Get schema paths for definitions and defs.

        For JsonSchema, uses schema_features.definitions_key to determine
        the primary path, with fallback to the alternative in Lenient mode.
        OpenAPI subclass uses its own SCHEMA_PATHS (#/components/schemas).
        """
        # OpenAPI and other subclasses use their own SCHEMA_PATHS
        if list(_DEFAULT_SCHEMA_PATHS) != self.SCHEMA_PATHS:
            return [(s, s.lstrip("#/").split("/")) for s in self.SCHEMA_PATHS]

        # JsonSchema: use definitions_key from schema_features
        primary_key = self.schema_features.definitions_key
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
        """Get schema features based on config or detected version."""
        return self._schema_features_for(self.raw_obj)

    def _schema_features_for(self, root: dict[str, YamlValue]) -> JsonSchemaFeatures:
        """Get the schema features of a root document based on config or its detected version."""
        config_version = getattr(self.config, "jsonschema_version", None)
        if config_version is not None and config_version != JsonSchemaVersion.Auto:
            return JsonSchemaFeatures.from_version(config_version)
        version = detect_jsonschema_version(root) if root else JsonSchemaVersion.Auto
        return JsonSchemaFeatures.from_version(version)

    @cached_property
    def _ref_sibling_keywords_enabled(self) -> bool:
        """Return whether this JSON Schema draft evaluates validation beside ``$ref``."""
        config_version = getattr(self.config, "jsonschema_version", None)
        version = (
            config_version
            if config_version is not None and config_version != JsonSchemaVersion.Auto
            else detect_jsonschema_version(self.raw_obj)
            if self.raw_obj
            else JsonSchemaVersion.Auto
        )
        return version not in _REF_SIBLING_KEYWORDS_DISABLED_VERSIONS

    @property
    def _ref_siblings_are_parts(self) -> bool:
        """Whether the document's dialect reads the keywords beside a ``$ref`` as parts of the schema."""
        return self._ref_sibling_keywords_enabled

    def _ref_siblings_are_parts_of(self, schema: JsonSchemaObject) -> bool:
        """Return whether the keywords beside the ``$ref`` of ``schema`` are parts of it.

        They are in a dialect that reads them so, and in every dialect where an inherited property override wrote
        them beside the inherited ``$ref`` (marked ``_INHERITED_OVERRIDE_KEY``), since an override is an allOf part.
        """
        return self._ref_siblings_are_parts or _INHERITED_OVERRIDE_KEY in schema.__dict__

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
        return self._enum_as_literal_under(
            self.enum_field_as_literal.value if self.enum_field_as_literal else None, obj, property_name, property_obj
        )

    def _enum_as_literal_under(
        self,
        setting: LiteralSetting | None,
        obj: JsonSchemaObject,
        property_name: str | None,
        property_obj: JsonSchemaObject | None,
    ) -> bool:
        """Return whether an enum is written as a Literal under an ``--enum-field-as-literal`` setting."""
        return enum_as_literal(
            extension=(property_obj if property_obj is not None else obj).x_enum_field_as_literal,
            mapped=self.enum_field_as_literal_map.get(property_name) if property_name else None,
            setting=setting,
            member_count=len(obj.enum),
        )

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
        if self._enum_as_literal_under("all", obj, name, property_obj):
            self._literal_under_all_enum_reference_ids.update(
                id(model.reference) for model in self.results[built:] if isinstance(model, Enum)
            )
        return data_type

    def _extract_const_enum_from_combined(  # noqa: PLR0912
        self, items: list[JsonSchemaObject | bool], parent: JsonSchemaObject, *, writes: bool = False
    ) -> tuple[list[Any], list[str], list[str], str | None, bool, list[Any] | None] | None:
        """Extract enum values from oneOf/anyOf const pattern.

        Const branches that --use-single-value-enum-as-const read merge into one Enum only when there are two or more
        and all hold values of one JSON type; otherwise each branch is typed as written, each const as its Literal and
        null as None. Under --read-const-beside-enum, without the option a document's own read-as-const marker reads
        nothing; without --read-const-beside-enum it reads, as in 0.86.0. The values are those that meet their branch's
        constraints and the parent's (``_combined_values_admitted``), with all the consts as written when some do not;
        the parent's field writes its own when ``writes``.
        """
        enum_values: list[Any] = []
        branches: list[JsonSchemaObject] = []
        nullable = False
        inferred_type: str | None = None
        read_as_const = self.use_single_value_enum_as_const or not self.read_const_beside_enum

        for item in items:
            if item is False:
                continue
            if item is True:
                return None
            if item.type == "null" and "const" not in item.extras:
                nullable = True
                continue

            if "const" not in item.extras:
                return None

            if item.ref or item.properties or item.oneOf or item.anyOf or item.allOf:
                return None

            const_value = item.extras["const"]
            enum_values.append(const_value)
            branches.append(item)
            read_as_const = read_as_const and item.read_as_const

            if inferred_type is None:
                inferred_type = _CONST_ENUM_TYPES.get(literal_value_kind(const_value))

        if not enum_values:  # pragma: no cover
            return None
        if read_as_const and (
            len(values := [value for value in enum_values if value is not None]) == 1
            or len({_get_json_value_type(value) for value in values}) > 1
        ):
            return None
        values_as_written = None
        if (admitted := self._combined_values_admitted(parent, enum_values, branches, writes=writes)) is not None:
            values_as_written = enum_values
            enum_values = list(compress(enum_values, admitted))
            branches = list(compress(branches, admitted))
        final_type = inferred_type
        parent_type = parent.type
        match parent_type:
            case str():
                final_type = parent_type
            case list():
                non_null_types = [t for t in parent_type if t != "null"]
                final_type = non_null_types[0] if non_null_types else inferred_type
                if "null" in parent_type:
                    nullable = True
            case _:
                pass

        return (
            enum_values,
            [item.title for item in branches if item.title],
            [item.description or "" for item in branches],
            final_type,
            nullable,
            values_as_written,
        )

    def _combined_values_admitted(
        self, parent: JsonSchemaObject, values: list[Any], branches: list[JsonSchemaObject], *, writes: bool
    ) -> tuple[bool, ...] | None:
        """Return whether the field keeps each const of a oneOf or anyOf, None when it keeps all.

        A const is kept when it meets its branch's constraints and those of the parent its field leaves out
        (``_literal_written``); a const that meets its branch's and every one of the parent's is a value the schema
        admits (``_admitted_or_warn``). None without --filter-literal-values.
        """
        if not self.checks_literal_values:
            return None
        keywords: dict[str, None] = {}
        own: list[bool] = []
        for value, branch in zip(values, branches, strict=True):
            decided, (met,) = literal_check([value], _literal_constraints(branch))
            keywords.update(dict.fromkeys(decided))
            own.append(met)
        decided, met_parent, kept_parent = _literal_met(
            parent, values, self._literal_written(parent, values, writes=writes)
        )
        keywords.update(dict.fromkeys(decided))
        return self._admitted_or_warn(
            parent, tuple(map(and_, own, met_parent)), tuple(map(and_, own, kept_parent)), ("const", *keywords)
        )

    def _literal_written(self, obj: JsonSchemaObject, values: list[Any], *, writes: bool) -> frozenset[str]:
        """Return the keywords a literal check leaves to the field of a schema's values, as the field writes them.

        A field that writes Field() arguments (``writes`` at its site) writes beside the ``Literal`` of the values the
        keywords ``_split_constraint_values`` gives it, which Pydantic checks on the ``Literal``; under
        ``--omit-redundant-constraints`` the literal check decides them all and the field writes none of them.
        """
        if not (
            writes
            and self.checks_literal_values
            and self.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS
            and not self.config.omit_redundant_constraints
            and _literal_constraints(obj)
        ):
            return NO_KEYWORDS
        return (
            frozenset(self._split_constraint_values(obj, self._literal_values_type(values))[0])
            & LITERAL_DECIDABLE_KEYWORDS
        )

    def _admitted_or_warn(
        self, obj: JsonSchemaObject, met: tuple[bool, ...], kept: tuple[bool, ...], keywords: tuple[str, ...]
    ) -> tuple[bool, ...] | None:
        """Return whether the field keeps each value (``kept``), None when it keeps all.

        When no value meets every constraint the check decides (``met``), the schema admits no value: the
        unsatisfiable warning names the ``keywords`` and the values stay as written (None).
        """
        if not any(met):
            self._warn_unsatisfiable(str(obj.schema_location or "schema"), keywords, _LITERAL_PARTS)
            return None
        return None if all(kept) else kept

    def _check_literal_const(self, obj: JsonSchemaObject, const: object) -> None:
        """Warn when a const does not meet every constraint beside it that the literal check decides."""
        if not self.checks_literal_values:
            return
        decided, met, _ = _literal_met(obj, [const], NO_KEYWORDS)
        self._admitted_or_warn(obj, met, met, ("const", *decided))

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
        view = self._literal_view(obj, written)
        data_type = self._literal_values_type(view.enum)
        if (values_as_written := view.__dict__.get(VALUES_AS_WRITTEN_KEY)) is not None:
            self._graph_builder.record_values_as_written(data_type, values_as_written)
        return data_type

    def _create_synthetic_enum_obj(  # noqa: PLR0913, PLR0917
        self,
        original: JsonSchemaObject,
        enum_values: list[Any],
        varnames: list[str],
        descriptions: list[str],
        enum_type: str | None,
        nullable: bool,  # noqa: FBT001
        values_as_written: list[Any] | None = None,
    ) -> JsonSchemaObject:
        """Create a synthetic JsonSchemaObject for enum parsing, which keeps the consts as written when given."""
        final_enum = [*enum_values, None] if nullable else enum_values
        final_varnames = varnames if len(varnames) == len(enum_values) else []
        enum_metadata = {"x-enum-varnames": final_varnames}
        if any(descriptions):
            enum_metadata["x-enum-descriptions"] = descriptions

        synthetic = self.SCHEMA_OBJECT_TYPE(
            type=enum_type,
            enum=final_enum,
            nullable=nullable,
            title=original.title,
            description=original.description,
            **(enum_metadata | ({"default": original.default} if original.has_default else {})),
        )
        if values_as_written is not None:
            _keep_values_as_written(synthetic, [*values_as_written, None] if nullable else values_as_written)
        return _locate(synthetic, original.schema_location)

    def is_constraints_field(self, obj: JsonSchemaObject) -> bool:
        """Check if a field should include constraints."""
        return obj.is_array or (
            self.field_constraints
            and not (
                obj.ref
                or obj.anyOf
                or obj.oneOf
                or obj.allOf
                or obj.is_object
                or (obj.enum and not self.ignore_enum_constraints)
            )
        )

    def _has_effective_constraints(self, obj: JsonSchemaObject) -> bool:
        """Return whether direct or derived constraints require a generated field."""
        if obj.properties or obj.patternProperties or obj.required:
            return False
        if obj.is_object or obj.propertyNames is not None:
            return bool(self._get_property_count_constraints(obj))
        if obj.is_array:
            return bool(obj.minItems is not None or obj.maxItems is not None or self._get_array_items_constraints(obj))

        match obj.type:
            case str() as schema_type:
                pass
            case list() as schema_types if (
                schema_type := next((item for item in schema_types if item != "null"), None)
            ) is not None and all(item in ("null", schema_type) for item in schema_types):  # noqa: PLR6201
                pass
            case _:
                return False

        match schema_type:
            case "integer" | "number":
                constraint_fields = NUMBER_KEYWORD_ORDER
            case "string":
                constraint_fields = STRING_KEYWORD_ORDER
            case _:
                return False
        return any(field in obj.model_fields_set for field in constraint_fields)

    def _accepts_number_constraints(self, types: Sequence[str], format_: str | None) -> bool:
        """Return whether a declared type of the schema holds numbers, as number, integer or a numeric string format."""
        format_ = format_ or "default"
        return any(
            type_ in {"number", "integer"}
            or (
                (
                    (type_, format_) in self.type_mappings
                    or (type_ == "string" and format_ in self._data_formats["string"])
                )
                and holds_number_format(self._get_type_with_mappings(type_, format_).name)
            )
            for type_ in types
        )

    def _declared_kinds(self, types: str | Sequence[str] | None, format_: str | None) -> DeclaredKinds:
        """Return the JSON kinds whose keywords a schema's declared types hold, with its format."""
        if types is None:
            return DeclaredKinds()
        types = (types,) if isinstance(types, str) else types
        return DeclaredKinds(
            numbers=self._accepts_number_constraints(types, format_), string="string" in types, array="array" in types
        )

    def _declared_constraint_keywords(self, obj: JsonSchemaObject) -> frozenset[str]:
        """Return the type-dependent keywords whose kind a declared type of the schema holds.

        A declared string also holds minItems and maxItems and a declared array also holds minLength and maxLength,
        which are written as their length bounds.
        """
        return declared_keywords(self._declared_kinds(obj.type, obj.format))

    def _keywords_taken(self, kinds: DeclaredKinds, data_type: DataType) -> tuple[frozenset[str], frozenset[str]]:
        """Return the keywords of the declared kinds that a field of the emitted type writes, and those it leaves out.

        An output model that writes constraints as ``Field()`` arguments writes a number keyword of a declared number
        only when a number value of the type meets it, and a keyword of a declared string only when the type takes
        it (``declared_string_keywords_taken``), which a ``date`` or a ``UUID`` does not; another output model writes
        them all. The kinds of a schema type and format (``_declared_kinds``) ask it of the type they map to before a
        field is built.
        """
        if not self.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS:
            return declared_keywords(kinds), NO_KEYWORDS
        target = self.target_python_version.version_key
        return split_declared_keywords(
            kinds,
            number_taken=self.data_type_manager.constraint_keywords(
                data_type, target, number_values=True, target_model_version=self.target_pydantic_version
            )
            if kinds.numbers
            else None,
            string_taken=declared_string_keywords_taken(
                self.data_type_manager.constraint_keywords(data_type, target), _imported_type_paths(data_type)
            )
            if kinds.string
            else None,
        )

    def _get_constraint_values(self, obj: JsonSchemaObject, data_type: DataType | None = None) -> dict[str, Any]:
        """Return JSON Schema constraint values, with the type-dependent keywords that the field takes."""
        return self._split_constraint_values(obj, data_type)[0]

    def _split_constraint_values(
        self, obj: JsonSchemaObject, data_type: DataType | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the constraint values of the type-dependent keywords that a field takes, and those it leaves out.

        The keywords of a kind a declared schema type holds (number, string or array) follow ``_keywords_taken``;
        the emitted Python type decides the others. The left-out ones are the declared keywords the type does not
        take, to warn about, ``minItems`` and ``maxItems`` of a declared string as the lengths they are written as.
        """
        values = {
            constraint: value
            for constraint in obj.__constraint_field_order__
            if (value := getattr(obj, constraint)) is not None
        }
        if data_type is not None and (carried := self._type_enforced_keywords(obj, data_type)):
            values = {key: value for key, value in values.items() if key not in carried}
        if not values:
            return values, {}
        kinds = self._declared_kinds(obj.type, obj.format)
        if data_type is None:
            declared = declared_keywords(kinds)
            return {key: value for key, value in values.items() if key in declared}, {}
        written, untaken = self._keywords_taken(kinds, data_type)
        if values.keys() <= written:
            return values, {}
        written |= (
            undeclared_keywords_taken(
                self.data_type_manager.constraint_keywords(data_type, self.target_python_version.version_key),
                value_literal=_is_value_literal(data_type),
            )
            - untaken
        )
        return {key: value for key, value in values.items() if key in written}, untaken_values(values, untaken)

    def _const_literal_enforces_constraints(self, obj: JsonSchemaObject) -> bool:
        """Return whether ``--omit-redundant-constraints`` types a schema by the ``Literal`` of its const alone.

        The ``Literal`` enforces every constraint beside the const when the const meets each of them as the literal
        check decides it, so an item of the schema needs no root model to write them on.
        """
        if not self.config.omit_redundant_constraints or "const" not in obj.extras:
            return False
        if not self._const_is_literal(const := obj.extras["const"]):
            return False
        constraints = _literal_constraints(obj)
        return constraints.keys() == obj.__constraint_fields__ & obj.model_fields_set and len(
            _literal_meets([const], constraints)
        ) == len(constraints)

    def _type_enforced_keywords(self, obj: JsonSchemaObject, data_type: DataType) -> frozenset[str]:
        """Return the keywords of a schema that the type of its field enforces itself, which the field leaves out.

        The keywords the type consumed (``_carried_constraint_keywords``), and under ``--omit-redundant-constraints``
        also those it enforces by its class or its literal values (``_enforced_constraint_keywords``).
        """
        carried = _carried_constraint_keywords(data_type)
        if not self.config.omit_redundant_constraints:
            return carried
        return carried | _enforced_constraint_keywords(
            data_type, self.data_type_manager.CONSTRAINED_TYPE_CONSUMED_KEYS, _literal_constraints(obj)
        )

    def _mark_type_dependent_constraints(self, model_field: DataModelFieldBase, obj: JsonSchemaObject) -> None:
        """Remember the keywords the emitted type decides, so a scoped type override decides them again."""
        if not self._type_override_imports:
            return
        declared = self._declared_constraint_keywords(obj)
        if type_dependent_constraints := {
            key: value
            for key in obj.__constraint_field_order__
            if key not in declared and (value := getattr(obj, key)) is not None
        }:
            model_field.__dict__[_TYPE_DEPENDENT_CONSTRAINTS_KEY] = type_dependent_constraints

    def _is_fixed_length_tuple(self, obj: JsonSchemaObject) -> bool:
        """Check if an array field represents a fixed-length tuple."""
        return (
            self._get_fixed_positional_items(obj) is not None
            or self._get_fixed_length_homogeneous_tuple_length(obj) is not None
        )

    def _get_fixed_length_homogeneous_tuple_length(self, obj: JsonSchemaObject) -> int | None:
        """Return the length of an opted-in homogeneous fixed-length tuple."""
        if (
            not self.use_tuple_for_fixed_length_arrays
            or obj.prefixItems is not None
            or not isinstance(obj.items, JsonSchemaObject)
        ):
            return None
        if (tuple_length := obj.minItems) is None or tuple_length != obj.maxItems or tuple_length < 0:
            return None
        return tuple_length

    @classmethod
    def _get_schemas_before_false(
        cls, items: Sequence[JsonSchemaObject | bool] | None
    ) -> tuple[list[JsonSchemaObject | bool], bool]:
        """Return schemas before the first false schema and whether one was found."""
        schema_items = [*(items or [])]
        if (false_index := cls._get_first_false_schema_index(schema_items)) is not None:
            return schema_items[:false_index], True
        return schema_items, False

    @staticmethod
    def _tail_schema(
        *tail_candidates: JsonSchemaObject | bool | list[JsonSchemaObject | bool] | None,
        include_true_schema: bool = False,
    ) -> JsonSchemaObject | bool | None:
        for tail_candidate in tail_candidates:
            match tail_candidate:
                case JsonSchemaObject() as tail_schema:
                    return tail_schema
                case True if include_true_schema:
                    return True
        return None

    def _get_array_item_schemas(
        self,
        obj: JsonSchemaObject,
        *,
        include_true_tail_schema: bool = False,
        force_prefix_items: bool = False,
    ) -> tuple[list[JsonSchemaObject | bool], bool, bool, int | None]:
        """Return item schemas plus tuple/constraint flags and homogeneous tuple length."""
        if (fixed_items := self._get_fixed_positional_items(obj)) is not None:
            return fixed_items, True, True, None
        if (
            isinstance(item_schema := obj.items, JsonSchemaObject)
            and (tuple_item_count := self._get_fixed_length_homogeneous_tuple_length(obj)) is not None
        ):
            return [item_schema] if tuple_item_count else [], True, True, tuple_item_count
        return (
            self._get_list_item_schemas(
                obj,
                include_true_tail_schema=include_true_tail_schema,
                force_prefix_items=force_prefix_items,
            ),
            False,
            False,
            None,
        )

    def _get_list_item_schemas(
        self,
        obj: JsonSchemaObject,
        *,
        include_true_tail_schema: bool = False,
        force_prefix_items: bool = False,
    ) -> list[JsonSchemaObject | bool]:
        """Return the item schemas whose union the list form of an array holds; none means any item."""
        if obj.prefixItems is not None and (force_prefix_items or self._has_prefix_items_tail_schema_or_boolean(obj)):
            items, has_false_schema = self._get_schemas_before_false(obj.prefixItems)
            if (
                not has_false_schema
                and (
                    tail_schema := self._tail_schema(
                        obj.items,
                        obj.unevaluatedItems,
                        include_true_schema=include_true_tail_schema,
                    )
                )
                is not None
            ):
                items.append(tail_schema)
            return items

        match obj.items:
            case JsonSchemaObject() as item_schema:
                return [item_schema]
            case list() as item_schemas:
                items, has_false_schema = self._get_schemas_before_false(item_schemas)
                if (
                    not has_false_schema
                    and (
                        tail_schema := self._tail_schema(
                            obj.additionalItems,
                            include_true_schema=include_true_tail_schema,
                        )
                    )
                    is not None
                ):
                    items.append(tail_schema)
                return items

        match obj.unevaluatedItems:
            case JsonSchemaObject() as item_schema:
                return [item_schema]
            case True if include_true_tail_schema:
                return [True]

        return []

    @classmethod
    def _get_property_count_constraints(cls, obj: JsonSchemaObject) -> dict[str, int]:
        """Return dict length constraints derived from object property-count keywords."""
        constraints: dict[str, int] = {}
        if obj.minProperties is not None:
            constraints["minItems"] = obj.minProperties
        if obj.maxProperties is not None:
            constraints["maxItems"] = obj.maxProperties
        if cls._property_names_forbids_all_keys(obj.propertyNames):
            constraints["maxItems"] = 0
        if obj.additionalProperties is False and not obj.properties and not obj.patternProperties:
            constraints["maxItems"] = 0
        if (
            obj.additionalProperties is False
            and obj.patternProperties
            and all(value is False for value in obj.patternProperties.values())
            and not obj.properties
        ):
            constraints["maxItems"] = 0
        return constraints

    def _dropped_property_counts(self, obj: JsonSchemaObject, data_type: DataType) -> dict[str, int]:
        """Return the property counts of ``obj`` that the object map builder leaves out of its type-array union.

        A field takes the counts only on a type that is a map itself; they are keyed as the field writes the length
        of a map, so the dropped-constraint warning names them. Only the builder of
        ``--use-single-dict-for-object-maps`` joins the map with the other types.
        """
        if (
            not self.use_single_dict_for_object_maps
            or not (obj.patternProperties or obj.propertyNames is not None)
            or _object_map_other_types(obj) is None
            or not any(member.is_dict or member.is_mapping for member in data_type.data_types)
        ):
            return {}
        return {
            "minLength" if key == "minItems" else "maxLength": value
            for key, value in self._get_property_count_constraints(obj).items()
        }

    def _should_parse_empty_object_as_dict(self, obj: JsonSchemaObject) -> bool:  # noqa: PLR6301
        return bool(obj.minProperties is not None or obj.maxProperties is not None or obj.propertyNames is not None)

    @staticmethod
    def _property_names_forbids_all_keys(property_names: JsonSchemaObject | bool | None) -> bool:  # noqa: FBT001
        """Return whether a propertyNames schema rejects every JSON object key."""
        if property_names is False:
            return True
        if not isinstance(property_names, JsonSchemaObject):
            return False
        forbids_all_keys = bool(
            property_names.enum and not any(isinstance(value, str) for value in property_names.enum)
        )
        forbids_all_keys = forbids_all_keys or (
            "const" in property_names.extras and not isinstance(property_names.extras["const"], str)
        )
        if isinstance(property_names.type, str):
            forbids_all_keys = forbids_all_keys or property_names.type != "string"
        elif isinstance(property_names.type, list):
            forbids_all_keys = forbids_all_keys or "string" not in property_names.type
        return forbids_all_keys

    @classmethod
    def _get_array_max_items_constraints(cls, obj: JsonSchemaObject) -> list[int]:
        max_items: list[int] = []
        false_prefix_index = cls._get_first_false_schema_index(obj.prefixItems)
        if false_prefix_index is not None:
            max_items.append(false_prefix_index)
        if isinstance(obj.items, list):
            false_item_index = cls._get_first_false_schema_index(obj.items)
            if false_item_index is not None:
                max_items.append(false_item_index)
        if obj.items is False and obj.prefixItems is None:
            max_items.append(0)
        if obj.items is False and obj.prefixItems is not None:
            max_items.append(len(obj.prefixItems))
        if obj.additionalItems is False and isinstance(obj.items, list):
            max_items.append(len(obj.items))
        if obj.unevaluatedItems is False and obj.items is None:
            max_items.append(len(obj.prefixItems or []))
        return max_items

    @staticmethod
    def _contains_matches_every_item(obj: JsonSchemaObject) -> bool:
        contains = obj.extras.get("contains")
        return contains is True or contains == {}

    @classmethod
    def _get_contains_count_constraints(cls, obj: JsonSchemaObject) -> tuple[int | None, int | None]:
        if not cls._contains_matches_every_item(obj):
            return None, None

        min_contains = obj.extras.get("minContains")
        max_contains = obj.extras.get("maxContains")
        min_items = (
            min_contains
            if isinstance(min_contains, int) and not isinstance(min_contains, bool) and min_contains > 0
            else 1
            if "minContains" not in obj.extras
            else None
        )
        max_items = max_contains if isinstance(max_contains, int) and not isinstance(max_contains, bool) else None
        return min_items, max_items

    @staticmethod
    def _contains_false_requires_match(obj: JsonSchemaObject) -> bool:
        if obj.extras.get("contains") is not False:
            return False
        if "minContains" not in obj.extras:
            return True
        min_contains = obj.extras["minContains"]
        return isinstance(min_contains, int) and not isinstance(min_contains, bool) and min_contains > 0

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
        if cls._contains_matches_every_item(obj):
            contains_min_items, contains_max_items = cls._get_contains_count_constraints(obj)
            if contains_min_items is not None:
                min_items.append(contains_min_items)
            if contains_max_items is not None:
                max_items.append(contains_max_items)
        elif cls._contains_false_requires_match(obj):
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

    @classmethod
    def _get_effective_array_length(cls, obj: JsonSchemaObject) -> int | None:
        """Return the length that the effective length bounds fix, the bounds the length constraints come from."""
        derived = cls._get_array_items_constraints(obj)
        min_items = derived.get("minItems", obj.minItems)
        return min_items if min_items is not None and min_items == derived.get("maxItems", obj.maxItems) else None

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
        if not self.use_tuple_for_closed_arrays:
            return self._get_explicit_fixed_positional_items(obj)
        if obj.prefixItems is not None:
            positional_key, positions = "prefixItems", obj.prefixItems
            explicit = obj.minItems is not None and obj.minItems == obj.maxItems
        elif self.use_tuple_for_fixed_items and isinstance(obj.items, list):
            positional_key, positions = "items", obj.items
            explicit = obj.minItems == obj.maxItems == len(obj.items)
        else:
            return None
        length = obj.minItems if explicit else self._get_effective_array_length(obj)
        if length is None or length < 0:
            return None
        tail = cast("JsonSchemaObject | bool", self._get_inherited_positional_tail(obj, positional_key))
        items = [*positions[:length], *(tail for _ in range(length - len(positions)))]
        if any(item is False for item in items):
            return None
        if explicit:
            return items
        read_ref = canonical_ref or self.model_resolver.resolve_ref
        first, *others = (
            self._get_position_validation_keywords(schema, read_ref)
            for schema in [*items, *(self._get_list_item_schemas(obj) or [True])]
        )
        return None if all(other == first for other in others) else items

    def _get_explicit_fixed_positional_items(self, obj: JsonSchemaObject) -> list[JsonSchemaObject | bool] | None:
        """Return the positions of an array whose explicit ``minItems`` and ``maxItems`` are equal, or None."""
        if obj.prefixItems is not None:
            return self._get_fixed_length_prefix_tuple_items(obj)
        if (
            self.use_tuple_for_fixed_items
            and isinstance(obj.items, list)
            and obj.minItems == obj.maxItems == len(obj.items)
            and not any(item is False for item in obj.items)
        ):
            return [*obj.items]
        return None

    @classmethod
    def _get_fixed_length_prefix_tuple_items(cls, obj: JsonSchemaObject) -> list[JsonSchemaObject | bool] | None:
        """Return positional item schemas for fixed-length prefixItems arrays."""
        if (tuple_length := obj.minItems) is None or tuple_length != obj.maxItems or tuple_length < 0:
            return None
        items = [*(obj.prefixItems or [])[:tuple_length]]
        if isinstance(obj.items, (JsonSchemaObject, bool)):
            tail_item: JsonSchemaObject | bool = obj.items
        elif isinstance(obj.unevaluatedItems, (JsonSchemaObject, bool)):
            tail_item = obj.unevaluatedItems
        else:
            tail_item = True
        items.extend(tail_item for _ in range(tuple_length - len(items)))
        return None if any(item is False for item in items) else items

    def _get_position_validation_keywords(
        self,
        schema: JsonSchemaObject | bool,  # ruff: ignore[boolean-type-hint-positional-argument]
        canonical_ref: Callable[[str], str],
    ) -> object:
        """Return the keywords that decide what a position accepts, following a position that is only a ``$ref``.

        A target that an external reference mapping imports, a missing target and a cycle stay the reference key.
        """
        keywords = _validation_keywords(
            schema.model_dump(exclude_unset=True, by_alias=True) if isinstance(schema, JsonSchemaObject) else schema,
            canonical_ref,
        )
        followed: set[str] = set()
        while (
            isinstance(keywords, dict)
            and keywords.keys() == {"$ref"}
            and (ref := keywords["$ref"]) not in followed
            and self._resolve_external_ref_mapping(ref) is None
            and self._ref_schema_exists(ref)
        ):
            followed.add(ref)
            target = self._load_inherited_schema_object(ref)
            keywords = _validation_keywords(
                False if target.is_boolean_schema_false else target.model_dump(exclude_unset=True, by_alias=True),
                self._ref_path,
            )
        return keywords

    def _get_array_constraints(self, obj: JsonSchemaObject, data_type: DataType) -> dict[str, Any]:
        """Return direct and boolean-schema array constraints."""
        constraints = self._get_constraint_values(obj, data_type)
        constraints.update(self._get_array_items_constraints(obj))
        return constraints

    @classmethod
    def _get_first_false_schema_index(cls, items: Sequence[JsonSchemaObject | bool] | None) -> int | None:
        """Return the first tuple/prefix item index that rejects every value."""
        if items is None:
            return None
        return next((index for index, item in enumerate(items) if item is False), None)

    def _has_prefix_items_tail_schema_or_boolean(self, obj: JsonSchemaObject) -> bool:  # noqa: PLR6301
        return bool(
            obj.prefixItems is not None
            and (
                obj.items is False
                or isinstance(obj.items, JsonSchemaObject)
                or obj.unevaluatedItems is False
                or isinstance(obj.unevaluatedItems, JsonSchemaObject)
                or any(isinstance(item, bool) for item in obj.prefixItems)
            )
        )

    def _suppress_array_length_constraints(self, constraints: dict[str, Any] | None, obj: JsonSchemaObject) -> None:
        if not constraints:
            return
        if self._is_fixed_length_tuple(obj):
            constraints.pop("minItems", None)
            constraints.pop("maxItems", None)

    def _resolve_array_field_required_nullable(self, obj: JsonSchemaObject) -> tuple[bool, bool | None]:
        facts = self._null_facts(obj, "array-field", has_default=obj.has_default)
        return bool(facts.required), facts.admits_null

    def _fallback_array_item_data_types(self) -> list[DataType]:
        return [
            self.data_type_manager.data_type(type="object")
            if self.data_type_manager.use_object_type
            else self.data_type_manager.get_data_type(Types.any)
        ]

    def _get_scalar_data_type_from_json_value(self, value: object) -> DataType | None:
        """Infer a normal Python type from a scalar JSON value."""
        if value is None:
            return self.data_type_manager.get_data_type(Types.null)
        if isinstance(value, bool):
            return self.data_type_manager.get_data_type(Types.boolean)
        if isinstance(value, int):
            return self.data_type_manager.get_data_type(Types.integer)
        if isinstance(value, float):
            return self.data_type_manager.get_data_type(Types.float)
        if isinstance(value, str):
            return self.data_type_manager.get_data_type(Types.string)
        return None

    def _get_data_type_from_json_value(self, value: object) -> DataType:
        """Infer a normal Python type from a JSON value."""
        scalar_type = self._get_scalar_data_type_from_json_value(value)
        if scalar_type is not None:
            return scalar_type
        if isinstance(value, list):
            item_types = [self._get_data_type_from_json_value(item) for item in value]
            return self.data_type(
                data_types=item_types or [self.data_type(type=ANY, import_=IMPORT_ANY)],
                is_list=True,
            )
        if isinstance(value, dict):
            value_types = [self._get_data_type_from_json_value(item) for item in value.values()]
            return self.data_type(
                data_types=value_types or [self.data_type(type=ANY, import_=IMPORT_ANY)],
                is_dict=True,
            )
        return self.data_type_manager.get_data_type(Types.any)

    def _const_is_literal(self, const: object) -> bool:
        """Return whether the output model writes a JSON Schema const value as a Literal (``const_type``)."""
        return const_type(const, bool_literals=self._output_model_context.supports_boolean_literals) == "literal"

    def _const_type_is_exact(self, const: object) -> bool:
        """Return whether the type of a const value admits that value only: its Literal, or None for null.

        A float, an object or, where the output writes no boolean Literal, a boolean is typed by its value type.
        """
        return const is None or self._const_is_literal(const)

    def _get_const_data_type(self, const: object, obj: JsonSchemaObject | None = None) -> DataType:
        """Return a DataType for a JSON Schema const value: its Literal (``const_type``) or its value's type.

        With the schema ``obj``, a Literal const that does not meet the constraints beside it is kept as written, with
        the unsatisfiable warning.
        """
        if self._const_is_literal(const):
            if obj is not None:
                self._check_literal_const(obj, const)
            return self.data_type(literals=[const])
        return self._get_data_type_from_json_value(const)

    def _partition_enum_values(self, enum_values: list[Any]) -> tuple[list[JsonSchemaLiteral], list[object], bool]:
        """Split enum values into literal (``is_literal_kind``) and non-literal values."""
        literal_values: list[JsonSchemaLiteral] = []
        non_literal_values: list[object] = []
        has_null = False
        bool_literals = self._output_model_context.supports_boolean_literals
        for enum_value in enum_values:
            if (kind := literal_value_kind(enum_value)) == "none":
                has_null = True
                non_literal_values.append(enum_value)
            elif is_literal_kind(kind, bool_literals=bool_literals):
                literal_values.append(enum_value)
            else:
                non_literal_values.append(enum_value)
        return literal_values, non_literal_values, has_null

    def _resolve_field_flag(self, obj: JsonSchemaObject, flag: Literal["readOnly", "writeOnly"]) -> bool:
        """Resolve a field flag (readOnly/writeOnly) from direct value, $ref, and compositions.

        Visits the $ref target, then the allOf, anyOf and oneOf members, depth first on an explicit stack; each schema
        node a $ref names is read once, in the contexts of the $refs leading to it.
        """
        visited: set[_TargetNode] = set()
        pending: list[tuple[JsonSchemaObject, tuple[str, ...]]] = [(obj, ())]
        while pending:
            schema, contexts = pending.pop()
            if getattr(schema, flag) is True:
                return True
            pending.extend(
                (sub, contexts)
                for schemas in (schema.oneOf, schema.anyOf, schema.allOf)
                for sub in reversed(schemas)
                if isinstance(sub, JsonSchemaObject)
            )
            if not (self.read_only_write_only_model_type and schema.ref):
                continue
            with ExitStack() as scope:
                for context in contexts:
                    scope.enter_context(self._inherited_ref_context(context))
                if (node := self._key_node(resolved_ref := self._ref_path(schema.ref))) in visited:
                    continue
                visited.add(node)
                pending.append((self._load_ref_schema_object(schema.ref), (*contexts, resolved_ref)))
        return False

    def _collect_inherited_fields_for_request_response(
        self,
        base_classes: list[Reference],
        active: frozenset[str] = frozenset(),
    ) -> list[DataModelFieldBase]:
        """Collect canonical inherited fields in declaration order with C3 winners."""
        if not base_classes:
            return []

        winners: dict[str, DataModelFieldBase] = {}
        unnamed_fields: list[DataModelFieldBase] = []
        for resolved_ref in self._linearize_inherited_schema_refs(base_classes):
            reference = self.model_resolver.add_ref(resolved_ref, resolved=True)
            own_names = self._raw_inherited_own_names_cache.get(resolved_ref)
            raw_fields = self._get_raw_inherited_fields(reference, active)
            if own_names is None:
                own_names = self._raw_inherited_own_names_cache.get(resolved_ref, frozenset())
            for field in raw_fields:
                if (field_name := _field_source_name(field)) is None:
                    unnamed_fields.append(field)
                elif field_name in own_names:
                    winners.setdefault(field_name, field)

        result: list[DataModelFieldBase] = []
        emitted_names: set[str] = set()
        for field_name in self._get_inherited_property_order(base_classes):
            if (field := winners.get(field_name)) is None:
                continue
            result.append(field)
            emitted_names.add(field_name)
        result.extend(field for name, field in winners.items() if name not in emitted_names)
        result.extend(unnamed_fields)
        if not self.force_optional_for_required_fields:
            required_names = self._get_inherited_required_names(base_classes)
            for field in result:
                if _field_source_name(field) in required_names:
                    field.required = True
                    field.use_default_with_required = (
                        self.apply_default_values_for_required_fields and field.has_default
                    )
        return result

    def _unregister_temporary_field_references(  # noqa: PLR6301
        self,
        fields: Iterable[DataModelFieldBase],
    ) -> None:
        """Keep temporary raw fields out of the live reverse-reference graph."""
        for field in fields:
            for data_type in field.data_type.all_data_types:
                data_type.unregister_reference()

    def _unregister_temporary_type(self, data_type: DataType | None) -> None:  # noqa: PLR6301
        """Keep a type built only to read facts from out of the live reverse-reference graph."""
        for item in () if data_type is None else data_type.all_data_types:
            item.unregister_reference()

    def _temporary_constraint_values(self, obj: JsonSchemaObject, data_type: DataType | None) -> dict[str, Any]:
        """Return the constraint values ``obj`` writes beside ``data_type``, a type built only to read them."""
        constraints = self._split_constraint_values(obj, data_type)[0]
        self._unregister_temporary_type(data_type)
        return constraints

    def _copy_unregistered_fields(
        self,
        fields: Iterable[DataModelFieldBase],
    ) -> list[DataModelFieldBase]:
        """Copy fields for temporary inheritance work without registering reverse edges."""
        return [self._copy_model_field(field, register_references=False) for field in fields]

    def _clear_inherited_field_caches(self) -> None:
        """Release temporary inheritance state after parsing."""
        for cached_fields in chain(
            self._raw_inherited_fields_cache.values(),
            self._request_response_fields.values(),
        ):
            self._unregister_temporary_field_references(cached_fields)
            for field in cached_fields:
                _detach_deferred_inherited_field_parents(field)
        self._raw_inherited_fields_cache.clear()
        self._raw_inherited_own_names_cache.clear()
        self._request_response_fields.clear()
        self._rw_model_field_facts_cache.clear()
        self._rw_model_references_cache.clear()
        self._rw_model_variant_requirement_cache.clear()
        self._rw_model_variant_references.clear()
        self._inherited_schema_cache.clear()
        self._inherited_schema_ancestor_cache.clear()
        self._inherited_schema_linearization_cache.clear()
        self._inherited_required_cache.clear()
        self._inherited_parent_property_cache.clear()
        self._ref_target_dumps.clear()
        self._unsatisfiable_schemas.clear()
        self._untaken_pattern_schemas.clear()
        if self.generate_schema_validators:
            self._property_count_rule_cache.clear()

    def _merge_inherited_field_overrides(
        self,
        inherited_fields: list[DataModelFieldBase],
        fields: list[DataModelFieldBase],
    ) -> list[DataModelFieldBase]:
        """Overlay declarations on inherited fields while resolving deferred partial types."""
        deduplicated: dict[str, DataModelFieldBase] = {}
        overridden_fields: dict[str, DataModelFieldBase] = {}
        for field in chain(inherited_fields, fields):
            if (key := _field_source_name(field)) is None:
                continue
            if inherited_field := deduplicated.get(key):
                overridden_fields[key] = inherited_field
            deduplicated[key] = field

        reserved_names = {field.name for field in deduplicated.values() if field.name}
        result: list[DataModelFieldBase] = []
        for key, field in deduplicated.items():
            inherited_field = overridden_fields.get(key)
            if (
                inherited_field is None
                or (
                    resolved_field := self._copy_inherited_field(
                        field,
                        inherited_field,
                        force_optional=self.force_optional_for_required_fields,
                        partial_merge_mode=self.allof_merge_mode,
                        register_references=False,
                        reserved_names=reserved_names,
                    )
                )
                is None
            ):
                result.append(field)
                continue
            self._prepare_required_inherited_field(
                resolved_field,
                inherited_field,
                overriding_field=field,
            )
            if class_name := field.__dict__.get(_DEFERRED_INHERITED_FIELD_KEY):
                self._apply_inherited_field_default(
                    resolved_field,
                    inherited_field,
                    class_name=class_name,
                )
            elif class_name := field.__dict__.get(_DEFERRED_INHERITED_CLASS_KEY):
                default_source = field
                if self.allof_merge_mode == AllOfMergeMode.All and not (
                    _RAW_SCHEMA_DEFAULT_KEY in field.__dict__
                    and field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] is not _RAW_SCHEMA_DEFAULT_UNDEFINED
                ):
                    default_source = inherited_field
                self._apply_inherited_field_default(
                    resolved_field,
                    default_source,
                    class_name=class_name,
                )
            result.append(resolved_field)
        return result

    def _prepare_required_inherited_field(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        overriding_field: DataModelFieldBase | None = None,
    ) -> None:
        """Remove inherited constructor defaults while retaining explicit child metadata."""
        explicit_extras = (
            overriding_field.__dict__.pop(_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY, overriding_field.extras)
            if overriding_field is not None
            else ()
        )
        self.data_model_type.prepare_required_inherited_field(
            field,
            inherited_field,
            explicit_extras=explicit_extras,
        )

    def _finalize_required_inherited_field(self, field: DataModelFieldBase) -> None:
        """Drop an inherited factory only after the child is known to be required."""
        self.data_model_type.finalize_required_inherited_field(field)

    def _get_raw_inherited_fields(
        self,
        reference: Reference,
        active: frozenset[str],
    ) -> list[DataModelFieldBase]:
        """Recursively materialize one raw schema's effective fields without live reverse edges."""
        resolved_ref = reference.path
        if (cached_fields := self._raw_inherited_fields_cache.get(resolved_ref)) is not None:
            return self._copy_unregistered_fields(cached_fields)
        if (cached_fields := self._request_response_fields.get(resolved_ref)) is not None:
            if resolved_ref not in self._raw_inherited_own_names_cache and SPECIAL_PATH_MARKER not in resolved_ref:
                schema = self._load_inherited_schema_object(resolved_ref)
                self._raw_inherited_own_names_cache[resolved_ref] = self._get_inline_property_names(schema)
            return self._copy_unregistered_fields(cached_fields)
        if resolved_ref in active or SPECIAL_PATH_MARKER in resolved_ref:
            return []

        schema = self._load_inherited_schema_object(resolved_ref)
        parent_refs = self._get_allof_parent_references(
            schema,
            defining_ref=resolved_ref,
        )
        parent_fields = self._collect_inherited_fields_for_request_response(
            parent_refs,
            active | {resolved_ref},
        )
        if isinstance(reference.source, DataModel):
            own_fields = self._copy_unregistered_fields(reference.source.fields)
        else:
            own_fields = self._parse_inherited_schema_fields(
                reference,
                schema,
                parent_refs,
                parent_fields,
            )
        self._raw_inherited_own_names_cache[resolved_ref] = frozenset(
            field_name for field in own_fields if (field_name := _field_source_name(field)) is not None
        )
        result = self._merge_inherited_field_overrides(parent_fields, own_fields)

        required_names = {
            field_name
            for field in parent_fields
            if field.required and (field_name := _field_source_name(field)) is not None
        }
        required_names.update(self._get_inline_required_names(schema))
        if not self.force_optional_for_required_fields:
            for field in result:
                if _field_source_name(field) in required_names:
                    field.required = True
                    field.use_default_with_required = (
                        self.apply_default_values_for_required_fields and field.has_default
                    )
                self._finalize_required_inherited_field(field)

        self._raw_inherited_fields_cache[resolved_ref] = tuple(result)
        return self._copy_unregistered_fields(result)

    def _collect_all_fields_for_request_response(
        self,
        fields: list[DataModelFieldBase],
        base_classes: list[Reference] | None,
    ) -> list[DataModelFieldBase]:
        """Collect parent → child fields, with each child declaration overriding its parent.

        The merge leaves out the root field of a root-model base, which types the base's value and is no property.
        """
        inherited_fields = self._collect_inherited_fields_for_request_response(base_classes or [])
        if not inherited_fields:
            return fields
        return self._merge_inherited_field_overrides(inherited_fields, fields)

    def _get_separate_model_fields(
        self,
        reference: Reference,
        fields: list[DataModelFieldBase],
        base_classes: list[Reference] | None,
    ) -> list[DataModelFieldBase] | None:
        """Collect Request/Response fields once when separate models are needed.

        The model's own fields, the types it writes beside them and the schema facts of its allOf bases decide;
        inherited fields are collected only for a model that may split. A stand-in never splits.
        """
        if self.read_only_write_only_model_type is None or self._is_base_stand_in(reference):
            return None
        if not self.dedupe_request_response_variants:
            all_fields = self._collect_all_fields_for_request_response(fields, base_classes)
            return all_fields if self._splits_into_rw_model_variants(reference, all_fields, base_classes) else None
        all_fields: list[DataModelFieldBase] | None = None
        if (
            self._rw_model_fields_split(fields)
            or any(self._ref_schema_has_rw_model_facts(base.path) for base in base_classes or () if base.path)
            or self._rw_model_writes_variant_beside_fields(reference.path)
        ):
            all_fields = self._collect_all_fields_for_request_response(fields, base_classes)
            if not self._splits_into_rw_model_variants(reference, all_fields, base_classes):
                all_fields = None
        self._check_rw_model_split(reference, splits=all_fields is not None)
        return all_fields

    def _rw_model_fields_split(self, fields: list[DataModelFieldBase]) -> bool:
        """Return whether fields split their model: one is flagged or, under request-response, references variants."""
        return any(field.read_only or field.write_only for field in fields) or (
            self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse
            and self._fields_reference_rw_model_variant(fields, "Request")
        )

    def _splits_into_rw_model_variants(
        self,
        reference: Reference,
        all_fields: list[DataModelFieldBase],
        base_classes: list[Reference] | None,
    ) -> bool:
        """Return whether the model of ``reference`` with these effective fields splits into read/write variants.

        Under request-response a model also splits where the types it writes beside its fields name a model with
        variants, where the effective fields of an allOf ancestor split it, or where the references to one of its allOf
        bases read variants, as those of a root model do: the variants replace the base's model, so nothing can inherit
        from it, even where the model's own declarations take the flags of the inherited fields away.
        """
        if self._rw_model_fields_split(all_fields):
            return True
        if self.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse:
            return False
        return (
            any(
                self._rw_model_fields_split(
                    self._get_raw_inherited_fields(
                        self.model_resolver.add_ref(resolved_ref, resolved=True), frozenset()
                    )
                )
                for resolved_ref in self._linearize_inherited_schema_refs(base_classes or [])
            )
            or self._rw_model_writes_variant_beside_fields(reference.path)
            or any(self._ref_schema_generates_variant(base.path, "Request") for base in base_classes or ())
        )

    def _rw_model_writes_variant_beside_fields(self, reference_path: str) -> bool:
        """Return whether a type the model at a key writes beside its fields names a model with variants.

        Under request-response, the types its schema validator rules name and the type of additional properties that
        TypedDict records as extra_items (``set_additional_properties``) name models as the fields do.
        """
        if self.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse:
            return False
        template_data = self.extra_template_data.get(reference_path, {})
        data_types: list[DataType] = []
        if _is_internal_schema_runtime_validation(rules := template_data.get("schema_runtime_validation")):
            data_types.extend(rules.data_types)
        if (source := self._output_model_context.additional_properties_type_source(template_data)) is not None:
            data_types.append(cast("DataType", source))
        return self._data_types_reference_rw_model_variant(data_types, "Request")

    def _should_generate_base_model(self, *, generates_separate_models: bool = False) -> bool:
        """Determine if Base model should be generated."""
        if self.read_only_write_only_model_type is None:
            return True
        if self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.All:
            return True
        return not generates_separate_models

    def _get_rw_model_field_facts(  # noqa: PLR6301
        self,
        fields: Iterable[DataModelFieldBase],
    ) -> tuple[bool, bool, bool, bool]:
        """Return read/write-only and retained-field facts in one pass."""
        has_read_only = False
        has_write_only = False
        has_non_read_only = False
        has_non_write_only = False
        for field in fields:
            if field.read_only:
                has_read_only = True
            else:
                has_non_read_only = True
            if field.write_only:
                has_write_only = True
            else:
                has_non_write_only = True
            if has_read_only and has_write_only and has_non_read_only and has_non_write_only:
                break
        return has_read_only, has_write_only, has_non_read_only, has_non_write_only

    def _get_ref_schema_rw_model_field_facts(
        self,
        resolved_ref: str,
    ) -> tuple[bool, bool, bool, bool] | None:
        """Return the read/write facts of the effective fields of the raw schema at ``resolved_ref``.

        Properties are read in allOf C3 order with their flags joined per name, and required names without a
        declaration count as unflagged fields; nothing is parsed. Without --dedupe-request-response-variants the facts
        of a temporary parse, ``None`` where it gives none (``_get_parsed_ref_schema_rw_model_field_facts``).
        """
        if (cached := self._rw_model_field_facts_cache.get(resolved_ref)) is not None:
            return cached
        if not self.dedupe_request_response_variants:
            return self._get_parsed_ref_schema_rw_model_field_facts(resolved_ref)
        flags: dict[str, tuple[bool, bool]] = {}
        reference = self.model_resolver.add_ref(resolved_ref, resolved=True)
        for ref in reversed(self._linearize_inherited_schema_refs([reference])):
            self._join_rw_model_property_flags(ref, flags)
        if not self.force_optional_for_required_fields:
            for name in self._get_inherited_required_names([reference]):
                flags.setdefault(name, (False, False))
        result = (
            any(read_only for read_only, _ in flags.values()),
            any(write_only for _, write_only in flags.values()),
            any(not read_only for read_only, _ in flags.values()),
            any(not write_only for _, write_only in flags.values()),
        )
        self._rw_model_field_facts_cache[resolved_ref] = result
        return result

    def _get_parsed_ref_schema_rw_model_field_facts(
        self,
        resolved_ref: str,
    ) -> tuple[bool, bool, bool, bool] | None:
        """Return the read/write facts of the fields a temporary parse of the schema at ``resolved_ref`` gives.

        The facts of 0.86.0, kept without --dedupe-request-response-variants: the parse registers the inline models it
        meets. A key whose parse is already in progress, reached again through the models that parse builds, has no
        facts, so the parse never re-enters itself; ``None`` also for a key that names no loadable schema.
        """
        if resolved_ref in self._rw_model_field_facts_in_progress:
            return None
        self._rw_model_field_facts_in_progress.add(resolved_ref)
        try:
            reference = self.model_resolver.add_ref(resolved_ref, resolved=True)
            fields = self._get_raw_inherited_fields(reference, frozenset())
        except (Error, OSError):
            return None
        finally:
            self._rw_model_field_facts_in_progress.discard(resolved_ref)
        result = self._get_rw_model_field_facts(fields)
        self._rw_model_field_facts_cache[resolved_ref] = result
        return result

    def _join_rw_model_property_flags(self, resolved_ref: str, flags: dict[str, tuple[bool, bool]]) -> None:
        """Join the readOnly/writeOnly flags of the properties the schema at a key declares, by name, into ``flags``.

        The properties of inline allOf members count as its own, as inherited fields collect them.
        """
        pending = [self._load_inherited_schema_object(resolved_ref)]
        with self._inherited_ref_context(resolved_ref):
            while pending:
                schema = pending.pop()
                for name, prop in (schema.properties or {}).items():
                    read_only, write_only = flags.get(name, (False, False))
                    if isinstance(prop, JsonSchemaObject):
                        read_only |= self._resolve_field_flag(prop, "readOnly")
                        write_only |= self._resolve_field_flag(prop, "writeOnly")
                    flags[name] = read_only, write_only
                pending.extend(sub for sub in schema.allOf if isinstance(sub, JsonSchemaObject) and not sub.ref)

    def _ref_schema_has_rw_model_facts(self, resolved_ref: str) -> bool:
        """Return whether inheriting the schema at ``resolved_ref`` can bring read/write facts into a model."""
        if self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
            return self._ref_schema_generates_variant(resolved_ref, "Request")
        facts = self._get_ref_schema_rw_model_field_facts(resolved_ref)
        return facts is not None and (facts[0] or facts[1])

    def _check_rw_model_split(self, reference: Reference, *, splits: bool) -> None:
        """Report a request/response split that differs from the schema graph's answer for the model's key.

        A model whose key names the schema object it is built from splits exactly when the key needs variants; any
        other model must decide before a reference to it reads its answer.
        """
        if (
            self.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse
            or _is_rw_model_variant_path(path := reference.path)
            or (mode := check_mode(CheckArea.SCHEMA_GRAPH)) is None
        ):
            return
        needs = (
            self._ref_schema_generates_variant(path, "Request")
            if str(location_of(reference)) == path
            else self._rw_model_variant_requirement_cache.get((path, "Request"), splits)
        )
        if needs == splits:
            return
        mode.violation(
            Error,
            f"the model at {redact_url(path)!r} {'splits' if splits else 'does not split'} into request/response "
            f"variants, but its references read {'a' if needs else 'no'} variant",
            kind="rw-split",
            path=redact_url(path),
            splits=splits,
        )

    def _ref_schema_generates_variant(
        self,
        resolved_ref: str,
        suffix: Literal["Request", "Response"],
    ) -> bool:
        """Check whether a referenced schema generates the requested read/write variant."""
        cache_key = resolved_ref, suffix
        if (cached := self._rw_model_variant_requirement_cache.get(cache_key)) is not None:
            return cached
        if _is_rw_model_variant_path(resolved_ref):
            return False
        if self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
            return self._request_response_ref_schema_generates_variant(resolved_ref)
        if (facts := self._get_ref_schema_rw_model_field_facts(resolved_ref)) is None:
            return False
        has_read_only, has_write_only, has_non_read_only, has_non_write_only = facts
        match suffix:
            case "Request":
                result = has_read_only and has_non_read_only
            case "Response":
                result = has_write_only and has_non_write_only
            case _:  # pragma: no cover
                return False
        self._rw_model_variant_requirement_cache[cache_key] = result
        return result

    def _request_response_ref_schema_generates_variant(
        self,
        resolved_ref: str,
    ) -> bool:
        """Resolve Request/Response variant reachability once per graph node."""
        pending = [resolved_ref]
        parent_by_ref: dict[str, str | None] = {resolved_ref: None}
        matched_ref: str | None = None
        while pending:
            current_ref = pending.pop()
            match self._rw_model_variant_requirement_cache.get((current_ref, "Request")):
                case True:
                    matched_ref = current_ref
                    break
                case False:
                    continue
            if _is_rw_model_variant_path(current_ref):
                continue
            has_direct_variant, nested_refs = self._get_rw_model_variant_graph_node(current_ref)
            if has_direct_variant:
                matched_ref = current_ref
                break
            for nested_ref in nested_refs:
                if nested_ref not in parent_by_ref:
                    parent_by_ref[nested_ref] = current_ref
                    pending.append(nested_ref)

        if matched_ref is None:
            for evaluated_ref in parent_by_ref:
                self._rw_model_variant_requirement_cache[evaluated_ref, "Request"] = False
                self._rw_model_variant_requirement_cache[evaluated_ref, "Response"] = False
            return False

        while matched_ref is not None:
            self._rw_model_variant_requirement_cache[matched_ref, "Request"] = True
            self._rw_model_variant_requirement_cache[matched_ref, "Response"] = True
            matched_ref = parent_by_ref[matched_ref]
        return True

    def _get_rw_model_variant_graph_node(self, resolved_ref: str) -> tuple[bool, tuple[str, ...]]:
        """Return whether the raw schema at a key declares a flagged property, and else the keys it references.

        A special path has no raw schema of its own: its split is the one recorded when its model is parsed. Without
        --dedupe-request-response-variants, the 0.86.0 node: the facts of a temporary parse of the key also split it,
        and the models its parsed fields reference are edges too.
        """
        if self.dedupe_request_response_variants:
            if SPECIAL_PATH_MARKER in resolved_ref:
                return False, ()
            return self._get_ref_schema_rw_model_reference_facts(resolved_ref)
        facts = self._get_ref_schema_rw_model_field_facts(resolved_ref)
        fields = self._raw_inherited_fields_cache.get(resolved_ref)
        if fields is None:
            fields = self._request_response_fields.get(resolved_ref, ())
        nested_refs = {
            reference.path: None
            for field in fields
            for data_type in field.data_type.all_data_types
            if (reference := data_type.reference) is not None
        }
        has_inline_variant, raw_refs = self._get_ref_schema_rw_model_reference_facts(resolved_ref)
        nested_refs.update(dict.fromkeys(raw_refs))
        return (facts is not None and (facts[0] or facts[1])) or has_inline_variant, tuple(nested_refs)

    def _fields_reference_rw_model_variant(
        self,
        fields: Iterable[DataModelFieldBase],
        suffix: Literal["Request", "Response"],
    ) -> bool:
        """Return whether any nested field reference needs the requested variant."""
        return self._data_types_reference_rw_model_variant((field.data_type for field in fields), suffix)

    def _data_types_reference_rw_model_variant(
        self,
        data_types: Iterable[DataType],
        suffix: Literal["Request", "Response"],
    ) -> bool:
        """Return whether any reference these types name, nested ones included, needs the requested variant."""
        for data_type in data_types:
            for nested_data_type in data_type.all_data_types:
                if (reference := nested_data_type.reference) and self._ref_schema_generates_variant(
                    reference.path,
                    suffix,
                ):
                    return True
        return False

    def _iter_rw_model_schema_children(
        self,
        schema: JsonSchemaObject,
        defining_ref: str,
        *,
        detect_inline_variant: bool,
        types_map: bool,
        extras: JsonSchemaObject | bool | None,
    ) -> Iterator[tuple[JsonSchemaObject, bool, bool]]:
        """Yield the children ``parse_item`` types, whether their inline models are generated and whether they merge.

        The inline allOf members merge into the model of the schema; the values of patternProperties are children only
        where ``types_map`` or where the patternProperties rules of --generate-schema-validators name them (not a value
        that is false written as an allOf, ``_is_false_all_of``: the rules forbid its keys), the additionalProperties
        the model types are ``extras``, and the item positions of an array are those array parsing types
        (``_get_array_item_schemas``), not the entries it leaves untyped, as the prefixItems of an array with no tail
        or boolean entry and the entries after a false one. A child typed by a customTypePath names no model
        (``_site_writes_custom_type``).
        """

        def site(item: object) -> TypeGuard[JsonSchemaObject]:
            return isinstance(item, JsonSchemaObject) and not self._site_writes_custom_type(item, defining_ref)

        pattern_properties = schema.patternProperties
        if self.generate_schema_validators and not types_map and pattern_properties:
            pattern_properties = {
                pattern: value for pattern, value in pattern_properties.items() if not _is_false_all_of(value)
            }
        mappings = (
            (schema.properties, pattern_properties)
            if self.generate_schema_validators
            else (pattern_properties if types_map else schema.properties,)
        )
        yield from (
            (item, detect_inline_variant, False)
            for mapping in mappings
            if mapping
            for item in mapping.values()
            if site(item)
        )
        if schema.is_array:
            with self._inherited_ref_context(defining_ref):
                positions = {id(item): item for item in self._get_array_item_schemas(schema)[0]}
            yield from ((item, detect_inline_variant, False) for item in positions.values() if site(item))
        if site(extras):
            yield extras, detect_inline_variant, False
        yield from ((item, detect_inline_variant, False) for item in chain(schema.oneOf, schema.anyOf) if site(item))
        yield from ((item, detect_inline_variant, True) for item in schema.allOf if isinstance(item, JsonSchemaObject))
        if self.generate_schema_validators:
            yield from ((item, detect_inline_variant, False) for item in self._iter_conditional_branches(schema))

    def _extra_items_type_reference_paths(self, extras: JsonSchemaObject) -> tuple[str, ...]:
        """Return the keys of the models the extra_items type TypedDict writes for ``extras`` names.

        The type is the one ``set_additional_properties`` writes, built by ``_build_lightweight_type``, which generates
        no model; it is built here only to read its references, so its warnings stay with the model's own build.
        """
        with catch_warnings():
            simplefilter("ignore")
            data_type = self._build_lightweight_type(extras)
        if data_type is None:
            return ()
        paths = tuple(item.reference.path for item in data_type.all_data_types if item.reference)
        self._unregister_temporary_type(data_type)
        return paths

    @cached_property
    def _writes_extra_items_type(self) -> bool:
        """Whether TypedDict writes the type of additional properties as extra_items, under --use-closed-typed-dict.

        That type is the lightweight type of additionalProperties (``_build_lightweight_type``), which names models
        and generates none.
        """
        return (
            self.use_closed_typed_dict and self._output_model_context.requires_additional_properties_reference_classes
        )

    def _get_ref_schema_rw_model_reference_facts(
        self,
        resolved_ref: str,
    ) -> tuple[bool, tuple[str, ...]]:
        """Return whether the raw schema at a key declares a flagged property, and else the keys its references name.

        Every schema is read as the one its model is built from (``_merged_ref_model_schemas``), and its flags, its
        references and its children all from that schema. The walk stops at the first flagged property: the
        references of a node that needs variants are never read (without --dedupe-request-response-variants it reads
        them all, as 0.86.0 did). A key that names no loadable schema has neither. patternProperties and propertyNames
        type the values and keys of an object map, propertyNames only where no patternProperties give the keys; a
        schema with properties or allOf, and each inline allOf member merged into it, builds a model whose fields hold
        properties alone, so its map keywords type nothing. A model types the additionalProperties its builder types,
        as its values where it is a map (``_schema_builds_map_model``) and beside its fields otherwise
        (``_rw_typed_additional_properties``). TypedDict also records those of the key's model and of each inline model
        with fields, unless an inline allOf member closes it (``_model_additional_properties``), as their extra_items
        type (``set_additional_properties``); that type generates no model, so the walk takes the models it names
        (``_extra_items_type_reference_paths``), and the model splits where one has variants. Those of an inline allOf
        member type something only where the allOf merges into a map.
        """
        if (cached := self._rw_model_references_cache.get(resolved_ref)) is not None:
            return cached
        try:
            root_schema = self._load_inherited_schema_object(resolved_ref)
        except (Error, OSError):
            return False, ()

        has_inline_variant = False
        references: dict[str, None] = {}

        def add_reference(ref: str | None) -> None:
            if ref and (nested_ref := self._resolve_rw_model_reference(ref, resolved_ref)):
                references.setdefault(nested_ref, None)

        stack: list[tuple[JsonSchemaObject, bool, bool | None]] = [(root_schema, True, None)]
        while stack:
            schema, detect_inline_variant, merged_into_map = stack.pop()
            if merged_into_map is None:
                is_key_model = schema is root_schema
                schema, model_schema = map(
                    self._read_false_all_of_values, self._merged_ref_model_schemas(schema, resolved_ref)
                )
                builds_map = self._schema_builds_map_model(model_schema, resolved_ref)
                types_map = not (model_schema.properties or model_schema.allOf)
                extras = self._rw_typed_additional_properties(schema, model_schema, resolved_ref, builds_map=builds_map)
                if (
                    (is_key_model or not builds_map)
                    and self._writes_extra_items_type
                    and isinstance(extra_items := self._model_additional_properties(model_schema), JsonSchemaObject)
                ):
                    with self._inherited_ref_context(resolved_ref):
                        references.update(dict.fromkeys(self._extra_items_type_reference_paths(extra_items)))
            else:
                model_schema = schema
                builds_map = merged_into_map
                extras = schema.additionalProperties if merged_into_map else None
                types_map = False
            if (
                detect_inline_variant
                and not has_inline_variant
                and self._schema_has_rw_model_fields(schema, resolved_ref)
            ):
                if self.dedupe_request_response_variants:
                    self._rw_model_references_cache[resolved_ref] = result = (True, ())
                    return result
                has_inline_variant = True
            for ref in (schema.ref, schema.recursiveRef, schema.dynamicRef):
                add_reference(ref)
            property_name_refs, property_name_schemas = (
                self._get_rw_property_name_sources(property_names=schema.propertyNames, defining_ref=resolved_ref)
                if types_map and not model_schema.patternProperties
                else ((), ())
            )
            for ref in property_name_refs:
                add_reference(ref)
            stack.extend((item, True, None) for item in property_name_schemas)
            stack.extend(
                (item, detect, builds_map if merged else None)
                for item, detect, merged in self._iter_rw_model_schema_children(
                    schema,
                    resolved_ref,
                    detect_inline_variant=detect_inline_variant,
                    types_map=types_map,
                    extras=extras,
                )
            )
        self._rw_model_references_cache[resolved_ref] = result = (has_inline_variant, tuple(references))
        return result

    def _rw_typed_additional_properties(
        self, schema: JsonSchemaObject, model_schema: JsonSchemaObject, defining_ref: str, *, builds_map: bool
    ) -> JsonSchemaObject | bool | None:
        """Return the additionalProperties the model built from ``model_schema`` types.

        The patternProperties rules of --generate-schema-validators type the value that
        ``_pattern_rule_additional_properties`` picks. Otherwise a map types those of the model as its values, except
        where patternProperties give its values: there they type only the keys no pattern matches, where
        ``_other_keys_schema`` says so. A model with fields types them in its typed extra field, where
        ``_typed_extra_field_schema`` writes it. Those of the model are read from ``schema``, like its other children.
        """
        with self._inherited_ref_context(defining_ref):
            if self._has_pattern_properties_validator(model_schema):
                if (rule_extras := self._pattern_rule_additional_properties(model_schema)) is None:
                    return None
                _, source, value = rule_extras
                return schema.additionalProperties if source is model_schema else value
            if builds_map:
                if model_schema.patternProperties and self._other_keys_schema(model_schema) is None:
                    return None
            elif self._typed_extra_field_schema(model_schema) is None:
                return None
        return schema.additionalProperties

    def _site_writes_custom_type(self, schema: JsonSchemaObject, defining_ref: str) -> bool:
        """Return whether ``parse_item`` types a site of ``schema`` by its customTypePath, which names no model.

        A $ref beside it is read merged with the schema it names, and the customTypePath types the site where the
        merged schema keeps no reference (``_FIELD_TABLE``).
        """
        if schema.custom_type_path and _merges_ref_siblings(schema):
            with self._inherited_ref_context(defining_ref):
                schema = self._merge_ref_with_schema(schema)
        return bool(schema.custom_type_path and not (schema.ref or schema.recursiveRef or schema.dynamicRef))

    def _merged_ref_model_schemas(
        self, schema: JsonSchemaObject, defining_ref: str
    ) -> tuple[JsonSchemaObject, JsonSchemaObject]:
        """Return the schema the walk reads for ``schema`` and the schema whose keywords decide what its keywords type.

        A $ref with sibling keywords decides as ``_merged_ref_sibling_schemas`` says. An allOf whose members redeclare
        a property with different schemas builds its model from the one object they merge into
        (``_merge_all_of_object``), which keeps their additionalProperties, so the walk reads that object for both.
        """
        schema, model_schema = self._merged_ref_sibling_schemas(schema, defining_ref)
        if schema is model_schema and schema.allOf:
            with self._inherited_ref_context(defining_ref):
                if merged := self._merge_all_of_object(schema):
                    return merged, merged
        return schema, model_schema

    def _merged_ref_sibling_schemas(
        self, schema: JsonSchemaObject, defining_ref: str
    ) -> tuple[JsonSchemaObject, JsonSchemaObject]:
        """Return the schema the walk reads for a $ref with sibling keywords and the schema that decides its keywords.

        The model of a $ref with sibling keywords is built from the $ref merged with them, so where map keywords sit
        beside a $ref outside allOf, that merged schema decides what they type. Where the target builds a map with map
        keywords and the merged schema builds none (``_schema_builds_map_model``), the model types none of the target's
        map keywords and never names the target's model, so the walk reads the merged schema in place of the reference
        to the target. Any other schema decides for itself.
        """
        if not _merges_ref_siblings(schema):
            return schema, schema
        with self._inherited_ref_context(defining_ref):
            if self._is_ref_circular(resolved_ref := self._ref_path(ref := self._lenient_ref(cast("str", schema.ref)))):
                return schema, schema
            target = self._schema_object(self._ref_target_dump(ref, resolved_ref)[0])
            target_builds_map = _has_map_keywords(target) and self._schema_builds_map_model(target, defining_ref)
            if not (target_builds_map or _has_map_keywords(schema)):
                return schema, schema
            merged = self._merge_ref_with_schema(schema)
        if target_builds_map and not self._schema_builds_map_model(merged, defining_ref):
            return merged, merged
        return schema, merged

    def _schema_builds_map_model(self, schema: JsonSchemaObject, defining_ref: str) -> bool:
        """Return whether the model built from a schema is an object map, whose map keywords type its keys and values.

        A schema with properties builds a model with fields. An allOf builds a map only where the schema has no
        patternProperties or propertyNames and every item is an object map (``_is_all_of_mapping_item``), as
        ``_merge_all_of_root_schema`` merges it; otherwise the allOf builds a model with fields or bases.
        """
        if schema.properties:
            return False
        if not schema.allOf:
            return True
        if schema.patternProperties or schema.propertyNames is not None:
            return False
        with self._inherited_ref_context(defining_ref):
            return all(
                isinstance(item, JsonSchemaObject)
                and self._is_all_of_mapping_item(self._load_ref_schema_object(item.ref) if item.ref else item)
                for item in schema.allOf
            )

    def _resolve_rw_model_reference(self, ref: str, defining_ref: str) -> str | None:
        """Resolve a type-producing ref while keeping configured imports as graph leaves."""
        with self._inherited_ref_context(defining_ref):
            if self._resolve_external_ref_mapping(ref) is not None:
                return None
            return self._ref_path(ref)

    def _get_rw_property_name_sources(
        self,
        *,
        property_names: JsonSchemaObject | bool | None,
        defining_ref: str,
    ) -> tuple[tuple[str, ...], tuple[JsonSchemaObject, ...]]:
        """Mirror propertyNames key parsing without following ignored nested shapes."""
        if not isinstance(property_names, JsonSchemaObject):
            return (), ()
        if property_names.has_ref_with_schema_keywords and not property_names.is_ref_with_nullable_only:
            with self._inherited_ref_context(defining_ref):
                property_names = self._merge_ref_with_schema(property_names)
        combined = tuple(
            item
            for item in chain(property_names.anyOf, property_names.oneOf, property_names.allOf)
            if isinstance(item, JsonSchemaObject)
        )
        if combined:
            return (), combined
        return ((property_names.ref,), ()) if property_names.ref else ((), ())

    def _schema_has_rw_model_fields(
        self,
        schema: JsonSchemaObject,
        defining_ref: str,
    ) -> bool:
        """Resolve read/write flags on fields exactly as object parsing does."""
        with self._inherited_ref_context(defining_ref):
            return any(
                isinstance(prop, JsonSchemaObject)
                and (self._resolve_field_flag(prop, "readOnly") or self._resolve_field_flag(prop, "writeOnly"))
                for prop in (schema.properties or {}).values()
            )

    def _preload_property_refs_for_rw_models(self, obj: JsonSchemaObject) -> None:
        """Preload property refs needed for readOnly/writeOnly model splitting."""
        if self.read_only_write_only_model_type is None or not obj.properties:
            return
        for prop in obj.properties.values():
            if isinstance(prop, JsonSchemaObject) and prop.ref and self._resolve_external_ref_mapping(prop.ref) is None:
                self._load_ref_schema_object(prop.ref)

    def _get_rw_model_variant_reference(
        self,
        base_reference: Reference,
        suffix: Literal["Request", "Response"],
        *,
        loaded: bool = False,
    ) -> Reference:
        """Create a variant reference, reserving request/response paths for recursive uses."""
        cache_key: tuple[str, str] | None = None
        if self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
            cache_key = base_reference.path, suffix
            if (reference := self._rw_model_variant_references.get(cache_key)) is not None:
                reference.loaded |= loaded
                return reference

        if self._ref_schema_exists(source_variant_ref := _get_rw_model_variant_source_path(base_reference, suffix)):
            self.model_resolver.add_ref(source_variant_ref, resolved=True)

        class_name_suffix = self.model_resolver.class_name_suffix
        base_name_without_index = base_reference.name.rstrip(digits)
        numeric_index = base_reference.name.removeprefix(base_name_without_index)
        variant_name = (
            f"{base_name_without_index[: -len(class_name_suffix)]}{suffix}{class_name_suffix}{numeric_index}"
            if class_name_suffix and base_name_without_index.endswith(class_name_suffix)
            else f"{base_reference.name}{suffix}"
        )
        yielding = self.model_resolver.is_yielding(base_reference)
        unique_name = self.model_resolver.get_class_name(
            variant_name,
            unique=True,
            skip_affix=True,
            preserve_name=True,
            yields=yielding,
        ).name
        reference = self.model_resolver.add(
            get_special_path(f"read-write-{suffix.lower()}", base_reference.path.split("/")),
            unique_name,
            class_name=False,
            unique=False,
            loaded=loaded,
            yields=yielding or None,
        )
        if cache_key is not None:
            self._rw_model_variant_references[cache_key] = reference
        return reference

    def _update_data_type_ref_for_variant(
        self,
        data_type: DataType,
        suffix: Literal["Request", "Response"],
    ) -> None:
        """Recursively update data type references to point to variant models."""
        for nested_data_type in data_type.all_data_types:
            if (reference := nested_data_type.reference) and self._ref_schema_generates_variant(reference.path, suffix):
                base_reference = self.model_resolver.add_ref(reference.path, resolved=True)
                variant_ref = self._get_rw_model_variant_reference(base_reference, suffix)
                self._graph_builder.replace_data_type_ref(nested_data_type, variant_ref)

    def _update_field_refs_for_variant(
        self,
        model_fields: list[DataModelFieldBase],
        suffix: Literal["Request", "Response"],
    ) -> list[DataModelFieldBase]:
        """Update field references in model_fields to point to variant models.

        For Request models, refs should point to Request variants.
        For Response models, refs should point to Response variants.
        """
        if self.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse:
            return model_fields
        for field in model_fields:
            if field.data_type:  # pragma: no branch
                self._update_data_type_ref_for_variant(field.data_type, suffix)
        return model_fields

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
        self._update_data_type_ref_for_variant(additional_type, suffix)
        reference_classes = {
            data_type.reference.path for data_type in additional_type.all_data_types if data_type.reference
        }
        self._output_model_context.store_additional_properties_type(
            metadata,
            _make_referencing_type_expression(additional_type) if reference_classes else additional_type.type_hint,
            reference_classes,
            imports=tuple(additional_type.all_imports),
        )
        for data_type in additional_type.all_data_types:
            data_type.unregister_reference()

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

        pattern_properties: list[PatternPropertiesRule] = []
        for rule in source.pattern_properties:
            copied_patterns: list[tuple[str, DataType]] = []
            for pattern, data_type in rule.pattern_properties:
                copied_type = self._copy_model_type(data_type)
                self._update_data_type_ref_for_variant(copied_type, suffix)
                copied_patterns.append((pattern, copied_type))
            copied_additional_type = (
                self._copy_model_type(rule.additional_property_type)
                if rule.additional_property_type is not None
                else None
            )
            if copied_additional_type is not None:
                self._update_data_type_ref_for_variant(copied_additional_type, suffix)
            pattern_properties.append(
                type(rule)(
                    declared_properties=tuple(name for name in rule.declared_properties if name in available_names),
                    pattern_properties=tuple(copied_patterns),
                    rejected_patterns=rule.rejected_patterns,
                    additional_property_type=copied_additional_type,
                    allow_unmatched=rule.allow_unmatched,
                )
            )

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
        if not model_fields and self.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse:
            return
        model_fields = [self._copy_model_field(field) for field in model_fields]
        reference = self._get_rw_model_variant_reference(base_reference, suffix, loaded=True)
        self._update_field_refs_for_variant(model_fields, suffix)
        self._record_schema_object(reference, obj, variant_of=base_reference)
        self._update_variant_additional_properties_metadata(reference.path, obj, suffix)
        self._copy_schema_runtime_validation_for_variant(
            base_reference.path,
            reference.path,
            model_fields,
            suffix,
            obj=obj,
            source_fields=source_fields,
        )
        model = self._create_data_model(
            model_type=data_model_type_class,
            reference=reference,
            fields=model_fields,
            custom_base_class=self._resolve_base_class(reference.name, obj.custom_base_path),
            custom_template_dir=self.custom_template_dir,
            extra_template_data=self.extra_template_data,
            path=self.current_source_path,
            description=obj.description if self.use_schema_description else None,
            nullable=self._object_nullable(obj),
            keyword_only=self.keyword_only,
            treat_dot_as_module=self.treat_dot_as_module,
            dataclass_arguments=self.dataclass_arguments,
        )
        self._graph_builder.register_model(model)

    def _create_request_response_models(
        self,
        reference: Reference,
        obj: JsonSchemaObject,
        all_fields: list[DataModelFieldBase],
        own_fields: list[DataModelFieldBase],
        data_model_type_class: type[DataModel],
    ) -> None:
        """Generate Request and Response model variants."""
        facts = self._get_rw_model_field_facts(all_fields)
        has_read_only, has_write_only, has_non_read_only, has_non_write_only = facts
        variants: list[tuple[Literal["Request", "Response"], list[DataModelFieldBase]]] = []
        match self.read_only_write_only_model_type:
            case ReadOnlyWriteOnlyModelType.RequestResponse:
                if not self.dedupe_request_response_variants:
                    self._rw_model_field_facts_cache[reference.path] = facts
                self._rw_model_variant_requirement_cache[reference.path, "Request"] = True
                self._rw_model_variant_requirement_cache[reference.path, "Response"] = True
                self._request_response_fields[reference.path] = tuple(self._copy_unregistered_fields(all_fields))
                self._raw_inherited_own_names_cache[reference.path] = frozenset(
                    field_name for field in own_fields if (field_name := _field_source_name(field)) is not None
                )
                variants.extend((
                    ("Request", [field for field in all_fields if not field.read_only]),
                    ("Response", [field for field in all_fields if not field.write_only]),
                ))
            case _:
                if has_read_only and has_non_read_only:
                    variants.append(("Request", [field for field in all_fields if not field.read_only]))
                if has_write_only and has_non_write_only:
                    variants.append(("Response", [field for field in all_fields if not field.write_only]))
        for suffix, model_fields in variants:
            self._create_variant_model(
                reference,
                suffix,
                model_fields,
                obj,
                data_model_type_class,
                source_fields=all_fields,
            )

    def _build_neutral_object_field(  # noqa: PLR0913
        self,
        *,
        field_name: str | None,
        required: bool,
        field_type: DataType,
        alias: str | list[str] | None,
        original_field_name: str | None,
        effective_default: Any = None,
        effective_has_default: bool | None = None,
        use_default_with_required: bool = False,
        class_name: str | None = None,
    ) -> DataModelFieldBase:
        """Build a field without schema-owned constraints or metadata."""
        single_alias, validation_aliases = self._split_field_alias(alias)
        serialization_alias = (
            self.get_serialization_alias(original_field_name, field_name, class_name)
            if original_field_name is not None and field_name is not None
            else None
        )
        return self._data_model_field_constructor(
            name=field_name,
            default=effective_default,
            data_type=field_type,
            required=required,
            alias=single_alias,
            validation_aliases=validation_aliases,
            serialization_alias=serialization_alias,
            strip_default_none=self.strip_default_none,
            order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
            use_annotated=self.use_annotated,
            use_serialize_as_any=self.use_serialize_as_any,
            use_field_description=self.use_field_description,
            use_field_description_example=self.use_field_description_example,
            use_inline_field_description=self.use_inline_field_description,
            use_default_kwarg=self.use_default_kwarg,
            original_name=original_field_name,
            has_default=effective_has_default is True,
            use_frozen_field=self.use_frozen_field,
            use_serialization_alias=self.use_serialization_alias,
            use_default_factory_for_optional_nested_models=self.use_default_factory_for_optional_nested_models,
            use_default_with_required=use_default_with_required,
            **self._data_model_field_common_kwargs(),
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
    ) -> DataModelFieldBase:
        """Build an output field using the shared JSON Schema field policy."""
        if field is None:
            return self._build_neutral_object_field(
                field_name=field_name,
                required=required,
                field_type=field_type,
                alias=alias,
                original_field_name=original_field_name,
                effective_default=effective_default,
                effective_has_default=effective_has_default,
                use_default_with_required=use_default_with_required,
                class_name=class_name,
            )

        single_alias, validation_aliases = self._split_field_alias(alias)
        serialization_alias = (
            self.get_serialization_alias(original_field_name, field_name, class_name)
            if original_field_name is not None and field_name is not None
            else None
        )
        default_value = effective_default if effective_has_default is not None else field.default
        has_default = effective_has_default if effective_has_default is not None else field.has_default
        skip_constraints = (
            self._should_skip_root_field_constraints_for_multiple_types(field)
            and not self._has_shared_numeric_union_constraints(field)
        ) or (isinstance(field.type, list) and bool(self._get_array_union_non_array_types(field)))
        constraints = None
        untaken: dict[str, Any] = {}
        if not skip_constraints and self.is_constraints_field(field):
            constraints, untaken = self._split_constraint_values(field, field_type)
            for key in property_consumed_keywords(
                self.data_type_manager.CONSTRAINED_TYPE_CONSUMED_KEYS, field_type.type
            ):
                constraints.pop(key, None)
        if constraints is not None and self.field_constraints and field.format == "hostname":
            constraints["pattern"] = self.data_type_manager.HOSTNAME_REGEX
        if (
            not skip_constraints
            and (field_type.is_dict or field_type.is_mapping)
            and (property_count_constraints := self._get_property_count_constraints(field))
        ):
            constraints = constraints or {}
            constraints.update(property_count_constraints)
        dropped_counts = self._dropped_property_counts(field, field_type)
        if not skip_constraints and (array_items_constraints := self._get_array_items_constraints(field)):
            constraints = constraints or {}
            constraints.update(array_items_constraints)
        self._suppress_array_length_constraints(constraints, field)
        nullable = self._null_facts(field, "field", required=required, has_default=has_default).admits_null
        extras = self.get_field_extras(field)
        read_only = self._resolve_field_flag(field, "readOnly")
        write_only = self._resolve_field_flag(field, "writeOnly")
        model_field = self._data_model_field_constructor(
            name=field_name,
            default=default_value,
            data_type=field_type,
            required=required,
            alias=single_alias,
            validation_aliases=validation_aliases,
            serialization_alias=serialization_alias,
            constraints=constraints,
            nullable=nullable,
            strip_default_none=self.strip_default_none,
            order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
            extras=extras,
            use_annotated=self.use_annotated,
            use_serialize_as_any=self.use_serialize_as_any,
            use_field_description=self.use_field_description,
            use_field_description_example=self.use_field_description_example,
            use_inline_field_description=self.use_inline_field_description,
            use_default_kwarg=self.use_default_kwarg,
            original_name=original_field_name,
            has_default=has_default,
            type_has_null=self._type_has_null(field),
            read_only=read_only,
            write_only=write_only,
            use_frozen_field=self.use_frozen_field,
            use_serialization_alias=self.use_serialization_alias,
            use_default_factory_for_optional_nested_models=self.use_default_factory_for_optional_nested_models,
            use_default_with_required=use_default_with_required,
            **self._data_model_field_common_kwargs(),
        )
        if self.model_resolver.default_value_overrides:
            model_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] = (
                field.default if field.has_default else _RAW_SCHEMA_DEFAULT_UNDEFINED
            )
        if constraints is not None:
            self._mark_type_dependent_constraints(model_field, field)
        self._mark_untaken_constraints(
            model_field,
            {**untaken, **dropped_counts},
            lambda minimum: (
                self.data_type_manager.constraint_keywords(
                    field_type,
                    self.target_python_version.version_key,
                    number_values=True,
                    target_model_version=minimum,
                )
                - dropped_counts.keys()
            ),
        )
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
        """Build scalar types while retaining constraints owned by their actual context."""
        if self._null_facts(obj, "scalar").rule == "legacy-nullable-as-type-list":
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
                    for type_ in schema_type
                    if type_ != "null"
                ],
            )
        data_type = self._get_constrained_data_type(obj, schema_type, field_constraints=field_constraints)
        if self._null_facts(obj, "scalar").admits_null:
            return build_union(self.data_type, [data_type], nullable=True)
        return data_type

    def _get_constrained_data_type(self, obj: JsonSchemaObject, type_: str, *, field_constraints: bool) -> DataType:
        """Map one type of ``obj`` and its format, with the constraints ``obj`` writes beside it.

        Under ``field_constraints`` the field writes the constraints, so the type takes none of them.
        """
        format_ = obj.format or "default"
        types = self._get_type_with_mappings(type_, format_)
        kwargs: dict[str, JsonSchemaDataTypeKwargValue] = {
            **_get_data_type_constraint_kwargs(obj, types),
            **self._get_format_type_kwargs(type_, format_, types),
        }
        data_type = self.data_type_manager.get_data_type(types, field_constraints=field_constraints, **kwargs)
        if (
            not field_constraints
            and (dropped := self.data_type_manager._non_finite_multiple_of(types, kwargs)) is not None  # noqa: SLF001
        ):
            self._graph_builder.record_dropped_multiple_of(data_type, dropped)
        return data_type

    def _get_type_array_data_type(self, types: list[str], data_types: list[DataType]) -> DataType:
        """Return the type of a type array from the types of its members other than null.

        The dict of an array of object and null is the type itself, admitting the null, so the property counts of
        the object stay on it, under ``--use-single-dict-for-object-maps``.
        """
        match data_types:
            case []:
                return self.data_type_manager.get_data_type(Types.null)
            case [map_type] if (
                self.use_single_dict_for_object_maps and map_type.is_dict and set(types) <= {"object", "null"}
            ):
                return _with_null(map_type) if "null" in types else map_type
        return build_union(self.data_type, data_types, nullable=len(data_types) != len(types))

    def _resolve_external_ref_mapping(self, ref: str) -> tuple[str, str] | None:
        """Resolve a ref and return mapped package + fragment if configured; a ref with an edge maps as its key does."""
        if not self._external_ref_mapping:
            return None
        if isinstance(ref, ResolvedRef):
            return self._ref_key(ref).mapping

        def _resolve_lookup_key(file_part: str) -> str:
            if is_url(file_part):
                return file_part
            path = Path(file_part)
            if path.is_absolute():
                return str(path.resolve())
            base_path = self.model_resolver.current_base_path or self.base_path
            return str((base_path / path).resolve())

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
            lookup_key = _resolve_lookup_key(file_part)
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
        return self._get_emitted_python_type_path(
            _validate_schema_python_import_path(f"{module}.{type_name}", "x-python-import")
        )

    def _get_target_runtime_symbol(self, module: str, qualname: str) -> PythonTypeRuntimeSymbol:
        """Bind ``module.qualname`` through ``import module``, which import overrides cannot move.

        The type the target must have is therefore checked here rather than in the emitted imports.
        """
        from datamodel_code_generator._python_type_annotation import (  # ruff: ignore[import-outside-top-level]
            PythonTypeRuntimeSymbol,
        )

        parts = tuple(qualname.split("."))
        self._get_target_python_type_path(f"{module}.{parts[0]}")
        return PythonTypeRuntimeSymbol(module, parts)

    def _get_x_python_runtime_symbol(self, x_python_import: dict[str, Any]) -> PythonTypeRuntimeSymbol | None:
        """Retain nested runtime module boundaries."""
        if (qualname := x_python_import.get("qualname")) is None:
            return None
        return self._get_target_runtime_symbol(
            x_python_import["module"], _validate_schema_python_import_path(qualname, "x-python-import qualname")
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

            expression: PythonTypeExpr = self._get_target_runtime_symbol(path["module"], path["qualname"])
            for field in path["fields"]:
                expression = PythonTypeModelField(
                    expression, field["name"], tuple(field["arguments"]), field["pydantic"]
                )
            self._generic_python_import_expressions[resolved_ref] = expression
            return expression
        from datamodel_code_generator._python_type_annotation import (  # ruff: ignore[import-outside-top-level]
            PythonTypeQualifiedName,
            PythonTypeRuntimeSymbol,
            rewrite_python_type_expr,
        )

        expression = cast("PythonTypeExpr", self._get_x_python_type(self._schema_object(x_python_import)))
        if symbol_definitions := x_python_import.get("symbols"):
            symbols = {
                name: PythonTypeRuntimeSymbol(
                    symbol["module"],
                    tuple(symbol["qualname"].split(".")),
                )
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
            type_excludes_member_null=self.intersects_allof_keywords,
            read_nullable_beside_type_list=self.config.read_nullable_beside_type_list,
        )

    def _type_has_null(self, obj: JsonSchemaObject) -> bool:
        """Return ``JsonSchemaObject.type_has_null``, false where a ``type`` excludes the null of its members.

        With --intersect-allof-keywords a ``type`` that is neither null nor a list holding null, beside a null member
        of its ``anyOf`` or ``oneOf``, admits no null (row ``type-excludes-member-null``).
        """
        if (
            self.intersects_allof_keywords
            and resolve_null_facts(_type_null_sources(obj), "schema-type", self._null_mode).rule
            == "type-excludes-member-null"
        ):
            return False
        return obj.type_has_null

    def _null_facts(
        self,
        obj: JsonSchemaObject,
        site: NullSite,
        *,
        required: bool = False,
        has_default: bool = False,
        parts_null: bool = False,
    ) -> NullFacts:
        """Return what ``site`` reads from where the null of ``obj`` comes from."""
        return resolve_null_facts(
            _null_sources(obj, site, self._type_has_null),
            site,
            self._null_mode,
            required=required,
            has_default=has_default,
            parts_null=parts_null,
        )

    def _object_nullable(self, obj: JsonSchemaObject) -> bool:
        """Return whether an object model's schema admits null, by its types or by nullable under strict nullability."""
        return bool(self._null_facts(obj, "object-model").admits_null)

    def _cache_ref_data_type_facts(self, resolved_ref: str, obj: JsonSchemaObject) -> None:
        self._ref_data_type_facts[resolved_ref] = (
            obj.extras.get("x-python-import"),
            bool(self._null_facts(obj, "ref-target").admits_null),
        )
        self._false_schema_refs = _update_false_schema_refs(
            self._false_schema_refs,
            resolved_ref,
            is_false=obj.is_boolean_schema_false,
        )

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

        key = self._ref_key(ref, embedded=False)
        self._check_ref_key("get_ref_data_type", ref, key, embedded=False)
        ref = keep_resolved_ref(ref, key.spelling)
        resolved_ref = key.key
        if (facts := self._ref_data_type_facts.get(resolved_ref)) is None:
            ref_schema = self._load_ref_schema_object(ref)
            facts = (
                ref_schema.extras.get("x-python-import"),
                bool(self._null_facts(ref_schema, "ref-target").admits_null),
            )
            self._ref_data_type_facts[resolved_ref] = facts
            self._false_schema_refs = _update_false_schema_refs(
                self._false_schema_refs,
                resolved_ref,
                is_false=ref_schema.is_boolean_schema_false,
            )
        x_python_import, is_optional = facts
        if isinstance(x_python_import, dict) and (full_path := self._get_x_python_import_path(x_python_import)):
            if runtime_symbol := self._get_x_python_import_expression(x_python_import, resolved_ref):
                from datamodel_code_generator._python_type_annotation import (  # ruff: ignore[import-outside-top-level]
                    render_python_type_expr,
                )

                bound_type = self._bind_python_type(runtime_symbol)
                return self.data_type(type=render_python_type_expr(bound_type.expression), python_type=bound_type)
            import_ = self._get_x_python_import(full_path)
            self._import_ledger.append(self.imports, import_, _X_PYTHON_IMPORT)
            return self.data_type(type=import_.import_, import_=import_, alias=import_.alias)
        if not self._dynamic_anchors or (
            (reference := self._dynamic_specialization_reference(ref, resolved_ref)) is None
        ):
            reference = self._add_ref(ref)
        data_type = self.data_type(reference=reference, is_optional=is_optional)
        if is_optional and self.omit_redundant_reference_null:
            self._graph_builder.mark_target_null(data_type)
        return data_type

    def _model_additional_properties(self, obj: JsonSchemaObject) -> JsonSchemaObject | bool | None:
        """Return the additionalProperties of the model built from ``obj``.

        An inline allOf member with additionalProperties: false and no patternProperties closes the combined model.
        """
        if (
            obj.allOf
            and obj.additionalProperties is not False
            and any(
                source.additionalProperties is False and not source.patternProperties
                for source in self._iter_schema_validation_sources(obj, include_references=False)
            )
        ):
            return False
        return obj.additionalProperties

    def set_additional_properties(self, path: str, obj: JsonSchemaObject) -> None:
        """Set additional properties flag in extra template data.

        For TypedDict with PEP 728 support:
        - additionalProperties: false -> closed=True
        - additionalProperties: { type: X } -> extra_items=X

        This is controlled by use_closed_typed_dict option. When disabled,
        the additionalProperties constraint is not converted to PEP 728 syntax.
        The additionalProperties are those of the model (``_model_additional_properties``).
        """
        additional_properties = self._model_additional_properties(obj)
        if isinstance(additional_properties, bool):
            if not self.use_closed_typed_dict:
                return
            self._output_model_context.store_additional_properties_value(
                self.extra_template_data[path],
                value=additional_properties,
                target_python_version=self.target_python_version,
            )
        elif isinstance(additional_properties, JsonSchemaObject):
            # A schema-valued additionalProperties still means extra keys are accepted.
            # Keep typed extra validation out of this bugfix; PEP 728 TypedDict uses
            # output-owned type metadata below when explicitly enabled.
            self._output_model_context.store_additional_properties_value(
                self.extra_template_data[path],
                value=True,
            )
            if not self.use_closed_typed_dict:
                return
            additional_props_type = self._build_lightweight_type(
                self._read_false_all_of_values(additional_properties, lightweight=True)
            )
            if additional_props_type:  # pragma: no branch
                reference_classes = (
                    {
                        data_type.reference.path
                        for data_type in additional_props_type.all_data_types
                        if data_type.reference
                    }
                    if self._output_model_context.requires_additional_properties_reference_classes
                    else None
                )
                self._output_model_context.store_additional_properties_type(
                    self.extra_template_data[path],
                    _make_referencing_type_expression(additional_props_type)
                    if reference_classes
                    else additional_props_type.type_hint,
                    reference_classes,
                    imports=tuple(additional_props_type.all_imports),
                    target_python_version=self.target_python_version,
                )

    def set_unevaluated_properties(self, path: str, obj: JsonSchemaObject) -> None:
        """Set unevaluated properties flag in extra template data."""
        if isinstance(obj.unevaluatedProperties, bool):
            self.extra_template_data[path]["unevaluatedProperties"] = obj.unevaluatedProperties
        elif isinstance(obj.unevaluatedProperties, JsonSchemaObject) and obj.additionalProperties is None:
            # Schema-valued unevaluatedProperties allows extra keys. Its value
            # schema would require generated typed-extra validation, which is out
            # of scope for this bugfix.
            self.extra_template_data[path]["unevaluatedProperties"] = True

    def set_title(self, path: str, obj: JsonSchemaObject) -> None:
        """Set title in extra template data."""
        if obj.title:
            self.extra_template_data[path]["title"] = obj.title

    def set_schema_id(self, path: str, obj: JsonSchemaObject) -> None:
        """Set $id in extra template data."""
        if obj.id:
            self.extra_template_data[path]["schema_id"] = obj.id

    def _record_schema_location(
        self,
        reference: Reference,
        location: SchemaLocation | None,
        *,
        variant_of: Reference | None = None,
    ) -> None:
        """Record where the model of ``reference`` is built from and the frames of the models building it (L1).

        Both are kept on the Reference object, so they stay valid when later passes change ``Reference.path``. The
        frames are innermost first; each cell holds the reference of the model its builder built, once it returns. A
        read or write variant stands for its base model in the frames, so it takes the base's owner.
        """
        _set_schema_location(reference, location)
        reference.__dict__[SCHEMA_OWNER_FRAMES_KEY] = BuildFrames(
            reference if variant_of is None else variant_of, tuple(reversed(self._model_frames))
        )

    def _record_schema_object(
        self,
        reference: Reference,
        obj: JsonSchemaObject,
        *,
        variant_of: Reference | None = None,
    ) -> None:
        """Record the schema object the model of ``reference`` is built from, and take its template data."""
        self._record_schema_location(reference, obj.schema_location, variant_of=variant_of)
        self._record_derived_from(reference, obj)
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)

    def _record_derived_from(self, reference: Reference, obj: JsonSchemaObject) -> None:
        """Record the target a site schema is built from (``_derived_site``) on the Reference of its model."""
        if (ref := obj.__dict__.get(_DERIVED_FROM_REF_KEY)) is None:
            return
        data_type = self.get_ref_data_type(ref)
        reference.__dict__[_DERIVED_FROM_KEY] = data_type.reference
        self._unregister_temporary_type(data_type)

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
                    # Strip the x- prefix
                    model_extras[k.lstrip("x-")] = self._externalize_schema_extra(k, v)
        if model_extras:
            self.extra_template_data[path]["model_extras"] = model_extras

    def _get_python_type_flags(self, obj: JsonSchemaObject) -> dict[str, bool]:
        """Get container type flags from x-python-type extension.

        Returns a dict with flags like is_set, is_frozen_set, is_mapping, is_sequence
        that can be passed to data_type() to override the default container type.

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

        type_to_flag: dict[str, dict[str, bool]] = {
            "Set": {"is_set": True},
            "set": {"is_set": True},
            "FrozenSet": {"is_frozen_set": True},
            "frozenset": {"is_frozen_set": True},
            "Mapping": {"is_mapping": True},
            "MutableMapping": {"is_mapping": True},
            "Sequence": {"is_sequence": True},
            "MutableSequence": {"is_sequence": True},
            "AbstractSet": {"is_frozen_set": True},
            "MutableSet": {"is_set": True},
        }

        base_type = python_type_expr_base_name(python_type)
        if base_type in type_to_flag:
            return type_to_flag[base_type]

        if is_union_python_type_expr(python_type):
            for argument in python_type_expr_arguments(python_type):
                arg_base = python_type_expr_base_name(argument)
                if arg_base in type_to_flag:
                    return type_to_flag[arg_base]

        return {}

    def _is_compatible_python_type(self, schema_type: str | None, python_type: PythonTypeExpr) -> bool:
        """Check if x-python-type is compatible with the JSON Schema type."""
        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            is_union_python_type_expr,
            iter_python_type_expr_names,
            python_type_expr_base_name,
        )

        base_type = python_type_expr_base_name(python_type)
        if base_type in self.PYTHON_TYPE_OVERRIDE_ALWAYS:
            return False
        if any(name in self.PYTHON_TYPE_OVERRIDE_ALWAYS for name in iter_python_type_expr_names(python_type)):
            return False
        if schema_type is None:
            return not is_union_python_type_expr(python_type)
        if base_type in _PYTHON_UNION_BASE_TYPES:  # pragma: no cover
            return True
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

    def _resolve_type_import(self, type_name: str) -> Import | None:
        """Resolve a type through target-stable metadata, never host imports."""
        from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
            PythonTypeUnavailableError,
            get_python_type_import_path,
        )

        try:
            import_path = get_python_type_import_path(type_name, self.target_python_version.version_key)
        except PythonTypeUnavailableError as exc:
            raise Error(str(exc)) from exc
        if import_path:
            return Import.from_full_path(import_path)
        return None

    def _is_target_python_builtin_type(self, type_name: str) -> bool:
        """Recognize builtins against the target version, never the host runtime."""
        from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
            is_python_builtin_type_name,
        )

        return is_python_builtin_type_name(type_name, self.target_python_version.version_key)

    def _resolve_qualified_type_import(self, qualified_name: str) -> Import:
        """Resolve known stdlib paths against the target without host imports."""
        return _qualified_python_type_import(self._get_target_python_type_path(qualified_name))

    def _resolve_type_import_from_defs(self, type_name: str) -> Import | PythonTypeExpr | None:
        """Resolve import for a type name from $defs with x-python-import."""
        try:
            spelled = f"#/$defs/{type_name}"
            ref = self._derived_ref(
                spelled, self._current_document_root(), "x-python-type", spelled, RefOrigin.TYPE_DEFINITION
            )
            ref_schema = self._load_ref_schema_object(ref)
            x_python_import = ref_schema.extras.get("x-python-import")
            if isinstance(x_python_import, dict) and (full_path := self._get_x_python_import_path(x_python_import)):
                return self._get_x_python_import_expression(
                    x_python_import, self._ref_path(ref)
                ) or self._get_x_python_import(full_path)
        except Error:
            raise
        except Exception:  # noqa: BLE001, S110
            pass
        return None

    def _bind_python_type(self, expression: PythonTypeExpr) -> BoundPythonType:
        """Bind names/imports without returning to runtime-dependent text parsing.

        ``expression`` was parsed only at the external JSON Schema boundary.
        Keep the IR through internal stages: rendering and reparsing here would
        incorrectly make the host AST/tokenizer an authority for target syntax.
        """
        # Binding is exclusive to x-python-type. Import it only after that
        # extension is present so ordinary schema generation keeps its fast path.
        from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
            PythonTypeBoundName,
            PythonTypeExpr,
            PythonTypeName,
            PythonTypeQualifiedName,
            PythonTypeRuntimeSymbol,
            read_literal_enum_members,
            rewrite_python_type_expr,
        )
        from datamodel_code_generator._python_type_binding import (  # noqa: PLC0415
            BoundPythonType,
            python_type_import_key,
            python_type_import_name,
        )

        imports: dict[Import, None] = {}
        bound_imports: dict[tuple[str | None, str], Import] = {}
        resolved_name_imports: dict[str, Import | PythonTypeExpr | None] = {}
        resolving_names: set[str] | None = None

        def bind_import(import_: Import) -> tuple[Import, str]:
            key = python_type_import_key(import_)
            if (bound_import := bound_imports.get(key)) is not None:
                return bound_import, python_type_import_name(bound_import)

            name = python_type_import_name(import_)
            bound_imports[key] = import_
            imports.setdefault(import_, None)
            return import_, name

        def bind_leaf(item: PythonTypeExpr) -> PythonTypeExpr:
            nonlocal resolving_names
            match item:
                case PythonTypeQualifiedName():
                    import_, name = bind_import(self._resolve_qualified_type_import(".".join(item.parts)))
                    return PythonTypeBoundName(name, import_.from_, import_.import_)
                case PythonTypeRuntimeSymbol(module=module) if module:
                    import_, _ = bind_import(Import(import_=module))
                    return PythonTypeRuntimeSymbol(import_.alias, item.qualname_parts) if import_.alias else item
                case PythonTypeName(value=name):
                    if name not in resolved_name_imports:
                        try:
                            is_builtin = self._is_target_python_builtin_type(name)
                            import_ = None if is_builtin else self._resolve_type_import(name)
                        except Error:
                            if not (import_ := self._resolve_type_import_from_defs(name)):
                                raise
                            resolved_name_imports[name] = import_
                        else:
                            resolved_name_imports[name] = (
                                None if is_builtin else import_ or self._resolve_type_import_from_defs(name)
                            )
                    if isinstance(resolved := resolved_name_imports[name], PythonTypeExpr):
                        if resolving_names is None:
                            resolving_names = set()
                        if name in resolving_names:
                            msg = f"Cyclic x-python-import type expression involving {name!r}"
                            raise Error(msg)
                        resolving_names.add(name)
                        try:
                            return rewrite_python_type_expr(read_literal_enum_members(resolved), bind_leaf)
                        finally:
                            resolving_names.remove(name)
                    if resolved:
                        import_, bound_name = bind_import(resolved)
                        return PythonTypeBoundName(bound_name, import_.from_, import_.import_)
            return item

        return BoundPythonType(
            rewrite_python_type_expr(read_literal_enum_members(expression), bind_leaf), tuple(imports)
        )

    @override
    def _apply_override_to_field(self, field: DataModelFieldBase, override_import: Import) -> None:
        """Replace the type of a field by a ``--type-overrides`` value, an annotation such as ``typing.Literal[1, 2]``.

        A value with brackets names no class. It is written as ``x-python-type`` writes it, with the imports of its
        names and the keywords that type takes, and drops the arguments of every ``con*`` call the field held; the
        field keeps the None its schema admits.
        """
        path = _override_path(override_import)
        expression = _parse_override_annotation(path) if "[" in path else None
        if expression is None:
            super()._apply_override_to_field(field, override_import)
            return
        from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415

        bound_type = self._bind_python_type(expression)
        data_type = self.data_type(
            type=render_python_type_expr(bound_type.expression),
            python_type=bound_type,
            is_optional=field.data_type.is_optional,
        )
        self._record_overridden_constraints(field, keeps_top_kwargs=False)
        self._decide_overridden_constraints(
            field,
            partial(self.data_type_manager.constraint_keywords, data_type, self.target_python_version.version_key),
        )
        self._graph_builder.replace_field_type(field, data_type)

    def _get_python_type_override(self, obj: JsonSchemaObject) -> DataType | None:
        """Get DataType from x-python-type if it's incompatible with schema type."""
        if (python_type := self._get_x_python_type(obj)) is None:
            return None

        schema_type = type_ if isinstance(type_ := _read_type(obj), str) else None
        if self._is_compatible_python_type(schema_type, python_type):
            return None

        bound_type = self._bind_python_type(python_type)
        from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415

        return self.data_type(
            type=render_python_type_expr(bound_type.expression),
            python_type=bound_type,
        )

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
        """Return the one value a property schema admits as a tag (``field_tag`` at ``schema``)."""
        tag = field_tag("schema", _SchemaTagFacts(self, obj, seen_refs))
        return None if tag is None else tag.value

    def _get_referenced_single_literal_value(
        self, obj: JsonSchemaObject, seen_refs: set[_TargetNode] | None
    ) -> JsonSchemaLiteral | None:
        """Return the one value the schema a ``$ref`` names admits as a tag, reading each schema node once."""
        if not obj.ref:
            return None
        seen_refs = seen_refs or set()
        resolved_ref = self._ref_path(obj.ref)
        if self._resolve_external_ref_mapping(obj.ref) or (node := self._key_node(resolved_ref)) in seen_refs:
            return None
        seen_refs.add(node)
        return self._get_single_literal_value(self._load_ref_schema_object(obj.ref), seen_refs)

    def _get_union_variant_literal_values(
        self,
        combined_schemas: Sequence[JsonSchemaObject],
        field_name: str,
        tag_keys: Mapping[int, str] | None = None,
    ) -> dict[int, JsonSchemaLiteral] | None:
        """Return each property branch's distinct literal for a field; key branches count only for their own key.

        Without `tag_keys`, at least two branches must carry a literal; with them, one is enough, and branches that are
        unions themselves are skipped.
        """
        values: dict[int, JsonSchemaLiteral] = {}
        for index, item in enumerate(combined_schemas):
            if not item.properties or (
                tag_keys is not None and (item.oneOf or item.anyOf or tag_keys.get(index, field_name) != field_name)
            ):
                continue
            field = item.properties.get(field_name)
            if not isinstance(field, JsonSchemaObject):
                return None
            value = self._get_single_literal_value(field)
            if value is None:
                return None
            values[index] = value

        if len(values) < (_MIN_UNION_VARIANT_LITERAL_VALUES if tag_keys is None else 1):
            return None
        return values if distinct_tags(values.values(), "type-and-value") else None

    def _iter_union_variant_literal_field_names(  # noqa: PLR6301
        self,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
    ) -> Iterator[str]:
        seen: set[str] = set()
        if discriminator_property_name := _get_discriminator_property_name(obj):
            seen.add(discriminator_property_name)
            yield discriminator_property_name

        for item in combined_schemas:
            if not item.properties:
                continue
            for field_name in item.properties:
                if field_name in seen:
                    continue
                seen.add(field_name)
                yield field_name

    def _is_null_only_schema(self, obj: JsonSchemaObject, seen_refs: set[_TargetNode] | None = None) -> bool:
        """Return whether a schema, or the schema its `$ref` chain reaches, admits only null by type, const, or enum."""
        if (
            obj.type in ("null", ["null"])
            or obj.enum == [None]
            or ("const" in obj.extras and obj.extras["const"] is None)
        ):
            return True
        if not obj.ref:
            return False
        seen_refs = seen_refs or set()
        resolved_ref = self._ref_path(obj.ref)
        if self._resolve_external_ref_mapping(obj.ref) or (node := self._key_node(resolved_ref)) in seen_refs:
            return False
        seen_refs.add(node)
        return self._is_null_only_schema(self._load_ref_schema_object(obj.ref), seen_refs)

    def _is_single_literal_schema(self, obj: JsonSchemaObject | bool | None) -> bool:  # noqa: FBT001
        """Return whether a property schema admits a single literal value."""
        return isinstance(obj, JsonSchemaObject) and self._get_single_literal_value(obj) is not None

    def _all_of_adds_properties(self, items: Iterable[JsonSchemaObject | bool]) -> bool:
        """Return whether allOf members add properties inline, through nested allOf/oneOf/anyOf, or through a `$ref`."""
        return any(
            isinstance(item, JsonSchemaObject)
            and bool(
                item.ref
                or item.properties
                or self._all_of_adds_properties(item.allOf)
                or self._all_of_adds_properties(item.oneOf)
                or self._all_of_adds_properties(item.anyOf)
            )
            for item in items
        )

    def _get_union_variant_class_name(self, variant_name: str) -> str:
        """Return the class name a variant name becomes before the model resolver makes it unique."""
        return self.model_resolver.get_class_name(variant_name, unique=False).name

    def _get_union_variant_tag_keys(
        self,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
        literal_fields: Container[str],
    ) -> dict[int, str]:
        """Return the key of each key branch by branch index.

        A key branch has no `oneOf`/`anyOf` and no `allOf` of its own that adds properties (the union schema's `allOf`
        is copied into every branch first), one own property (one the union schema does not declare) that is required
        and not a single literal, and no single literal for a field the union declares without one. A union of one
        branch beside null has no key branch.
        """
        tag_keys: dict[int, str] = {}
        non_null_branches = (item for item in combined_schemas if not self._is_null_only_schema(item))
        if (
            len(combined_schemas) > 1
            and next(non_null_branches, None) is not None
            and next(non_null_branches, None) is None
        ):
            return tag_keys
        shared = obj.properties or {}
        open_fields = [field_name for field_name in shared if field_name not in literal_fields]
        for index, item in enumerate(combined_schemas):
            if (
                not item.properties
                or item.oneOf
                or item.anyOf
                or self._all_of_adds_properties(item.allOf[len(obj.allOf) :])
            ):
                continue
            own_properties = [(key, value) for key, value in item.properties.items() if key not in shared]
            if len(own_properties) != 1:
                continue
            key, value = own_properties[0]
            if (
                key in item.required
                and not self._is_single_literal_schema(value)
                and not any(self._is_single_literal_schema(item.properties.get(field)) for field in open_fields)
            ):
                tag_keys[index] = key
        return tag_keys

    def _infer_union_variant_literal_names(
        self,
        name: str,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
        tag_keys: Mapping[int, str] | None = None,
        skipped_fields: Container[str] = (),
    ) -> list[str | None] | None:
        """Name the branches after the first field not in `skipped_fields` whose literals give distinct names."""
        for field_name in self._iter_union_variant_literal_field_names(obj, combined_schemas):
            if field_name in skipped_fields:
                continue
            values = self._get_union_variant_literal_values(combined_schemas, field_name, tag_keys)
            if values is None:
                continue
            variant_names: list[str | None] = [None] * len(combined_schemas)
            for index, literal in values.items():
                variant_names[index] = _get_union_variant_name(name, literal)
            generated_names = [variant_name for variant_name in variant_names if variant_name]
            if self.use_tag_as_union_variant_name:
                generated_names = [self._get_union_variant_class_name(variant_name) for variant_name in generated_names]
            if len(set(generated_names)) != len(generated_names):
                continue
            return variant_names
        return None

    def _infer_union_variant_names(
        self,
        name: str,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
        variant_keys: dict[int, str],
    ) -> list[str | None] | None:
        """Name the branches from literal fields, or with tag names only for a union literal fields leave unnamed.

        Tag names come from a literal that one branch alone may carry, skipping fields the union schema fixes to a
        literal, then from the keys of key branches; a key name whose class name equals that of a literal name or
        another key name is dropped. With tags, names must also differ as class names. The key of each branch named
        after its key goes into `variant_keys` by branch index.
        """
        variant_names = self._infer_union_variant_literal_names(name, obj, combined_schemas)
        if not self.use_tag_as_union_variant_name or (variant_names and any(variant_names)):
            return variant_names
        literal_fields = {
            field_name for field_name, field in (obj.properties or {}).items() if self._is_single_literal_schema(field)
        }
        tag_keys = self._get_union_variant_tag_keys(obj, combined_schemas, literal_fields)
        tag_names = self._infer_union_variant_literal_names(name, obj, combined_schemas, tag_keys, literal_fields)
        tag_names = tag_names or [None] * len(combined_schemas)
        key_names = {
            index: key_name for index, key in tag_keys.items() if (key_name := _get_union_variant_name(name, key))
        }
        key_class_names = {index: self._get_union_variant_class_name(key_name) for index, key_name in key_names.items()}
        literal_class_names = {self._get_union_variant_class_name(tag_name) for tag_name in tag_names if tag_name}
        counts = Counter(key_class_names.values())
        for index, class_name in key_class_names.items():
            if counts[class_name] == 1 and class_name not in literal_class_names:
                tag_names[index] = key_names[index]
                variant_keys[index] = tag_keys[index]
        return tag_names

    def _get_inferred_union_variant_names(
        self,
        name: str,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
        variant_keys: dict[int, str],
    ) -> list[str | None] | None:
        if not self.infer_union_variant_names:
            return None
        return self._infer_union_variant_names(name, obj, combined_schemas, variant_keys)

    def _get_tagged_union_field_values(
        self,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
    ) -> tuple[str, dict[int, TaggedUnionValue]] | None:
        """Return the shared required literal field and the msgspec tags of the branches (``union_tag_values``)."""
        discriminator_schemas = [
            self._load_ref_schema_object(item.ref) if item.ref and self._is_local_ref(item.ref) else item
            for item in combined_schemas
        ]
        for field_name in self._iter_union_variant_literal_field_names(obj, discriminator_schemas):
            if (
                literal_values := self._get_union_variant_literal_values(discriminator_schemas, field_name)
            ) is not None and (
                tag_values := union_tag_values(
                    literal_values,
                    branch_count=len(discriminator_schemas),
                    required={index for index in literal_values if field_name in discriminator_schemas[index].required},
                )
            ) is not None:
                return field_name, tag_values
        return None

    def _set_tagged_union_discriminator(
        self,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
    ) -> None:
        """Set a discriminator extra when the output requires a tagged union."""
        if not (tag_data := self._get_tagged_union_field_values(obj, combined_schemas)):
            return
        obj.extras["discriminator"] = tagged_union_discriminator(
            obj.extras.get("discriminator"), literal_property=tag_data[0]
        )

    def _parse_combined_schema_items(  # noqa: PLR0913, PLR0917
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        combined_schemas: Sequence[JsonSchemaObject],
        variant_names: Sequence[str | None] | None,
        variant_keys: Mapping[int, str],
    ) -> list[DataType]:
        """Parse the branches of a union, recording the key and name of each branch named after its key by its node."""
        if variant_names:
            for index, key in variant_keys.items():
                self._tag_key_variants[combined_schemas[index].schema_location] = (key, variant_names[index] or name)
            return [
                self.parse_item(
                    variant_names[index] or name,
                    item,
                    [*path, str(index)],
                    singular_name=False,
                    parent=obj,
                )
                for index, item in enumerate(combined_schemas)
            ]
        return [
            self.parse_item(
                name,
                item,
                [*path, str(index)],
                singular_name=False,
                parent=obj,
            )
            for index, item in enumerate(combined_schemas)
        ]

    def _deep_merge(self, dict1: dict[Any, Any], dict2: dict[Any, Any]) -> dict[Any, Any]:
        """Deep merge two dictionaries, combining nested dicts and lists."""
        result = dict1.copy()
        for key, value in dict2.items():
            if key in result:
                if isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = _located(self._deep_merge(result[key], value), _dump_location(value))
                    continue
                if isinstance(result[key], list) and isinstance(value, list):
                    result[key] = result[key] + value  # noqa: PLR6104
                    continue
            result[key] = value
        return result

    def _get_ref_raw_schema(self, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
        return self._read_ref_value(resolved_ref)

    def _read_ref_value(self, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
        """Return the value of a loaded document at a reference key, with the references it holds as a schema."""
        file_part, fragment = ([*resolved_ref.split("#", 1), ""])[:2]
        raw_doc = self._get_ref_body(file_part) if file_part else self.raw_obj

        target_schema: dict[str, YamlValue] | YamlValue = raw_doc
        if fragment:
            pointer = split_json_pointer(raw_doc, fragment)
            target_schema = _get_model_by_decoded_path(raw_doc, pointer)
        return self._read_as_schema(target_schema)

    def _read_as_schema(self, raw: YamlValue) -> YamlValue:
        """Return a value of a loaded document that a reference names, with the references it holds as a schema."""
        if (
            (graph := self._schema_graph) is None
            or not isinstance(raw, dict)
            or (location := graph.locations.get(id(raw))) is None
        ):
            return raw
        document = DocumentId(self._schema_resource_document([location.document]))
        return graph.read_as_schema(raw, location, document, self._ref_reading("object"))

    def _ref_reading(self, root: Literal["root", "object"]) -> RefReading:
        """Return how ``SchemaGraphBuilder.with_ref_edges`` reads a document (``root``) or a schema (``object``)."""
        return RefReading(partial(_ref_edge_child_entries, named_entry_maps=self._named_entry_maps), "object", root)

    def _ref_schema_exists(self, resolved_ref: str) -> bool:
        """Return whether a resolved JSON pointer identifies a source schema."""
        file_part, fragment = ([*resolved_ref.split("#", 1), ""])[:2]
        raw_doc = self._get_ref_body(file_part) if file_part else self.raw_obj
        if not fragment:
            return True
        return (
            _get_model_by_path_or_missing(raw_doc, split_json_pointer(raw_doc, fragment)) is not _MISSING_JSON_POINTER
        )

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Load a JsonSchemaObject from a $ref using standard resolve/load pipeline."""
        resolved_ref = self._ref_path(ref)
        loaded = self._shared_schema_object(self._get_ref_raw_schema(resolved_ref), [resolved_ref])
        self._check_ref_target("_load_ref_schema_object", ref, loaded.schema_location)
        return loaded

    @property
    def _graph(self) -> SchemaGraphBuilder:
        """Return the schema graph of the parse."""
        return cast("SchemaGraphBuilder", self._schema_graph)

    def _key_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node at a reference key, or the key when the graph does not locate its value.

        The value is read as ``_load_ref_schema_object`` reads it, without validating it.
        """
        node = cast("SchemaGraphBuilder", self._schema_graph).node(self._read_ref_value(resolved_ref))
        return resolved_ref if node is None else node

    def _shared_schema_object(self, raw: dict[str, YamlValue] | YamlValue, path: list[str]) -> JsonSchemaObject:
        """Return the schema object a value of a loaded document validates to, validated once and shared by its readers.

        ``path`` names the reference in the error of a value that does not validate.
        """
        return self._shared_object(raw, self.SCHEMA_OBJECT_TYPE, partial(self._validate_schema_object, raw, path))

    def _shared_object(self, raw: object, kind: type[_ValidatedT], validate: Callable[[], _ValidatedT]) -> _ValidatedT:
        """Return the ``kind`` object a value of a loaded document validates to, validated once by the schema graph."""
        return cast("SchemaGraphBuilder", self._schema_graph).schema(
            raw, kind, validate, fingerprint=check_mode(CheckArea.SCHEMA_GRAPH) is not None
        )

    def _current_document_root(self) -> SchemaLocation:
        """Return the location of the root of the document whose schemas are being parsed."""
        return SchemaLocation(
            self.model_resolver.join_path(tuple(self.model_resolver.current_root)).partition("#")[0], ()
        )

    def _derived_ref(self, value: str, source: SchemaLocation | None, keyword: str, ref: str, origin: RefOrigin) -> str:
        """Return ``value``, a reference the parser derives from the JSON object at ``source`` of a loaded document.

        It carries the edge of ``ref``, the reference under ``keyword`` of that object that names the same target from
        its document. An object the schema graph does not locate gives no edge; a ``ResolvedRef`` keeps its own.
        """
        if source is None or isinstance(value, ResolvedRef):
            return value
        document = DocumentId(self._schema_resource_document([source.document]))
        return ResolvedRef(value, RefEdge(source, keyword, document, ref, canonical_ref_spelling(str(ref)), origin))

    @contextmanager
    def _home_context(self, document: str) -> Generator[None, None, None]:
        """Resolve references as a loaded document resolves its own: from its location, its root `$id` and `$self`.

        ``document`` names the document as reference paths do.
        """
        path_parts = _ref_path_parts(document)
        prepared = self._schema_resource_cache.get(name := self._schema_resource_document(path_parts))
        with self._schema_document_context(path_parts):
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

    def _compute_edge_key(self, edge: RefEdge) -> RefKey:
        """Resolve the reference of an edge as the document that holds it resolves it (``_compute_ref_key``)."""
        with self._home_context(edge.home or edge.source.document):
            return self._compute_ref_key(edge.spelling, embedded=True)

    def _compute_ref_key(self, ref: str, *, embedded: bool) -> RefKey:
        """Resolve a reference in the current context: its mapping, embedded resource, normalized spelling and key.

        ``embedded`` resolves a reference through the schema resource an `$id` of the current document embeds first.
        """
        if (mapping := self._resolve_external_ref_mapping(ref)) is not None:
            return RefKey(self.model_resolver.resolve_ref(ref), ref, mapping)
        if embedded and self._has_embedded_schema_resources:
            document = self._schema_resource_document(list(self.model_resolver.current_root))
            ref = self._resolve_schema_resource_ref(
                ref, self._schema_resource_root_bases.get(document, document), document, nested_scope=False
            )
        ref = self._normalize_external_ref(ref)
        return RefKey(self.model_resolver.resolve_ref(ref), ref)

    def _ref_key(self, ref: str, *, embedded: bool = True) -> RefKey:
        """Return what a reference resolves to: the key of its edge, the same at every site and in every context.

        A plain string has no edge: it resolves in the current context (``_compute_ref_key``).
        """
        if isinstance(ref, ResolvedRef):
            return cast("SchemaGraphBuilder", self._schema_graph).edge_key(ref.edge, self._compute_edge_key)
        return self._compute_ref_key(ref, embedded=embedded)

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names: its edge's key, or a plain string's ``resolve_ref``."""
        return self._ref_key(ref).key if isinstance(ref, ResolvedRef) else self.model_resolver.resolve_ref(ref)

    def _add_ref(self, ref: str) -> Reference:
        """Return the Reference at the key of a reference's edge, named after the edge's spelling if it is new.

        A plain string is added as ``ModelResolver.add_ref`` adds it in the current context. The package and fragment
        that ``--external-ref-mapping`` maps a reference to are recorded for its Reference (``_mapped_references``).
        """
        if isinstance(ref, ResolvedRef):
            key = self._ref_key(ref)
            reference, mapping = self.model_resolver._add_ref_at(key.key, key.spelling), key.mapping  # noqa: SLF001
        else:
            reference, mapping = self.model_resolver.add_ref(ref), self._resolve_external_ref_mapping(ref)
        if mapping is not None:
            self._mapped_references[reference.path] = mapping
        return reference

    def _check_ref_key(self, site: str, ref: str, key: RefKey, *, embedded: bool) -> None:
        """Report a reference keyword that the context of ``site`` resolves to another target than its edge.

        Only the schema graph check reads edges. The difference is the case edges fix, recorded and not reported, when
        the context raises, names no value of a loaded document, or is not the reference's own document.
        """
        if (mode := check_mode(CheckArea.SCHEMA_GRAPH)) is None:
            return
        graph = cast("SchemaGraphBuilder", self._schema_graph)
        graph.count_ref(site, ref, self._plain_ref_origin)
        if not isinstance(ref, ResolvedRef):
            return
        warnings = set(self._resource_ref_warnings)
        try:
            here: RefKey | None = self._compute_ref_key(canonical_ref_spelling(str(ref)), embedded=embedded)
        except Error:
            here = None
        finally:
            self._resource_ref_warnings.intersection_update(warnings)
        if here is not None and (here.key, here.mapping) == (key.key, key.mapping):
            return
        home = not self._outside_home(ref)
        context_key = (
            redact_url(here.key)
            if here is not None and (here.mapping is not None or self._names_loaded_value(here.key))
            else None
        )
        details = {
            "site": site,
            "ref": redact_url(ref),
            "source": redact_url(str(ref.edge.source)),
            "key": redact_url(key.key),
            "context_key": context_key,
            "home": home,
        }
        if context_key is None or not home:
            mode.record(
                f"{mode.area.value}: the {ref.edge.keyword} {redact_url(ref)!r} at "
                f"{redact_url(str(ref.edge.source))!r} resolves to {redact_url(key.key)!r} in its document, and to "
                f"{'no loaded value' if context_key is None else repr(context_key)} at {site} "
                f"{'in' if home else 'outside'} it",
                kind="fixed-context",
                **details,
            )
            return
        mode.violation(
            Error,
            f"the {ref.edge.keyword} {redact_url(ref)!r} at {redact_url(str(ref.edge.source))!r} resolves to "
            f"{redact_url(key.key)!r} in its document, but to {context_key!r} at {site}",
            kind="key",
            **details,
        )

    def _outside_home(self, ref: str) -> bool:
        """Return whether a reference with an edge is read outside the document that holds it, the one parsed now."""
        return isinstance(ref, ResolvedRef) and ref.edge.document != self._schema_resource_document(
            list(self.model_resolver.current_root)
        )

    def _is_local_ref(self, ref: str) -> bool:
        """Return whether a reference is a fragment of the document parsed now: written as one there, by its edge."""
        return get_ref_type(ref) == JSONReference.LOCAL and not self._outside_home(ref)

    def _names_loaded_value(self, key: str) -> bool:
        """Return whether a reference key names a value of a document the parse has loaded."""
        file_part, _, fragment = key.partition("#")
        prepared = self._schema_resource_cache.get(self._schema_resource_document(_ref_path_parts(file_part)))
        return (
            prepared is not None
            and _get_model_by_path_or_missing(prepared[1], split_json_pointer(prepared[1], fragment))
            is not _MISSING_JSON_POINTER
        )

    def _check_ref_target(self, site: str, ref: str, location: SchemaLocation | None) -> None:
        """Report a reference keyword whose target at ``site`` is not the registered location its edge names.

        Only the schema graph check reads edges; an edge whose target is not registered is not compared. A target
        whose pointer the parser splits at another `/` (a key with an unescaped or percent-encoded slash, which the
        parser matches against the keys of the document) is the listed slash-key class, and a reference of a document
        that resolves references against itself (``_resolve_lenient_resource_ref``, which warns where JSON Schema
        resolves otherwise) is the listed lenient-document class; neither is a mismatch.
        """
        if (mode := check_mode(CheckArea.SCHEMA_GRAPH)) is None:
            return
        graph = cast("SchemaGraphBuilder", self._schema_graph)
        graph.count_ref(site, ref, self._plain_ref_origin)
        if (
            not isinstance(ref, ResolvedRef)
            or location is None
            or ref.edge.document in self._lenient_documents
            or (target := graph.target(ref.edge)) is None
        ):
            return
        if self._schema_resource_document([location.document]) == target.document and "/".join(
            location.tokens
        ) == "/".join(target.tokens):
            return
        named = SchemaLocation(
            self.model_resolver.join_path(tuple(self._schema_resource_path_parts[target.document])).partition("#")[0],
            target.tokens,
        )
        mode.violation(
            Error,
            f"the {ref.edge.keyword} {redact_url(ref)!r} at {redact_url(str(ref.edge.source))!r} names "
            f"{redact_url(str(named))!r}, but {site} loads {redact_url(str(location))!r}",
            kind="target",
            site=site,
            ref=redact_url(ref),
            source=redact_url(str(ref.edge.source)),
            target=redact_url(str(named)),
            loaded=redact_url(str(location)),
        )

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
        if not use_builtin_facts:
            return self._load_ref_schema_object(ref).is_boolean_schema_false

        resolved_ref = self._ref_path(ref)
        if (false_schema_refs := self._false_schema_refs) is not None and resolved_ref in false_schema_refs:
            return True
        if resolved_ref in self._ref_data_type_facts:
            return False
        return self._load_ref_schema_object(ref).is_boolean_schema_false

    def _anchor_ref_path(self, root_key: tuple[str, ...], path: list[str]) -> str:  # noqa: PLR6301
        """Return the local ref path for an anchor under the current root."""
        root_len = len(root_key)
        if root_len >= len(path):
            return "#"
        suffix_parts = path[root_len:]
        first = suffix_parts[0]
        if first.startswith("#"):
            suffix_parts = [first[1:].lstrip("/"), *suffix_parts[1:]]
        return "#/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in suffix_parts if part)

    def _build_anchor_indexes(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Build $recursiveAnchor and $dynamicAnchor indexes for a schema object."""
        root_key = tuple(self.model_resolver.current_root)
        ref_path = self._anchor_ref_path(root_key, path)
        if obj.recursiveAnchor:
            anchors = self._recursive_anchor_index.setdefault(root_key, [])
            if ref_path not in anchors:
                anchors.append(ref_path)
        if obj.dynamicAnchor:
            self._dynamic_anchor_index.setdefault(root_key, {}).setdefault(obj.dynamicAnchor, ref_path)

    def _enclosing_unanchored_resource_ref(self) -> str | None:
        """Return the nearest $id resource enclosing the schema being parsed, when it has no $recursiveAnchor."""
        if (
            not self._has_embedded_schema_resources
            or (cached := self._schema_resource_cache.get(self._current_dynamic_document())) is None
        ):
            return None
        raw = cached[1]
        return _unanchored_resource_ref(raw, split_json_pointer(raw, self._dynamic_scope_pointer))

    def _resolve_recursive_ref(self, item: JsonSchemaObject, path: list[str]) -> str | None:
        """Resolve $recursiveRef to an equivalent $ref.

        Per JSON Schema 2019-09, $recursiveRef only allows "#" as value.
        Resolves to the root of the nearest enclosing $id resource when it has no $recursiveAnchor,
        and otherwise to the nearest enclosing schema with $recursiveAnchor: true.
        For standalone JSON Schema files, this is the root "#".
        For OpenAPI, this is the component schema definition path.
        """
        if item.recursiveRef != "#":  # pragma: no cover
            return None
        if (resource := self._enclosing_unanchored_resource_ref()) is not None:
            return resource
        root_key = tuple(self.model_resolver.current_root)
        current_ref = self._anchor_ref_path(root_key, path)
        target = _nearest_recursive_anchor(self._recursive_anchor_index.get(root_key, ()), current_ref)
        self._check_anchor_answer(
            "$recursiveRef", current_ref, target, lambda _, anchors: _nearest_recursive_anchor(anchors, current_ref)
        )
        return target

    def _resolve_dynamic_ref(self, item: JsonSchemaObject) -> str | None:
        """Return the first $dynamicAnchor of the name of a fragment $dynamicRef in the document being parsed, if any.

        Per JSON Schema 2020-12, a $dynamicRef first resolves like a $ref, and an anchor of its name in the dynamic
        scope then overrides that target. In code generation, the dynamic scope is resolved statically via index lookup.
        """
        if not (ref := item.dynamicRef) or not ref.startswith("#"):
            return None
        target = self._dynamic_anchor_index.get(tuple(self.model_resolver.current_root), {}).get(ref[1:])
        self._check_anchor_answer("$dynamicRef", ref, target, lambda anchors, _: anchors.get(ref[1:]))
        return target

    def _check_anchor_answer(
        self,
        keyword: str,
        ref: str,
        answer: object,
        full: Callable[[Mapping[str, str], tuple[str, ...]], object],
        path_parts: list[str] | None = None,
    ) -> None:
        """Record an answer from the anchors L1 has registered that differs from the answer of the document's L0 table.

        L1 registers the `$dynamicAnchor` and `$recursiveAnchor` of the document being parsed (or of the one at
        ``path_parts``) as it parses: the root and the definitions of a document `_parse_file` parses, then each schema
        parsed through `_parse_raw_or_validated_obj`, so an answer can depend on the parse order. ``full`` computes the
        answer from every anchor the schema graph found in the document: its `$dynamicAnchor` names and its
        `$recursiveAnchor` schemas, as local references. The difference is the listed ``anchor-answer`` class, recorded
        and not reported: the parser keeps the registered answer.
        """
        if (mode := check_mode(CheckArea.SCHEMA_GRAPH)) is None:
            return
        if path_parts is None:
            path_parts = list(self.model_resolver.current_root)
        if (
            tables := self._schema_resource_registry.anchor_tables.get(self._schema_resource_document(path_parts))
        ) is None:
            return
        dynamic, recursive = tables
        expected = full(
            {name: f"#{location.partition('#')[2]}" for name, location in dynamic.items()},
            tuple(f"#{location.partition('#')[2]}" for location in recursive),
        )
        if expected == answer:
            return
        name = redact_url(self.model_resolver.join_path(tuple(path_parts)).partition("#")[0])
        mode.record(
            f"{mode.area.value}: the {keyword} {ref!r} of {repr(name) if name else 'the input'} resolves to "
            f"{answer!r} by the anchors registered so far and to {expected!r} by every anchor of the document",
            kind="anchor-answer",
            keyword=keyword,
            ref=ref,
            document=name,
            answer=answer,
            expected=expected,
        )

    def _dynamic_scope_resource(self, document: str, pointer: str) -> str | None:
        """Return the root pointer of the schema resource enclosing a location in a registered document."""
        if (resources := self._schema_resources.get(document)) is None:
            return None
        while pointer and pointer not in resources:
            pointer = pointer.rpartition("/")[0]
        return pointer

    def _lexical_dynamic_anchor(self, document: str, pointer: str, ref: str) -> str | None:
        """Return the $dynamicAnchor location a plain-name $dynamicRef at a location initially resolves to.

        Plain-name fragments resolve within the enclosing schema resource, so embedded resources
        that reuse an anchor name keep their own targets.
        """
        if not ref.startswith("#") or (resource := self._dynamic_scope_resource(document, pointer)) is None:
            return None
        return self._dynamic_anchors.get((document, resource), {}).get(ref[1:])

    def _dynamic_anchor_bindings(self, document: str, pointer: str, outer: dict[str, str]) -> dict[str, str]:
        """Bind each $dynamicAnchor name to its outermost declaration in a dynamic scope.

        Outer bindings come from the schemas that referenced the model being parsed, which entered the
        dynamic scope first; the schema resource enclosing the location declares the others.
        """
        if (resource := self._dynamic_scope_resource(document, pointer)) is None:
            return dict(outer)
        return {**self._dynamic_anchors.get((document, resource), {}), **outer}

    def _current_dynamic_document(self) -> str:
        """Return the document of the model root whose dynamic scope is being resolved."""
        if (document := self._dynamic_document) is not None:
            return document
        return self._schema_resource_document(list(self.model_resolver.current_root))

    def _current_dynamic_scope(self) -> dict[str, str]:
        """Return the $dynamicAnchor bindings that schemas entered from the model being parsed see."""
        return self._dynamic_anchor_bindings(
            self._current_dynamic_document(), self._dynamic_scope_pointer, self._dynamic_bindings
        )

    def _bound_dynamic_anchor(self, ref: str) -> str | None:
        """Return the location a plain-name $dynamicRef resolves to in the current dynamic scope.

        A fragment that initially resolves to a $dynamicAnchor binds to the outermost schema resource
        in the dynamic scope that declares the same anchor name.
        """
        document = self._current_dynamic_document()
        if (location := self._lexical_dynamic_anchor(document, self._dynamic_scope_pointer, ref)) is None:
            return None
        return self._dynamic_bindings.get(ref[1:], location)

    def _schema_location(self, resolved_ref: str) -> str:
        """Return the registry location of a resolved reference."""
        file_part, _, pointer = resolved_ref.partition("#")
        return f"{self._schema_resource_document(_ref_path_parts(file_part))}#{pointer}"

    def _location_ref(self, location: str) -> str:
        """Return the resolved reference of a registry location in a registered document."""
        document, _, pointer = location.partition("#")
        path_parts = self._schema_resource_path_parts[document]
        return f"{self.model_resolver.join_path(tuple(path_parts)).partition('#')[0]}#{pointer}"

    def _dynamic_target_bindings(self, resolved_ref: str) -> dict[str, str]:
        """Return the bindings under which a referenced schema resolves $dynamicRef differently.

        The referenced schema sees the current dynamic scope plus its own schema resource. Anchors
        count when a $dynamicRef reachable from it resolves to them, following each bound anchor to the
        schema it names, and a binding that matches the schema's own resource keeps the shared model.
        """
        if not self._dynamic_anchors or not (scope := self._current_dynamic_scope()):
            return {}
        if not (used := set(self._dynamic_anchors_used(resolved_ref))):
            return {}
        target_document, _, target_pointer = self._schema_location(resolved_ref).partition("#")
        defaults = self._dynamic_anchor_bindings(target_document, target_pointer, {})
        bindings = {**defaults, **scope}
        pending = [anchor for anchor in used if anchor in bindings]
        while pending:
            found = self._dynamic_anchors_used(self._location_ref(bindings[pending.pop()])) - used
            used |= found
            pending.extend(anchor for anchor in found if anchor in bindings)
        return {
            anchor: bindings[anchor]
            for anchor in sorted(used)
            if anchor in bindings and bindings[anchor] != defaults.get(anchor)
        }

    def _dynamic_anchors_used(self, resolved_ref: str) -> frozenset[str]:
        """Return the $dynamicAnchor names that $dynamicRef reachable from a referenced schema resolve to.

        Results are memoized per schema graph node and computed per strongly connected component of the reference
        graph, so each schema is analyzed once however many references, under whichever key, reach it.
        """
        start = self._dynamic_ref_edges(resolved_ref)
        if (cached := self._dynamic_anchor_usage.get(start.node)) is not None:
            return cached
        index = {start.node: 0}
        lowlink = {start.node: 0}
        position = {start.node: 0}
        stack = [start.node]
        work = [(start.node, iter(start.targets))]
        while work:
            node, targets = work[-1]
            for target in targets:
                if (successor := self._dynamic_ref_edges(target).node) in self._dynamic_anchor_usage:
                    continue
                if successor not in index:
                    index[successor] = lowlink[successor] = len(index)
                    position[successor] = len(stack)
                    stack.append(successor)
                    work.append((successor, iter(self._dynamic_ref_graph[successor].targets)))
                    break
                lowlink[node] = min(lowlink[node], index[successor])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])
                if lowlink[node] == index[node]:
                    component = stack[position[node] :]
                    del stack[position[node] :]
                    members = set(component)
                    anchors: set[str] = set()
                    for member in component:
                        edges = self._dynamic_ref_graph[member]
                        anchors |= edges.anchors
                        anchors.update(
                            *(
                                self._dynamic_anchor_usage[successor]
                                for target in edges.targets
                                if (successor := self._dynamic_ref_nodes[target]) not in members
                            )
                        )
                    self._dynamic_anchor_usage.update(dict.fromkeys(component, frozenset(anchors)))
        return self._dynamic_anchor_usage[start.node]

    def _dynamic_ref_edges(self, resolved_ref: str) -> _DynamicEdges:
        """Return the node at a reference key, the anchors its $dynamicRef initially resolve to and what it references.

        The schema is read from the prepared tree of its document; a key whose value the graph does not locate is its
        own node, and the first key that reaches a node gives the node its analysis.
        """
        if (node := self._dynamic_ref_nodes.get(resolved_ref)) is not None:
            return self._dynamic_ref_graph[node]
        file_part, _, fragment = resolved_ref.partition("#")
        document, raw = self._prepared_ref_document(file_part)
        schema = _get_model_by_path_or_missing(raw, split_json_pointer(raw, fragment)) if fragment else raw
        located = cast("SchemaGraphBuilder", self._schema_graph).node(schema)
        node = self._dynamic_ref_nodes[resolved_ref] = resolved_ref if located is None else located
        anchors: set[str] = set()
        targets: list[str] = []
        for pointer, subschema in self._iter_referencing_schemas(schema, fragment):
            match subschema:
                case {"$ref": str() as ref}:
                    if (target := self._analysis_ref_target(document, file_part, ref)) is not None:
                        targets.append(target)
                case {"$dynamicRef": str() as dynamic_ref} if location := self._lexical_dynamic_anchor(
                    document, pointer, dynamic_ref
                ):
                    anchors.add(dynamic_ref[1:])
                    targets.append(self._location_ref(location))
        return self._dynamic_ref_graph.setdefault(node, _DynamicEdges(node, frozenset(anchors), tuple(targets)))

    def _prepared_ref_document(self, file_part: str) -> tuple[str, dict[str, Any]]:
        """Return the document a resolved reference points into and its prepared schema, loading it once."""
        document = self._schema_resource_document(_ref_path_parts(file_part))
        if (cached := self._schema_resource_cache.get(document)) is not None:
            return document, cached[1]
        with self._ref_document_context(file_part) as prepared:
            return prepared

    def _iter_referencing_schemas(self, schema: object, pointer: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Visit the raw subschemas that the parser follows, with their JSON pointers.

        Keywords are those of the reference pre-pass plus the additionalItems of tuple-style arrays,
        which array parsing appends as the tail item. References under keywords the parser ignores and
        definitions that apply only through references are not analyzed.
        """
        keywords = (
            _SCHEMA_OBJECT_CHILD_FIELDS | _CONDITIONAL_SCHEMA_KEYWORDS
            if self.generate_schema_validators
            else _SCHEMA_OBJECT_CHILD_FIELDS
        )
        stack = [(pointer, cast("dict[str, Any]", schema))] if isinstance(schema, dict) else []
        while stack:
            current_pointer, current = stack.pop()
            yield current_pointer, current
            tuple_items = isinstance(current.get("items"), list)
            stack.extend(
                (
                    current_pointer + "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in path),
                    child,
                )
                for path, child in self._iter_schema_resource_children(current)
                if path[0] in keywords or (tuple_items and path[0] == "additionalItems")
            )

    def _analysis_ref_target(self, document: str, file_part: str, ref: str) -> str | None:
        """Resolve a $ref met while analyzing $dynamicRef reachability, without loading its target.

        Pointers into the same document need no resolution. Plain-name anchors come from the resource
        registry, so the result does not depend on which documents were parsed first, and references
        mapped to Python packages are not followed.
        """
        if ref == "#" or ref.startswith("#/"):
            return f"{file_part}{ref}"
        if self._resolve_external_ref_mapping(ref) is not None:
            return None
        if location := self._schema_resource_locations.get(urljoin(self._schema_resource_root_bases[document], ref)):
            return self._location_ref(location)
        with self._ref_document_context(file_part):
            return self._ref_key(ref, embedded=False).key

    @contextmanager
    def _schema_document_context(self, path_parts: list[str]) -> Generator[None, None, None]:
        """Resolve references relative to the document identified by model resolver root parts."""
        document = self.model_resolver.join_path(tuple(path_parts)).partition("#")[0]
        remote = is_url(document)
        with (
            self.model_resolver.current_base_path_context(None if remote else Path(document).parent),
            self.model_resolver.base_url_context(document if remote else None),
            self.model_resolver.current_root_context(path_parts),
        ):
            yield

    @contextmanager
    def _ref_document_context(self, file_part: str) -> Generator[tuple[str, dict[str, Any]], None, None]:
        """Enter the resolution context of the document that a resolved reference points into."""
        document = self._schema_resource_document(_ref_path_parts(file_part))
        path_parts = self._schema_resource_path_parts.get(document) or _ref_path_parts(file_part)
        with self._schema_document_context(path_parts):
            raw = self._prepare_schema_resources(
                self._get_ref_body(file_part) if file_part else self.raw_obj, path_parts
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
        with self._dynamic_scope_refs():
            if target_document == self._schema_resource_document(list(self.model_resolver.current_root)):
                yield f"#{pointer}"
                return
            with self._schema_document_context([]), self.root_id_context({}):
                yield self._location_ref(location)

    @contextmanager
    def _dynamic_scope_refs(self) -> Generator[None, None, None]:
        """Name the references read inside as resolved in the dynamic scope, for the counts of the schema graph check.

        Such a reference stays a plain string: its target depends on the schemas that referenced the model being
        parsed.
        """
        origin, self._plain_ref_origin = self._plain_ref_origin, RefOrigin.DYNAMIC_SCOPE
        try:
            yield
        finally:
            self._plain_ref_origin = origin

    def _dynamic_specialization_reference(self, ref: str, resolved_ref: str) -> Reference | None:
        """Return a model of the referenced schema specialized for the current dynamic scope, if it differs.

        The shared model is registered first so it keeps naming priority. The specialized model is
        registered under the referencing model and named like the shared model before parsing, so
        parsing names both the same way once the referencing model is named.
        """
        if not (bindings := self._dynamic_target_bindings(resolved_ref)):
            return None
        key = (self._dynamic_ref_edges(resolved_ref).node, frozenset(bindings.items()))
        if (reference := self._dynamic_specializations.get(key)) is not None:
            return reference
        shared = self._add_ref(ref)
        path = get_special_path(f"dynamic-scope-{len(self._dynamic_specializations)}", list(self._dynamic_scope_path))
        reference = self.model_resolver.add(
            path, self.model_resolver.ref_original_name(ref, resolved_ref) or shared.original_name, class_name=True
        )
        self._dynamic_specializations[key] = reference
        self._dynamic_specialization_paths.add(tuple(path))
        self._pending_dynamic_specializations.append(
            _DynamicSpecialization(resolved_ref, bindings, reference, tuple(path))
        )
        return reference

    def _parse_dynamic_specializations(self) -> None:
        """Parse pending specialized models iteratively, outside of any specialization being parsed."""
        if self._draining_dynamic_specializations or not self._pending_dynamic_specializations:
            return
        self._draining_dynamic_specializations = True
        try:
            while self._pending_dynamic_specializations:
                self._parse_dynamic_specialization(self._pending_dynamic_specializations.popleft())
        finally:
            self._draining_dynamic_specializations = False

    def _parse_dynamic_specialization(self, specialization: _DynamicSpecialization) -> None:
        """Parse a referenced schema again under the $dynamicAnchor bindings of its dynamic scope."""
        file_part, _, fragment = specialization.target.partition("#")
        with self._ref_document_context(file_part) as (_, raw):
            target = _get_model_by_decoded_path(raw, split_json_pointer(raw, fragment)) if fragment else raw
            obj = self._validate_schema_object(target, list(specialization.path))
            with self._dynamic_scope_context(specialization.path, specialization.bindings, fragment):
                self._parse_schema_obj(specialization.reference.name, obj, list(specialization.path))

    def _dynamic_scope_target(self, item: JsonSchemaObject, path: list[str]) -> _DynamicTarget | None:
        """Return where rule DS resolves the `$recursiveRef` or `$dynamicRef` of a schema; None resolves its edge.

        Rule DS: a `$recursiveRef`, and a plain-name `$dynamicRef`, resolve against the dynamic scope of the model
        being parsed, not against their own document; every other reference resolves through its edge. A
        `$recursiveRef` names the root of its enclosing `$id` resource without `$recursiveAnchor`, else the innermost
        `$recursiveAnchor` that encloses it, else `#` (``_resolve_recursive_ref``). A `$dynamicRef` names the
        `$dynamicAnchor` the dynamic scope binds its name to (``_bound_dynamic_anchor``), else the first
        `$dynamicAnchor` of its name in the document being parsed (``_resolve_dynamic_ref``), else its target through
        its edge. In code generation, the dynamic scope is the chain of schema resources that referenced the model
        being parsed; schemas reached under a different scope are parsed again for it.
        """
        if item.recursiveRef:
            return _DynamicTarget(self._resolve_recursive_ref(item, path) or "#")
        if not (dynamic_ref := item.dynamicRef):
            return None
        if (location := self._bound_dynamic_anchor(dynamic_ref)) is not None:
            return _DynamicTarget(location=location)
        if (anchor := self._resolve_dynamic_ref(item)) is not None:
            return _DynamicTarget(anchor)
        return None

    @contextmanager
    def _dynamic_target_ref(self, target: _DynamicTarget) -> Generator[str, None, None]:
        """Yield the reference to a target of rule DS that resolves from the current document."""
        if target.location is not None:
            with self._schema_location_context(target.location) as ref:
                yield ref
            return
        with self._dynamic_scope_refs():
            yield target.ref

    def _get_dynamic_scope_data_type(self, item: JsonSchemaObject, path: list[str]) -> DataType:
        """Return the data type of a `$recursiveRef` or `$dynamicRef` (rule DS, ``_dynamic_scope_target``)."""
        if (target := self._dynamic_scope_target(item, path)) is None:
            return self.get_ref_data_type(cast("str", item.dynamicRef))
        with self._dynamic_target_ref(target) as ref:
            return self.get_ref_data_type(ref)

    def _subschema_scope(self, obj: JsonSchemaObject) -> AbstractContextManager[None] | None:
        """Return the dynamic scope of a subschema that stands for another schema resource, if any.

        A $ref merged with its sibling keywords is parsed in place of the schema it names, and an inline
        subschema with its own $id is an embedded schema resource. The $dynamicRef in either initially
        resolve within that resource, while the $dynamicAnchor bindings of the enclosing resources and the
        referencing schemas stay in effect, so the outermost declaration wins.
        """
        if (resolved_ref := obj.__dict__.get(_MERGED_REF_TARGET_KEY)) is not None:
            if not self._dynamic_anchors:
                return None
            document, _, pointer = self._schema_location(resolved_ref).partition("#")
            pointer = unquote(pointer)
        elif (identifier := obj.id) and self._embedded_resources:
            document = self._current_dynamic_document()
            resource = self._dynamic_scope_resource(document, self._dynamic_scope_pointer)
            if (pointer := self._embedded_resources.get((document, resource or ""), {}).get(identifier)) is None:
                return None
        else:
            return None
        return self._dynamic_scope_context(self._dynamic_scope_path, self._current_dynamic_scope(), pointer, document)

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
        self._dynamic_document = (
            self._schema_resource_document(list(self.model_resolver.current_root)) if document is None else document
        )
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

    def _get_contains_bounds(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the item bounds that the contains keywords of a schema imply, as a schema of those bounds alone."""
        if not (keys := _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS & obj.extras.keys()):
            return None
        contains = self._schema_object({key: obj.extras[key] for key in keys})
        return self._schema_object(self._get_array_items_constraints(contains))

    def _ref_target_dump(self, ref: str, resolved_ref: str) -> tuple[dict[str, Any], str]:
        """Return a fresh dump of a referenced schema for the current document, and the reference it ends at.

        A $recursiveRef inside a schema from another document is pinned to its target there (``_pin_recursive_refs``),
        and a referenced schema that is itself a $ref is merged with its own target. The dump depends only on the schema
        graph node the reference names and the current document, so each parse computes it once per pair and hands out
        copies. A chain of such aliases is loaded first and merged from its far end, so each merge finds the dump of the
        next alias computed.
        """
        document = self._schema_resource_document(list(self.model_resolver.current_root))
        key = node, _ = self._key_node(resolved_ref), document
        chain: list[tuple[str, _TargetNode, dict[str, Any], JsonSchemaObject | None]] = []
        while (node, document) not in self._ref_target_dumps:
            ref_dict = self._load_ref_schema_object(ref).model_dump(exclude_unset=True, by_alias=True)
            if self._schema_location(resolved_ref).partition("#")[0] != document:
                self._pin_recursive_refs(ref_dict, resolved_ref)
            alias = self._schema_object(ref_dict) if isinstance(ref_dict.get("$ref"), str) else None
            chain.append((resolved_ref, node, ref_dict, alias))
            if (
                alias is None
                or not alias.ref
                or self._is_ref_circular(resolved_ref := self._ref_path(ref := self._lenient_ref(alias.ref)))
            ):
                break
            node = self._key_node(resolved_ref)
        for chain_ref, chain_node, ref_dict, alias in reversed(chain):
            dump, target_ref = ref_dict, chain_ref
            if alias is not None:
                target = self._merge_ref_with_schema(alias)
                dump = target.model_dump(exclude_unset=True, by_alias=True)
                target_ref = target.__dict__.get(_MERGED_REF_TARGET_KEY, chain_ref)
            self._ref_target_dumps[chain_node, document] = dump, target_ref
        cached = self._ref_target_dumps[key]
        return deepcopy(cached[0]), cached[1]

    def _intersect_parts(
        self, parts: Sequence[dict[str, Any]], row: dict[str, Any], *, ref_siblings_are_parts: bool = True
    ) -> KeywordIntersection:
        """Return the intersection of the ``parts`` of a site, whose earlier merge wrote them as ``row``.

        Without --intersect-allof-keywords the schema is ``row``, the merge of 0.86.0 (a sibling's or a child's value
        winning), whatever the parts say.
        """
        if not self.intersects_allof_keywords:
            return KeywordIntersection(row, ())
        return intersect_keywords(
            parts, row, extras_key=JsonSchemaObject.__extra_key__, ref_siblings_are_parts=ref_siblings_are_parts
        )

    def _intersect_ref_siblings(
        self, target: dict[str, Any], siblings: dict[str, Any], where: str, *, siblings_are_parts: bool
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Merge the dump of a referenced schema with the sibling keywords of its $ref into their intersection.

        The earlier merge (``_deep_merge``: lists concatenated, a sibling's value winning) stays wherever it enforces
        what the intersection enforces; ``intersect_keywords`` replaces the keywords where it does not. A keyword whose
        parts have no value in common keeps the earlier merge's value, with a warning. Returns the merged keywords and
        the keywords with no value in common. Without --intersect-allof-keywords the earlier merge alone.
        """
        intersection = self._intersect_parts(
            [target, siblings], self._deep_merge(target, siblings), ref_siblings_are_parts=siblings_are_parts
        )
        self._warn_intersection(where, intersection)
        return intersection.keywords, intersection.unsatisfiable

    def _intersect_ref_siblings_schema(
        self, target: dict[str, Any], siblings: dict[str, Any], where: str, *, siblings_are_parts: bool
    ) -> JsonSchemaObject:
        """Return the schema of the intersection of a referenced schema's dump and the sibling keywords of its $ref."""
        merged, unsatisfiable = self._intersect_ref_siblings(
            target, siblings, where, siblings_are_parts=siblings_are_parts
        )
        merged.pop("$ref", None)
        merged_schema = self._schema_object(merged)
        self._mark_reported_value_conflict(merged_schema, unsatisfiable)
        return merged_schema

    def _warn_unsatisfiable(self, where: str, keywords: tuple[str, ...], *parts: str) -> None:
        """Warn once per parse for each schema whose parts have no value in common for some keywords."""
        if keywords and (where, keywords) not in self._unsatisfiable_schemas:
            self._unsatisfiable_schemas.add((where, keywords))
            warn_unsatisfiable(where, keywords, *parts)

    def _warn_untaken_patterns(self, where: str, pattern: str, untaken: tuple[str, ...]) -> None:
        """Warn once per parse for each schema whose written pattern leaves out patterns of its parts."""
        if untaken and (where, (pattern, *untaken)) not in self._untaken_pattern_schemas:
            self._untaken_pattern_schemas.add((where, (pattern, *untaken)))
            warn_untaken_patterns(where, pattern, untaken)

    def _warn_intersection(self, where: str, intersection: KeywordIntersection, *parts: str) -> None:
        """Warn about the keywords of an intersection whose parts have no value in common, and its untaken patterns."""
        self._warn_unsatisfiable(where, intersection.unsatisfiable, *parts)
        if intersection.untaken_patterns:
            self._warn_untaken_patterns(where, intersection.keywords["pattern"], intersection.untaken_patterns)

    def _conjoined_pattern(self, patterns: Sequence[str], where: str) -> str:
        """Return the conjoined pattern (``conjoin_patterns``), warning about the patterns it leaves out.

        Without --intersect-allof-keywords the lookaheads 0.86.0 wrote (``lookahead_patterns``), without a warning.
        """
        if not self.intersects_allof_keywords:
            return lookahead_patterns(patterns)
        conjunction = conjoin_patterns(patterns)
        self._warn_untaken_patterns(where, conjunction.pattern, conjunction.untaken)
        return conjunction.pattern

    def _mark_reported_value_conflict(  # noqa: PLR6301
        self, schema: JsonSchemaObject, keywords: Sequence[str]
    ) -> None:
        """Mark a merged schema whose ``enum`` or ``const`` conflict the intersection warning already reports."""
        if _VALUE_CONFLICT_KEYWORDS.intersection(keywords):
            schema.__dict__[_REPORTED_VALUE_CONFLICT_KEY] = True

    def _merge_ref_with_schema(self, obj: JsonSchemaObject, where: str | None = None) -> JsonSchemaObject:
        """Merge $ref schema with current schema's additional keywords.

        JSON Schema 2020-12 allows $ref alongside other keywords, which apply together with the referenced schema:
        the merged schema is their intersection, keyword by keyword (``_intersect_ref_siblings``).

        A local contains does not replace the referenced one: both contains then apply, so the referenced contains
        keywords become the item bounds they imply and intersect with the local bounds. A referenced schema that is
        itself a $ref is merged with its own target, so aliases keep the definition they name. A warning for literals
        no value meets names ``where``, by default the location of ``obj``.
        """
        if not obj.ref:
            return obj

        resolved_ref = self._ref_path(ref := self._lenient_ref(obj.ref))
        if self._is_ref_circular(resolved_ref):
            return obj

        ref_dict, resolved_ref = self._ref_target_dump(ref, resolved_ref)
        current_dict = obj.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True)
        merged, unsatisfiable = self._intersect_ref_siblings(
            ref_dict,
            current_dict,
            where or str(obj.schema_location or redact_reference(obj.ref)),
            siblings_are_parts=self._ref_siblings_are_parts_of(obj),
        )
        merged.pop("$ref", None)
        if (
            "contains" in obj.extras
            and (contains_bounds := self._get_contains_bounds(self._schema_object(ref_dict))) is not None
        ):
            sibling_contains = _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS & obj.extras.keys()
            extras_key = JsonSchemaObject.__extra_key__
            merged[extras_key] = {
                **{
                    key: value
                    for key, value in merged[extras_key].items()
                    if key not in _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
                },
                **{key: obj.extras[key] for key in sibling_contains},
            }
            self._merge_schema_constraints(merged, [contains_bounds], intersect=True)

        merged_obj = self._schema_object(merged)
        self._mark_reported_value_conflict(merged_obj, unsatisfiable)
        merged_obj.__dict__[_MERGED_REF_TARGET_KEY] = resolved_ref
        return _locate(merged_obj, obj.schema_location)

    def _pin_recursive_refs(self, schema: dict[str, Any], defining_ref: str) -> None:
        """Rewrite in place each $recursiveRef of a schema dumped from another document that has a fixed target there.

        It becomes a $ref to that target in the defining document. The other references keep their edges, which
        resolve them in their own document wherever the schema is read.
        """
        stack: list[tuple[dict[str, Any], tuple[str | int, ...]]] = [(schema, ())]
        while stack:
            node, path = stack.pop()
            match node:
                case {"$recursiveRef": "#"} if "$ref" not in node and (
                    target := self._recursive_ref_target(defining_ref, path)
                ):
                    del node["$recursiveRef"]
                    node["$ref"] = target
            stack.extend(
                (child, (*path, *child_path)) for child_path, child in self._iter_schema_resource_children(node)
            )

    def _recursive_ref_target(self, defining_ref: str, path: tuple[str | int, ...]) -> str | None:
        """Return the location a $recursiveRef in a schema dumped from another document names there.

        It resolves as it does when that document is parsed, see _resolve_recursive_ref. The outermost
        $recursiveAnchor of the dynamic scope extends an anchored target, so the reference is left to it
        when the document the schema is merged into declares $recursiveAnchor.
        """
        file_part, _, pointer = defining_ref.partition("#")
        _, document = self._prepared_ref_document(file_part)
        parts = [*split_json_pointer(document, pointer), *map(str, path)]
        if (target := _unanchored_resource_ref(document, parts)) is None:
            anchors = self._document_recursive_anchors(document)
            current_ref = _pointer_ref(parts)
            target = _nearest_recursive_anchor(anchors, current_ref)
            self._check_anchor_answer(
                "$recursiveRef",
                current_ref,
                target,
                lambda _, declared: _nearest_recursive_anchor(declared, current_ref),
                _ref_path_parts(file_part),
            )
            if (target != "#" or "#" in anchors) and self._declares_recursive_anchor():
                return None
        with self._inherited_ref_context(defining_ref):
            location = self._schema_location(self.model_resolver.resolve_ref(target))
        return self._derived_ref(
            location,
            SchemaLocation(file_part, tuple(parts)),
            "$recursiveRef",
            target,
            RefOrigin.RECURSIVE_REF,
        )

    def _declares_recursive_anchor(self) -> bool:
        """Return whether the document being parsed has registered a `$recursiveAnchor` so far."""
        anchored = bool(self._recursive_anchor_index.get(tuple(self.model_resolver.current_root)))
        self._check_anchor_answer("$recursiveAnchor", "#", anchored, lambda _, anchors: bool(anchors))
        return anchored

    def _document_recursive_anchors(self, raw: dict[str, Any]) -> list[str]:
        """Return the $recursiveAnchor references a document registers when parsed: its root and definitions."""
        anchors = ["#"] if raw.get("$recursiveAnchor") is True else []
        for schema_path, split_schema_path in self.schema_paths:
            if definitions := cast("dict[str, YamlValue]", get_model_by_path(raw, split_schema_path)):
                entries = chain(
                    ((str(key), model, [schema_path, str(key)]) for key, model in definitions.items()),
                    self._iter_schema_definition_entries(definitions, [schema_path]),
                )
                anchors.extend(
                    self._anchor_ref_path((), path)
                    for _, model, path in entries
                    if isinstance(model, dict) and model.get("$recursiveAnchor") is True
                )
                break
        return anchors

    def _is_ref_circular(self, resolved_ref: str) -> bool:
        """Check if a resolved $ref target contains a circular reference (cached).

        A target whose references reach a document or pointer that cannot be loaded counts as circular.
        """
        if resolved_ref in self._circular_ref_cache:
            return self._circular_ref_cache[resolved_ref]
        try:
            result = self._has_ref_cycle(resolved_ref)
        except (Error, NotImplementedError, OSError, ValueError):
            result = True
        self._circular_ref_cache[resolved_ref] = result
        return result

    def _has_ref_cycle(self, target: str) -> bool:
        """Check if the schema at target reaches a reference back to target.

        References are followed depth first, each one once, on an explicit stack: a frame per followed reference
        holds the resolution context of its document and the schemas left to walk there, so any chain length works.
        Reaching a boolean, number or null schema counts as a cycle, since it has no keywords to merge.
        """
        visited: set[str] = set()
        frames: list[tuple[Generator[object, None, None], list[dict[str, Any] | list[Any]]]] = []
        try:
            if self._push_ref_cycle_frame(target, visited, frames):
                return True
            while frames:
                if not (nodes := frames[-1][1]):
                    frames.pop()[0].close()
                    continue
                if isinstance(node := nodes.pop(), list):
                    nodes.extend(item for item in reversed(node) if isinstance(item, (dict, list)))
                    continue
                nodes.extend(reversed(list(_iter_ref_walk_children(node))))
                if isinstance(ref_value := node.get("$ref"), str):
                    try:
                        resolved = self._ref_path(ref_value)
                    except (Error, ValueError):
                        resolved = ref_value
                    if resolved == target:
                        return True
                    if resolved not in visited and self._push_ref_cycle_frame(resolved, visited, frames):
                        return True
        finally:
            while frames:
                frames.pop()[0].close()
        return False

    def _push_ref_cycle_frame(
        self,
        ref_to_check: str,
        visited: set[str],
        frames: list[tuple[Generator[object, None, None], list[dict[str, Any] | list[Any]]]],
    ) -> bool:
        """Enter the resolution context of ref_to_check and push a frame with the schema it names.

        Returns whether that schema is a boolean, number or null, which has no keywords to walk.
        """
        visited.add(ref_to_check)
        document = self._ref_cycle_document(ref_to_check)
        raw_obj = next(document)
        frames.append((document, [raw_obj] if isinstance(raw_obj, (dict, list)) else []))
        return not isinstance(raw_obj, (dict, list, str))

    def _ref_cycle_document(self, ref_to_check: str) -> Generator[object, None, None]:
        """Yield the value ref_to_check names, resolving references relative to its document until closed."""
        file_part, _, fragment = ref_to_check.partition("#")
        if file_part and is_url(file_part):
            base_path = None
            root_path = [file_part]
        else:
            base_path = Path(file_part).parent if file_part else self.model_resolver.current_base_path
            root_path = file_part.split("/") if file_part else self.model_resolver.current_root
        with (
            self.model_resolver.current_base_path_context(base_path),
            self.model_resolver.base_url_context(file_part or self.model_resolver.base_url),
            self.model_resolver.current_root_context(root_path),
        ):
            raw_doc = self._get_ref_body(file_part) if file_part else self.raw_obj
            yield get_model_by_path(raw_doc, [p for p in fragment.split("/") if p]) if fragment else raw_doc

    def _merge_primitive_schemas(self, items: list[JsonSchemaObject]) -> JsonSchemaObject:
        """Merge multiple primitive schemas by computing the intersection of their constraints."""
        if len(items) == 1:
            return items[0]

        base_dict = JsonSchemaParser._first_typed_schema_dict(items)
        self._merge_schema_constraints(base_dict, items, intersect=True)

        return self._schema_object(base_dict)

    def _merge_primitive_schemas_for_allof(self, items: list[JsonSchemaObject]) -> JsonSchemaObject | None:
        """Merge primitive schemas for allOf, respecting allof_merge_mode setting."""
        if len(items) == 1:
            return items[0]  # pragma: no cover

        formats = {item.format for item in items if item.format}
        if len(formats) > 1:
            return None

        merged_format = formats.pop() if formats else None

        if self.allof_merge_mode != AllOfMergeMode.NoMerge:
            merged = self._merge_primitive_schemas(items)
            merged_dict = merged.model_dump(exclude_unset=True, by_alias=True)
            if merged_format:
                merged_dict["format"] = merged_format
            return self._schema_object(merged_dict)

        base_dict = JsonSchemaParser._first_typed_schema_dict(items)
        self._merge_schema_constraints(base_dict, items, intersect=False)

        if merged_format:
            base_dict["format"] = merged_format

        return self._schema_object(base_dict)

    @staticmethod
    def _first_typed_schema_dict(items: list[JsonSchemaObject]) -> dict[str, Any]:
        base_dict = next(
            (item.model_dump(exclude_unset=True, by_alias=True) for item in items if item.type),
            {},
        )
        if (original_domain := _numeric_type_domain(base_dict.get("type", ""))) is None:
            return base_dict
        domain = original_domain
        original_type = base_dict["type"]
        for item in items:
            if item.type and item.type != original_type:
                if (item_domain := _numeric_type_domain(item.type)) is None:
                    return base_dict
                domain &= item_domain
        if not domain:
            return base_dict
        if domain != original_domain:
            types = original_type if isinstance(original_type, list) else [original_type]
            narrowed_types = dict.fromkeys(
                "integer" if type_ == "number" and "number" not in domain else type_
                for type_ in types
                if type_ in domain or (type_ == "number" and "integer" in domain)
            )
            base_dict["type"] = next(iter(narrowed_types)) if len(narrowed_types) == 1 else list(narrowed_types)
        return base_dict

    @staticmethod
    def _schema_constraint_value(item: JsonSchemaObject, field: str) -> Any:
        return value if (value := getattr(item, field, None)) is not None else item.extras.get(field)

    @staticmethod
    def _intersect_multiple_of(val1: Any, val2: Any) -> Any:
        """Return the least common multiple for JSON Schema multipleOf values."""
        with suppress(TypeError, ValueError, ZeroDivisionError):
            return intersect_multiple_of(val1, val2)
        return val1  # pragma: no cover

    @staticmethod
    def _intersect_constraint(field: str, val1: Any, val2: Any) -> Any:
        """Compute the intersection of two constraint values."""
        if field == "multipleOf":
            return JsonSchemaParser._intersect_multiple_of(val1, val2)
        v1 = val1.value if isinstance(val1, UnionIntFloat) else val1
        v2 = val2.value if isinstance(val2, UnionIntFloat) else val2
        return val1 if intersect_constraint(field, v1, v2) is v1 else val2

    def _merge_schema_constraints(
        self,
        base_dict: dict[str, Any],
        items: list[JsonSchemaObject],
        *,
        intersect: bool,
    ) -> None:
        patterns: list[str] | None = None
        for item in items:
            for field in JsonSchemaObject.__constraint_fields__:
                if (value := JsonSchemaParser._schema_constraint_value(item, field)) is None:
                    continue
                if intersect and field == "pattern":
                    if patterns is None:
                        patterns = [] if (base_pattern := base_dict.get(field)) is None else [base_pattern]
                    patterns.append(value)
                    continue
                if intersect and field in base_dict and base_dict[field] is not None:
                    base_dict[field] = JsonSchemaParser._intersect_constraint(field, base_dict[field], value)
                else:
                    base_dict[field] = value
        if patterns:
            base_dict["pattern"] = self._conjoined_pattern(
                patterns,
                _ALL_OF_MEMBER_SUFFIX.sub(
                    "", next((str(item.schema_location) for item in items if item.schema_location), "allOf")
                ),
            )
        if (
            base_dict.get("type") in ("null", ["null"])
            and all(not item.type or _numeric_type_domain(item.type) is not None for item in items)
            and any("integer" in (_numeric_type_domain(item.type or "") or ()) for item in items)
        ):
            for field in NUMBER_KEYWORD_ORDER:
                base_dict.pop(field, None)

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
                    self._unregister_temporary_type(nested_type)
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
        if any(self._is_false_schema_item(item) for item in allof_items):
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

        if (ref_items or ref_data_types) and not primitive_items and not object_items:
            if ref_data_types:
                return ref_data_types[0]
            ref = ref_items[0].ref
            if ref:
                return self.get_ref_data_type(ref)
            return None  # pragma: no cover

        if (ref_items or ref_data_types) and (primitive_items or object_items or constraint_only_items):
            ignored_count = len(primitive_items) + len(constraint_only_items)
            if warn_on_ref_constraints and ignored_count > 0:
                warn(
                    f"allOf combines $ref with {ignored_count} constraint(s) that will be ignored "
                    f"in inherited field type resolution. Consider defining constraints in the referenced schema.",
                    stacklevel=4,
                )
            if ref_data_types:
                return ref_data_types[0]
            ref = ref_items[0].ref
            if ref:
                return self.get_ref_data_type(ref)
            return None  # pragma: no cover

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

    def _build_lightweight_array_type(
        self,
        schema: JsonSchemaObject,
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
    ) -> DataType:
        """Build a lightweight list type from array item schemas."""
        item_schemas, _, _, tuple_item_count = self._get_array_item_schemas(
            schema,
            include_true_tail_schema=True,
            force_prefix_items=True,
        )
        is_homogeneous_tuple = tuple_item_count is not None
        item_types = (
            []
            if is_homogeneous_tuple and tuple_item_count == 0
            else [
                item_type
                for item_schema in item_schemas
                if (
                    item_type := self._build_lightweight_item_type(
                        item_schema, depth, visited, max_depth, max_union_elements
                    )
                )
                is not None
            ]
        )
        if not item_types and not (is_homogeneous_tuple and tuple_item_count == 0):
            item_types = [DataType(type=ANY, import_=IMPORT_ANY)]
        return self.data_type(
            data_types=item_types,
            is_list=not is_homogeneous_tuple,
            is_tuple=is_homogeneous_tuple,
            tuple_item_count=tuple_item_count if is_homogeneous_tuple else None,
        )

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

    def _is_false_schema_item(self, item: JsonSchemaObject | bool) -> bool:  # noqa: FBT001, PLR6301
        return item is False or (isinstance(item, JsonSchemaObject) and item.is_boolean_schema_false)

    def _contains_false_schema(self, items: Iterable[JsonSchemaObject | bool]) -> bool:
        return any(self._is_false_schema_item(item) for item in items)

    def _schema_requires_model_type(
        self,
        item: JsonSchemaObject,
        *,
        resolve_ref: bool = False,
        visited_refs: frozenset[_TargetNode] | None = None,
    ) -> bool:
        """Return whether a schema describes a model-shaped object."""
        if item.ref:
            if not resolve_ref:
                return True
            if visited_refs is None:
                visited_refs = frozenset()
            if (node := self._key_node(self._ref_path(item.ref))) in visited_refs:
                return True
            return self._schema_requires_model_type(
                self._load_ref_schema_object(item.ref),
                resolve_ref=True,
                visited_refs=visited_refs | {node},
            )
        return bool(
            item.properties is not None
            or item.patternProperties is not None
            or item.propertyNames is not None
            or item.additionalProperties is not None
            or item.unevaluatedProperties is not None
            or item.required
            or item.minProperties is not None
            or item.maxProperties is not None
            or item.type == "object"
            or (isinstance(item.type, list) and "object" in item.type)
        )

    def _allof_requires_model_type(
        self,
        items: Iterable[JsonSchemaObject | bool],
        *,
        resolve_ref: bool = False,
        visited_refs: frozenset[_TargetNode] | None = None,
    ) -> bool:
        """Return whether allOf members describe a model-shaped schema."""
        for item in items:
            if not isinstance(item, JsonSchemaObject):
                continue
            if self._schema_requires_model_type(item, resolve_ref=resolve_ref, visited_refs=visited_refs):
                return True
            if item.allOf and self._allof_requires_model_type(item.allOf, resolve_ref=True, visited_refs=visited_refs):
                return True
            if item.anyOf and self._allof_requires_model_type(item.anyOf, resolve_ref=True, visited_refs=visited_refs):
                return True
            if item.oneOf and self._allof_requires_model_type(item.oneOf, resolve_ref=True, visited_refs=visited_refs):
                return True
        return False

    def _schema_has_own_value_keywords(self, schema: JsonSchemaObject) -> bool:  # noqa: PLR6301
        return bool(schema.type or schema.format or schema.enum or schema.has_constraint or "const" in schema.extras)

    def _without_allof_keywords(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
        schema_dict.pop("allOf", None)
        return self._schema_object(schema_dict)

    def _build_lightweight_allof_type(
        self,
        schema: JsonSchemaObject,
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
    ) -> DataType | None:
        return self._build_allof_type(
            self._allof_lightweight_items(schema),
            depth,
            visited,
            max_depth,
            max_union_elements,
        )

    def _is_all_of_value_part(self, schema: JsonSchemaObject) -> bool:
        """Return whether an allOf member is a schema of one value whose keywords the intersection table reads.

        A ``pattern`` the static scanner cannot certify as portable keeps the earlier form of the site.
        """
        return not (
            schema.ref
            or schema.is_boolean_schema_false
            or schema.anyOf
            or schema.oneOf
            or schema.prefixItems
            or schema.recursiveRef
            or schema.dynamicRef
            or schema.custom_type_path
            or _ALL_OF_VALUE_PART_EXCLUDED_EXTRAS.intersection(schema.extras)
            or self._schema_requires_model_type(schema)
            or (schema.pattern is not None and not is_portable_pattern(schema.pattern))
        )

    def _collect_all_of_value_parts(
        self, schema: JsonSchemaObject, parts: list[JsonSchemaObject], *, resolve_refs: bool = False
    ) -> bool:
        """Append the parts of an allOf schema to ``parts``: its members in document order, then its own keywords.

        A member that is itself an allOf contributes its parts, and with ``resolve_refs`` a ``$ref`` member contributes
        those of the schema it references, merged with its siblings. The own keywords are a part when they write a
        value keyword or an item schema. Return False when a part is no schema of one value.
        """
        for item in schema.allOf:
            if item is True:
                continue
            if not isinstance(item, JsonSchemaObject):
                return False
            member = self._merge_ref_with_schema(item, str(schema.schema_location or "allOf")) if resolve_refs else item
            if not self._is_all_of_value_part(member):
                return False
            if member.allOf:
                if not self._collect_all_of_value_parts(member, parts, resolve_refs=resolve_refs):
                    return False
            else:
                parts.append(member)
        if self._schema_has_own_value_keywords(schema) or any(
            getattr(schema, keyword) is not None for keyword in _ALL_OF_VALUE_ITEM_KEYWORDS
        ):
            if not self._is_all_of_value_part(own := self._without_allof_keywords(schema)):
                return False
            parts.append(own)
        return True

    def _all_of_value_row(self, obj: JsonSchemaObject) -> dict[str, Any]:
        """Return the schema the lightweight type of an allOf value schema is built from, as a dump.

        It is the merge ``_build_allof_type`` writes for members that are value schemas (the parts of
        ``_collect_all_of_value_parts``): the first typed member with every member's constraints intersected, and
        nothing when it writes no type.
        """
        items = [item for item in self._allof_lightweight_items(obj) if isinstance(item, JsonSchemaObject)]
        if len(items) == 1:
            return items[0].model_dump(exclude_unset=True, by_alias=True)
        if (
            groups := self._group_allof_items(
                items, 0, frozenset(), _LIGHTWEIGHT_MAX_DEPTH, _LIGHTWEIGHT_MAX_UNION_ELEMENTS
            )
        ) is None or not groups.primitives:
            return {}
        return self._merge_primitive_schemas([*groups.primitives, *groups.constraint_only]).model_dump(
            exclude_unset=True, by_alias=True
        )

    def _intersect_all_of_value_parts(
        self, parts: list[JsonSchemaObject], row: dict[str, Any], where: str | None
    ) -> JsonSchemaObject:
        """Return the intersection of the parts of an allOf value schema, keeping the merge ``row`` where it is exact.

        A keyword the row drops (another member's ``enum``, ``const`` or item schemas, a nested member's bounds, a type
        list) is taken from the first part that has it before the parts are intersected: ``additionalItems`` only with
        the ``items`` of its own part, which it qualifies, and item schemas only where the intersected ``type`` admits
        arrays, since they constrain nothing else. A keyword whose parts have no value
        in common keeps the row's value, with a warning naming ``where`` unless it is None.
        """
        row.pop("allOf", None)
        dumps = [part.model_dump(exclude_unset=True, by_alias=True) for part in parts]
        extras_key = JsonSchemaObject.__extra_key__
        row_extras = row[extras_key] = dict(row.get(extras_key, {}))
        added = {
            keyword: next(dump[keyword] for dump in dumps if keyword in dump)
            for keyword in (
                "type",
                "enum",
                "nullable",
                "items",
                "unevaluatedItems",
                *JsonSchemaObject.__constraint_field_order__,
            )
            if keyword not in row and any(keyword in dump for dump in dumps)
        }
        if "items" in added and "additionalItems" not in row:
            items_part = next(dump for dump in dumps if "items" in dump)
            if "additionalItems" in items_part:
                added["additionalItems"] = items_part["additionalItems"]
        row.update(added)
        if "const" not in row_extras and (
            const_part := next((dump for dump in dumps if "const" in dump.get(extras_key, {})), None)
        ):
            row_extras["const"] = added["const"] = const_part[extras_key]["const"]
        intersection = intersect_keywords(dumps, row, extras_key=extras_key)
        keywords = intersection.keywords
        if where is not None:
            self._warn_intersection(where, intersection, _ALL_OF_PARTS)
        dropped = set(intersection.unsatisfiable)
        if not _admits_arrays(keywords.get("type")):
            dropped.update(_ALL_OF_VALUE_ITEM_KEYWORDS)
        for keyword in added.keys() & dropped:
            if keyword == "const":
                keywords[extras_key] = {key: value for key, value in keywords[extras_key].items() if key != "const"}
            else:
                keywords.pop(keyword)
        return self._schema_object(keywords)

    def _intersect_typed_value_parts(self, parts: list[JsonSchemaObject], where: str | None) -> JsonSchemaObject:
        """Return the intersection of value parts whose row is the first typed part with every part's constraints."""
        return self._intersect_all_of_value_parts(
            parts, self._merge_primitive_schemas(parts).model_dump(exclude_unset=True, by_alias=True), where
        )

    def _reference_value_parts(self, ref: str) -> list[JsonSchemaObject] | None:
        """Return the value parts of the schema ``ref`` names, or None when it is no schema of one value."""
        parts: list[JsonSchemaObject] = []
        return (
            parts
            if self._collect_all_of_value_parts(
                self._schema_object({"allOf": [{"$ref": ref}]}), parts, resolve_refs=True
            )
            else None
        )

    def _all_of_value_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the schema of the values an inline allOf value schema admits: its parts intersected.

        The lightweight merge (``_all_of_value_row``) stays wherever it enforces what the intersection enforces; the
        values of an ``enum`` or ``const`` are written as the literal check filters them (``_build_lightweight_type``).
        None when a member is a reference or no schema of one value: such a site keeps its earlier form, as does
        every site without --intersect-allof-keywords.
        """
        parts: list[JsonSchemaObject] = []
        if not self.intersects_allof_keywords or not self._collect_all_of_value_parts(obj, parts):
            return None
        return self._intersect_all_of_value_parts(
            parts, self._all_of_value_row(obj), str(obj.schema_location or "allOf")
        )

    def _enforced_value_keywords(
        self, schema: JsonSchemaObject
    ) -> tuple[frozenset[str], bool, dict[str, Any], dict[str, Any]]:
        """Return what a value schema enforces: its types, null, the constraints its type takes and its other keywords.

        The other keywords (``format``, ``enum`` and item schemas) are compared as written.
        """
        types = frozenset(
            () if schema.type is None else (schema.type,) if isinstance(schema.type, str) else schema.type
        )
        constraints = self._temporary_constraint_values(schema, self._build_lightweight_type(schema))
        written = schema.model_dump(
            include={"format", "enum", *_ALL_OF_VALUE_ITEM_KEYWORDS}, exclude_unset=True, by_alias=True
        )
        return (
            types,
            bool(schema.nullable),
            {key: value.value if isinstance(value, UnionIntFloat) else value for key, value in constraints.items()},
            written,
        )

    def _output_enforced_value_keywords(
        self, schema: JsonSchemaObject
    ) -> tuple[frozenset[str], bool, dict[str, Any], dict[str, Any]]:
        """Return what a value schema enforces in this output: its constraints only where the output checks them.

        Types, null, formats, literals and item schemas count in every output (``_enforced_value_keywords``). The
        constraints beside an ``enum`` or ``const`` count in none: its literals are those that meet them.
        """
        types, null, constraints, written = self._enforced_value_keywords(schema)
        checked = self._output_model_context.checks_constraints and not (schema.enum or "const" in schema.extras)
        return types, null, constraints if checked else {}, written

    def _layers_on_reference(self, target_keys: Collection[str], keys: Collection[str]) -> bool:
        """Return whether the output writes constraints ``keys`` beside a reference to a target with ``target_keys``.

        Only an alias takes them, and an output that rejects a constraint the alias sets as well (msgspec ``Meta``)
        takes only the others.
        """
        return self.data_model_root_type.IS_ALIAS and (
            self._output_model_context.layers_repeated_constraints or not set(target_keys).intersection(keys)
        )

    def _reference_site_value(
        self, obj: JsonSchemaObject, *, layered: bool
    ) -> tuple[list[JsonSchemaObject], dict[str, Any]] | None:
        """Return the parts and the value of an allOf site whose earlier form leaves out what its parts enforce.

        That form is the type of the site's first ``$ref`` member; with ``layered`` it writes the site's own
        constraints beside it, as an output with field constraints does where the site is a model. It fails when the
        reference is a model class or the output rejects a constraint the referenced alias sets as well, unless
        ``--collapse-root-models`` inlines the site into its holders, which merge the constraints; else it enforces the
        target's constraints with the written ones over those of the same keyword. Where it does not fail, the site
        keeps it (None), under ``--use-site-models-for-constrained-refs`` only where it also enforces what the
        intersection of the parts enforces in this output (``_output_enforced_value_keywords``). None also for a site
        with a ``$ref`` member written with siblings, whose parts are no value schemas or are not intersected here
        (``_intersects_value_parts``), and for one whose parts have no value in common (warned about) where that form
        does not fail. The value is the reference alone when the intersection enforces nothing beyond the referenced
        schema, the reference with the intersection's other constraints layered on it where the output writes field
        constraints and takes them exactly, else the intersection.
        """
        if (
            not (refs := _bare_ref_members(obj))
            or (parts := self._all_of_value_parts(obj)) is None
            or not self._intersects_value_parts(parts)
            or (target_parts := self._reference_value_parts(refs[0])) is None
        ):
            return None
        satisfiable = self._satisfiable_all_of_value_parts(obj) is not None
        written = self._temporary_constraint_values(obj, self.get_ref_data_type(refs[0])) if layered else {}
        target = self._intersect_typed_value_parts(target_parts, None)
        intersection = _enum_narrowed_by_part_consts(
            self._intersect_typed_value_parts(parts, str(obj.schema_location or "allOf")),
            parts,
            const_support=self.schema_features.const_support,
        )
        target_dump = target.model_dump(exclude_unset=True, by_alias=True)
        target_keys = {keyword for keyword in target.__constraint_field_order__ if getattr(target, keyword) is not None}
        fails = bool(written) and not (self.collapse_root_models or self._layers_on_reference(target_keys, written))
        if not fails and (
            not satisfiable
            or not self.config.use_site_models_for_constrained_refs
            or self._output_enforced_value_keywords(self.SCHEMA_OBJECT_TYPE.model_validate({**target_dump, **written}))
            == self._output_enforced_value_keywords(intersection)
        ):
            return None
        enforced = self._enforced_value_keywords(intersection)
        target_enforced = self._enforced_value_keywords(target)
        if len(refs) == 1 and enforced == target_enforced:
            return parts, {"$ref": refs[0]}
        value = intersection.model_dump(exclude_unset=True, by_alias=True)
        added = {
            keyword: value[keyword]
            for keyword in intersection.__constraint_field_order__
            if keyword in value and value[keyword] != target_dump.get(keyword)
        }
        if (
            self.field_constraints
            and len(refs) == 1
            and (enforced[0], enforced[1], enforced[3]) == (target_enforced[0], target_enforced[1], target_enforced[3])
            and self._layers_on_reference(target_keys, added)
        ):
            return parts, {"allOf": [{"$ref": refs[0]}], **added}
        return parts, value

    def _all_of_reference_value_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the value schema of an allOf site whose earlier form writes its own constraints beside a reference.

        That form is the type of the first ``$ref`` member with the site's own constraints. Under
        ``--use-site-models-for-constrained-refs`` a site of bare ``$ref`` members keeps it where it enforces what the
        site's parts enforce together and does not fail (``_reference_site_value``). Any other site keeps it wherever
        the output takes the constraints beside the reference, else it takes the reference alone where the intersection
        of its parts enforces nothing beyond it, and else that intersection. None for every other site, which keeps its
        earlier form: a site without own constraints the referenced type takes, members that are no value schemas and
        nested combinators. Without --intersect-allof-keywords a site leaves its earlier form only where that form
        fails, so it keeps it under ``--collapse-root-models``, whose holders merge the constraints.
        """
        members = [item for item in obj.allOf if isinstance(item, JsonSchemaObject)]
        refs = [member.ref for member in members if member.ref]
        if not refs or not (constraints := self._temporary_constraint_values(obj, self.get_ref_data_type(refs[0]))):
            return None
        if self.config.use_site_models_for_constrained_refs and _bare_ref_members(obj):
            site = self._reference_site_value(obj, layered=True)
            return None if site is None else _derived_site(self.SCHEMA_OBJECT_TYPE.model_validate(site[1]), refs)
        parts: list[JsonSchemaObject] = []
        if (
            (self.collapse_root_models and not self.intersects_allof_keywords)
            or any(member.allOf or member.anyOf or member.oneOf for member in members)
            or (target_parts := self._reference_value_parts(refs[0])) is None
            or not self._collect_all_of_value_parts(obj, parts, resolve_refs=True)
        ):
            return None
        target = self._intersect_typed_value_parts(target_parts, None)
        target_keys = {keyword for keyword in target.__constraint_field_order__ if getattr(target, keyword) is not None}
        if self._layers_on_reference(target_keys, constraints):
            return None
        intersection = self._intersect_typed_value_parts(parts, str(obj.schema_location or "allOf"))
        if len(refs) == 1 and self._enforced_value_keywords(intersection) == self._enforced_value_keywords(target):
            return self._schema_object({"$ref": refs[0]})
        return intersection

    def _allof_lightweight_items(self, schema: JsonSchemaObject) -> Sequence[JsonSchemaObject | bool]:
        """Return the members ``_build_lightweight_allof_type`` builds a type from: own keywords first, then allOf."""
        allof_items: Sequence[JsonSchemaObject | bool] = schema.allOf or []
        if self._schema_has_own_value_keywords(schema):
            return [self._without_allof_keywords(schema), *allof_items]
        return allof_items

    def _raise_unsatisfiable_schema(  # noqa: PLR6301
        self, obj: JsonSchemaObject, path: list[str], keyword: str
    ) -> None:
        """Reject the schema ``obj``, whose ``keyword`` branches leave no value it accepts."""
        raise SchemaParseError(
            message=f"{keyword} contains a boolean false schema that makes the schema unsatisfiable",
            path=_named_path(obj.schema_location, path),
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
            return self._build_lightweight_array_type(schema, depth, visited, max_depth, max_union_elements)

        if schema.allOf:
            return self._build_lightweight_allof_type(schema, depth, visited, max_depth, max_union_elements)

        if schema.type and not isinstance(schema.type, list) and schema.type != "object":
            return self.get_data_type(schema)
        if isinstance(schema.type, list):
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

    def _is_list_with_any_item_type(self, data_type: DataType | None) -> bool:  # noqa: PLR6301
        """Return True when data_type represents List[Any] (including nested lists)."""
        if not data_type:  # pragma: no cover
            return False

        candidate = data_type
        if not candidate.is_list and len(candidate.data_types) == 1 and candidate.data_types[0].is_list:
            candidate = candidate.data_types[0]

        if not candidate.is_list or len(candidate.data_types) != 1:
            return False

        item_type = candidate.data_types[0]
        while len(item_type.data_types) == 1:
            inner = item_type.data_types[0]
            if (not item_type.is_list and inner.is_list) or item_type.is_list:
                item_type = inner
            else:
                break
        return item_type.type == ANY

    def _merge_property_schemas(
        self,
        parent_dict: dict[str, Any],
        child_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge parent and child property schemas for allOf; the result stands for the child schema."""
        if self.allof_merge_mode == AllOfMergeMode.NoMerge or "$ref" in child_dict:
            return _located(child_dict.copy(), _dump_location(child_dict))

        non_merged_fields: set[str] = set()
        if self.allof_merge_mode == AllOfMergeMode.Constraints:
            non_merged_fields = {"default", "examples", "example"}

        result = {key: value for key, value in parent_dict.items() if key not in non_merged_fields}
        if (
            (child_type := child_dict.get("type")) is not None
            and child_dict.get("nullable") is not True
            and (not isinstance(child_type, list) or "null" not in child_type)
        ):
            result.pop("nullable", None)

        for key, value in child_dict.items():
            if (
                key in result
                and (
                    merged_schema_keyword := self._merge_inherited_schema_keyword(
                        key,
                        result[key],
                        value,
                        result,
                    )
                )
                is not _NO_INHERITED_SCHEMA_MERGE
            ):
                result[key] = merged_schema_keyword
            elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
                if "$ref" in value:
                    result[key] = value
                else:
                    result[key] = self._merge_property_schemas(result[key], value)
            else:
                result[key] = value
        return _located(result, _dump_location(child_dict))

    def _merge_inherited_nested_schemas(self, parent: object, child: object) -> object:
        """Intersect two schema-valued container keywords."""
        result = child
        match parent, child:
            case (False, _) | (_, False):
                result = False
            case True, _:
                pass
            case _, True:
                result = parent
            case dict() as parent_schema, dict() as child_schema:
                result = self._merge_property_schemas(parent_schema, child_schema)
            case _:
                pass
        return result

    def _get_inherited_positional_tail(  # noqa: PLR6301
        self,
        schema: JsonSchemaObject | dict[str, Any],
        key: str,
    ) -> object:
        """Return the schema governing positions beyond a tuple declaration."""
        items = schema.get("items") if isinstance(schema, dict) else schema.items
        additional_items = schema.get("additionalItems") if isinstance(schema, dict) else schema.additionalItems
        unevaluated_items = schema.get("unevaluatedItems") if isinstance(schema, dict) else schema.unevaluatedItems
        if key == "items":
            return additional_items if additional_items is not None else True
        if items is not None and not isinstance(items, list):
            return items
        return unevaluated_items if unevaluated_items is not None else True

    def _merge_inherited_schema_keyword(
        self,
        key: str,
        parent: object,
        child: object,
        parent_schema: dict[str, Any],
    ) -> object:
        """Merge schema-valued keywords while leaving ordinary values untouched."""
        if key in _INHERITED_POSITIONAL_SCHEMA_FIELDS and isinstance(parent, list) and isinstance(child, list):
            parent_tail = self._get_inherited_positional_tail(parent_schema, key)
            return [
                self._merge_inherited_nested_schemas(
                    parent[index] if index < len(parent) else parent_tail,
                    child_item,
                )
                for index, child_item in enumerate(child)
            ] + parent[len(child) :]
        if key in _INHERITED_NESTED_SCHEMA_FIELDS:
            return self._merge_inherited_nested_schemas(parent, child)
        return _NO_INHERITED_SCHEMA_MERGE

    def _get_inherited_override_shape(  # noqa: PLR6301
        self,
        schema: JsonSchemaObject,
    ) -> dict[str, Any]:
        """Return only keywords that can change an inherited property's type shape."""
        return _dump_without_keywords(
            schema,
            {
                *JsonSchemaObject.__constraint_fields__,
                *_INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS,
                *PROPERTY_COUNT_KEYWORDS,
                *_ANNOTATION_KEYWORDS,
                "nullable",
            },
        )

    def _get_inherited_type_shape(
        self,
        schema: JsonSchemaObject,
        canonical_ref: Callable[[str], str],
    ) -> dict[str, Any]:
        """Return structural schema keywords without inherited value restrictions; a tuple keeps its length.

        ``canonical_ref`` reads the ``$ref`` values of ``schema`` in the document that defines it.
        """
        schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
        shape = {key: value for key, value in schema_dict.items() if key in _INHERITED_TYPE_SHAPE_FIELDS}
        positional_items = schema.prefixItems or (schema.items if isinstance(schema.items, list) else None)
        if positional_items and schema.minItems == schema.maxItems == len(positional_items):
            shape["minItems"] = schema.minItems
            shape["maxItems"] = schema.maxItems
        elif (
            self.use_tuple_for_closed_arrays
            and (fixed_items := self._get_fixed_positional_items(schema, canonical_ref)) is not None
        ):
            shape["minItems"] = shape["maxItems"] = len(fixed_items)
        return shape

    def _get_inherited_schema_types(  # noqa: PLR0912
        self,
        schema: JsonSchemaObject,
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> frozenset[str]:
        """Infer JSON instance types accepted by an inherited schema."""
        schema, parent_ref, active = self._resolve_inherited_parent_property(schema, parent_ref, active)
        nullable_types = frozenset(("null",)) if schema.nullable is True else frozenset()
        direct_types: frozenset[str] = frozenset()
        match schema.type:
            case str() as schema_type:
                direct_types = frozenset((schema_type,))
            case list() as schema_types:
                direct_types = frozenset(schema_types)
            case _:
                pass
        if direct_types:
            return direct_types | nullable_types

        structural_types = (
            frozenset(("array",))
            if schema.is_array
            else frozenset(("object",))
            if (
                schema.is_object
                or schema.additionalProperties is not None
                or schema.patternProperties
                or schema.propertyNames is not None
            )
            else frozenset()
        )
        if structural_types:
            return structural_types | nullable_types

        values = schema.enum
        if "const" in schema.extras:
            values = [schema.extras["const"]]
        if values:
            value_types = {_get_json_value_type(value) for value in values}
            value_types.discard("")
            if value_types:
                return frozenset(value_types) | nullable_types

        combined_types: set[str] = set()
        for item in (*schema.anyOf, *schema.oneOf):
            if isinstance(item, JsonSchemaObject):
                combined_types.update(self._get_inherited_schema_types(item, parent_ref, active))
        if combined_types:
            return frozenset(combined_types) | nullable_types

        all_of_types: frozenset[str] | None = None
        for item in schema.allOf:
            if not isinstance(item, JsonSchemaObject):
                continue
            if not (item_types := self._get_inherited_schema_types(item, parent_ref, active)):
                continue
            all_of_types = item_types if all_of_types is None else all_of_types & item_types
        return (all_of_types or frozenset()) | nullable_types

    def _get_inherited_constraint_fields(self, schema: JsonSchemaObject) -> frozenset[str]:  # noqa: PLR6301
        """Return top-level validation constraints that need type-compatible placement."""
        fields = {field for _, fields in keyword_kinds() for field in fields if field in schema.model_fields_set}
        if schema.extras.keys() & _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS:
            fields.add(JsonSchemaObject.__extra_key__)
        return frozenset(fields)

    def _get_inherited_distributed_fields(self, schema: JsonSchemaObject) -> frozenset[str]:
        """Return type-specific keywords that must follow compatible union branches."""
        fields = set(self._get_inherited_constraint_fields(schema))
        for _, container_fields in _INHERITED_CONTAINER_TYPE_FIELDS:
            fields.update(container_fields & schema.model_fields_set)
        return frozenset(fields)

    def _get_inherited_field_compatible_types(self, field: str) -> frozenset[str]:  # noqa: PLR6301
        """Return the JSON instance types to which one schema keyword applies."""
        if field == JsonSchemaObject.__extra_key__:
            return frozenset({"array"})
        return next(
            (
                compatible_types
                for compatible_types, fields in (
                    *keyword_kinds(),
                    *_INHERITED_CONTAINER_TYPE_FIELDS,
                )
                if field in fields
            ),
            frozenset(),
        )

    def _select_inherited_distributed_shape(
        self,
        child: dict[str, Any],
        distributed_fields: frozenset[str],
        parent_types: frozenset[str],
    ) -> dict[str, Any]:
        """Select child keywords that apply to one inherited JSON type branch."""
        result: dict[str, Any] = {}
        for field, value in child.items():
            if field not in distributed_fields:
                continue
            compatible_types = self._get_inherited_field_compatible_types(field)
            if parent_types and parent_types.isdisjoint(compatible_types):
                continue
            selected_value = value
            if field == JsonSchemaObject.__extra_key__ and isinstance(value, dict):
                selected_value = {
                    key: nested_value
                    for key, nested_value in value.items()
                    if key in _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
                }
                if not selected_value:
                    continue
            result[field] = selected_value
        return result

    def _remove_inherited_distributed_shape(  # noqa: PLR6301
        self,
        child: dict[str, Any],
        distributed_fields: frozenset[str],
    ) -> dict[str, Any]:
        """Remove distributed keywords while preserving unrelated extension values."""
        result = {field: value for field, value in child.items() if field not in distributed_fields}
        if (
            JsonSchemaObject.__extra_key__ in distributed_fields
            and isinstance(extras := child.get(JsonSchemaObject.__extra_key__), dict)
            and (
                remaining_extras := {
                    key: value for key, value in extras.items() if key not in _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
                }
            )
        ):
            result[JsonSchemaObject.__extra_key__] = remaining_extras
        return result

    def _drop_incompatible_inherited_constraints(  # noqa: PLR6301
        self,
        child: dict[str, Any],
        parent_types: frozenset[str],
    ) -> dict[str, Any]:
        """Avoid applying schema constraints to incompatible Python value types."""
        result = child
        for compatible_types, constraint_fields in keyword_kinds():
            if parent_types and parent_types <= compatible_types:
                continue
            for field in constraint_fields & child.keys():
                if result is child:
                    result = child.copy()
                result.pop(field)
        if (
            (not parent_types or not parent_types <= {"array"})
            and isinstance(extras := child.get(JsonSchemaObject.__extra_key__), dict)
            and extras.keys() & _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
        ):
            if result is child:
                result = child.copy()
            filtered_extras = {
                key: value for key, value in extras.items() if key not in _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
            }
            if filtered_extras:
                result[JsonSchemaObject.__extra_key__] = filtered_extras
            else:
                result.pop(JsonSchemaObject.__extra_key__, None)
        return result

    def _is_unconstrained_inherited_schema(self, schema: object) -> bool:
        """Return whether a raw subschema adds no validation to an inherited type."""
        if schema is True:
            return True
        if not isinstance(schema, dict):
            return False
        schema_obj = self._schema_object(schema)
        return not schema_obj.has_constraint and not self._get_inherited_override_shape(schema_obj)

    def _remove_unconstrained_compositions(self, schema_dict: dict[str, Any]) -> dict[str, Any]:
        """Remove composition branches that make an inherited override universally valid."""
        result = schema_dict.copy()
        if isinstance(any_of := result.get("anyOf"), list) and any(
            self._is_unconstrained_inherited_schema(item) for item in any_of
        ):
            result.pop("anyOf")
        if (
            isinstance(one_of := result.get("oneOf"), list)
            and len(one_of) == 1
            and self._is_unconstrained_inherited_schema(one_of[0])
        ):
            result.pop("oneOf")
        if isinstance(all_of := result.get("allOf"), list):
            if constrained_items := [item for item in all_of if not self._is_unconstrained_inherited_schema(item)]:
                result["allOf"] = constrained_items
            else:
                result.pop("allOf")
        return result

    def _get_flattenable_inherited_constraint_items(
        self,
        keyword: str,
        raw_items: list[Any],
    ) -> list[dict[str, Any]] | None:
        """Return normalized constraint-only branches when composition flattening is equivalent."""
        items = raw_items
        match keyword:
            case "allOf":
                if any(item is False for item in raw_items):
                    return None
            case "anyOf" | "oneOf":
                items = [item for item in raw_items if item is not False]
            case _:  # pragma: no cover
                return None
        if len(items) != 1:
            return None

        normalized_items: list[dict[str, Any]] = []
        for item in items:
            match item:
                case True:
                    normalized_items.append({})
                case dict():
                    normalized_item = self._normalize_inherited_constraint_compositions(self._schema_object(item))
                    if self._get_inherited_override_shape(normalized_item):
                        return None
                    normalized_items.append(normalized_item.model_dump(exclude_unset=True, by_alias=True))
                case _:
                    return None
        return normalized_items

    def _normalize_inherited_nested_schema_value(self, value: object) -> object | None:
        """Normalize schema-valued container keywords without copying unchanged branches."""
        match value:
            case JsonSchemaObject() as nested_schema:
                if (normalized := self._normalize_inherited_constraint_compositions(nested_schema)) is nested_schema:
                    return None
                return normalized.model_dump(exclude_unset=True, by_alias=True)
            case list() as nested_schemas:
                normalized_schemas: list[JsonSchemaObject | bool] = []
                changed = False
                for nested_schema in nested_schemas:
                    if isinstance(nested_schema, JsonSchemaObject):
                        normalized = self._normalize_inherited_constraint_compositions(nested_schema)
                        changed = changed or normalized is not nested_schema
                        normalized_schemas.append(normalized)
                    else:
                        normalized_schemas.append(nested_schema)
                if changed:
                    return [
                        item.model_dump(exclude_unset=True, by_alias=True)
                        if isinstance(item, JsonSchemaObject)
                        else item
                        for item in normalized_schemas
                    ]
            case dict() as nested_schema_map:
                normalized_map: dict[str, JsonSchemaObject | bool] = {}
                changed = False
                for key, nested_schema in nested_schema_map.items():
                    if isinstance(nested_schema, JsonSchemaObject):
                        normalized = self._normalize_inherited_constraint_compositions(nested_schema)
                        changed = changed or normalized is not nested_schema
                        normalized_map[key] = normalized
                    else:
                        normalized_map[key] = nested_schema
                if changed:
                    return {
                        key: (
                            item.model_dump(exclude_unset=True, by_alias=True)
                            if isinstance(item, JsonSchemaObject)
                            else item
                        )
                        for key, item in normalized_map.items()
                    }
        return None

    def _normalize_inherited_constraint_compositions(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        """Flatten constraint-only compositions before resolving their inherited type."""
        nested_updates = {
            field_name: normalized
            for field_name in _INHERITED_NESTED_SCHEMA_FIELDS
            if (normalized := self._normalize_inherited_nested_schema_value(getattr(schema, field_name))) is not None
        }
        if not (nested_updates or schema.allOf or schema.anyOf or schema.oneOf):
            return schema
        schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
        schema_dict.update(nested_updates)
        changed = bool(nested_updates)
        for keyword in ("allOf", "anyOf", "oneOf"):
            match schema_dict.get(keyword):
                case list() as raw_items if (
                    raw_items
                    and (
                        normalized_items := self._get_flattenable_inherited_constraint_items(
                            keyword,
                            raw_items,
                        )
                    )
                    is not None
                ):
                    schema_dict.pop(keyword)
                    for normalized_item in normalized_items:
                        for key, value in normalized_item.items():
                            schema_dict.setdefault(key, value)
                    changed = True
                case _:
                    continue
        return self._schema_object(schema_dict) if changed else schema

    def _is_inherited_type_narrowing(  # noqa: PLR6301
        self,
        child_types: frozenset[str],
        parent_types: frozenset[str],
    ) -> bool:
        """Return whether explicit child JSON types are contained by the inherited types."""
        if not parent_types:
            return True
        compatible_parent_types = parent_types | (frozenset(("integer",)) if "number" in parent_types else frozenset())
        return child_types <= compatible_parent_types

    def _is_partial_inherited_property(
        self,
        child: JsonSchemaObject,
        parent: JsonSchemaObject,
        context: tuple[str, frozenset[_TargetNode]] = ("", frozenset()),
    ) -> bool:
        """Return whether a child schema only narrows metadata/constraints of its inherited type."""
        parent_ref, _ = context
        child_dict = self._remove_unconstrained_compositions(self._get_inherited_override_shape(child))
        if not child_dict:
            return True

        if (child_type := child_dict.pop("type", None)) is not None:
            child_types = frozenset((child_type,)) if isinstance(child_type, str) else frozenset(child_type)
            parent_types = self._get_inherited_schema_types(parent, parent_ref)
            if not self._is_inherited_type_narrowing(child_types, parent_types):
                return False

        for field_name, nested_child in child_dict.items():
            if field_name not in _INHERITED_NESTED_SCHEMA_FIELDS:
                return False
            nested_parent = getattr(parent, field_name)
            if nested_parent is None:
                continue
            parent_tail = (
                self._get_inherited_positional_tail(parent, field_name)
                if field_name in _INHERITED_POSITIONAL_SCHEMA_FIELDS and isinstance(nested_parent, list)
                else True
            )
            if not self._is_partial_inherited_nested_value(
                nested_child,
                nested_parent,
                parent_tail=parent_tail,
                context=context,
                schema_map=field_name in _INHERITED_SCHEMA_MAP_FIELDS,
            ):
                return False
        return True

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
        parent_ref, active = context
        if child is None or child is True or parent is True or parent is False:
            return True
        if not schema_map and isinstance(child, dict) and self._is_unconstrained_inherited_schema(child):
            return True
        result = False
        match child, parent:
            case dict() as child_schema, JsonSchemaObject() as parent_schema:
                child_obj = self._schema_object(child_schema)
                effective_parent = parent_schema
                effective_ref = parent_ref
                next_active = active
                if parent_schema.ref:
                    effective_parent, effective_ref, next_active = self._resolve_inherited_parent_property(
                        parent_schema,
                        parent_ref,
                        active - {self._inherited_node(self._ref_path(parent_schema.ref))},
                    )
                result = self._is_partial_inherited_property(
                    child_obj,
                    effective_parent,
                    (effective_ref, next_active),
                )
            case list() as child_schemas, list() as parent_schemas:
                result = all(
                    self._is_partial_inherited_nested_value(
                        child_schema,
                        parent_schemas[index] if index < len(parent_schemas) else parent_tail,
                        context=context,
                    )
                    for index, child_schema in enumerate(child_schemas)
                )
            case dict() as child_schema_map, dict() as parent_schema_map:
                result = all(
                    key in parent_schema_map
                    and self._is_partial_inherited_nested_value(
                        child_schema,
                        parent_schema_map[key],
                        context=context,
                    )
                    for key, child_schema in child_schema_map.items()
                )
            case _:
                pass
        return result

    def _has_inherited_constraints(self, schema: JsonSchemaObject) -> bool:
        """Return whether a partial schema contains constraints at any container depth."""
        if (
            schema.has_constraint
            or schema.enum
            or "const" in schema.extras
            or schema.extras.keys() & _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
            or schema.model_fields_set & PROPERTY_COUNT_KEYWORDS
        ):
            return True
        for field_name in _INHERITED_NESTED_SCHEMA_FIELDS:
            match getattr(schema, field_name):
                case JsonSchemaObject() as nested_schema if self._has_inherited_constraints(nested_schema):
                    return True
                case list() as nested_schemas if any(
                    isinstance(item, JsonSchemaObject) and self._has_inherited_constraints(item)
                    for item in nested_schemas
                ):
                    return True
                case dict() as nested_schema_map if any(
                    isinstance(item, JsonSchemaObject) and self._has_inherited_constraints(item)
                    for item in nested_schema_map.values()
                ):
                    return True
        return False

    def _mark_inherited_materialized_type_shapes(self, schema: JsonSchemaObject) -> None:
        """Mark synthesized schemas whose container constraints need a root wrapper."""
        schema.__dict__[_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY] = True
        for field_name in (*_INHERITED_NESTED_SCHEMA_FIELDS, "allOf", "anyOf", "oneOf"):
            match getattr(schema, field_name):
                case JsonSchemaObject() as nested_schema:
                    self._mark_inherited_materialized_type_shapes(nested_schema)
                case list() as nested_schemas:
                    for nested_schema in nested_schemas:
                        if isinstance(nested_schema, JsonSchemaObject):
                            self._mark_inherited_materialized_type_shapes(nested_schema)
                case dict() as nested_schema_map:
                    for nested_schema in nested_schema_map.values():
                        if isinstance(nested_schema, JsonSchemaObject):
                            self._mark_inherited_materialized_type_shapes(nested_schema)

    def _preserve_inherited_materialized_type_shape(
        self,
        source: JsonSchemaObject,
        target: JsonSchemaObject,
    ) -> JsonSchemaObject:
        """Propagate the internal materialization marker across schema normalization."""
        if source.__dict__.get(_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY):
            self._mark_inherited_materialized_type_shapes(target)
        return target

    def _resolve_inherited_parent_property(
        self,
        schema: JsonSchemaObject,
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> tuple[JsonSchemaObject, str, frozenset[_TargetNode]]:
        """Resolve pure property references before comparing nested override shapes.

        A reference is cached by the key it resolves to, which its edge gives wherever the schema is read.
        """
        source_schema = schema
        cache_by_ref = False
        cache_identity: str | int = id(schema)
        match schema.ref:
            case str() as schema_ref if schema_ref:
                cache_by_ref = True
                cache_identity = self._ref_path(schema_ref)
            case _:
                pass
        cache_key: tuple[str | int, str, frozenset[_TargetNode]] | None = None
        if not cache_by_ref or not schema.has_ref_with_schema_keywords:
            cache_key = (
                cache_identity,
                parent_ref,
                active,
            )
            if (cached := self._inherited_parent_property_cache.get(cache_key)) is not None and (
                cache_by_ref or cached[0] is schema
            ):
                return cached[1]
        while schema.ref:
            resolved_ref = self._ref_path(schema.ref)
            if (node := self._inherited_node(resolved_ref)) in active:
                break
            active |= {node}
            referenced_schema = self._load_inherited_schema_object(resolved_ref)
            if schema.has_ref_with_schema_keywords:
                schema = self._intersect_ref_siblings_schema(
                    referenced_schema.model_dump(exclude_unset=True, by_alias=True),
                    schema.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True),
                    str(schema.schema_location or redact_reference(resolved_ref)),
                    siblings_are_parts=self._ref_siblings_are_parts_of(schema),
                )
            else:
                schema = referenced_schema
            parent_ref = resolved_ref
        result = schema, parent_ref, active
        if cache_key is not None:
            self._inherited_parent_property_cache[cache_key] = source_schema, result
        return result

    def _get_nested_inherited_active(
        self,
        parent: dict[str, Any],
        child: dict[str, Any],
        active: frozenset[_TargetNode],
    ) -> frozenset[_TargetNode]:
        """Permit recursive expansion only along the finite child override path."""
        if child and isinstance(parent_ref := parent.get("$ref"), str):
            return active - {self._inherited_node(self._ref_path(parent_ref))}
        return active

    def _merge_inherited_type_shape_value(
        self,
        parent: object,
        child: object,
        parent_ref: str,
        active: frozenset[_TargetNode],
    ) -> object:
        """Fill a child schema value from its inherited type without parent constraints."""
        result = child
        match parent, child:
            case False, _:
                result = False
            case _, False:
                result = False
            case _, True:
                result = parent
            case True, _:
                result = True
            case dict() as parent_schema, dict() as child_schema:
                result = self._merge_inherited_type_shape_dict(
                    parent_schema,
                    child_schema,
                    parent_ref,
                    self._get_nested_inherited_active(
                        parent_schema,
                        child_schema,
                        active,
                    ),
                )
            case _:
                pass
        return result

    def _merge_inherited_type_shape_keyword(
        self,
        key: str,
        parent: object,
        child: object,
        context: tuple[dict[str, Any], str, frozenset[_TargetNode]],
    ) -> object:
        """Merge one nested schema keyword while preserving the parent's type shape."""
        parent_schema, parent_ref, active = context
        if key in _INHERITED_POSITIONAL_SCHEMA_FIELDS and isinstance(parent, list) and isinstance(child, list):
            parent_tail = self._get_inherited_positional_tail(parent_schema, key)
            return [
                self._merge_inherited_type_shape_value(
                    parent[index] if index < len(parent) else parent_tail,
                    child_item,
                    parent_ref,
                    active,
                )
                for index, child_item in enumerate(child)
            ] + parent[len(child) :]
        if key in _INHERITED_SCHEMA_MAP_FIELDS and isinstance(parent, dict) and isinstance(child, dict):
            parent_map = cast("dict[str, object]", parent)
            child_map = cast("dict[str, object]", child)
            result = parent_map.copy()
            for name, child_schema in child_map.items():
                result[name] = (
                    self._merge_inherited_type_shape_value(
                        parent_schema_value,
                        child_schema,
                        parent_ref,
                        active,
                    )
                    if (parent_schema_value := parent_map.get(name)) is not None
                    else child_schema
                )
            return result
        return self._merge_inherited_type_shape_value(
            parent,
            child,
            parent_ref,
            active,
        )

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
        if not isinstance(parent, dict):
            return parent
        parent = cast("dict[str, Any]", parent)
        parent_ref, active = context
        parent_obj = self._schema_object(parent)
        parent_types = self._get_inherited_schema_types(parent_obj, parent_ref, active)
        if excludes_null and parent_types == {"null"}:
            return _NO_INHERITED_SCHEMA_MERGE
        branch_child = self._select_inherited_distributed_shape(
            child,
            distributed_fields,
            parent_types,
        )
        if excludes_null and "null" in parent_types:
            branch_child["nullable"] = False
        return _located(
            self._merge_inherited_type_shape_dict(
                parent,
                branch_child,
                parent_ref,
                self._get_nested_inherited_active(
                    parent,
                    branch_child,
                    active,
                ),
            ),
            _dump_location(parent),
        )

    def _merge_inherited_type_shape_dict(  # noqa: PLR0914
        self,
        parent: dict[str, Any],
        child: dict[str, Any],
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> dict[str, Any]:
        """Fill missing type-shape keywords while keeping child validation precedence."""
        parent_obj = self._schema_object(parent)
        effective_parent, effective_ref, next_active = self._resolve_inherited_parent_property(
            parent_obj,
            parent_ref,
            active,
        )
        parent_types = self._get_inherited_schema_types(effective_parent, effective_ref, next_active)
        parent_shape = self._get_inherited_type_shape(effective_parent, self._ref_path)
        child_obj = self._schema_object(child)
        excludes_null = self._null_facts(child_obj, "inherited-child").admits_null is False
        if excludes_null:
            parent_types -= {"null"}
        distributed_fields = self._get_inherited_distributed_fields(child_obj)
        child_shape = child
        parent_type_shape = parent_shape.get("type")
        if excludes_null and isinstance(parent_type_shape, list):
            filtered_types = [schema_type for schema_type in parent_type_shape if schema_type != "null"]
            if filtered_types:
                parent_shape["type"] = filtered_types[0] if len(filtered_types) == 1 else filtered_types
        if (union_key := next((key for key in ("anyOf", "oneOf") if parent_shape.get(key)), None)) is not None:
            merged_branches = [
                merged_branch
                for branch in cast("list[object]", parent_shape[union_key])
                if (
                    merged_branch := self._merge_inherited_union_branch(
                        branch,
                        child,
                        distributed_fields,
                        (effective_ref, next_active),
                        excludes_null=excludes_null,
                    )
                )
                is not _NO_INHERITED_SCHEMA_MERGE
            ]
            parent_shape[union_key] = merged_branches
            if distributed_fields:
                child_shape = self._remove_inherited_distributed_shape(
                    child,
                    distributed_fields,
                )
        elif distributed_fields and len(parent_types) > 1:
            parent_distributed_fields = self._get_inherited_distributed_fields(effective_parent) & parent_shape.keys()
            branch_parent_shape = parent_shape
            parent_shape = self._remove_inherited_distributed_shape(
                parent_shape,
                frozenset(parent_distributed_fields),
            )
            parent_shape.pop("type", None)
            parent_shape["anyOf"] = [
                _located(
                    self._merge_inherited_type_shape_dict(
                        {
                            "type": parent_type,
                            **self._select_inherited_distributed_shape(
                                branch_parent_shape,
                                frozenset(parent_distributed_fields),
                                frozenset((parent_type,)),
                            ),
                        },
                        self._select_inherited_distributed_shape(
                            child,
                            distributed_fields,
                            frozenset((parent_type,)),
                        )
                        | ({"nullable": False} if excludes_null and parent_type == "null" else {}),
                        effective_ref,
                        next_active,
                    ),
                    _dump_location(child),
                )
                for parent_type in sorted(parent_types)
            ]
            child_shape = self._remove_inherited_distributed_shape(
                child,
                distributed_fields,
            )
        elif distributed_fields:
            child_shape = self._remove_inherited_distributed_shape(
                child,
                distributed_fields,
            )
            child_shape.update(
                self._select_inherited_distributed_shape(
                    child,
                    distributed_fields,
                    parent_types,
                )
            )
        if (
            parent_shape.get("allOf")
            and len(parent_types) == 1
            and (parent_type := next(iter(parent_types))) not in {"array", "object"}
        ):
            parent_shape = {"type": parent_type}
        child_shape = self._drop_incompatible_inherited_constraints(
            child_shape,
            parent_types,
        )
        if (
            "type" not in parent_shape
            and not any(parent_shape.get(key) for key in ("allOf", "anyOf", "oneOf"))
            and len(parent_types) == 1
        ):
            parent_shape["type"] = next(iter(parent_types))
        result: dict[str, Any] = parent_shape.copy()
        for key, value in child_shape.items():
            if key in _INHERITED_NESTED_SCHEMA_FIELDS and key in parent_shape:
                result[key] = self._merge_inherited_type_shape_keyword(
                    key,
                    parent_shape[key],
                    value,
                    (parent_shape, effective_ref, next_active),
                )
            else:
                result[key] = value
        return _located(result, _dump_location(child))

    def _merge_no_merge_inherited_property(
        self,
        parent: JsonSchemaObject,
        child: JsonSchemaObject,
        parent_ref: str,
    ) -> JsonSchemaObject:
        """Materialize child constraints on the inherited type shape in no-merge mode."""
        effective_parent, effective_ref, active = self._resolve_inherited_parent_property(
            parent,
            parent_ref,
        )
        if not self._is_partial_inherited_property(
            child,
            effective_parent,
            (effective_ref, active),
        ):
            return child
        child_dict = child.model_dump(exclude_unset=True, by_alias=True)
        merged_dict = self._merge_inherited_type_shape_dict(
            parent.model_dump(exclude_unset=True, by_alias=True),
            child_dict,
            parent_ref,
        )
        if merged_dict == child_dict:
            return child
        merged_schema = self._schema_object(merged_dict)
        self._mark_inherited_materialized_type_shapes(merged_schema)
        return merged_schema

    def _get_deferred_inherited_property_names(
        self,
        source_obj: JsonSchemaObject,
        parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]],
    ) -> frozenset[str]:
        """Find partial properties that need canonical generated-type resolution."""
        if not source_obj.properties:
            return frozenset()
        deferred_names: set[str] = set()
        for field_name, child_property in source_obj.properties.items():
            if not (
                (inherited_property := parent_properties.get(field_name))
                and isinstance(parent_property := inherited_property[0], JsonSchemaObject)
            ):
                continue
            match child_property:
                case True:
                    deferred_names.add(field_name)
                case JsonSchemaObject() as child_schema:
                    normalized_child = self._normalize_inherited_constraint_compositions(child_schema)
                    effective_parent, effective_ref, active = self._resolve_inherited_parent_property(
                        parent_property,
                        inherited_property[1],
                    )
                    if not self._is_partial_inherited_property(
                        normalized_child,
                        effective_parent,
                        (effective_ref, active),
                    ):
                        continue
                    if self._has_inherited_constraints(normalized_child):
                        continue
                    deferred_names.add(field_name)
                case _:
                    continue
        return frozenset(deferred_names)

    def _mark_partial_inherited_fields(
        self,
        fields: list[DataModelFieldBase],
        deferred_property_names: frozenset[str],
        source_obj: JsonSchemaObject,
    ) -> None:
        """Mark partial properties so late forward resolution uses the canonical parent type."""
        if not deferred_property_names:
            return
        properties = source_obj.properties or {}
        for field in fields:
            field_name = _field_source_name(field)
            if field_name not in deferred_property_names:
                continue
            source_property = properties.get(field_name)
            facts = (
                self._null_facts(source_property, "inherited-child")
                if isinstance(source_property, JsonSchemaObject)
                else None
            )
            field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = _get_inherited_type_modifiers(
                field.data_type,
                null_rule=facts.rule if facts is not None and facts.admits_null is False else None,
                drops_null_members=self.intersects_allof_keywords
                and facts is not None
                and facts.rule == "inherited-typed-without-null",
            )

    def _get_deferred_inherited_parse_object(  # noqa: PLR6301
        self,
        source_obj: JsonSchemaObject,
        deferred_property_names: frozenset[str],
    ) -> JsonSchemaObject:
        """Strip nested type work that late canonical inheritance will replace."""
        if not (deferred_property_names and source_obj.properties):
            return source_obj
        properties = source_obj.properties.copy()
        changed = False
        for field_name in deferred_property_names:
            if not isinstance(property_schema := properties.get(field_name), JsonSchemaObject):
                continue
            updates = {
                schema_field: None
                for schema_field in _INHERITED_NESTED_SCHEMA_FIELDS
                if getattr(property_schema, schema_field) is not None
            }
            if property_schema.title is not None:
                updates["title"] = None
            if not updates:
                continue
            properties[field_name] = property_schema.model_copy(update=updates)
            changed = True
        return source_obj.model_copy(update={"properties": properties}) if changed else source_obj

    def _sanitize_untyped_boolean_inherited_property(
        self,
        child: JsonSchemaObject,
    ) -> JsonSchemaObject | None:
        """Drop type-specific constraints when a boolean parent supplies no usable type."""
        if self.allof_merge_mode != AllOfMergeMode.NoMerge or child.type is not None:
            return None
        if any((child.ref, child.allOf, child.anyOf, child.oneOf, child.enum, "const" in child.extras)):
            return None
        if not self._has_inherited_constraints(child):
            return None
        child_dict = child.model_dump(exclude_unset=True, by_alias=True)
        sanitized_dict = self._drop_incompatible_inherited_constraints(
            self._remove_inherited_distributed_shape(
                child_dict,
                self._get_inherited_distributed_fields(child),
            ),
            frozenset(),
        )
        return self._schema_object(sanitized_dict)

    def _merge_properties_with_parent_constraints(  # noqa: PLR0912
        self,
        child_obj: JsonSchemaObject,
        base_classes: list[Reference],
        parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]] | None = None,
        deferred_property_names: frozenset[str] | None = None,
    ) -> JsonSchemaObject:
        """Merge child properties with parent property constraints for allOf inheritance."""
        if not child_obj.properties:
            return child_obj

        if parent_properties is None:
            parent_properties = self._get_inherited_property_map(base_classes)
        if not parent_properties:
            return child_obj
        if deferred_property_names is None:
            deferred_property_names = self._get_deferred_inherited_property_names(
                child_obj,
                parent_properties,
            )

        merged_properties: dict[str, JsonSchemaObject | bool] = {}
        merged_changed = False
        for prop_name, child_prop in child_obj.properties.items():
            if not isinstance(child_prop, JsonSchemaObject):
                merged_properties[prop_name] = child_prop
                continue
            inherited_property = parent_properties.get(prop_name)
            if inherited_property is None:
                merged_properties[prop_name] = child_prop
                continue

            parent_prop, parent_ref = inherited_property
            effective_child = self._normalize_inherited_constraint_compositions(child_prop)
            if effective_child is not child_prop:
                merged_changed = True
            if not isinstance(parent_prop, JsonSchemaObject):
                if isinstance(parent_prop, bool) and (
                    sanitized_child := self._sanitize_untyped_boolean_inherited_property(effective_child)
                ):
                    merged_properties[prop_name] = sanitized_child
                    merged_changed = True
                else:
                    merged_properties[prop_name] = effective_child
                continue
            if prop_name in deferred_property_names:
                child_dict = effective_child.model_dump(exclude_unset=True, by_alias=True)
                normalized_dict = self._remove_unconstrained_compositions(child_dict)
                merged_properties[prop_name] = (
                    self._schema_object(normalized_dict) if normalized_dict != child_dict else effective_child
                )
                merged_changed = merged_changed or normalized_dict != child_dict
                continue
            if self.allof_merge_mode == AllOfMergeMode.NoMerge:
                merged_property = (
                    self._merge_no_merge_inherited_property(
                        parent_prop,
                        effective_child,
                        parent_ref,
                    )
                    if self._has_inherited_constraints(effective_child)
                    else effective_child
                )
                merged_properties[prop_name] = merged_property
                merged_changed = merged_changed or merged_property is not effective_child
                continue
            parent_dict = parent_prop.model_dump(exclude_unset=True, by_alias=True)
            child_dict = effective_child.model_dump(exclude_unset=True, by_alias=True)
            intersection = self._intersect_parts(
                [parent_dict, child_dict], self._merge_property_schemas(parent_dict, child_dict)
            )
            self._warn_intersection(str(child_prop.schema_location or f"property {prop_name!r}"), intersection)
            merged_property = self._schema_object(intersection.keywords)
            self._mark_reported_value_conflict(merged_property, intersection.unsatisfiable)
            merged_property.__dict__[_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY] = frozenset(
                self.get_field_extras(effective_child)
            )
            merged_property.__dict__[_INHERITED_OVERRIDE_KEY] = True
            merged_properties[prop_name] = merged_property
            merged_changed = True

        if not merged_changed:
            return child_obj
        return child_obj.model_copy(
            update={
                "properties": {
                    prop_name: merged.with_schema_location(child_prop.schema_location)
                    if isinstance(merged, JsonSchemaObject)
                    and isinstance(child_prop := child_obj.properties[prop_name], JsonSchemaObject)
                    else merged
                    for prop_name, merged in merged_properties.items()
                }
            }
        )

    @contextmanager
    def _inherited_ref_context(self, resolved_ref: str) -> Generator[None, None, None]:
        """Resolve nested references relative to the inherited schema's own file and schema resource.

        The inherited schema joins the dynamic scope of the schema inheriting it, so its $dynamicRef
        follow the $dynamicAnchor bindings of that scope.
        """
        file_part, _, _ = resolved_ref.partition("#")
        bindings = (
            self._current_dynamic_scope() if self._has_embedded_schema_resources or self._dynamic_anchors else None
        )
        if file_part and is_url(file_part):
            base_path = None
            root_path = [file_part]
        else:
            base_path = Path(file_part).parent if file_part else self.model_resolver.current_base_path
            root_path = file_part.split("/") if file_part else self.model_resolver.current_root
        with (
            self.model_resolver.current_base_path_context(base_path),
            self.model_resolver.base_url_context(file_part or self.model_resolver.base_url),
            self.model_resolver.current_root_context(root_path),
        ):
            if bindings is None:
                yield
                return
            with self._dynamic_scope_context([resolved_ref], bindings):
                yield

    def _inherited_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node of the inherited schema at a key (``_load_inherited_schema_object``).

        A schema that does not load is its key: the load that reads it raises.
        """
        try:
            schema = self._load_inherited_schema_object(resolved_ref)
        except (Error, OSError):
            return resolved_ref
        return _schema_node(schema, resolved_ref)

    def _iter_allof_refs(self, schema: JsonSchemaObject) -> Iterator[str]:
        """Yield refs nested only through inline allOf branches in document order."""
        for item in schema.allOf:
            match item:
                case JsonSchemaObject(ref=str() as ref) if ref:
                    yield ref
                case JsonSchemaObject() as inline_schema:
                    yield from self._iter_allof_refs(inline_schema)

    def _get_allof_parent_references(
        self,
        schema: JsonSchemaObject,
        *,
        inherited: Iterable[Reference] = (),
        defining_ref: str | None = None,
    ) -> list[Reference]:
        """Collect effective allOf parents once, preserving declaration precedence."""
        references = {reference.path: reference for reference in inherited}
        for ref in self._iter_allof_refs(schema):
            reference = (
                self._add_ref(ref)
                if defining_ref is None
                else self.model_resolver.add_ref(self._ref_path(ref), resolved=True)
            )
            references.setdefault(reference.path, reference)
        return list(references.values())

    def _get_inherited_schema_ancestor_paths(
        self,
        resolved_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> frozenset[str]:
        """Collect raw-schema ancestors for stable base ordering before generation."""
        if resolved_ref in self._inherited_schema_ancestor_cache:
            return self._inherited_schema_ancestor_cache[resolved_ref]
        if SPECIAL_PATH_MARKER in resolved_ref:
            return frozenset()
        schema = self._load_inherited_schema_object(resolved_ref)
        if (node := _schema_node(schema, resolved_ref)) in active:
            return frozenset()
        ancestors: set[str] = set()
        for ref in self._iter_allof_refs(schema):
            parent_ref = self._ref_path(ref)
            ancestors.add(parent_ref)
            ancestors.update(
                self._get_inherited_schema_ancestor_paths(
                    parent_ref,
                    active | {node},
                )
            )
        result = frozenset(ancestors)
        self._inherited_schema_ancestor_cache[resolved_ref] = result
        return result

    def _sort_inherited_base_classes_for_mro(self, base_classes: list[Reference]) -> list[Reference]:
        """Match the stable descendant-before-ancestor ordering used at render time."""
        if len(base_classes) <= 1:
            return base_classes.copy()
        resolved_paths = {id(base): base.path for base in base_classes if base.path}
        ancestor_paths = {
            resolved_path: self._get_inherited_schema_ancestor_paths(resolved_path)
            for resolved_path in resolved_paths.values()
        }
        return sorted(
            base_classes,
            key=lambda base: sum(resolved_paths.get(id(base)) in ancestors for ancestors in ancestor_paths.values()),
        )

    def _linearize_inherited_schema_refs(self, base_classes: list[Reference]) -> tuple[str, ...]:
        """Return raw inherited schemas in the same C3 order as generated models."""
        direct_refs = tuple(
            base.path
            for base in self._sort_inherited_base_classes_for_mro(base_classes)
            if base.path and SPECIAL_PATH_MARKER not in base.path
        )
        if direct_refs in self._inherited_schema_linearization_cache:
            return self._inherited_schema_linearization_cache[direct_refs]
        linearized_refs: dict[str, list[str]] = {}

        def linearize(ref: str, active: frozenset[_TargetNode] = frozenset()) -> list[str]:
            if cached_ref := linearized_refs.get(ref):
                return cached_ref
            schema = self._load_inherited_schema_object(ref)
            if (node := _schema_node(schema, ref)) in active:
                return [ref]
            parents = [self._ref_path(parent_ref) for parent_ref in self._iter_allof_refs(schema)]
            parents.sort(
                key=lambda parent: sum(parent in self._get_inherited_schema_ancestor_paths(other) for other in parents)
            )
            result = [
                ref,
                *c3_merge(
                    [
                        *[linearize(parent, active | {node}).copy() for parent in parents],
                        parents.copy(),
                    ],
                    key=lambda item: item,
                ),
            ]
            linearized_refs[ref] = result
            return result

        result = tuple(
            c3_merge(
                [
                    *[linearize(ref).copy() for ref in direct_refs],
                    list(direct_refs),
                ],
                key=lambda item: item,
            )
        )
        self._inherited_schema_linearization_cache[direct_refs] = result
        return result

    def _get_inherited_property_map(
        self,
        base_classes: list[Reference],
    ) -> dict[str, tuple[JsonSchemaObject | bool, str]]:
        """Build the effective raw property map once in inherited C3 order."""
        properties: dict[str, tuple[JsonSchemaObject | bool, str]] = {}

        def collect_inline_properties(schema: JsonSchemaObject, ref: str) -> None:
            if schema.properties:
                for name, prop_schema in schema.properties.items():
                    properties.setdefault(name, (prop_schema, ref))
            for item in schema.allOf:
                if isinstance(item, JsonSchemaObject) and not item.ref:
                    collect_inline_properties(item, ref)

        for ref in self._linearize_inherited_schema_refs(base_classes):
            collect_inline_properties(self._load_inherited_schema_object(ref), ref)
        return properties

    def _get_inherited_required_names(self, base_classes: list[Reference]) -> frozenset[str]:
        """Collect required names across the effective inherited allOf graph."""
        linearized_refs = self._linearize_inherited_schema_refs(base_classes)
        if linearized_refs in self._inherited_required_cache:
            return self._inherited_required_cache[linearized_refs]
        required: set[str] = set()

        def collect(schema: JsonSchemaObject) -> None:
            required.update(schema.required)
            for item in schema.allOf:
                if isinstance(item, JsonSchemaObject) and not item.ref:
                    collect(item)

        for ref in linearized_refs:
            collect(self._load_inherited_schema_object(ref))
        result = frozenset(required)
        self._inherited_required_cache[linearized_refs] = result
        return result

    def _get_inline_required_names(self, schema: JsonSchemaObject) -> tuple[str, ...]:
        """Collect required names in declaration order from one schema and inline allOf items."""
        required = dict.fromkeys(schema.required)
        for item in schema.allOf:
            if isinstance(item, JsonSchemaObject) and not item.ref:
                for required_name in self._get_inline_required_names(item):
                    required.setdefault(required_name, None)
        return tuple(required)

    def _get_inline_property_names(self, schema: JsonSchemaObject) -> frozenset[str]:
        """Collect properties declared directly by one schema and its inline allOf items."""
        property_names = set(schema.properties or ())
        for item in schema.allOf:
            if isinstance(item, JsonSchemaObject) and not item.ref:
                property_names.update(self._get_inline_property_names(item))
        return frozenset(property_names)

    def _get_inherited_field_owner_reference(
        self,
        reference: Reference,
        schema: JsonSchemaObject,
    ) -> Reference:
        """Apply the same title-based class scope as normal object parsing."""
        if schema.allOf or (owner_name := self._apply_title_as_name(reference.name, schema)) == reference.name:
            return reference

        source_path, marker, fragment = reference.path.partition("#")
        if not marker:  # pragma: no cover
            return reference
        path = [f"{source_path}#", *fragment.removeprefix("/").split("/")]
        return self.model_resolver.add(
            path,
            owner_name,
            class_name=True,
            loaded=reference.loaded,
        )

    def _get_inherited_property_order(self, base_classes: list[Reference]) -> tuple[str, ...]:
        """Return raw inherited property names in ancestor-first declaration order."""
        ordered_names: dict[str, None] = {}
        visited_refs: set[_TargetNode] = set()

        def collect_inline_properties(schema: JsonSchemaObject) -> None:
            for item in schema.allOf:
                if isinstance(item, JsonSchemaObject) and not item.ref:
                    collect_inline_properties(item)
            for field_name in schema.properties or {}:
                ordered_names.setdefault(field_name, None)

        def collect_ref(resolved_ref: str) -> None:
            if SPECIAL_PATH_MARKER in resolved_ref:
                return
            schema = self._load_inherited_schema_object(resolved_ref)
            if (node := _schema_node(schema, resolved_ref)) in visited_refs:
                return
            visited_refs.add(node)
            parent_refs = self._get_allof_parent_references(
                schema,
                defining_ref=resolved_ref,
            )
            for parent in self._sort_inherited_base_classes_for_mro(parent_refs):
                collect_ref(parent.path)
            collect_inline_properties(schema)

        for base in self._sort_inherited_base_classes_for_mro(base_classes):
            collect_ref(base.path)
        return tuple(ordered_names)

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
        inherited_properties = self._get_inherited_property_map(parent_refs)
        reference = self._get_inherited_field_owner_reference(reference, schema)
        class_name = reference.name
        module_name = get_inferred_module_name(
            class_name,
            treat_dot_as_module=self.treat_dot_as_module,
            strict_dotted_module_names=self.strict_dotted_module_names,
            target_python_version=self.target_python_version,
        )
        fields: list[DataModelFieldBase] = []
        owner_path = [reference.path] if self.dedupe_request_response_variants else []

        def parse_inline(source: JsonSchemaObject) -> None:
            if source.properties:
                deferred_names = self._get_deferred_inherited_property_names(
                    source,
                    inherited_properties,
                )
                source = self._merge_properties_with_parent_constraints(
                    source,
                    parent_refs,
                    inherited_properties,
                    deferred_names,
                )
                parsed_fields = self.parse_object_fields(
                    self._get_deferred_inherited_parse_object(source, deferred_names),
                    owner_path,
                    module_name,
                    class_name=class_name,
                )
                self._mark_partial_inherited_fields(parsed_fields, deferred_names, source)
                self._unregister_temporary_field_references(parsed_fields)
                fields.extend(parsed_fields)
            for item in source.allOf:
                if isinstance(item, JsonSchemaObject) and not item.ref:
                    parse_inline(item)

        with self._inherited_ref_context(reference.path):
            for item in schema.allOf:
                if isinstance(item, JsonSchemaObject) and not item.ref:
                    parse_inline(item)
            if schema.properties:
                deferred_names = self._get_deferred_inherited_property_names(
                    schema,
                    inherited_properties,
                )
                schema = self._merge_properties_with_parent_constraints(
                    schema,
                    parent_refs,
                    inherited_properties,
                    deferred_names,
                )
                parsed_fields = self.parse_object_fields(
                    self._get_deferred_inherited_parse_object(schema, deferred_names),
                    owner_path,
                    module_name,
                    class_name=class_name,
                )
                self._mark_partial_inherited_fields(parsed_fields, deferred_names, schema)
                self._unregister_temporary_field_references(parsed_fields)
                fields.extend(parsed_fields)
        if not self.force_optional_for_required_fields:
            existing_names = {field_name for field in fields if (field_name := _field_source_name(field)) is not None}
            inherited_fields = {
                field_name: field for field in parent_fields if (field_name := _field_source_name(field)) is not None
            }
            for required_name in self._get_inline_required_names(schema):
                if required_name in existing_names:
                    continue
                field = self._build_missing_required_field(
                    required_name,
                    excludes={field.name for field in fields if field.name},
                    base_classes=parent_refs,
                    class_name=class_name,
                    inherited_fields=inherited_fields,
                )
                fields.append(field)
                existing_names.add(required_name)
        return fields

    def _iter_inherited_schema_objects(
        self, base_classes: list[Reference], visited: frozenset[_TargetNode]
    ) -> Iterator[tuple[JsonSchemaObject, frozenset[_TargetNode], str]]:
        """Yield inherited schema objects not visited yet, with the visited schema nodes they add to."""
        for base in self._sort_inherited_base_classes_for_mro(base_classes):
            if not base.path:  # pragma: no cover
                continue
            resolved_ref = base.path
            try:
                parent_schema = self._load_inherited_schema_object(resolved_ref)
            except Exception:  # pragma: no cover  # noqa: BLE001, S112
                continue
            if (node := _schema_node(parent_schema, resolved_ref)) in visited:  # pragma: no cover
                continue
            yield parent_schema, visited | {node}, resolved_ref

    def _load_inherited_schema_object(self, resolved_ref: str) -> JsonSchemaObject:
        """Load and cache a schema used only for inherited field inspection."""
        if cached := self._inherited_schema_cache.get(resolved_ref):
            return cached
        schema = self._shared_schema_object(self._get_ref_raw_schema(resolved_ref), [resolved_ref])
        self._inherited_schema_cache[resolved_ref] = schema
        return schema

    def _get_inherited_field_type(
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[_TargetNode] | None = None
    ) -> DataType | None:
        """Get the data type for an inherited property from parent schemas.

        Recursively traverses the inheritance chain when a parent property
        doesn't have type information but the parent itself inherits from another schema.
        """
        if inherited_field := self._get_inherited_field(prop_name, base_classes):
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
                    for ref in self._iter_allof_refs(parent_schema)
                ]
                if grandparent_refs:
                    parent_result = self._get_inherited_field_type(prop_name, grandparent_refs, next_visited)
                    if parent_result is not None:
                        return parent_result
                    return result

        return None

    def _get_generated_base_models(self, base_classes: list[Reference]) -> list[DataModel]:
        """Resolve generated direct bases without traversing their fields."""
        data_models: list[DataModel] = []
        for base in base_classes:
            data_model = base.source if isinstance(base.source, DataModel) else None
            if data_model is None:
                data_model = next((result for result in self.results if result.reference.path == base.path), None)
            if data_model is not None:
                data_models.append(data_model)
        return data_models

    def _get_inherited_field_map(self, base_classes: list[Reference]) -> dict[str, DataModelFieldBase]:
        """Build the effective generated field map once for a set of direct bases."""
        inherited_fields = get_inherited_fields(self._get_generated_base_models(base_classes))
        deferred_names = {
            name
            for name, field in inherited_fields.items()
            if _DEFERRED_INHERITED_TYPE_KEY in field.__dict__ or _DEFERRED_INHERITED_FIELD_KEY in field.__dict__
        }
        if not deferred_names:
            return inherited_fields

        for field in self._collect_inherited_fields_for_request_response(base_classes):
            if (field_name := _field_source_name(field)) in deferred_names:
                inherited_fields[field_name] = field
        return inherited_fields

    def _get_inherited_field(
        self,
        prop_name: str,
        base_classes: list[Reference],
        inherited_fields: dict[str, DataModelFieldBase] | None = None,
    ) -> DataModelFieldBase | None:
        """Get an inherited generated field from parsed base models."""
        return (inherited_fields if inherited_fields is not None else self._get_inherited_field_map(base_classes)).get(
            prop_name
        )

    def _get_inherited_field_schema(
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[_TargetNode] | None = None
    ) -> JsonSchemaObject | None:
        """Get the schema for an inherited property from parent schemas."""
        if visited is None:
            visited = frozenset()

        def find_property(schema: JsonSchemaObject, current_visited: frozenset[_TargetNode]) -> JsonSchemaObject | None:
            if schema.properties and isinstance(
                property_schema := schema.properties.get(prop_name),
                JsonSchemaObject,
            ):
                return property_schema

            for item in schema.allOf:
                if (
                    isinstance(item, JsonSchemaObject)
                    and not item.ref
                    and (inline_property := find_property(item, current_visited))
                ):
                    return inline_property

            for item in schema.allOf:
                if not isinstance(item, JsonSchemaObject) or not item.ref:
                    continue
                resolved_ref = self._ref_path(item.ref)
                parent_schema = self._load_inherited_schema_object(resolved_ref)
                if (node := _schema_node(parent_schema, resolved_ref)) in current_visited:
                    continue
                if property_schema := find_property(parent_schema, current_visited | {node}):
                    return property_schema
            return None

        for parent_schema, next_visited, _ in self._iter_inherited_schema_objects(base_classes, visited):
            if property_schema := find_property(parent_schema, next_visited):
                return property_schema

        return None

    def _build_missing_required_field(
        self,
        required_field_name: str,
        excludes: set[str],
        base_classes: list[Reference],
        class_name: str,
        inherited_fields: dict[str, DataModelFieldBase] | None = None,
    ) -> DataModelFieldBase:
        """Build a field for a required name that is not declared in properties."""
        field_name, alias = self.model_resolver.get_valid_field_name_and_alias(
            required_field_name,
            excludes=excludes,
            model_type=self.field_name_model_type,
            class_name=class_name,
        )
        inherited_field = self._get_inherited_field(required_field_name, base_classes, inherited_fields)
        if inherited_field is not None and inherited_field.name and inherited_field.name not in excludes:
            field_name = inherited_field.name
        single_alias, validation_aliases = self._split_field_alias(alias)
        serialization_alias = self.get_serialization_alias(required_field_name, field_name, class_name)

        if inherited_field is not None:
            copied_field = self._copy_model_field(inherited_field)
            copied_field.name = field_name
            copied_field.required = True
            copied_field.original_name = required_field_name
            copied_field.alias = single_alias
            copied_field.validation_aliases = validation_aliases
            copied_field.serialization_alias = serialization_alias
            copied_field.use_serialization_alias = self.use_serialization_alias
            self._prepare_required_inherited_field(copied_field, inherited_field)
            self._apply_inherited_field_default(copied_field, inherited_field, class_name=class_name)
            return copied_field

        inherited_schema = self._get_inherited_field_schema(required_field_name, base_classes)
        if inherited_schema is not None:
            field = self.data_model_field_type(
                name=field_name,
                required=True,
                original_name=required_field_name,
                alias=single_alias,
                validation_aliases=validation_aliases,
                serialization_alias=serialization_alias,
                data_type=self.data_type(),
                use_serialization_alias=self.use_serialization_alias,
                **self._data_model_field_common_kwargs(),
            )
            field.__dict__[_DEFERRED_INHERITED_FIELD_KEY] = class_name
            return field

        return self.data_model_field_type(
            name=field_name,
            required=True,
            original_name=required_field_name,
            alias=single_alias,
            validation_aliases=validation_aliases,
            serialization_alias=serialization_alias,
            data_type=self._get_inherited_field_type(required_field_name, base_classes)
            or DataType(type=ANY, import_=IMPORT_ANY),
            use_serialization_alias=self.use_serialization_alias,
            **self._data_model_field_common_kwargs(),
        )

    def _schema_signature(self, prop_schema: JsonSchemaObject | bool) -> str | bool:  # noqa: FBT001, PLR6301
        """Normalize property schema for comparison across allOf items."""
        if isinstance(prop_schema, bool):
            return prop_schema
        return json.dumps(prop_schema.model_dump(exclude_unset=True, by_alias=True), sort_keys=True, default=repr)

    def _is_root_model_schema(self, obj: JsonSchemaObject) -> bool:  # noqa: PLR0911
        """Check if schema represents a root model (primitive type with constraints).

        Based on parse_raw_obj() else branch conditions. Returns True when
        the schema would be processed by parse_root_type().
        """
        if obj.is_array:
            return False
        if obj.allOf or obj.oneOf or obj.anyOf:
            return False
        if obj.properties:
            return False
        if obj.patternProperties:
            return False
        if obj.propertyNames is not None:
            return False
        if obj.type == "object":
            return False
        return not obj.enum or self.ignore_enum_constraints

    def _handle_allof_root_model_with_constraints(  # noqa: PLR0911, PLR0912, PLR0915
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataType | None:
        """Handle allOf that combines a root model $ref with additional constraints.

        This handler is for generating a root model from a root model reference.
        Object inheritance (with properties) is handled by existing _parse_all_of_item() path.
        Only applies to named schema definitions, not inline properties.
        """
        for path_element in path:
            if SPECIAL_PATH_MARKER in path_element:
                return None  # pragma: no cover

        ref_items = [item for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref]

        if len(ref_items) != 1:
            return None

        ref_item = ref_items[0]
        ref_value = ref_item.ref
        if ref_value is None:
            return None  # pragma: no cover

        if ref_item.has_ref_with_schema_keywords and not ref_item.is_ref_with_nullable_only:
            ref_schema = self._merge_ref_with_schema(ref_item)
        else:
            ref_schema = self._load_ref_schema_object(ref_value)

        if not self._is_root_model_schema(ref_schema):
            return None

        constraint_items: list[JsonSchemaObject] = []
        for item in obj.allOf:
            if not isinstance(item, JsonSchemaObject):  # pragma: no cover
                continue
            if item.ref:
                continue
            if item.properties or item.items:
                return None
            if item.has_constraint or item.type or item.format:
                constraint_items.append(item)

        if not constraint_items:
            return None

        common_types: list[str] | None = None
        if ref_schema.type and (typed := [item.type for item in constraint_items if item.type]):
            ref_types = _json_schema_types(ref_schema.type)
            if any(_numeric_type_domain(type_) is None for type_ in (ref_schema.type, *typed)):
                common_types = reduce(_intersect_json_schema_types, map(_json_schema_types, typed), ref_types)
                if not common_types:
                    return None
                if common_types == ref_types:
                    common_types = None

        all_items = [ref_schema, *constraint_items]
        merged_schema = self._merge_primitive_schemas_for_allof(all_items)
        if merged_schema is None:
            return None
        if common_types:
            merged_schema = self._schema_object({
                **merged_schema.model_dump(exclude_unset=True, by_alias=True),
                "type": common_types[0] if len(common_types) == 1 else common_types,
            })

        has_literals = (
            self.generate_schema_validators
            and hasattr(self.data_model_root_type, "add_literal_validation")
            and ("enum" in obj.model_fields_set or "const" in obj.extras)
        )
        if obj.has_constraint or obj.description or obj.format or has_literals or self.field_constraints:
            merged_dict = merged_schema.model_dump(exclude_unset=True, by_alias=True)
            if obj.has_constraint:
                self._merge_schema_constraints(merged_dict, [obj], intersect=True)
            if obj.format and not merged_schema.format:
                merged_dict["format"] = obj.format
            if has_literals:
                self._merge_all_of_root_validation_keywords(merged_dict, [*all_items, obj])
            if obj.description:
                merged_dict["description"] = obj.description
            if isinstance(schema_type := merged_schema.type, list) and not merged_schema.has_multiple_types:
                schema_type = next((item for item in schema_type if item != "null"), None)
            if isinstance(schema_type, str):
                constraint_types = frozenset({schema_type})
                if (
                    (format_ := merged_dict.get("format"))
                    and (
                        format_ in self._data_formats.get(schema_type, {})
                        or (schema_type, format_) in self.type_mappings
                    )
                    and (
                        kinds := all_of_root_keyword_kinds(
                            schema_type, self._get_type_with_mappings(schema_type, format_).name
                        )
                    )
                    is not None
                ):
                    constraint_types = kinds
                merged_dict = self._drop_incompatible_inherited_constraints(merged_dict, constraint_types)
            merged_schema = self._schema_object(merged_dict)

        merged_schema = merged_schema.with_schema_location(obj.schema_location)
        if has_literals or (obj.format and merged_schema.has_constraint):
            with self._temporarily_enable_field_constraints():
                if has_literals:
                    return self._parse_all_of_root_value(name, merged_schema, path)
                return self.parse_root_type(name, merged_schema, path)
        return self.parse_root_type(name, merged_schema, path)

    def _merge_all_of_schema(
        self,
        parent: dict[str, Any],
        child: dict[str, Any],
        *,
        schema_map: bool = False,
        intersect_bounds: bool = True,
    ) -> dict[str, Any]:
        """Intersect allOf bounds and enums only in positive schema positions."""
        result = parent.copy()
        conjoins_patterns = self.intersects_allof_keywords and intersect_bounds and not schema_map
        for key, value in child.items():
            if key not in result:
                result[key] = value
                continue
            previous = result[key]
            if isinstance(previous, dict) and isinstance(value, dict):
                if schema_map or key in _INHERITED_NESTED_SCHEMA_FIELDS:
                    result[key] = _located(
                        self._merge_all_of_schema(
                            previous,
                            value,
                            schema_map=not schema_map and key in _INHERITED_SCHEMA_MAP_FIELDS,
                            intersect_bounds=intersect_bounds,
                        ),
                        _dump_location(value),
                    )
                else:
                    result[key] = self._deep_merge(previous, value)
            elif (
                intersect_bounds
                and not schema_map
                and key in _ALLOF_BOUND_CONSTRAINT_FIELDS
                and previous is not None
                and value is not None
            ):
                # Preserve the legacy child representation when two bounds are equal.
                result[key] = JsonSchemaParser._intersect_constraint(key, value, previous)
            elif isinstance(previous, list) and isinstance(value, list):
                if not schema_map and key == "enum":
                    result[key] = _intersect_all_of_enum(previous, value)
                else:
                    result[key] = previous + value
            elif (
                conjoins_patterns
                and key == "pattern"
                and isinstance(previous, str)
                and isinstance(value, str)
                and previous != value
            ):
                result[key] = self._conjoined_pattern((previous, value), "allOf")
            else:
                result[key] = value
        if (
            not schema_map
            and isinstance(parent_enum := parent.get("enum"), list)
            and isinstance(child_enum := child.get("enum"), list)
            and len(result["enum"]) < len(parent_enum) + len(child_enum)
        ):
            _align_all_of_enum_metadata(parent, child, result)
        return result

    def _merge_all_of_object(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Merge allOf items when they share object properties to avoid duplicate models.

        Skip merging when there is exactly one $ref (inheritance with property overrides).
        Continue merging when multiple $refs have conflicting property definitions to avoid MRO issues.
        Child property overrides (obj.properties) are not considered conflicts.
        """
        if self.allof_class_hierarchy == AllOfClassHierarchy.Always:
            # Skip merging when always inherit from the base classes
            return None

        ref_count = sum(1 for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref)
        if ref_count == 1:
            return None

        resolved_items: list[JsonSchemaObject] = []
        property_signatures: dict[str, set[str | bool]] = {}
        for item in obj.allOf:
            if not isinstance(item, JsonSchemaObject):  # pragma: no cover
                continue
            resolved_item = self._load_ref_schema_object(item.ref) if item.ref else item
            if resolved_item.is_boolean_schema_false:
                self._raise_unsatisfiable_schema(obj, [], "allOf")
            resolved_items.append(resolved_item)
            if resolved_item.properties:
                for prop_name, prop_schema in resolved_item.properties.items():
                    property_signatures.setdefault(prop_name, set()).add(self._schema_signature(prop_schema))

        if not any(len(signatures) > 1 for signatures in property_signatures.values()):
            return None

        merged_schema: dict[str, Any] = obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True)
        merge_schema = (
            self._deep_merge if self.allof_merge_mode == AllOfMergeMode.NoMerge else self._merge_all_of_schema
        )
        for resolved_item in resolved_items:
            merged_schema = merge_schema(merged_schema, resolved_item.model_dump(exclude_unset=True, by_alias=True))
        if any(
            source.additionalProperties is False and not source.patternProperties
            for resolved_item in (obj, *resolved_items)
            for source in self._iter_schema_validation_sources(resolved_item, include_references=False)
        ):
            merged_schema["additionalProperties"] = False

        if "required" in merged_schema and isinstance(merged_schema["required"], list):
            merged_schema["required"] = list(dict.fromkeys(merged_schema["required"]))

        merged_schema.pop("allOf", None)
        return self._schema_object(merged_schema)

    def _merge_all_of_root_validation_keywords(self, merged: dict[str, Any], sources: list[JsonSchemaObject]) -> None:
        """Preserve literal and item intersections while materializing a value reference.

        ``merged`` stands for the last source: the allOf holder, or the node whose keywords win. Literals that no value
        meets in every source keep those of the first source that has some, with a warning.
        """
        literal_sets = [values for source in sources if (values := self._schema_literal_values(source)) is not None]
        if literal_sets:
            literal_values = [
                value
                for value in literal_sets[0]
                if all(any(_json_literal_values_equal(value, other) for other in values) for values in literal_sets[1:])
            ]
            if not literal_values:
                self._warn_unsatisfiable(
                    str(sources[-1].schema_location or "allOf"),
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
            if False in item_schemas:
                merged["items"] = self._merge_all_of_root_value_nodes(
                    cast("list[JsonSchemaObject | bool]", item_schemas)
                )
                return
            positional_lengths = [len(child) for child in item_schemas if isinstance(child, list)]
            if positional_lengths:
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
            merged["items"] = self._merge_all_of_root_value_nodes(cast("list[JsonSchemaObject | bool]", item_schemas))

    def _merge_all_of_root_value_nodes(self, nodes: list[JsonSchemaObject | bool]) -> dict[str, Any] | bool:
        """Intersect schema nodes without dropping constraints on nested properties."""
        first = nodes[0]
        if len(nodes) == 1:
            return first.model_dump(exclude_unset=True, by_alias=True) if isinstance(first, JsonSchemaObject) else first
        if False in nodes:
            return False
        references = tuple(
            self._key_node(self._ref_path(node.ref))
            for node in nodes
            if isinstance(node, JsonSchemaObject) and node.ref
        )
        if not references:
            return self._merge_all_of_root_value_children(nodes)
        if self._all_of_root_value_ref_stack is None:
            self._all_of_root_value_ref_stack = set()
        if references in self._all_of_root_value_ref_stack:
            return {
                "allOf": [
                    node.model_dump(exclude_unset=True, by_alias=True) if isinstance(node, JsonSchemaObject) else node
                    for node in nodes
                ]
            }
        self._all_of_root_value_ref_stack.add(references)
        try:
            return self._merge_all_of_root_value_children(nodes)
        finally:
            self._all_of_root_value_ref_stack.remove(references)

    def _merge_all_of_root_value_children(  # noqa: PLR0912
        self, nodes: list[JsonSchemaObject | bool]
    ) -> dict[str, Any] | bool:
        """Merge acyclic nodes while leaving recursive references for normal reference resolution.

        The merged node stands for the last node, whose keywords win.
        """
        children: list[JsonSchemaObject] = []
        for child in nodes:
            if not isinstance(child, JsonSchemaObject):
                continue
            child_schema = child
            if child.ref:
                resolved = self._load_ref_schema_object(child.ref).model_dump(exclude_unset=True, by_alias=True)
                children.append(self._schema_object(resolved))
                child_schema = child.model_copy(update={"ref": None})
            children.append(child_schema)
        merged: dict[str, Any] = {}
        for child in children:
            merged.update(child.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True))
        allowed_types: set[str] | None = None
        for child in children:
            if not child.type:
                continue
            child_types = set(child.type) if isinstance(child.type, list) else {child.type}
            if "number" in child_types:
                child_types.add("integer")
            allowed_types = child_types if allowed_types is None else allowed_types & child_types
        if allowed_types is not None:
            if not allowed_types:
                return False
            if "number" in allowed_types:
                allowed_types.discard("integer")
            merged["type"] = next(iter(allowed_types)) if len(allowed_types) == 1 else sorted(allowed_types)
        self._merge_schema_constraints(merged, children, intersect=self.allof_merge_mode != AllOfMergeMode.NoMerge)
        self._merge_all_of_root_validation_keywords(merged, children)
        for keyword in ("properties", "patternProperties"):
            properties: dict[str, list[JsonSchemaObject | bool]] = {}
            for source in children:
                for name, node in (getattr(source, keyword) or {}).items():
                    properties.setdefault(name, []).append(node)
            if properties:
                if keyword == "properties":
                    forbidden = set()
                    for source in children:
                        if source.additionalProperties is False and not source.patternProperties:
                            forbidden.update(name for name in properties if name not in (source.properties or {}))
                    if forbidden:
                        if any(name in source.required for name in forbidden for source in children):
                            return False
                        for name in forbidden:
                            properties.pop(name)
                merged[keyword] = {
                    name: self._merge_all_of_root_value_nodes(nodes) for name, nodes in properties.items()
                }
        if required := list(dict.fromkeys(name for source in children for name in source.required)):
            merged["required"] = required
        return _located(merged, children[-1].schema_location if children else None)

    def _is_all_of_mapping_item(self, schema: JsonSchemaObject) -> bool:
        """Return whether an allOf item is an object map, which a mapping-shaped allOf merges into one root map."""
        return not (
            schema.properties
            or schema.patternProperties
            or schema.propertyNames is not None
            or not _is_object_only_type(_non_null_types(schema.type) if self.intersects_allof_keywords else schema.type)
            or not isinstance(schema.additionalProperties, JsonSchemaObject)
        )

    def _merge_all_of_root_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:  # noqa: PLR0912
        """Unwrap one value schema or merge mapping-shaped allOf items into a root schema."""
        if obj.properties or obj.patternProperties or obj.propertyNames is not None:
            return None

        supports_literal_validation = hasattr(self.data_model_root_type, "add_literal_validation")
        single_value = len(obj.allOf) == 1 and not self._schema_requires_model_type(obj)
        mapping_schemas: list[JsonSchemaObject] = []
        sibling_types: list[str | list[str]] = []
        for item in obj.allOf:
            match item:
                case JsonSchemaObject() as schema:
                    if ref := schema.ref:
                        schema = self._load_ref_schema_object(ref)
                        if item.type and self._ref_sibling_keywords_enabled:
                            sibling_types.append(item.type)
                case _:
                    return None
            narrows_literals = bool(
                ref
                and single_value
                and not supports_literal_validation
                and schema.enum
                and self._ref_member_narrows_literals(item, schema.enum, obj)
            )
            materialize_siblings = bool(
                ref
                and len(item.model_fields_set) > 1
                and self._ref_sibling_keywords_enabled
                and (
                    item.has_ref_with_schema_keywords
                    if supports_literal_validation
                    else item.has_constraint or narrows_literals
                )
            )
            scalar_or_array = (
                schema.type
                and schema.type != "object"
                and not (
                    schema.enum and not (materialize_siblings and (supports_literal_validation or narrows_literals))
                )
            )
            if (
                single_value
                and scalar_or_array
                and not (schema.allOf or schema.anyOf or schema.oneOf)
                and not self._schema_requires_model_type(schema)
            ):
                merged_root: JsonSchemaObject | None = None
                if materialize_siblings:
                    merged = schema.model_dump(exclude_unset=True, by_alias=True)
                    referenced_schema = self._schema_object(merged)
                    merged.update(item.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True))
                    merged.update(obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True))
                    self._merge_schema_constraints(
                        merged, [schema, item, obj], intersect=self.allof_merge_mode != AllOfMergeMode.NoMerge
                    )
                    if supports_literal_validation or narrows_literals:
                        self._merge_all_of_root_validation_keywords(merged, [referenced_schema, item, obj])
                    merged_root = self._schema_object(merged)
                if not merged_root and ref:
                    merged_root = self._reference_site_schema(obj, layered=self.field_constraints)
                return merged_root or (
                    obj.model_copy(update={"ref": ref, "allOf": []})
                    if ref
                    else self._schema_object({
                        **schema.model_dump(exclude_unset=True, by_alias=True),
                        **obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True),
                    })
                )
            if not self._is_all_of_mapping_item(schema):
                return None
            mapping_schemas.append(schema)

        if not mapping_schemas:
            return None

        merged: dict[str, Any] = {}
        for schema in mapping_schemas:
            merged = self._merge_property_schemas(
                merged,
                schema.model_dump(exclude_unset=True, by_alias=True),
            )
        merged = self._merge_property_schemas(
            merged,
            obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True),
        )
        typed = [*sibling_types, *(schema.type for schema in (*mapping_schemas, obj) if schema.type)]
        if self.intersects_allof_keywords and any(_lists_null(type_) for type_ in typed):
            merged["type"] = (
                ["object", "null"]
                if self._null_facts(obj, "all-of-map", parts_null=all(map(_lists_null, typed))).admits_null
                else "object"
            )
        elif merged.get("type") == ["object"]:
            merged["type"] = "object"
        else:
            merged.setdefault("type", "object")
        return self._schema_object(merged)

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
            merged = self._deep_merge(
                merged,
                schema.model_dump(
                    exclude={"allOf", *JsonSchemaObject.__constraint_fields__}, exclude_unset=True, by_alias=True
                ),
            )
        if types:
            merged["type"] = JsonSchemaParser._first_typed_schema_dict(schemas)["type"]
        self._merge_schema_constraints(merged, schemas, intersect=self.allof_merge_mode != AllOfMergeMode.NoMerge)
        return self._schema_object(merged)

    def _merge_all_of_value_schema(
        self, obj: JsonSchemaObject, path: list[str], base_classes: list[Reference]
    ) -> JsonSchemaObject | None:
        """Rewrite a named allOf whose members describe one scalar value as the schema it is without allOf."""
        if (
            any(SPECIAL_PATH_MARKER in element for element in path)
            or any(
                not (target := self._load_inherited_schema_object(base.path)).ref
                and self._schema_requires_model_type(target)
                for base in base_classes
            )
            or (base_classes and self._get_inherited_property_map(base_classes))
        ):
            return None
        own_schemas: list[JsonSchemaObject] = []
        schemas: list[JsonSchemaObject] = []
        pending = [(obj, False)]
        while pending:
            schema, referenced = pending.pop()
            if schema.ref:
                if not referenced:
                    own_schemas.append(schema)
                schema, referenced = self._merge_ref_with_schema(schema), True
            if schema.allOf:
                pending.append((schema.model_copy(update={"allOf": []}), referenced))
                pending.extend(
                    (
                        item if isinstance(item, JsonSchemaObject) else self._validate_schema_object(item, path),
                        referenced,
                    )
                    for item in reversed(schema.allOf)
                )
                continue
            if (
                self._schema_requires_model_type(schema)
                or any((schema.is_boolean_schema_false, schema.is_array, schema.anyOf, schema.oneOf))
                or (referenced and self._schema_literal_values(schema) is not None)
            ):
                return None
            schemas.append(schema)
            if not referenced:
                own_schemas.append(schema)
        if (
            sum("pattern" in schema.model_fields_set for schema in schemas) > 1
            or (not any(schema.type for schema in schemas) and any(schema.has_constraint for schema in schemas))
            or (merged := self._merge_value_schemas(schemas)) is None
        ):
            return None
        if (
            sum(bool(schema.ref) for schema in own_schemas) == 1
            and (reference := self._merge_value_schemas(own_schemas))
            and (not reference.has_ref_with_schema_keywords or reference.is_ref_with_nullable_only)
        ):
            return reference
        return merged

    def _all_of_value_parts(self, obj: JsonSchemaObject) -> list[JsonSchemaObject] | None:
        """Return the parts of an allOf site whose members each describe one value, references resolved, else None."""
        parts: list[JsonSchemaObject] = []
        if (
            self._schema_requires_model_type(obj)
            or self._allof_requires_model_type(obj.allOf, resolve_ref=True)
            or not self._collect_all_of_value_parts(obj, parts, resolve_refs=True)
        ):
            return None
        return parts

    def _satisfiable_all_of_value_parts(self, obj: JsonSchemaObject) -> list[JsonSchemaObject] | None:
        """Return the parts of an allOf site whose members each describe one value, references resolved, else None.

        A site whose parts no value meets in common gets None, and a warning with --intersect-allof-keywords: it keeps
        the form it is written in, and a form that cannot be generated takes its first part.
        """
        if (parts := self._all_of_value_parts(obj)) is None or len(parts) <= 1:
            return parts
        dumps = [part.model_dump(exclude_unset=True, by_alias=True) for part in parts]
        row: dict[str, Any] = {}
        for dump in dumps:
            row.update(dump)
        unsatisfiable = intersect_keywords(dumps, row, extras_key=JsonSchemaObject.__extra_key__).unsatisfiable
        if self.intersects_allof_keywords:
            self._warn_unsatisfiable(str(obj.schema_location or "allOf"), unsatisfiable, _ALL_OF_PARTS)
        return None if unsatisfiable else parts

    def _intersects_value_parts(self, parts: Sequence[JsonSchemaObject], *, literals: bool = True) -> bool:
        """Return whether the intersection of value parts is taken here, which needs a value keyword in some part.

        It is not for two parts with ``items`` (their conjunction is not intersected here), two different ``format``,
        and, unless ``literals``, a part with an ``enum`` or ``const``.
        """
        return (
            any(self._schema_has_own_value_keywords(part) for part in parts)
            and (literals or not any(part.enum or "const" in part.extras for part in parts))
            and sum(part.items is not None for part in parts) <= 1
            and len({part.format for part in parts if part.format}) <= 1
        )

    def _all_of_site_schema(
        self, obj: JsonSchemaObject, parts: Sequence[JsonSchemaObject], value: dict[str, Any]
    ) -> JsonSchemaObject:
        """Return the schema of an allOf site with the ``value`` of its parts; its own annotations win, the parts' fill.

        The site admits null as its null facts say (``all-of-value``).
        """
        annotations: dict[str, Any] = {}
        for part in reversed(parts):
            annotations.update(part.model_dump(include=_ALL_OF_MEMBER_ANNOTATIONS, exclude_unset=True, by_alias=True))
        own = obj.model_dump(exclude={"allOf", *_ALL_OF_VALUE_KEYWORDS}, exclude_unset=True, by_alias=True)
        if value.pop("nullable", False) or self._null_facts(obj, "all-of-value").admits_null:
            if (type_ := value.get("type")) is None:
                value["nullable"] = True
            else:
                value["type"] = [
                    *(item for item in (type_ if isinstance(type_, list) else [type_]) if item != "null"),
                    "null",
                ]
        return self._schema_object({**value, **annotations, **own})

    def _all_of_root_value_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the schema of an allOf site of values that would get a model without fields: its parts intersected.

        A single ``$ref`` member stays the type when the intersection enforces nothing beyond the referenced schema.
        The site's own annotations win, the members' fill in (``_all_of_site_schema``). None when no part has a value
        keyword, and where the site keeps its earlier form: a part with an ``enum`` or ``const`` (an allOf of enum
        references is decided by ``_all_of_enum_site_schema``) and the parts ``_intersects_value_parts`` leaves out.
        None without --intersect-allof-keywords.
        """
        if (
            not self.intersects_allof_keywords
            or (parts := self._satisfiable_all_of_value_parts(obj)) is None
            or not self._intersects_value_parts(parts, literals=False)
        ):
            return None
        intersection = self._intersect_typed_value_parts(parts, None)
        value: dict[str, Any] = intersection.model_dump(exclude_unset=True, by_alias=True)
        if (
            len(refs := [item.ref for item in obj.allOf if isinstance(item, JsonSchemaObject) and item.ref]) == 1
            and (target_parts := self._reference_value_parts(refs[0])) is not None
            and self._enforced_value_keywords(intersection)
            == self._enforced_value_keywords(self._intersect_typed_value_parts(target_parts, None))
        ):
            value = {"$ref": refs[0]}
        return self._all_of_site_schema(obj, parts, value)

    def _reference_site_schema(self, obj: JsonSchemaObject, *, layered: bool = False) -> JsonSchemaObject | None:
        """Return the schema of an allOf site whose earlier form, the type of its first ``$ref`` member, it leaves.

        ``_reference_site_value`` decides; the site's own annotations win, the parts' fill (``_all_of_site_schema``).
        """
        if (site := self._reference_site_value(obj, layered=layered)) is None:
            return None
        return _derived_site(self._all_of_site_schema(obj, *site), _bare_ref_members(obj))

    def _all_of_enum_site_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the schema of an allOf site of enum references whose merged enum leaves out what its parts enforce.

        Such a site gets a model without fields that ``extract_inherited_enum`` writes as one enum of the members of
        every referenced enum, in the class of the first, without the members a class with a value base cannot hold.
        That enum stays where each referenced enum lists exactly the values the parts admit together, and where the
        parts are no value schemas or are not intersected here. Where they admit no value in common (warned about), it
        stays where its class holds every member, else the site takes its first part. Elsewhere, under
        ``--use-site-models-for-constrained-refs``, the site is the intersection of its parts.
        """
        if (
            not (refs := _bare_ref_members(obj))
            or (parts := self._all_of_value_parts(obj)) is None
            or not self._intersects_value_parts(parts)
        ):
            return None
        targets = [
            self._intersect_typed_value_parts(target_parts, None)
            for ref in refs
            if (target_parts := self._reference_value_parts(ref)) is not None
        ]
        if not (enums := [target for target in targets if target.enum]):
            return None
        if self._satisfiable_all_of_value_parts(obj) is None:
            return (
                self._all_of_site_schema(obj, parts, parts[0].model_dump(exclude_unset=True, by_alias=True))
                if self._merged_enum_fails(enums)
                else None
            )
        if not self.config.use_site_models_for_constrained_refs:
            return None
        intersection = _enum_narrowed_by_part_consts(
            self._intersect_typed_value_parts(parts, str(obj.schema_location or "allOf")),
            parts,
            const_support=self.schema_features.const_support,
        )
        literals = intersection.enum
        if all(
            all(any(_json_literal_values_equal(value, other) for other in literals) for value in target.enum)
            and all(any(_json_literal_values_equal(value, other) for other in target.enum) for value in literals)
            for target in enums
        ):
            return None
        return _derived_site(
            self._all_of_site_schema(obj, parts, intersection.model_dump(exclude_unset=True, by_alias=True)), refs
        )

    def _merged_enum_fails(self, enums: Sequence[JsonSchemaObject]) -> bool:
        """Return whether the enum merged from ``enums``, in the class of the first, takes a member it cannot hold.

        A class with a value base (``IntEnum``, ``StrEnum``) cannot hold a member its value type does not hold; a
        ``StrEnum`` holds only a str, as ``extract_inherited_enum`` decides.
        ``extract_inherited_enum`` leaves out the members it cannot construct (``holds_inherited_member_value``), so
        the merge imports; this decides only whether a site whose parts admit no value in common takes its first part,
        as it did when the merge failed at import. A ``None`` member counts only with --intersect-allof-keywords. An
        output that writes the first enum as a ``Literal`` or as its const merges no enum class.
        """
        first = enums[0]
        if self.should_parse_enum_as_literal(first) or self._reads_enum_as_const(first):
            return False
        enum_class = self._get_enum_model_class(self._get_enum_type(first), first.enum)[0]
        value_type = "int" if issubclass(enum_class, IntEnum) else "str" if issubclass(enum_class, StrEnum) else None
        keep = self.config.keep_enum_member_values
        return value_type is not None and any(
            self.intersects_allof_keywords
            if value is None
            else not (
                holds_inherited_member_value(value_type, value)
                if value_type == "str"
                else holds_member_value(value_type, value, keep_member_values=keep)
            )
            for target in enums
            for value in target.enum
        )

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
        lightweight type of a TypedDict ``extra_items`` types them (``_build_lightweight_array_type``).
        """
        update: dict[str, object] = {}
        if (
            _is_false_all_of(obj.additionalProperties)
            and not obj.properties
            and (
                not self._has_pattern_properties_validator(obj) and self._other_keys_schema(obj) is not None
                if obj.patternProperties
                else obj.is_object or self._typed_nullable_map_value_schema(obj) is not None
            )
        ):
            update["additionalProperties"] = False
        if _is_false_all_of(obj.items) and self._get_fixed_length_homogeneous_tuple_length(obj) != 0:
            update["items"] = False
        update |= self._read_false_all_of_array_positions(
            obj, lightweight=lightweight
        ) | self._read_false_all_of_map_schemas(obj)
        return obj.model_copy(update=update) if update else obj

    def _read_false_all_of_array_positions(
        self, obj: JsonSchemaObject, *, lightweight: bool = False
    ) -> dict[str, object]:
        """Return the array keywords of ``obj`` whose false allOf the array parse types, with ``false`` in its place.

        Those are the entries of a tuple ``items`` and of ``prefixItems`` that array parsing types, the schemas before
        the first false (``_get_array_item_schemas``), and the ``additionalItems`` or ``unevaluatedItems`` that types
        the items past them. A position it leaves untyped keeps its output. The lightweight type of ``lightweight``
        types the prefixItems and the true tail as well.
        """
        if not obj.is_array or obj.ref:
            return {}
        entries = {"items": obj.items if isinstance(obj.items, list) else [], "prefixItems": obj.prefixItems or []}
        tails = {"additionalItems": obj.additionalItems, "unevaluatedItems": obj.unevaluatedItems}
        if not any(_is_false_all_of(item) for item in (*chain.from_iterable(entries.values()), *tails.values())):
            return {}
        typed = {
            id(item)
            for item in self._get_array_item_schemas(
                obj, include_true_tail_schema=lightweight, force_prefix_items=lightweight
            )[0]
        }

        def reads(item: object) -> bool:
            return id(item) in typed and _is_false_all_of(item)

        return {
            **{
                key: [False if reads(item) else item for item in items]
                for key, items in entries.items()
                if any(map(reads, items))
            },
            **{key: False for key, item in tails.items() if reads(item)},
        }

    def _read_false_all_of_map_schemas(self, obj: JsonSchemaObject) -> dict[str, object]:
        """Return the patternProperties or propertyNames of the map ``obj`` whose false allOf it types, as ``false``.

        An object map without properties (``_parse_object_map``) types each value of its patternProperties; without
        patternProperties it types its propertyNames. A patternProperties validator owns the patterns instead, and
        the other schemas of ``obj`` (properties, items, ``$ref``, combinators) leave both untyped, so they keep their
        output.
        """
        if any((obj.properties, obj.is_array, obj.ref, obj.allOf, obj.anyOf, obj.oneOf)):
            return {}
        if pattern_properties := obj.patternProperties:
            if self.generate_schema_validators or not any(map(_is_false_all_of, pattern_properties.values())):
                return {}
            return {
                "patternProperties": {
                    pattern: False if _is_false_all_of(schema) else schema
                    for pattern, schema in pattern_properties.items()
                }
            }
        return {"propertyNames": False} if _is_false_all_of(obj.propertyNames) else {}

    def _array_all_of_site_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        """Return an array site with ``allOf`` as the intersection of its own keywords and its members, else ``obj``.

        The site is then one value schema, as a site of another type is in ``parse_all_of``, instead of its own array
        beside a model of its members. It keeps its form where the root rule (``_all_of_root_value_schema``) does, and
        without --intersect-allof-keywords.
        """
        if (
            not (self.intersects_allof_keywords and obj.allOf and obj.is_array)
            or (value := self._all_of_root_value_schema(obj)) is None
        ):
            return obj
        return value.with_schema_location(obj.schema_location)

    def _ref_member_narrows_literals(
        self, member: JsonSchemaObject, target_literals: object, site: JsonSchemaObject
    ) -> bool:
        """Return whether the sibling ``enum`` or ``const`` of a ``$ref`` member admits some, not all, target literals.

        Only in a dialect that reads ``$ref`` siblings as parts, and with --intersect-allof-keywords; siblings that
        admit none keep the reference.
        """
        if not (
            self.intersects_allof_keywords
            and self._ref_sibling_keywords_enabled
            and (member.enum or "const" in member.extras)
        ):
            return False
        merged = self._schema_literal_values(self._merge_ref_with_schema(member, str(site.schema_location or "allOf")))
        return (
            isinstance(target_literals, list)
            and bool(merged)
            and len(cast("tuple[object, ...]", merged)) < len(target_literals)
        )

    def parse_combined_schema(  # noqa: PLR0912
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        target_attribute_name: str,
    ) -> list[DataType]:
        """Parse combined schema (anyOf, oneOf, allOf) into a list of data types."""
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
        dropped = {
            id(member)
            for member in _excluded_null_members(
                obj, [item for item in items if not self._is_false_schema_item(item)], self._null_mode
            )
        }
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
            if target_attribute.ref:
                if get_ref_type(target_attribute.ref) == JSONReference.LOCAL:
                    if use_builtin_false_ref_facts is None:
                        use_builtin_false_ref_facts = self._uses_builtin_false_ref_facts()
                    if self._is_local_ref_false_schema(
                        target_attribute.ref,
                        use_builtin_facts=use_builtin_false_ref_facts,
                    ):
                        continue
                if target_attribute.has_ref_with_schema_keywords and not target_attribute.is_ref_with_nullable_only:
                    merged_attr = self._merge_ref_with_schema(target_attribute)
                    self._preserve_inherited_materialized_type_shape(target_attribute, merged_attr)
                    if merged_attr.ref:
                        combined_schemas.append(merged_attr)
                        refs.append(ref_index)
                    else:
                        combined_schemas.append(
                            _mark_copied_nullable(
                                _locate(
                                    self._preserve_inherited_materialized_type_shape(
                                        target_attribute,
                                        self._schema_object(
                                            self._merge_all_of_schema(
                                                base_object,
                                                merged_attr.model_dump(exclude_unset=True, by_alias=True),
                                                intersect_bounds=False,
                                            ),
                                        ),
                                    ),
                                    target_attribute.schema_location,
                                ),
                                base_object,
                                merged_attr,
                                default_left_off=default_left_off,
                            )
                        )
                else:
                    combined_schemas.append(target_attribute)
                    refs.append(ref_index)
            else:
                combined_schemas.append(
                    _mark_copied_nullable(
                        _locate(
                            self._preserve_inherited_materialized_type_shape(
                                target_attribute,
                                self._schema_object(
                                    self._merge_all_of_schema(
                                        base_object,
                                        target_attribute.model_dump(exclude_unset=True, by_alias=True),
                                        intersect_bounds=False,
                                    ),
                                ),
                            ),
                            target_attribute.schema_location,
                        ),
                        base_object,
                        target_attribute,
                        default_left_off=default_left_off,
                    )
                )

        variant_keys: dict[int, str] = {}
        variant_names = self._get_inferred_union_variant_names(name, obj, combined_schemas, variant_keys)
        if self._output_model_context.requires_tagged_union_discriminator:
            self._set_tagged_union_discriminator(obj, combined_schemas)
        parsed_schemas = self._parse_combined_schema_items(
            name, obj, path, combined_schemas, variant_names, variant_keys
        )
        if not parsed_schemas:
            self._raise_unsatisfiable_schema(obj, path, target_attribute_name)
        common_path_keyword = f"{target_attribute_name}Common"
        return [
            self._parse_object_common_part(
                name,
                obj.with_schema_location(combined_schemas[i].schema_location),
                [*get_special_path(common_path_keyword, path), str(i)],
                ignore_duplicate_model=True,
                fields=[],
                base_classes=[d.reference],
                required=[],
            )
            if i in refs and d.reference
            else d
            for i, d in enumerate(parsed_schemas)
        ]

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
        if not isinstance(item, JsonSchemaObject):
            return False
        if not item.required and not (allow_empty and "required" in item.model_fields_set):
            return False

        return _is_object_only_type(item.type) and _is_empty_property_schema(
            item, allowed_keywords=self.REQUIRED_ONLY_SCHEMA_ALLOWED_FIELDS
        )

    def _get_required_groups(self, items: Sequence[JsonSchemaObject | bool]) -> tuple[tuple[str, ...], ...]:
        if not items:
            return ()

        groups: list[tuple[str, ...]] = []
        for item in items:
            if not self._is_required_only_schema(item):
                return ()
            groups.append(tuple(item.required))
        return tuple(groups)

    def _get_not_required_groups(self, obj: JsonSchemaObject) -> tuple[tuple[str, ...], ...]:
        """Return the required-only property group `not` forbids, or an empty tuple."""
        not_schema = self._get_conditional_schema(obj, "not")
        if not_schema is None or not self._is_required_only_schema(not_schema, allow_empty=True):
            return ()
        return (tuple(not_schema.required),)

    def _has_required_group_validators(self, obj: JsonSchemaObject) -> bool:
        """Return whether obj has a oneOf, anyOf, or not required-property group."""
        return bool(
            self._get_required_groups(obj.oneOf)
            or self._get_required_groups(obj.anyOf)
            or self._get_not_required_groups(obj)
        )

    def _get_conditional_schema(
        self,
        obj: JsonSchemaObject,
        keyword: Literal["if", "then", "else", "not"],
    ) -> JsonSchemaObject | bool | None:
        """Return the branch schema named by keyword, which the schema graph validates once from extras."""
        item = obj.extras.get(keyword)
        if isinstance(item, (JsonSchemaObject, bool)):
            return item
        if isinstance(item, dict):
            return self._shared_object(item, self.SCHEMA_OBJECT_TYPE, partial(self._schema_object, item))
        return None

    def _iter_conditional_branches(self, obj: JsonSchemaObject) -> Iterator[JsonSchemaObject]:
        for keyword in ("then", "else"):
            branch = self._get_conditional_schema(obj, keyword)
            if isinstance(branch, JsonSchemaObject):
                yield branch

    def _has_conditional_validator(self, obj: JsonSchemaObject) -> bool:
        return self._get_conditional_predicate(obj) is not None and any(
            branch.required for branch in self._iter_conditional_branches(obj)
        )

    def _has_schema_validator_constraints(self, obj: JsonSchemaObject) -> bool:
        return bool(
            self._has_required_group_validators(obj)
            or self._has_conditional_validator(obj)
            or self._has_pattern_properties_validator(obj)
            or self._has_property_count_validator(obj)
        )

    def _has_pattern_properties_validator(self, obj: JsonSchemaObject) -> bool:
        """Return whether schema runtime validation owns patternProperties dispatch."""
        return self.generate_schema_validators and any(
            source.patternProperties for source in self._iter_schema_validation_sources(obj)
        )

    def _has_core_schema_runtime_rules(  # noqa: PLR6301
        self, runtime_validation: SchemaRuntimeValidation
    ) -> bool:
        """Return whether rendering this model shadows an inherited core validator."""
        return bool(
            runtime_validation.pattern_properties
            or runtime_validation.required_groups
            or runtime_validation.conditional_required
            or runtime_validation.unique_items
        )

    def _has_property_count_validator(self, obj: JsonSchemaObject) -> bool:
        """Return whether an object or its allOf sources constrain property counts."""
        return self._get_property_count_rule(obj) is not None

    def _get_property_count_rule(self, obj: JsonSchemaObject) -> PropertyCountRule | None:
        """Aggregate one immutable count rule and cache it for this parse only."""
        if not self.generate_schema_validators:
            return None
        cache_key = id(obj)
        if (cached := self._property_count_rule_cache.get(cache_key)) is not None and cached[0] is obj:
            return cached[1]

        min_properties: int | None = None
        max_properties: int | None = None
        for source in self._iter_property_count_validation_sources(obj):
            match source.minProperties:
                case int() as source_minimum if source_minimum > 0:
                    min_properties = source_minimum if min_properties is None else max(min_properties, source_minimum)
                case _:
                    pass
            if (source_maximum := source.maxProperties) is not None:
                max_properties = source_maximum if max_properties is None else min(max_properties, source_maximum)
        rule = (
            PropertyCountRule(min_properties=min_properties, max_properties=max_properties)
            if min_properties is not None or max_properties is not None
            else None
        )
        self._property_count_rule_cache[cache_key] = obj, rule
        return rule

    def _iter_property_count_validation_sources(
        self,
        obj: JsonSchemaObject,
        visited_refs: frozenset[_TargetNode] = frozenset(),
    ) -> Iterator[JsonSchemaObject]:
        """Yield count constraints from allOf, preserving JSON Schema $ref siblings."""
        yield obj
        for item in obj.allOf:
            if not isinstance(item, JsonSchemaObject):
                continue
            if not item.ref:
                yield from self._iter_property_count_validation_sources(item, visited_refs)
                continue
            yield item
            if (node := self._key_node(self._ref_path(item.ref))) in visited_refs:
                continue
            yield from self._iter_property_count_validation_sources(
                self._load_ref_schema_object(item.ref),
                visited_refs | {node},
            )

    def _should_parse_object_with_schema_validators(self, obj: JsonSchemaObject) -> bool:
        if not self.generate_schema_validators:
            return False
        has_pattern_properties = bool(obj.patternProperties)
        if has_pattern_properties:
            return True
        if obj.properties and self._has_required_group_validators(obj):
            return True
        if obj.properties and self._has_property_count_validator(obj):
            return True
        has_conditional_properties = any(
            branch.properties or branch.patternProperties for branch in self._iter_conditional_branches(obj)
        )
        return bool((obj.properties or has_conditional_properties) and self._has_conditional_validator(obj))

    def _merge_conditional_properties(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        if not self.generate_schema_validators:
            return obj

        merged_properties: dict[str, Any] = {
            key: value.model_dump(exclude_unset=True, by_alias=True) if isinstance(value, JsonSchemaObject) else value
            for key, value in (obj.properties or {}).items()
        }
        for branch in self._iter_conditional_branches(obj):
            if not branch.properties:
                continue
            for key, value in branch.properties.items():
                if key in merged_properties:
                    continue
                merged_properties[key] = (
                    value.model_dump(exclude_unset=True, by_alias=True)
                    if isinstance(value, JsonSchemaObject)
                    else value
                )

        if not merged_properties or set(merged_properties) == set(obj.properties or {}):
            return obj

        schema_dict = obj.model_dump(exclude_unset=True, by_alias=True)
        schema_dict["properties"] = merged_properties
        return self._schema_object(schema_dict)

    def _field_input_names(self, field: DataModelFieldBase) -> tuple[str, ...]:
        """Return Pydantic's ordered raw input names for one declared field."""
        names: list[str] = []

        def append_name(value: str | None) -> None:
            if value is not None and value not in names:
                names.append(value)

        if field.validation_aliases:
            for alias in field.validation_aliases:
                append_name(alias)
        elif field.use_serialization_alias:
            append_name(self._field_alias_generator_input_name(field))
        else:
            append_name(field.alias)
            if field.alias is None:
                append_name(self._field_alias_generator_input_name(field))
        if (
            field.use_serialization_alias
            or field.alias is None
            or self.config.allow_population_by_field_name
            or self.config.alias_generator
        ):
            append_name(field.name)
        return tuple(names)

    def _field_alias_generator_input_name(self, field: DataModelFieldBase) -> str | None:
        """Return the generated Pydantic validation alias for one field."""
        if field.name is None:
            return None
        match self.config.alias_generator:
            case AliasGenerator.ToCamel:
                return to_camel(field.name, self.target_python_version)
            case AliasGenerator.ToPascal:
                return to_pascal(field.name, self.target_python_version)
            case AliasGenerator.ToSnake:
                return to_snake(field.name, self.target_python_version)
        return None

    def _get_input_names_by_property(
        self,
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
    ) -> dict[str, tuple[str, ...]]:
        names_by_property: dict[str, tuple[str, ...]] = {}
        for field in fields:
            if field.name == self.data_model_type.TYPED_EXTRA_FIELD_NAME:
                continue
            property_name = _field_source_name(field)
            if property_name is not None:
                names_by_property[property_name] = self._field_input_names(field)

        for base_class in base_classes:
            data_model = base_class.source if isinstance(base_class.source, DataModel) else None
            if data_model is not None:
                for field in data_model.iter_all_fields():
                    if field.name == self.data_model_type.TYPED_EXTRA_FIELD_NAME:
                        continue
                    property_name = _field_source_name(field)
                    if property_name is not None:
                        names_by_property.setdefault(property_name, self._field_input_names(field))
                continue

            for schema, _, _ in self._iter_inherited_schema_objects([base_class], frozenset()):
                if not schema.properties:
                    continue
                for property_name in schema.properties:
                    names_by_property.setdefault(property_name, (property_name,))

        return names_by_property

    def _required_group_input_names(  # noqa: PLR6301
        self,
        groups: Sequence[Sequence[str]],
        names_by_property: dict[str, tuple[str, ...]],
    ) -> tuple[tuple[tuple[str, ...], ...], ...]:
        return tuple(
            tuple(names_by_property.get(property_name, (property_name,)) for property_name in group) for group in groups
        )

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
        for source, _ in self._iter_scoped_schema_validation_sources(
            obj, visited_refs, include_references=include_references
        ):
            yield source

    def _iter_scoped_schema_validation_sources(
        self,
        obj: JsonSchemaObject,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        scope: str | None = "",
        *,
        include_references: bool = True,
    ) -> Iterator[tuple[JsonSchemaObject, str | None]]:
        """Yield the schema and its allOf sources with the document each was read from.

        The scope of a source is the file part of its resolved reference, ``""`` for the schema itself, and None
        when the document is not known: a reference followed inside another document resolves against the current
        one.
        """
        yield obj, scope
        for item in obj.allOf:
            if not isinstance(item, JsonSchemaObject):
                continue
            if item.ref:
                if self._ref_sibling_keywords_enabled:
                    has_sibling_constraints = (
                        item.patternProperties
                        or item.additionalProperties is not None
                        or item.unevaluatedProperties is not None
                    )
                    if item.allOf or item.oneOf or item.anyOf or "if" in item.extras or has_sibling_constraints:
                        yield from self._iter_scoped_schema_validation_sources(
                            item.model_copy(update={"ref": None}), visited_refs, scope, include_references=False
                        )
                if not include_references:
                    continue
                resolved_ref = self._ref_path(item.ref)
                if (node := self._key_node(resolved_ref)) in visited_refs:
                    continue
                file_part = resolved_ref.split("#", 1)[0]
                root_file = self.model_resolver.resolve_ref("#").split("#", 1)[0]
                yield from self._iter_scoped_schema_validation_sources(
                    self._load_ref_schema_object(item.ref),
                    visited_refs | {node},
                    None if scope is None or scope else ("" if file_part == root_file else file_part),
                )
                continue
            yield from self._iter_scoped_schema_validation_sources(
                item, visited_refs, scope, include_references=include_references
            )

    def _schema_item_may_accept_container(
        self,
        item: JsonSchemaObject | bool,  # noqa: FBT001
        container_type: Literal["array", "object"],
        visited_refs: frozenset[_TargetNode],
    ) -> bool:
        """Return whether a schema item can accept one raw JSON container shape."""
        if isinstance(item, JsonSchemaObject):
            return self._schema_may_accept_container(item, container_type, visited_refs)
        return item

    def _schema_literal_values(self, obj: JsonSchemaObject) -> tuple[object, ...] | None:
        """Return direct const/enum values after their JSON Schema intersection."""
        has_const = self.schema_features.const_support and "const" in obj.extras
        const = obj.extras.get("const") if has_const else None
        has_enum = "enum" in obj.model_fields_set
        if not has_const and not has_enum:
            return None
        if not has_const:
            return tuple(obj.enum)
        if not has_enum or any(_json_literal_values_equal(const, value) for value in obj.enum):
            return (const,)
        return ()

    def _schema_may_accept_container(  # noqa: PLR0911
        self,
        obj: JsonSchemaObject,
        container_type: Literal["array", "object"],
        visited_refs: frozenset[_TargetNode],
    ) -> bool:
        """Return whether a schema can accept one raw JSON container shape."""
        if obj.is_boolean_schema_false:
            return False
        if obj.ref:
            node = self._key_node(self._ref_path(obj.ref))
            if not self._ref_sibling_keywords_enabled:
                return node in visited_refs or self._schema_may_accept_container(
                    self._load_ref_schema_object(obj.ref), container_type, visited_refs | {node}
                )
            if node not in visited_refs and not self._schema_may_accept_container(
                self._load_ref_schema_object(obj.ref), container_type, visited_refs | {node}
            ):
                return False
        if (values := self._schema_literal_values(obj)) is not None and not any(
            _json_literal_may_accept_container(value, container_type) for value in values
        ):
            return False
        if obj.type is not None and not _json_schema_type_may_accept_container(obj.type, container_type):
            return False

        if any(not self._schema_item_may_accept_container(item, container_type, visited_refs) for item in obj.allOf):
            return False
        return all(
            not items
            or any(self._schema_item_may_accept_container(item, container_type, visited_refs) for item in items)
            for items in (obj.anyOf, obj.oneOf)
        )

    def _unique_items_path_container_type(  # noqa: PLR0911, PLR6301
        self,
        path: UniqueItemsPath,
    ) -> Literal["array", "object"]:
        """Return the raw container shape needed for one relative uniqueItems path."""
        if not path:
            return "array"
        match path[0]:
            case None | int():
                return "array"
            case (marker, int()) if marker == UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP:
                return "array"
            case marker if marker == UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP:
                return "object"
            case (marker, str(), None) if marker == UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP:
                return "object"
            case (marker, tuple(), tuple()) if marker == UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP:
                return "object"
            case tuple():
                return "object"
        return "object"

    def _get_union_unique_items_branch_rules(
        self,
        items: Sequence[JsonSchemaObject | bool],
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode],
        property_input_names: Mapping[str, tuple[str, ...]] | None,
    ) -> tuple[list[tuple[JsonSchemaObject | bool, tuple[UniqueItemsRule, ...]]], list[UniqueItemsRule]]:
        """Collect ordered uniqueItems rules produced by each union branch."""
        branch_rules: list[tuple[JsonSchemaObject | bool, tuple[UniqueItemsRule, ...]]] = []
        candidates: list[UniqueItemsRule] = []
        for item in items:
            if item is False:
                continue
            if item is True:
                branch_rules.append((item, ()))
                continue
            branch = item
            rules = tuple(
                self._iter_unique_items_rules(
                    branch,
                    path,
                    visited_refs,
                    property_input_names,
                    include_properties=True,
                )
            )
            if branch.ref and not self.collapse_root_models:
                node = self._key_node(self._ref_path(branch.ref))
                if node not in visited_refs:
                    rules = (
                        *rules,
                        *self._iter_unique_items_rules(
                            self._load_ref_schema_object(branch.ref),
                            path,
                            visited_refs | {node},
                            property_input_names,
                            include_properties=True,
                        ),
                    )
            branch_rules.append((branch, rules))
            candidates.extend(rule for rule in rules if all(rule.path != candidate.path for candidate in candidates))
        return branch_rules, candidates

    def _iter_union_unique_items_rules(
        self,
        items: Sequence[JsonSchemaObject | bool],
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode],
        *,
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
    ) -> Iterator[UniqueItemsRule]:
        """Yield paths unconditionally required by every shape-compatible union branch."""
        branch_rules, candidates = self._get_union_unique_items_branch_rules(
            items, path, visited_refs, property_input_names
        )

        for candidate in candidates:
            container_type = self._unique_items_path_container_type(candidate.path[len(path) :])
            for branch, rules in branch_rules:
                if not self._schema_item_may_accept_container(branch, container_type, visited_refs):
                    continue
                if not any(rule.path == candidate.path for rule in rules):
                    break
            else:
                yield candidate

    def _iter_union_unique_items_paths(
        self,
        items: Sequence[JsonSchemaObject | bool],
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode],
    ) -> Iterator[UniqueItemsPath]:
        """Yield raw uniqueItems paths for parser inspection callers."""
        yield from (rule.path for rule in self._iter_union_unique_items_rules(items, path, visited_refs))

    def _get_common_executable_unique_items_paths(
        self,
        data_types: Sequence[DataType],
        path: UniqueItemsPath,
        visited: set[int],
        *,
        filter_array_branches: bool = True,
    ) -> set[UniqueItemsPath]:
        """Return paths every branch delegates to an executable validator."""
        executable_data_types = (
            tuple(data_type for data_type in data_types if self._data_type_may_accept_array(data_type))
            if filter_array_branches
            else data_types
        )
        if not executable_data_types:
            return set()
        common_paths = self._get_executable_unique_items_paths(executable_data_types[0], path, visited)
        for data_type in executable_data_types[1:]:
            common_paths.intersection_update(self._get_executable_unique_items_paths(data_type, path, visited))
            if not common_paths:
                return common_paths
        return common_paths

    def _data_type_may_accept_array(
        self,
        data_type: DataType,
        visited: frozenset[int] = frozenset(),
    ) -> bool:
        """Return whether a generated type can receive an array from raw input."""
        data_type_id = id(data_type)
        if data_type_id in visited:
            return False
        visited |= {data_type_id}

        match data_type:
            case DataType(is_list=True) | DataType(is_set=True) | DataType(is_sequence=True) | DataType(is_tuple=True):
                return True
            case DataType(reference=reference) if reference is not None and isinstance(reference.source, DataModel):
                source = reference.source
                return bool(
                    source.IS_ROOT_MODEL
                    and source.fields
                    and self._data_type_may_accept_array(source.fields[0].data_type, visited)
                )
            case DataType(data_types=data_types) if data_types:
                return any(self._data_type_may_accept_array(child, visited) for child in data_types)
            case DataType(type="None" | "str" | "int" | "float" | "bool" | "bytes"):
                return False
            case _:
                pass
        return True

    def _get_executable_unique_items_paths(  # noqa: PLR0911, PLR0912
        self,
        data_type: DataType,
        path: UniqueItemsPath,
        visited: set[int] | None = None,
    ) -> set[UniqueItemsPath]:
        """Return raw paths delegated by every applicable generated type branch."""
        if visited is None:
            visited = set()
        data_type_id = id(data_type)
        if data_type_id in visited:
            return set()
        visited.add(data_type_id)
        try:
            if data_type.reference and isinstance(data_type.reference.source, DataModel):
                source = data_type.reference.source
                runtime_validation = source.schema_runtime_validation
                if not _is_internal_schema_runtime_validation(runtime_validation):
                    runtime_validation = self.extra_template_data[data_type.reference.path].get(
                        "schema_runtime_validation"
                    )
                if (
                    not source.is_alias
                    and source.SUPPORTS_SCHEMA_RUNTIME_VALIDATION
                    and isinstance(runtime_validation, SchemaRuntimeValidation)
                    and _is_internal_schema_runtime_validation(runtime_validation)
                ):
                    if self.collapse_root_models and source.IS_ROOT_MODEL:
                        return set()
                    return {(*path, *rule.path) for rule in runtime_validation.unique_items}

            match data_type:
                case DataType(is_list=True) | DataType(is_set=True) | DataType(is_sequence=True):
                    return self._get_common_executable_unique_items_paths(
                        data_type.data_types,
                        (*path, None),
                        visited,
                        filter_array_branches=False,
                    )
                case DataType(is_tuple=True, tuple_item_count=int() as item_count):
                    owned_paths = cast("set[UniqueItemsPath]", set())
                    for index, child in enumerate(data_type.data_types[:item_count]):
                        owned_paths.update(self._get_executable_unique_items_paths(child, (*path, index), visited))
                    return owned_paths
                case DataType(is_tuple=True):
                    return self._get_common_executable_unique_items_paths(
                        data_type.data_types,
                        (*path, None),
                        visited,
                        filter_array_branches=False,
                    )
                case DataType(is_dict=True) | DataType(is_mapping=True):
                    return self._get_common_executable_unique_items_paths(
                        data_type.data_types,
                        (*path, UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP),
                        visited,
                        filter_array_branches=False,
                    )
                case _:
                    pass
            return self._get_common_executable_unique_items_paths(data_type.data_types, path, visited)
        finally:
            visited.remove(data_type_id)

    def _iter_unique_items_rules(  # noqa: PLR0912
        self,
        obj: JsonSchemaObject,
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
        *,
        include_properties: bool = False,
    ) -> Iterator[UniqueItemsRule]:
        """Yield compact uniqueItems rules required by all applicable union branches."""
        if obj.ref:
            if self.collapse_root_models:
                node = self._key_node(self._ref_path(obj.ref))
                if node not in visited_refs:
                    yield from self._iter_unique_items_rules(
                        self._load_ref_schema_object(obj.ref),
                        path,
                        visited_refs | {node},
                        property_input_names,
                        include_properties=include_properties,
                    )
            if not self._ref_sibling_keywords_enabled:
                return
        if obj.uniqueItems is True:
            yield UniqueItemsRule(path=path)

        for item in obj.allOf:
            if not isinstance(item, JsonSchemaObject):
                continue
            yield from self._iter_unique_items_rules(
                item,
                path,
                visited_refs,
                property_input_names,
                include_properties=include_properties,
            )
            if not item.ref:
                continue
            if (node := self._key_node(self._ref_path(item.ref))) in visited_refs:
                continue
            yield from self._iter_unique_items_rules(
                self._load_ref_schema_object(item.ref),
                path,
                visited_refs | {node},
                property_input_names,
                include_properties=include_properties,
            )

        if obj.anyOf:
            yield from self._iter_union_unique_items_rules(
                obj.anyOf,
                path,
                visited_refs,
                property_input_names=property_input_names,
            )
        if obj.oneOf:
            yield from self._iter_union_unique_items_rules(
                obj.oneOf,
                path,
                visited_refs,
                property_input_names=property_input_names,
            )

        if include_properties:
            for property_name, property_schema in (obj.properties or {}).items():
                if not isinstance(property_schema, JsonSchemaObject):
                    continue
                input_names = (
                    property_input_names.get(property_name, (property_name,))
                    if property_input_names is not None
                    else (property_name,)
                )
                yield from self._iter_unique_items_rules(
                    property_schema,
                    (*path, input_names),
                    visited_refs,
                    include_properties=True,
                )

        match obj.items:
            case JsonSchemaObject() as item:
                items_path_step: UniqueItemsPathStep = (
                    (UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP, len(obj.prefixItems))
                    if self.schema_features.prefix_items and obj.prefixItems
                    else None
                )
                yield from self._iter_unique_items_rules(
                    item,
                    (*path, items_path_step),
                    visited_refs,
                    include_properties=include_properties,
                )
            case list() as items:
                for index, item in enumerate(items):
                    if isinstance(item, JsonSchemaObject):
                        yield from self._iter_unique_items_rules(
                            item,
                            (*path, index),
                            visited_refs,
                            include_properties=include_properties,
                        )

        if self.schema_features.prefix_items and obj.prefixItems:
            for index, item in enumerate(obj.prefixItems):
                if isinstance(item, JsonSchemaObject):
                    yield from self._iter_unique_items_rules(
                        item,
                        (*path, index),
                        visited_refs,
                        include_properties=include_properties,
                    )

        if (
            not self.schema_features.prefix_items
            and isinstance(obj.additionalItems, JsonSchemaObject)
            and isinstance(obj.items, list)
        ):
            yield from self._iter_unique_items_rules(
                obj.additionalItems,
                (*path, (UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP, len(obj.items))),
                visited_refs,
                include_properties=include_properties,
            )

        for pattern, item in (obj.patternProperties or {}).items():
            if isinstance(item, JsonSchemaObject):
                yield from self._iter_unique_items_rules(
                    item,
                    (*path, (UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP, pattern, None)),
                    visited_refs,
                    include_properties=include_properties,
                )

        if isinstance(obj.additionalProperties, JsonSchemaObject):
            if obj.properties or obj.patternProperties:
                additional_path_step: UniqueItemsPathStep = (
                    UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP,
                    tuple(
                        property_input_names.get(property_name, (property_name,))
                        if property_input_names is not None
                        else (property_name,)
                        for property_name in obj.properties or {}
                    ),
                    tuple(obj.patternProperties or {}),
                )
            else:
                additional_path_step = UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP
            yield from self._iter_unique_items_rules(
                obj.additionalProperties,
                (*path, additional_path_step),
                visited_refs,
                include_properties=include_properties,
            )

    def _iter_unique_items_paths(
        self,
        obj: JsonSchemaObject,
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
    ) -> Iterator[UniqueItemsPath]:
        """Yield raw uniqueItems paths for parser inspection callers."""
        yield from (rule.path for rule in self._iter_unique_items_rules(obj, path, visited_refs, property_input_names))

    def _add_unique_items_validator(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
        *,
        is_root_model: bool,
    ) -> None:
        """Register ``uniqueItems`` checks as compact paths through raw input data."""
        rules: list[UniqueItemsRule] = []
        replace_inherited_rules = False

        def is_owned_path(unique_items_path: UniqueItemsPath, owned_paths: set[UniqueItemsPath]) -> bool:
            if unique_items_path in owned_paths:
                return True
            for owned_path in owned_paths:
                if len(unique_items_path) != len(owned_path):
                    continue
                for path_step, owned_step in zip(unique_items_path, owned_path, strict=True):
                    if path_step == owned_step:
                        continue
                    match path_step, owned_step:
                        case (
                            (marker, tuple(), tuple() as patterns),
                            (owned_marker, tuple(), tuple() as owned_patterns),
                        ) if (
                            marker == UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP
                            and owned_marker == UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP
                            and patterns == owned_patterns
                        ):
                            continue
                    break
                else:
                    return True
            return False

        def get_data_type_property_input_names(data_type: DataType) -> dict[str, tuple[str, ...]] | None:
            if data_type.reference and isinstance(data_type.reference.source, DataModel):
                return {
                    property_name: self._field_input_names(field)
                    for field in data_type.reference.source.iter_all_fields()
                    if (property_name := _field_source_name(field)) is not None
                }
            return None

        def add_rules(
            schema: JsonSchemaObject,
            path: UniqueItemsPath,
            owned_paths: set[UniqueItemsPath] | None = None,
            property_input_names: Mapping[str, tuple[str, ...]] | None = None,
        ) -> None:
            for rule in self._iter_unique_items_rules(
                schema,
                path,
                property_input_names=property_input_names,
            ):
                if owned_paths is not None and is_owned_path(rule.path, owned_paths):
                    continue
                if all(existing_rule.path != rule.path for existing_rule in rules):
                    rules.append(rule)

        if is_root_model:
            root_type = fields[0].data_type if fields else None
            owned_paths = self._get_executable_unique_items_paths(root_type, ()) if root_type is not None else None
            add_rules(
                obj,
                (),
                owned_paths,
                self._get_input_names_by_property(fields, base_classes),
            )
        else:
            names_by_property = self._get_input_names_by_property(fields, base_classes)
            fields_by_property = {
                property_name: field for field in fields if (property_name := _field_source_name(field)) is not None
            }
            add_rules(obj, (), property_input_names=names_by_property)
            overridden_properties: set[str] = set()
            for source in self._iter_schema_validation_sources(obj):
                if not source.properties:
                    continue
                for property_name, property_schema in source.properties.items():
                    if not isinstance(property_schema, JsonSchemaObject):
                        continue
                    if property_name in overridden_properties:
                        if next(self._iter_unique_items_rules(property_schema, ()), None) is not None:
                            replace_inherited_rules = True
                        continue
                    if (input_names := names_by_property.get(property_name)) is not None:
                        field = fields_by_property.get(property_name)
                        owned_paths = (
                            self._get_executable_unique_items_paths(field.data_type, (input_names,))
                            if field is not None
                            and any(data_type.reference for data_type in field.data_type.all_data_types)
                            else None
                        )
                        add_rules(
                            property_schema,
                            (input_names,),
                            owned_paths,
                            get_data_type_property_input_names(field.data_type) if field is not None else None,
                        )
                if overrides := source.python_field_overrides:
                    overridden_properties.update(overrides)
                    overridden_properties.update(overrides.values())

        if not rules and not replace_inherited_rules:
            return
        runtime_validation = self._schema_runtime_validation(reference_path)
        runtime_validation.unique_items.extend(rules)
        runtime_validation.replace_unique_items = replace_inherited_rules

    def _collect_pattern_property_validators(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> tuple[list[tuple[str, DataType]], list[str], DataType | None, bool]:
        pattern_value_types: list[tuple[str, DataType]] = []
        rejected_patterns: list[str] = []
        allow_unmatched = True

        for source_index, source in enumerate(self._iter_schema_validation_sources(obj)):
            if source.additionalProperties is False or source.unevaluatedProperties is False:
                allow_unmatched = False
            if not source.patternProperties:
                continue
            for pattern_index, (pattern, schema) in enumerate(source.patternProperties.items()):
                match schema:
                    case True:
                        pattern_value_types.append((pattern, self.data_type_manager.get_data_type(Types.any)))
                    case JsonSchemaObject() if not _is_false_all_of(schema):
                        pattern_value_types.append((
                            pattern,
                            self.data_type_manager.get_data_type(
                                Types.string,
                                field_constraints=False,
                                **_get_data_type_constraint_kwargs(schema, Types.string),
                            )
                            if schema is self._root_pattern_string_constraints
                            else self.parse_item(
                                name,
                                schema,
                                get_special_path(
                                    f"schemaValidators/patternProperties/{source_index}/{pattern_index}",
                                    path,
                                ),
                            ),
                        ))
                    case False | JsonSchemaObject():
                        rejected_patterns.append(pattern)

        if not pattern_value_types and not rejected_patterns:
            return pattern_value_types, rejected_patterns, None, allow_unmatched
        if (additional_property := self._first_schema_additional_properties(obj)) is None:
            return pattern_value_types, rejected_patterns, None, allow_unmatched

        source_index, source, additional_properties = additional_property
        if _is_false_all_of(additional_properties):
            return pattern_value_types, rejected_patterns, None, False
        additional_property_name = f"{name}AdditionalProperty"
        additional_property_type = self._parse_additional_properties_value(
            additional_property_name,
            get_special_path(f"schemaValidators/additionalProperties/{source_index}", path),
            source,
            additional_properties=additional_properties,
            constrained_name=additional_property_name,
        )
        return pattern_value_types, rejected_patterns, additional_property_type, allow_unmatched

    def _first_schema_additional_properties(
        self, obj: JsonSchemaObject
    ) -> tuple[int, JsonSchemaObject, JsonSchemaObject] | None:
        """Return the index, schema and value of the first schema-valued additionalProperties of ``obj``.

        It is read from ``obj`` and then its allOf sources (``_iter_schema_validation_sources``).
        """
        for index, source in enumerate(self._iter_schema_validation_sources(obj)):
            if isinstance(additional_properties := source.additionalProperties, JsonSchemaObject):
                return index, source, additional_properties
        return None

    def _pattern_rule_additional_properties(
        self, obj: JsonSchemaObject
    ) -> tuple[int, JsonSchemaObject, JsonSchemaObject] | None:
        """Return the index, schema and value of the additionalProperties the patternProperties rules of ``obj`` type.

        They are the first schema-valued additionalProperties (``_first_schema_additional_properties``), except one
        that is ``false`` written as an allOf (``_is_false_all_of``): the rules forbid the keys it covers instead.
        """
        if (additional_property := self._first_schema_additional_properties(obj)) is None or _is_false_all_of(
            additional_property[2]
        ):
            return None
        return additional_property

    @staticmethod
    def _plain_pattern_value_type(schema: object, *, allow_minimum: bool = False) -> str | None:
        """Identify inhabited primitives, optionally allowing integer minimum-only constraints."""
        if not isinstance(schema, JsonSchemaObject) or schema.extras:
            return None
        if schema.model_fields_set <= {"type", "default", "title", "description"}:
            return (
                schema.type
                if isinstance(schema.type, str) and schema.type in {"string", "integer", "boolean"}
                else None
            )
        if not allow_minimum or schema.type != "integer" or schema.model_fields_set != {"type", "minimum"}:
            return None
        if schema.minimum is not None and (
            isinstance(schema.minimum.value, int) or math.isfinite(schema.minimum.value)
        ):
            return "integer"
        return None

    @classmethod
    def _plain_pattern_object(cls, schema: object) -> dict[str, str] | None:
        """Recognize open objects whose primitive properties have a common JSON witness."""
        if not isinstance(schema, JsonSchemaObject):
            return None
        if (
            schema.type != "object"
            or schema.additionalProperties is not True
            or schema.extras
            or not schema.model_fields_set <= {"type", "properties", "required", "additionalProperties"}
            or not schema.properties
        ):
            return None
        if not set(schema.required or ()) <= schema.properties.keys():
            return None
        properties = {}
        for name, field in schema.properties.items():
            if (field_type := cls._plain_pattern_value_type(field, allow_minimum=True)) is None:
                return None
            properties[name] = field_type
        return properties

    @classmethod
    def _declared_pattern_rejects_value(cls, declared: object, pattern: object) -> bool:
        """Prove a skipped pattern rejects some value accepted by a simple declared field."""
        declared_type = cls._plain_pattern_value_type(declared)
        if declared_type is not None:
            if pattern is False or _is_false_all_of(pattern):
                return True
            return (
                declared_type == "integer"
                and isinstance(pattern, JsonSchemaObject)
                and pattern.type == "integer"
                and not pattern.extras
                and pattern.model_fields_set == {"type", "minimum"}
                and pattern.minimum is not None
                and (isinstance(pattern.minimum.value, int) or math.isfinite(pattern.minimum.value))
            )
        pattern_properties = cls._plain_pattern_object(pattern)
        if declared is True:
            return pattern_properties is not None and bool(cast("JsonSchemaObject", pattern).required)
        declared_properties = cls._plain_pattern_object(declared)
        return (
            declared_properties is not None
            and pattern_properties is not None
            and bool(
                set(cast("JsonSchemaObject", pattern).required or ())
                - set(cast("JsonSchemaObject", declared).required or ())
            )
        )

    @classmethod
    def _pattern_rejects_declared_value(
        cls, pattern_properties: dict[str, JsonSchemaObject | bool], declared_properties: dict[str, Any]
    ) -> bool:
        """Return whether a pattern matches a declared key and rejects a value of its simple declared schema."""
        for pattern, schema in pattern_properties.items():
            for name, declared in declared_properties.items():
                try:
                    matches = re.search(pattern, name)
                except re.error:
                    continue
                if matches and cls._declared_pattern_rejects_value(declared, schema):
                    return True
        return False

    @classmethod
    def _pattern_validation_loses_raw_value(
        cls,
        obj: JsonSchemaObject,
        pattern_value_types: list[tuple[str, DataType]],
        *,
        declared_witness: bool = False,
    ) -> Literal["declared", "models"] | None:
        """Limit changed helpers to incompatible model instances, or skipped constraints (``declared_witness``)."""
        if not obj.extras.keys() <= {"title", "description"}:
            return None
        return cls._pattern_validation_intersection(obj, pattern_value_types, declared_witness=declared_witness)

    @classmethod
    def _pattern_validation_intersection(
        cls,
        obj: JsonSchemaObject,
        pattern_value_types: list[tuple[str, DataType]],
        *,
        declared_witness: bool = False,
    ) -> Literal["declared", "models"] | None:
        """Prove the same bounded intersection after root annotation eligibility.

        With ``declared_witness``, the check of 0.86.0 without ``--check-pattern-maps-strictly``, a pattern that
        matches a declared key and rejects a value of its simple declared schema gives ``declared``.
        """
        if (
            obj.type != "object"
            or not obj.model_fields_set
            <= {
                "type",
                "properties",
                "required",
                "patternProperties",
                "additionalProperties",
                "title",
                "description",
                "extras",
            }
            or not obj.patternProperties
        ):
            return None
        declared_properties = obj.properties or {}
        if not set(obj.required or ()) <= declared_properties.keys() or any(
            field is not True
            and cls._plain_pattern_value_type(field) is None
            and cls._plain_pattern_object(field) is None
            for field in declared_properties.values()
        ):
            return None
        if declared_witness and cls._pattern_rejects_declared_value(obj.patternProperties, declared_properties):
            return "declared"
        pair_size = 2
        if len(obj.patternProperties) != pair_size or len(pattern_value_types) != pair_size:
            return None
        # A literal prefix followed by a literal suffix supplies an explicit common key.
        witness = None
        patterns = tuple(obj.patternProperties)
        for prefix, suffix in (patterns, patterns[::-1]):
            if (start := re.fullmatch(r"\^([A-Za-z0-9_ -]+)", prefix)) and (
                end := re.fullmatch(r"([A-Za-z0-9_ -]+)\$", suffix)
            ):
                witness = start[1] + end[1] if start[1] != end[1] else None
                break
        literal_models: dict[str, list[tuple[str, dict[str, str]]]] = {}
        for pattern, data_type in pattern_value_types:
            if (
                data_type.reference is None
                or (match := re.fullmatch(r"\^?([A-Za-z0-9_ -]+)\$?", pattern)) is None
                or (witness or match[1]) in declared_properties
                or (properties := cls._plain_pattern_object(obj.patternProperties.get(pattern, False))) is None
            ):
                continue
            previous = literal_models.setdefault(witness or match[1], [])
            if any(
                reference != data_type.reference.path
                and all(properties[name] == field_type for name, field_type in fields.items() if name in properties)
                for reference, fields in previous
            ):
                return "models"
            previous.append((data_type.reference.path, properties))
        return None

    @classmethod
    def _pattern_validation_with_undeclared_required(
        cls,
        obj: JsonSchemaObject,
        pattern_value_types: list[tuple[str, DataType]],
        *,
        declared_witness: bool = False,
    ) -> Literal["models"] | None:
        """Reuse the object compatibility proof after missing names receive raw required validation."""
        pair_size = 2
        if not obj.patternProperties or len(obj.patternProperties) != pair_size:
            return None
        patterns = {}
        for pattern, schema in obj.patternProperties.items():
            if (
                isinstance(schema, JsonSchemaObject)
                and schema.properties
                and not set(schema.required) <= schema.properties.keys()
            ):
                patterns[pattern] = schema.model_copy(
                    update={"required": [name for name in schema.required if name in schema.properties]}
                )
        if (
            patterns
            and cls._pattern_validation_loses_raw_value(
                obj.model_copy(update={"patternProperties": {**obj.patternProperties, **patterns}}),
                pattern_value_types,
                declared_witness=declared_witness,
            )
            == "models"
        ):
            return "models"
        return None

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

    def _pattern_checks_declared_key(
        self, obj: JsonSchemaObject, names_by_property: dict[str, tuple[str, ...]]
    ) -> bool:
        """Return whether a pattern matches a declared key and its schema adds an assertion to the declared schema.

        A key declared by several sources skips a pattern whose schema one of the declarations implies; a key with no
        declared schema among the sources is checked.
        """
        declared: dict[str, list[tuple[object, str | None]]] = {}
        patterns: list[tuple[str, object, str | None]] = []
        for source, scope in self._iter_scoped_schema_validation_sources(obj):
            for property_name, schema in (source.properties or {}).items():
                declared.setdefault(property_name, []).append((_schema_dump(schema), scope))
            patterns.extend(
                (pattern, _schema_dump(schema), scope) for pattern, schema in (source.patternProperties or {}).items()
            )
        return any(
            _searches_any(pattern, input_names)
            and not any(
                _schema_implies(schema, pattern_schema, same_document=scope is not None and scope == pattern_scope)
                for schema, scope in declared.get(property_name, ())
            )
            for property_name, input_names in names_by_property.items()
            for pattern, pattern_schema, pattern_scope in patterns
        )

    def _add_pattern_properties_validator(  # noqa: PLR0913, PLR0917
        self,
        reference_path: str,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
    ) -> None:
        pattern_value_types, rejected_patterns, additional_property_type, allow_unmatched = (
            self._collect_pattern_property_validators(name, obj, path)
        )
        if not pattern_value_types and not rejected_patterns:
            return

        names_by_property = self._get_input_names_by_property(fields, base_classes)
        declared_names = tuple(sorted({name for names in names_by_property.values() for name in names}))
        rule_type = PatternPropertiesRule
        helpers_kept = (
            any((self.config.base_class, self.config.base_class_map, self.config.custom_template_dir))
            or self.config.extra_template_data is not None
        )
        declared_witness = not self.check_pattern_maps_strictly
        intersection = (
            None
            if helpers_kept
            else self._pattern_validation_loses_raw_value(obj, pattern_value_types, declared_witness=declared_witness)
        )
        if obj is self._pattern_validation_document_root:
            if intersection is None and not self.force_optional_for_required_fields:
                intersection = self._pattern_validation_with_undeclared_required(
                    obj, pattern_value_types, declared_witness=declared_witness
                )
            if (
                intersection is None
                and not obj.extras.keys() <= {"title", "description"}
                and self._plain_pattern_root_annotations(obj, pattern_value_types)
            ):
                intersection = self._pattern_validation_intersection(
                    obj, pattern_value_types, declared_witness=declared_witness
                )
        if intersection == "declared" or (
            not declared_witness and not helpers_kept and self._pattern_checks_declared_key(obj, names_by_property)
        ):
            rule_type = IndependentDeclaredPatternPropertiesRule
        elif intersection == "models":
            rule_type = IndependentModelPatternPropertiesRule
        self._schema_runtime_validation(reference_path).pattern_properties.append(
            rule_type(
                declared_properties=declared_names,
                pattern_properties=tuple(pattern_value_types),
                rejected_patterns=tuple(rejected_patterns),
                additional_property_type=additional_property_type,
                allow_unmatched=allow_unmatched,
            )
        )
        if obj is self._pattern_validation_document_root:
            self._pattern_validation_document_root = None

    def _add_required_groups_validator(
        self,
        reference_path: str,
        keyword: Literal["anyOf", "oneOf", "not"],
        groups: Sequence[Sequence[str]],
        names_by_property: dict[str, tuple[str, ...]],
    ) -> None:
        """Register a required-property group runtime rule for keyword, deduplicated by value."""
        if not groups:
            return

        rule = RequiredGroupsRule(
            keyword=keyword,
            groups=self._required_group_input_names(groups, names_by_property),
        )
        runtime_validation = self._schema_runtime_validation(reference_path)
        if rule not in runtime_validation.required_groups:
            runtime_validation.required_groups.append(rule)

    def _add_property_count_validator(self, reference_path: str, obj: JsonSchemaObject) -> None:
        """Add raw-object property-count rules from this schema and its allOf sources."""
        if (property_count_rule := self._get_property_count_rule(obj)) is None:
            return
        self._schema_runtime_validation(reference_path).property_count = property_count_rule

    def _get_conditional_predicate(
        self,
        obj: JsonSchemaObject,
    ) -> tuple[tuple[str, tuple[object, ...]], ...] | None:
        """Extract supported required-property predicates without dropping other conditions."""
        if_schema = self._get_conditional_schema(obj, "if")
        if not isinstance(if_schema, JsonSchemaObject) or not if_schema.required:
            return None
        if not _is_object_only_type(if_schema.type) or not _is_empty_property_schema(
            if_schema, allowed_keywords=self.CONDITIONAL_SCHEMA_ALLOWED_FIELDS
        ):
            return None

        properties = if_schema.properties or {}
        if any(
            property_name not in if_schema.required
            and property_schema is not True
            and not (isinstance(property_schema, JsonSchemaObject) and _is_empty_property_schema(property_schema))
            for property_name, property_schema in properties.items()
        ):
            return None
        predicates: list[tuple[str, tuple[object, ...]]] = []
        for property_name in if_schema.required:
            if (values := _get_conditional_property_values(properties.get(property_name))) is None:
                return None
            predicates.append((property_name, values))
        return tuple(predicates)

    def _add_conditional_validator(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        names_by_property: dict[str, tuple[str, ...]],
    ) -> None:
        if (predicate := self._get_conditional_predicate(obj)) is None:
            return

        then_schema = self._get_conditional_schema(obj, "then")
        else_schema = self._get_conditional_schema(obj, "else")
        then_groups = (
            (tuple(then_schema.required),) if isinstance(then_schema, JsonSchemaObject) and then_schema.required else ()
        )
        else_groups = (
            (tuple(else_schema.required),) if isinstance(else_schema, JsonSchemaObject) and else_schema.required else ()
        )
        if not then_groups and not else_groups:
            return

        condition = tuple(
            (names_by_property.get(property_name, (property_name,)), expected_values)
            for property_name, expected_values in predicate
        )
        rule = ConditionalRequiredRule(
            condition=condition,
            then_groups=self._required_group_input_names(then_groups, names_by_property),
            else_groups=self._required_group_input_names(else_groups, names_by_property),
        )
        runtime_validation = self._schema_runtime_validation(reference_path)
        if rule not in runtime_validation.conditional_required:
            runtime_validation.conditional_required.append(rule)

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
        """Register every runtime validation rule this schema and its allOf sources contribute."""
        if not self.generate_schema_validators:
            return

        self._add_pattern_properties_validator(reference_path, name, obj, path, fields, base_classes)
        names_by_property = self._get_input_names_by_property(fields, base_classes)
        if (
            not self.force_optional_for_required_fields
            and not obj.custom_base_path
            and not any((self.config.base_class, self.config.base_class_map, self.config.custom_template_dir))
            and self.config.extra_template_data is None
            and (missing_required := [name for name in obj.required if name not in names_by_property])
        ):
            self._add_required_groups_validator(reference_path, "anyOf", [missing_required], names_by_property)
        self._add_required_groups_validator(
            reference_path, "oneOf", self._get_required_groups(obj.oneOf), names_by_property
        )
        self._add_required_groups_validator(
            reference_path, "anyOf", self._get_required_groups(obj.anyOf), names_by_property
        )
        self._add_required_groups_validator(
            reference_path, "not", self._get_not_required_groups(obj), names_by_property
        )
        self._add_conditional_validator(reference_path, obj, names_by_property)
        if include_property_count:
            self._add_property_count_validator(reference_path, obj)
        self._add_unique_items_validator(
            reference_path,
            obj,
            fields,
            base_classes,
            is_root_model=False,
        )
        if not obj.allOf:
            return
        # Inline rules belong to this model even when it has no other runtime rules.
        # Once it defines core rules, also merge those shadowed on referenced bases.
        for include_references in (False, True):
            runtime_validation = self.extra_template_data[reference_path].get("schema_runtime_validation")
            has_core_rules = _is_internal_schema_runtime_validation(
                runtime_validation
            ) and self._has_core_schema_runtime_rules(runtime_validation)
            if include_references:
                if not has_core_rules:
                    return
            elif has_core_rules:
                continue
            for source in self._iter_schema_validation_sources(obj, include_references=include_references):
                if source is obj:
                    continue
                self._add_required_groups_validator(
                    reference_path,
                    "oneOf",
                    self._get_required_groups(source.oneOf),
                    names_by_property,
                )
                self._add_required_groups_validator(
                    reference_path,
                    "anyOf",
                    self._get_required_groups(source.anyOf),
                    names_by_property,
                )
                self._add_required_groups_validator(
                    reference_path,
                    "not",
                    self._get_not_required_groups(source),
                    names_by_property,
                )
                self._add_conditional_validator(reference_path, source, names_by_property)

    @_builds_model
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
        if self.generate_schema_validators:
            obj = self._merge_conditional_properties(obj)
        self._preload_property_refs_for_rw_models(obj)
        inherited_fields: dict[str, DataModelFieldBase] = {}
        inherited_properties: dict[str, tuple[JsonSchemaObject | bool, str]] = {}
        inherited_required_names: frozenset[str] = frozenset()
        if base_classes:
            base_classes[:] = self._sort_inherited_base_classes_for_mro(base_classes)
            inherited_fields = self._get_inherited_field_map(base_classes)
            inherited_properties = self._get_inherited_property_map(base_classes)
            inherited_required_names = self._get_inherited_required_names(base_classes)
        python_overrides = obj.python_field_overrides
        if python_overrides:
            inherited_properties = {
                key: value for key, value in inherited_properties.items() if key not in python_overrides
            }
            inherited_required_names = inherited_required_names.difference(python_overrides)
        if obj.properties:
            deferred_property_names = self._get_deferred_inherited_property_names(
                obj,
                inherited_properties,
            )
            properties_obj = self._merge_properties_with_parent_constraints(
                obj,
                base_classes,
                inherited_properties,
                deferred_property_names,
            )
            object_fields = self.parse_object_fields(
                self._get_deferred_inherited_parse_object(properties_obj, deferred_property_names),
                path,
                get_inferred_module_name(
                    name,
                    treat_dot_as_module=self.treat_dot_as_module,
                    strict_dotted_module_names=self.strict_dotted_module_names,
                    target_python_version=self.target_python_version,
                ),
                class_name=name,
            )
            self._mark_partial_inherited_fields(object_fields, deferred_property_names, properties_obj)
            fields.extend(object_fields)
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
                        field.name = parent_field.name
                        field.alias = field_name if field.name != field_name else None
                    continue
                inherited_field = inherited_fields.get(field_name) if field_name is not None else None
                if (
                    field_name in inherited_required_names or (inherited_field is not None and inherited_field.required)
                ) and not self.force_optional_for_required_fields:
                    field.required = True
                if field_name is None or _DEFERRED_INHERITED_TYPE_KEY not in field.__dict__:
                    if inherited_field is not None:
                        self._prepare_required_inherited_field(
                            field,
                            inherited_field,
                            overriding_field=field,
                        )
                    continue
                if inherited_field is not None:
                    resolved_field = self._copy_inherited_field(
                        field,
                        inherited_field,
                        force_optional=self.force_optional_for_required_fields,
                        partial_merge_mode=self.allof_merge_mode,
                        reserved_names=reserved_names,
                    )
                    if resolved_field is None:  # pragma: no cover
                        continue
                    self._prepare_required_inherited_field(
                        resolved_field,
                        inherited_field,
                        overriding_field=field,
                    )
                    if self.model_resolver.default_value_overrides:
                        default_source = field
                        if self.allof_merge_mode == AllOfMergeMode.All and not (
                            _RAW_SCHEMA_DEFAULT_KEY in field.__dict__
                            and field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] is not _RAW_SCHEMA_DEFAULT_UNDEFINED
                        ):
                            default_source = inherited_field
                        self._apply_inherited_field_default(
                            resolved_field,
                            default_source,
                            class_name=name,
                        )
                    fields[index] = resolved_field
                    continue
                if self.model_resolver.default_value_overrides:
                    field.__dict__[_DEFERRED_INHERITED_CLASS_KEY] = name
                self._graph_builder.replace_field_type(field, self.data_type())
        original_class_name = name
        name = self._apply_title_as_name(name, obj)  # pragma: no cover
        reference = self.model_resolver.add(path, name, class_name=True, loaded=True)
        extra_field = self._get_typed_additional_properties_field(reference.name, obj, path)
        # ignore an undetected object
        if (
            ignore_duplicate_model
            and not fields
            and extra_field is None
            and len(base_classes) == 1
            and not (self.generate_schema_validators and self._has_schema_validator_constraints(obj))
        ):
            with self.model_resolver.current_base_path_context(self.model_resolver._base_path):  # noqa: SLF001
                self.model_resolver.delete(path)
                return self.data_type(reference=base_classes[0])
        if required_names := list(dict.fromkeys([*required, *obj.required])):
            field_name_to_field = {_field_source_name(field): field for field in fields}
            for required_name in required_names:
                if self.force_optional_for_required_fields:
                    continue
                if field := field_name_to_field.get(required_name):
                    field.required = True
                    if self.apply_default_values_for_required_fields and field.has_default:
                        field.use_default_with_required = True
                    continue
                field = self._build_missing_required_field(
                    required_name,
                    excludes={field.name for field in fields if field.name},
                    base_classes=base_classes,
                    class_name=name,
                    inherited_fields=inherited_fields,
                )
                fields.append(field)
                field_name_to_field[required_name] = field
        for field in fields:
            self._finalize_required_inherited_field(field)
        self._apply_final_class_field_aliases(fields, reference.name, original_class_name)
        if extra_field is not None:
            extra_field.target_python_version = self.target_python_version
            fields.insert(0, extra_field)
        self._record_schema_object(reference, obj)
        if self.generate_schema_validators:
            self._add_schema_validators(reference.path, reference.name, obj, path, fields, base_classes)

        separate_model_fields = self._get_separate_model_fields(reference, fields, base_classes)
        generates_separate = separate_model_fields is not None
        if separate_model_fields is not None:
            self._create_request_response_models(
                reference=reference,
                obj=obj,
                all_fields=separate_model_fields,
                own_fields=fields,
                data_model_type_class=self.data_model_type,
            )

        # Generate base model if needed
        if self._should_generate_base_model(generates_separate_models=generates_separate):
            data_model_type = self._create_data_model(
                reference=reference,
                fields=fields,
                base_classes=base_classes,
                custom_base_class=self._resolve_base_class(reference.name, obj.custom_base_path),
                custom_template_dir=self.custom_template_dir,
                extra_template_data=self.extra_template_data,
                path=self.current_source_path,
                description=obj.description if self.use_schema_description else None,
                nullable=nullable,
                keyword_only=self.keyword_only,
                treat_dot_as_module=self.treat_dot_as_module,
                dataclass_arguments=self.dataclass_arguments,
            )
            self._graph_builder.register_model(data_model_type)
        else:
            self._unregister_temporary_field_references(fields)

        return self.data_type(reference=reference)

    def _append_missing_required_fields(
        self,
        *,
        required_names: list[str],
        fields: list[DataModelFieldBase],
        base_classes: list[Reference],
        class_name: str,
        declared_property_names: frozenset[str],
    ) -> None:
        """Preserve the source position of required-only fields in inline allOf items."""
        if self.force_optional_for_required_fields:
            return

        existing_field_names = {field_name for field in fields if (field_name := _field_source_name(field)) is not None}
        for required_field_name in required_names:
            if required_field_name in existing_field_names or required_field_name in declared_property_names:
                continue
            field = self._build_missing_required_field(
                required_field_name,
                excludes={field.name for field in fields if field.name},
                base_classes=base_classes,
                class_name=class_name,
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
        union_models: list[Reference],
        declared_property_names: frozenset[str],
        inherited_parent_refs: Iterable[Reference] = (),
    ) -> None:
        parent_refs = self._get_allof_parent_references(
            obj,
            inherited=inherited_parent_refs,
        )
        parent_properties = self._get_inherited_property_map(parent_refs) if parent_refs else {}

        for all_of_item in obj.allOf:
            if self._is_false_schema_item(all_of_item):
                self._raise_unsatisfiable_schema(obj, path, "allOf")
            if not isinstance(all_of_item, JsonSchemaObject):  # pragma: no cover
                continue
            if all_of_item.ref:  # $ref
                ref_schema = self._load_ref_schema_object(all_of_item.ref)
                if ref_schema.is_boolean_schema_false:
                    self._raise_unsatisfiable_schema(obj, path, "allOf")

                if ref_schema.oneOf or ref_schema.anyOf:
                    self.model_resolver.add(path, name, class_name=True, loaded=True)
                    if ref_schema.anyOf:
                        union_models.extend(
                            d.reference for d in self.parse_any_of(name, ref_schema, path) if d.reference
                        )
                    if ref_schema.oneOf:
                        union_models.extend(
                            d.reference for d in self.parse_one_of(name, ref_schema, path) if d.reference
                        )
                else:
                    ref = self._add_ref(all_of_item.ref)
                    if self._dynamic_target_bindings(ref.path):
                        warn(
                            f"allOf base class {redact_reference(all_of_item.ref)!r} cannot follow the $dynamicAnchor "
                            "bindings of its dynamic scope; its $dynamicRef resolve within its own schema resources.",
                            stacklevel=2,
                        )
                    if ref.path not in {b.path for b in base_classes}:
                        base_classes.append(ref)
                        self.extra_template_data[ref.path]["is_base_class"] = True
            else:
                # Merge child properties with parent constraints before processing
                deferred_property_names = self._get_deferred_inherited_property_names(
                    all_of_item,
                    parent_properties,
                )
                merged_item = self._merge_properties_with_parent_constraints(
                    all_of_item,
                    parent_refs,
                    parent_properties,
                    deferred_property_names,
                )
                module_name = get_inferred_module_name(
                    name,
                    treat_dot_as_module=self.treat_dot_as_module,
                    strict_dotted_module_names=self.strict_dotted_module_names,
                    target_python_version=self.target_python_version,
                )
                object_fields = self.parse_object_fields(
                    self._get_deferred_inherited_parse_object(merged_item, deferred_property_names),
                    path,
                    module_name,
                    class_name=name,
                )
                self._mark_partial_inherited_fields(
                    object_fields,
                    deferred_property_names,
                    merged_item,
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
                            class_name=name,
                            declared_property_names=declared_property_names,
                        )
                self._parse_all_of_item(
                    name,
                    all_of_item,
                    path,
                    fields,
                    base_classes,
                    required,
                    union_models,
                    declared_property_names,
                    parent_refs,
                )
                if all_of_item.anyOf:
                    self.model_resolver.add(path, name, class_name=True, loaded=True)
                    union_models.extend(d.reference for d in self.parse_any_of(name, all_of_item, path) if d.reference)
                if all_of_item.oneOf:
                    self.model_resolver.add(path, name, class_name=True, loaded=True)
                    union_models.extend(d.reference for d in self.parse_one_of(name, all_of_item, path) if d.reference)

    def _build_all_of_ref_root_model(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        ref_data_type: DataType,
    ) -> DataType:
        reference = self.model_resolver.add(path, name, class_name=True, loaded=True)
        self._record_schema_object(reference, obj)
        field = self.data_model_field_type(
            name=None,
            data_type=ref_data_type,
            required=True,
            **self._data_model_field_common_kwargs(),
        )
        self._register_root_model(
            reference=reference,
            fields=[field],
            obj=obj,
            custom_base_class_name=name,
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
        data_model_root = data_model_root_type(
            reference=reference,
            fields=fields,
            custom_base_class=self._resolve_base_class(custom_base_class_name, obj.custom_base_path),
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
        (``_rw_model_writes_variant_beside_fields``) name a model with variants, as a model with fields does.
        """
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
        splits = (
            self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse
            and not self._is_base_stand_in(reference)
            and (
                self._fields_reference_rw_model_variant(fields, "Request")
                or self._rw_model_writes_variant_beside_fields(reference.path)
            )
        )
        if self.dedupe_request_response_variants:
            self._check_rw_model_split(reference, splits=splits)
        if splits:
            self._request_response_fields[reference.path] = tuple(self._copy_unregistered_fields(fields))
            if not self.dedupe_request_response_variants:
                self._rw_model_field_facts_cache[reference.path] = self._get_rw_model_field_facts(fields)
            variants: list[DataModel] = []
            for suffix in ("Request", "Response"):
                variant_fields = [self._copy_model_field(field) for field in fields]
                variant_reference = self._get_rw_model_variant_reference(reference, suffix, loaded=True)
                self._update_field_refs_for_variant(variant_fields, suffix)
                self._record_schema_object(variant_reference, obj, variant_of=reference)
                self._update_variant_additional_properties_metadata(
                    variant_reference.path,
                    obj,
                    suffix,
                )
                self._copy_schema_runtime_validation_for_variant(
                    reference.path,
                    variant_reference.path,
                    variant_fields,
                    suffix,
                    obj=obj,
                    source_fields=fields,
                    is_root_model=True,
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
                        custom_base_class_name=variant_reference.name,
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
            item_type = self.data_type(data_types=root_type.data_types).type_hint or ANY
        slice_type = root_type.type_hint or f"list[{item_type}]"
        if "[" not in slice_type:
            slice_type = f"{slice_type}[{item_type}]"
        add_sequence_interface(item_type, slice_type)

    def _get_root_model_sequence_type(self, data_type: DataType) -> DataType | None:  # noqa: PLR6301
        """Return a sequence data type for RootModel helpers.

        This is an instance method because snooper_to_methods does not preserve
        staticmethod descriptors.
        """
        if data_type.is_optional:
            return None

        root_type = data_type
        if not (root_type.is_list or root_type.is_sequence):
            if len(root_type.data_types) != 1:
                return None
            root_type = root_type.data_types[0]
            if root_type.is_optional:
                return None

        if root_type.is_list or root_type.is_sequence:
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
                hasattr(self.data_model_root_type, "add_literal_validation")
                and self._ref_sibling_keywords_enabled
                and single_obj.has_ref_with_schema_keywords
            )
        ):
            return None

        referenced_schema = get_model_by_path(self.raw_obj, single_obj.ref[2:].split("/"))
        ref_data_type = self.get_ref_data_type(single_obj.ref)
        if referenced_schema is True:
            return self._build_all_of_ref_root_model(name, obj, path, ref_data_type)
        if referenced_schema is False:
            self._raise_unsatisfiable_schema(obj, path, "allOf")

        if not (isinstance(referenced_schema, dict) and referenced_schema.get("enum")) or (
            self._ref_member_narrows_literals(single_obj, referenced_schema["enum"], obj)
        ):
            return None

        full_path = self.model_resolver.join_path(tuple(path))
        existing_ref = self.model_resolver.references.get(full_path)
        if existing_ref is not None and not existing_ref.loaded:
            return self._build_all_of_ref_root_model(name, obj, path, ref_data_type)

        return ref_data_type

    def _parse_all_of_root_value(self, name: str, merged_root: JsonSchemaObject, path: list[str]) -> DataType:
        """Keep literal membership alongside the existing scalar or container representation."""
        literal_values = merged_root.enum if not self.ignore_enum_constraints else []
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
            and not self.ignore_enum_constraints
            and isinstance(merged_root.items, JsonSchemaObject)
            and merged_root.items.enum
        ):
            item_type = self._parse_all_of_root_value(f"{name}Item", merged_root.items, get_special_path("items", path))
            merged_root = merged_root.model_copy(update={"items": True})
        if merged_root.is_array:
            root_data_type = self.parse_array(name, merged_root, path)
        else:
            root_data_type = self.parse_root_type(name, merged_root, path)
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
                    (
                        data_type
                        for data_type in root_model.fields[0].data_type.all_data_types
                        if data_type.is_list or data_type.is_sequence or data_type.is_set or data_type.is_tuple
                    ),
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
                        if nested.is_list or nested.is_sequence or nested.is_set or nested.is_tuple
                    )
                array_type.data_types = [item_type]
                self._graph_builder.note_changed_models(array_owner)
            if literal_values:
                literal_validation(root_model, literal_values)
        return root_data_type

    def _parse_all_of_merged_root(self, name: str, merged_root: JsonSchemaObject, path: list[str]) -> DataType:
        """Parse the schema an allOf merges into like the same schema written without allOf."""
        if (
            merged_root.enum
            and not self.ignore_enum_constraints
            and not self._reads_enum_as_const(merged_root)
            and not self.should_parse_enum_as_literal(merged_root, property_name=name)
        ):
            return self._parse_enum_class(name, self._definition_enum(merged_root), path)
        return (
            self.parse_array(name, merged_root, path)
            if merged_root.is_array
            else self.parse_root_type(name, merged_root, path)
        )

    @_builds_model
    def parse_all_of(  # noqa: PLR0911
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        ignore_duplicate_model: bool = False,  # noqa: FBT001, FBT002
    ) -> DataType:
        """Parse allOf schema into a single data type with combined properties."""
        if self._contains_false_schema(obj.allOf):
            self._raise_unsatisfiable_schema(obj, path, "allOf")
        if self.intersects_allof_keywords:
            self._satisfiable_all_of_value_parts(obj)

        single_ref_result = self._parse_all_of_single_ref(name, obj, path)
        if single_ref_result is not None:
            return single_ref_result

        if merged_root := self._merge_all_of_root_schema(obj):
            if self.generate_schema_validators and hasattr(self.data_model_root_type, "add_literal_validation"):
                return self._parse_all_of_root_value(name, merged_root.with_schema_location(obj.schema_location), path)
            return self._parse_all_of_merged_root(name, merged_root.with_schema_location(obj.schema_location), path)

        nullable = self._object_nullable(obj)
        merged_all_of_obj = self._merge_all_of_object(obj)
        if merged_all_of_obj:
            return self._parse_object_common_part(
                name,
                merged_all_of_obj.with_schema_location(obj.schema_location),
                path,
                ignore_duplicate_model=ignore_duplicate_model,
                fields=[],
                base_classes=[],
                required=[],
                nullable=nullable,
            )

        root_model_result = self._handle_allof_root_model_with_constraints(name, obj, path)
        if root_model_result is not None:
            return root_model_result

        fields: list[DataModelFieldBase] = []
        base_classes: list[Reference] = []
        required: list[str] = []
        union_models: list[Reference] = []
        declared_property_names: set[str] = set()
        pending_schemas = [obj]
        while pending_schemas:
            current_schema = pending_schemas.pop()
            if current_schema.properties:
                declared_property_names.update(current_schema.properties)
            pending_schemas.extend(
                item for item in current_schema.allOf if isinstance(item, JsonSchemaObject) and not item.ref
            )
        self._parse_all_of_item(
            name,
            obj,
            path,
            fields,
            base_classes,
            required,
            union_models,
            frozenset(declared_property_names),
        )
        if not (fields or required or union_models) and (
            value_root := self._merge_all_of_value_schema(obj, path, base_classes)
            or (
                self._reference_site_schema(obj)
                if ignore_duplicate_model and len(base_classes) == 1
                else self._all_of_root_value_schema(obj) or self._all_of_enum_site_schema(obj)
            )
        ):
            return self._parse_all_of_merged_root(name, value_root.with_schema_location(obj.schema_location), path)
        if not union_models:
            return self._parse_object_common_part(
                name,
                obj,
                path,
                ignore_duplicate_model=ignore_duplicate_model,
                fields=fields,
                base_classes=base_classes,
                required=required,
                nullable=nullable,
            )
        reference = self.model_resolver.add(path, name, class_name=True, loaded=True)
        self._record_schema_object(reference, obj)
        all_of_data_type = self._parse_object_common_part(
            name,
            obj,
            get_special_path("allOf", path),
            ignore_duplicate_model=ignore_duplicate_model,
            fields=fields,
            base_classes=base_classes,
            required=required,
        )
        assert all_of_data_type.reference is not None
        data_type = build_union(
            self.data_type,
            [
                self._parse_all_of_union_model(
                    name,
                    obj,
                    get_special_path(f"union_model-{index}", path),
                    (union_model, all_of_data_type.reference),
                    ignore_duplicate_model=ignore_duplicate_model,
                )
                for index, union_model in enumerate(union_models)
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
            custom_base_class_name=name,
            description=obj.description if self.use_schema_description else None,
        )
        return self.data_type(reference=reference)

    def _parse_all_of_union_model(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        bases: tuple[Reference, Reference],
        *,
        ignore_duplicate_model: bool,
    ) -> DataType:
        """Parse the class of one union member of an allOf, located at the member's branch.

        The class of a member whose Reference several schema objects reach is left without a location too.
        """
        union_model = bases[0]
        data_type = self._parse_object_common_part(
            name,
            obj.with_schema_location(location_of(union_model)),
            path,
            ignore_duplicate_model=ignore_duplicate_model,
            fields=[],
            base_classes=list(bases),
            required=[],
        )
        if union_model.__dict__.get(SCHEMA_LOCATION_CONFLICT_KEY) and (reference := data_type.reference) is not None:
            _mark_location_conflict(reference)
        return data_type

    def _get_tag_key_variant_key(self, obj: JsonSchemaObject, path: list[str]) -> str | None:
        """Return the key of the union branch `obj` modeled at `path` when its class name still comes from that key.

        A branch its title names (`--use-title-as-name`) or `--model-name-map` renames does not repeat the key.
        """
        if (location := obj.schema_location) is None or (variant := self._tag_key_variants.get(location)) is None:
            return None
        key, variant_name = variant
        named_after_key = self.model_resolver.named_after(self.model_resolver.join_path(tuple(path)), variant_name)
        return key if named_after_key else None

    def parse_object_fields(
        self,
        obj: JsonSchemaObject,
        path: list[str],
        module_name: Optional[str] = None,  # noqa: UP045
        class_name: Optional[str] = None,  # noqa: UP045
    ) -> list[DataModelFieldBase]:
        """Parse object properties into a list of data model fields."""
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
            )
            modular_name = f"{module_name}.{field_name}" if module_name else field_name

            exclude_field_names.add(field_name)

            if isinstance(field, bool):
                single_alias, validation_aliases = self._split_field_alias(alias)
                fields.append(
                    self.data_model_field_type(
                        name=field_name,
                        data_type=self.data_type_manager.get_data_type(Types.any),
                        required=False if self.force_optional_for_required_fields else original_field_name in requires,
                        alias=single_alias,
                        validation_aliases=validation_aliases,
                        serialization_alias=self.get_serialization_alias(original_field_name, field_name, class_name),
                        strip_default_none=self.strip_default_none,
                        order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
                        use_annotated=self.use_annotated,
                        use_field_description=self.use_field_description,
                        use_field_description_example=self.use_field_description_example,
                        use_inline_field_description=self.use_inline_field_description,
                        original_name=original_field_name,
                        use_serialization_alias=self.use_serialization_alias,
                        **self._data_model_field_common_kwargs(),
                    )
                )
                continue

            field = self._array_all_of_site_schema(field)  # noqa: PLW2901
            if field.has_ref_with_schema_keywords and not field.is_ref_with_nullable_only:
                field = self._merge_ref_with_schema(field)  # noqa: PLW2901
            field = self._read_false_all_of_values(field)  # noqa: PLW2901

            if original_field_name == variant_key:
                self.model_resolver.name_past_parent([*path, field_name])
            field_type = self.parse_item(modular_name, field, [*path, field_name])

            if self.force_optional_for_required_fields:
                required: bool = False
            else:
                required = original_field_name in requires
            effective_default, effective_has_default, use_default_with_required = self._effective_default_state(
                original_field_name,
                field.default,
                has_default=field.has_default,
                required=required,
                class_name=class_name,
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
                )
            )
        if self.config.aliases:
            for field in fields:
                field.__dict__[_ALIAS_RESOLUTION_CLASS_NAME_KEY] = class_name
        return fields

    def _get_typed_additional_properties_field(
        self,
        class_name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataModelFieldBase | None:
        """Build the output model's typed extra field for schema-valued extras (``_typed_extra_field_schema``)."""
        if (additional_props := self._typed_extra_field_schema(obj)) is None:
            return None
        additional_props = self._add_nullable_combined_schema_branches(additional_props)
        additional_property_name = f"{class_name}AdditionalProperty"
        extra_value_type = self._parse_additional_properties_value(
            additional_property_name,
            [*path, "additionalProperties"],
            obj,
            additional_properties=additional_props,
            constrained_name=additional_property_name,
        )
        dict_key = (
            self._parse_simple_property_name_key_type(property_names)
            if (property_names := obj.propertyNames) is not None
            else None
        )

        return self.data_model_type.create_typed_extra_field(
            field_model=self.data_model_field_type,
            data_type=self.data_type(
                data_types=[extra_value_type],
                is_dict=True,
                dict_key=dict_key,
            ),
        )

    def _typed_extra_field_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the additionalProperties schema the typed extra field of the model built from ``obj`` types.

        None where the output model has no typed extra field, additionalProperties is no schema, the patternProperties
        rules of --generate-schema-validators type it (``_pattern_rule_additional_properties``) or it is an allOf with a
        false schema. A $ref with sibling keywords is read merged with them.
        """
        if (
            self.data_model_type.TYPED_EXTRA_FIELD_NAME is None
            or not isinstance(additional_props := obj.additionalProperties, JsonSchemaObject)
            or self._has_pattern_properties_validator(obj)
        ):
            return None
        if additional_props.has_ref_with_schema_keywords and not additional_props.is_ref_with_nullable_only:
            additional_props = self._merge_ref_with_schema(additional_props)
        if additional_props.allOf and self._contains_false_schema(additional_props.allOf):
            return None
        return additional_props

    @cached_property
    def _nested_constrained_model_type(self) -> type[DataModel]:
        """Resolve the output-specific model for an inline constrained value."""
        return self._output_model_context.resolve_nested_constrained_model_type()

    def _flatten_additional_properties_all_of(self, obj: JsonSchemaObject) -> list[JsonSchemaObject] | None:
        """Resolve and flatten an additionalProperties allOf schema."""
        schemas: list[JsonSchemaObject] = []
        pending = [obj]
        while pending:
            schema = pending.pop()
            if schema.ref:
                if self._load_ref_schema_object(schema.ref).is_boolean_schema_false:
                    return None
                schema = self._merge_ref_with_schema(schema)
                if schema.ref:  # pragma: no cover
                    return None  # pragma: no cover
            if schema.title:
                schema = self._schema_object(schema.model_dump(exclude={"title"}, exclude_unset=True, by_alias=True))
            if schema.allOf:
                own_schema = self._without_allof_keywords(schema)
                if self._schema_has_own_value_keywords(own_schema):
                    pending.append(own_schema)
                for item in reversed(schema.allOf):
                    match item:
                        case False:
                            return None
                        case True:
                            continue
                        case JsonSchemaObject():  # pragma: no branch
                            pending.append(item)
                continue
            if (
                self._schema_requires_model_type(schema)
                or any((schema.anyOf, schema.oneOf, schema.enum))
                or "const" in schema.extras
                or isinstance(schema.type, list)
            ):
                return None  # pragma: no cover
            schemas.append(schema)
        return schemas

    def _merge_additional_properties_all_of(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Merge a primitive allOf value schema so every constraint is retained."""
        if not (schemas := self._flatten_additional_properties_all_of(obj)):
            return None

        schema_types = {schema.type for schema in schemas if schema.type}
        if not schema_types or not schema_types <= {"integer", "number", "string"}:  # pragma: no cover
            return None  # pragma: no cover
        if len(schema_types) > 1 and schema_types != {"integer", "number"}:  # pragma: no cover
            return None  # pragma: no cover
        if (merged_schema := self._merge_primitive_schemas_for_allof(schemas)) is None:  # pragma: no cover
            return None  # pragma: no cover

        metadata = {field: getattr(obj, field) for field in _ALL_OF_METADATA_FIELDS if field in obj.model_fields_set}
        if obj.extras:
            metadata["extras"] = obj.extras
        return merged_schema.model_copy(update=metadata) if metadata else merged_schema

    def _parse_additional_properties_value(
        self,
        name: str,
        path: list[str],
        parent: JsonSchemaObject,
        *,
        additional_properties: JsonSchemaObject,
        constrained_name: str | None = None,
    ) -> DataType:
        """Parse a mapping value schema while preserving supported Annotated constraints."""
        additional_properties = self._read_false_all_of_values(self._array_all_of_site_schema(additional_properties))
        if not self._output_model_context.supports_annotated_constraints:
            return self.parse_item(
                name,
                additional_properties,
                path,
                parent=parent
                if self.field_constraints and self.data_model_field_type.SUPPORTS_FIELD_CONSTRAINTS
                else None,
            )
        parser_type = type(self)
        parser_attributes = self.__dict__
        has_instance_hooks = (
            "parse_item" in parser_attributes
            or "parse_root_type" in parser_attributes
            or "_register_root_model" in parser_attributes
        )
        has_class_hooks = parser_type is not JsonSchemaParser and (
            parser_type.parse_item is not JsonSchemaParser.parse_item
            or parser_type.parse_root_type is not JsonSchemaParser.parse_root_type
            or parser_type._register_root_model is not JsonSchemaParser._register_root_model  # noqa: SLF001
        )
        if has_instance_hooks or has_class_hooks:
            return self.parse_item(name, additional_properties, path)
        if (
            self._should_create_type_alias_for_title(additional_properties, name)
            or additional_properties.enum
            or additional_properties.anyOf
            or additional_properties.oneOf
        ):
            return self.parse_item(name, additional_properties, path)

        preserve_root_model = bool(additional_properties.allOf)
        if preserve_root_model:
            if (value_schema := self._merge_additional_properties_all_of(additional_properties)) is None:
                return self.parse_item(name, additional_properties, path)
        else:
            value_schema = additional_properties
        if (
            not preserve_root_model
            and value_schema.has_ref_with_schema_keywords
            and not value_schema.is_ref_with_nullable_only
        ):
            value_schema = self._merge_ref_with_schema(value_schema)
            if value_schema.title:
                extras = value_schema.extras
                if not additional_properties.title:
                    extras = {key: value for key, value in extras.items() if key != "title"}
                value_schema = value_schema.model_copy(
                    update={
                        "title": None,
                        "extras": extras,
                    }
                )
        if not self._has_effective_constraints(value_schema):
            return self.parse_item(name, additional_properties, path)

        return JsonSchemaParser._parse_constrained_additional_properties_value_item(
            self,
            constrained_name or (name if preserve_root_model else f"{name}AdditionalProperty"),
            value_schema,
            path,
            parent=parent,
            data_model_root_type=(self._nested_constrained_model_type if not preserve_root_model else None),
        )

    def _parse_additional_properties_map(
        self, name: str, obj: JsonSchemaObject, path: list[str], additional_properties: JsonSchemaObject
    ) -> DataType:
        """Parse a map whose values follow the additionalProperties schema."""
        return self.data_type(
            data_types=[
                self._parse_additional_properties_value(
                    name,
                    get_special_path("additionalProperties", path),
                    obj,
                    additional_properties=additional_properties,
                )
            ],
            **(self._get_python_type_flags(obj) or {"is_dict": True}),
        )

    @property
    def _types_nullable_map_values(self) -> bool:
        """Whether this run types the values of nullable maps."""
        return self.use_typed_nullable_map_values and not self.run_context.untyped_nullable_maps

    def _typed_nullable_map_value_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the value schema of a nullable map when this run types the values of nullable maps."""
        return (
            _nullable_map_value_schema(obj, beside_other_types=self.use_typed_map_values_beside_other_types)
            if self._types_nullable_map_values
            else None
        )

    def _parse_nullable_map(
        self, name: str, obj: JsonSchemaObject, path: list[str], additional_properties: JsonSchemaObject
    ) -> DataType:
        """Parse the dict of a type array of object and null, keeping the value type of the map.

        Models created for the values give way to every other model, so the names other models had before
        typed values stay theirs. The object map builder gives the dict its null.
        """
        self.typed_nullable_maps = True
        with self.model_resolver.yielding_new_references():
            return self._parse_additional_properties_map(name, obj, path, additional_properties)

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
        if python_type_override := self._get_python_type_override(item):
            return python_type_override
        if item.enum:
            return self.parse_item(name, item, path, parent=parent)
        if self.use_title_as_name and item.title:
            name = sanitize_module_name(item.title, treat_dot_as_module=self.treat_dot_as_module)
        if self._should_create_type_alias_for_title(item, name):
            return JsonSchemaParser._parse_root_type_with_context(
                self,
                name,
                item,
                path,
                data_model_root_type=data_model_root_type,
                preserve_constraints=True,
            )

        root_type_path = get_special_path("array", path)
        return JsonSchemaParser._parse_root_type_with_context(
            self,
            self.model_resolver.add(root_type_path, name, class_name=True).name,
            item,
            root_type_path,
            data_model_root_type=data_model_root_type,
            preserve_constraints=True,
        )

    def _get_additional_properties_root_field(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataModelFieldBase:
        """Build the dict root field of an object without declared fields, without leaking value constraints.

        additionalProperties types the values. Where the patternProperties validator checks the map, the values are
        ``Any``; under ``--check-pattern-maps-strictly`` its keys are read from propertyNames as a pattern map reads
        them and the field reads the keywords that count the keys of the object, patternProperties included, so a key
        that a pattern admits counts. Elsewhere the keys are ``str``: an object with propertyNames and without
        properties reaches this field only where the validator checks its patterns. The object map builder's null
        reaches the dict under ``--use-single-dict-for-object-maps``. Without the options the keys are ``str``, the
        counts leave the patterns out and the dict has no null, as in 0.86.0.
        """
        value_type = self.data_type_manager.get_data_type(Types.any)
        key_type = None
        counts: dict[str, Any] = {
            "minProperties": obj.minProperties,
            "maxProperties": obj.maxProperties,
            "propertyNames": obj.propertyNames,
            "additionalProperties": obj.additionalProperties,
        }
        if self._has_pattern_properties_validator(obj):
            if self.check_pattern_maps_strictly:
                key_type = self._pattern_map_key_type(obj.propertyNames, None)
                counts["patternProperties"] = obj.patternProperties
        else:
            match obj.additionalProperties:
                case JsonSchemaObject() as additional_properties:
                    value_type = self._parse_additional_properties_value(
                        name,
                        [*path, "additionalProperties"],
                        obj,
                        additional_properties=additional_properties,
                    )
        map_type = self.data_type(data_types=[value_type], is_dict=True, dict_key=key_type)
        return self.get_object_field(
            field_name=None,
            field=self._schema_object(counts),
            required=True,
            original_field_name=None,
            field_type=(
                _with_null(map_type)
                if self.use_single_dict_for_object_maps and self._object_map_admits_null(obj)
                else map_type
            ),
            alias=None,
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
                    updated_items.append(self._schema_object({"type": "null"}))

            if len(updated_items) != len(combined_items):
                updates[field_name] = updated_items

        return obj.model_copy(update=updates) if updates else obj

    @_builds_model
    def parse_object(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        unique: bool = True,  # noqa: FBT001, FBT002
    ) -> DataType:
        """Parse object schema into a data model."""
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
        class_name = reference.name
        self.set_title(reference.path, obj)
        self.set_schema_id(reference.path, obj)
        self._preload_property_refs_for_rw_models(obj)
        fields = self.parse_object_fields(
            obj,
            path,
            get_inferred_module_name(
                class_name,
                treat_dot_as_module=self.treat_dot_as_module,
                strict_dotted_module_names=self.strict_dotted_module_names,
                target_python_version=self.target_python_version,
            ),
            class_name=class_name,
        )
        has_declared_fields = bool(fields)
        if has_declared_fields and (extra_field := self._get_typed_additional_properties_field(class_name, obj, path)):
            extra_field.target_python_version = self.target_python_version
            fields.insert(0, extra_field)
        should_parse_dict_root = not has_declared_fields and (
            isinstance(obj.additionalProperties, JsonSchemaObject) or self._should_parse_empty_object_as_dict(obj)
        )
        if not should_parse_dict_root:
            data_model_type_class = self.data_model_type
        else:
            fields.append(self._get_additional_properties_root_field(name, obj, path))
            data_model_type_class = self.data_model_root_type

        self._record_schema_object(reference, obj)
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

        separate_model_fields = self._get_separate_model_fields(reference, fields, None)
        generates_separate = separate_model_fields is not None
        if separate_model_fields is not None:
            self._create_request_response_models(
                reference=reference,
                obj=obj,
                all_fields=separate_model_fields,
                own_fields=fields,
                data_model_type_class=data_model_type_class,
            )

        # Generate base model if needed
        if self._should_generate_base_model(generates_separate_models=generates_separate):
            data_model_type = self._create_data_model(
                model_type=data_model_type_class,
                reference=reference,
                fields=fields,
                custom_base_class=self._resolve_base_class(class_name, obj.custom_base_path),
                custom_template_dir=self.custom_template_dir,
                extra_template_data=self.extra_template_data,
                path=self.current_source_path,
                description=obj.description if self.use_schema_description else None,
                nullable=self._object_nullable(obj),
                keyword_only=self.keyword_only,
                treat_dot_as_module=self.treat_dot_as_module,
                dataclass_arguments=self.dataclass_arguments,
            )
            self._graph_builder.register_model(data_model_type)
            if merged_copy:
                self._merged_copies.add(data_model_type)
        else:
            self._unregister_temporary_field_references(fields)

        return self.data_type(reference=reference)

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
        Without ``--use-single-dict-for-object-maps`` the map is a union of dicts, one per value type, whose keys match
        its patterns; an output that cannot decode a union of several dicts, such as msgspec, gets the one dict.
        """
        if self.use_single_dict_for_object_maps:
            return self._parse_pattern_map(name, pattern_properties, path, property_names, None)
        pattern_value_pairs: list[tuple[str, DataType]] = []
        for i, (pattern, schema) in enumerate(pattern_properties.items()):
            if schema is False:
                continue

            if schema is True:
                value_type = self.data_type_manager.get_data_type(Types.any)
            else:
                value_type = self.parse_item(
                    name,
                    schema,
                    get_special_path(f"patternProperties/{i}", path),
                )
            pattern_value_pairs.append((pattern, value_type))

        if not pattern_value_pairs:
            return self.data_type(
                data_types=[self.data_type_manager.get_data_type(Types.any)],
                is_dict=True,
            )

        groups: dict[str, tuple[list[str], DataType]] = {}
        for pattern, value_type in pattern_value_pairs:
            key = value_type.type_hint
            if key not in groups:
                groups[key] = ([], value_type)
            groups[key][0].append(pattern)

        if len(groups) > 1 and not self._output_model_context.decodes_value_unions:
            return self._pattern_map_type(
                [pattern for pattern, _ in pattern_value_pairs],
                [value_type for _, value_type in pattern_value_pairs],
                property_names,
                joins_patterns=True,
            )

        data_types: list[DataType] = []
        for patterns, value_type in groups.values():
            merged_pattern = patterns[0] if len(patterns) == 1 else "|".join(patterns)
            data_types.append(
                self.data_type(
                    data_types=[value_type],
                    is_dict=True,
                    dict_key=self._pattern_map_key_type(property_names, merged_pattern),
                )
            )

        return self.data_type(data_types=data_types)

    def _parse_pattern_map(
        self,
        name: str,
        pattern_properties: dict[str, JsonSchemaObject | bool],
        path: list[str],
        property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
        other_keys: JsonSchemaObject | None,
    ) -> DataType:
        """Build the dict of ``parse_pattern_properties``, admitting the keys that ``other_keys`` types.

        Where ``other_keys`` types the keys that match no pattern, the dict's keys are those of ``property_names`` and
        its value joins the value of ``other_keys`` last.
        """
        patterns: list[str] = []
        value_types: list[DataType] = []
        for i, (pattern, schema) in enumerate(pattern_properties.items()):
            if schema is False:
                continue
            patterns.append(pattern)
            value_types.append(
                self.data_type_manager.get_data_type(Types.any)
                if schema is True
                else self.parse_item(name, schema, get_special_path(f"patternProperties/{i}", path))
            )
        if other_keys is not None:
            value_types.append(
                self._parse_additional_properties_value(
                    name,
                    get_special_path("additionalProperties", path),
                    other_keys,
                    additional_properties=other_keys,
                )
            )

        return self._pattern_map_type(patterns, value_types, property_names, joins_patterns=other_keys is None)

    def _pattern_map_type(
        self,
        patterns: list[str],
        value_types: list[DataType],
        property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
        *,
        joins_patterns: bool,
    ) -> DataType:
        """Return the one dict of a pattern map whose value is the union of ``value_types``.

        Its keys match one of ``patterns`` where ``joins_patterns``, else those of ``property_names``. It is also the
        map of 0.86.0 patternProperties whose union of dicts the output cannot decode.
        """
        if not value_types:
            return self.data_type(
                data_types=[self.data_type_manager.get_data_type(Types.any)],
                is_dict=True,
            )

        if (
            any_type := next((value_type for value_type in value_types if _is_plain_any(value_type)), None)
        ) is not None:
            value_types = [any_type]
        return self.data_type(
            data_types=value_types,
            is_dict=True,
            dict_key=self._pattern_map_key_type(property_names, "|".join(patterns) if joins_patterns else None),
        )

    def _other_keys_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Return the additionalProperties schema that types the keys no pattern of the pattern map of ``obj`` matches.

        Only the builder of ``--use-single-dict-for-object-maps`` types them, only with a schema that asserts
        something, and only where the output decodes a union of any value types (msgspec keeps its earlier type until
        its union fallbacks are decided); otherwise the map admits the keys of its patterns only.
        """
        if (
            self.use_single_dict_for_object_maps
            and isinstance(additional_properties := obj.additionalProperties, JsonSchemaObject)
            and self._output_model_context.decodes_value_unions
            and _schema_assertions(additional_properties)
        ):
            return additional_properties
        return None

    def _pattern_map_key_type(
        self,
        property_names: JsonSchemaObject | bool | None,  # ruff: ignore[boolean-type-hint-positional-argument]
        pattern: str | None,
    ) -> DataType | None:
        """Return the key type of a pattern map: propertyNames read as a string schema, its ``$ref`` resolved.

        ``pattern`` joins the key when the map admits only the keys of its patterns. ``None`` means ``str`` keys.
        """
        if not isinstance(property_names, JsonSchemaObject):
            return (
                None
                if pattern is None
                else self.data_type_manager.get_data_type(
                    Types.string,
                    pattern=pattern,
                    use_annotated=self.use_annotated,
                    target_pydantic_version=self.target_pydantic_version,
                )
            )
        merged_property_names = property_names.model_copy(deep=True)
        merged_property_names.type = "string"
        if pattern is not None:
            merged_property_names.pattern = (
                pattern
                if merged_property_names.pattern is None
                else self._conjoined_pattern(
                    (merged_property_names.pattern, pattern),
                    str(property_names.schema_location or "propertyNames"),
                )
            )
        if merged_property_names.ref:
            merged_property_names = self._merge_ref_with_schema(merged_property_names)
        return self._parse_property_name_key_schema(merged_property_names)

    def _parse_object_map(  # noqa: PLR0911
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

        Without ``--use-single-dict-for-object-maps`` the map is built as 0.86.0 built it: patternProperties give a
        union of dicts, one per value type (``parse_pattern_properties``), else propertyNames and additionalProperties
        one dict; the null and the other types of its type array are left to the field. The values typed under
        ``--use-typed-nullable-map-values`` make one dict, optional where its type array lists null, and the union
        with the other types of its type array.
        """
        if not self.use_single_dict_for_object_maps:
            if pattern_properties := obj.patternProperties:
                return self.parse_pattern_properties(
                    name, pattern_properties, object_path, property_names=obj.propertyNames
                )
            if obj.propertyNames is not None:
                return self.parse_property_names(
                    name, obj.propertyNames, obj.additionalProperties, object_path, parent_obj=obj
                )
            if typed_values is not None:
                map_type = self._parse_nullable_map(name, obj, object_path, typed_values)
                if (union := self._object_map_type_union(name, obj, path, map_type)) is not None:
                    return union
                return self.data_type(data_types=[map_type], is_optional=obj.type_has_null)
            if isinstance(obj.additionalProperties, JsonSchemaObject):
                return self._parse_additional_properties_map(name, obj, object_path, obj.additionalProperties)
            return self.data_type_manager.get_data_type(Types.object)
        if pattern_properties := obj.patternProperties:
            self._warn_inexact_object_map(name, obj, pattern_properties)
            map_type = (
                self.parse_pattern_properties(name, pattern_properties, object_path, property_names=obj.propertyNames)
                if (other_keys := self._other_keys_schema(obj)) is None
                else self._parse_pattern_map(name, pattern_properties, object_path, obj.propertyNames, other_keys)
            )
        elif obj.propertyNames is not None:
            map_type = self.parse_property_names(
                name, obj.propertyNames, obj.additionalProperties, object_path, parent_obj=obj
            )
        elif typed_values is not None:
            map_type = self._parse_nullable_map(name, obj, object_path, typed_values)
        elif isinstance(obj.additionalProperties, JsonSchemaObject):
            map_type = self._parse_additional_properties_map(name, obj, object_path, obj.additionalProperties)
        else:
            map_type = self.data_type_manager.get_data_type(Types.object)
        if (union := self._object_map_type_union(name, obj, path, map_type)) is not None:
            return union
        return _with_null(map_type) if self._object_map_admits_null(obj) else map_type

    def _object_map_type_union(
        self, name: str, obj: JsonSchemaObject, path: list[str], map_type: DataType
    ) -> DataType | None:
        """Return the union of the dict of an object map with the other types of its type array, or None."""
        if (type_list := _object_map_other_types(obj)) is None:
            return None
        return build_union(
            self.data_type,
            [map_type, *self._iter_non_object_union_types(name, obj, type_list, path, keep_constraints=True)],
            nullable=self._object_map_admits_null(obj),
        )

    def _object_map_admits_null(self, obj: JsonSchemaObject) -> bool:
        """Return whether an object map admits null: its type array lists object and null, or it is nullable.

        ``nullable: true`` counts under ``--strict-nullable``, the rule ``_get_data_type`` applies to other types,
        unless a combined schema copied it into its branch: the union of the branches admits that null.
        """
        return bool(self._null_facts(obj, "object-map").admits_null)

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
        where = str(location) if (location := obj.schema_location) is not None else name
        if not reasons or where in self._inexact_object_maps:
            return
        self._inexact_object_maps.add(where)
        warn(
            f"{where}: one dict type cannot express this object, since it {' and '.join(reasons)}; the generated "
            "dict accepts or rejects some objects that the schema does not. Pydantic output with "
            "--generate-schema-validators checks it exactly.",
            stacklevel=2,
        )

    def _parse_property_name_key_schema(  # noqa: PLR0911, PLR0912
        self,
        property_names: JsonSchemaObject | bool,  # noqa: FBT001
    ) -> DataType:
        if isinstance(property_names, bool):
            return self.data_type_manager.get_data_type(Types.string)
        if (python_type := self._get_x_python_type(property_names)) is not None:
            from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
                python_type_expr_base_name,
            )

            match python_type_expr_base_name(python_type):
                case "int":
                    return self.data_type_manager.get_data_type(Types.integer)
                case "bool":
                    return self.data_type_manager.get_data_type(Types.boolean)
                case "str":
                    return self.data_type_manager.get_data_type(Types.string)
        if property_names.ref:
            return self.get_ref_data_type(property_names.ref)
        if property_names.enum:
            string_enums = [value for value in self._const_narrowed_enum(property_names).enum if isinstance(value, str)]
            if string_enums:
                return self.data_type(literals=string_enums)
            return self.data_type_manager.get_data_type(Types.string)
        if isinstance(property_names.extras.get("const"), str):
            return self.data_type(literals=[property_names.extras["const"]])
        if (
            property_names.pattern is not None
            or property_names.minLength is not None
            or property_names.maxLength is not None
        ):
            kwargs: dict[str, Any] = {}
            if property_names.pattern:
                kwargs["pattern"] = property_names.pattern
            if property_names.minLength is not None:
                kwargs["minLength"] = property_names.minLength
            if property_names.maxLength is not None:
                kwargs["maxLength"] = property_names.maxLength
            return self.data_type_manager.get_data_type(
                Types.string,
                use_annotated=self.use_annotated,
                target_pydantic_version=self.target_pydantic_version,
                **kwargs,
            )
        return self.data_type_manager.get_data_type(Types.string)

    def _parse_string_property_name_union(
        self, schema: JsonSchemaObject, *, discard_nonstring: bool = False
    ) -> tuple[DataType | None, bool]:
        """Build constrained string keys, retaining unions already dominated by a plain string branch."""
        if (
            schema.model_fields_set - self.STRING_PROPERTY_NAME_FIELDS
            or type(self)._property_names_forbids_all_keys(schema)  # noqa: SLF001
            or (
                # Newly enforced patterns must work without changing the regex engine.
                # Keep complex patterns on their existing path, including Pydantic 2.0.
                discard_nonstring and schema.pattern and literal_pattern_value(schema.pattern.removeprefix("^")) is None
            )
        ):
            return None, False
        if not schema.anyOf:
            return self._parse_property_name_key_schema(schema), not schema.has_constraint
        if schema.has_constraint:
            return None, False
        data_types: list[DataType] = []
        branch_count = 0
        for branch in schema.anyOf:
            if branch is False or (
                discard_nonstring
                and isinstance(branch, JsonSchemaObject)
                and not branch.model_fields_set - self.STRING_PROPERTY_NAME_FIELDS
                and type(self)._property_names_forbids_all_keys(branch)  # noqa: SLF001
            ):
                continue
            data_type, unrestricted = (
                (self.data_type_manager.get_data_type(Types.string), True)
                if branch is True
                else self._parse_string_property_name_union(branch, discard_nonstring=discard_nonstring)
            )
            if unrestricted and (not self.field_constraints or branch_count == 0 or len(data_types) != branch_count):
                return None, True
            branch_count += 1
            if data_type is not None:
                data_types.append(data_type)
        if not data_types or len(data_types) != branch_count:
            return None, False
        return build_union(self.data_type, data_types), False

    def _parse_property_names_key_type(
        self,
        name: str,
        property_names: JsonSchemaObject | bool,  # noqa: FBT001
        path: list[str],
    ) -> DataType:
        """Build a propertyNames key type, parsing compound schemas only when needed."""
        if (
            isinstance(property_names, JsonSchemaObject)
            and property_names.has_ref_with_schema_keywords
            and not property_names.is_ref_with_nullable_only
        ):
            property_names = self._merge_ref_with_schema(property_names)

        match property_names:
            case JsonSchemaObject() if property_names.anyOf or property_names.oneOf or property_names.allOf:
                if property_names.anyOf:
                    key_type, _ = self._parse_string_property_name_union(property_names)
                    if key_type is not None:
                        return key_type
                key_type = self.parse_item(name, property_names, get_special_path("propertyNames/key", path))
                if property_names.anyOf and not any(item.reference for item in key_type.all_data_types):
                    string_key_type, _ = self._parse_string_property_name_union(property_names, discard_nonstring=True)
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
                    merged, schemas, intersect=self.allof_merge_mode != AllOfMergeMode.NoMerge
                )
                key_type.unregister_reference()
                return self._parse_property_name_key_schema(self._schema_object(merged))
        return self._parse_property_name_key_schema(property_names)

    def _parse_simple_property_name_key_type(
        self,
        property_names: JsonSchemaObject | bool | None,  # noqa: FBT001
    ) -> DataType | None:
        """Parse a direct propertyNames schema without resolving references or combinators."""
        match property_names:
            case JsonSchemaObject(ref=None, anyOf=[], oneOf=[], allOf=[]) as property_names if (
                not type(self)._property_names_forbids_all_keys(property_names)  # noqa: SLF001
                and (
                    (
                        bool(x_python_type := property_names.extras.get("x-python-type"))
                        and (isinstance(x_python_type, str) or self._get_x_python_type(property_names) is not None)
                    )
                    or property_names.pattern is not None
                    or property_names.minLength is not None
                    or property_names.maxLength is not None
                    or any(isinstance(value, str) for value in property_names.enum)
                    or isinstance(property_names.extras.get("const"), str)
                )
            ):
                return self._parse_property_name_key_schema(property_names)
        return None

    def parse_property_names(
        self,
        name: str,
        property_names: JsonSchemaObject | bool,  # noqa: FBT001
        additional_properties: JsonSchemaObject | bool | None,  # noqa: FBT001
        path: list[str],
        parent_obj: JsonSchemaObject | None = None,
    ) -> DataType:
        """Parse propertyNames into a dict data type with constrained keys.

        Args:
            name: Name for the data type
            property_names: Schema constraining property names
            additional_properties: Schema for values (or bool/None)
            path: Current path in schema
            parent_obj: Parent schema object for x-python-type lookup

        Returns:
            DataType representing dict with constrained keys
        """
        # Determine value type from additionalProperties
        if isinstance(additional_properties, JsonSchemaObject):
            value_type = self._parse_additional_properties_value(
                name,
                get_special_path("propertyNames/value", path),
                parent_obj or additional_properties,
                additional_properties=additional_properties,
            )
        else:
            value_type = self.data_type_manager.get_data_type(Types.any)

        key_type = self._parse_property_names_key_type(name, property_names, path)

        dict_flags: dict[str, bool] = {"is_dict": True}
        if parent_obj:  # pragma: no branch
            python_type_flags = self._get_python_type_flags(parent_obj)
            if python_type_flags:  # pragma: no cover
                dict_flags = python_type_flags

        return self.data_type(
            data_types=[value_type],
            **dict_flags,
            dict_key=key_type,
        )

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
            const_enum_data = self._extract_const_enum_from_combined(combined_items, item)
            if const_enum_data is None:
                return True
            synthetic_obj = self._create_synthetic_enum_obj(item, *const_enum_data)
            if self.should_parse_enum_as_literal(synthetic_obj, property_name=name, property_obj=item):
                return True
        if (
            item.is_object
            and not item.properties
            and not item.patternProperties
            and item.propertyNames is None
            and isinstance(item.additionalProperties, JsonSchemaObject)
        ):
            return True
        if item.patternProperties:
            return True
        if item.propertyNames is not None:
            return True
        if (
            item.enum
            and not self.ignore_enum_constraints
            and self.should_parse_enum_as_literal(item, property_name=name)
        ):
            return True
        is_primitive = (
            item.type
            and not item.is_array
            and not item.is_object
            and not item.anyOf
            and not item.oneOf
            and not item.allOf
            and not item.ref
            and not (item.enum and not self.ignore_enum_constraints and not self._reads_enum_as_const(item))
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
    ) -> DataType | None:
        """Return the Enum or Literal of a oneOf or anyOf of consts, else None.

        The parent's field writes its constraints beside the values when ``writes``.
        """
        const_enum_data = self._extract_const_enum_from_combined(combined_items, obj, writes=writes)
        if const_enum_data is None:
            return None

        synthetic_obj = self._create_synthetic_enum_obj(obj, *const_enum_data)
        if self.should_parse_enum_as_literal(synthetic_obj, property_name=name, property_obj=obj):
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

        The name and the $ref sibling keywords are resolved here and the row of the field table (``_FIELD_TABLE``)
        is built in this frame, so a nested schema costs no more frames than the parse methods it reaches.
        """
        with nullcontext() if (scope := self._subschema_scope(item)) is None else scope:
            item = self._read_false_all_of_values(self._array_all_of_site_schema(item))
            python_type_override = self._get_python_type_override(item)
            if python_type_override:
                return python_type_override
            if self.use_title_as_name and item.title:
                name = sanitize_module_name(item.title, treat_dot_as_module=self.treat_dot_as_module)
                singular_name = False
            if self._should_create_type_alias_for_title(item, name):
                return self.parse_root_type(name, item, path)
            merged_scope = None
            if (
                item.has_ref_with_schema_keywords
                and not item.is_ref_with_nullable_only
                and (merged_item := self._merge_ref_with_schema(item)) is not item
            ):
                item, merged_scope = self._read_false_all_of_values(merged_item), self._subschema_scope(merged_item)
            with nullcontext() if merged_scope is None else merged_scope:
                choice = self._FIELD_TABLE.choose(
                    self, SchemaSite(name, item, path, singular_name=singular_name, parent=parent)
                )
                return choice.build(self, choice.site)

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
            and not self._const_literal_enforces_constraints(item)
            and (
                self._has_materialized_container_constraints(item)
                or (item.has_constraint and (parent.has_constraint or self.field_constraints))
            )
        )

    def _parse_constrained_item(self, site: _Site) -> DataType:
        root_type_path = get_special_path("array", site.path)
        return self._parse_root_type_with_context(
            self.model_resolver.add(
                root_type_path,
                site.name,
                class_name=True,
                singular_name=site.singular_name,
            ).name,
            site.obj,
            root_type_path,
            preserve_constraints=self._has_materialized_container_constraints(site.obj),
        )

    def _parse_ref_nullable_only_item(self, site: _Site) -> DataType:
        item = site.obj
        ref_data_type = self.get_ref_data_type(cast("str", item.ref))
        if self._null_facts(item, "ref-nullable-only").admits_null:
            return build_union(self.data_type, [ref_data_type], nullable=True)
        return ref_data_type

    def _parse_custom_type_item(self, site: _Site) -> DataType:
        return self.data_type_manager.get_data_type_from_full_path(
            self._get_emitted_python_type_path(
                _validate_schema_python_import_path(cast("str", site.obj.custom_type_path), "customTypePath")
            ),
            is_custom_type=True,
        )

    def _parse_combined_item_const_enum(self, site: _Site, items: list[JsonSchemaObject | bool]) -> DataType | None:
        return self._parse_combined_const_enum(
            site.name,
            site.obj,
            items,
            get_special_path("enum", site.path),
            singular_name=site.singular_name,
        )

    def _parse_all_of_field_item(self, site: _Site) -> DataType:
        item, name, path, singular_name = site.obj, site.name, site.path, site.singular_name
        if self._contains_false_schema(item.allOf):
            self._raise_unsatisfiable_schema(item, get_special_path("allOf", path), "allOf")
        all_of_items = [sub_item for sub_item in item.allOf if isinstance(sub_item, JsonSchemaObject)]
        if len(all_of_items) == 1 and len(all_of_items) != len(item.allOf) and not item.properties:
            return self.parse_item(name, all_of_items[0], path, singular_name=singular_name, parent=site.parent)
        if not self._schema_requires_model_type(item) and not self._allof_requires_model_type(item.allOf):
            all_of_path = get_special_path("allOf", path)
            all_of_path = [self.model_resolver.resolve_ref(all_of_path)]
            root_model_name = self.model_resolver.add(
                all_of_path, name, singular_name=singular_name, class_name=True
            ).name
            return self.parse_root_type(root_model_name, item, all_of_path)
        if len(item.allOf) == 1 and not item.properties:
            single_item = item.allOf[0]
            if isinstance(single_item, JsonSchemaObject) and single_item.ref:
                if (reference_site := self._reference_site_schema(item)) is None:
                    return self.get_ref_data_type(single_item.ref)
                all_of_path = [self.model_resolver.resolve_ref(get_special_path("allOf", path))]
                with self.model_resolver.yielding_new_references():
                    site_type = self._parse_all_of_merged_root(
                        self.model_resolver.add(all_of_path, name, singular_name=singular_name, class_name=True).name,
                        reference_site.with_schema_location(item.schema_location),
                        all_of_path,
                    )
                self._site_model_reference_ids.add(id(site_type.reference))
                return site_type
        all_of_path = get_special_path("allOf", path)
        all_of_path = [self.model_resolver.resolve_ref(all_of_path)]
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
        return self._join_object_type_array(
            site, self.parse_object(name, item, get_special_path("object", path), singular_name=site.singular_name)
        )

    def _parse_enum_item(self, site: _Site) -> DataType:
        item, name = site.obj, site.name
        if self._reads_enum_as_const(item):
            return self._get_const_data_type(self._exact_const_enum_value(item)[0], item)
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
        return self._get_dynamic_scope_data_type(site.obj, site.path)

    def _field_ref_nullable_only_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.is_ref_with_nullable_only and site.obj.ref)

    def _field_ref_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.ref)

    def _build_field_ref(self, site: _Site) -> DataType:
        return self.get_ref_data_type(cast("str", site.obj.ref))

    def _field_custom_type_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.custom_type_path)

    def _field_constrained_type_union_applies(self, site: _Site) -> bool:
        return not self._has_shared_numeric_union_constraints(site.obj) and self._localizes_constrained_type_union(
            site.obj
        )

    def _build_field_constrained_type_union(self, site: _Site) -> DataType:
        return self._build_constrained_type_union(site.name, site.obj, site.path)

    def _field_array_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return site.obj.is_array

    def _build_field_array(self, site: _Site) -> DataType:
        return self.parse_array_fields(site.name, site.obj, get_special_path("array", site.path)).data_type

    def _field_discriminated_union_item_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(
            site.obj.discriminator and site.parent and site.parent.is_array and (site.obj.oneOf or site.obj.anyOf)
        )

    def _build_field_discriminated_union_item(self, site: _Site) -> DataType:
        return self.parse_root_type(site.name, site.obj, site.path)

    def _field_schema_validator_object_applies(self, site: _Site) -> bool:
        return bool(self.generate_schema_validators and self._should_parse_object_with_schema_validators(site.obj))

    def _build_field_schema_validator_object(self, site: _Site) -> DataType:
        return self._join_object_type_array(
            site,
            self.parse_object(
                site.name, site.obj, get_special_path("object", site.path), singular_name=site.singular_name
            ),
        )

    def _field_any_of_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.anyOf)

    def _build_field_any_of(self, site: _Site) -> DataType:
        if combined_const_enum := self._parse_combined_item_const_enum(site, site.obj.anyOf):
            return combined_const_enum
        return build_union(self.data_type, self.parse_any_of(site.name, site.obj, get_special_path("anyOf", site.path)))

    def _field_one_of_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.oneOf)

    def _build_field_one_of(self, site: _Site) -> DataType:
        if combined_const_enum := self._parse_combined_item_const_enum(site, site.obj.oneOf):
            return combined_const_enum
        return build_union(self.data_type, self.parse_one_of(site.name, site.obj, get_special_path("oneOf", site.path)))

    def _field_all_of_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.allOf)

    def _field_object_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.is_object or site.obj.patternProperties or site.obj.propertyNames is not None)

    def _field_enum_applies(self, site: _Site) -> bool:
        return bool(site.obj.enum and not self.ignore_enum_constraints)

    def _field_shared_numeric_constraints_applies(self, site: _Site) -> bool:
        return self._has_shared_numeric_union_constraints(site.obj)

    def _build_field_shared_numeric_constraints(self, site: _Site) -> DataType:
        return self._get_data_type(site.obj, localize_constraints=False)

    def _field_typed_nullable_map_applies(self, site: _Site) -> bool:
        return self._typed_nullable_map_value_schema(site.obj) is not None

    def _build_field_data_type(self, site: _Site) -> DataType:
        return self.get_data_type(site.obj)

    _FIELD_TABLE: ClassVar[DispatchTable[JsonSchemaParser, JsonSchemaObject, DataType]] = DispatchTable(
        (
            Row("constrained-item", _types_item_by_root_model, _parse_constrained_item),
            Row("dynamic-ref", _field_dynamic_ref_applies, _build_field_dynamic_ref),
            Row("ref-nullable-only", _field_ref_nullable_only_applies, _parse_ref_nullable_only_item),
            Row("ref", _field_ref_applies, _build_field_ref),
            Row("custom-type", _field_custom_type_applies, _parse_custom_type_item),
            Row(
                "constrained-type-union",
                _field_constrained_type_union_applies,
                _build_field_constrained_type_union,
            ),
            Row("array", _field_array_applies, _build_field_array),
            Row(
                "discriminated-union-item",
                _field_discriminated_union_item_applies,
                _build_field_discriminated_union_item,
            ),
            Row(
                "schema-validator-object",
                _field_schema_validator_object_applies,
                _build_field_schema_validator_object,
            ),
            Row("any-of", _field_any_of_applies, _build_field_any_of),
            Row("one-of", _field_one_of_applies, _build_field_one_of),
            Row("all-of", _field_all_of_applies, _parse_all_of_field_item),
            Row("object", _field_object_applies, _parse_object_item),
            Row("enum", _field_enum_applies, _parse_enum_item),
            Row(
                "shared-numeric-constraints",
                _field_shared_numeric_constraints_applies,
                _build_field_shared_numeric_constraints,
            ),
            Row("typed-nullable-map", _field_typed_nullable_map_applies, _parse_typed_nullable_map_item),
        ),
        Default("data-type", _build_field_data_type),
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

    def _parse_constrained_type_union(self, name: str, obj: JsonSchemaObject, path: list[str]) -> DataType | None:
        """Localize union branches that need annotated aliases instead of constrained scalar types."""
        if not self._localizes_constrained_type_union(obj):
            return None
        return self._build_constrained_type_union(name, obj, path)

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
            self._get_inherited_constraint_fields(obj) or obj.propertyNames is not None
        ):
            return False
        if self._output_model_context.writes_constrained_types:
            return "array" in obj.type or "object" in obj.type
        return self._output_model_context.supports_annotated_constraints

    def _build_constrained_type_union(self, name: str, obj: JsonSchemaObject, path: list[str]) -> DataType:
        """Build the union of a type list whose branches take annotated aliases."""
        data_types = []
        for type_ in cast("list[str]", obj.type):
            if type_ == "null":
                continue
            if type_ == "object" and (obj.propertyNames is not None or obj.patternProperties):
                branch_schema = self._get_array_union_branch_schema(obj, type_)
                branch_path = get_special_path("type-union-object", path)
                if obj.patternProperties:
                    data_type = self.parse_item(f"{name}Object", branch_schema, branch_path)
                else:
                    data_type = self._parse_root_type_with_context(
                        f"{name}Object",
                        branch_schema,
                        branch_path,
                        data_model_root_type=self._nested_constrained_model_type,
                        preserve_constraints=True,
                        use_annotated=True,
                    )
            else:
                data_type = self._parse_array_union_constrained_branch(
                    name, obj, path, type_, self._parse_typed_union_map(name, obj, path, type_)
                )
            data_types.append(data_type)
        return build_union(self.data_type, data_types, nullable=bool(self._null_facts(obj, "type-union").admits_null))

    def _get_array_union_non_array_types(  # noqa: PLR6301
        self,
        obj: JsonSchemaObject,
    ) -> tuple[str, ...]:
        """Return the non-array branches of a heterogeneous array type union."""
        match obj.type:
            case list() as type_list if "array" in type_list:
                return tuple(type_ for type_ in type_list if type_ not in {"array", "null"})
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
        if not non_array_types or (obj.enum and not self.ignore_enum_constraints):
            return 0

        count = len(data_types)
        for type_ in non_array_types:
            if type_ == "object" and obj.is_object:
                continue
            typed_map = self._parse_typed_union_map(name, obj, path, type_)
            data_types.append(
                self._parse_array_union_constrained_branch(name, obj, path, type_, typed_map)
                if localize_constraints
                else typed_map or self._get_format_data_type(type_, obj.format or "default")
            )
        return len(data_types) - count

    def _parse_typed_union_map(self, name: str, obj: JsonSchemaObject, path: list[str], type_: str) -> DataType | None:
        """Return the map type of the object member of a type array whose map values this run types, else None.

        patternProperties or propertyNames make the member the object map of the object branch.
        """
        if type_ != "object" or (typed_values := self._typed_nullable_map_value_schema(obj)) is None:
            return None
        object_path = get_special_path("object", path)
        if obj.patternProperties or obj.propertyNames is not None:
            return self._parse_object_map(
                name, self._get_array_union_branch_schema(obj, "object"), path, object_path, typed_values=typed_values
            )
        return self._parse_nullable_map(name, obj, object_path, typed_values)

    def _should_localize_array_union_constraints(
        self,
        obj: JsonSchemaObject,
        non_array_types: tuple[str, ...] | None = None,
    ) -> bool:
        """Return whether this output can retain heterogeneous union constraints per branch."""
        if (
            not self._output_model_context.supports_internal_annotated_constraints
            or (obj.enum and not self.ignore_enum_constraints)
            or not (non_array_types if non_array_types is not None else self._get_array_union_non_array_types(obj))
        ):
            return False
        return bool(self._get_inherited_constraint_fields(obj) or self._get_array_items_constraints(obj))

    def _get_array_union_branch_schema(self, obj: JsonSchemaObject, type_: str) -> JsonSchemaObject:
        """Build a shallow schema retaining only keywords valid for one union branch."""
        schema = obj.model_dump(exclude_unset=True, by_alias=True)
        distributed_fields = self._get_inherited_distributed_fields(obj)
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
            return fallback_data_type or self._get_format_data_type(type_, obj.format or "default")

        branch_path = get_special_path(f"array-union-{type_}", path)
        branch_name = self.model_resolver.add(
            branch_path,
            f"{name}{type_.title()}",
            class_name=True,
        ).name
        return JsonSchemaParser._parse_root_type_with_context(
            self,
            branch_name,
            branch_schema,
            branch_path,
            data_model_root_type=self._nested_constrained_model_type,
            preserve_constraints=True,
            use_annotated=True,
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
            not obj.extras.keys() <= {"$schema", "$defs", "definitions", "title"}
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

        required, nullable = self._resolve_array_field_required_nullable(obj)
        items, is_tuple, suppress_item_constraints, tuple_item_count = self._get_array_item_schemas(obj)

        if items:
            item_data_types = self.parse_list_item(
                name,
                items,
                path,
                obj,
                singular_name=singular_name,
            )
        elif not (is_tuple and tuple_item_count == 0):
            item_data_types = self._fallback_array_item_data_types()
        else:
            item_data_types = []

        container_flags: dict[str, bool] = {}
        if not is_tuple:
            container_flags = self._get_python_type_flags(obj) or {"is_list": True}

        non_array_types = self._get_array_union_non_array_types(obj)
        localize_constraints = self._should_localize_array_union_constraints(obj, non_array_types)
        array_data_type = self.data_type(
            data_types=item_data_types,
            is_tuple=is_tuple,
            tuple_item_count=tuple_item_count,
            **container_flags,
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
        if obj.enum and not self.ignore_enum_constraints:
            data_types.append(
                self._get_const_data_type(self._exact_const_enum_value(obj)[0], obj)
                if self._reads_enum_as_const(obj)
                else self.parse_enum(name, obj, get_special_path("enum", path))
            )
        constraints = self._get_array_constraints(obj, array_data_type)
        if non_array_types:
            constraints = {}
        if suppress_item_constraints:
            self._suppress_array_length_constraints(constraints, obj)
        return self.data_model_field_type(
            data_type=build_union(
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
            strip_default_none=self.strip_default_none,
            order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
            extras=self.get_field_extras(obj),
            use_annotated=use_annotated,
            use_serialize_as_any=self.use_serialize_as_any,
            use_field_description=self.use_field_description,
            use_field_description_example=self.use_field_description_example,
            use_inline_field_description=self.use_inline_field_description,
            original_name=None,
            has_default=obj.has_default,
            **self._data_model_field_common_kwargs(),
        )

    @_builds_model
    def parse_array(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        original_name: str | None = None,
    ) -> DataType:
        """Parse array schema into a root model with array type."""
        name = self._apply_title_as_name(name, obj)
        reference = self.model_resolver.add(path, name, loaded=True, class_name=True)
        self._record_schema_object(reference, obj)
        field = self.parse_array_fields(original_name or name, obj, [*path, name])

        non_array_types = self._get_array_union_non_array_types(obj)
        direct_data_types = field.data_type.data_types
        if (
            not self._should_localize_array_union_constraints(obj, non_array_types)
            and direct_data_types
            and any(
                _keyed_answer(
                    "array self reference",
                    data_type.reference is reference,
                    lambda data_type=data_type: data_type.reference == reference,
                )
                for data_type in direct_data_types[0].all_data_types
                if data_type.reference
            )
            and (len(direct_data_types) > 1 or not non_array_types)
        ):
            # self-reference
            recursive_tuple_item_count = direct_data_types[0].tuple_item_count
            field = self.data_model_field_type(
                data_type=build_union(
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
                strip_default_none=field.strip_default_none,
                order_constructor_fields_by_written_defaults=field.order_constructor_fields_by_written_defaults,
                extras=field.extras,
                use_annotated=self.use_annotated,
                use_field_description=self.use_field_description,
                use_field_description_example=self.use_field_description_example,
                use_inline_field_description=self.use_inline_field_description,
                original_name=None,
                has_default=field.has_default,
                **self._data_model_field_common_kwargs(),
            )

        self._register_root_model(
            reference=reference,
            fields=[field],
            obj=obj,
            custom_base_class_name=name,
            description=obj.description if self.use_schema_description else None,
        )
        return self.data_type(reference=reference)

    @_builds_model
    def parse_root_type(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> DataType:
        """Parse a root-level type into a root model."""
        return JsonSchemaParser._parse_root_type_with_context(self, name, obj, path)

    @contextmanager
    def _temporarily_enable_field_constraints(self) -> Generator[None, None, None]:
        """Render an internal annotated alias with Field constraints without bypassing parser hooks."""
        if self.field_constraints:
            yield
            return

        previous_field_constraints = self.field_constraints
        self.field_constraints = True
        try:
            yield
        finally:
            self.field_constraints = previous_field_constraints

    def _parse_root_type_with_context(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        *,
        data_model_root_type: type[DataModel] | None = None,
        preserve_constraints: bool = False,
        use_annotated: bool | None = None,
    ) -> DataType:
        """Parse a root type with an internal output representation override."""
        reference: Reference | None = None
        array_constraints: Any = None
        constraint_obj = obj
        effective_use_annotated = self.use_annotated if use_annotated is None else use_annotated
        writes = (self.field_constraints or preserve_constraints) and not (
            self._should_skip_root_field_constraints_for_multiple_types(obj)
        )
        if obj.ref:
            data_type: DataType = self.get_ref_data_type(obj.ref)
        elif obj.custom_type_path:
            data_type = self.data_type_manager.get_data_type_from_full_path(
                self._get_emitted_python_type_path(
                    _validate_schema_python_import_path(obj.custom_type_path, "customTypePath")
                ),
                is_custom_type=True,
            )  # pragma: no cover
        elif (union_type := self._parse_constrained_type_union(name, obj, path)) is not None:
            data_type = union_type
        elif obj.is_array:
            array_field = self.parse_array_fields(
                name,
                obj,
                get_special_path("array", path),
                use_annotated=use_annotated,
            )
            data_type = array_field.data_type  # pragma: no cover
            if preserve_constraints:
                array_constraints = array_field.constraints
        elif obj.anyOf or obj.oneOf:
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
        elif obj.allOf:
            value_obj = obj
            if (inline_value := self._all_of_value_schema(obj)) is not None:
                value_obj = inline_value
                if not (inline_value.enum or "const" in inline_value.extras):
                    constraint_obj = inline_value
            elif (self.field_constraints or preserve_constraints) and (
                referenced_value := self._all_of_reference_value_schema(obj)
            ) is not None:
                value_obj = constraint_obj = referenced_value
            data_type = (
                self._enum_literal(value_obj, self._literal_written(constraint_obj, value_obj.enum, writes=writes))
                if value_obj.enum and "const" not in value_obj.extras
                else self._build_lightweight_type(value_obj)
            )
            if data_type is None:  # pragma: no cover
                data_type = self.data_type_manager.get_data_type(Types.any)
        elif (
            obj.patternProperties
            or obj.propertyNames is not None
            or (obj.is_object and not obj.properties and isinstance(obj.additionalProperties, JsonSchemaObject))
        ):
            data_type = self._parse_object_map(name, obj, path, path)
        elif obj.enum and not self.ignore_enum_constraints and self._reads_enum_as_const(obj):
            data_type = self._get_const_data_type(self._exact_const_enum_value(obj)[0], obj)
        elif obj.enum and not self.ignore_enum_constraints:
            if self.should_parse_enum_as_literal(obj, property_name=name):
                narrowed = self._const_narrowed_enum(obj)
                data_type = self._enum_literal(narrowed, self._literal_written(narrowed, narrowed.enum, writes=writes))
            else:  # pragma: no cover
                data_type = self.parse_enum(name, obj, path)
        elif (typed_values := self._typed_nullable_map_value_schema(obj)) is not None:
            reference = self.model_resolver.add(
                path, self._apply_title_as_name(name, obj), loaded=True, class_name=True
            )
            self.typed_nullable_map_reference_ids.add(id(reference))
            data_type = self._parse_object_map(name, obj, path, path, typed_values=typed_values)
        elif obj.type:
            if preserve_constraints and effective_use_annotated:
                with self._temporarily_enable_field_constraints():
                    data_type = self.get_data_type(obj)
            else:
                data_type = self.get_data_type(obj)
        else:
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
        constraints = array_constraints or {}
        untaken: dict[str, Any] = {}
        if not constraints and (self.field_constraints or preserve_constraints):
            constraints, untaken = self._split_constraint_values(constraint_obj, data_type)
        if self._should_skip_root_field_constraints_for_multiple_types(constraint_obj):
            constraints = {}
        elif self.field_constraints and constraint_obj.format == "hostname":
            constraints["pattern"] = self.data_type_manager.HOSTNAME_REGEX
        if data_type.is_dict or data_type.is_mapping:
            constraints.update(self._get_property_count_constraints(constraint_obj))
        fields = [
            self.data_model_field_type(
                data_type=data_type,
                default=default_value,
                required=required,
                constraints=constraints,
                nullable=nullable,
                strip_default_none=self.strip_default_none,
                order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
                extras=self.get_field_extras(obj),
                use_annotated=effective_use_annotated,
                use_field_description=self.use_field_description,
                use_field_description_example=self.use_field_description_example,
                use_inline_field_description=self.use_inline_field_description,
                original_name=None,
                has_default=has_default_override,
                **self._data_model_field_common_kwargs(),
            )
        ]
        takes_constraints = self.field_constraints and not self._should_skip_root_field_constraints_for_multiple_types(
            constraint_obj
        )
        if takes_constraints:
            self._mark_type_dependent_constraints(fields[0], constraint_obj)
        dropped_counts = self._dropped_property_counts(constraint_obj, data_type)
        self._mark_untaken_constraints(
            fields[0],
            {**(untaken if takes_constraints else {}), **dropped_counts},
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
        root_default = default_value if has_default_override else UNDEFINED
        if data_model_root_type is not None:
            JsonSchemaParser._register_root_model_as(
                self,
                selected_root_type,
                reference=reference,
                fields=fields,
                obj=obj,
                custom_base_class_name=name,
                default=root_default,
            )
            return self.data_type(reference=reference)

        self._register_root_model(
            reference=reference,
            fields=fields,
            obj=obj,
            custom_base_class_name=name,
            default=root_default,
        )
        return self.data_type(reference=reference)

    def _should_skip_root_field_constraints_for_multiple_types(self, obj: JsonSchemaObject) -> bool:
        """Avoid applying type-specific Field constraints to heterogeneous root unions."""
        if not self.field_constraints or not isinstance(obj.type, list) or not obj.has_multiple_types:
            return False
        return len({type_ for type_ in obj.type if type_ != "null"}) > 1

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
            and self._get_inherited_constraint_fields(obj)
        )
        for type_ in type_list:
            if type_ not in {"object", "null"}:
                if localize_constraints:
                    yield self._parse_array_union_constrained_branch(name, obj, path, type_)
                elif keep_constraints:
                    yield self._get_constrained_data_type(obj, type_, field_constraints=self.field_constraints)
                else:
                    yield self._get_format_data_type(type_, obj.format or "default")

    def _parse_object_model(self, name: str, obj: JsonSchemaObject, path: list[str]) -> DataType:
        """Parse an object into its model; a type array with other types makes a root model of their union."""
        if self._joins_other_types(obj) and isinstance(obj.type, list):
            return self._parse_multiple_types_with_properties(name, obj, obj.type, path)
        return self.parse_object(name, obj, path)

    def _joins_other_types(self, obj: JsonSchemaObject) -> bool:
        """Return whether the model of an object joins the other types of its type array in a union.

        An object without properties, built as a model for its schema validators, joins them only under
        ``--check-pattern-maps-strictly``, as an object map does.
        """
        return obj.has_multiple_types and (bool(obj.properties) or self.check_pattern_maps_strictly)

    def _parse_root_type_site(self, site: _Site) -> DataType:
        """Parse the schema of a site into a root model."""
        return self.parse_root_type(site.name, site.obj, site.path)

    def _object_kind_object_model_applies(self, site: _Site) -> bool:
        return bool(site.obj.properties or self._should_parse_object_with_schema_validators(site.obj))

    def _build_object_kind_object_model(self, site: _Site) -> DataType:
        return self._parse_object_model(site.name, site.obj, site.path)

    def _object_kind_object_map_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(
            site.obj.patternProperties or (site.obj.propertyNames is not None and site.obj.propertyNames is not False)
        )

    def _object_kind_object_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return site.obj.type == "object"

    def _build_object_kind_object(self, site: _Site) -> DataType:
        return self.parse_object(site.name, site.obj, site.path)

    def _object_kind_closed_property_names_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return site.obj.propertyNames is False

    _OBJECT_KIND_ROWS: ClassVar[tuple[Row[JsonSchemaParser, JsonSchemaObject, DataType], ...]] = (
        Row("object-model", _object_kind_object_model_applies, _build_object_kind_object_model),
        Row("object-map", _object_kind_object_map_applies, _parse_root_type_site),
        Row("object", _object_kind_object_applies, _build_object_kind_object),
        Row("closed-property-names", _object_kind_closed_property_names_applies, _parse_root_type_site),
    )
    """The rows of an object-kind schema, as a definition is parsed."""

    def _parse_object_kind_schema(self, name: str, obj: JsonSchemaObject, path: list[str]) -> DataType | None:
        """Parse an object-kind schema into its model as a definition is parsed; ``None`` for other schemas."""
        site = SchemaSite(name, obj, path)
        return next((row.build(self, site) for row in self._OBJECT_KIND_ROWS if row.applies(self, site)), None)

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

        constraints = self._get_constraint_values(obj) if self.field_constraints else {}
        if self._should_skip_root_field_constraints_for_multiple_types(obj):
            constraints = {}
        self._register_root_model(
            reference=reference,
            fields=[
                self.data_model_field_type(
                    data_type=build_union(self.data_type, data_types),
                    default=obj.default,
                    required=bool(null_facts.required),
                    constraints=constraints,
                    nullable=null_facts.admits_null,
                    strip_default_none=self.strip_default_none,
                    order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
                    extras=self.get_field_extras(obj),
                    use_annotated=self.use_annotated,
                    use_field_description=self.use_field_description,
                    use_field_description_example=self.use_field_description_example,
                    use_inline_field_description=self.use_inline_field_description,
                    original_name=None,
                    has_default=obj.has_default,
                    **self._data_model_field_common_kwargs(),
                )
            ],
            obj=obj,
            custom_base_class_name=name,
            default=obj.default if obj.has_default else UNDEFINED,
        )
        return self.data_type(reference=reference)

    def _enum_positions_outside_const(self, obj: JsonSchemaObject) -> set[int]:
        """Return the positions of the enum values that the const beside them excludes, when it admits one of them.

        Draft-04 has no const keyword. A const equal to none of the values admits no value: the schema is read as
        written, and a warning says so once per schema location unless the intersection warning already reports it.
        Without --read-const-beside-enum a const beside an enum excludes no value.
        """
        if not self.read_const_beside_enum or "const" not in obj.extras or not self.schema_features.const_support:
            return set()
        const = obj.extras["const"]
        excluded = {position for position, value in enumerate(obj.enum) if not _json_literal_values_equal(const, value)}
        if len(excluded) < len(obj.enum):
            return excluded
        if _REPORTED_VALUE_CONFLICT_KEY in obj.__dict__:
            return set()
        if (location := redact_reference(str(obj.schema_location))) not in self._unsatisfiable_const_locations:
            self._unsatisfiable_const_locations.add(location)
            warn(
                f"The schema at {location} admits no value: its const {const!r} is none of its enum values. It is "
                "generated as written.",
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
                location=redact_reference(str(obj.schema_location)),
                as_member=self._output_model_context.literal_holds_enum_members
                and self._enum_class_has_value_base(obj),
            ),
        ))

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
            and (exact[0] is None or obj.nullable is not True)
        )

    def _exact_const_enum_value(self, obj: JsonSchemaObject) -> tuple[Any, ...]:
        """Return the value an enum's const narrows it to, as a one-tuple, or an empty one when it narrows to none.

        The const when its type admits that value only (``_const_type_is_exact``), else the enum value it equals that
        does, such as ``1`` for a const ``1.0``. A const that equals no enum value narrows
        nothing. The schema must hold a const.
        """
        const = obj.extras["const"]
        equal = [value for value in obj.enum if _json_literal_values_equal(const, value)]
        return next(((value,) for value in (const, *equal) if equal and self._const_type_is_exact(value)), ())

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
        """Return the Literal type of enum values, joined with the types of values no Literal holds."""
        literal_values, non_literal_values, value_null = self._partition_enum_values(values)
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
        return build_union(self.data_type, data_types, nullable=has_null)

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
        if extra_template_data.get(reference.path) or extra_template_data.get(reference.name):
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

    @_builds_model
    def parse_enum(  # noqa: PLR0912, PLR0914, PLR0915
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        unique: bool = True,  # noqa: FBT001, FBT002
    ) -> DataType:
        """Parse enum schema into an Enum class."""
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
        msgspec_null_alias = self.use_msgspec_nullable_enum_alias and not self.data_model_type.SUPPORTS_NULL_ENUM_MEMBER
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
                self.data_model_field_type(
                    name=field_name,
                    default=default,
                    data_type=self.data_type_manager.get_data_type(
                        Types.any,
                    ),
                    required=True,
                    strip_default_none=self.strip_default_none,
                    order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
                    has_default=obj.has_default,
                    use_field_description=self.use_field_description,
                    use_field_description_example=self.use_field_description_example,
                    use_inline_field_description=self.use_inline_field_description,
                    extras=field_extras,
                    original_name=None,
                    **self._data_model_field_common_kwargs(),
                )
            )

        if not enum_fields:
            if not nullable:
                return self.data_type_manager.get_data_type(Types.null)
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
            self._record_schema_object(reference, obj)
            self._register_root_model(
                reference=reference,
                fields=[
                    self.data_model_field_type(
                        data_type=self.data_type_manager.get_data_type(Types.null),
                        default=obj.default,
                        required=False,
                        nullable=True,
                        strip_default_none=self.strip_default_none,
                        order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
                        extras=self.get_field_extras(obj),
                        use_annotated=self.use_annotated,
                        has_default=obj.has_default,
                        use_field_description=self.use_field_description,
                        use_field_description_example=self.use_field_description_example,
                        use_inline_field_description=self.use_inline_field_description,
                        original_name=None,
                        **self._data_model_field_common_kwargs(),
                    )
                ],
                obj=obj,
                custom_base_class_name=name,
                default=obj.default if obj.has_default else UNDEFINED,
            )
            return self.data_type(reference=reference)

        def create_enum(reference_: Reference) -> DataType:
            """Build the enum model; the ``use_subclass_enum`` check is kept for parser subclasses (see above)."""
            enum_cls, type_ = self._get_enum_model_class(self._get_enum_type(obj), enum_times)
            self._record_schema_object(reference_, obj)

            enum = enum_cls(
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

        self._note_const_narrowing(reference, written_obj)
        if not nullable:
            return create_enum(reference)

        self._record_schema_object(reference, obj)

        enum_reference = self.model_resolver.add(
            [*path, "Enum"],
            f"{reference.name}Enum",
            class_name=True,
            singular_name=singular_name,
            singular_name_suffix="Enum",
            loaded=True,
            model_type="enum",
        )
        if message := _shifted_enum_entries_message(enum_reference.name, obj, named_values):
            warn(message, stacklevel=2)

        self._register_root_model(
            reference=reference,
            fields=[
                self.data_model_field_type(
                    data_type=create_enum(enum_reference),
                    default=obj.default,
                    required=False,
                    nullable=True,
                    strip_default_none=self.strip_default_none,
                    order_constructor_fields_by_written_defaults=self.order_constructor_fields_by_written_defaults,
                    extras=self.get_field_extras(obj),
                    use_annotated=self.use_annotated,
                    has_default=obj.has_default,
                    use_field_description=self.use_field_description,
                    use_field_description_example=self.use_field_description_example,
                    use_inline_field_description=self.use_inline_field_description,
                    original_name=None,
                    **self._data_model_field_common_kwargs(),
                )
            ],
            obj=obj,
            custom_base_class_name=reference.name,
            default=obj.default if obj.has_default else UNDEFINED,
        )
        return self.data_type(reference=reference)

    def _get_ref_body(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Get the body of a reference from URL or remote file."""
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
            return self._prepare_schema_resources(self._get_ref_body_from_url(resolved_ref), [resolved_ref])
        return self._prepare_schema_resources(self._get_ref_body_from_remote(resolved_ref), [resolved_ref])

    def _lenient_ref(self, ref: str) -> str:
        """Return a reference of the current document mapped as its lenient resolution maps it, if it has one.

        The model resolver registers anchors only while parsing JSON Schema documents, so plain-name references
        of any document with implicit schema resources, like OpenAPI, resolve through the resource registry. A
        reference with an edge maps so in its own document, as part of its key (``_compute_ref_key``).
        """
        documents = self._implicit_documents if _is_plain_name_fragment(ref) else self._lenient_documents
        if (
            not documents
            or isinstance(ref, ResolvedRef)
            or (document := self._schema_resource_document(list(self.model_resolver.current_root))) not in documents
        ):
            return ref
        return keep_resolved_ref(
            ref, self._resolve_lenient_resource_ref(ref, self._schema_resource_root_bases[document], document)
        )

    def _normalize_external_ref(self, ref: str) -> str:
        """Resolve an external anchor before falling back to legacy shorthand pointers."""
        if get_ref_type(ref := self._lenient_ref(ref)) == JSONReference.LOCAL:
            return ref

        resolved_ref = self.model_resolver.resolve_ref(ref)
        if get_ref_type(resolved_ref) == JSONReference.LOCAL:
            return ref

        relative_path, separator, object_path = resolved_ref.partition("#")
        if not separator or not object_path or object_path.startswith("/"):
            return ref

        ref_body = self._get_ref_body(relative_path)
        if (anchor_pointer := _find_json_schema_anchor_pointer(ref_body, object_path)) is not None:
            return keep_resolved_ref(ref, f"{relative_path}#{'' if anchor_pointer == '/' else anchor_pointer}")
        return keep_resolved_ref(ref, f"{relative_path}#/{object_path}")

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

    def _read_document(
        self, document: dict[str, YamlValue], root: dict[str, YamlValue] | None = None
    ) -> dict[str, YamlValue]:
        """Return a document as read, with each single-value enum as const under --use-single-value-enum-as-const.

        A document parsed where const is no keyword is kept as written: under the draft-04 schema features the
        parser already uses, or else those of its root document. The document as written is kept beside a rewritten
        one for the targets that serve it.
        """
        if not self.use_single_value_enum_as_const:
            return document
        features = self.__dict__.get("schema_features") or self._schema_features_for(root or document)
        if not features.const_support:
            return document
        read = cast("dict[str, YamlValue]", _single_value_enum_as_const(document, self._named_entry_maps))
        if read is not document:
            self._documents_as_written[id(read)] = read, document
        return read

    def _document_as_written(self, document: dict[str, YamlValue]) -> dict[str, YamlValue]:
        """Return a document as written, before --use-single-value-enum-as-const read its single-value enums."""
        entry = self._documents_as_written.get(id(document))
        return entry[1] if entry is not None and entry[0] is document else document

    def _load_ref_data_from_path(self, path: Path) -> dict[str, YamlValue]:
        """Load one referenced path and contextualize only its decode failures."""
        try:
            return self._read_document(load_data_from_path(path, self.encoding), self.raw_obj)
        except (json.JSONDecodeError, TypeError, *get_yaml_parse_errors()) as exc:
            raise InvalidFileFormatError(exc, self._input_file_type, source=path) from exc

    def _load_ref_data_from_text(self, text: str, source: str) -> dict[str, YamlValue]:
        """Decode one referenced text body and contextualize only that operation."""
        try:
            return self._read_document(load_data(text), self.raw_obj)
        except (json.JSONDecodeError, TypeError, *get_yaml_parse_errors()) as exc:
            raise InvalidFileFormatError(exc, self._input_file_type, source=source) from exc

    def _load_ref_data_from_local_http_file(self, path: Path, ref: str) -> dict[str, YamlValue]:
        """Decode a local HTTP mirror while locking the same raw bytes as HTTP."""
        if self._remote_response_observer is None:
            return self._load_ref_data_from_path(path)
        data = path.read_bytes()
        self._remote_response_observer(ref, self.http_headers, self.http_query_parameters, data)
        try:
            result = _load_parser_source_data_from_path_bytes(path, data, self.encoding)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, *get_yaml_parse_errors()) as exc:
            raise InvalidFileFormatError(exc, self._input_file_type, source=path) from exc
        if isinstance(result, dict):
            return self._read_document(result, self.raw_obj)
        msg = f"Expected dict, got {type(result).__name__}"
        raise InvalidFileFormatError(TypeError(msg), self._input_file_type, source=path)

    def _get_ref_body_from_local_http_path(self, ref: str) -> dict[str, YamlValue]:
        assert self.http_local_ref_path is not None
        parsed = urlparse(ref)
        if parsed.scheme not in {"http", "https"}:  # pragma: no cover
            msg = f"Unsupported local HTTP $ref URL: {redact_url(ref)}"
            raise Error(msg)

        parts = [unquote(part) for part in parsed.path.split("/") if part]
        host = parsed.netloc.rpartition("@")[2]
        if not host or any(part in {".", ".."} or "/" in part or "\\" in part for part in parts):
            msg = f"Unsupported local HTTP $ref URL path: {redact_url(ref)}"
            raise Error(msg)

        base_path = self.http_local_ref_path.resolve()
        relative_path = Path(host, *parts)
        file_paths = [(base_path / relative_path).resolve()]
        if not parts or not Path(parts[-1]).suffix:
            file_paths.append((base_path / relative_path.with_name(f"{relative_path.name}.json")).resolve())

        if any(not file_path.is_relative_to(base_path) for file_path in file_paths):
            msg = f"Unsupported local HTTP $ref URL path: {redact_url(ref)}"
            raise Error(msg)

        for file_path in file_paths:
            if file_path.is_file():
                cache_key = f"{file_path}\x00{ref}" if self._remote_response_observer is not None else str(file_path)
                return self.remote_object_cache.get_or_put(
                    # Several remote identities may intentionally share one
                    # checked-in mirror. Include the logical request only when
                    # each identity must reach the lock observer.
                    cache_key,
                    default_factory=lambda _, file_path=file_path: self._load_ref_data_from_local_http_file(
                        file_path, ref
                    ),
                )

        msg = f"$ref local file not found for {redact_url(ref)}: tried {', '.join(str(path) for path in file_paths)}"
        raise Error(msg)

    def _get_ref_body_from_url(self, ref: str) -> dict[str, YamlValue]:
        """Get reference body from a URL (HTTP, HTTPS, or file scheme)."""
        if ref.startswith("file://"):
            from urllib.request import url2pathname  # noqa: PLC0415

            parsed = urlparse(ref)
            # url2pathname handles percent-decoding and Windows drive letters
            path = url2pathname(parsed.path)
            # Handle UNC paths (file://server/share/path)
            if parsed.netloc:
                path = f"//{parsed.netloc}{path}"
            file_path = self._resolve_local_ref_path(Path(path), ref)
            try:
                return self.remote_object_cache.get_or_put(
                    str(file_path),
                    default_factory=lambda _: self._load_ref_data_from_path(file_path),
                )
            except (IsADirectoryError, PermissionError) as exc:
                if not _is_directory_read_error(exc, file_path):
                    raise
                msg = f"$ref path is a directory: {ref}"
                raise Error(msg) from None
        if self.http_local_ref_path is not None and urlparse(ref).scheme in {"http", "https"}:
            return self._get_ref_body_from_local_http_path(ref)
        return self.remote_object_cache.get_or_put(
            ref,
            default_factory=lambda key: self._load_ref_data_from_text(self._get_text_from_url(key), key),
        )

    def _get_ref_body_from_remote(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Get reference body from a remote file path."""
        full_path = self._resolve_local_ref_path(self.base_path / resolved_ref, resolved_ref)

        try:
            return self.remote_object_cache.get_or_put(
                str(full_path),
                default_factory=lambda _: self._load_ref_data_from_path(full_path),
            )
        except FileNotFoundError:
            msg = f"$ref file not found: {full_path}"
            raise Error(msg) from None
        except (IsADirectoryError, PermissionError) as exc:
            if not _is_directory_read_error(exc, full_path):
                raise
            msg = f"$ref path is a directory: {resolved_ref}"
            raise Error(msg) from None

    def resolve_ref(self, object_ref: str) -> Reference:
        """Resolve a reference by loading and parsing the referenced schema."""
        _validate_external_ref(object_ref)

        key = self._ref_key(object_ref)
        # If the ref is mapped to an external package, mark as loaded and skip parsing
        if key.mapping is not None:
            reference = self._add_ref(object_ref)
            reference.loaded = True
            return reference

        self._check_ref_key("resolve_ref", object_ref, key, embedded=True)
        ref = key.key
        if get_ref_type(ref) == JSONReference.LOCAL or (
            get_ref_type(key.spelling) == JSONReference.LOCAL and not self._outside_home(object_ref)
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
                object_paths, reference_paths = _split_json_pointer(ref_body, object_path)
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
        if (scope := self._subschema_scope(obj)) is None:
            self._walk_schema_objects(obj, path, callback)
            return
        with scope:
            self._walk_schema_objects(obj, path, callback)

    def _walk_schema_objects(  # noqa: PLR0912
        self,
        obj: JsonSchemaObject,
        path: list[str],
        callback: Callable[[JsonSchemaObject, list[str]], None],
    ) -> None:
        """Apply callback to a schema object and traverse its child schemas."""
        callback(obj, path)
        if (
            type(self) is JsonSchemaParser
            and type(obj) is JsonSchemaObject
            and obj.model_fields_set.isdisjoint(_SCHEMA_OBJECT_CHILD_FIELDS)
            and (not self.generate_schema_validators or obj.extras.keys().isdisjoint(_CONDITIONAL_SCHEMA_KEYWORDS))
        ):
            return
        match obj.items:
            case JsonSchemaObject() as item:
                self._traverse_schema_objects(item, [*path, "items"], callback)
            case list() as items:
                for index, item in enumerate(items):
                    if isinstance(item, JsonSchemaObject):
                        self._traverse_schema_objects(
                            item,
                            [*path, "items", str(index)],
                            callback,
                        )
        if obj.prefixItems:
            for index, item in enumerate(obj.prefixItems):
                if isinstance(item, JsonSchemaObject):
                    self._traverse_schema_objects(
                        item,
                        [*path, "prefixItems", str(index)],
                        callback,
                    )
        if isinstance(obj.additionalProperties, JsonSchemaObject):
            self._traverse_schema_objects(
                obj.additionalProperties,
                [*path, "additionalProperties"],
                callback,
            )
        if isinstance(obj.unevaluatedProperties, JsonSchemaObject):
            self._traverse_schema_objects(
                obj.unevaluatedProperties,
                [*path, "unevaluatedProperties"],
                callback,
            )
        if isinstance(obj.unevaluatedItems, JsonSchemaObject):
            self._traverse_schema_objects(
                obj.unevaluatedItems,
                [*path, "unevaluatedItems"],
                callback,
            )
        if obj.patternProperties:
            for key, value in obj.patternProperties.items():
                if isinstance(value, JsonSchemaObject):
                    self._traverse_schema_objects(
                        value,
                        [*path, "patternProperties", key],
                        callback,
                    )
        if isinstance(obj.propertyNames, JsonSchemaObject):
            self._traverse_schema_objects(
                obj.propertyNames,
                [*path, "propertyNames"],
                callback,
            )
        for index, item in enumerate(obj.anyOf):
            if isinstance(item, JsonSchemaObject):
                self._traverse_schema_objects(
                    item,
                    [*path, "anyOf", str(index)],
                    callback,
                )
        for index, item in enumerate(obj.allOf):
            if isinstance(item, JsonSchemaObject):
                self._traverse_schema_objects(
                    item,
                    [*path, "allOf", str(index)],
                    callback,
                )
        for index, item in enumerate(obj.oneOf):
            if isinstance(item, JsonSchemaObject):
                self._traverse_schema_objects(item, [*path, "oneOf", str(index)], callback)
        if self.generate_schema_validators:
            for keyword in ("if", "then", "else"):
                item = self._get_conditional_schema(obj, keyword)
                if isinstance(item, JsonSchemaObject):
                    self._traverse_schema_objects(
                        item,
                        [*path, keyword],
                        callback,
                    )
        if obj.properties:
            for key, value in obj.properties.items():
                if isinstance(value, JsonSchemaObject):
                    self._traverse_schema_objects(
                        value,
                        [*path, "properties", key],
                        callback,
                    )

    def _resolve_ref_callback(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Resolve $ref in schema object, and the targets of $dynamicRef and $recursiveRef as their data types do."""
        if obj.ref:
            self.resolve_ref(obj.ref)
            return
        if not obj.recursiveRef and (dynamic_ref := obj.dynamicRef) and not _is_plain_name_fragment(dynamic_ref):
            self.resolve_ref(dynamic_ref)
        elif (target := self._dynamic_scope_target(obj, path)) is not None and (
            obj.recursiveRef or target.location is not None
        ):
            with self._dynamic_target_ref(target) as ref:
                self.resolve_ref(ref)

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
        self._schema_resource_registry = registry = graph.registry
        self._schema_resource_locations: dict[str, str] = registry.locations
        self._schema_resource_document_aliases: dict[str, str] = {}
        self._schema_resource_root_bases: dict[str, str] = registry.root_bases
        self._schema_resource_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self._has_embedded_schema_resources = False
        self._lenient_documents: dict[str, dict[str, Any]] = {}
        self._implicit_documents: set[str] = set()
        self._rewritten_documents: set[str] = set()
        self._resource_ref_warnings: set[str] = set()
        self._schema_resources: dict[str, set[str]] = registry.resources
        self._dynamic_anchors: dict[tuple[str, str], dict[str, str]] = registry.dynamic_anchors
        self._embedded_resources: dict[tuple[str, str], dict[str, str]] = registry.embedded
        self._dynamic_scope_pointer = ""
        self._plain_ref_origin = RefOrigin.UNATTRIBUTED
        self._schema_resource_path_parts: dict[str, list[str]] = {}
        self._dynamic_document: str | None = None
        self._dynamic_scope_path: tuple[str, ...] = ()
        self._dynamic_bindings: dict[str, str] = {}
        self._dynamic_specializations: dict[tuple[_TargetNode, frozenset[tuple[str, str]]], Reference] = {}
        self._dynamic_specialization_paths: set[tuple[str, ...]] = set()
        self._pending_dynamic_specializations: deque[_DynamicSpecialization] = deque()
        self._draining_dynamic_specializations = False
        self._dynamic_anchor_usage: dict[_TargetNode, frozenset[str]] = {}
        self._dynamic_ref_graph: dict[_TargetNode, _DynamicEdges] = {}
        self._dynamic_ref_nodes: dict[str, _TargetNode] = {}

    def _schema_resource_document(self, path_parts: list[str]) -> str:
        """Identify one physical schema document without changing its public reference names."""
        document = self.model_resolver.join_path(tuple(path_parts)).split("#", 1)[0]
        if (location := self._schema_resource_document_aliases.get(document)) is None:
            location = (
                document if not document or is_url(document) else (self.base_path / document).resolve().as_posix()
            )
            self._schema_resource_document_aliases[document] = location
        return location

    def _iter_schema_resource_children(
        self, schema: dict[str, Any]
    ) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
        """Visit schema-valued keywords without inspecting examples, defaults or extensions."""
        for keyword, value in schema.items():
            if keyword in {"$defs", "definitions"} and isinstance(value, dict):
                for _name, child, path in self._iter_schema_definition_entries(value, [keyword]):
                    if isinstance(child, dict):
                        yield tuple(path), child
            elif keyword in _JSON_SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
                for name, child in value.items():
                    if isinstance(child, dict):
                        yield (keyword, str(name)), child
            elif (
                keyword in _JSON_SCHEMA_SEQUENCE_KEYWORDS or keyword in _JSON_SCHEMA_SINGLE_OR_SEQUENCE_KEYWORDS
            ) and isinstance(value, list):
                for index, sequence_child in enumerate(value):
                    if isinstance(sequence_child, dict):
                        yield (keyword, index), cast("dict[str, Any]", sequence_child)
            elif (
                keyword in _JSON_SCHEMA_SINGLE_KEYWORDS or keyword in _JSON_SCHEMA_SINGLE_OR_SEQUENCE_KEYWORDS
            ) and isinstance(value, dict):
                yield (keyword,), value

    def _schema_resource_identifier(
        self, schema: dict[str, Any], id_field: str | None, *, is_root: bool
    ) -> tuple[str | None, object]:
        """Inherit draft identifiers while preserving fallbacks for roots and unknown dialects."""
        if "$schema" in schema and getattr(self.config, "jsonschema_version", None) in {None, JsonSchemaVersion.Auto}:
            version = _detect_declared_jsonschema_version(schema)
            id_field = JsonSchemaFeatures.from_version(version).id_field if version is not None else None
        identifier = schema.get(id_field or self.schema_features.id_field)
        if not identifier and (is_root or id_field is None):
            identifier = schema.get("$id") or schema.get("id")
        return id_field, identifier

    def _resolve_schema_resource_ref(self, reference: str, base: str, document: str, *, nested_scope: bool) -> str:
        """Resolve registered resources before considering a physical document fetch."""
        if document in self._lenient_documents:
            return self._resolve_lenient_resource_ref(reference, base, document)
        absolute = urljoin(base, reference)
        resource, _, fragment = absolute.partition("#")
        if (location := self._schema_resource_registry.location(absolute)) is not None:
            return _document_location(location, document)
        if (
            fragment
            and not fragment.startswith("/")
            and (root := self._schema_resource_locations.get(resource)) is not None
            and root.split("#", 1)[1]
        ):
            msg = f"Embedded schema resource has no anchor {fragment!r}: {redact_url(reference)!r}"
            raise Error(msg)
        if nested_scope and resource.startswith("file://") and not reference.startswith("file://"):
            absolute = _file_uri_path(resource).as_posix() + absolute[len(resource) :]
        return absolute if nested_scope else reference

    def _resolve_lenient_resource_ref(self, reference: str, base: str, document: str) -> str:
        """Resolve a reference of a document that resolves references against itself, like OpenAPI.

        The reference keeps resolving against the document whenever that finds a target, and names the
        schema resource an $id of the document declares only otherwise. A reference that JSON Schema
        resolves elsewhere warns, so the schema can be fixed to mean the same under both.
        """
        if _is_plain_name_fragment(reference):
            location = self._schema_resource_locations.get(urljoin(base, reference))
            return reference if location is None else _document_location(location, document)
        if (location := self._schema_resource_registry.location(urljoin(base, reference))) is None:
            return reference
        if (target := _document_location(location, document)) == reference or not target.startswith("#"):
            return reference
        source = redact_url("/".join(self._schema_resource_path_parts[document]))
        if reference.startswith("#"):
            if not self._lenient_pointer_exists(document, reference):
                return target if self._lenient_pointer_exists(document, target) else reference
            message = (
                f"$ref {redact_url(reference)!r} in {source} is resolved against the document for compatibility, "
                f"but JSON Schema resolves it against the enclosing $id, to {redact_url(target)!r}."
            )
        else:
            if not self._loads_document(urljoin(self._schema_resource_root_bases[document], reference)):
                return target
            message = (
                f"$ref {redact_url(reference)!r} in {source} loads the referenced document for compatibility, "
                f"but JSON Schema resolves it to the schema with that $id at {redact_url(target)!r}."
            )
        self._resource_ref_warnings.add(f"{message} Update the reference or the $id so that both name one schema.")
        return reference

    def _lenient_pointer_exists(self, document: str, reference: str) -> bool:
        """Return whether a local JSON pointer reference names a location in a lenient document."""
        raw = self._lenient_documents[document]
        return not (parts := split_json_pointer(raw, reference.partition("#")[2])) or (
            _get_model_by_path_or_missing(raw, parts) is not _MISSING_JSON_POINTER
        )

    def _loads_document(self, absolute: str) -> bool:
        """Return whether loading an absolute reference as a document would find one."""
        if not absolute.startswith("file://"):
            return is_url(absolute) and self.allow_remote_refs is not False
        return _file_uri_path(absolute.partition("#")[0]).is_file()

    def _rewrite_schema_resource_refs(  # noqa: PLR0913
        self,
        schema: dict[str, Any],
        document: str,
        base: str,
        root_base: str,
        id_field: str | None,
        *,
        is_root: bool = False,
    ) -> dict[str, Any]:
        """Copy only schema containers whose resource-relative references change."""
        id_field, identifier = self._schema_resource_identifier(schema, id_field, is_root=is_root)
        if isinstance(identifier, str):
            base = urljoin(base, identifier).split("#", 1)[0]
        result = schema
        if isinstance(reference := schema.get("$ref"), str):
            resolved = self._resolve_schema_resource_ref(reference, base, document, nested_scope=base != root_base)
            if resolved != reference:
                result = {**schema, "$ref": resolved}
        children = self._iter_schema_resource_children(schema)
        for path, child in chain(children, self._implicit_schema_resources(schema)) if is_root else children:
            rewritten = self._rewrite_schema_resource_refs(child, document, base, root_base, id_field)
            if rewritten is child:
                continue
            if result is schema:
                result = dict(schema)
            parent: Any = result
            original: Any = schema
            for part in path[:-1]:
                if parent[part] is original[part]:
                    parent[part] = copy(original[part])
                parent, original = parent[part], original[part]
            if isinstance(parent, list):
                parent[cast("int", path[-1])] = rewritten
            else:
                cast("dict[str, Any]", parent)[cast("str", path[-1])] = rewritten
        return result

    def _implicit_schema_resources(self, raw: dict[str, Any]) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
        """Yield the paths and schemas that a document evaluates as schema resources without an $id.

        Formats such as OpenAPI evaluate each component schema independently.
        """
        if not self._component_schemas_are_resources:
            return
        match raw.get("components"):
            case {"schemas": dict() as schemas}:
                for name, schema in schemas.items():
                    if isinstance(schema, dict):
                        yield ("components", "schemas", name), schema

    def _prepare_schema_resources(self, raw: dict[str, Any], path_parts: list[str]) -> dict[str, Any]:
        """Add a loaded document to the schema graph once, instead of rescanning it on each reference (L0).

        The graph walk finds the document's resources and fills the registry dicts from them; then `$ref`s that
        resolve through an embedded `$id` are rewritten copy-on-write, and the prepared document is indexed.
        """
        document = self._schema_resource_document(path_parts)
        self._schema_resource_path_parts.setdefault(document, path_parts)
        if (cached := self._schema_resource_cache.get(document)) is not None and (raw is cached[0] or raw is cached[1]):
            return cached[1]
        registry = self._schema_resource_registry
        registry.forget(document)
        self._lenient_documents.pop(document, None)
        self._implicit_documents.discard(document)
        self._rewritten_documents.discard(document)
        base = document if is_url(document) else Path(document).as_uri() if document else f"{self.base_path.as_uri()}/"
        id_field = (
            self.schema_features.id_field
            if getattr(self.config, "jsonschema_version", None) not in {None, JsonSchemaVersion.Auto}
            else None
        )
        resources = walk_schema_resources(
            raw,
            tuple(self._implicit_schema_resources(raw)),
            document=DocumentId(document),
            base=base,
            id_field=id_field,
            children=self._iter_schema_resource_children,
            identifier=self._schema_resource_identifier,
        )
        registry.register(resources)
        if resources.implicit_resources:
            self._implicit_documents.add(document)
        if resources.implicit_resources and resources.embeds_resources:
            self._lenient_documents[document] = raw
        prepared = raw
        if resources.embeds_resources:
            self._has_embedded_schema_resources = True
            prepared = self._rewrite_schema_resource_refs(
                raw, document, base, resources.root_base, id_field, is_root=True
            )
            if prepared is not raw:
                self._rewritten_documents.add(document)
        prepared = self._index_document(prepared, path_parts)
        self._schema_resource_cache[document] = raw, prepared
        return prepared

    def _index_document(self, raw: dict[str, Any], path_parts: list[str]) -> dict[str, Any]:
        """Return a loaded document as a tree whose JSON objects the schema graph locates (L0).

        The document is named as reference paths name it. A copy of the root keeps the document as written that
        ``_document_as_written`` returns for the loaded one. A mounted converted schema keeps its locations.
        """
        if (graph := self._schema_graph) is None or graph.is_mounted(raw):
            return raw
        root = SchemaLocation(self.model_resolver.join_path(tuple(path_parts)).partition("#")[0], ())
        indexed = graph.index_document(
            graph.with_ref_edges(
                raw,
                root,
                DocumentId(self._schema_resource_document(path_parts)),
                self._ref_reading("root"),
            ),
            root,
        )
        if (entry := self._documents_as_written.get(id(raw))) is not None and entry[0] is raw:
            self._documents_as_written[id(indexed)] = indexed, entry[1]
        return indexed

    def _mount_converted_schema(
        self, converted: dict[str, Any], location: SchemaLocation | None, container: str = "", home: str | None = None
    ) -> dict[str, Any]:
        """Locate a JSON Schema converted from a value of a document at the location of that value (L0).

        Its references are local: below ``container``, the pointer the parser keeps the schema at in the document, or
        to its own root. Their edges name the locations their targets are mounted at, and resolve in ``home``, the
        document the parser keeps the schema in when that is not the document of the value.
        """
        if location is None or (graph := self._schema_graph) is None:
            return converted  # pragma: no cover - the values a parser converts are in documents it has indexed

        def mounted_ref(ref: str) -> str:
            return f"#{location.pointer}{ref.partition('#')[2].removeprefix(container)}"

        document = DocumentId(self._schema_resource_document([location.document]))
        edged = graph.with_ref_edges(
            converted, location, document, self._ref_reading("object"), ConvertedRefs(mounted_ref, home)
        )
        return graph.mount(edged, location)

    def _add_id_callback(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Add $id and $anchor to model resolver."""
        if obj.id:
            self.model_resolver.add_id(obj.id, path)
        if obj.anchor:
            self.model_resolver.add_id(f"#{obj.anchor}", path)

    def parse_ref(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Recursively parse all $ref references in a schema object, unless only the used ones are loaded."""
        if self._loads_only_used_references:
            return
        self._traverse_schema_objects(obj, path, self._resolve_ref_callback)
        self._parse_dynamic_specializations()

    def parse_id(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Recursively parse all $id fields in a schema object."""
        self._traverse_schema_objects(obj, path, self._add_id_callback)

    @contextmanager
    def root_id_context(self, root_raw: dict[str, Any]) -> Generator[None, None, None]:
        """Context manager to temporarily set the root $id during parsing.

        Uses schema_features.id_field to support both "id" (Draft 4) and "$id" (Draft 6+).
        Falls back to checking both fields for lenient compatibility.
        """
        previous_root_id = self.root_id
        # Try version-specific field first, then fallback to alternative for compatibility
        id_field = self.schema_features.id_field
        self.root_id = root_raw.get(id_field) or root_raw.get("$id") or root_raw.get("id") or None
        yield
        self.root_id = previous_root_id

    @cached_property
    def _schema_validation_context(self) -> dict[str, bool] | None:
        """Return the validation context of the schema objects: whether they read ``unevaluatedProperties``."""
        return {READ_UNEVALUATED_PROPERTIES_CONTEXT: True} if self.read_unevaluated_properties else None

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw or dumped schema as ``SCHEMA_OBJECT_TYPE`` in the parser's validation context."""
        return self.SCHEMA_OBJECT_TYPE.model_validate(raw, context=self._schema_validation_context)

    def _validate_schema_object(
        self,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
    ) -> JsonSchemaObject:
        """Validate raw data as JsonSchemaObject with path context in errors."""
        if raw is True:
            return self.SCHEMA_OBJECT_TYPE()
        if raw is False:
            return self.SCHEMA_OBJECT_TYPE(is_boolean_schema_false=True)
        try:
            return self._schema_object(raw)
        except SchemaParseError:
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

    def _validate_pointer_target(
        self, document: object, tokens: Sequence[str], target: YamlValue, path: list[str]
    ) -> JsonSchemaObject:
        """Validate the value a JSON pointer names in a document; a boolean schema is located by the pointer."""
        return self._validate_member(
            self._read_as_schema(target), path, self._member_location(document, *map(str, tokens))
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
        """Parse a raw schema, reusing a validated object when available."""
        if isinstance(raw, dict) and "x-python-import" in raw and not self._is_base_stand_in_path(path):
            self._handle_python_import(name, path)
            self._python_import_values[self.model_resolver.join_path(tuple(path))] = raw["x-python-import"]
            return

        # Strict mode: check for version-specific features before validation
        self._check_version_specific_features(raw, path)

        obj = validated_obj if validated_obj is not None else self._validate_schema_object(raw, path)
        self._cache_ref_data_type_facts(self.model_resolver.join_path(tuple(path)), obj)
        # Build $recursiveAnchor / $dynamicAnchor indexes for this schema
        self._build_anchor_indexes(obj, path)
        self.parse_obj(name, obj, path)

    def _check_version_specific_features(  # noqa: PLR0912
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
        schema_path = redact_path(_named_path(self._schema_value_location(raw), path))

        # Check boolean schemas (Draft 6+)
        if isinstance(raw, bool):
            if not self.schema_features.boolean_schemas:
                version_name = "Draft 4" if self.schema_features.id_field == "id" else "this version"
                warn(
                    f"Boolean schemas are not supported in {version_name}. Schema path: {schema_path}",
                    stacklevel=3,
                )
            return

        raw = cast("dict[str, YamlValue]", raw)
        # Check null in type array (JSON Schema / OpenAPI 3.1+)
        type_value = raw.get("type")
        if isinstance(type_value, list) and "null" in type_value and not self.schema_features.null_in_type_array:
            warn(
                'null in type array (e.g., type: ["string", "null"]) is not supported '
                f"in this schema version. Use nullable: true instead. Schema path: {schema_path}",
                stacklevel=3,
            )

        # Check exclusive min/max format (Draft 4 uses boolean, Draft 6+ uses number)
        exclusive_min = raw.get("exclusiveMinimum")
        exclusive_max = raw.get("exclusiveMaximum")
        if self.schema_features.exclusive_as_number:
            # Draft 6+: should be numeric, not boolean
            if isinstance(exclusive_min, bool):
                warn(
                    f"exclusiveMinimum as boolean is Draft 4 style, but schema version uses numeric style. "
                    f"Schema path: {schema_path}",
                    stacklevel=3,
                )
            if isinstance(exclusive_max, bool):
                warn(
                    f"exclusiveMaximum as boolean is Draft 4 style, but schema version uses numeric style. "
                    f"Schema path: {schema_path}",
                    stacklevel=3,
                )
        else:
            # Draft 4: should be boolean, not numeric
            if exclusive_min is not None and not isinstance(exclusive_min, bool):
                warn(
                    f"exclusiveMinimum as number is Draft 6+ style, but schema version is Draft 4. "
                    f"Schema path: {schema_path}",
                    stacklevel=3,
                )
            if exclusive_max is not None and not isinstance(exclusive_max, bool):
                warn(
                    f"exclusiveMaximum as number is Draft 6+ style, but schema version is Draft 4. "
                    f"Schema path: {schema_path}",
                    stacklevel=3,
                )

        if not self.schema_features.read_only_write_only:
            if raw.get("readOnly") is True:
                warn(
                    f"readOnly is not supported in this schema version (Draft 7+ only). Schema path: {schema_path}",
                    stacklevel=3,
                )
            if raw.get("writeOnly") is True:
                warn(
                    f"writeOnly is not supported in this schema version (Draft 7+ only). Schema path: {schema_path}",
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
        if obj.prefixItems is not None and not self.schema_features.prefix_items:
            warn(
                f"prefixItems is not supported in this schema version. "
                f"Use items as array for tuple validation. Schema path: {schema_path}",
                stacklevel=4,
            )

        # Check items as array usage (deprecated in Draft 2020-12)
        if isinstance(obj.items, list) and self.schema_features.prefix_items:
            warn_deprecated(
                "schema.jsonschema-items-array",
                details=f"Schema path: {schema_path}",
                stacklevel=4,
            )

    def _handle_python_import(
        self,
        name: str,
        path: list[str],
    ) -> None:
        """Mark x-python-import reference as loaded to skip model generation."""
        self.model_resolver.add(path, name, class_name=True, loaded=True)

    def _definition_entry_prefixes(self) -> tuple[tuple[str, ...], ...]:
        """Return the path segments after a document root that name a definitions container (``#/$defs``)."""
        return tuple((schema_path,) for schema_path, _ in self.schema_paths)

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

    def _is_current_root_schema(self, obj: JsonSchemaObject) -> bool:
        """Return whether a schema object is the root of the current document."""
        return obj.schema_location == self._current_document_root()

    def _drop_ref_from_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        return self._schema_object(obj.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True))

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
        if not (self._has_embedded_schema_resources or self._dynamic_anchors):
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
        """Dispatch a JsonSchemaObject to the parse method for its schema shape (``_DEFINITION_TABLE``)."""
        choice = self._DEFINITION_TABLE.choose(
            self, SchemaSite(name, self._read_false_all_of_values(self._array_all_of_site_schema(obj)), path)
        )
        choice.build(self, choice.site)

    def _merge_definition_ref(self, site: _Site) -> _Site:
        """Merge the `$ref` of a definition with its sibling keywords, in the dynamic scope the merged schema names."""
        if (merged := self._merge_ref_with_schema(site.obj)) is site.obj:
            return site
        return replace(site, obj=merged, scope=self._subschema_scope(merged))

    def _parse_definition_in_scope(self, site: _Site) -> None:
        with cast("AbstractContextManager[None]", site.scope):
            self._parse_schema_obj(site.name, site.obj, site.path)

    def _parse_combined_definition(self, site: _Site) -> None:
        obj, name, path = site.obj, site.name, site.path
        combined_items = obj.oneOf or obj.anyOf
        const_enum_data = self._extract_const_enum_from_combined(combined_items, obj)
        if const_enum_data is not None:
            synthetic_obj = self._create_synthetic_enum_obj(obj, *const_enum_data)
            if not self.should_parse_enum_as_literal(synthetic_obj, property_name=name, property_obj=obj):
                self._parse_enum_class(name, synthetic_obj, path, property_obj=obj)
            else:
                self.parse_root_type(name, synthetic_obj, path)
            return
        data_type = self.parse_root_type(name, obj, path)
        if isinstance(data_type, EmptyDataType) and obj.properties:
            self.parse_object(name, obj, path)

    def _definition_root_self_reference_applies(self, site: _Site) -> bool:
        return bool(_merges_ref_siblings(site.obj) and site.obj.ref == "#" and self._is_current_root_schema(site.obj))

    def _rewrite_definition_root_self_reference(self, site: _Site) -> _Site:
        return replace(site, obj=self._drop_ref_from_schema(site.obj))

    def _definition_ref_siblings_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return _merges_ref_siblings(site.obj)

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

    def _definition_ref_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(_merges_ref_siblings(site.obj) and site.obj.ref)

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
        """Type each user of ``reference`` in a field, a type or a dict key by ``import_``, as a mapped ``$ref`` is."""
        for fact in self.generation_store.index.data_type_facts_for_reference(reference):
            if fact.role == "base":
                continue
            self._import_ledger.append(self.imports, import_, _EXTERNAL_REF_MAPPING_IMPORT)
            self._graph_builder.detach_data_type_ref(fact.data_type)
            if fact.role == "dict_key":
                self._graph_builder.replace_nested_data_type(
                    cast("DataType", fact.data_type.parent), fact.data_type, self.data_type.from_import(import_)
                )
            else:
                self._graph_builder.swap_data_type(fact.data_type, self.data_type.from_import(import_))

    def _type_additional_properties_by_import(
        self, model: DataModel, reference: Reference, import_: Import, reference_classes: set[str]
    ) -> None:
        """Type the users of ``reference`` in the model's typed additional properties by ``import_``.

        The other classes they reference stay ``reference_classes``; ``extra_items`` is written again.
        """
        source = cast(
            "DataType", self._output_model_context.additional_properties_type_source(model.extra_template_data)
        )
        for data_type in [data_type for data_type in source.all_data_types if data_type.reference is reference]:
            self._graph_builder.detach_data_type_ref(data_type)
            if data_type is source:
                source = self.data_type.from_import(import_)
            else:
                self._graph_builder.swap_data_type(data_type, self.data_type.from_import(import_))
        self._output_model_context.store_additional_properties_type(
            model.extra_template_data,
            _make_referencing_type_expression(source),
            reference_classes,
            imports=tuple(source.all_imports),
            target_python_version=self.target_python_version,
        )
        model._name_final_classes()  # noqa: SLF001

    def _type_references_to_definitions_without_model(self) -> None:
        """Redirect the users of a definition parsed as its ``$ref`` alone to the reference that ``$ref`` names.

        Such a definition is a ``$ref`` with sibling keywords that cannot merge into its circular target, and outside
        a definitions container it gets no model. Once every schema is parsed, a reference to it is typed by its
        target (through such definitions in turn), as such a ``$ref`` is at a field, or by the import of a target
        that an external ref mapping names; a definition that got a model elsewhere keeps its users. A used one whose
        chain of such definitions loops back without reaching a model or an import has nothing to type it by, which
        is an error.
        """
        if not self._definitions_without_model:
            return
        modelled = {model.reference.path for model in self.results}
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
            elif target.path in seen and (
                self.generation_store.index.has_data_type_references(reference)
                or any(key in model.additional_properties_reference_classes for model in self.results)
            ):
                cycle = " -> ".join(redact_reference(path) for path in (*seen, target.path))
                msg = f"its $ref chain loops back without reaching a schema to type it by: {cycle}"
                raise SchemaParseError(msg, path=[key])
            for model in self.results:
                if key not in (classes := model.additional_properties_reference_classes):
                    continue
                other_classes = {path for path in classes if path != key}
                if import_ is None:
                    self._output_model_context.store_additional_properties_reference_classes(
                        model.extra_template_data, {*other_classes, target.path}
                    )
                else:
                    self._type_additional_properties_by_import(model, reference, import_, other_classes)
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
                message=f"$ref {redact_reference(path)!r} names no schema of the document that "
                f"--external-ref-mapping maps to {python_package!r}; a mapped $ref needs a fragment that names a "
                "schema of that document",
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
                for model in self.results:
                    if reference.path in (classes := model.additional_properties_reference_classes):
                        self._type_additional_properties_by_import(
                            model, reference, import_, {path for path in classes if path != reference.path}
                        )
        finally:
            self.imports = shared_imports
        self._reuse_optimization_context = replace(
            self._reuse_optimization_context, base_stand_in_keys=frozenset(self._base_stand_in_imports)
        )

    def _parse_base_stand_in(self, reference: Reference, file_path: Path | None) -> None:
        """Parse the schema at the key of ``reference`` into its model, in its document and in the module of a model."""
        source_path = self.current_source_path
        if source_path is not None:
            self.current_source_path = file_path
        try:
            with self._ref_document_context(reference.path.partition("#")[0]) as (_, raw):
                path_parts = list(self.model_resolver.current_root)
                self.parse_json_pointer(raw, reference.path, path_parts)
                self._parse_reserved_targets(raw, path_parts)
        finally:
            self.current_source_path = source_path

    def _end_schema_parsing(self) -> None:
        """Settle definition references, parse stand-ins, reject mapped references that name no schema, run the hook."""
        self._type_references_to_definitions_without_model()
        self._parse_base_stand_ins()
        self._reject_mapped_references_without_schema()
        self._generate_forced_base_models()

    def _definition_array_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return site.obj.is_array

    def _build_definition_array(self, site: _Site) -> object:
        return self.parse_array(site.name, site.obj, site.path)

    def _definition_all_of_beside_one_of_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.allOf and (site.obj.oneOf or site.obj.anyOf))

    def _definition_all_of_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.allOf)

    def _build_definition_all_of(self, site: _Site) -> object:
        return self.parse_all_of(site.name, site.obj, site.path)

    def _definition_schema_validator_object_applies(self, site: _Site) -> bool:
        return bool(self.generate_schema_validators and self._should_parse_object_with_schema_validators(site.obj))

    def _build_definition_schema_validator_object(self, site: _Site) -> object:
        return self._parse_object_model(site.name, site.obj, site.path)

    def _definition_one_of_any_of_applies(self, site: _Site) -> bool:  # noqa: PLR6301
        return bool(site.obj.oneOf or site.obj.anyOf)

    def _definition_enum_applies(self, site: _Site) -> bool:
        return bool(
            site.obj.enum
            and not self.ignore_enum_constraints
            and not self._reads_enum_as_const(site.obj)
            and not self.should_parse_enum_as_literal(site.obj, property_name=site.name)
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
            Step("ref-siblings", _definition_ref_siblings_applies, _merge_definition_ref),
            Row("merged-ref-in-own-scope", _definition_merged_ref_in_own_scope_applies, _parse_definition_in_scope),
            Row(
                "ref-of-definition-entry",
                _definition_ref_of_definition_entry_applies,
                _then_parse_refs(_parse_root_type_site),
            ),
            Row("ref", _definition_ref_applies, _build_definition_ref),
            Row("array", _definition_array_applies, _then_parse_refs(_build_definition_array)),
            Row(
                "all-of-beside-one-of",
                _definition_all_of_beside_one_of_applies,
                _then_parse_refs(_parse_root_type_site),
            ),
            Row("all-of", _definition_all_of_applies, _then_parse_refs(_build_definition_all_of)),
            Row(
                "schema-validator-object",
                _definition_schema_validator_object_applies,
                _then_parse_refs(_build_definition_schema_validator_object),
            ),
            Row("one-of-any-of", _definition_one_of_any_of_applies, _then_parse_refs(_parse_combined_definition)),
            *(Row(row.name, row.applies, _then_parse_refs(row.build)) for row in _OBJECT_KIND_ROWS),
            Row("enum", _definition_enum_applies, _then_parse_refs(_build_definition_enum)),
        ),
        Default("root-type", _then_parse_refs(_parse_root_type_site)),
    )

    def _get_context_source_path_parts(self) -> Iterator[tuple[Source, list[str]]]:
        """Get source and path parts for each input file with context managers."""
        if isinstance(self.source, list) or (isinstance(self.source, Path) and self.source.is_dir()):
            self.current_source_path = Path()
            self._cache_local_sources = self._cache_local_sources_during_parse
            self.model_resolver.after_load_files = {
                path.resolve().as_posix() for path in self._iter_local_source_paths()
            }

        for source in self.iter_source:
            if isinstance(self.source, ParseResult):
                path_parts = self.get_url_path_parts(self.source)
            else:
                path_parts = list(source.path.parts)
            if self.current_source_path is not None:
                self.current_source_path = source.path
            with (
                self.model_resolver.current_base_path_context(source.path.parent),
                self.model_resolver.current_root_context(path_parts),
            ):
                yield source, path_parts

    def _iter_local_source_paths(self) -> Iterator[Path]:
        if self._cache_local_sources and self._source_context.directory_input_filter is not None:
            self._local_source_cache = tuple(self._iter_source_uncached())
            yield from (self.base_path / source.path for source in self._local_source_cache)
            return
        match self.source:
            case Path() as path if path.is_dir():
                yield from (
                    file_path
                    for file_path in sorted(path.rglob("*"), key=lambda item: item.name)
                    if file_path.is_file()
                )
            case list() as paths:
                yield from ((self.base_path / path) for path in paths)

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
        if source.raw_data is None:
            try:
                return self._read_document(load_data(source.text))
            except (json.JSONDecodeError, *get_yaml_parse_errors()) as exc:
                source_path = self._source_path_for_diagnostics(source.path)
                raise InvalidFileFormatError(exc, self._input_file_type, source=source_path) from exc
            except TypeError as exc:
                if not self._non_dict_source_is_invalid:
                    raise
                source_path = self._source_path_for_diagnostics(source.path)
                raise InvalidFileFormatError(exc, self._input_file_type, source=source_path) from exc
        if isinstance(source.raw_data, dict):
            return self._read_document({**source.raw_data})

        msg = f"Expected dict, got {type(source.raw_data).__name__}"
        error = TypeError(msg)
        if not self._non_dict_source_is_invalid:
            raise error
        source_path = self._source_path_for_diagnostics(source.path)
        raise InvalidFileFormatError(error, self._input_file_type, source=source_path) from error

    def _cache_source_ref_body(self, source: Source, raw_obj: dict[str, YamlValue]) -> None:
        """Cache a local source body for later local $ref resolution."""
        if not source.path.parts:
            return
        self.remote_object_cache[str((self.base_path / source.path).resolve())] = raw_obj

    def _resolve_root_model_name(self, raw_obj: dict[str, Any]) -> tuple[str, bool]:
        title = raw_obj.get("title")
        title_str = str(title) if title is not None else "Model"
        if self.custom_class_name_generator:
            return title_str, False

        if class_name := self.class_name:
            if not self.model_resolver.validate_name(class_name, self.target_python_version):
                raise InvalidClassNameError(class_name)
            return class_name, self._should_preserve_explicit_root_class_name(class_name)

        obj_name = title_str
        if not self.model_resolver.validate_name(obj_name, self.target_python_version):
            obj_name = title_to_class_name(obj_name)
        if not self.model_resolver.validate_name(obj_name, self.target_python_version):
            raise InvalidClassNameError(obj_name)
        return obj_name, False

    def _parse_converted_sources(self, make_converter: Callable[[], Any]) -> None:
        try:
            for source, path_parts in self._get_context_source_path_parts():
                raw_obj = make_converter().convert(source)
                source.raw_data = raw_obj
                self._cache_source_ref_body(source, raw_obj)
                self.raw_obj = raw_obj
                obj_name, preserve_root_class_name = self._resolve_root_model_name(raw_obj)
                self._parse_file(
                    raw_obj,
                    obj_name,
                    path_parts,
                    preserve_root_class_name=preserve_root_class_name,
                )

            self._resolve_unparsed_json_pointer()
            self._end_schema_parsing()
        finally:
            self._clear_inherited_field_caches()
            self._reset_local_source_cache()

    @override
    def parse_raw(self) -> None:
        """Parse all raw input sources into data models."""
        try:
            for source, path_parts in self._get_context_source_path_parts():
                try:
                    raw_obj = self._load_source_dict(source)
                except TypeError:
                    warn(f"{source.path} is empty or not a dict. Skipping this file", stacklevel=2)
                    continue
                self._cache_source_ref_body(source, raw_obj)
                self.raw_obj = raw_obj
                obj_name, preserve_root_class_name = self._resolve_root_model_name(self.raw_obj)
                self._parse_file(self.raw_obj, obj_name, path_parts, preserve_root_class_name=preserve_root_class_name)

            self._resolve_unparsed_json_pointer()
            self._end_schema_parsing()
        finally:
            self._clear_inherited_field_caches()
            self._reset_local_source_cache()

    def _resolve_unparsed_json_pointer(self) -> None:
        """Parse the targets each source reserves, in key order, until a round over the sources adds no model.

        A target is read from the prepared tree of its source document, the tree the document's models came from. A
        document whose references the embedded-resource rewrite changed, and that does not resolve references against
        itself, is the named legacy row ``rewritten-document``: its targets are read from a fresh load of the source,
        which holds the references as written.
        """
        while True:
            model_count = len(self.results)
            for source in self.iter_source:
                path_parts = list(source.path.parts)
                if not self.reserved_refs.get(tuple(path_parts)):
                    continue
                if self.current_source_path is not None:
                    self.current_source_path = source.path
                prepared = self._schema_resource_cache[document := self._schema_resource_document(path_parts)][1]
                rewritten = document in self._rewritten_documents and document not in self._lenient_documents
                loads: list[dict[str, YamlValue]] = []
                with (
                    self.model_resolver.current_base_path_context(source.path.parent),
                    self.model_resolver.current_root_context(path_parts),
                ):
                    for target in self._reserved_targets(path_parts, prepared, self._reserved_ref_loaded):
                        tree = self._load_source_once(source, loads) if rewritten else prepared
                        self._check_reserved_target(target, prepared, source, loads, rewritten=rewritten)
                        self.raw_obj = tree
                        self.parse_json_pointer(tree, target["key"], path_parts)
            if model_count == len(self.results):
                return

    def _reserved_targets(
        self, path_parts: list[str], tree: dict[str, YamlValue], loaded: Callable[[str], bool]
    ) -> Iterator[ReservedTarget]:
        """Yield the targets the source document at ``path_parts`` reserves and ``loaded`` does not, in key order.

        The keys are read when the first target is taken, and each key is tested when its turn comes, after the target
        before it is parsed; the targets parsed meanwhile reserve more for a later round. The node of a target is the
        object its key names in ``tree``, the document's prepared tree.
        """
        document = DocumentId(self._schema_resource_document(path_parts))
        graph = cast("SchemaGraphBuilder", self._schema_graph)
        for key in sorted(self.reserved_refs.get(tuple(path_parts)) or ()):
            if not loaded(key):
                yield ReservedTarget(document=document, key=key, node=graph.node(_reserved_value(tree, key)))

    def _reserved_ref_loaded(self, key: str) -> bool:
        """Return whether the model of a reserved key is loaded, adding its reference when it has none."""
        return self.model_resolver.add_ref(key, resolved=True).loaded

    def _reserved_ref_settled(self, key: str) -> bool:
        """Return whether a reserved key has no reference, or names a loaded model."""
        return (reference := self.model_resolver.references.get(key)) is None or reference.loaded

    def _load_source_once(self, source: Source, loads: list[dict[str, YamlValue]]) -> dict[str, YamlValue]:
        """Return a fresh load of a source as a located tree; ``loads`` keeps the one load a round makes."""
        if not loads:
            loads.append(self._index_document(self._load_source_dict(source), list(source.path.parts)))
        return loads[0]

    def _check_reserved_target(
        self,
        target: ReservedTarget,
        prepared: dict[str, YamlValue],
        source: Source,
        loads: list[dict[str, YamlValue]],
        *,
        rewritten: bool,
    ) -> None:
        """Report a reserved target whose value in the prepared tree differs from a fresh load of its source.

        A document that resolves references against itself is parsed from its prepared tree, whose rewritten
        references a fresh load does not hold, and is not compared. A difference in a ``rewritten`` document, whose
        targets are read from the fresh load, is the listed ``rewritten-document`` class, recorded and not reported.
        """
        if (mode := check_mode(CheckArea.SCHEMA_GRAPH)) is None or target["document"] in self._lenient_documents:
            return
        key = target["key"]
        if same_values(_reserved_value(prepared, key), _reserved_value(self._load_source_once(source, loads), key)):
            return
        message = (
            f"the reserved target {redact_url(key)!r} differs between the prepared tree and a fresh load of its source"
        )
        if rewritten:
            mode.record(f"{mode.area.value}: {message}", kind="rewritten-document", key=redact_url(key))
            return
        mode.violation(Error, message, kind="reserved-target", key=redact_url(key))

    def parse_json_pointer(self, raw: dict[str, YamlValue], ref: str, path_parts: list[str]) -> None:
        """Parse a JSON pointer reference into a model."""
        path = ref.split("#", 1)[-1]
        path = path.removeprefix("/")
        object_paths, reference_paths = _split_json_pointer(raw, path)
        if not object_paths:  # pragma: no cover
            reference = self.model_resolver.add_ref(ref)
            self.parse_obj(reference.name, self._validate_schema_object(raw, [ref]), [ref])
            return
        models = self._get_model_by_json_pointer(raw, object_paths, ref)
        model_name = reference_paths[-1]

        self._parse_raw_at(
            model_name,
            models,
            [*path_parts, f"#/{reference_paths[0]}", *reference_paths[1:]],
            self._member_location(raw, *object_paths),
        )

    def _get_model_by_json_pointer(
        self,
        raw: dict[str, YamlValue],
        object_paths: list[str],
        ref: str,
    ) -> YamlValue:
        """Resolve one JSON pointer, preserving the legacy empty-schema fallback when it is missing.

        The fallback stands for the missing target, so it is located where the pointer points.
        """
        model = _get_model_by_path_or_missing(raw, object_paths)
        if model is not _MISSING_JSON_POINTER:
            return cast("YamlValue", model)

        source, _, fragment = ref.partition("#")
        if not source:
            source = self._source_path_for_diagnostics(self.current_source_path)
        self._dangling_refs.add((source, f"#{fragment}"))
        return _located({}, self._member_location(raw, *map(str, object_paths)))

    @override
    def _report_parse_diagnostics(self) -> None:
        """Report each unique dangling local reference and compatibility resolution after schema parsing completes."""
        for message in sorted(self._resource_ref_warnings):
            warn(message, SchemaResourceRefWarning, stacklevel=stacklevel_at_parse_caller())
        self._resource_ref_warnings.clear()
        if not self._dangling_refs:
            return

        dangling_refs = sorted(self._dangling_refs)
        if self.strict_refs:
            details = "\n".join(f"- {redact_url(source)}: {ref}" for source, ref in dangling_refs)
            msg = f"Unresolved local $ref targets:\n{details}"
            raise Error(msg)

        for source, ref in dangling_refs:
            warn(
                f"Unresolved local $ref {ref!r} in {redact_url(source)}: JSON pointer was not found. "
                "Generated a fallback Any model; use --strict-refs to fail instead.",
                DanglingRefWarning,
                stacklevel=stacklevel_at_parse_caller(),
            )
        self._dangling_refs.clear()

    @staticmethod
    @lru_cache(maxsize=16)
    def _schema_object_raw_key_sets(
        schema_object_type: type[JsonSchemaObject],
    ) -> tuple[frozenset[str], frozenset[str]]:
        keys = {"definitions", "$defs"}
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
        return self._schema_object_raw_key_sets(self.SCHEMA_OBJECT_TYPE)[0]

    def _has_schema_affecting_keywords(self, raw: dict[str, Any]) -> bool:
        schema_affecting_keys = self._schema_object_raw_key_sets(self.SCHEMA_OBJECT_TYPE)[1]
        return any(str(key) in schema_affecting_keys for key in raw)

    def _is_version_definition_namespace_name(self, name: str) -> bool:  # noqa: PLR6301
        return re.fullmatch(r"v\d+(?:[._-]\d+)*", name, flags=re.IGNORECASE) is not None

    def _iter_definition_namespace_entries(
        self,
        raw: dict[str, Any],
        path: list[str],
        *,
        include_direct_children: bool,
    ) -> Iterator[tuple[str, YamlValue, list[str]]]:
        for schema_key in ("definitions", "$defs"):
            if isinstance(definitions := raw.get(schema_key), dict):
                yield from self._iter_schema_definition_entries(definitions, [*path, schema_key])

        if not include_direct_children:
            return

        known_keys = self._known_schema_object_raw_keys()
        for key, value in raw.items():
            key_str = str(key)
            if key_str in known_keys or key_str.startswith("x-") or not isinstance(value, (dict, bool)):
                continue
            yield from self._iter_schema_definition_entry(key_str, value, [*path, key_str])

    def _iter_schema_definition_entry(
        self,
        name: str,
        raw: YamlValue,
        path: list[str],
    ) -> Iterator[tuple[str, YamlValue, list[str]]]:
        if isinstance(raw, dict) and not self._has_schema_affecting_keywords(raw):
            entries = list(
                self._iter_definition_namespace_entries(
                    raw,
                    path,
                    include_direct_children=self._is_version_definition_namespace_name(name),
                )
            )
            if entries:
                yield from entries
                return
        yield name, raw, path

    def _iter_schema_definition_entries(
        self,
        definitions: dict[str, YamlValue],
        base_path: list[str],
    ) -> Iterator[tuple[str, YamlValue, list[str]]]:
        for key, model in definitions.items():
            name = str(key)
            yield from self._iter_schema_definition_entry(name, model, [*base_path, name])

    @_parses_document
    def _parse_file(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915
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
        object_paths = [o for o in object_paths or [] if o]
        reference_paths = [r for r in reference_paths or [] if r]
        path = (
            [*path_parts, f"#/{reference_paths[0]}", *reference_paths[1:]]
            if reference_paths
            else [*path_parts, f"#/{object_paths[0]}", *object_paths[1:]]
            if object_paths
            else path_parts
        )
        with self.model_resolver.current_root_context(path_parts):
            prepared = self._prepare_schema_resources(raw, path_parts)
            if self.raw_obj is raw:
                self.raw_obj = prepared
            raw = prepared
            self._schema_resource_path_parts[self._schema_resource_document(path_parts)] = path_parts
            obj_name = self.model_resolver.add(
                path,
                obj_name,
                unique=False,
                class_name=True,
                preserve_class_name=preserve_root_class_name,
            ).name
            with self.root_id_context(raw):
                # parse $id before parsing $ref
                root_obj = self._validate_schema_object(raw, path_parts or ["#"])
                if (
                    self.config.generate_schema_validators
                    and raw is self.raw_obj
                    and root_obj.patternProperties
                    and not any((self.config.base_class, self.config.base_class_map, self.config.custom_template_dir))
                    and self.config.extra_template_data is None
                ):
                    self._pattern_validation_document_root = root_obj
                self._cache_ref_data_type_facts(self.model_resolver.join_path(tuple(path_parts or ["#"])), root_obj)
                self.parse_id(root_obj, [*path_parts, "#"] if path_parts else ["#"])
                root_key = tuple(path_parts)
                if root_obj.recursiveAnchor:
                    self._recursive_anchor_index.setdefault(root_key, []).append(
                        self._anchor_ref_path(root_key, path_parts)
                    )
                if root_obj.dynamicAnchor:
                    self._dynamic_anchor_index.setdefault(root_key, {}).setdefault(
                        root_obj.dynamicAnchor, self._anchor_ref_path(root_key, path_parts)
                    )
                definitions: dict[str, YamlValue] = {}
                schema_path = ""
                for schema_path_candidate, split_schema_path in self.schema_paths:
                    if definitions := cast("dict[str, YamlValue]", get_model_by_path(raw, split_schema_path)):
                        schema_path = schema_path_candidate
                        break

                definition_entries = list(self._iter_schema_definition_entries(definitions, [*path_parts, schema_path]))
                definition_metadata_entries = [
                    *((str(key), model, [*path_parts, schema_path, str(key)]) for key, model in definitions.items()),
                    *definition_entries,
                ]
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
                    if obj.recursiveAnchor:
                        ref_path = self._anchor_ref_path(root_key, definition_path)
                        self._recursive_anchor_index.setdefault(root_key, []).append(ref_path)
                    if obj.dynamicAnchor:
                        ref_path = self._anchor_ref_path(root_key, definition_path)
                        self._dynamic_anchor_index.setdefault(root_key, {}).setdefault(obj.dynamicAnchor, ref_path)

                if object_paths:
                    models = (
                        get_model_by_path(raw, object_paths)
                        if ref is None
                        else self._get_model_by_json_pointer(raw, object_paths, ref)
                    )
                    model_name = object_paths[-1]
                    self.parse_obj(model_name, self._validate_pointer_target(raw, object_paths, models, path), path)
                elif not self.skip_root_model:
                    self.parse_obj(obj_name, root_obj, path_parts or ["#"])
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
            for target in self._reserved_targets(path_parts, raw, self._reserved_ref_settled):
                reserved_path = target["key"]
                reference = self.model_resolver.references[reserved_path]
                object_paths, reference_paths = _split_json_pointer(raw, reserved_path.split("#", 1)[-1])
                if not object_paths:
                    self.parse_obj(reference.name, self._validate_schema_object(raw, [reserved_path]), [reserved_path])
                    continue
                models = self._get_model_by_json_pointer(raw, object_paths, reserved_path)
                model_name = reference_paths[-1]
                path = [*path_parts, f"#/{reference_paths[0]}", *reference_paths[1:]]
                self.parse_obj(model_name, self._validate_pointer_target(raw, object_paths, models, path), path)


# Snapshot the default hooks after JsonSchemaParser is fully defined. The local
# false-ref fact fast path is disabled for any parser that customizes a loader.
_BUILTIN_REF_RAW_SCHEMA_LOADER = JsonSchemaParser._get_ref_raw_schema  # noqa: SLF001
_BUILTIN_REF_SCHEMA_LOADER = JsonSchemaParser._load_ref_schema_object  # noqa: SLF001
_BUILTIN_SCHEMA_VALIDATOR = JsonSchemaParser._validate_schema_object  # noqa: SLF001
_BUILTIN_REF_FACT_CACHER = JsonSchemaParser._cache_ref_data_type_facts  # noqa: SLF001
