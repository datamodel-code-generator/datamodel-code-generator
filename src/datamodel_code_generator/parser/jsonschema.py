"""JSON Schema parser implementation.

Handles parsing of JSON Schema, JSON, YAML, Dict, and CSV inputs to generate
Python data models. Supports draft-04 through draft-2020-12 schemas.
"""

from __future__ import annotations

import enum as _enum
import json
import math
import re
from collections import defaultdict, deque
from contextlib import contextmanager, suppress
from copy import copy
from fractions import Fraction
from functools import cached_property, lru_cache
from itertools import chain, starmap
from math import gcd, lcm
from pathlib import Path
from string import digits
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, Optional, Union, cast
from urllib.parse import ParseResult, unquote, urljoin, urlparse
from warnings import warn

from pydantic import (
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel, to_pascal, to_snake
from typing_extensions import Unpack

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
    VersionMode,
    snooper_to_methods,
)
from datamodel_code_generator._format_types import (
    DatetimeClassType,
)
from datamodel_code_generator._shared_types import DefaultPutDict, LiteralType
from datamodel_code_generator._source import (
    YamlValue,
    _load_parser_source_data_from_path_bytes,
    load_data,
    load_data_from_path,
)
from datamodel_code_generator.deprecations import warn_deprecated
from datamodel_code_generator.enums import AliasGenerator
from datamodel_code_generator.imports import IMPORT_ANY, Import
from datamodel_code_generator.model import DataModel, DataModelFieldBase
from datamodel_code_generator.model.base import UNDEFINED, c3_merge, get_inherited_fields, sanitize_module_name
from datamodel_code_generator.model.enum import (
    NULL_ENUM_MEMBER_VALUE,
    SPECIALIZED_ENUM_TYPE_MATCH,
    Enum,
    EnumMemberValue,
    StrEnum,
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
from datamodel_code_generator.parser.base import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
    _RAW_SCHEMA_DEFAULT_UNDEFINED,
    _SOURCE_REFERENCE_PATH_KEY,
    SPECIAL_PATH_FORMAT,
    Parser,
    Source,
    _copy_data_model_field,  # noqa: F401  # Preserve the existing parser helper export.
    _copy_data_type,  # noqa: F401  # Preserve the existing parser helper export.
    _copy_resolved_inherited_field,  # noqa: F401  # Preserve the existing parser helper export.
    _detach_deferred_inherited_field_parents,
    _get_inherited_type_modifiers,
    get_special_path,
    title_to_class_name,
)
from datamodel_code_generator.parser.schema_version import (
    JsonSchemaFeatures,
    _detect_declared_jsonschema_version,
    detect_jsonschema_version,
    get_data_formats,
)
from datamodel_code_generator.python_literal import _semantic_value_text
from datamodel_code_generator.reference import (
    _ALIAS_RESOLUTION_CLASS_NAME_KEY,
    SPECIAL_PATH_MARKER,
    ModelType,
    Reference,
    get_inferred_module_name,
    is_url,
)
from datamodel_code_generator.types import (
    ANY,
    DataType,
    EmptyDataType,
    FloatConstraint,
    Types,
    UnionIntFloat,
)
from datamodel_code_generator.util import BaseModel, get_yaml_parse_errors

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence

    from typing_extensions import TypeIs

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr, PythonTypeRuntimeSymbol
    from datamodel_code_generator._python_type_binding import BoundPythonType
    from datamodel_code_generator._types import JSONSchemaParserConfigDict
    from datamodel_code_generator.config import JSONSchemaParserConfig

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
_NUMBER_CONSTRAINT_KEYS: tuple[JsonSchemaConstraintKey, ...] = (
    "minimum",
    "maximum",
    "multipleOf",
    "exclusiveMaximum",
    "exclusiveMinimum",
)
_VALUE_STRING_CONSTRAINT_KEYS: tuple[JsonSchemaConstraintKey, ...] = ("pattern", "minLength", "maxLength")
_ARRAY_CONSTRAINT_KEYS = frozenset({"maxItems", "minItems", "uniqueItems"})
_ALL_OF_METADATA_FIELDS = ("nullable", "description", "default", "example", "examples", "readOnly", "writeOnly")
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
_INHERITED_PROPERTY_COUNT_CONSTRAINT_FIELDS = frozenset({"maxProperties", "minProperties"})
_ALLOF_BOUND_CONSTRAINT_FIELDS = frozenset({
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    *_INHERITED_PROPERTY_COUNT_CONSTRAINT_FIELDS,
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
_INHERITED_CONSTRAINT_TYPE_FIELDS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"string"}), frozenset(_VALUE_STRING_CONSTRAINT_KEYS)),
    (frozenset({"integer", "number"}), frozenset(_NUMBER_CONSTRAINT_KEYS)),
    (frozenset({"array"}), _ARRAY_CONSTRAINT_KEYS),
    (frozenset({"object"}), _INHERITED_PROPERTY_COUNT_CONSTRAINT_FIELDS),
)
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
_NO_INHERITED_SCHEMA_MERGE = object()

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


_NUMERIC_TYPE_DOMAINS = {
    "integer": frozenset({"integer"}),
    "number": frozenset({"integer", "number"}),
    "null": frozenset({"null"}),
}


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


def _json_literal_values_equal(left: object, right: object) -> bool:  # noqa: PLR0911
    """Compare JSON literals with the equality semantics used by uniqueItems."""
    match left:
        case None:
            return right is None
        case bool() as boolean:
            return isinstance(right, bool) and boolean == right
        case int() | float() as number:
            return not isinstance(right, bool) and isinstance(right, (int, float)) and number == right
        case str() as string:
            return isinstance(right, str) and string == right
        case list() as array:
            return (
                isinstance(right, list)
                and len(array) == len(right)
                and all(starmap(_json_literal_values_equal, zip(array, right, strict=True)))
            )
        case dict() as object_:
            return (
                isinstance(right, dict)
                and len(object_) == len(right)
                and all(key in right and _json_literal_values_equal(item, right[key]) for key, item in object_.items())
            )
    return False


_HASH_SAFE_JSON_SCALAR_TYPES = frozenset({str, int, float, bool, type(None)})


def _is_hash_safe_json_scalar(value: object) -> bool:
    """Keep scalar subclasses with custom equality or hashing on the equality fallback."""
    value_type = type(value)
    return any(
        isinstance(value, scalar_type)
        and value_type.__eq__ is scalar_type.__eq__
        and value_type.__hash__ is scalar_type.__hash__
        for scalar_type in (str, int, float)
    )


def _intersect_all_of_enum(parent: list[Any], child: list[Any]) -> list[Any]:
    """Intersect JSON enum values while retaining order and already-correct enum aliases."""
    if all(
        type(item) in _HASH_SAFE_JSON_SCALAR_TYPES or _is_hash_safe_json_scalar(item) for item in chain(parent, child)
    ):
        # JSON booleans are distinct from numbers; integral floats equal their integer values.
        parent_keys = {(isinstance(item, bool), item) for item in parent}
        child_keys = {(isinstance(item, bool), item) for item in child}
        if parent_keys == child_keys and parent:
            return parent + child
        intersection = [item for item in parent if (isinstance(item, bool), item) in child_keys]
    else:
        intersection = [
            item for item in parent if any(_json_literal_values_equal(item, candidate) for candidate in child)
        ]
        if len(intersection) == len(parent) and all(
            any(_json_literal_values_equal(item, candidate) for candidate in parent) for item in child
        ):
            return parent + child
    return intersection or parent + child


def _align_all_of_enum_metadata(parent: dict[str, Any], child: dict[str, Any], result: dict[str, Any]) -> None:
    """Keep annotations attached to their retained enum values, preferring parent annotations."""
    metadata = [
        key for key in ("x-enum-varnames", "x-enumNames", "x-enum-descriptions") if parent.get(key) or child.get(key)
    ]
    if not metadata:
        return
    retained_ids = {id(value) for value in result["enum"]}
    indices = [index for index, value in enumerate(parent["enum"]) if id(value) in retained_ids]
    child_indices: list[int] | None = None
    for key in metadata:
        parent_metadata = parent.get(key, [])
        child_metadata = child.get(key, [])
        if not child_metadata or indices[-1] < len(parent_metadata):
            result[key] = [parent_metadata[index] if index < len(parent_metadata) else None for index in indices]
            continue
        if child_indices is None:
            if all(
                type(value) in _HASH_SAFE_JSON_SCALAR_TYPES or _is_hash_safe_json_scalar(value)
                for value in chain(parent["enum"], child["enum"])
            ):
                positions: dict[tuple[bool, Any], int] = {}
                for index, value in enumerate(child["enum"]):
                    positions.setdefault((isinstance(value, bool), value), index)
                child_indices = [positions[isinstance(value, bool), value] for value in result["enum"]]
            else:
                child_indices = [
                    next(
                        index
                        for index, candidate in enumerate(child["enum"])
                        if _json_literal_values_equal(value, candidate)
                    )
                    for value in result["enum"]
                ]
        result[key] = [
            parent_metadata[index]
            if index < len(parent_metadata)
            else child_metadata[child_index]
            if child_index < len(child_metadata)
            else None
            for index, child_index in zip(indices, child_indices, strict=True)
        ]


def _is_rw_model_variant_path(path: str) -> bool:
    """Return whether a path belongs to an internal Request/Response variant."""
    return any(marker in path for marker in _RW_MODEL_VARIANT_SPECIAL_MARKERS)


def _get_rw_model_variant_source_path(
    base_reference: Reference,
    suffix: Literal["Request", "Response"],
) -> str:
    """Return the legacy logical source path for a generated variant."""
    source_prefix, separator, source_name = base_reference.path.rpartition("/")
    return f"{source_prefix}{separator}{source_name}{suffix}"


def _get_unique_rw_model_variant_source_path(
    source_variant_ref: str,
    variant_name: str,
    unique_name: str,
    *,
    collides_with_source: bool,
) -> str:
    """Keep logical metadata paths compatible and unambiguous on source collisions."""
    if not collides_with_source:
        return source_variant_ref
    if unique_name != variant_name and unique_name.startswith(variant_name):
        return f"{source_variant_ref}{unique_name.removeprefix(variant_name)}"
    if numeric_index := unique_name.removeprefix(unique_name.rstrip(digits)):
        return f"{source_variant_ref}{numeric_index}"
    source_prefix, separator, _ = source_variant_ref.rpartition("/")
    return f"{source_prefix}{separator}{unique_name}"


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


def _qualified_python_type_import(qualified_name: str) -> Import:
    return _QUALIFIED_PYTHON_TYPE_IMPORT_ALIASES.get(qualified_name) or Import.from_full_path(qualified_name)


_STRING_CONSTRAINT_KEYS: tuple[JsonSchemaConstraintKey, ...] = (
    "pattern",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
)
_BYTES_CONSTRAINT_KEYS: tuple[JsonSchemaConstraintKey, ...] = ("minLength", "maxLength")
_NUMBER_CONSTRAINT_TYPES = frozenset({
    Types.int32,
    Types.int64,
    Types.integer,
    Types.float,
    Types.double,
    Types.number,
    Types.time,
    Types.decimal,
})

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


def _get_data_type_constraint_kwargs(
    obj: JsonSchemaObject,
    type_: Types,
) -> dict[str, JsonSchemaConstraintValue]:
    match type_:
        case number_type if number_type in _NUMBER_CONSTRAINT_TYPES:
            keys = _NUMBER_CONSTRAINT_KEYS
        case Types.string:
            keys = _STRING_CONSTRAINT_KEYS
        case Types.binary:
            keys = _BYTES_CONSTRAINT_KEYS
        case _:
            return {}
    return {
        key: value.value if isinstance(value, UnionIntFloat) else value
        for key in keys
        if (value := getattr(obj, key, None)) is not None
    }


def _get_discriminator_property_name(obj: JsonSchemaObject) -> str | None:
    """Return the discriminator property name from either JSON Schema or OpenAPI shape."""
    discriminator = obj.discriminator
    if isinstance(discriminator, Discriminator):
        return discriminator.propertyName
    if isinstance(discriminator, str):
        return discriminator
    return None


def _literal_uniqueness_key(value: JsonSchemaLiteral) -> tuple[type[object], JsonSchemaLiteral]:
    return type(value), value


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


def _get_tagged_union_value(literal: JsonSchemaLiteral) -> TaggedUnionValue | None:
    """Return a supported tagged-union value for a JSON Schema literal."""
    match literal:
        case bool():
            return None
        case int() | str():
            return literal
    return None  # pragma: no cover


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
_MISSING_JSON_POINTER = object()


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


def _is_object_only_type(type_: str | list[str] | None) -> bool:
    """Return whether a schema type permits only object values."""
    match type_:
        case None | "object" | ["object"]:
            return True
    return False


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
    msg = f"Invalid external $ref: {ref}"
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


class JsonSchemaObject(BaseModel):
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
        """Validate and normalize $ref values."""
        if isinstance(value, str) and "#" in value:
            if value.endswith("#/"):
                return value[:-1]
            if "#/" in value or value[0] == "#" or value[-1] == "#":
                return value
        return value

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
    extras: dict[str, Any] = Field(alias=__extra_key__, default_factory=dict)
    discriminator: Optional[Union[Discriminator, str]] = None  # noqa: UP007, UP045
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ignored_types=(cached_property,),
        defer_build=True,
    )

    def model_post_init(self, __context: Any, /) -> None:
        """Apply post-validation compatibility handling for extension metadata."""
        # Support x-propertyNames extension for OpenAPI 3.0
        if "x-propertyNames" in self.extras and self.propertyNames is None:
            x_prop_names = self.extras.pop("x-propertyNames")
            if isinstance(x_prop_names, bool):
                self.propertyNames = x_prop_names
            elif isinstance(x_prop_names, dict):
                self.propertyNames = JsonSchemaObject.model_validate(x_prop_names)

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
        return None  # pragma: no cover

    @cached_property
    def type_has_null(self) -> bool:
        """Check if the type list or oneOf/anyOf contains null."""
        if isinstance(self.type, list) and "null" in self.type:
            return True
        for item in self.oneOf + self.anyOf:
            if not isinstance(item, JsonSchemaObject):
                continue
            if item.type == "null":
                return True
            if isinstance(item.type, list) and "null" in item.type:
                return True
        return False

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
    JsonSchemaObject.__extra_key__,
}


_DEFAULT_SCHEMA_PATHS = ("#/definitions", "#/$defs")
_REGEX_META_CHARACTERS = frozenset(r"\.^$*+?{}[]|()")


def _literal_pattern_value(pattern: str) -> str | None:
    """Recognize plain characters and escaped regex metacharacters only."""
    if _REGEX_META_CHARACTERS.isdisjoint(pattern):
        return pattern
    if re.fullmatch(r"(?:[^\\.^$*+?{}\[\]|()]|\\[\\.^$*+?{}\[\]|()])*", pattern) is None:
        return None
    return re.sub(r"\\(.)", r"\1", pattern)


def _intersect_patterns(patterns: Sequence[str]) -> str:
    """Preserve pairwise output unless literal searches need independent positions."""
    match patterns:
        case [pattern]:
            return pattern
        case [left, right] if left == right:
            return left
    literals = [_literal_pattern_value(pattern) for pattern in patterns]
    if all(literal is not None for literal in literals):
        longest = max((literal for literal in literals if literal is not None), key=str.__len__)
        if any(literal is not None and not longest.startswith(literal) for literal in literals):
            return r"\A" + "".join(rf"(?=[\s\S]*{pattern})" for pattern in dict.fromkeys(patterns))
    match patterns:
        case [left, right]:
            return f"(?={left})(?={right})"
    result = patterns[0]
    for pattern in patterns[1:]:
        result = _intersect_patterns((result, pattern))
    return result


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


