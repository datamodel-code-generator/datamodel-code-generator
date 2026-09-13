"""Shared JSON Schema positions and local definition reference rewriting."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal, TypeAlias

DefinitionKey: TypeAlias = Literal["$defs", "definitions"]
DEFINITION_KEYS: tuple[DefinitionKey, ...] = ("$defs", "definitions")
SCHEMA_MAP_KEYS: frozenset[str] = frozenset({
    "$defs",
    "definitions",
    "dependencies",
    "dependentSchemas",
    "patternProperties",
    "properties",
})
SCHEMA_VALUE_KEYS: frozenset[str] = frozenset({
    "additionalItems",
    "additionalProperties",
    "allOf",
    "anyOf",
    "contains",
    "contentSchema",
    "else",
    "extends",
    "if",
    "items",
    "not",
    "oneOf",
    "prefixItems",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
})


def _local_definition_ref_name(ref: str) -> tuple[str, str, str] | None:
    if not ref.startswith("#"):
        return None
    if "%" in ref:
        from urllib.parse import unquote  # noqa: PLC0415

        ref = unquote(ref)
    for key in DEFINITION_KEYS:
        prefix = f"#/{key}/"
        if not ref.startswith(prefix):
            continue
        escaped_name, separator, suffix = ref[len(prefix) :].partition("/")
        return escaped_name.replace("~1", "/").replace("~0", "~"), escaped_name, f"{separator}{suffix}"
    return None


def _rewrite_local_definition_ref(ref: str, ref_map: Mapping[str, str], references: set[str]) -> str:
    if local_ref := _local_definition_ref_name(ref):
        name, escaped_name, suffix = local_ref
        definition_name = ref_map.get(name, escaped_name)
        references.add(definition_name)
        return f"#/$defs/{definition_name}{suffix}"
    return ref


def _rewrite_schema_refs(
    value: Any,
    ref_map: Mapping[str, str],
    references: set[str],
    *,
    strip_root_definitions: bool = False,
) -> Any:
    """Copy a schema, rewriting references only at schema-bearing keywords."""
    if isinstance(value, Mapping):
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            if strip_root_definitions and key in DEFINITION_KEYS:
                continue
            if key in {"$ref", "$dynamicRef"} and isinstance(item, str):
                rewritten[key] = _rewrite_local_definition_ref(item, ref_map, references)
                continue
            if key in SCHEMA_MAP_KEYS and isinstance(item, Mapping):
                rewritten[key] = {
                    name: _rewrite_schema_refs(child, ref_map, references) for name, child in item.items()
                }
            elif key in SCHEMA_VALUE_KEYS:
                rewritten[key] = _rewrite_schema_refs(item, ref_map, references)
            else:
                rewritten[key] = deepcopy(item)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_schema_refs(item, ref_map, references) for item in value]
    return value
