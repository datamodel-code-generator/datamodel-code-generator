"""Compatibility re-export for Avro input detection helpers."""

from __future__ import annotations

from datamodel_code_generator._avro_detection import (
    COMPLEX_TYPES,
    JSON_SCHEMA_MARKER_KEYS,
    NAMED_TYPES,
    PRIMITIVE_TYPES,
    is_avro_schema_data,
)

__all__ = [
    "COMPLEX_TYPES",
    "JSON_SCHEMA_MARKER_KEYS",
    "NAMED_TYPES",
    "PRIMITIVE_TYPES",
    "is_avro_schema_data",
]
