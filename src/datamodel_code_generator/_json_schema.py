"""The subschema positions of JSON Schema, local definition reference rewriting, and the kind and equality of values.

``SCHEMA_POSITIONS`` is the one table of the keywords that hold subschemas: the shape of each value and the roles the
keyword plays. Every walker reads it, by shape or through a view of its roles (``schema_positions``); a walker that
visits another set or order than a role gives keeps it as a named view here. ``DEFINITION_KEYS`` and
``INSTANCE_DATA_KEYWORDS`` name the keywords that hold schemas only for references and those whose values are instance
data. ``subschema_entries`` reads the subschemas one keyword holds by its shape, and ``json_nodes`` walks every value of
a loaded JSON value. ``declared_types`` reads the types a ``type`` keyword lists, ``admitted_types`` what they admit,
and ``non_null_types`` which of them are not ``null``.

The kind of a JSON value is one ladder, ``literal_value_kind`` (a bool is no int), read by the model layer, the parser
and the targets alike (``model/_decisions/enum_kind.py`` keeps it under its name). ``json_value_type`` names the JSON
type of a JSON value, None for a value outside JSON, which only a schema built in Python holds; ``json_type`` names one
for every value, with the row of the ladders it replaced: outside JSON a Decimal is a number and any other value an
array.

The JSON equality of two values (a bool is no number, ``1 == 1.0``, and NaN, which a YAML document can write, equals
NaN; arrays and objects item by item) is the equality of their keys: ``json_value_key`` is the one rule, which tells a
bool from a number by the kind. The ``enum``, ``const`` and ``uniqueItems`` intersections compare by it
(``json_values_equal``, which ``parser/_intersection.py`` keeps under its name) and find values by it
(``parser/_intersection.py:_retained_indices``); enum member lookups index members by it (``model/enum.py``). Two rows
kept from the forms it replaced are its parameters, which the lookup turns on: ``exact_types``
(``legacy-exact-type-key``), a value outside the numbers under its exact type, so a ``str`` subclass is another value
than the equal ``str``, where the intersections take any ``str`` as its text; and ``python_items``
(``legacy-python-equality-fallback``), the items of an array or object as Python compares them, so ``[True]`` is
``[1]`` and a NaN inside is equal only to the same object, where the intersections compare the items as JSON values.

Not routed here: ``_constraint_applicability.py`` (``json_integer``, ``json_number``) tells a bool from a number on its
own, as the constraint leaf does not import this module.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from copy import deepcopy
from decimal import Decimal
from math import isnan
from types import MappingProxyType
from typing import Any, Final, Literal, NamedTuple, TypeAlias, overload
from urllib.parse import unquote

from typing_extensions import Sentinel

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


@overload
def subschema_entries(  # noqa: D418 - CodeQL flags an ellipsis body.
    keyword: str, value: object, *, booleans: Literal[False] = False, whole: Literal[False] = False
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
    """Yield the object subschemas ``keyword`` holds."""


@overload
def subschema_entries(  # noqa: D418
    keyword: str, value: object, *, booleans: Literal[True], whole: Literal[False] = False
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any] | bool]]:
    """Yield the object and boolean subschemas ``keyword`` holds."""


@overload
def subschema_entries(  # noqa: D418
    keyword: str, value: object, *, booleans: Literal[False] = False, whole: Literal[True]
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any] | list[Any]]]:
    """Yield the object subschemas of a map ``keyword`` holds, or its object or array value whole."""


def subschema_entries(
    keyword: str, value: object, *, booleans: bool = False, whole: bool = False
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any] | bool | list[Any]]]:
    """Yield each subschema that ``keyword`` holds in its value, by the keyword's shape, with its path in the schema.

    The path is ``(keyword, name)`` in a map (the name as text), ``(keyword, index)`` in a list and ``(keyword,)`` for
    one subschema, in document order; a member that is no JSON object, and a value of another shape, hold none.
    ``booleans`` also yields the boolean schemas. ``whole`` (row ``legacy-cycle-walk-reads-values-whole``, the check
    for a `$ref` cycle) yields the value of a keyword of any other shape than a map whole, at ``(keyword,)``, when it
    is an object or an array, whatever shape the keyword gives it: an array under a keyword of one schema too.
    """
    kinds = (dict, bool) if booleans else dict
    match SCHEMA_SHAPES.get(keyword), value:
        case "map", dict():
            for name, child in value.items():
                if isinstance(child, kinds):
                    yield (keyword, str(name)), child
        case "sequence" | "single" | "single-or-sequence", dict() | list() if whole:
            yield (keyword,), value
        case "sequence" | "single-or-sequence", list():
            for index, child in enumerate(value):
                if isinstance(child, kinds):
                    yield (keyword, index), child
        case "single" | "single-or-sequence", dict() | bool() if isinstance(value, kinds):
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


LiteralValueKind: TypeAlias = Literal["none", "bool", "int", "float", "str", "other"]
"""The kind of a JSON value, named like the Python type of the value."""


def literal_value_kind(value: object, /) -> LiteralValueKind:
    """Return the kind of a JSON value; a bool is no int."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "other"


