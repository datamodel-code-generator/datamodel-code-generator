"""Intersect the keywords of the parts of one schema object, keyword by keyword.

A schema object with a ``$ref`` and sibling keywords means the conjunction of the referenced schema (its first part)
and its own keywords (its last part); a property that overrides an inherited property means the conjunction of the
inherited property and its own. Each such site has a legacy merge, its presentation row, which writes one schema for
the parts: ``JsonSchemaParser._deep_merge`` for ``$ref`` siblings (lists concatenated, the own value of any other
keyword winning, the referenced value filling in) and ``JsonSchemaParser._merge_property_schemas`` for inherited
overrides (the child's value winning). :func:`intersect_keywords` keeps the row's schema wherever it enforces what the
conjunction enforces, so working output keeps its bytes, and writes the intersection of a keyword where the row
enforces something else.

Pure: the parts and the row are dumped schema dicts, nothing is written to them, and nested schemas that change are
copied with ``copy.copy``, so a located dump keeps its location. The adapters are ``parser/jsonschema.py``:
``JsonSchemaParser._intersect_ref_siblings`` (for ``_merge_ref_with_schema`` and
``_resolve_inherited_parent_property``) and ``JsonSchemaParser._merge_properties_with_parent_constraints``.

``pattern`` is the conjunction of the parts' patterns (``_regex_syntax.py:conjoin_patterns``) unless the row's pattern
implies each of them; patterns not every engine runs, and patterns no single expression conjoins, keep the row's pattern
(the output before the conjunction), the others listed in ``untaken_patterns`` for a warning.

The row's value stays for keywords not intersected here yet: literal filtering (the parser's literal check filters the
values it writes), ``format`` (the static ``(type, format)`` table), tuple positions (``prefixItems``, array
``items``), ``anyOf``, ``oneOf``, ``not`` and ``if`` (combinator parts), ``contains`` groups (each part keeps its own
group; the parser turns a referenced group into item bounds), ``dependentRequired`` and ``dependentSchemas``, and a
``$ref`` that the row replaces by another.

Legacy row *sibling enum before 2019-09*: where the document's dialect (JSON Schema draft 7 or earlier, OpenAPI 3.0)
does not read ``$ref`` siblings as parts, a sibling ``enum`` that the document writes does not narrow the referenced
schema (the caller passes ``ref_siblings_are_parts=False``). The site's own ``enum`` (that of the last part, at every
depth) is then no part: the row's ``enum`` stays (the referenced values and the sibling values concatenated), and no
``enum`` or ``const`` warning counts it. The other sibling keywords are parts in every dialect.

Known gap, left to a later change: an ``enum`` value that the ``type`` of another part rejects (``{"$ref": I, "enum":
[1, 2.5, "x"]}`` with an ``integer`` target) is kept, and an ``enum`` none of whose values the other part's ``type``
admits gives no warning.
"""

from __future__ import annotations

from copy import copy
from fractions import Fraction
from itertools import chain, starmap
from math import gcd, isfinite, isnan, lcm
from typing import TYPE_CHECKING, Any, Final, final
from warnings import warn

from typing_extensions import Sentinel

from datamodel_code_generator._records import record
from datamodel_code_generator._regex_syntax import conjoin_patterns, pattern_implies

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "ENUM_METADATA_KEYWORDS",
    "KeywordIntersection",
    "bounds_admit_no_value",
    "enum_metadata",
    "intersect_constraint",
    "intersect_enum_values",
    "intersect_keywords",
    "intersect_multiple_of",
    "is_hash_safe_json_scalar",
    "json_values_equal",
    "warn_unsatisfiable",
    "warn_untaken_patterns",
]

_LOWER_BOUNDS: Final = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
_UPPER_BOUNDS: Final = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")
_BOUND_DIMENSIONS: Final = (
    (("minimum", "exclusiveMinimum"), ("maximum", "exclusiveMaximum")),
    (("minLength",), ("maxLength",)),
    (("minItems",), ("maxItems",)),
    (("minProperties",), ("maxProperties",)),
)
_SCHEMA_KEYWORDS: Final = (
    "items",
    "additionalItems",
    "additionalProperties",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
)
_SCHEMA_MAP_KEYWORDS: Final = ("properties", "patternProperties")
ENUM_METADATA_KEYWORDS: Final = ("x-enum-varnames", "x-enumNames", "x-enum-descriptions")
_HASHABLE_SCALAR_TYPES: Final = frozenset({str, int, float, bool, type(None)})
_NUMBER_TYPES: Final = frozenset({"integer", "number"})
_REMOVED: Final = Sentinel("_REMOVED")


