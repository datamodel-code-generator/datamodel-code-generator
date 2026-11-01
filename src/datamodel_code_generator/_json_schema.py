"""The subschema positions of JSON Schema, and local definition reference rewriting.

``SCHEMA_POSITIONS`` is the one table of the keywords that hold subschemas: the shape of each value and the roles the
keyword plays. Every walker reads it, by shape or through a view of its roles (``schema_positions``); a walker that
visits another set or order than a role gives keeps it as a named view here. ``DEFINITION_KEYS`` and
``INSTANCE_DATA_KEYWORDS`` name the keywords that hold schemas only for references and those whose values are instance
data. ``subschema_entries`` reads the subschemas one keyword holds by its shape, and ``json_nodes`` walks every value of
a loaded JSON value. ``admitted_types`` reads what the types a ``type`` keyword lists admit.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Final, Literal, NamedTuple, TypeAlias

from datamodel_code_generator._json_pointer import unescape_token

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


def subschema_entries(keyword: str, value: object) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
    """Yield each subschema that ``keyword`` holds in its value, by the keyword's shape, with its path in the schema.

    The path is ``(keyword, name)`` in a map (the name as text), ``(keyword, index)`` in a list and ``(keyword,)`` for
    one subschema, in document order; a member that is no JSON object, and a value of another shape, hold none.
    """
    match SCHEMA_SHAPES.get(keyword), value:
        case "map", dict():
            for name, child in value.items():
                if isinstance(child, dict):
                    yield (keyword, str(name)), child
        case "sequence" | "single-or-sequence", list():
            for index, child in enumerate(value):
                if isinstance(child, dict):
                    yield (keyword, index), child
        case "single" | "single-or-sequence", dict():
            yield (keyword,), value


def json_nodes(value: object, *, tuples: bool = False) -> Iterator[object]:
    """Yield a loaded JSON value and every value inside it, on a stack: the last member of an object or array first.

    An object or array met again, as a YAML alias gives it and also one that holds itself, is yielded and walked once.
    The walk takes the members of an object or array when it resumes after yielding it, so a caller may change the
    object first, or stop. ``tuples`` also walks the items of a tuple (a schema built in Python).
    """
    containers = (dict, list, tuple) if tuples else (dict, list)
    seen: dict[int, object] = {}
    pending = [value]
    while pending:
        node = pending.pop()
        if not isinstance(node, containers):
            yield node
        elif id(node) not in seen:
            seen[id(node)] = node
            yield node
            pending.extend(node.values() if isinstance(node, dict) else node)


SCHEMA_MAP_KEYS: frozenset[str] = frozenset(schema_positions(shapes=("map",)))
SCHEMA_VALUE_KEYS: frozenset[str] = frozenset(schema_positions(shapes=SCHEMA_VALUE_SHAPES))
COMBINATOR_POSITIONS: Final = schema_positions("in-place", shapes=("sequence",))
"""``allOf``, ``anyOf`` and ``oneOf``: the lists of subschemas that apply to the instance itself, in table order."""
NAMED_SCHEMA_MAPS: Final = schema_positions(shapes=("map",), without=("dependencies",))
"""The maps whose every value is a subschema under a name; a value of ``dependencies`` may be a list of names."""
MODELED_SCHEMA_MAPS: Final = schema_positions("modeled", shapes=("map",))
"""The maps of named subschemas ``JsonSchemaObject`` holds as fields: ``properties`` and ``patternProperties``."""
ITEM_POSITIONS: Final = schema_positions("array", without=("contains",))
"""The positions whose subschemas type the items of an array; ``contains`` only requires a matching item."""
NAMESPACE_DEFINITION_KEYS: Final = DEFINITION_KEYS[::-1]
"""The definition keywords in the order namespaces of definitions are read: ``definitions`` first (#3445)."""
INSTANCE_DATA_KEYWORDS: Final = frozenset({"const", "default", "enum", "example", "examples"})
"""The keywords whose values are instance data, never subschemas, whatever JSON objects they hold."""
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


def admitted_types(types: Iterable[str]) -> frozenset[str]:
    """Return the JSON types a ``type`` keyword admits: the ones it lists, and ``integer`` where it lists ``number``."""
    listed = frozenset(types)
    return listed | {"integer"} if "number" in listed else listed


def definition_ref(key: DefinitionKey, name: str = "") -> str:
    """Return the local reference to the definition ``name`` under ``key``, the name as the reference writes it.

    Without a name, it is the text every local reference to a definition under ``key`` starts with.
    """
    return f"#/{key}/{name}"


def _local_definition_ref_name(ref: str) -> tuple[str, str, str] | None:
    if not ref.startswith("#"):
        return None
    if "%" in ref:
        from urllib.parse import unquote  # noqa: PLC0415

        ref = unquote(ref)
    for key in DEFINITION_KEYS:
        prefix = definition_ref(key)
        if not ref.startswith(prefix):
            continue
        escaped_name, separator, suffix = ref[len(prefix) :].partition("/")
        return unescape_token(escaped_name), escaped_name, f"{separator}{suffix}"
    return None


def _rewrite_local_definition_ref(ref: str, ref_map: Mapping[str, str], references: set[str]) -> str:
    if local_ref := _local_definition_ref_name(ref):
        name, escaped_name, suffix = local_ref
        definition_name = ref_map.get(name, escaped_name)
        references.add(definition_name)
        return f"{definition_ref('$defs', definition_name)}{suffix}"
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