_JSON_TYPES: Final[Mapping[LiteralValueKind, str]] = MappingProxyType({
    "none": "null",
    "bool": "boolean",
    "int": "integer",
    "float": "number",
    "str": "string",
})
"""The JSON type of a value of each scalar kind; a float is a number, also one without a fraction."""


def json_value_type(value: object, /) -> str | None:
    """Return the JSON type of a JSON value: that of its kind, an object for a dict and an array for a list.

    None for a value outside JSON, which only a schema built in Python holds.
    """
    if (found := _JSON_TYPES.get(literal_value_kind(value))) is not None:
        return found
    return "object" if isinstance(value, dict) else "array" if isinstance(value, list) else None


def json_type(value: object) -> str:
    """Return the JSON type of a JSON value (``json_value_type``), with a type for every other value too.

    The row of the ladders it replaced for a value outside JSON: a Decimal is a number and any other value an array.
    """
    return json_value_type(value) or ("number" if isinstance(value, Decimal) else "array")


_JSON_NUMBER_KEY = Sentinel("_JSON_NUMBER_KEY")
_JSON_NAN_KEY = Sentinel("_JSON_NAN_KEY")
_KEY_KINDS: Final[Mapping[type, object]] = MappingProxyType({
    type(None): type(None),
    bool: bool,
    int: _JSON_NUMBER_KEY,
    float: _JSON_NUMBER_KEY,
    str: str,
})
"""What a scalar of each JSON type is keyed under: a bool is no number, an int and a float are numbers alike."""
_PLAIN_VALUES: Final[Mapping[LiteralValueKind, Callable[[Any], object]]] = MappingProxyType({
    "int": int.__int__,
    "float": float.__float__,
    "str": str.__str__,
})
"""The value of the scalar type itself that an instance of one of its subclasses holds, by the kind of the instance."""


def _python_item_key(item: object) -> object:
    """Return an item as Python compares it (``python_items``): itself, an array or object by its items in turn."""
    return json_value_key(item, python_items=True) if isinstance(item, (list, dict)) else item