@final
@record
class KeywordIntersection:
    """The schema a site is parsed as, and the keywords whose parts have no value in common.

    ``keywords`` is a new dict the caller owns. A keyword listed in ``unsatisfiable`` keeps the row's value.
    ``untaken_patterns`` are the patterns of the parts that the written ``pattern`` leaves out.
    """

    keywords: dict[str, Any]
    unsatisfiable: tuple[str, ...]
    untaken_patterns: tuple[str, ...] = ()


def json_values_equal(left: object, right: object) -> bool:  # ruff: ignore[too-many-return-statements]
    """Compare JSON values as ``enum``, ``const`` and ``uniqueItems`` do: booleans are not numbers, ``1 == 1.0``.

    NaN, which a YAML document can write, equals NaN.
    """
    match left:
        case None:
            return right is None
        case bool() as boolean:
            return isinstance(right, bool) and boolean == right
        case int() | float() as number:
            return (
                not isinstance(right, bool)
                and isinstance(right, (int, float))
                and (
                    number == right
                    or (isinstance(number, float) and isinstance(right, float) and isnan(number) and isnan(right))
                )
            )
        case str() as string:
            return isinstance(right, str) and string == right
        case list() as array:
            return (
                isinstance(right, list)
                and len(array) == len(right)
                and all(starmap(json_values_equal, zip(array, right, strict=True)))
            )
        case dict() as object_:
            return (
                isinstance(right, dict)
                and len(object_) == len(right)
                and all(key in right and json_values_equal(item, right[key]) for key, item in object_.items())
            )
    return False


def is_hash_safe_json_scalar(value: object) -> bool:
    """Return whether a JSON scalar hashes and compares as its base type, so a set can hold it; NaN cannot."""
    value_type = type(value)
    if isinstance(value, float) and isnan(value):
        return False
    return value_type in _HASHABLE_SCALAR_TYPES or any(
        isinstance(value, scalar_type)
        and value_type.__eq__ is scalar_type.__eq__
        and value_type.__hash__ is scalar_type.__hash__
        for scalar_type in (str, int, float)
    )


def _retained_indices(first: Sequence[Any], second: Sequence[Any]) -> list[int]:
    """Return the positions of the values of ``first`` that ``second`` also lists."""
    if all(map(is_hash_safe_json_scalar, chain(first, second))):
        keys = {(isinstance(value, bool), value) for value in second}
        return [index for index, value in enumerate(first) if (isinstance(value, bool), value) in keys]
    return [
        index for index, value in enumerate(first) if any(json_values_equal(value, candidate) for candidate in second)
    ]


def intersect_enum_values(first: Sequence[Any], second: Sequence[Any]) -> list[Any]:
    """Return the values of ``first`` that ``second`` also lists, in the order of ``first``."""
    return [first[index] for index in _retained_indices(first, second)]


def intersect_multiple_of(first: Any, second: Any) -> Any:
    """Return the least common multiple of two ``multipleOf`` values, which every multiple of both is a multiple of."""
    multiple_1 = Fraction(str(first))
    multiple_2 = Fraction(str(second))
    merged = Fraction(
        lcm(multiple_1.numerator, multiple_2.numerator), gcd(multiple_1.denominator, multiple_2.denominator)
    )
    return merged.numerator if merged.denominator == 1 else float(merged)


def intersect_constraint(keyword: str, first: Any, second: Any) -> Any:
    """Return the value of a bound, ``uniqueItems`` or ``multipleOf`` keyword that both values imply.

    A bound returns the tighter value, and ``first`` when both are equal.
    """
    if keyword in _LOWER_BOUNDS:
        return max(first, second)
    if keyword in _UPPER_BOUNDS:
        return min(first, second)
    if keyword == "uniqueItems":
        return first or second
    return intersect_multiple_of(first, second)


def warn_unsatisfiable(
    where: str, keywords: Iterable[str], parts: str = "the referenced or inherited schema and its own keywords"
) -> None:
    """Warn that no value satisfies every part of a schema, whose generated code keeps its earlier form."""
    names = ", ".join(f"`{keyword}`" for keyword in keywords)
    warn(
        f"{where}: no value satisfies every part of this schema ({parts}), since no value meets all of their "
        f"{names}; the generated code keeps its earlier {names}.",
        stacklevel=3,
    )


