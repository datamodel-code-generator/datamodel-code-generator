"""Lightweight Avro schema detection shared by the API and Avro parser."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

JsonSchema = dict[str, Any]
YamlValue: TypeAlias = "dict[str, YamlValue] | list[YamlValue] | str | int | float | bool | None"

PRIMITIVE_TYPES = frozenset({"null", "boolean", "int", "long", "float", "double", "bytes", "string"})
NAMED_TYPES = frozenset({"record", "enum", "fixed"})
COMPLEX_TYPES = NAMED_TYPES | {"array", "map"}
JSON_SCHEMA_MARKER_KEYS = frozenset({"$schema", "$defs", "definitions", "properties", "allOf", "anyOf", "oneOf"})


def _is_avro_union_item(item: object) -> bool:
    match item:
        case str() as type_name:
            return type_name in PRIMITIVE_TYPES or "." in type_name
        case [*_] as union:
            return _is_avro_union(union)
        case {"type": [*_] as union}:
            return _is_avro_union(union)
        case {"type": dict() as nested_schema}:
            return _is_avro_union_item(nested_schema)
        case {"type": str() as type_name}:
            return _is_avro_typed_schema(cast("Mapping[str, object]", item), type_name) or "." in type_name
    return False


def _is_avro_union(union: Sequence[object]) -> bool:
    return bool(union) and all(_is_avro_union_item(child) for child in union)


def _is_avro_typed_schema(schema: Mapping[str, object], type_name: str) -> bool:
    if type_name in PRIMITIVE_TYPES:
        return True

    match type_name:
        case "record":
            required_key, required_type = "fields", list
        case "enum":
            required_key, required_type = "symbols", list
        case "fixed":
            required_key, required_type = "size", int
        case "array":
            return "items" in schema
        case "map":
            return "values" in schema
        case _:
            return False
    return isinstance(schema.get("name"), str) and isinstance(schema.get(required_key), required_type)


def is_avro_schema_data(data: YamlValue) -> bool:
    """Return whether loaded data appears to be an Avro schema."""
    match data:
        case [*_] as union:
            return _is_avro_union(union)
        case str() as type_name:
            return type_name in PRIMITIVE_TYPES
        case dict() as schema if not any(key in schema for key in JSON_SCHEMA_MARKER_KEYS):
            return _is_avro_schema_object(schema)
        case _:
            return False
    return False


def _is_avro_schema_object(schema: JsonSchema) -> bool:
    match schema.get("type"):
        case [*_]:
            return False
        case dict() as nested_schema:
            return is_avro_schema_data(nested_schema)
        case str() as type_name if type_name in PRIMITIVE_TYPES:
            return any(key in schema for key in ("logicalType", "namespace", "aliases"))
        case str() as type_name:
            return _is_avro_typed_schema(schema, type_name) or (type_name not in COMPLEX_TYPES and "." in type_name)
    return False


is_avro_schema_data.__module__ = "datamodel_code_generator.parser._avro_detection"

__all__ = [
    "COMPLEX_TYPES",
    "JSON_SCHEMA_MARKER_KEYS",
    "NAMED_TYPES",
    "PRIMITIVE_TYPES",
    "is_avro_schema_data",
]