def json_value_key(value: Any, /, *, exact_types: bool = False, python_items: bool = False) -> object:
    """Return the key of a value under JSON equality: two values are equal exactly when their keys are.

    A null, a bool, a number (every NaN one value) and a string are keyed under their kind, an array by the keys of its
    items and an object by those of its values under their names, in any order. An instance of a subclass of ``int``,
    ``float`` or ``str`` (an ``IntEnum`` or ``StrEnum`` member of a schema built in Python) is keyed as the value of
    that type it holds. A value outside JSON, which a schema built in Python or a YAML tag such as ``!!binary`` gives,
    has a key equal to no other.

    The rows of enum member lookups:

    * ``exact_types`` (``legacy-exact-type-key``): a value outside the numbers is keyed under its exact type, so a
      ``str`` subclass is another value than the equal ``str``; it and a value outside JSON are keyed by their own hash
      and equality where their type has a hash (a set, which a unique-items default is written as, has none).
    * ``python_items`` (``legacy-python-equality-fallback``): the items of an array or object are their own keys, as
      Python compares them: ``True``, ``1`` and ``1.0`` are one item and a NaN is equal only to the same object.
    """
    cls = type(value)
    if cls in _KEY_KINDS:
        return _KEY_KINDS[cls], _JSON_NAN_KEY if cls is float and isnan(value) else value
    item_key = _python_item_key if python_items else json_value_key
    if isinstance(value, list):
        return (cls if exact_types else list), tuple(map(item_key, value))
    if isinstance(value, dict):
        return (cls if exact_types else dict), frozenset(zip(value, map(item_key, value.values()), strict=True))
    kind = literal_value_kind(value)
    if exact_types and kind not in {"int", "float"} and cls.__hash__ is not None:
        return cls, value
    return json_value_key(_PLAIN_VALUES[kind](value)) if kind in _PLAIN_VALUES else object()


def json_values_equal(left: object, right: object) -> bool:
    """Compare JSON values as ``enum``, ``const`` and ``uniqueItems`` do: by their keys (``json_value_key``)."""
    return json_value_key(left) == json_value_key(right)


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


def declared_types(value: object) -> tuple[str, ...]:
    """Return the JSON types a ``type`` keyword lists, in order: the one a string names, or a list's string items."""
    match value:
        case str():
            return (value,)
        case list():
            return tuple(item for item in value if isinstance(item, str))
    return ()


def admitted_types(types: Iterable[str]) -> frozenset[str]:
    """Return the JSON types a ``type`` keyword admits: the ones it lists, and ``integer`` where it lists ``number``."""
    listed = frozenset(types)
    return listed | {"integer"} if "number" in listed else listed


def non_null_types(types: Iterable[str], /, *, distinct: bool = False) -> list[str]:
    """Return the types a ``type`` list holds other than ``null``, in their order; ``distinct`` keeps one of each."""
    listed = [type_ for type_ in types if type_ != "null"]
    return list(dict.fromkeys(listed)) if distinct else listed


def definition_ref(key: DefinitionKey, name: str = "") -> str:
    """Return the local reference to the definition ``name`` under ``key``, the name as the reference writes it.

    Without a name, it is the text every local reference to a definition under ``key`` starts with.
    """
    return f"#/{key}/{name}"


def _local_definition_ref_name(ref: str) -> tuple[str, str, str] | None:
    if not ref.startswith("#"):
        return None
    if "%" in ref:
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


__all__ = [
    "COMBINATOR_POSITIONS",
    "DEFINITION_KEYS",
    "INSTANCE_DATA_KEYWORDS",
    "ITEM_POSITIONS",
    "MODELED_SCHEMA_MAPS",
    "MODEL_VALUE_POSITIONS",
    "NAMED_SCHEMA_MAPS",
    "NAMESPACE_DEFINITION_KEYS",
    "REF_PRE_PASS_FIELDS",
    "REF_PRE_PASS_POSITIONS",
    "SCHEMA_MAP_KEYS",
    "SCHEMA_POSITIONS",
    "SCHEMA_SHAPES",
    "SCHEMA_VALUE_KEYS",
    "SCHEMA_VALUE_SHAPES",
    "SEQUENCE_SCHEMA_SHAPES",
    "SINGLE_SCHEMA_SHAPES",
    "DefinitionKey",
    "LiteralValueKind",
    "SchemaPosition",
    "SchemaRole",
    "SchemaShape",
    "admitted_types",
    "declared_types",
    "definition_ref",
    "json_nodes",
    "json_type",
    "json_value_key",
    "json_value_type",
    "json_values_equal",
    "literal_value_kind",
    "non_null_types",
    "schema_positions",
    "subschema_entries",
]