def warn_untaken_patterns(where: str, pattern: str, untaken: Sequence[str]) -> None:
    """Warn that the generated code checks a schema's ``pattern`` but not the other patterns of its parts."""
    names = ", ".join(f"`{other}`" for other in untaken)
    warn(
        f"{where}: no single regular expression checks `{pattern}` together with {names} (lookarounds, backreferences, "
        "repeated group names, inline flags or syntax Python reads differently); "
        f"the generated code checks only `{pattern}`.",
        stacklevel=3,
    )


def _intersect_values(keyword: str, parts: Sequence[Mapping[str, Any]]) -> Any:
    """Return the value of a bound, ``multipleOf`` or ``uniqueItems`` keyword that two or more parts set, else None.

    A ``multipleOf`` that is not finite is left out, since no common multiple exists for it; an integer of any size is
    finite.
    """
    values = [
        value
        for part in parts
        if (value := part.get(keyword)) is not None
        and (keyword != "multipleOf" or isinstance(value, int) or isfinite(value))
    ]
    if len(values) <= 1:
        return None
    merged = values[0]
    for value in values[1:]:
        merged = intersect_constraint(keyword, merged, value)
    return merged


def _crosses(schema: Mapping[str, Any], lower: Sequence[str], upper: Sequence[str]) -> bool:
    """Return whether the lower and upper bounds of one dimension of a schema admit no value."""
    lows = [(value, keyword.startswith("exclusive")) for keyword in lower if (value := schema.get(keyword)) is not None]
    highs = [
        (value, keyword.startswith("exclusive")) for keyword in upper if (value := schema.get(keyword)) is not None
    ]
    if not (lows and highs):
        return False
    low = max(value for value, _ in lows)
    high = min(value for value, _ in highs)
    return low > high or (
        low == high and any(exclusive for value, exclusive in (*lows, *highs) if value in {low, high})
    )


def bounds_admit_no_value(schema: Mapping[str, Any]) -> bool:
    """Return whether the bounds of any dimension of a schema admit no value."""
    return any(_crosses(schema, lower, upper) for lower, upper in _BOUND_DIMENSIONS)


def _types(value: object) -> tuple[str, ...]:
    """Return the JSON types a ``type`` keyword lists."""
    return (value,) if isinstance(value, str) else tuple(value) if isinstance(value, list) else ()


def _explicit_null(part: Mapping[str, Any]) -> bool:
    """Return whether a part's own keywords admit null: ``nullable: true`` or ``null`` in its ``type`` list."""
    return part.get("nullable") is True or "null" in _types(part.get("type"))


def _intersect_types(first: Sequence[str], second: Sequence[str]) -> tuple[str, ...]:
    """Return the types both lists admit, in the order of ``first``; ``integer`` is the part of ``number`` both admit.

    ``null`` stays only when both lists hold it; whether a site admits null is the null rule's decision.
    """
    return tuple(
        dict.fromkeys(
            schema_type if schema_type in second else "integer"
            for schema_type in first
            if schema_type in second or (schema_type in _NUMBER_TYPES and _NUMBER_TYPES.intersection(second))
        )
    )


def enum_metadata(enum_parts: Sequence[Mapping[str, Any]], key: str, index: int) -> object:
    """Return the annotation of a retained enum value: the first part's, else that of the next part that has one."""
    value = enum_parts[0]["enum"][index]
    positions = (
        index
        if part is enum_parts[0]
        else next(i for i, item in enumerate(part["enum"]) if json_values_equal(value, item))
        for part in enum_parts
    )
    return next(
        (
            metadata[position]
            for part, position in zip(enum_parts, positions, strict=True)
            if position < len(metadata := part.get(key) or [])
        ),
        None,
    )


