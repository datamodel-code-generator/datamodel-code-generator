"""The subschema positions of JSON Schema, and local definition reference rewriting.

``SCHEMA_POSITIONS`` is the one table of the keywords that hold subschemas: the shape of each value and the roles the
keyword plays. Every walker reads it, by shape or through a view of its roles (``schema_positions``); a walker that
visits another set or order than a role gives keeps it as a named view here.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Final, Literal, NamedTuple, TypeAlias

DefinitionKey: TypeAlias = Literal["$defs", "definitions"]
DEFINITION_KEYS: tuple[DefinitionKey, ...] = ("$defs", "definitions")
SchemaShape: TypeAlias = Literal["map", "sequence", "single", "single-or-sequence"]
"""How a keyword holds subschemas: a map of named ones, a list, one, or one or a list."""
SchemaRole: TypeAlias = Literal[
    "applicator",
    "in-place",
    "conditional",
    "child",
    "array",
    "object",
    "unmatched",
    "unevaluated",
    "modeled",
    "definitions",
]
"""What a subschema keyword does.

``applicator``: it applies its subschemas to the instance or to its members. ``in-place``: to the instance itself.
``conditional``: ``if``, ``then`` and ``else``. ``child``: to the members of the instance; ``array`` to items,
``object`` to properties or their names. ``unmatched``: to the members no sibling keyword matches. ``unevaluated``:
of the unevaluated vocabulary. ``modeled``: ``JsonSchemaObject`` holds it as a field of the same name, not in its
extras. ``definitions``: it holds schemas for references only.
"""
SCHEMA_VALUE_SHAPES: Final[tuple[SchemaShape, ...]] = ("sequence", "single", "single-or-sequence")
"""The shapes of keywords whose value is a subschema or a list of them."""
SINGLE_SCHEMA_SHAPES: Final[tuple[SchemaShape, ...]] = ("single", "single-or-sequence")
"""The shapes of keywords whose value may be one subschema."""
SEQUENCE_SCHEMA_SHAPES: Final[tuple[SchemaShape, ...]] = ("sequence", "single-or-sequence")
"""The shapes of keywords whose value may be a list of subschemas."""


class SchemaPosition(NamedTuple):
    """The shape of a subschema keyword's value and the roles of the keyword."""

    shape: SchemaShape
    roles: frozenset[SchemaRole]


def _position(shape: SchemaShape, *roles: SchemaRole) -> SchemaPosition:
    return SchemaPosition(shape, frozenset(roles))


SCHEMA_POSITIONS: Final[Mapping[str, SchemaPosition]] = MappingProxyType({
    "allOf": _position("sequence", "applicator", "in-place", "modeled"),
    "anyOf": _position("sequence", "applicator", "in-place", "modeled"),
    "oneOf": _position("sequence", "applicator", "in-place", "modeled"),
    "prefixItems": _position("sequence", "applicator", "child", "array", "modeled"),
    "items": _position("single-or-sequence", "applicator", "child", "array", "modeled"),
    "additionalItems": _position("single", "applicator", "child", "array", "unmatched", "modeled"),
    "additionalProperties": _position("single", "applicator", "child", "object", "unmatched", "modeled"),
    "unevaluatedItems": _position("single", "applicator", "child", "array", "unmatched", "unevaluated", "modeled"),
    "unevaluatedProperties": _position(
        "single", "applicator", "child", "object", "unmatched", "unevaluated", "modeled"
    ),
    "contains": _position("single", "applicator", "child", "array"),
    "propertyNames": _position("single", "applicator", "child", "object", "modeled"),
    "not": _position("single", "applicator", "in-place"),
    "if": _position("single", "applicator", "in-place", "conditional"),
    "then": _position("single", "applicator", "in-place", "conditional"),
    "else": _position("single", "applicator", "in-place", "conditional"),
    "properties": _position("map", "applicator", "child", "object", "modeled"),
    "patternProperties": _position("map", "applicator", "child", "object", "modeled"),
    "dependentSchemas": _position("map", "applicator", "in-place"),
    "dependencies": _position("map", "applicator", "in-place"),
    "$defs": _position("map", "definitions"),
    "definitions": _position("map", "definitions"),
    "contentSchema": _position("single"),
    "extends": _position("single-or-sequence", "applicator", "in-place"),
})
"""Every keyword that holds subschemas, in the order walkers visit them unless a view says otherwise."""
SCHEMA_SHAPES: Final[Mapping[str, SchemaShape]] = MappingProxyType({
    keyword: position.shape for keyword, position in SCHEMA_POSITIONS.items()
})
"""The shape of each subschema keyword, for walkers that read raw schemas in their own key order."""


def schema_positions(
    *roles: SchemaRole, shapes: Collection[SchemaShape] = (), without: Collection[str] = ()
) -> tuple[str, ...]:
    """Return the keywords that play every role of ``roles`` with one of ``shapes`` (any), in table order.

    ``without`` names the keywords a walker leaves out of the role, each a legacy subset named where it is read.
    """
    return tuple(
        keyword
        for keyword, position in SCHEMA_POSITIONS.items()
        if position.roles.issuperset(roles) and (not shapes or position.shape in shapes) and keyword not in without
    )


SCHEMA_MAP_KEYS: frozenset[str] = frozenset(schema_positions(shapes=("map",)))
SCHEMA_VALUE_KEYS: frozenset[str] = frozenset(schema_positions(shapes=SCHEMA_VALUE_SHAPES))
_UNTYPED_MODEL_POSITIONS: Final = ("additionalItems", "unevaluatedItems", "unevaluatedProperties", "propertyNames")
MODEL_VALUE_POSITIONS: Final = schema_positions("modeled", without=_UNTYPED_MODEL_POSITIONS)
"""The positions whose schemas make up the values of a schema: its branches, items and properties.

The legacy subset the target binding and the input-model converter walk: no tuple tail, no unevaluated keyword and no
property names.
"""
REF_PRE_PASS_FIELDS: Final = frozenset(schema_positions("modeled", without=("additionalItems",)))
"""The fields the reference pre-pass descends into: the modeled positions without ``additionalItems`` (legacy)."""
REF_PRE_PASS_POSITIONS: Final = tuple(
    sorted(
        REF_PRE_PASS_FIELDS.union(schema_positions("conditional")),
        key=(
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
            "if",
            "then",
            "else",
            "properties",
        ).index,
    )
)
"""The positions of the reference pre-pass, the conditional ones under --generate-schema-validators only.

In the legacy order it meets references in, which the names of the references it registers follow.
"""


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