class _DynamicSpecialization(NamedTuple):
    """A referenced schema to parse again with the $dynamicAnchor bindings of its dynamic scope."""

    target: str
    bindings: dict[str, str]
    reference: Reference
    path: tuple[str, ...]


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
        self._all_of_root_value_ref_stack: set[tuple[str, ...]] | None = None
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
        self._dangling_refs: set[tuple[str, str]] = set()
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
        self._inherited_parent_property_cache: dict[
            tuple[str | int, str, frozenset[str], bool],
            tuple[
                JsonSchemaObject,
                tuple[JsonSchemaObject, str, frozenset[str], bool],
            ],
        ] = {}
        self._raw_inherited_fields_cache: dict[str, tuple[DataModelFieldBase, ...]] = {}
        self._raw_inherited_own_names_cache: dict[str, frozenset[str]] = {}
        self._request_response_fields: dict[str, tuple[DataModelFieldBase, ...]] = {}
        self._rw_model_field_facts_cache: dict[str, tuple[bool, bool, bool, bool]] = {}
        self._rw_model_references_cache: dict[str, tuple[bool, tuple[str, ...]]] = {}
        self._rw_model_variant_requirement_cache: dict[tuple[str, str], bool] = {}
        self._rw_model_variant_references: dict[tuple[str, str], Reference] = {}
        self._local_ref_path_cache: dict[Path, Path] = {}
        if self.generate_schema_validators:
            self._property_count_rule_cache: dict[int, tuple[JsonSchemaObject, PropertyCountRule | None]] = {}
        self.field_keys: set[str] = {
            *DEFAULT_FIELD_KEYS,
            *self.field_extra_keys,
            *self.field_extra_keys_without_x_prefix,
        }
        self._circular_ref_cache: dict[str, bool] = {}

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
    def schema_features(self) -> JsonSchemaFeatures:
        """Get schema features based on config or detected version."""
        config_version = getattr(self.config, "jsonschema_version", None)
        if config_version is not None and config_version != JsonSchemaVersion.Auto:
            return JsonSchemaFeatures.from_version(config_version)
        version = detect_jsonschema_version(self.raw_obj) if self.raw_obj else JsonSchemaVersion.Auto
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
        """Determine if an enum should be parsed as a literal type.

        Priority (highest to lowest):
        1. x-enum-field-as-literal on the property schema
        2. enum_field_as_literal_map matching Model.field or field
        3. Global enum_field_as_literal setting
        """
        # Check x-enum-field-as-literal on property or obj
        target_obj = property_obj if property_obj is not None else obj
        if target_obj.x_enum_field_as_literal is not None:
            return target_obj.x_enum_field_as_literal

        # Check enum_field_as_literal_map for matching keys
        if property_name and self.enum_field_as_literal_map and property_name in self.enum_field_as_literal_map:
            return self.enum_field_as_literal_map[property_name] == "literal"

        # Fall back to global setting
        if self.enum_field_as_literal == LiteralType.All:
            return True
        if self.enum_field_as_literal == LiteralType.One:
            return len(obj.enum) == 1
        return False

    @classmethod
    def _extract_const_enum_from_combined(  # noqa: PLR0912
        cls, items: list[JsonSchemaObject | bool], parent_type: str | list[str] | None
    ) -> tuple[list[Any], list[str], list[str], str | None, bool] | None:
        """Extract enum values from oneOf/anyOf const pattern."""
        enum_values: list[Any] = []
        varnames: list[str] = []
        descriptions: list[str] = []
        nullable = False
        inferred_type: str | None = None

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

            if item.title:
                varnames.append(item.title)
            descriptions.append(item.description or "")

            if inferred_type is None and const_value is not None:
                match const_value:
                    case str():
                        inferred_type = "string"
                    case bool():  # bool must come before int (bool is subclass of int)
                        inferred_type = "boolean"
                    case int():
                        inferred_type = "integer"
                    case float():
                        inferred_type = "number"

        if not enum_values:  # pragma: no cover
            return None

        final_type = inferred_type
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

        return (enum_values, varnames, descriptions, final_type, nullable)

    def _create_synthetic_enum_obj(  # noqa: PLR0913, PLR0917
        self,
        original: JsonSchemaObject,
        enum_values: list[Any],
        varnames: list[str],
        descriptions: list[str],
        enum_type: str | None,
        nullable: bool,  # noqa: FBT001
    ) -> JsonSchemaObject:
        """Create a synthetic JsonSchemaObject for enum parsing."""
        final_enum = [*enum_values, None] if nullable else enum_values
        final_varnames = varnames if len(varnames) == len(enum_values) else []
        enum_metadata = {"x-enum-varnames": final_varnames}
        if any(descriptions):
            enum_metadata["x-enum-descriptions"] = descriptions

        return self.SCHEMA_OBJECT_TYPE(
            type=enum_type,
            enum=final_enum,
            nullable=nullable,
            title=original.title,
            description=original.description,
            **(enum_metadata | ({"default": original.default} if original.has_default else {})),
        )

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
                constraint_fields = _NUMBER_CONSTRAINT_KEYS
            case "string":
                constraint_fields = _VALUE_STRING_CONSTRAINT_KEYS
            case _:
                return False
        return any(field in obj.model_fields_set for field in constraint_fields)

    def _get_constraint_values(self, obj: JsonSchemaObject) -> dict[str, Any]:  # noqa: PLR6301
        """Return JSON Schema constraint values without serializing nested schemas."""
        return {
            constraint: value
            for constraint in obj.__constraint_field_order__
            if (value := getattr(obj, constraint)) is not None
        }

    def _is_fixed_length_tuple(self, obj: JsonSchemaObject) -> bool:
        """Check if an array field represents a fixed-length tuple."""
        if obj.prefixItems is not None and (obj.items is None or obj.items is False):
            if any(item is False for item in obj.prefixItems):
                return False
            return obj.minItems == obj.maxItems == len(obj.prefixItems)
        if self.use_tuple_for_fixed_items and isinstance(obj.items, list) and obj.prefixItems is None:
            if any(item is False for item in obj.items):
                return False
            return obj.minItems == obj.maxItems == len(obj.items)
        return self._get_fixed_length_homogeneous_tuple_length(obj) is not None

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
    def _get_fixed_length_prefix_tuple_items(cls, obj: JsonSchemaObject) -> list[JsonSchemaObject | bool] | None:
        """Return positional item schemas for fixed-length prefixItems arrays."""
        if (
            obj.prefixItems is None
            or obj.minItems is None
            or obj.maxItems is None
            or obj.minItems != obj.maxItems
            or obj.minItems < 0
        ):  # pragma: no cover
            return None

        tuple_length = obj.minItems
        prefix_items = obj.prefixItems
        if any(item is False for item in prefix_items[:tuple_length]):  # pragma: no cover
            return None

        items = [*prefix_items[:tuple_length]]
        if len(items) == tuple_length:
            return items

        if isinstance(obj.items, (JsonSchemaObject, bool)):
            tail_item: JsonSchemaObject | bool = obj.items
        elif isinstance(obj.unevaluatedItems, (JsonSchemaObject, bool)):
            tail_item = obj.unevaluatedItems
        else:
            tail_item = True
        if tail_item is False:  # pragma: no cover
            return None

        items.extend(tail_item for _ in range(tuple_length - len(items)))
        return items

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

    def _get_array_item_schemas(  # noqa: PLR0911
        self,
        obj: JsonSchemaObject,
        *,
        include_true_tail_schema: bool = False,
        force_prefix_items: bool = False,
    ) -> tuple[list[JsonSchemaObject | bool], bool, bool, int | None]:
        """Return item schemas plus tuple/constraint flags and homogeneous tuple length."""
        if (
            obj.prefixItems is not None
            and obj.minItems is not None
            and obj.minItems == obj.maxItems
            and (fixed_items := self._get_fixed_length_prefix_tuple_items(obj)) is not None
        ):
            return fixed_items, True, True, None

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
            return items, False, False, None

        match obj.items:
            case JsonSchemaObject() as item_schema:
                if (tuple_item_count := self._get_fixed_length_homogeneous_tuple_length(obj)) is not None:
                    return [item_schema] if tuple_item_count else [], True, True, tuple_item_count
                return [item_schema], False, False, None
            case list() as item_schemas:
                items, has_false_schema = self._get_schemas_before_false(item_schemas)
                if self._is_fixed_length_tuple(obj):
                    return items, True, True, None
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
                return items, False, False, None

        match obj.unevaluatedItems:
            case JsonSchemaObject() as item_schema:
                return [item_schema], False, False, None
            case True if include_true_tail_schema:
                return [True], False, False, None

        return [], False, False, None

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

    def _get_array_constraints(self, obj: JsonSchemaObject) -> dict[str, Any]:
        """Return direct and boolean-schema array constraints."""
        constraints = self._get_constraint_values(obj)
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
        fixed_prefix_tuple_items = (
            self._get_fixed_length_prefix_tuple_items(obj)
            if obj.prefixItems is not None and obj.minItems is not None and obj.minItems == obj.maxItems
            else None
        )
        if self._is_fixed_length_tuple(obj) or fixed_prefix_tuple_items is not None:
            constraints.pop("minItems", None)
            constraints.pop("maxItems", None)

    def _resolve_array_field_required_nullable(self, obj: JsonSchemaObject) -> tuple[bool, bool | None]:
        if self.force_optional_for_required_fields:
            return False, None

        required = not obj.has_default
        if self.strict_nullable:
            return required, obj.nullable if obj.has_default or required else True

        required = not obj.nullable and required
        if obj.nullable:
            return required, True
        if obj.has_default:
            return required, False
        return required, None

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

    def _get_const_data_type(self, const: object) -> DataType:
        """Return a DataType for a JSON Schema const value."""
        if isinstance(const, bool):
            if not self._output_model_context.supports_boolean_literals:
                return self.data_type_manager.get_data_type(Types.boolean)
            return self.data_type(literals=[const])
        if isinstance(const, (int, str)):
            return self.data_type(literals=[const])
        return self._get_data_type_from_json_value(const)

    def _partition_enum_values(self, enum_values: list[Any]) -> tuple[list[JsonSchemaLiteral], list[object], bool]:
        """Split enum values into literal and non-literal values."""
        literal_values: list[JsonSchemaLiteral] = []
        non_literal_values: list[object] = []
        has_null = False
        for enum_value in enum_values:
            if enum_value is None:
                has_null = True
                non_literal_values.append(enum_value)
            elif isinstance(enum_value, bool) and not self._output_model_context.supports_boolean_literals:
                non_literal_values.append(enum_value)
            elif isinstance(enum_value, (bool, int, str)):
                literal_values.append(enum_value)
            else:
                non_literal_values.append(enum_value)
        return literal_values, non_literal_values, has_null

    def _resolve_field_flag(
        self,
        obj: JsonSchemaObject,
        flag: Literal["readOnly", "writeOnly"],
        active: frozenset[str] = frozenset(),
    ) -> bool:
        """Resolve a field flag (readOnly/writeOnly) from direct value, $ref, and compositions."""
        if getattr(obj, flag) is True:
            return True
        if (
            self.read_only_write_only_model_type
            and obj.ref
            and (resolved_ref := self.model_resolver.resolve_ref(obj.ref)) not in active
        ):
            referenced_schema = self._load_ref_schema_object(obj.ref)
            with self._inherited_ref_context(resolved_ref):
                if self._resolve_field_flag(
                    referenced_schema,
                    flag,
                    active | {resolved_ref},
                ):
                    return True
        for schemas in (obj.allOf, obj.anyOf, obj.oneOf):
            for sub in schemas:
                if isinstance(sub, JsonSchemaObject) and self._resolve_field_flag(sub, flag, active):
                    return True
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
        """Collect parent → child fields, with each child declaration overriding its parent."""
        inherited_fields = self._collect_inherited_fields_for_request_response(base_classes or [])
        if not inherited_fields:
            return fields
        if not fields:
            return inherited_fields
        return self._merge_inherited_field_overrides(inherited_fields, fields)

    def _get_separate_model_fields(
        self,
        fields: list[DataModelFieldBase],
        base_classes: list[Reference] | None,
    ) -> list[DataModelFieldBase] | None:
        """Collect Request/Response fields once when separate models are needed."""
        if self.read_only_write_only_model_type is None:
            return None
        all_fields = self._collect_all_fields_for_request_response(fields, base_classes)
        if any(field.read_only or field.write_only for field in all_fields):
            return all_fields
        if (
            self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse
            and self._fields_reference_rw_model_variant(all_fields, "Request")
        ):
            return all_fields
        return None

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
        ref_path: str,
    ) -> tuple[bool, bool, bool, bool] | None:
        """Return cached effective read/write field facts for a referenced schema."""
        resolved_ref = self.model_resolver.resolve_ref(ref_path)
        if (cached := self._rw_model_field_facts_cache.get(resolved_ref)) is not None:
            return cached
        try:
            reference = self.model_resolver.add_ref(resolved_ref, resolved=True)
            fields = self._get_raw_inherited_fields(reference, frozenset())
        except Exception:  # noqa: BLE001  # pragma: no cover
            return None
        result = self._get_rw_model_field_facts(fields)
        self._rw_model_field_facts_cache[resolved_ref] = result
        return result

    def _ref_schema_generates_variant(
        self,
        ref_path: str,
        suffix: Literal["Request", "Response"],
    ) -> bool:
        """Check whether a referenced schema generates the requested read/write variant."""
        resolved_ref = self.model_resolver.resolve_ref(ref_path)
        cache_key = resolved_ref, suffix
        if (cached := self._rw_model_variant_requirement_cache.get(cache_key)) is not None:
            return cached
        if _is_rw_model_variant_path(resolved_ref):
            return False
        if self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
            return self._request_response_ref_schema_generates_variant(resolved_ref)
        if (facts := self._get_ref_schema_rw_model_field_facts(ref_path)) is None:
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
        """Return direct split facts and deduplicated outgoing model references."""
        facts = self._get_ref_schema_rw_model_field_facts(resolved_ref)
        has_direct_variant = facts is not None and (facts[0] or facts[1])
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
        return has_direct_variant or has_inline_variant, tuple(nested_refs)

    def _fields_reference_rw_model_variant(
        self,
        fields: Iterable[DataModelFieldBase],
        suffix: Literal["Request", "Response"],
    ) -> bool:
        """Return whether any nested field reference needs the requested variant."""
        for field in fields:
            for data_type in field.data_type.all_data_types:
                if (reference := data_type.reference) and self._ref_schema_generates_variant(
                    reference.path,
                    suffix,
                ):
                    return True
        return False

    def _iter_rw_model_schema_children(
        self,
        schema: JsonSchemaObject,
        *,
        detect_inline_variant: bool,
    ) -> Iterator[tuple[JsonSchemaObject, bool]]:
        """Yield type-contributing children and whether their inline models are generated."""
        mappings = (
            (schema.properties,) if self.generate_schema_validators else (schema.properties, schema.patternProperties)
        )
        for mapping in mappings:
            if mapping:
                yield from (
                    (item, detect_inline_variant) for item in mapping.values() if isinstance(item, JsonSchemaObject)
                )
        match schema.items:
            case JsonSchemaObject() as item:
                yield item, detect_inline_variant
            case list() as items:
                yield from ((item, detect_inline_variant) for item in items if isinstance(item, JsonSchemaObject))
        for item in (
            schema.additionalItems,
            schema.additionalProperties,
        ):
            if isinstance(item, JsonSchemaObject):
                yield item, detect_inline_variant
        yield from (
            (item, detect_inline_variant)
            for item in chain(schema.prefixItems or (), schema.oneOf, schema.anyOf, schema.allOf)
            if isinstance(item, JsonSchemaObject)
        )
        if self.generate_schema_validators:
            yield from ((item, detect_inline_variant) for item in self._iter_conditional_branches(schema))

    def _get_ref_schema_rw_model_reference_facts(
        self,
        resolved_ref: str,
    ) -> tuple[bool, tuple[str, ...]]:
        """Return inline read/write presence and canonical refs independent of parse order."""
        if (cached := self._rw_model_references_cache.get(resolved_ref)) is not None:
            return cached
        try:
            root_schema = self._load_inherited_schema_object(resolved_ref)
        except Exception:  # noqa: BLE001  # pragma: no cover
            return False, ()

        has_inline_variant = False
        references: dict[str, None] = {}
        stack = [(root_schema, True)]
        while stack:
            schema, detect_inline_variant = stack.pop()
            if (
                detect_inline_variant
                and not has_inline_variant
                and self._schema_has_rw_model_fields(schema, resolved_ref)
            ):
                has_inline_variant = True
            for ref in (schema.ref, schema.recursiveRef, schema.dynamicRef):
                if not ref:
                    continue
                if nested_ref := self._resolve_rw_model_reference(ref, resolved_ref):
                    references.setdefault(nested_ref, None)
            property_name_refs, property_name_schemas = self._get_rw_property_name_sources(
                property_names=schema.propertyNames,
                defining_ref=resolved_ref,
            )
            for ref in property_name_refs:
                if nested_ref := self._resolve_rw_model_reference(ref, resolved_ref):
                    references.setdefault(nested_ref, None)
            stack.extend((item, True) for item in property_name_schemas)
            stack.extend(
                self._iter_rw_model_schema_children(
                    schema,
                    detect_inline_variant=detect_inline_variant,
                )
            )
        result = has_inline_variant, tuple(references)
        self._rw_model_references_cache[resolved_ref] = result
        return result

    def _resolve_rw_model_reference(self, ref: str, defining_ref: str) -> str | None:
        """Resolve a type-producing ref while keeping configured imports as graph leaves."""
        with self._inherited_ref_context(defining_ref):
            if self._resolve_external_ref_mapping(ref) is not None:
                return None
            return self.model_resolver.resolve_ref(ref)

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

        source_variant_ref = _get_rw_model_variant_source_path(base_reference, suffix)
        source_variant_exists = self._ref_schema_exists(source_variant_ref)
        if source_variant_exists:
            self.model_resolver.add_ref(source_variant_ref, resolved=True)

        class_name_suffix = self.model_resolver.class_name_suffix
        base_name_without_index = base_reference.name.rstrip(digits)
        numeric_index = base_reference.name.removeprefix(base_name_without_index)
        variant_name = (
            f"{base_name_without_index[: -len(class_name_suffix)]}{suffix}{class_name_suffix}{numeric_index}"
            if class_name_suffix and base_name_without_index.endswith(class_name_suffix)
            else f"{base_reference.name}{suffix}"
        )
        unique_name = self.model_resolver.get_class_name(
            variant_name,
            unique=True,
            skip_affix=True,
            preserve_name=True,
        ).name
        reference = self.model_resolver.add(
            get_special_path(f"read-write-{suffix.lower()}", base_reference.path.split("/")),
            unique_name,
            class_name=False,
            unique=False,
            loaded=loaded,
        )
        source_reference_path = _get_unique_rw_model_variant_source_path(
            source_variant_ref,
            variant_name,
            unique_name,
            collides_with_source=source_variant_exists,
        )
        while source_variant_exists and self._ref_schema_exists(source_reference_path):
            source_reference_path = f"{source_reference_path}1"
        reference.__dict__[_SOURCE_REFERENCE_PATH_KEY] = source_reference_path
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
                self.generation_store.replace_data_type_ref(nested_data_type, variant_ref)

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
            additional_type.type_hint,
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
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)
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
            nullable=obj.type_has_null,
            keyword_only=self.keyword_only,
            treat_dot_as_module=self.treat_dot_as_module,
            dataclass_arguments=self.dataclass_arguments,
        )
        model.__dict__[_SOURCE_REFERENCE_PATH_KEY] = reference.__dict__.get(
            _SOURCE_REFERENCE_PATH_KEY,
            _get_rw_model_variant_source_path(base_reference, suffix),
        )
        self.generation_store.register_model(model)

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
        if not skip_constraints and self.is_constraints_field(field):
            constraints = self._get_constraint_values(field)
        consumed = self.data_type_manager.CONSTRAINED_TYPE_CONSUMED_KEYS
        if constraints is not None and field_type.type in consumed:
            for key in consumed[field_type.type]:
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
        if not skip_constraints and (array_items_constraints := self._get_array_items_constraints(field)):
            constraints = constraints or {}
            constraints.update(array_items_constraints)
        self._suppress_array_length_constraints(constraints, field)
        nullable = (
            field.nullable
            if (self.strict_nullable or self.use_missing_sentinel) and field.nullable is not None
            else (False if self.strict_nullable and (has_default or required) else None)
        )
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
            extras=extras,
            use_annotated=self.use_annotated,
            use_serialize_as_any=self.use_serialize_as_any,
            use_field_description=self.use_field_description,
            use_field_description_example=self.use_field_description_example,
            use_inline_field_description=self.use_inline_field_description,
            use_default_kwarg=self.use_default_kwarg,
            original_name=original_field_name,
            has_default=has_default,
            type_has_null=field.type_has_null,
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
        python_type_override = self._get_python_type_override(obj)
        if python_type_override:  # pragma: no cover
            return python_type_override

        if "const" in obj.extras:
            return self._get_const_data_type(obj.extras["const"])

        if obj.type is None:
            return self.data_type_manager.get_data_type(
                Types.any,
            )

        def _get_data_type(type_: str, format__: str) -> DataType:
            types = self._get_type_with_mappings(type_, format__)
            kwargs_to_pass: dict[str, JsonSchemaDataTypeKwargValue]
            if self.field_constraints and not localize_constraints:
                # To prevent type manager from generating conint/confloat,
                # we only pass constraints that perfectly match specialized types
                # (like NonNegativeInt -> minimum: 0).
                # Other constraints should remain on Field(), so we pass {}
                kwargs_to_pass = {}
                number_kwargs: dict[str, int | float | bool] = {}
                for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                    value = getattr(obj, key)
                    if value is not None:
                        number_kwargs[key] = value.value if isinstance(value, UnionIntFloat) else value

                if self.data_type_manager.use_non_positive_negative_number_constrained_types:
                    zero_bound_keys = [k for k, v in number_kwargs.items() if v == 0]
                    if len(zero_bound_keys) == 1:
                        key = zero_bound_keys[0]
                        kwargs_to_pass = {key: number_kwargs[key]}
            else:
                kwargs_to_pass = {}
                kwargs_to_pass.update(_get_data_type_constraint_kwargs(obj, types))

            if types == Types.binary and self._is_base64_encoded_binary_mapping(type_, format__):
                kwargs_to_pass["base64_encoded"] = True

            return self.data_type_manager.get_data_type(
                types,
                field_constraints=self.field_constraints and not localize_constraints,
                **kwargs_to_pass,
            )

        if isinstance(obj.type, list):
            data_types = [_get_data_type(t, obj.format or "default") for t in obj.type if t != "null"]
            return (
                self.data_type(
                    data_types=data_types,
                    is_optional=len(data_types) != len(obj.type),
                )
                if data_types
                else self.data_type_manager.get_data_type(Types.null)
            )
        data_type = _get_data_type(obj.type, obj.format or "default")
        if self.strict_nullable and obj.nullable:
            return self.data_type(data_types=[data_type], is_optional=True)
        return data_type

    def _resolve_external_ref_mapping(self, ref: str) -> tuple[str, str] | None:
        """Resolve a ref and return mapped package + fragment if configured."""
        if not self._external_ref_mapping:
            return None

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
        if mapped is None:
            return None
        python_package, fragment = mapped

        # Extract and normalize class name from fragment to match generated model naming.
        raw_name = unescape_json_pointer_segment(fragment.rstrip("/").rsplit("/", maxsplit=1)[-1])
        if not raw_name:
            return None
        class_name = self.model_resolver.get_class_name(raw_name, unique=False).name

        # Construct import — same pattern as x-python-import
        full_path = f"{python_package}.{class_name}"
        import_ = Import.from_full_path(full_path)
        self.imports.append(import_)
        return self.data_type.from_import(import_)

    def _get_x_python_import_path(self, x_python_import: dict[str, Any]) -> str | None:  # noqa: PLR6301
        module = x_python_import.get("module")
        type_name = x_python_import.get("name")
        if not module and not type_name:
            return None
        if not module or not type_name:
            msg = "x-python-import requires both module and name"
            raise Error(msg)
        return _validate_schema_python_import_path(f"{module}.{type_name}", "x-python-import")

    def _get_x_python_runtime_symbol(  # ruff: ignore[no-self-use]
        self, x_python_import: dict[str, Any]
    ) -> PythonTypeRuntimeSymbol | None:
        """Retain nested runtime module boundaries; snooper requires an instance method."""
        if (qualname := x_python_import.get("qualname")) is None:
            return None
        from datamodel_code_generator._python_type_annotation import (  # ruff: ignore[import-outside-top-level]
            PythonTypeRuntimeSymbol,
        )

        qualname = _validate_schema_python_import_path(qualname, "x-python-import qualname")
        return PythonTypeRuntimeSymbol(x_python_import["module"], tuple(qualname.split(".")))

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
                PythonTypeRuntimeSymbol,
            )

            expression: PythonTypeExpr = PythonTypeRuntimeSymbol(path["module"], tuple(path["qualname"].split(".")))
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

        expression = cast(
            "PythonTypeExpr", self._get_x_python_type(self.SCHEMA_OBJECT_TYPE.model_validate(x_python_import))
        )
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

    def _cache_ref_data_type_facts(self, resolved_ref: str, obj: JsonSchemaObject) -> None:
        self._ref_data_type_facts[resolved_ref] = (
            obj.extras.get("x-python-import"),
            obj.type == "null" or (self.strict_nullable and obj.nullable is True),
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

        ref = self._normalize_external_ref(ref)
        resolved_ref = self.model_resolver.resolve_ref(ref)
        if (facts := self._ref_data_type_facts.get(resolved_ref)) is None:
            ref_schema = self._load_ref_schema_object(ref)
            facts = (
                ref_schema.extras.get("x-python-import"),
                ref_schema.type == "null" or (self.strict_nullable and ref_schema.nullable is True),
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
            self.imports.append(import_)
            return self.data_type(type=import_.import_, import_=import_, alias=import_.alias)
        if not self._dynamic_anchors or (
            (reference := self._dynamic_specialization_reference(ref, resolved_ref)) is None
        ):
            reference = self.model_resolver.add_ref(ref)
        return self.data_type(reference=reference, is_optional=is_optional)

    def set_additional_properties(self, path: str, obj: JsonSchemaObject) -> None:
        """Set additional properties flag in extra template data.

        For TypedDict with PEP 728 support:
        - additionalProperties: false -> closed=True
        - additionalProperties: { type: X } -> extra_items=X

        This is controlled by use_closed_typed_dict option. When disabled,
        the additionalProperties constraint is not converted to PEP 728 syntax.
        """
        if isinstance(obj.additionalProperties, bool):
            if not self.use_closed_typed_dict:
                return
            self._output_model_context.store_additional_properties_value(
                self.extra_template_data[path],
                value=obj.additionalProperties,
                target_python_version=self.target_python_version,
            )
        elif isinstance(obj.additionalProperties, JsonSchemaObject):
            # A schema-valued additionalProperties still means extra keys are accepted.
            # Keep typed extra validation out of this bugfix; PEP 728 TypedDict uses
            # output-owned type metadata below when explicitly enabled.
            self._output_model_context.store_additional_properties_value(
                self.extra_template_data[path],
                value=True,
            )
            if not self.use_closed_typed_dict:
                return
            additional_props_type = self._build_lightweight_type(obj.additionalProperties)
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
                    additional_props_type.type_hint,
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

        target_version = self.target_python_version.version_key
        try:
            import_path = get_python_type_import_path(type_name, target_version)
        except PythonTypeUnavailableError as exc:
            target = ".".join(map(str, target_version))
            msg = f"{exc.type_name} is unavailable for target Python {target}"
            raise Error(msg) from exc
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
        from datamodel_code_generator._python_type_import_registry import (  # noqa: PLC0415
            PythonTypeUnavailableError,
            get_qualified_python_type_import_path,
        )

        target_version = self.target_python_version.version_key
        try:
            import_path = get_qualified_python_type_import_path(qualified_name, target_version)
        except PythonTypeUnavailableError as exc:
            target = ".".join(map(str, target_version))
            msg = f"{exc.type_name} is unavailable for target Python {target}"
            raise Error(msg) from exc
        return _qualified_python_type_import(import_path)

    def _resolve_type_import_from_defs(self, type_name: str) -> Import | PythonTypeExpr | None:
        """Resolve import for a type name from $defs with x-python-import."""
        try:
            ref_schema = self._load_ref_schema_object(f"#/$defs/{type_name}")
            x_python_import = ref_schema.extras.get("x-python-import")
            if isinstance(x_python_import, dict) and (full_path := self._get_x_python_import_path(x_python_import)):
                return self._get_x_python_import_expression(
                    x_python_import, self.model_resolver.resolve_ref(f"#/$defs/{type_name}")
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
                            return rewrite_python_type_expr(resolved, bind_leaf)
                        finally:
                            resolving_names.remove(name)
                    if resolved:
                        import_, bound_name = bind_import(resolved)
                        return PythonTypeBoundName(bound_name, import_.from_, import_.import_)
            return item

        return BoundPythonType(rewrite_python_type_expr(expression, bind_leaf), tuple(imports))

    def _get_python_type_override(self, obj: JsonSchemaObject) -> DataType | None:
        """Get DataType from x-python-type if it's incompatible with schema type."""
        if (python_type := self._get_x_python_type(obj)) is None:
            return None

        schema_type = obj.type if isinstance(obj.type, str) else None
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
        seen_refs: set[str] | None = None,
    ) -> JsonSchemaLiteral | None:
        if "const" in obj.extras:
            const = obj.extras["const"]
            return const if isinstance(const, (bool, int, str)) else None
        if len(obj.enum) == 1 and isinstance(obj.enum[0], (bool, int, str)):
            return obj.enum[0]
        if obj.ref:
            seen_refs = seen_refs or set()
            resolved_ref = self.model_resolver.resolve_ref(obj.ref)
            if self._resolve_external_ref_mapping(obj.ref) or resolved_ref in seen_refs:
                return None
            seen_refs.add(resolved_ref)
            return self._get_single_literal_value(self._load_ref_schema_object(obj.ref), seen_refs)
        return None

    def _get_union_variant_literal_values(
        self,
        combined_schemas: Sequence[JsonSchemaObject],
        field_name: str,
    ) -> dict[int, JsonSchemaLiteral] | None:
        values: dict[int, JsonSchemaLiteral] = {}
        for index, item in enumerate(combined_schemas):
            if not item.properties:
                continue
            field = item.properties.get(field_name)
            if not isinstance(field, JsonSchemaObject):
                return None
            value = self._get_single_literal_value(field)
            if value is None:
                return None
            values[index] = value

        if len(values) < _MIN_UNION_VARIANT_LITERAL_VALUES:
            return None
        unique_values = {_literal_uniqueness_key(value) for value in values.values()}
        return values if len(unique_values) == len(values) else None

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

    def _infer_union_variant_names(
        self,
        name: str,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
    ) -> list[str | None] | None:
        for field_name in self._iter_union_variant_literal_field_names(obj, combined_schemas):
            values = self._get_union_variant_literal_values(combined_schemas, field_name)
            if values is None:
                continue
            variant_names: list[str | None] = [None] * len(combined_schemas)
            for index, literal in values.items():
                variant_names[index] = _get_union_variant_name(name, literal)
            generated_names = [variant_name for variant_name in variant_names if variant_name]
            if len(set(generated_names)) != len(generated_names):
                continue
            return variant_names
        return None

    def _get_inferred_union_variant_names(
        self,
        name: str,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
    ) -> list[str | None] | None:
        if not self.infer_union_variant_names:
            return None
        return self._infer_union_variant_names(name, obj, combined_schemas)

    def _get_tagged_union_field_values(
        self,
        obj: JsonSchemaObject,
        combined_schemas: Sequence[JsonSchemaObject],
    ) -> tuple[str, dict[int, TaggedUnionValue]] | None:
        """Return the shared required literal field and supported values for a tagged union."""
        discriminator_schemas = [
            self._load_ref_schema_object(item.ref) if item.ref and item.ref_type == JSONReference.LOCAL else item
            for item in combined_schemas
        ]
        for field_name in self._iter_union_variant_literal_field_names(obj, discriminator_schemas):
            if (literal_values := self._get_union_variant_literal_values(discriminator_schemas, field_name)) is None:
                continue
            if len(literal_values) != len(discriminator_schemas):
                continue

            tag_values: dict[int, TaggedUnionValue] = {}
            for index, literal in literal_values.items():
                match discriminator_schemas[index]:
                    case JsonSchemaObject(required=required) if field_name in required:
                        pass
                    case _:
                        break
                if (tag_value := _get_tagged_union_value(literal)) is None:
                    break
                tag_values[index] = tag_value
            else:
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
        tag_field, _tag_values = tag_data
        match obj.extras.get("discriminator"):
            case dict() as discriminator:
                discriminator["propertyName"] = tag_field
            case _:
                obj.extras["discriminator"] = {"propertyName": tag_field}

    def _parse_combined_schema_items(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        combined_schemas: Sequence[JsonSchemaObject],
        variant_names: Sequence[str | None] | None,
    ) -> list[DataType]:
        if variant_names:
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
                    result[key] = self._deep_merge(result[key], value)
                    continue
                if isinstance(result[key], list) and isinstance(value, list):
                    result[key] = result[key] + value  # noqa: PLR6104
                    continue
            result[key] = value
        return result

    def _get_ref_raw_schema(self, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
        file_part, fragment = ([*resolved_ref.split("#", 1), ""])[:2]
        raw_doc = self._get_ref_body(file_part) if file_part else self.raw_obj

        target_schema: dict[str, YamlValue] | YamlValue = raw_doc
        if fragment:
            pointer = split_json_pointer(raw_doc, fragment)
            target_schema = _get_model_by_decoded_path(raw_doc, pointer)
        return target_schema

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
        resolved_ref = self.model_resolver.resolve_ref(ref)
        return self._validate_schema_object(self._get_ref_raw_schema(resolved_ref), [resolved_ref])

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

        resolved_ref = self.model_resolver.resolve_ref(ref)
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

    def _resolve_recursive_ref(self, item: JsonSchemaObject, path: list[str]) -> str | None:
        """Resolve $recursiveRef to an equivalent $ref.

        Per JSON Schema 2019-09, $recursiveRef only allows "#" as value.
        Resolves to the nearest enclosing schema with $recursiveAnchor: true.
        For standalone JSON Schema files, this is the root "#".
        For OpenAPI, this is the component schema definition path.
        """
        if item.recursiveRef != "#":  # pragma: no cover
            return None
        root_key = tuple(self.model_resolver.current_root)
        anchors = self._recursive_anchor_index.get(root_key, [])
        if not anchors:
            return "#"
        current_ref = self._anchor_ref_path(root_key, path)
        best = "#"
        best_len = 0
        for anchor_ref in anchors:
            if anchor_ref != "#" and (
                len(anchor_ref) > best_len
                and current_ref.startswith(anchor_ref)
                and (len(current_ref) == len(anchor_ref) or current_ref[len(anchor_ref)] == "/")
            ):
                best = anchor_ref
                best_len = len(anchor_ref)
        return best

    def _resolve_dynamic_ref(self, item: JsonSchemaObject) -> str | None:
        """Resolve $dynamicRef to an equivalent $ref.

        Per JSON Schema 2020-12:
        1. Resolve the URI like $ref first (fallback behavior)
        2. If target has $dynamicAnchor, override with outermost matching anchor

        In code generation, dynamic scope is resolved statically via index lookup.
        """
        ref = item.dynamicRef
        if not ref:  # pragma: no cover
            return None
        if ref.startswith("#"):
            anchor_name = ref[1:]
            root_key = tuple(self.model_resolver.current_root)
            anchor_map = self._dynamic_anchor_index.get(root_key, {})
            if anchor_name in anchor_map:
                return anchor_map[anchor_name]
            return ref  # pragma: no cover
        return ref  # pragma: no cover

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

        Results are memoized per strongly connected component of the reference graph, so each schema
        is analyzed once however many references reach it.
        """
        if (cached := self._dynamic_anchor_usage.get(resolved_ref)) is not None:
            return cached
        index = {resolved_ref: 0}
        lowlink = {resolved_ref: 0}
        position = {resolved_ref: 0}
        stack = [resolved_ref]
        work = [(resolved_ref, iter(self._dynamic_ref_edges(resolved_ref)[1]))]
        while work:
            node, successors = work[-1]
            for successor in successors:
                if successor in self._dynamic_anchor_usage:
                    continue
                if successor not in index:
                    index[successor] = lowlink[successor] = len(index)
                    position[successor] = len(stack)
                    stack.append(successor)
                    work.append((successor, iter(self._dynamic_ref_edges(successor)[1])))
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
                        local_anchors, targets = self._dynamic_ref_edges(member)
                        anchors |= local_anchors
                        anchors.update(
                            *(self._dynamic_anchor_usage[target] for target in targets if target not in members)
                        )
                    self._dynamic_anchor_usage.update(dict.fromkeys(component, frozenset(anchors)))
        return self._dynamic_anchor_usage[resolved_ref]

    def _dynamic_ref_edges(self, resolved_ref: str) -> tuple[frozenset[str], tuple[str, ...]]:
        """Return the anchors a schema's $dynamicRef initially resolve to and the schemas it references."""
        if (cached := self._dynamic_ref_graph.get(resolved_ref)) is not None:
            return cached
        anchors: set[str] = set()
        targets: list[str] = []
        file_part, _, fragment = resolved_ref.partition("#")
        document, raw = self._prepared_ref_document(file_part)
        schema = _get_model_by_path_or_missing(raw, split_json_pointer(raw, fragment)) if fragment else raw
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
        self._dynamic_ref_graph[resolved_ref] = edges = (frozenset(anchors), tuple(targets))
        return edges

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
            return self.model_resolver.resolve_ref(self._normalize_external_ref(ref))

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
        if target_document == self._schema_resource_document(list(self.model_resolver.current_root)):
            yield f"#{pointer}"
            return
        with self._schema_document_context([]), self.root_id_context({}):
            yield self._location_ref(location)

    def _dynamic_specialization_reference(self, ref: str, resolved_ref: str) -> Reference | None:
        """Return a model of the referenced schema specialized for the current dynamic scope, if it differs.

        The shared model is registered first so it keeps naming priority. The specialized model is
        registered under the referencing model and named like the shared model before parsing, so
        parsing names both the same way once the referencing model is named.
        """
        if not (bindings := self._dynamic_target_bindings(resolved_ref)):
            return None
        key = (resolved_ref, frozenset(bindings.items()))
        if (reference := self._dynamic_specializations.get(key)) is not None:
            return reference
        shared = self.model_resolver.add_ref(ref)
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

    def _get_dynamic_ref_data_type(self, item: JsonSchemaObject, dynamic_ref: str) -> DataType:
        """Return the data type of a $dynamicRef resolved in the current dynamic scope.

        In code generation, the dynamic scope is the chain of schema resources that referenced the
        model being parsed; schemas reached under a different scope are parsed again for it.
        """
        if (location := self._bound_dynamic_anchor(dynamic_ref)) is None:
            return self.get_ref_data_type(self._resolve_dynamic_ref(item) or dynamic_ref)
        with self._schema_location_context(location) as ref:
            return self.get_ref_data_type(ref)

    @contextmanager
    def _dynamic_scope_context(
        self,
        path: Sequence[str],
        bindings: dict[str, str],
        pointer: str | None = None,
    ) -> Generator[None, None, None]:
        """Track the model root being parsed and the $dynamicAnchor bindings of its dynamic scope.

        The pointer locates the schema in its own document; it differs from the model path only for
        schemas parsed again under another dynamic scope.
        """
        previous = self._dynamic_document, self._dynamic_scope_path, self._dynamic_scope_pointer, self._dynamic_bindings
        self._dynamic_document = self._schema_resource_document(list(self.model_resolver.current_root))
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

    def _merge_ref_with_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        """Merge $ref schema with current schema's additional keywords.

        JSON Schema 2020-12 allows $ref alongside other keywords,
        which should be merged together.

        The local keywords take precedence over referenced schema.
        """
        if not obj.ref:
            return obj

        resolved_ref = self.model_resolver.resolve_ref(obj.ref)
        if self._is_ref_circular(resolved_ref):
            return obj

        ref_schema = self._load_ref_schema_object(obj.ref)
        ref_dict = ref_schema.model_dump(exclude_unset=True, by_alias=True)
        current_dict = obj.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True)
        merged = self._deep_merge(ref_dict, current_dict)
        merged.pop("$ref", None)

        return self.SCHEMA_OBJECT_TYPE.model_validate(merged)

    def _is_ref_circular(self, resolved_ref: str) -> bool:
        """Check if a resolved $ref target contains a circular reference (cached)."""
        if resolved_ref in self._circular_ref_cache:
            return self._circular_ref_cache[resolved_ref]
        try:
            result = self._has_ref_cycle(resolved_ref, resolved_ref, set())
        except Exception:  # noqa: BLE001  # pragma: no cover
            result = True
        self._circular_ref_cache[resolved_ref] = result
        return result

    def _has_ref_cycle(self, ref_to_check: str, target: str, visited: set[str]) -> bool:
        """Check if the schema at ref_to_check contains a reference back to target."""
        visited.add(ref_to_check)
        file_part, _, fragment = ref_to_check.partition("#")
        if file_part and is_url(file_part):
            base_path = None
            root_path = [file_part]
        else:
            base_path = Path(file_part).parent if file_part else self.model_resolver.current_base_path
            root_path = file_part.split("/") if file_part else self.model_resolver.current_root
        base_url = file_part or self.model_resolver.base_url
        with (
            self.model_resolver.current_base_path_context(base_path),
            self.model_resolver.base_url_context(base_url),
            self.model_resolver.current_root_context(root_path),
        ):
            raw_doc = self._get_ref_body(file_part) if file_part else self.raw_obj
            raw_obj: Any = raw_doc
            if fragment:
                pointer = [p for p in fragment.split("/") if p]
                raw_obj = get_model_by_path(raw_doc, pointer)
            return self._walk_for_ref(raw_obj, target, visited)

    def _walk_for_ref(self, data: dict[str, Any] | list[Any], target: str, visited: set[str]) -> bool:
        """Follow references only in schemas, leaving instance values and metadata unvisited."""
        if isinstance(data, dict):
            ref_value = data.get("$ref")
            if isinstance(ref_value, str):
                try:
                    resolved = self.model_resolver.resolve_ref(ref_value)
                except Exception:  # noqa: BLE001
                    resolved = ref_value
                if resolved == target:
                    return True
                if resolved not in visited and self._has_ref_cycle(resolved, target, visited):
                    return True
            for keyword, value in data.items():
                if keyword in _JSON_SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
                    if any(
                        isinstance(schema, dict) and self._walk_for_ref(schema, target, visited)
                        for schema in value.values()
                    ):
                        return True
                elif (
                    (
                        keyword in _JSON_SCHEMA_SINGLE_KEYWORDS
                        or keyword in _JSON_SCHEMA_SEQUENCE_KEYWORDS
                        or keyword in _JSON_SCHEMA_SINGLE_OR_SEQUENCE_KEYWORDS
                    )
                    and isinstance(value, (dict, list))
                    and self._walk_for_ref(value, target, visited)
                ):
                    return True
            return False
        return any(isinstance(item, (dict, list)) and self._walk_for_ref(item, target, visited) for item in data)

    def _merge_primitive_schemas(self, items: list[JsonSchemaObject]) -> JsonSchemaObject:
        """Merge multiple primitive schemas by computing the intersection of their constraints."""
        if len(items) == 1:
            return items[0]

        base_dict = JsonSchemaParser._first_typed_schema_dict(items)
        self._merge_schema_constraints(base_dict, items, intersect=True)

        return self.SCHEMA_OBJECT_TYPE.model_validate(base_dict)

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
            return self.SCHEMA_OBJECT_TYPE.model_validate(merged_dict)

        base_dict = JsonSchemaParser._first_typed_schema_dict(items)
        self._merge_schema_constraints(base_dict, items, intersect=False)

        if merged_format:
            base_dict["format"] = merged_format

        return self.SCHEMA_OBJECT_TYPE.model_validate(base_dict)

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
            multiple_1 = Fraction(str(val1))
            multiple_2 = Fraction(str(val2))
            merged = Fraction(
                lcm(multiple_1.numerator, multiple_2.numerator),
                gcd(multiple_1.denominator, multiple_2.denominator),
            )
            return merged.numerator if merged.denominator == 1 else float(merged)
        return val1  # pragma: no cover

    @staticmethod
    def _intersect_constraint(field: str, val1: Any, val2: Any) -> Any:  # noqa: PLR0911
        """Compute the intersection of two constraint values."""
        v1 = val1.value if isinstance(val1, UnionIntFloat) else val1
        v2 = val2.value if isinstance(val2, UnionIntFloat) else val2

        match field:
            case "minLength" | "minimum" | "exclusiveMinimum" | "minItems" | "minProperties":
                if v1 is not None and v2 is not None:
                    return val1 if v1 >= v2 else val2
                return val1  # pragma: no cover
            case "maxLength" | "maximum" | "exclusiveMaximum" | "maxItems" | "maxProperties":
                if v1 is not None and v2 is not None:
                    return val1 if v1 <= v2 else val2
                return val1  # pragma: no cover
            case "uniqueItems":
                return val1 or val2
            case "multipleOf":
                return JsonSchemaParser._intersect_multiple_of(val1, val2)
        return val1  # pragma: no cover

    @classmethod
    def _merge_schema_constraints(
        cls,
        base_dict: dict[str, Any],
        items: list[JsonSchemaObject],
        *,
        intersect: bool,
    ) -> None:
        patterns: list[str] | None = None
        for item in items:
            for field in JsonSchemaObject.__constraint_fields__:
                if (value := cls._schema_constraint_value(item, field)) is None:
                    continue
                if intersect and field == "pattern":
                    if patterns is None:
                        patterns = [] if (base_pattern := base_dict.get(field)) is None else [base_pattern]
                    patterns.append(value)
                    continue
                if intersect and field in base_dict and base_dict[field] is not None:
                    base_dict[field] = cls._intersect_constraint(field, base_dict[field], value)
                else:
                    base_dict[field] = value
        if patterns:
            base_dict["pattern"] = _intersect_patterns(patterns)
        if (
            base_dict.get("type") in ("null", ["null"])
            and all(not item.type or _numeric_type_domain(item.type) is not None for item in items)
            and any("integer" in (_numeric_type_domain(item.type or "") or ()) for item in items)
        ):
            for field in _NUMBER_CONSTRAINT_KEYS:
                base_dict.pop(field, None)

    def _build_allof_type(  # noqa: PLR0911, PLR0912, PLR0913, PLR0915, PLR0917
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

        ref_items: list[JsonSchemaObject] = []
        ref_data_types: list[DataType] = []
        primitive_items: list[JsonSchemaObject] = []
        constraint_only_items: list[JsonSchemaObject] = []
        object_items: list[JsonSchemaObject] = []

        for item in allof_effective_items:
            if item.ref:
                ref_items.append(item)
            elif item.type and item.type != "object" and not isinstance(item.type, list):
                primitive_items.append(item)
            elif item.properties or item.additionalProperties or item.type == "object":
                object_items.append(item)
            elif item.allOf or item.anyOf or item.oneOf:
                nested_type = self._build_lightweight_type(item, depth + 1, visited, max_depth, max_union_elements)
                if nested_type is None:  # pragma: no cover
                    return None
                if nested_type.reference:  # pragma: no cover
                    ref_data_types.append(nested_type)
                else:
                    primitive_items.append(item)
            elif item.enum:  # pragma: no cover
                primitive_items.append(item)
            elif item.has_constraint:
                constraint_only_items.append(item)

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
        visited_refs: frozenset[str] | None = None,
    ) -> bool:
        """Return whether a schema describes a model-shaped object."""
        if item.ref:
            if not resolve_ref:
                return True
            if visited_refs is None:
                visited_refs = frozenset()
            if item.ref in visited_refs:
                return True
            return self._schema_requires_model_type(
                self._load_ref_schema_object(item.ref),
                resolve_ref=True,
                visited_refs=visited_refs | {item.ref},
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
        visited_refs: frozenset[str] | None = None,
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
        return self.SCHEMA_OBJECT_TYPE.model_validate(schema_dict)

    def _build_lightweight_allof_type(
        self,
        schema: JsonSchemaObject,
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
    ) -> DataType | None:
        allof_items: Sequence[JsonSchemaObject | bool] = schema.allOf or []
        if self._schema_has_own_value_keywords(schema):
            allof_items = [self._without_allof_keywords(schema), *allof_items]
        return self._build_allof_type(
            allof_items,
            depth,
            visited,
            max_depth,
            max_union_elements,
        )

    def _raise_unsatisfiable_schema(self, path: list[str], keyword: str) -> None:  # noqa: PLR6301
        raise SchemaParseError(
            message=f"{keyword} contains a boolean false schema that makes the schema unsatisfiable",
            path=path,
        )

    def _build_lightweight_type(  # noqa: PLR0911, PLR0912
        self,
        schema: JsonSchemaObject,
        depth: int = 0,
        visited: frozenset[int] | None = None,
        max_depth: int = 3,
        max_union_elements: int = 5,
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
            return self._get_const_data_type(schema.extras["const"])

        if schema.enum:
            literal_values, non_literal_values, has_null = self._partition_enum_values(schema.enum)
            if not non_literal_values:
                return self.data_type(literals=literal_values, is_optional=has_null)
            data_types = [self.data_type(literals=literal_values)] if literal_values else []
            data_types.extend(self._get_data_type_from_json_value(value) for value in non_literal_values)
            return self.data_type(data_types=data_types, is_optional=has_null)

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
            return self.data_type(data_types=data_types)

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
        """Merge parent and child property schemas for allOf."""
        if self.allof_merge_mode == AllOfMergeMode.NoMerge:
            return child_dict.copy()
        if "$ref" in child_dict:
            return child_dict.copy()

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
        return result

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
        schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
        ignored_keys = {
            *JsonSchemaObject.__constraint_fields__,
            *_INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS,
            *_INHERITED_PROPERTY_COUNT_CONSTRAINT_FIELDS,
            "default",
            "deprecated",
            "description",
            "example",
            "examples",
            "$comment",
            "nullable",
            "readOnly",
            "title",
            "writeOnly",
        }
        for key in ignored_keys:
            schema_dict.pop(key, None)
        if isinstance(extras := schema_dict.get(JsonSchemaObject.__extra_key__), dict):
            if meaningful_extras := {key: value for key, value in extras.items() if key not in ignored_keys}:
                schema_dict[JsonSchemaObject.__extra_key__] = meaningful_extras
            else:
                schema_dict.pop(JsonSchemaObject.__extra_key__, None)
        return schema_dict

    def _get_inherited_type_shape(self, schema: JsonSchemaObject) -> dict[str, Any]:  # noqa: PLR6301
        """Return structural schema keywords without inherited value restrictions."""
        schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
        shape = {key: value for key, value in schema_dict.items() if key in _INHERITED_TYPE_SHAPE_FIELDS}
        positional_items = schema.prefixItems or (schema.items if isinstance(schema.items, list) else None)
        if positional_items and schema.minItems == schema.maxItems == len(positional_items):
            shape["minItems"] = schema.minItems
            shape["maxItems"] = schema.maxItems
        return shape

    def _get_inherited_schema_types(  # noqa: PLR0912
        self,
        schema: JsonSchemaObject,
        parent_ref: str,
        active: frozenset[str] = frozenset(),
        *,
        refs_resolved: bool = False,
    ) -> frozenset[str]:
        """Infer JSON instance types accepted by an inherited schema."""
        schema, parent_ref, active, refs_resolved = self._resolve_inherited_parent_property(
            schema,
            parent_ref,
            active,
            refs_resolved=refs_resolved,
        )
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
                combined_types.update(
                    self._get_inherited_schema_types(
                        item,
                        parent_ref,
                        active,
                        refs_resolved=refs_resolved,
                    )
                )
        if combined_types:
            return frozenset(combined_types) | nullable_types

        all_of_types: frozenset[str] | None = None
        for item in schema.allOf:
            if not isinstance(item, JsonSchemaObject):
                continue
            if not (
                item_types := self._get_inherited_schema_types(
                    item,
                    parent_ref,
                    active,
                    refs_resolved=refs_resolved,
                )
            ):
                continue
            all_of_types = item_types if all_of_types is None else all_of_types & item_types
        return (all_of_types or frozenset()) | nullable_types

    def _get_inherited_constraint_fields(self, schema: JsonSchemaObject) -> frozenset[str]:  # noqa: PLR6301
        """Return top-level validation constraints that need type-compatible placement."""
        fields = {
            field
            for _, fields in _INHERITED_CONSTRAINT_TYPE_FIELDS
            for field in fields
            if field in schema.model_fields_set
        }
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
                    *_INHERITED_CONSTRAINT_TYPE_FIELDS,
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
        for compatible_types, constraint_fields in _INHERITED_CONSTRAINT_TYPE_FIELDS:
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
        schema_obj = self.SCHEMA_OBJECT_TYPE.model_validate(schema)
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
                    normalized_item = self._normalize_inherited_constraint_compositions(
                        self.SCHEMA_OBJECT_TYPE.model_validate(item)
                    )
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
        return self.SCHEMA_OBJECT_TYPE.model_validate(schema_dict) if changed else schema

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
        context: tuple[str, frozenset[str], bool] = ("", frozenset(), False),
    ) -> bool:
        """Return whether a child schema only narrows metadata/constraints of its inherited type."""
        parent_ref, _, parent_refs_resolved = context
        child_dict = self._remove_unconstrained_compositions(self._get_inherited_override_shape(child))
        if not child_dict:
            return True

        if (child_type := child_dict.pop("type", None)) is not None:
            child_types = frozenset((child_type,)) if isinstance(child_type, str) else frozenset(child_type)
            parent_types = self._get_inherited_schema_types(
                parent,
                parent_ref,
                frozenset(),
                refs_resolved=parent_refs_resolved,
            )
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
            ):
                return False
        return True

    def _is_partial_inherited_nested_value(  # noqa: PLR0914
        self,
        child: object,
        parent: object,
        *,
        parent_tail: object = True,
        context: tuple[str, frozenset[str], bool] = ("", frozenset(), False),
    ) -> bool:
        """Compare one nested schema keyword using JSON Schema intersection semantics."""
        parent_ref, active, parent_refs_resolved = context
        if child is None or child is True or parent is True or parent is False:
            return True
        if isinstance(child, dict) and self._is_unconstrained_inherited_schema(child):
            return True
        result = False
        match child, parent:
            case dict() as child_schema, JsonSchemaObject() as parent_schema:
                child_obj = self.SCHEMA_OBJECT_TYPE.model_validate(child_schema)
                effective_parent = parent_schema
                effective_ref = parent_ref
                next_active = active
                effective_refs_resolved = parent_refs_resolved
                if parent_schema.ref:
                    resolved_ref = (
                        parent_schema.ref
                        if parent_refs_resolved
                        else self._resolve_inherited_child_ref(parent_schema.ref, parent_ref)
                    )
                    effective_parent, effective_ref, next_active, effective_refs_resolved = (
                        self._resolve_inherited_parent_property(
                            parent_schema,
                            parent_ref,
                            active - {resolved_ref},
                            refs_resolved=parent_refs_resolved,
                        )
                    )
                result = self._is_partial_inherited_property(
                    child_obj,
                    effective_parent,
                    (effective_ref, next_active, effective_refs_resolved),
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
            or schema.model_fields_set & _INHERITED_PROPERTY_COUNT_CONSTRAINT_FIELDS
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
        active: frozenset[str] = frozenset(),
        *,
        refs_resolved: bool = False,
    ) -> tuple[JsonSchemaObject, str, frozenset[str], bool]:
        """Resolve pure property references before comparing nested override shapes."""
        source_schema = schema
        cache_by_ref = False
        cache_identity: str | int = id(schema)
        match schema.ref:
            case str() as schema_ref if schema_ref:
                cache_by_ref = True
                cache_identity = schema_ref
            case _:
                pass
        cache_key: tuple[str | int, str, frozenset[str], bool] | None = None
        if not cache_by_ref or not schema.has_ref_with_schema_keywords:
            cache_key = (
                cache_identity,
                parent_ref,
                active,
                refs_resolved,
            )
            if (cached := self._inherited_parent_property_cache.get(cache_key)) is not None and (
                cache_by_ref or cached[0] is schema
            ):
                return cached[1]
        while schema.ref:
            resolved_ref = schema.ref if refs_resolved else self._resolve_inherited_child_ref(schema.ref, parent_ref)
            if resolved_ref in active:
                break
            active |= {resolved_ref}
            referenced_schema = self._load_inherited_schema_object(resolved_ref)
            if schema.has_ref_with_schema_keywords:
                referenced_dict = referenced_schema.model_dump(exclude_unset=True, by_alias=True)
                self._resolve_schema_refs_in_place(referenced_dict, resolved_ref)
                sibling_dict = schema.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True)
                if not refs_resolved:
                    self._resolve_schema_refs_in_place(sibling_dict, parent_ref)
                merged_dict = self._deep_merge(referenced_dict, sibling_dict)
                merged_dict.pop("$ref", None)
                schema = self.SCHEMA_OBJECT_TYPE.model_validate(merged_dict)
                refs_resolved = True
            else:
                schema = referenced_schema
                refs_resolved = False
            parent_ref = resolved_ref
        result = schema, parent_ref, active, refs_resolved
        if cache_key is not None:
            self._inherited_parent_property_cache[cache_key] = source_schema, result
        return result

    def _get_nested_inherited_active(  # noqa: PLR6301
        self,
        parent: dict[str, Any],
        child: dict[str, Any],
        active: frozenset[str],
    ) -> frozenset[str]:
        """Permit recursive expansion only along the finite child override path."""
        if child and isinstance(parent_ref := parent.get("$ref"), str):
            return active - {parent_ref}
        return active

    def _merge_inherited_type_shape_value(
        self,
        parent: object,
        child: object,
        parent_ref: str,
        active: frozenset[str],
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
                    parent_refs_resolved=True,
                )
            case _:
                pass
        return result

    def _merge_inherited_type_shape_keyword(
        self,
        key: str,
        parent: object,
        child: object,
        context: tuple[dict[str, Any], str, frozenset[str]],
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
        context: tuple[str, frozenset[str]],
        *,
        excludes_null: bool,
    ) -> object:
        """Apply type-specific child keywords to one compatible inherited branch."""
        if not isinstance(parent, dict):
            return parent
        parent = cast("dict[str, Any]", parent)
        parent_ref, active = context
        parent_obj = self.SCHEMA_OBJECT_TYPE.model_validate(parent)
        parent_types = self._get_inherited_schema_types(
            parent_obj,
            parent_ref,
            active,
            refs_resolved=True,
        )
        if excludes_null and parent_types == {"null"}:
            return _NO_INHERITED_SCHEMA_MERGE
        branch_child = self._select_inherited_distributed_shape(
            child,
            distributed_fields,
            parent_types,
        )
        if excludes_null and "null" in parent_types:
            branch_child["nullable"] = False
        return self._merge_inherited_type_shape_dict(
            parent,
            branch_child,
            parent_ref,
            self._get_nested_inherited_active(
                parent,
                branch_child,
                active,
            ),
            parent_refs_resolved=True,
        )

    def _merge_inherited_type_shape_dict(  # noqa: PLR0912, PLR0914
        self,
        parent: dict[str, Any],
        child: dict[str, Any],
        parent_ref: str,
        active: frozenset[str] = frozenset(),
        *,
        parent_refs_resolved: bool = False,
    ) -> dict[str, Any]:
        """Fill missing type-shape keywords while keeping child validation precedence."""
        parent_obj = self.SCHEMA_OBJECT_TYPE.model_validate(parent)
        effective_parent, effective_ref, next_active, effective_refs_resolved = self._resolve_inherited_parent_property(
            parent_obj,
            parent_ref,
            active,
            refs_resolved=parent_refs_resolved,
        )
        parent_types = self._get_inherited_schema_types(
            effective_parent,
            effective_ref,
            next_active,
            refs_resolved=effective_refs_resolved,
        )
        parent_shape = self._get_inherited_type_shape(effective_parent)
        if not effective_refs_resolved:
            self._resolve_schema_refs_in_place(parent_shape, effective_ref)
        child_obj = self.SCHEMA_OBJECT_TYPE.model_validate(child)
        excludes_null = child_obj.nullable is False or (
            child_obj.type is not None and not child_obj.type_has_null and child_obj.nullable is not True
        )
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
                    parent_refs_resolved=True,
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
        result = parent_shape.copy()
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
        return result

    def _merge_no_merge_inherited_property(
        self,
        parent: JsonSchemaObject,
        child: JsonSchemaObject,
        parent_ref: str,
    ) -> JsonSchemaObject:
        """Materialize child constraints on the inherited type shape in no-merge mode."""
        effective_parent, effective_ref, active, refs_resolved = self._resolve_inherited_parent_property(
            parent,
            parent_ref,
        )
        if not self._is_partial_inherited_property(
            child,
            effective_parent,
            (effective_ref, active, refs_resolved),
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
        merged_schema = self.SCHEMA_OBJECT_TYPE.model_validate(merged_dict)
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
                    effective_parent, effective_ref, active, refs_resolved = self._resolve_inherited_parent_property(
                        parent_property,
                        inherited_property[1],
                    )
                    if not self._is_partial_inherited_property(
                        normalized_child,
                        effective_parent,
                        (effective_ref, active, refs_resolved),
                    ):
                        continue
                    if self._has_inherited_constraints(normalized_child):
                        continue
                    deferred_names.add(field_name)
                case _:
                    continue
        return frozenset(deferred_names)

    def _mark_partial_inherited_fields(  # noqa: PLR6301
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
            excludes_null = bool(
                isinstance(source_property, JsonSchemaObject)
                and (
                    source_property.nullable is False
                    or (
                        source_property.type is not None
                        and not source_property.type_has_null
                        and source_property.nullable is not True
                    )
                )
            )
            field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = _get_inherited_type_modifiers(
                field.data_type,
                excludes_null=excludes_null,
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
        return self.SCHEMA_OBJECT_TYPE.model_validate(sanitized_dict)

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
                    self.SCHEMA_OBJECT_TYPE.model_validate(normalized_dict)
                    if normalized_dict != child_dict
                    else effective_child
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
            self._resolve_schema_refs_in_place(parent_dict, parent_ref)
            child_dict = effective_child.model_dump(exclude_unset=True, by_alias=True)
            merged_dict = self._merge_property_schemas(parent_dict, child_dict)
            merged_property = self.SCHEMA_OBJECT_TYPE.model_validate(merged_dict)
            merged_property.__dict__[_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY] = frozenset(
                self.get_field_extras(effective_child)
            )
            merged_properties[prop_name] = merged_property
            merged_changed = True

        if not merged_changed:
            return child_obj
        return child_obj.model_copy(update={"properties": merged_properties})

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

    def _resolve_inherited_child_ref(self, ref: str, parent_ref: str) -> str:
        """Resolve a nested inherited reference in its defining document."""
        with self._inherited_ref_context(parent_ref):
            return self.model_resolver.resolve_ref(ref)

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
                self.model_resolver.add_ref(ref)
                if defining_ref is None
                else self.model_resolver.add_ref(
                    self._resolve_inherited_child_ref(ref, defining_ref),
                    resolved=True,
                )
            )
            references.setdefault(reference.path, reference)
        return list(references.values())

    def _get_inherited_schema_ancestor_paths(
        self,
        resolved_ref: str,
        active: frozenset[str] = frozenset(),
    ) -> frozenset[str]:
        """Collect raw-schema ancestors for stable base ordering before generation."""
        if resolved_ref in self._inherited_schema_ancestor_cache:
            return self._inherited_schema_ancestor_cache[resolved_ref]
        if SPECIAL_PATH_MARKER in resolved_ref or resolved_ref in active:
            return frozenset()
        schema = self._load_inherited_schema_object(resolved_ref)
        ancestors: set[str] = set()
        for ref in self._iter_allof_refs(schema):
            parent_ref = self._resolve_inherited_child_ref(ref, resolved_ref)
            ancestors.add(parent_ref)
            ancestors.update(
                self._get_inherited_schema_ancestor_paths(
                    parent_ref,
                    active | {resolved_ref},
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

        def linearize(ref: str, active: frozenset[str] = frozenset()) -> list[str]:
            if cached_ref := linearized_refs.get(ref):
                return cached_ref
            if ref in active:
                return [ref]
            schema = self._load_inherited_schema_object(ref)
            parents = [
                self._resolve_inherited_child_ref(parent_ref, ref) for parent_ref in self._iter_allof_refs(schema)
            ]
            parents.sort(
                key=lambda parent: sum(parent in self._get_inherited_schema_ancestor_paths(other) for other in parents)
            )
            result = [
                ref,
                *c3_merge(
                    [
                        *[linearize(parent, active | {ref}).copy() for parent in parents],
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
        visited_refs: set[str] = set()

        def collect_inline_properties(schema: JsonSchemaObject) -> None:
            for item in schema.allOf:
                if isinstance(item, JsonSchemaObject) and not item.ref:
                    collect_inline_properties(item)
            for field_name in schema.properties or {}:
                ordered_names.setdefault(field_name, None)

        def collect_ref(resolved_ref: str) -> None:
            if SPECIAL_PATH_MARKER in resolved_ref or resolved_ref in visited_refs:
                return
            visited_refs.add(resolved_ref)
            schema = self._load_inherited_schema_object(resolved_ref)
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
        """Parse one forward schema's own fields in its defining scope."""
        inherited_properties = self._get_inherited_property_map(parent_refs)
        reference = self._get_inherited_field_owner_reference(reference, schema)
        class_name = reference.name
        module_name = get_inferred_module_name(
            class_name,
            treat_dot_as_module=self.treat_dot_as_module,
            strict_dotted_module_names=self.strict_dotted_module_names,
        )
        fields: list[DataModelFieldBase] = []

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
                    [],
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
                    [],
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

    def _resolve_schema_refs_in_place(self, value: Any, parent_ref: str) -> None:
        """Canonicalize copied inherited refs before parsing them in a child document."""
        match value:
            case dict() as mapping:
                if isinstance(ref := mapping.get("$ref"), str):
                    mapping["$ref"] = self._resolve_inherited_child_ref(ref, parent_ref)
                for nested_value in mapping.values():
                    self._resolve_schema_refs_in_place(nested_value, parent_ref)
            case list() as items:
                for item in items:
                    self._resolve_schema_refs_in_place(item, parent_ref)

    def _iter_inherited_schema_objects(
        self, base_classes: list[Reference], visited: frozenset[str]
    ) -> Iterator[tuple[JsonSchemaObject, frozenset[str], str]]:
        """Yield inherited schema objects with updated visited paths."""
        for base in self._sort_inherited_base_classes_for_mro(base_classes):
            if not base.path:  # pragma: no cover
                continue
            resolved_ref = base.path
            if resolved_ref in visited:  # pragma: no cover
                continue
            next_visited = visited | {resolved_ref}

            try:
                parent_schema = self._load_inherited_schema_object(resolved_ref)
            except Exception:  # pragma: no cover  # noqa: BLE001, S112
                continue
            yield parent_schema, next_visited, resolved_ref

    def _load_inherited_schema_object(self, resolved_ref: str) -> JsonSchemaObject:
        """Load and cache a schema used only for inherited field inspection."""
        if cached := self._inherited_schema_cache.get(resolved_ref):
            return cached
        schema = self._validate_schema_object(self._get_ref_raw_schema(resolved_ref), [resolved_ref])
        self._inherited_schema_cache[resolved_ref] = schema
        return schema

    def _get_inherited_field_type(
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[str] | None = None
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
                    self.model_resolver.add_ref(
                        self._resolve_inherited_child_ref(ref, parent_ref),
                        resolved=True,
                    )
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
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[str] | None = None
    ) -> JsonSchemaObject | None:
        """Get the schema for an inherited property from parent schemas."""
        if visited is None:
            visited = frozenset()

        def find_property(
            schema: JsonSchemaObject,
            current_visited: frozenset[str],
            current_ref: str,
        ) -> JsonSchemaObject | None:
            if schema.properties and isinstance(
                property_schema := schema.properties.get(prop_name),
                JsonSchemaObject,
            ):
                return property_schema

            for item in schema.allOf:
                if (
                    isinstance(item, JsonSchemaObject)
                    and not item.ref
                    and (inline_property := find_property(item, current_visited, current_ref))
                ):
                    return inline_property

            for item in schema.allOf:
                if not isinstance(item, JsonSchemaObject) or not item.ref:
                    continue
                resolved_ref = self._resolve_inherited_child_ref(item.ref, current_ref)
                if resolved_ref in current_visited:
                    continue
                if property_schema := find_property(
                    self._load_inherited_schema_object(resolved_ref),
                    current_visited | {resolved_ref},
                    resolved_ref,
                ):
                    return property_schema
            return None

        for parent_schema, next_visited, parent_ref in self._iter_inherited_schema_objects(base_classes, visited):
            if property_schema := find_property(parent_schema, next_visited, parent_ref):
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
                if item.type and ref_schema.type:
                    compatible_type_pairs = {
                        ("integer", "number"),
                        ("number", "integer"),
                    }
                    numeric_types = (
                        _numeric_type_domain(item.type) is not None
                        and _numeric_type_domain(ref_schema.type) is not None
                    )
                    if (
                        not numeric_types
                        and item.type != ref_schema.type
                        and (item.type, ref_schema.type) not in compatible_type_pairs
                    ):
                        return None
                constraint_items.append(item)

        if not constraint_items:
            return None

        all_items = [ref_schema, *constraint_items]
        merged_schema = self._merge_primitive_schemas_for_allof(all_items)
        if merged_schema is None:
            return None

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
                if (format_ := merged_dict.get("format")) and (
                    format_ in self._data_formats.get(schema_type, {}) or (schema_type, format_) in self.type_mappings
                ):
                    mapped_type = self._get_type_with_mappings(schema_type, format_)
                    if schema_type == "string" and mapped_type in {
                        Types.date,
                        Types.date_time,
                        Types.date_time_local,
                        Types.timedelta,
                        Types.uuid,
                        Types.uuid1,
                        Types.uuid2,
                        Types.uuid3,
                        Types.uuid4,
                        Types.uuid5,
                        Types.ipv4,
                        Types.ipv6,
                        Types.ipv4_network,
                        Types.ipv6_network,
                    }:
                        constraint_types = frozenset()
                    elif mapped_type in _NUMBER_CONSTRAINT_TYPES:
                        constraint_types = frozenset({"number"})
                    elif mapped_type in {
                        Types.string,
                        Types.byte,
                        Types.binary,
                        Types.password,
                        Types.email,
                        Types.hostname,
                    }:
                        constraint_types = frozenset({"string"})
                merged_dict = self._drop_incompatible_inherited_constraints(merged_dict, constraint_types)
            merged_schema = self.SCHEMA_OBJECT_TYPE.model_validate(merged_dict)

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
    ) -> dict[str, Any]:
        """Intersect allOf bounds and enums only in positive schema positions."""
        result = parent.copy()
        for key, value in child.items():
            if key not in result:
                result[key] = value
                continue
            previous = result[key]
            if isinstance(previous, dict) and isinstance(value, dict):
                if schema_map or key in _INHERITED_NESTED_SCHEMA_FIELDS:
                    result[key] = self._merge_all_of_schema(
                        previous,
                        value,
                        schema_map=not schema_map and key in _INHERITED_SCHEMA_MAP_FIELDS,
                    )
                else:
                    result[key] = self._deep_merge(previous, value)
            elif (
                not schema_map and key in _ALLOF_BOUND_CONSTRAINT_FIELDS and previous is not None and value is not None
            ):
                # Preserve the legacy child representation when two bounds are equal.
                result[key] = JsonSchemaParser._intersect_constraint(key, value, previous)
            elif isinstance(previous, list) and isinstance(value, list):
                if not schema_map and key == "enum":
                    result[key] = _intersect_all_of_enum(previous, value)
                else:
                    result[key] = previous + value
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
                self._raise_unsatisfiable_schema([], "allOf")
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

        if "required" in merged_schema and isinstance(merged_schema["required"], list):
            merged_schema["required"] = list(dict.fromkeys(merged_schema["required"]))

        merged_schema.pop("allOf", None)
        return self.SCHEMA_OBJECT_TYPE.model_validate(merged_schema)

    def _merge_all_of_root_validation_keywords(self, merged: dict[str, Any], sources: list[JsonSchemaObject]) -> None:
        """Preserve literal and item intersections while materializing a value reference."""
        literal_sets = [values for source in sources if (values := self._schema_literal_values(source)) is not None]
        if literal_sets:
            literal_values = [
                value
                for value in literal_sets[0]
                if all(any(_json_literal_values_equal(value, other) for other in values) for values in literal_sets[1:])
            ]
            if not literal_values:
                self._raise_unsatisfiable_schema([], "allOf")
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
                        positional_sources.append(
                            self.SCHEMA_OBJECT_TYPE.model_validate({"items": True if child is None else child})
                        )
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
            self.model_resolver.resolve_ref(node.ref)
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
        """Merge acyclic nodes while leaving recursive references for normal reference resolution."""
        children: list[JsonSchemaObject] = []
        for child in nodes:
            if not isinstance(child, JsonSchemaObject):
                continue
            child_schema = child
            if child.ref:
                resolved = self._load_ref_schema_object(child.ref).model_dump(exclude_unset=True, by_alias=True)
                self._resolve_schema_refs_in_place(resolved, self.model_resolver.resolve_ref(child.ref))
                children.append(self.SCHEMA_OBJECT_TYPE.model_validate(resolved))
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
        return merged

    def _merge_all_of_root_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:  # noqa: PLR0912
        """Unwrap one value schema or merge mapping-shaped allOf items into a root schema."""
        if obj.properties or obj.patternProperties or obj.propertyNames is not None:
            return None

        supports_literal_validation = hasattr(self.data_model_root_type, "add_literal_validation")
        single_value = len(obj.allOf) == 1 and not self._schema_requires_model_type(obj)
        mapping_schemas: list[JsonSchemaObject] = []
        for item in obj.allOf:
            match item:
                case JsonSchemaObject() as schema:
                    if ref := schema.ref:
                        schema = self._load_ref_schema_object(ref)
                case _:
                    return None
            materialize_siblings = bool(
                ref
                and len(item.model_fields_set) > 1
                and self._ref_sibling_keywords_enabled
                and (item.has_ref_with_schema_keywords if supports_literal_validation else item.has_constraint)
            )
            scalar_or_array = (
                schema.type
                and schema.type != "object"
                and not (schema.enum and not (materialize_siblings and supports_literal_validation))
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
                    self._resolve_schema_refs_in_place(merged, self.model_resolver.resolve_ref(ref))
                    referenced_schema = self.SCHEMA_OBJECT_TYPE.model_validate(merged)
                    merged.update(item.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True))
                    merged.update(obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True))
                    self._merge_schema_constraints(
                        merged, [schema, item, obj], intersect=self.allof_merge_mode != AllOfMergeMode.NoMerge
                    )
                    if supports_literal_validation:
                        self._merge_all_of_root_validation_keywords(merged, [referenced_schema, item, obj])
                    merged_root = self.SCHEMA_OBJECT_TYPE.model_validate(merged)
                return merged_root or (
                    obj.model_copy(update={"ref": ref, "allOf": []})
                    if ref
                    else self.SCHEMA_OBJECT_TYPE.model_validate({
                        **schema.model_dump(exclude_unset=True, by_alias=True),
                        **obj.model_dump(exclude={"allOf"}, exclude_unset=True, by_alias=True),
                    })
                )
            if (
                schema.properties
                or schema.patternProperties
                or schema.propertyNames is not None
                or not _is_object_only_type(schema.type)
                or not isinstance(schema.additionalProperties, JsonSchemaObject)
            ):
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
        if merged.get("type") == ["object"]:
            merged["type"] = "object"
        else:
            merged.setdefault("type", "object")
        return self.SCHEMA_OBJECT_TYPE.model_validate(merged)

    def parse_combined_schema(  # noqa: PLR0912
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        target_attribute_name: str,
    ) -> list[DataType]:
        """Parse combined schema (anyOf, oneOf, allOf) into a list of data types."""
        base_object = obj.model_dump(exclude={target_attribute_name, "title"}, exclude_unset=True, by_alias=True)
        combined_schemas: list[JsonSchemaObject] = []
        refs = []
        use_builtin_false_ref_facts: bool | None = None
        for index, target_attribute in enumerate(getattr(obj, target_attribute_name, [])):
            if self._is_false_schema_item(target_attribute):
                continue
            if target_attribute is True:
                combined_schemas.append(self.SCHEMA_OBJECT_TYPE.model_validate(base_object))
                continue
            if target_attribute.ref:
                if target_attribute.ref_type == JSONReference.LOCAL:
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
                        refs.append(index)
                    else:
                        combined_schemas.append(
                            self._preserve_inherited_materialized_type_shape(
                                target_attribute,
                                self.SCHEMA_OBJECT_TYPE.model_validate(
                                    self._deep_merge(
                                        base_object, merged_attr.model_dump(exclude_unset=True, by_alias=True)
                                    ),
                                ),
                            )
                        )
                else:
                    combined_schemas.append(target_attribute)
                    refs.append(index)
            else:
                combined_schemas.append(
                    self._preserve_inherited_materialized_type_shape(
                        target_attribute,
                        self.SCHEMA_OBJECT_TYPE.model_validate(
                            self._deep_merge(
                                base_object,
                                target_attribute.model_dump(exclude_unset=True, by_alias=True),
                            ),
                        ),
                    )
                )

        variant_names = self._get_inferred_union_variant_names(name, obj, combined_schemas)
        if self._output_model_context.requires_tagged_union_discriminator:
            self._set_tagged_union_discriminator(obj, combined_schemas)
        parsed_schemas = self._parse_combined_schema_items(name, obj, path, combined_schemas, variant_names)
        if not parsed_schemas:
            self._raise_unsatisfiable_schema(path, target_attribute_name)
        common_path_keyword = f"{target_attribute_name}Common"
        return [
            self._parse_object_common_part(
                name,
                obj,
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
        """Return the branch schema named by keyword, parsing it from extras on first use."""
        item = obj.extras.get(keyword)
        if isinstance(item, (JsonSchemaObject, bool)):
            return item
        if isinstance(item, dict):
            item = self.SCHEMA_OBJECT_TYPE.model_validate(item)
            obj.extras[keyword] = item
            return item
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
        visited_refs: frozenset[str] = frozenset(),
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
            resolved_ref = self.model_resolver.resolve_ref(item.ref)
            if resolved_ref in visited_refs:
                continue
            yield from self._iter_property_count_validation_sources(
                self._load_ref_schema_object(item.ref),
                visited_refs | {resolved_ref},
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
        return self.SCHEMA_OBJECT_TYPE.model_validate(schema_dict)

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
                return to_camel(field.name)
            case AliasGenerator.ToPascal:
                return to_pascal(field.name)
            case AliasGenerator.ToSnake:
                return to_snake(field.name)
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
        visited_refs: frozenset[str] = frozenset(),
        *,
        include_references: bool = True,
    ) -> Iterator[JsonSchemaObject]:
        yield obj
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
                        yield from self._iter_schema_validation_sources(
                            item.model_copy(update={"ref": None}), visited_refs, include_references=False
                        )
                if not include_references:
                    continue
                resolved_ref = self.model_resolver.resolve_ref(item.ref)
                if resolved_ref in visited_refs:
                    continue
                yield from self._iter_schema_validation_sources(
                    self._load_ref_schema_object(item.ref),
                    visited_refs | {resolved_ref},
                )
                continue
            yield from self._iter_schema_validation_sources(item, visited_refs, include_references=include_references)

    def _schema_item_may_accept_container(
        self,
        item: JsonSchemaObject | bool,  # noqa: FBT001
        container_type: Literal["array", "object"],
        visited_refs: frozenset[str],
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
        visited_refs: frozenset[str],
    ) -> bool:
        """Return whether a schema can accept one raw JSON container shape."""
        if obj.is_boolean_schema_false:
            return False
        if obj.ref:
            resolved_ref = self.model_resolver.resolve_ref(obj.ref)
            if not self._ref_sibling_keywords_enabled:
                return resolved_ref in visited_refs or self._schema_may_accept_container(
                    self._load_ref_schema_object(obj.ref), container_type, visited_refs | {resolved_ref}
                )
            if resolved_ref not in visited_refs and not self._schema_may_accept_container(
                self._load_ref_schema_object(obj.ref), container_type, visited_refs | {resolved_ref}
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
        visited_refs: frozenset[str],
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
                resolved_ref = self.model_resolver.resolve_ref(branch.ref)
                if resolved_ref not in visited_refs:
                    rules = (
                        *rules,
                        *self._iter_unique_items_rules(
                            self._load_ref_schema_object(branch.ref),
                            path,
                            visited_refs | {resolved_ref},
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
        visited_refs: frozenset[str],
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
        visited_refs: frozenset[str],
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
            return cast("set[UniqueItemsPath]", set())
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
            return cast("set[UniqueItemsPath]", set())
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
                        return cast("set[UniqueItemsPath]", set())
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
        visited_refs: frozenset[str] = frozenset(),
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
        *,
        include_properties: bool = False,
    ) -> Iterator[UniqueItemsRule]:
        """Yield compact uniqueItems rules required by all applicable union branches."""
        if obj.ref:
            if self.collapse_root_models:
                resolved_ref = self.model_resolver.resolve_ref(obj.ref)
                if resolved_ref not in visited_refs:
                    yield from self._iter_unique_items_rules(
                        self._load_ref_schema_object(obj.ref),
                        path,
                        visited_refs | {resolved_ref},
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
            resolved_ref = self.model_resolver.resolve_ref(item.ref)
            if resolved_ref in visited_refs:
                continue
            yield from self._iter_unique_items_rules(
                self._load_ref_schema_object(item.ref),
                path,
                visited_refs | {resolved_ref},
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
        visited_refs: frozenset[str] = frozenset(),
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
        additional_property: tuple[int, JsonSchemaObject, JsonSchemaObject] | None = None
        allow_unmatched = True

        for source_index, source in enumerate(self._iter_schema_validation_sources(obj)):
            if source.additionalProperties is False or source.unevaluatedProperties is False:
                allow_unmatched = False
            if isinstance(source.additionalProperties, JsonSchemaObject) and additional_property is None:
                additional_property = (source_index, source, source.additionalProperties)
            if not source.patternProperties:
                continue
            for pattern_index, (pattern, schema) in enumerate(source.patternProperties.items()):
                match schema:
                    case False:
                        rejected_patterns.append(pattern)
                    case True:
                        pattern_value_types.append((pattern, self.data_type_manager.get_data_type(Types.any)))
                    case JsonSchemaObject():
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

        if not pattern_value_types and not rejected_patterns:
            return pattern_value_types, rejected_patterns, None, allow_unmatched
        if additional_property is None:
            return pattern_value_types, rejected_patterns, None, allow_unmatched

        source_index, source, additional_properties = additional_property
        additional_property_name = f"{name}AdditionalProperty"
        additional_property_type = self._parse_additional_properties_value(
            additional_property_name,
            get_special_path(f"schemaValidators/additionalProperties/{source_index}", path),
            source,
            additional_properties=additional_properties,
            constrained_name=additional_property_name,
        )
        return pattern_value_types, rejected_patterns, additional_property_type, allow_unmatched

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
            if pattern is False:
                return True
            return (
                declared_type == "integer"
                and isinstance(pattern, JsonSchemaObject)
                and pattern.type == "integer"
                and not pattern.extras
                and pattern.model_fields_set == {"type", "minimum"}
                and pattern.minimum is not None
                and (isinstance(pattern.minimum, int) or math.isfinite(pattern.minimum))
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
    def _pattern_validation_loses_raw_value(
        cls, obj: JsonSchemaObject, pattern_value_types: list[tuple[str, DataType]]
    ) -> Literal["declared", "models"] | None:
        """Limit changed helpers to proven skipped constraints or incompatible model instances."""
        if not obj.extras.keys() <= {"title", "description"}:
            return None
        return cls._pattern_validation_intersection(obj, pattern_value_types)

    @classmethod
    def _pattern_validation_intersection(
        cls, obj: JsonSchemaObject, pattern_value_types: list[tuple[str, DataType]]
    ) -> Literal["declared", "models"] | None:
        """Prove the same bounded intersection after root annotation eligibility."""
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
        for pattern, schema in obj.patternProperties.items():
            for name, declared in (obj.properties or {}).items():
                try:
                    matches = re.search(pattern, name)
                except re.error:
                    continue
                if matches and cls._declared_pattern_rejects_value(declared, schema):
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
        cls, obj: JsonSchemaObject, pattern_value_types: list[tuple[str, DataType]]
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
                obj.model_copy(update={"patternProperties": {**obj.patternProperties, **patterns}}), pattern_value_types
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
        intersection = (
            self._pattern_validation_loses_raw_value(obj, pattern_value_types)
            if not any((self.config.base_class, self.config.base_class_map, self.config.custom_template_dir))
            and self.config.extra_template_data is None
            else None
        )
        if obj is self._pattern_validation_document_root:
            if intersection is None and not self.force_optional_for_required_fields:
                intersection = self._pattern_validation_with_undeclared_required(obj, pattern_value_types)
            if (
                intersection is None
                and not obj.extras.keys() <= {"title", "description"}
                and self._plain_pattern_root_annotations(obj, pattern_value_types)
            ):
                intersection = self._pattern_validation_intersection(obj, pattern_value_types)
        match intersection:
            case "declared":
                rule_type = IndependentDeclaredPatternPropertiesRule
            case "models":
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
                    parent_name = python_overrides[field_name]
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
                self.generation_store.replace_field_type(field, self.data_type())
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
            fields.insert(0, extra_field)
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)
        if self.generate_schema_validators:
            self._add_schema_validators(reference.path, reference.name, obj, path, fields, base_classes)

        separate_model_fields = self._get_separate_model_fields(fields, base_classes)
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
                keyword_only=self.keyword_only,
                treat_dot_as_module=self.treat_dot_as_module,
                dataclass_arguments=self.dataclass_arguments,
            )
            self.generation_store.register_model(data_model_type)
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
                self._raise_unsatisfiable_schema(path, "allOf")
            if not isinstance(all_of_item, JsonSchemaObject):  # pragma: no cover
                continue
            if all_of_item.ref:  # $ref
                ref_schema = self._load_ref_schema_object(all_of_item.ref)
                if ref_schema.is_boolean_schema_false:
                    self._raise_unsatisfiable_schema(path, "allOf")

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
                    ref = self.model_resolver.add_ref(all_of_item.ref)
                    if self._dynamic_target_bindings(ref.path):
                        warn(
                            f"allOf base class {all_of_item.ref!r} cannot follow the $dynamicAnchor bindings of its "
                            "dynamic scope; its $dynamicRef resolve within its own schema resources.",
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
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)
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
            nullable=obj.type_has_null,
            treat_dot_as_module=self.treat_dot_as_module,
        )
        self._apply_root_model_sequence_interface(data_model_root, fields)
        self.generation_store.register_model(data_model_root)
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
        """Register a root using an internal alternate output representation."""
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
        if (
            self.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse
            and self._fields_reference_rw_model_variant(fields, "Request")
        ):
            self._request_response_fields[reference.path] = tuple(self._copy_unregistered_fields(fields))
            self._rw_model_field_facts_cache[reference.path] = self._get_rw_model_field_facts(fields)
            variants: list[DataModel] = []
            for suffix in ("Request", "Response"):
                variant_fields = [self._copy_model_field(field) for field in fields]
                variant_reference = self._get_rw_model_variant_reference(reference, suffix, loaded=True)
                self._update_field_refs_for_variant(variant_fields, suffix)
                self._set_schema_metadata(variant_reference.path, obj)
                self.set_schema_extensions(variant_reference.path, obj)
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
                variants[-1].__dict__[_SOURCE_REFERENCE_PATH_KEY] = variant_reference.__dict__.get(
                    _SOURCE_REFERENCE_PATH_KEY,
                    _get_rw_model_variant_source_path(reference, suffix),
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
            and single_obj.ref_type == JSONReference.LOCAL
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
            self._raise_unsatisfiable_schema(path, "allOf")

        if not (isinstance(referenced_schema, dict) and referenced_schema.get("enum")):
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
                array_type = next(
                    (
                        data_type
                        for data_type in root_model.fields[0].data_type.all_data_types
                        if data_type.is_list or data_type.is_sequence or data_type.is_set or data_type.is_tuple
                    ),
                    None,
                )
                if array_type is None:
                    array_type = next(
                        nested
                        for data_type in root_model.fields[0].data_type.all_data_types
                        if (array_ref := data_type.reference) is not None
                        and (array_model := cast("DataModel | None", array_ref.source)) is not None
                        and (array_model.IS_ALIAS or array_model.IS_ROOT_MODEL)
                        for nested in array_model.fields[0].data_type.all_data_types
                        if nested.is_list or nested.is_sequence or nested.is_set or nested.is_tuple
                    )
                array_type.data_types = [item_type]
            if literal_values:
                literal_validation(root_model, literal_values)
        return root_data_type

    def parse_all_of(  # noqa: PLR0911
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        ignore_duplicate_model: bool = False,  # noqa: FBT001, FBT002
    ) -> DataType:
        """Parse allOf schema into a single data type with combined properties."""
        if self._contains_false_schema(obj.allOf):
            self._raise_unsatisfiable_schema(path, "allOf")

        single_ref_result = self._parse_all_of_single_ref(name, obj, path)
        if single_ref_result is not None:
            return single_ref_result

        if merged_root := self._merge_all_of_root_schema(obj):
            if self.generate_schema_validators and hasattr(self.data_model_root_type, "add_literal_validation"):
                return self._parse_all_of_root_value(name, merged_root, path)
            if (
                merged_root.enum
                and not self.ignore_enum_constraints
                and not self.should_parse_enum_as_literal(merged_root, property_name=name)
            ):
                return self.parse_enum(name, merged_root, path)
            return (
                self.parse_array(name, merged_root, path)
                if merged_root.is_array
                else self.parse_root_type(name, merged_root, path)
            )

        merged_all_of_obj = self._merge_all_of_object(obj)
        if merged_all_of_obj:
            return self._parse_object_common_part(
                name,
                merged_all_of_obj,
                path,
                ignore_duplicate_model=ignore_duplicate_model,
                fields=[],
                base_classes=[],
                required=[],
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
        if not union_models:
            return self._parse_object_common_part(
                name,
                obj,
                path,
                ignore_duplicate_model=ignore_duplicate_model,
                fields=fields,
                base_classes=base_classes,
                required=required,
            )
        reference = self.model_resolver.add(path, name, class_name=True, loaded=True)
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)
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
        data_type = self.data_type(
            data_types=[
                self._parse_object_common_part(
                    name,
                    obj,
                    get_special_path(f"union_model-{index}", path),
                    ignore_duplicate_model=ignore_duplicate_model,
                    fields=[],
                    base_classes=[union_model, all_of_data_type.reference],
                    required=[],
                )
                for index, union_model in enumerate(union_models)
            ]
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

            if field.has_ref_with_schema_keywords and not field.is_ref_with_nullable_only:
                field = self._merge_ref_with_schema(field)  # noqa: PLW2901

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
        """Build the output model's typed extra field for schema-valued extras."""
        if (
            self.data_model_type.TYPED_EXTRA_FIELD_NAME is None
            or not isinstance(obj.additionalProperties, JsonSchemaObject)
            or self._has_pattern_properties_validator(obj)
        ):
            return None

        additional_props = obj.additionalProperties
        if additional_props.has_ref_with_schema_keywords and not additional_props.is_ref_with_nullable_only:
            additional_props = self._merge_ref_with_schema(additional_props)
        if additional_props.allOf and self._contains_false_schema(additional_props.allOf):
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
                schema = self.SCHEMA_OBJECT_TYPE.model_validate(
                    schema.model_dump(exclude={"title"}, exclude_unset=True, by_alias=True)
                )
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
        """Build a dict root field without leaking value constraints onto the container."""
        additional_props_type = None
        if not self._has_pattern_properties_validator(obj):
            match obj.additionalProperties:
                case JsonSchemaObject() as additional_properties:
                    additional_props_type = self._parse_additional_properties_value(
                        name,
                        [*path, "additionalProperties"],
                        obj,
                        additional_properties=additional_properties,
                    )

        additional_props_field = self.SCHEMA_OBJECT_TYPE.model_validate({
            "minProperties": obj.minProperties,
            "maxProperties": obj.maxProperties,
            "propertyNames": obj.propertyNames,
            "additionalProperties": obj.additionalProperties,
        })

        return self.get_object_field(
            field_name=None,
            field=additional_props_field,
            required=True,
            original_field_name=None,
            field_type=self.data_type(
                data_types=[
                    additional_props_type
                    if additional_props_type is not None
                    else self.data_type_manager.get_data_type(Types.any)
                ],
                is_dict=True,
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
                if isinstance(item, JsonSchemaObject) and item.nullable and not item.type_has_null:
                    updated_items.append(self.SCHEMA_OBJECT_TYPE.model_validate({"type": "null"}))

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
            ),
            class_name=class_name,
        )
        has_declared_fields = bool(fields)
        if has_declared_fields and (extra_field := self._get_typed_additional_properties_field(class_name, obj, path)):
            fields.insert(0, extra_field)
        should_parse_dict_root = not has_declared_fields and (
            isinstance(obj.additionalProperties, JsonSchemaObject) or self._should_parse_empty_object_as_dict(obj)
        )
        if not should_parse_dict_root:
            data_model_type_class = self.data_model_type
        else:
            fields.append(self._get_additional_properties_root_field(name, obj, path))
            data_model_type_class = self.data_model_root_type

        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)
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

        separate_model_fields = self._get_separate_model_fields(fields, None)
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
                nullable=obj.type_has_null,
                keyword_only=self.keyword_only,
                treat_dot_as_module=self.treat_dot_as_module,
                dataclass_arguments=self.dataclass_arguments,
            )
            self.generation_store.register_model(data_model_type)
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
        """Parse patternProperties into a dict data type with regex keys."""
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

        data_types: list[DataType] = []
        for patterns, value_type in groups.values():
            merged_pattern = patterns[0] if len(patterns) == 1 else "|".join(patterns)
            key_type = self.data_type_manager.get_data_type(
                Types.string,
                pattern=merged_pattern,
            )
            if isinstance(property_names, JsonSchemaObject):
                merged_property_names = property_names.model_copy(deep=True)
                merged_property_names.type = "string"
                merged_property_names.pattern = (
                    merged_pattern
                    if merged_property_names.pattern is None
                    else _intersect_patterns((merged_property_names.pattern, merged_pattern))
                )
                if merged_property_names.ref:
                    merged_property_names = self._merge_ref_with_schema(merged_property_names)
                key_type = self._parse_property_name_key_schema(merged_property_names)
            data_types.append(
                self.data_type(
                    data_types=[value_type],
                    is_dict=True,
                    dict_key=key_type,
                )
            )

        return self.data_type(data_types=data_types)

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
            string_enums = [value for value in property_names.enum if isinstance(value, str)]
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
            return self.data_type_manager.get_data_type(Types.string, **kwargs)
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
                discard_nonstring
                and schema.pattern
                and _literal_pattern_value(schema.pattern.removeprefix("^")) is None
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
        return self.data_type(data_types=data_types), False

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
                return self._parse_property_name_key_schema(self.SCHEMA_OBJECT_TYPE.model_validate(merged))
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
            const_enum_data = self._extract_const_enum_from_combined(combined_items, item.type)
            if const_enum_data is None:
                return True
            enum_values, varnames, descriptions, enum_type, nullable = const_enum_data
            synthetic_obj = self._create_synthetic_enum_obj(
                item, enum_values, varnames, descriptions, enum_type, nullable
            )
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
            and not (item.enum and not self.ignore_enum_constraints)
        )
        return bool(is_primitive)

    def _parse_combined_const_enum(
        self,
        name: str,
        obj: JsonSchemaObject,
        combined_items: list[JsonSchemaObject | bool],
        enum_path: list[str],
        *,
        singular_name: bool = False,
    ) -> DataType | None:
        const_enum_data = self._extract_const_enum_from_combined(combined_items, obj.type)
        if const_enum_data is None:
            return None

        enum_values, varnames, descriptions, enum_type, nullable = const_enum_data
        synthetic_obj = self._create_synthetic_enum_obj(obj, enum_values, varnames, descriptions, enum_type, nullable)
        if self.should_parse_enum_as_literal(synthetic_obj, property_name=name, property_obj=obj):
            return self.parse_enum_as_literal(synthetic_obj)
        return self.parse_enum(name, synthetic_obj, enum_path, singular_name=singular_name)

    def parse_item(  # noqa: PLR0911, PLR0912, PLR0914, PLR0915
        self,
        name: str,
        item: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        parent: JsonSchemaObject | None = None,
    ) -> DataType:
        """Parse a single JSON Schema item into a data type."""
        python_type_override = self._get_python_type_override(item)
        if python_type_override:
            return python_type_override
        if self.use_title_as_name and item.title:
            name = sanitize_module_name(item.title, treat_dot_as_module=self.treat_dot_as_module)
            singular_name = False
        if self._should_create_type_alias_for_title(item, name):
            return self.parse_root_type(name, item, path)
        if (
            item.has_ref_with_schema_keywords
            and not item.is_ref_with_nullable_only
            and (merged_item := self._merge_ref_with_schema(item)) is not item
        ):
            item = merged_item
        if parent and not item.enum:
            has_materialized_container_constraints = bool(
                item.__dict__.get(_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY)
                and (item.is_array or item.is_object or item.propertyNames is not None)
                and self._has_effective_constraints(item)
            )
            if has_materialized_container_constraints or (
                item.has_constraint and (parent.has_constraint or self.field_constraints)
            ):
                root_type_path = get_special_path("array", path)
                return self._parse_root_type_with_context(
                    self.model_resolver.add(
                        root_type_path,
                        name,
                        class_name=True,
                        singular_name=singular_name,
                    ).name,
                    item,
                    root_type_path,
                    preserve_constraints=has_materialized_container_constraints,
                )
        if item.recursiveRef and not item.ref:
            return self.get_ref_data_type(self._resolve_recursive_ref(item, path) or "#")
        if item.dynamicRef and not item.ref:
            return self._get_dynamic_ref_data_type(item, item.dynamicRef)
        if item.is_ref_with_nullable_only and item.ref:
            ref_data_type = self.get_ref_data_type(item.ref)
            if self.strict_nullable:
                return self.data_type(data_types=[ref_data_type], is_optional=True)
            return ref_data_type
        if item.has_ref_with_schema_keywords:
            item = self._merge_ref_with_schema(item)
        if item.ref:
            return self.get_ref_data_type(item.ref)
        if item.custom_type_path:  # pragma: no cover
            return self.data_type_manager.get_data_type_from_full_path(
                _validate_schema_python_import_path(item.custom_type_path, "customTypePath"),
                is_custom_type=True,
            )
        shared_numeric_constraints = self._has_shared_numeric_union_constraints(item)
        if (
            not shared_numeric_constraints
            and (union_type := self._parse_constrained_type_union(name, item, path)) is not None
        ):
            return union_type
        if item.is_array:
            return self.parse_array_fields(name, item, get_special_path("array", path)).data_type
        if item.discriminator and parent and parent.is_array and (item.oneOf or item.anyOf):
            return self.parse_root_type(name, item, path)
        if self.generate_schema_validators and self._should_parse_object_with_schema_validators(item):
            return self.parse_object(name, item, get_special_path("object", path), singular_name=singular_name)
        if item.anyOf:
            if combined_const_enum := self._parse_combined_const_enum(
                name,
                item,
                item.anyOf,
                get_special_path("enum", path),
                singular_name=singular_name,
            ):
                return combined_const_enum
            return self.data_type(data_types=self.parse_any_of(name, item, get_special_path("anyOf", path)))
        if item.oneOf:
            if combined_const_enum := self._parse_combined_const_enum(
                name,
                item,
                item.oneOf,
                get_special_path("enum", path),
                singular_name=singular_name,
            ):
                return combined_const_enum
            return self.data_type(data_types=self.parse_one_of(name, item, get_special_path("oneOf", path)))
        if item.allOf:
            if self._contains_false_schema(item.allOf):
                self._raise_unsatisfiable_schema(get_special_path("allOf", path), "allOf")
            all_of_items = [sub_item for sub_item in item.allOf if isinstance(sub_item, JsonSchemaObject)]
            if len(all_of_items) == 1 and len(all_of_items) != len(item.allOf) and not item.properties:
                return self.parse_item(name, all_of_items[0], path, singular_name=singular_name, parent=parent)
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
                    return self.get_ref_data_type(single_item.ref)
            all_of_path = get_special_path("allOf", path)
            all_of_path = [self.model_resolver.resolve_ref(all_of_path)]
            return self.parse_all_of(
                self.model_resolver.add(all_of_path, name, singular_name=singular_name, class_name=True).name,
                item,
                all_of_path,
                ignore_duplicate_model=True,
            )
        if item.is_object or item.patternProperties or item.propertyNames is not None:
            object_path = get_special_path("object", path)
            if item.properties:
                if item.has_multiple_types and isinstance(item.type, list):
                    data_types: list[DataType] = []
                    data_types.append(self.parse_object(name, item, object_path, singular_name=singular_name))
                    data_types.extend(self._iter_non_object_union_types(name, item, item.type, path))
                    return self.data_type(data_types=data_types)
                return self.parse_object(name, item, object_path, singular_name=singular_name)
            if item.patternProperties:
                # support only single key dict.
                return self.parse_pattern_properties(
                    name, item.patternProperties, object_path, property_names=item.propertyNames
                )
            if item.propertyNames is not None:
                return self.parse_property_names(
                    name, item.propertyNames, item.additionalProperties, object_path, parent_obj=item
                )
            if isinstance(item.additionalProperties, JsonSchemaObject):
                additional_props_type = self._parse_additional_properties_value(
                    name,
                    get_special_path("additionalProperties", object_path),
                    item,
                    additional_properties=item.additionalProperties,
                )
                python_type_flags = self._get_python_type_flags(item)
                dict_flags = python_type_flags or {"is_dict": True}
                return self.data_type(
                    data_types=[additional_props_type],
                    **dict_flags,
                )
            return self.data_type_manager.get_data_type(
                Types.object,
            )
        if item.enum and not self.ignore_enum_constraints:
            if self.should_parse_enum_as_literal(item, property_name=name):
                return self.parse_enum_as_literal(item)
            return self.parse_enum(name, item, get_special_path("enum", path), singular_name=singular_name)
        return (
            self._get_data_type(item, localize_constraints=False)
            if shared_numeric_constraints
            else self.get_data_type(item)
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
            and bool(self.data_type_manager.CONSTRAINED_TYPE_CONSUMED_KEYS)
            and isinstance(obj.type, list)
            and all(type_ in {"integer", "number", "null"} for type_ in obj.type)
            and all(getattr(obj, key) is None for key in ("minItems", "maxItems", *_VALUE_STRING_CONSTRAINT_KEYS))
        )

    def _parse_constrained_type_union(self, name: str, obj: JsonSchemaObject, path: list[str]) -> DataType | None:
        """Localize union branches that need annotated aliases instead of constrained scalar types."""
        if (
            not isinstance(obj.type, list)
            or not self._should_skip_root_field_constraints_for_multiple_types(obj)
            or obj.is_array
            or obj.properties
        ):
            return None
        if obj.anyOf or obj.oneOf or obj.allOf or obj.enum or obj.extras.get("x-python-type"):
            return None
        if not self._output_model_context.supports_internal_annotated_constraints or not (
            self._get_inherited_constraint_fields(obj) or obj.propertyNames is not None
        ):
            return None
        if self.data_type_manager.CONSTRAINED_TYPE_CONSUMED_KEYS:
            if "array" not in obj.type and "object" not in obj.type:
                return None
        elif not self._output_model_context.supports_annotated_constraints:
            return None
        data_types = []
        for type_ in obj.type:
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
                data_type = self._parse_array_union_constrained_branch(name, obj, path, type_)
            data_types.append(data_type)
        return self.data_type(data_types=data_types, is_optional=obj.type_has_null)

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
    ) -> None:
        """Add type-union branches that array parsing does not materialize itself."""
        if not non_array_types or (obj.enum and not self.ignore_enum_constraints):
            return

        data_types[:0] = (
            self._parse_array_union_constrained_branch(
                name,
                obj,
                path,
                type_,
            )
            if localize_constraints
            else self.data_type_manager.get_data_type(self._get_type_with_mappings(type_, obj.format or "default"))
            for type_ in non_array_types
            if type_ != "object" or not obj.is_object
        )

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
        return self.SCHEMA_OBJECT_TYPE.model_validate(branch_schema)

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
            return fallback_data_type or self.data_type_manager.get_data_type(
                self._get_type_with_mappings(type_, obj.format or "default")
            )

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

        python_type_flags = self._get_python_type_flags(obj)
        container_flags: dict[str, bool] = {}
        if not is_tuple:
            container_flags = python_type_flags or {"is_list": True}

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
        self._add_array_union_non_array_types(
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
            data_types.append(self.parse_enum(name, obj, get_special_path("enum", path)))
        constraints = self._get_array_constraints(obj)
        if non_array_types:
            constraints = {}
        if suppress_item_constraints:
            self._suppress_array_length_constraints(constraints, obj)
        return self.data_model_field_type(
            data_type=self.data_type(
                data_types=data_types,
                is_optional=isinstance(obj.type, list) and obj.type_has_null,
            ),
            default=obj.default,
            required=required,
            constraints=constraints,
            nullable=nullable,
            strip_default_none=self.strip_default_none,
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
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)
        field = self.parse_array_fields(original_name or name, obj, [*path, name])

        non_array_types = self._get_array_union_non_array_types(obj)
        direct_data_types = field.data_type.data_types
        if (
            not self._should_localize_array_union_constraints(obj, non_array_types)
            and direct_data_types
            and any(
                data_type.reference == reference
                for data_type in direct_data_types[0].all_data_types
                if data_type.reference
            )
            and (len(direct_data_types) > 1 or not non_array_types)
        ):
            # self-reference
            recursive_tuple_item_count = direct_data_types[0].tuple_item_count
            field = self.data_model_field_type(
                data_type=self.data_type(
                    data_types=[
                        self.data_type(
                            data_types=direct_data_types[1:],
                            is_list=recursive_tuple_item_count is None,
                            is_tuple=recursive_tuple_item_count is not None,
                            tuple_item_count=recursive_tuple_item_count,
                        ),
                        *direct_data_types[1:],
                    ]
                ),
                default=field.default,
                required=field.required,
                constraints=field.constraints,
                nullable=field.nullable,
                strip_default_none=field.strip_default_none,
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
        effective_use_annotated = self.use_annotated if use_annotated is None else use_annotated
        if obj.ref:
            data_type: DataType = self.get_ref_data_type(obj.ref)
        elif obj.custom_type_path:
            data_type = self.data_type_manager.get_data_type_from_full_path(
                _validate_schema_python_import_path(obj.custom_type_path, "customTypePath"),
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
            if const_enum_type := self._parse_combined_const_enum(name, obj, combined_items, path):
                data_type = const_enum_type  # pragma: no cover
            else:
                reference = self.model_resolver.add(path, name, loaded=True, class_name=True)
                if obj.anyOf:
                    data_types: list[DataType] = self.parse_any_of(name, obj, get_special_path("anyOf", path))
                else:
                    data_types = self.parse_one_of(name, obj, get_special_path("oneOf", path))

                if len(data_types) > 1:  # pragma: no cover
                    data_type = self.data_type(data_types=data_types)
                elif not data_types:  # pragma: no cover
                    return EmptyDataType()
                else:  # pragma: no cover
                    data_type = data_types[0]
        elif obj.allOf:
            data_type = self._build_lightweight_type(obj)
            if data_type is None:  # pragma: no cover
                data_type = self.data_type_manager.get_data_type(Types.any)
        elif obj.patternProperties:
            data_type = self.parse_pattern_properties(
                name, obj.patternProperties, path, property_names=obj.propertyNames
            )
        elif obj.propertyNames is not None:
            data_type = self.parse_property_names(
                name, obj.propertyNames, obj.additionalProperties, path, parent_obj=obj
            )
        elif obj.is_object and not obj.properties and isinstance(obj.additionalProperties, JsonSchemaObject):
            additional_props_type = self._parse_additional_properties_value(
                name,
                get_special_path("additionalProperties", path),
                obj,
                additional_properties=obj.additionalProperties,
            )
            python_type_flags = self._get_python_type_flags(obj)
            dict_flags = python_type_flags or {"is_dict": True}
            data_type = self.data_type(
                data_types=[additional_props_type],
                **dict_flags,
            )
        elif obj.enum and not self.ignore_enum_constraints:
            if self.should_parse_enum_as_literal(obj, property_name=name):
                data_type = self.parse_enum_as_literal(obj)
            else:  # pragma: no cover
                data_type = self.parse_enum(name, obj, path)
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
        if self.force_optional_for_required_fields:
            required = False
            nullable = None
            has_default_override = True
            default_value = obj.default if obj.has_default else None
        elif obj.nullable:
            required = False
            nullable = True
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
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)
        constraints = array_constraints or (
            self._get_constraint_values(obj) if self.field_constraints or preserve_constraints else {}
        )
        if self._should_skip_root_field_constraints_for_multiple_types(obj):
            constraints = {}
        elif self.field_constraints and obj.format == "hostname":
            constraints["pattern"] = self.data_type_manager.HOSTNAME_REGEX
        if data_type.is_dict or data_type.is_mapping:
            constraints.update(self._get_property_count_constraints(obj))
        fields = [
            self.data_model_field_type(
                data_type=data_type,
                default=default_value,
                required=required,
                constraints=constraints,
                nullable=nullable,
                strip_default_none=self.strip_default_none,
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
        self, name: str, obj: JsonSchemaObject, type_list: list[str], path: list[str]
    ) -> Iterator[DataType]:
        """Retain scalar sibling constraints while preserving the existing object-first union order."""
        localize_constraints = (
            self.field_constraints
            and self._output_model_context.supports_internal_annotated_constraints
            and (
                self.data_type_manager.CONSTRAINED_TYPE_CONSUMED_KEYS
                or self._output_model_context.supports_annotated_constraints
            )
            and self._get_inherited_constraint_fields(obj)
        )
        for type_ in type_list:
            if type_ not in {"object", "null"}:
                yield (
                    self._parse_array_union_constrained_branch(name, obj, path, type_)
                    if localize_constraints
                    else self.data_type_manager.get_data_type(
                        self._get_type_with_mappings(type_, obj.format or "default")
                    )
                )

    def _parse_multiple_types_with_properties(
        self,
        name: str,
        obj: JsonSchemaObject,
        type_list: list[str],
        path: list[str],
    ) -> None:
        """Parse a schema with multiple types including object with properties."""
        data_types: list[DataType] = []

        object_path = get_special_path("object", path)
        object_data_type = self.parse_object(name, obj, object_path)
        data_types.append(object_data_type)

        data_types.extend(self._iter_non_object_union_types(name, obj, type_list, path))

        is_nullable = obj.nullable or obj.type_has_null
        reference = self.model_resolver.add(path, name, loaded=True, class_name=True)
        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)

        constraints = self._get_constraint_values(obj) if self.field_constraints else {}
        if self._should_skip_root_field_constraints_for_multiple_types(obj):
            constraints = {}
        self._register_root_model(
            reference=reference,
            fields=[
                self.data_model_field_type(
                    data_type=self.data_type(data_types=data_types),
                    default=obj.default,
                    required=not (self.force_optional_for_required_fields or is_nullable),
                    constraints=constraints,
                    nullable=obj.type_has_null if (self.strict_nullable or self.use_missing_sentinel) else None,
                    strip_default_none=self.strip_default_none,
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

    def parse_enum_as_literal(self, obj: JsonSchemaObject) -> DataType:
        """Parse enum values as a Literal type."""
        literal_values, non_literal_values, has_null = self._partition_enum_values(obj.enum)
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
        return self.data_type(
            data_types=data_types,
            is_optional=has_null,
        )

    def _get_enum_model_class(self, type_: Types | None, enum_values: list[Any]) -> tuple[type[Enum], Types | None]:
        """Return the enum model class and remaining subtype for schema enum generation."""
        if not (self.use_specialized_enum and type_ and (specialized_type := SPECIALIZED_ENUM_TYPE_MATCH.get(type_))):
            return Enum, type_

        match specialized_type:
            case _ if specialized_type is StrEnum:
                if not self.target_python_version.has_strenum or not all(
                    isinstance(enum_value, str) for enum_value in enum_values
                ):
                    return Enum, type_
            case _:
                pass

        return specialized_type, None

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

    def parse_enum(  # noqa: PLR0912, PLR0915
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

        if None in obj.enum and (obj.type == "string" or obj.nullable):
            nullable: bool = True
            enum_times = [e for e in obj.enum if e is not None]
        else:
            enum_times = obj.enum
            nullable = False

        exclude_field_names: set[str] = set()

        enum_names = obj.x_enum_varnames or obj.x_enum_names
        enum_descriptions = obj.x_enum_descriptions

        for i, enum_part in enumerate(enum_times):
            match enum_part:
                case str():
                    default = EnumMemberValue(enum_part)
                case None:
                    default = NULL_ENUM_MEMBER_VALUE
                case _:
                    default = enum_part
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
                field_name, excludes=exclude_field_names, model_type=ModelType.ENUM
            )
            exclude_field_names.add(field_name)
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
            self._set_schema_metadata(reference.path, obj)
            self.set_schema_extensions(reference.path, obj)
            self._register_root_model(
                reference=reference,
                fields=[
                    self.data_model_field_type(
                        data_type=self.data_type_manager.get_data_type(Types.null),
                        default=obj.default,
                        required=False,
                        nullable=True,
                        strip_default_none=self.strip_default_none,
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
            type_: Types | None = (
                self._get_type_with_mappings(obj.type, obj.format) if isinstance(obj.type, str) else None
            )
            enum_cls, type_ = self._get_enum_model_class(type_, enum_times)
            self._set_schema_metadata(reference_.path, obj)
            self.set_schema_extensions(reference_.path, obj)

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
            self.generation_store.register_model(enum)
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

        if not nullable:
            return create_enum(reference)

        self._set_schema_metadata(reference.path, obj)
        self.set_schema_extensions(reference.path, obj)

        enum_reference = self.model_resolver.add(
            [*path, "Enum"],
            f"{reference.name}Enum",
            class_name=True,
            singular_name=singular_name,
            singular_name_suffix="Enum",
            loaded=True,
            model_type="enum",
        )

        self._register_root_model(
            reference=reference,
            fields=[
                self.data_model_field_type(
                    data_type=create_enum(enum_reference),
                    default=obj.default,
                    required=False,
                    nullable=True,
                    strip_default_none=self.strip_default_none,
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
                        f"Fetching remote $ref is disabled: {resolved_ref}\n"
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
                            f"Reference: {resolved_ref}. Pass --allow-remote-refs for trusted remote schemas, "
                            "or --no-allow-remote-refs to block HTTP(S) $ref fetching. Internal network targets "
                            "also require --allow-private-network."
                        ),
                        stacklevel=2,
                    )
            return self._prepare_schema_resources(self._get_ref_body_from_url(resolved_ref), [resolved_ref])
        return self._prepare_schema_resources(self._get_ref_body_from_remote(resolved_ref), [resolved_ref])

    def _normalize_external_ref(self, ref: str) -> str:
        """Resolve an external anchor before falling back to legacy shorthand pointers."""
        if get_ref_type(ref) == JSONReference.LOCAL:
            return ref

        resolved_ref = self.model_resolver.resolve_ref(ref)
        if get_ref_type(resolved_ref) == JSONReference.LOCAL:
            return ref

        relative_path, separator, object_path = resolved_ref.partition("#")
        if not separator or not object_path or object_path.startswith("/"):
            return ref

        ref_body = self._get_ref_body(relative_path)
        if (anchor_pointer := _find_json_schema_anchor_pointer(ref_body, object_path)) is not None:
            return f"{relative_path}#{'' if anchor_pointer == '/' else anchor_pointer}"
        return f"{relative_path}#/{object_path}"

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

    def _load_ref_data_from_path(self, path: Path) -> dict[str, YamlValue]:
        """Load one referenced path and contextualize only its decode failures."""
        try:
            return load_data_from_path(path, self.encoding)
        except (json.JSONDecodeError, TypeError, *get_yaml_parse_errors()) as exc:
            raise InvalidFileFormatError(exc, self._input_file_type, source=path) from exc

    def _load_ref_data_from_text(self, text: str, source: str) -> dict[str, YamlValue]:
        """Decode one referenced text body and contextualize only that operation."""
        try:
            return load_data(text)
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
            return result
        msg = f"Expected dict, got {type(result).__name__}"
        raise InvalidFileFormatError(TypeError(msg), self._input_file_type, source=path)

    def _get_ref_body_from_local_http_path(self, ref: str) -> dict[str, YamlValue]:
        assert self.http_local_ref_path is not None
        parsed = urlparse(ref)
        if parsed.scheme not in {"http", "https"}:  # pragma: no cover
            msg = f"Unsupported local HTTP $ref URL: {ref}"
            raise Error(msg)

        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if not parsed.netloc or any(part in {".", ".."} or "/" in part or "\\" in part for part in parts):
            msg = f"Unsupported local HTTP $ref URL path: {ref}"
            raise Error(msg)

        base_path = self.http_local_ref_path.resolve()
        relative_path = Path(parsed.netloc, *parts)
        file_paths = [(base_path / relative_path).resolve()]
        if not parts or not Path(parts[-1]).suffix:
            file_paths.append((base_path / relative_path.with_name(f"{relative_path.name}.json")).resolve())

        if any(not file_path.is_relative_to(base_path) for file_path in file_paths):
            msg = f"Unsupported local HTTP $ref URL path: {ref}"
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

        msg = f"$ref local file not found for {ref}: tried {', '.join(str(path) for path in file_paths)}"
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

        # If the ref is mapped to an external package, mark as loaded and skip parsing
        if self._resolve_external_ref_mapping(object_ref) is not None:
            reference = self.model_resolver.add_ref(object_ref)
            reference.loaded = True
            return reference

        if self._has_embedded_schema_resources:
            document = self._schema_resource_document(list(self.model_resolver.current_root))
            object_ref = self._resolve_schema_resource_ref(
                object_ref, self._schema_resource_root_bases.get(document, document), document, nested_scope=False
            )
        # https://swagger.io/docs/specification/using-ref/
        object_ref = self._normalize_external_ref(object_ref)
        ref = self.model_resolver.resolve_ref(object_ref)
        if get_ref_type(object_ref) == JSONReference.LOCAL or get_ref_type(ref) == JSONReference.LOCAL:
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

    def _traverse_schema_objects(  # noqa: PLR0912
        self,
        obj: JsonSchemaObject,
        path: list[str],
        callback: Callable[[JsonSchemaObject, list[str]], None],
        *,
        include_one_of: bool = True,
    ) -> None:
        """Traverse schema objects recursively and apply callback."""
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
                self._traverse_schema_objects(item, [*path, "items"], callback, include_one_of=include_one_of)
            case list() as items:
                for index, item in enumerate(items):
                    if isinstance(item, JsonSchemaObject):
                        self._traverse_schema_objects(
                            item,
                            [*path, "items", str(index)],
                            callback,
                            include_one_of=include_one_of,
                        )
        if obj.prefixItems:
            for index, item in enumerate(obj.prefixItems):
                if isinstance(item, JsonSchemaObject):
                    self._traverse_schema_objects(
                        item,
                        [*path, "prefixItems", str(index)],
                        callback,
                        include_one_of=include_one_of,
                    )
        if isinstance(obj.additionalProperties, JsonSchemaObject):
            self._traverse_schema_objects(
                obj.additionalProperties,
                [*path, "additionalProperties"],
                callback,
                include_one_of=include_one_of,
            )
        if isinstance(obj.unevaluatedProperties, JsonSchemaObject):
            self._traverse_schema_objects(
                obj.unevaluatedProperties,
                [*path, "unevaluatedProperties"],
                callback,
                include_one_of=include_one_of,
            )
        if isinstance(obj.unevaluatedItems, JsonSchemaObject):
            self._traverse_schema_objects(
                obj.unevaluatedItems,
                [*path, "unevaluatedItems"],
                callback,
                include_one_of=include_one_of,
            )
        if obj.patternProperties:
            for key, value in obj.patternProperties.items():
                if isinstance(value, JsonSchemaObject):
                    self._traverse_schema_objects(
                        value,
                        [*path, "patternProperties", key],
                        callback,
                        include_one_of=include_one_of,
                    )
        if isinstance(obj.propertyNames, JsonSchemaObject):
            self._traverse_schema_objects(
                obj.propertyNames,
                [*path, "propertyNames"],
                callback,
                include_one_of=include_one_of,
            )
        for index, item in enumerate(obj.anyOf):
            if isinstance(item, JsonSchemaObject):
                self._traverse_schema_objects(
                    item,
                    [*path, "anyOf", str(index)],
                    callback,
                    include_one_of=include_one_of,
                )
        for index, item in enumerate(obj.allOf):
            if isinstance(item, JsonSchemaObject):
                self._traverse_schema_objects(
                    item,
                    [*path, "allOf", str(index)],
                    callback,
                    include_one_of=include_one_of,
                )
        if include_one_of:
            for index, item in enumerate(obj.oneOf):
                if isinstance(item, JsonSchemaObject):
                    self._traverse_schema_objects(
                        item,
                        [*path, "oneOf", str(index)],
                        callback,
                        include_one_of=include_one_of,
                    )
        if self.generate_schema_validators:
            for keyword in ("if", "then", "else"):
                item = self._get_conditional_schema(obj, keyword)
                if isinstance(item, JsonSchemaObject):
                    self._traverse_schema_objects(
                        item,
                        [*path, keyword],
                        callback,
                        include_one_of=include_one_of,
                    )
        if obj.properties:
            for key, value in obj.properties.items():
                if isinstance(value, JsonSchemaObject):
                    self._traverse_schema_objects(
                        value,
                        [*path, "properties", key],
                        callback,
                        include_one_of=include_one_of,
                    )

    def _resolve_ref_callback(self, obj: JsonSchemaObject, path: list[str]) -> None:  # noqa: ARG002
        """Resolve $ref in schema object."""
        if obj.ref:
            self.resolve_ref(obj.ref)
        elif obj.dynamicRef and (location := self._bound_dynamic_anchor(obj.dynamicRef)) is not None:
            with self._schema_location_context(location) as ref:
                self.resolve_ref(ref)

    def _init_schema_resources(self) -> None:
        """Initialize the document-local resource indexes and normalization cache.

        Schema resource root pointers are indexed by document, and $dynamicAnchor locations by
        document and resource root pointer, so documents that share an $id keep their own anchors.
        The dynamic scope state tracks the model root being parsed, the $dynamicAnchor bindings
        imposed by the schemas that referenced it, and the models specialized for those bindings.
        """
        self._schema_resource_locations: dict[str, str] = {}
        self._schema_resource_document_aliases: dict[str, str] = {}
        self._schema_resource_root_bases: dict[str, str] = {}
        self._schema_resource_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self._schema_resource_keys: dict[str, set[str]] = {}
        self._has_embedded_schema_resources = False
        self._schema_resources: dict[str, set[str]] = {}
        self._dynamic_anchors: dict[tuple[str, str], dict[str, str]] = {}
        self._dynamic_scope_pointer = ""
        self._schema_resource_path_parts: dict[str, list[str]] = {}
        self._dynamic_document: str | None = None
        self._dynamic_scope_path: tuple[str, ...] = ()
        self._dynamic_bindings: dict[str, str] = {}
        self._dynamic_specializations: dict[tuple[str, frozenset[tuple[str, str]]], Reference] = {}
        self._dynamic_specialization_paths: set[tuple[str, ...]] = set()
        self._pending_dynamic_specializations: deque[_DynamicSpecialization] = deque()
        self._draining_dynamic_specializations = False
        self._dynamic_anchor_usage: dict[str, frozenset[str]] = {}
        self._dynamic_ref_graph: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {}

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

    def _register_schema_resources(  # noqa: PLR0913
        self,
        schema: dict[str, Any],
        document: str,
        pointer: str,
        base: str,
        keys: set[str],
        *,
        id_field: str | None,
        resource: str = "",
    ) -> bool:
        """Index resource URIs and resource-scoped anchors once for each document."""
        id_field, identifier = self._schema_resource_identifier(schema, id_field, is_root=not pointer)
        nested = bool(pointer and isinstance(identifier, str) and not identifier.startswith("#"))
        location = f"{document}#{pointer}"
        if isinstance(identifier, str):
            absolute = urljoin(base, identifier)
            self._schema_resource_locations[absolute] = location
            keys.add(absolute)
            base = absolute.split("#", 1)[0]
        if not pointer:
            self._schema_resource_root_bases[document] = base
            self._schema_resource_locations.setdefault(base, location)
            keys.add(base)
        if nested or not pointer:
            self._schema_resources.setdefault(document, set()).add(pointer)
            resource = pointer
        for keyword in ("$anchor", "$dynamicAnchor"):
            if isinstance(anchor := schema.get(keyword), str):
                absolute = urljoin(base, f"#{anchor}")
                self._schema_resource_locations[absolute] = location
                keys.add(absolute)
                if keyword == "$dynamicAnchor":
                    self._dynamic_anchors.setdefault((document, resource), {}).setdefault(anchor, location)
        for path, child in self._iter_schema_resource_children(schema):
            child_pointer = pointer + "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in path)
            nested |= self._register_schema_resources(
                child, document, child_pointer, base, keys, id_field=id_field, resource=resource
            )
        return nested

    def _resolve_schema_resource_ref(self, reference: str, base: str, document: str, *, nested_scope: bool) -> str:
        """Resolve registered resources before considering a physical document fetch."""
        absolute = urljoin(base, reference)
        resource, _, fragment = absolute.partition("#")
        location = self._schema_resource_locations.get(absolute)
        if (
            location is None
            and (not fragment or fragment.startswith("/"))
            and (root := self._schema_resource_locations.get(resource)) is not None
        ):
            location = f"{root}{fragment}" if fragment else root
        if (
            location is None
            and fragment
            and not fragment.startswith("/")
            and (root := self._schema_resource_locations.get(resource)) is not None
            and root.split("#", 1)[1]
        ):
            msg = f"Embedded schema resource has no anchor {fragment!r}: {reference!r}"
            raise Error(msg)
        if location is not None:
            target_document, pointer = location.split("#", 1)
            return f"#{pointer}" if target_document == document else location
        if nested_scope and resource.startswith("file://") and not reference.startswith("file://"):
            from urllib.request import url2pathname  # noqa: PLC0415

            absolute = Path(url2pathname(resource[5:])).as_posix() + absolute[len(resource) :]
        return absolute if nested_scope else reference

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
        for path, child in self._iter_schema_resource_children(schema):
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

    def _prepare_schema_resources(self, raw: dict[str, Any], path_parts: list[str]) -> dict[str, Any]:
        """Cache resource indexing and normalization instead of rescanning on each reference."""
        document = self._schema_resource_document(path_parts)
        self._schema_resource_path_parts.setdefault(document, path_parts)
        if (cached := self._schema_resource_cache.get(document)) is not None and (raw is cached[0] or raw is cached[1]):
            return cached[1]
        for key in self._schema_resource_keys.get(document, ()):
            if self._schema_resource_locations.get(key, "").split("#", 1)[0] == document:
                self._schema_resource_locations.pop(key, None)
        for resource in self._schema_resources.pop(document, ()):
            self._dynamic_anchors.pop((document, resource), None)
        keys: set[str] = set()
        base = document if is_url(document) else Path(document).as_uri() if document else f"{self.base_path.as_uri()}/"
        id_field = (
            self.schema_features.id_field
            if getattr(self.config, "jsonschema_version", None) not in {None, JsonSchemaVersion.Auto}
            else None
        )
        nested = self._register_schema_resources(raw, document, "", base, keys, id_field=id_field)
        self._schema_resource_keys[document] = keys
        prepared = raw
        if nested:
            self._has_embedded_schema_resources = True
            prepared = self._rewrite_schema_resource_refs(
                raw, document, base, self._schema_resource_root_bases[document], id_field, is_root=True
            )
        self._schema_resource_cache[document] = raw, prepared
        return prepared

    def _add_id_callback(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Add $id and $anchor to model resolver."""
        if obj.id:
            self.model_resolver.add_id(obj.id, path)
        if obj.anchor:
            self.model_resolver.add_id(f"#{obj.anchor}", path)

    def parse_ref(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Recursively parse all $ref references in a schema object."""
        self._traverse_schema_objects(obj, path, self._resolve_ref_callback)
        self._parse_dynamic_specializations()

    def parse_id(self, obj: JsonSchemaObject, path: list[str]) -> None:
        """Recursively parse all $id fields in a schema object."""
        self._traverse_schema_objects(obj, path, self._add_id_callback, include_one_of=False)

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
            return self.SCHEMA_OBJECT_TYPE.model_validate(raw)
        except SchemaParseError:
            raise
        except Exception as e:
            raise SchemaParseError(
                message=f"{type(e).__name__}: {e}",
                path=path,
                original_error=e,
            ) from e

    def parse_raw_obj(
        self,
        name: str,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
    ) -> None:
        """Parse a raw dictionary into a JsonSchemaObject and process it."""
        self._parse_raw_or_validated_obj(name, raw, path)

    def _parse_raw_or_validated_obj(
        self,
        name: str,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
        validated_obj: JsonSchemaObject | None = None,
    ) -> None:
        """Parse a raw schema, reusing a validated object when available."""
        if isinstance(raw, dict) and "x-python-import" in raw:
            self._handle_python_import(name, path)
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

        # Check boolean schemas (Draft 6+)
        if isinstance(raw, bool):
            if not self.schema_features.boolean_schemas:
                version_name = "Draft 4" if self.schema_features.id_field == "id" else "this version"
                warn(
                    f"Boolean schemas are not supported in {version_name}. Schema path: {'/'.join(path)}",
                    stacklevel=3,
                )
            return

        # Check null in type array (JSON Schema / OpenAPI 3.1+)
        type_value = raw.get("type")
        if isinstance(type_value, list) and "null" in type_value and not self.schema_features.null_in_type_array:
            warn(
                'null in type array (e.g., type: ["string", "null"]) is not supported '
                f"in this schema version. Use nullable: true instead. Schema path: {'/'.join(path)}",
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
                    f"Schema path: {'/'.join(path)}",
                    stacklevel=3,
                )
            if isinstance(exclusive_max, bool):
                warn(
                    f"exclusiveMaximum as boolean is Draft 4 style, but schema version uses numeric style. "
                    f"Schema path: {'/'.join(path)}",
                    stacklevel=3,
                )
        else:
            # Draft 4: should be boolean, not numeric
            if exclusive_min is not None and not isinstance(exclusive_min, bool):
                warn(
                    f"exclusiveMinimum as number is Draft 6+ style, but schema version is Draft 4. "
                    f"Schema path: {'/'.join(path)}",
                    stacklevel=3,
                )
            if exclusive_max is not None and not isinstance(exclusive_max, bool):
                warn(
                    f"exclusiveMaximum as number is Draft 6+ style, but schema version is Draft 4. "
                    f"Schema path: {'/'.join(path)}",
                    stacklevel=3,
                )

        if not self.schema_features.read_only_write_only:
            if raw.get("readOnly") is True:
                warn(
                    f"readOnly is not supported in this schema version (Draft 7+ only). Schema path: {'/'.join(path)}",
                    stacklevel=3,
                )
            if raw.get("writeOnly") is True:
                warn(
                    f"writeOnly is not supported in this schema version (Draft 7+ only). Schema path: {'/'.join(path)}",
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

        # Check prefixItems usage (Draft 2020-12+ only)
        if obj.prefixItems is not None and not self.schema_features.prefix_items:
            warn(
                f"prefixItems is not supported in this schema version. "
                f"Use items as array for tuple validation. Schema path: {'/'.join(path)}",
                stacklevel=4,
            )

        # Check items as array usage (deprecated in Draft 2020-12)
        if isinstance(obj.items, list) and self.schema_features.prefix_items:
            warn_deprecated(
                "schema.jsonschema-items-array",
                details=f"Schema path: {'/'.join(path)}",
                stacklevel=4,
            )

    def _handle_python_import(
        self,
        name: str,
        path: list[str],
    ) -> None:
        """Mark x-python-import reference as loaded to skip model generation."""
        self.model_resolver.add(path, name, class_name=True, loaded=True)

    def _is_named_schema_definition_path(self, path: list[str]) -> bool:
        """Check if path points to a named schema entry under definitions/$defs."""
        current_root = list(self.model_resolver.current_root)
        if len(path) < len(current_root) + 2:
            return False

        schema_container_path = path[len(current_root)]
        return path[: len(current_root)] == current_root and any(
            schema_container_path == schema_path for schema_path, _ in self.schema_paths
        )

    def _is_current_root_schema_path(self, path: list[str]) -> bool:
        current_root = list(self.model_resolver.current_root)
        current_root_path = current_root or ["#"]
        return path == current_root_path or self.model_resolver.resolve_ref(path) == self.model_resolver.resolve_ref(
            current_root or "#"
        )

    def _drop_ref_from_schema(self, obj: JsonSchemaObject) -> JsonSchemaObject:
        return self.SCHEMA_OBJECT_TYPE.model_validate(
            obj.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True)
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
        if not (self._has_embedded_schema_resources or self._dynamic_anchors):
            self._parse_schema_obj(name, obj, path)
            return
        with self._dynamic_scope_context(path, {}):
            self._parse_schema_obj(name, obj, path)

    def _parse_schema_obj(  # noqa: PLR0912
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> None:
        """Dispatch a JsonSchemaObject to the parse method for its schema shape."""
        if obj.has_ref_with_schema_keywords and not obj.is_ref_with_nullable_only:
            if obj.ref == "#" and self._is_current_root_schema_path(path):
                obj = self._drop_ref_from_schema(obj)
            else:
                obj = self._merge_ref_with_schema(obj)
            if obj.ref:
                if self._is_named_schema_definition_path(path) or tuple(path) in self._dynamic_specialization_paths:
                    self.parse_root_type(name, obj, path)
                self.parse_ref(obj, path)
                return

        if obj.is_array:
            self.parse_array(name, obj, path)
        elif obj.allOf and (obj.oneOf or obj.anyOf):
            self.parse_root_type(name, obj, path)
        elif obj.allOf:
            self.parse_all_of(name, obj, path)
        elif self.generate_schema_validators and self._should_parse_object_with_schema_validators(obj):
            self.parse_object(name, obj, path)
        elif obj.oneOf or obj.anyOf:
            combined_items = obj.oneOf or obj.anyOf
            const_enum_data = self._extract_const_enum_from_combined(combined_items, obj.type)
            if const_enum_data is not None:
                synthetic_obj = self._create_synthetic_enum_obj(obj, *const_enum_data)
                if not self.should_parse_enum_as_literal(synthetic_obj, property_name=name, property_obj=obj):
                    self.parse_enum(name, synthetic_obj, path)
                else:
                    self.parse_root_type(name, synthetic_obj, path)
            else:
                data_type = self.parse_root_type(name, obj, path)
                if isinstance(data_type, EmptyDataType) and obj.properties:
                    self.parse_object(name, obj, path)  # pragma: no cover
        elif obj.properties:
            if obj.has_multiple_types and isinstance(obj.type, list):
                self._parse_multiple_types_with_properties(name, obj, obj.type, path)
            else:
                self.parse_object(name, obj, path)
        elif obj.patternProperties or (obj.propertyNames is not None and obj.propertyNames is not False):
            self.parse_root_type(name, obj, path)
        elif obj.type == "object":
            self.parse_object(name, obj, path)
        elif obj.propertyNames is False:
            self.parse_root_type(name, obj, path)
        elif (
            obj.enum
            and not self.ignore_enum_constraints
            and not self.should_parse_enum_as_literal(obj, property_name=name)
        ):
            self.parse_enum(name, obj, path)
        else:
            self.parse_root_type(name, obj, path)
        self.parse_ref(obj, path)

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
                return load_data(source.text)
            except (json.JSONDecodeError, *get_yaml_parse_errors()) as exc:
                source_path = self._source_path_for_diagnostics(source.path)
                raise InvalidFileFormatError(exc, self._input_file_type, source=source_path) from exc
            except TypeError as exc:
                if not self._non_dict_source_is_invalid:
                    raise
                source_path = self._source_path_for_diagnostics(source.path)
                raise InvalidFileFormatError(exc, self._input_file_type, source=source_path) from exc
        if isinstance(source.raw_data, dict):
            return dict(source.raw_data)

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
            if not self.model_resolver.validate_name(class_name):
                raise InvalidClassNameError(class_name)
            return class_name, self._should_preserve_explicit_root_class_name(class_name)

        obj_name = title_str
        if not self.model_resolver.validate_name(obj_name):
            obj_name = title_to_class_name(obj_name)
        if not self.model_resolver.validate_name(obj_name):
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
            self._generate_forced_base_models()
        finally:
            self._clear_inherited_field_caches()
            self._reset_local_source_cache()

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
            self._generate_forced_base_models()
        finally:
            self._clear_inherited_field_caches()
            self._reset_local_source_cache()

    def _resolve_unparsed_json_pointer(self) -> None:
        """Resolve any remaining unparsed JSON pointer references recursively."""
        model_count: int = len(self.results)
        for source in self.iter_source:
            path_parts = list(source.path.parts)
            if not (reserved_refs := self.reserved_refs.get(tuple(path_parts))):
                continue
            if self.current_source_path is not None:
                self.current_source_path = source.path

            with (
                self.model_resolver.current_base_path_context(source.path.parent),
                self.model_resolver.current_root_context(path_parts),
            ):
                for reserved_ref in sorted(reserved_refs):
                    if self.model_resolver.add_ref(reserved_ref, resolved=True).loaded:
                        continue
                    self.raw_obj = self._load_source_dict(source)
                    self.parse_json_pointer(self.raw_obj, reserved_ref, path_parts)

        if model_count != len(self.results):
            # New model have been generated. It try to resolve json pointer again.
            self._resolve_unparsed_json_pointer()

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

        self.parse_raw_obj(model_name, models, [*path_parts, f"#/{reference_paths[0]}", *reference_paths[1:]])

    def _get_model_by_json_pointer(
        self,
        raw: dict[str, YamlValue],
        object_paths: list[str],
        ref: str,
    ) -> YamlValue:
        """Resolve one JSON pointer, preserving the legacy empty-schema fallback when it is missing."""
        model = _get_model_by_path_or_missing(raw, object_paths)
        if model is not _MISSING_JSON_POINTER:
            return cast("YamlValue", model)

        source, _, fragment = ref.partition("#")
        if not source:
            source = self._source_path_for_diagnostics(self.current_source_path)
        self._dangling_refs.add((source, f"#{fragment}"))
        return {}

    def _report_parse_diagnostics(self) -> None:
        """Report each unique dangling local reference after schema parsing completes."""
        if not self._dangling_refs:
            return

        dangling_refs = sorted(self._dangling_refs)
        if self.strict_refs:
            details = "\n".join(f"- {source}: {ref}" for source, ref in dangling_refs)
            msg = f"Unresolved local $ref targets:\n{details}"
            raise Error(msg)

        for source, ref in dangling_refs:
            warn(
                f"Unresolved local $ref {ref!r} in {source}: JSON pointer was not found. "
                "Generated a fallback Any model; use --strict-refs to fail instead.",
                DanglingRefWarning,
                stacklevel=4,
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
            raw = self._prepare_schema_resources(raw, path_parts)
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
                    if definitions := get_model_by_path(raw, split_schema_path):
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
                    obj = self._validate_schema_object(model, definition_path)
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
                    self.parse_obj(model_name, self._validate_schema_object(models, path), path)
                elif not self.skip_root_model:
                    self.parse_obj(obj_name, root_obj, path_parts or ["#"])
                for key, model, path in definition_entries:
                    reference = self.model_resolver.references.get(self.model_resolver.join_path(tuple(path)))
                    if reference is None:
                        reference = self.model_resolver.get(path)
                    if not reference or not reference.loaded:
                        self._parse_raw_or_validated_obj(
                            key,
                            model,
                            path,
                            validated_definition_objects.get(tuple(path)),
                        )

                key = tuple(path_parts)
                reserved_refs = set(self.reserved_refs.get(key) or [])
                while reserved_refs:
                    for reserved_path in sorted(reserved_refs):
                        reference = self.model_resolver.references.get(reserved_path)
                        if not reference or reference.loaded:
                            continue
                        object_paths, reference_paths = _split_json_pointer(raw, reserved_path.split("#", 1)[-1])
                        if not object_paths:
                            self.parse_obj(
                                reference.name, self._validate_schema_object(raw, [reserved_path]), [reserved_path]
                            )
                            continue
                        models = self._get_model_by_json_pointer(raw, object_paths, reserved_path)
                        model_name = reference_paths[-1]
                        path = [*path_parts, f"#/{reference_paths[0]}", *reference_paths[1:]]
                        self.parse_obj(model_name, self._validate_schema_object(models, path), path)
                    previous_reserved_refs = reserved_refs
                    reserved_refs = set(self.reserved_refs.get(key) or [])
                    if previous_reserved_refs == reserved_refs:
                        break


# Snapshot the default hooks after JsonSchemaParser is fully defined. The local
# false-ref fact fast path is disabled for any parser that customizes a loader.
_BUILTIN_REF_RAW_SCHEMA_LOADER = JsonSchemaParser._get_ref_raw_schema  # noqa: SLF001
_BUILTIN_REF_SCHEMA_LOADER = JsonSchemaParser._load_ref_schema_object  # noqa: SLF001
_BUILTIN_SCHEMA_VALIDATOR = JsonSchemaParser._validate_schema_object  # noqa: SLF001
_BUILTIN_REF_FACT_CACHER = JsonSchemaParser._cache_ref_data_type_facts  # noqa: SLF001