@final
class _Reconciler:
    """Rewrite the keywords of a row whose value enforces something other than the intersection of the parts."""

    __slots__ = ("extras_key", "own_enum_is_part", "unsatisfiable", "untaken_patterns")

    def __init__(self, extras_key: str, *, own_enum_is_part: bool) -> None:
        self.extras_key = extras_key
        self.own_enum_is_part = own_enum_is_part
        self.unsatisfiable: list[str] = []
        self.untaken_patterns: list[str] = []

    def _enum_parts(self, parts: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        """Return the parts whose ``enum`` is a part of the site: all but the last under the legacy sibling enum row."""
        counted = parts if self.own_enum_is_part else parts[:-1]
        return [part for part in counted if isinstance(part.get("enum"), list)]

    def schema(self, parts: Sequence[Mapping[str, Any]], row: dict[str, Any]) -> dict[str, Any] | None:
        """Return a copy of ``row`` with the keywords it gets wrong replaced, or None when it gets none wrong."""
        updates: dict[str, Any] = {}
        for lower, upper in _BOUND_DIMENSIONS:
            self._bounds(lower, upper, parts, row, updates)
        if (merged := _intersect_values("multipleOf", parts)) is not None and row.get("multipleOf") != merged:
            updates["multipleOf"] = merged
        if (merged := _intersect_values("uniqueItems", parts)) is not None and row.get("uniqueItems") != merged:
            updates["uniqueItems"] = merged
        names = [part["required"] for part in parts if part.get("required")]
        if len(names) > 1 and set(union := list(dict.fromkeys(chain.from_iterable(names)))) != set(
            row.get("required") or ()
        ):
            updates["required"] = union
        self._pattern(parts, row, updates)
        self._type(parts, row, updates)
        self._enum(parts, row, updates)
        self._const(parts)
        for keyword in _SCHEMA_KEYWORDS:
            values = [part[keyword] for part in parts if keyword in part]
            if len(values) > 1 and (merged := self._value(values, row.get(keyword))) is not None:
                updates[keyword] = merged
        for keyword in _SCHEMA_MAP_KEYWORDS:
            self._schema_map(keyword, parts, row, updates)
        if not updates:
            return None
        result = copy(row)
        result.update(updates)
        for keyword in [keyword for keyword, value in updates.items() if value is _REMOVED]:
            del result[keyword]
        return result

    def _bounds(
        self,
        lower: tuple[str, ...],
        upper: tuple[str, ...],
        parts: Sequence[Mapping[str, Any]],
        row: dict[str, Any],
        updates: dict[str, Any],
    ) -> None:
        """Write the tightest bounds of one dimension, unless they admit no value: the row's bounds then stay.

        Bounds that admit no value only because one part's own bounds admit none are no conflict between the parts.
        """
        keywords = (*lower, *upper)
        merged = {keyword: value for keyword in keywords if (value := _intersect_values(keyword, parts)) is not None}
        if _crosses({**row, **merged}, lower, upper):
            if not any(_crosses(part, lower, upper) for part in parts):
                self.unsatisfiable.extend((lower[0], upper[0]))
            return
        updates.update({keyword: value for keyword, value in merged.items() if row.get(keyword) != value})

    def _pattern(self, parts: Sequence[Mapping[str, Any]], row: dict[str, Any], updates: dict[str, Any]) -> None:
        """Write the conjunction of the parts' patterns, unless the row's pattern implies each of them."""
        patterns = [pattern for part in parts if isinstance(pattern := part.get("pattern"), str)]
        if len(set(patterns)) <= 1 or (
            isinstance(row_pattern := row.get("pattern"), str)
            and all(pattern_implies(row_pattern, pattern) for pattern in patterns)
        ):
            return
        conjunction = conjoin_patterns(patterns)
        if conjunction.untaken and isinstance(row_pattern, str):
            self.untaken_patterns.extend(pattern for pattern in dict.fromkeys(patterns) if pattern != row_pattern)
            return
        self.untaken_patterns.extend(conjunction.untaken)
        if conjunction.pattern != row_pattern:
            updates["pattern"] = conjunction.pattern

    def _type(self, parts: Sequence[Mapping[str, Any]], row: dict[str, Any], updates: dict[str, Any]) -> None:
        """Write the types every part admits, and null by the null rule.

        Null is admitted when the site's own keywords (the last part) admit it explicitly, else when every part admits
        it: explicitly, or by having no ``type``.
        """
        if not (typed := [_types(part["type"]) for part in parts if "type" in part]):
            return
        admits_null = _explicit_null(parts[-1]) or all(_explicit_null(part) or "type" not in part for part in parts)
        if not admits_null and row.get("nullable") is True:
            updates["nullable"] = _REMOVED
        if len(typed) <= 1:
            return
        merged = typed[0]
        for types in typed[1:]:
            merged = _intersect_types(merged, types)
        if not merged:
            self.unsatisfiable.append("type")
            return
        row_types = _types(row.get("type"))
        written = [
            *(schema_type for schema_type in merged if schema_type != "null"),
            *(["null"] if admits_null and "null" in row_types else []),
        ]
        if written and set(written) != set(row_types):
            updates["type"] = written[0] if len(written) == 1 else written

    def _enum(self, parts: Sequence[Mapping[str, Any]], row: dict[str, Any], updates: dict[str, Any]) -> None:
        if len(enum_parts := self._enum_parts(parts)) <= 1:
            return
        first = enum_parts[0]["enum"]
        indices = sorted(set.intersection(*(set(_retained_indices(first, part["enum"])) for part in enum_parts[1:])))
        if not indices:
            self.unsatisfiable.append("enum")
            return
        retained = [first[index] for index in indices]
        if (
            isinstance(row_enum := row.get("enum"), list)
            and len(_retained_indices(row_enum, retained)) == len(row_enum)
            and len(_retained_indices(retained, row_enum)) == len(retained)
        ):
            return
        updates["enum"] = retained
        updates.update({
            key: [enum_metadata(enum_parts, key, index) for index in indices]
            for key in ENUM_METADATA_KEYWORDS
            if any(part.get(key) for part in enum_parts)
        })

    def _const(self, parts: Sequence[Mapping[str, Any]]) -> None:
        """Record a ``const`` that another part's ``const`` or ``enum`` does not admit."""
        consts = [(part, part[self.extras_key]["const"]) for part in parts if "const" in part.get(self.extras_key, {})]
        if not consts:
            return
        first = consts[0][1]
        if not all(json_values_equal(first, value) for _, value in consts[1:]):
            self.unsatisfiable.append("const")
        elif any(
            not any(json_values_equal(value, item) for item in enum_part["enum"])
            for owner, value in consts
            for enum_part in self._enum_parts(parts)
            if enum_part is not owner
        ):
            self.unsatisfiable.extend(("enum", "const"))

    def _schema_map(
        self, keyword: str, parts: Sequence[Mapping[str, Any]], row: dict[str, Any], updates: dict[str, Any]
    ) -> None:
        maps = [part[keyword] for part in parts if isinstance(part.get(keyword), dict)]
        if len(maps) <= 1 or not isinstance(row_map := row.get(keyword), dict):
            return
        changed = {
            name: merged
            for name in dict.fromkeys(chain.from_iterable(maps))
            if len(values := [schema_map[name] for schema_map in maps if name in schema_map]) > 1
            and (merged := self._value(values, row_map.get(name))) is not None
        }
        if changed:
            updates[keyword] = copy(row_map)
            updates[keyword].update(changed)

    def _value(self, values: Sequence[object], row_value: object) -> object | None:
        """Return the intersection of schema values (``false`` wins, ``true`` adds nothing), or None for the row's."""
        if any(value is False for value in values):
            return None if row_value is False else False
        schemas = [value for value in values if isinstance(value, dict)]
        if not schemas or len(schemas) < sum(value is not True for value in values):
            return None
        if len(schemas) == 1:
            return None if row_value is schemas[0] else schemas[0]
        return self.schema(schemas, row_value) if isinstance(row_value, dict) else None


def intersect_keywords(
    parts: Sequence[Mapping[str, Any]], row: dict[str, Any], *, extras_key: str, ref_siblings_are_parts: bool = True
) -> KeywordIntersection:
    """Return the schema of a site whose ``parts`` a legacy merge wrote as ``row``, keyword by keyword.

    ``parts`` are dumped schema dicts in order: the referenced or inherited schema first, the site's own keywords
    last. ``extras_key`` is where a dump keeps keywords such as ``const``. A keyword of the row stays wherever it
    enforces what the intersection of the parts enforces (a looser bound, a concatenated or wider ``enum``, a wider
    ``type`` or a dropped ``false`` does not), and nested schemas follow the same rule. ``ref_siblings_are_parts`` is
    False for ``$ref`` siblings in a dialect that does not read them as parts (the legacy sibling enum row).
    """
    reconciler = _Reconciler(extras_key, own_enum_is_part=ref_siblings_are_parts)
    keywords = reconciler.schema(parts, row)
    return KeywordIntersection(
        copy(row) if keywords is None else keywords,
        tuple(dict.fromkeys(reconciler.unsatisfiable)),
        tuple(dict.fromkeys(reconciler.untaken_patterns)),
    )
