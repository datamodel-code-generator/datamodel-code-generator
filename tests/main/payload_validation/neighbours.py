"""Value neighbours: the nearest payloads on both sides of each value keyword of a schema case.

A neighbour replaces one value of a source-valid seed payload with a value next to a keyword's boundary: a ``const`` or
``enum`` member in every other JSON kind, a bound one step off, a length or count off by one, a multiple of
``multipleOf`` and its neighbour, a discriminator tag of another kind. The suite's validator decides each neighbour;
the generated models are compared with that verdict. The seed and the neighbours are pure functions of the schema.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Final, TypeGuard

from scripts.check_ratchets import REJECTED_SEED_LINE

from .codegen import PayloadModelError
from .conformance import rejected_mutation_reason
from .models import PayloadBackend
from .mutation import COMBINED_SCHEMA_KEYS, _resolve_internal_ref
from .strategy import (
    NUMERIC_FORMAT_KINDS,
    _canonical_json_numbers,
    _json_number,
    _regex_agreement_validator,
    _RegexEnginesDisagreeError,
    format_example,
    numeric_keywords,
    numeric_text_schema,
    source_schema_validator,
    typed_string_formats,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from jsonschema.protocols import Validator

    from .codegen import PayloadRuntime
    from .models import JsonPath, SchemaCase
    from .strategy import SourceSchemaValidator

BOUND_KEYWORDS: Final = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
LENGTH_KEYWORDS: Final = ("minLength", "maxLength")
ITEM_COUNT_KEYWORDS: Final = ("minItems", "maxItems")
PROPERTY_COUNT_KEYWORDS: Final = ("minProperties", "maxProperties")
CONTAINS_COUNT_KEYWORDS: Final = ("contains", "minContains", "maxContains")
VALUE_NEIGHBOUR_KEYWORDS: Final = frozenset({
    "const",
    "enum",
    "multipleOf",
    "discriminator",
    *BOUND_KEYWORDS,
    *LENGTH_KEYWORDS,
    *ITEM_COUNT_KEYWORDS,
    *PROPERTY_COUNT_KEYWORDS,
    *CONTAINS_COUNT_KEYWORDS,
})
"""The keywords whose neighbours the suite builds."""

SEED_KEYWORD: Final = "seed"
"""The keyword under which the neighbours list their source-valid seed itself."""
SCHEMA_VALIDATOR_ARGS: Final = ("--generate-schema-validators",)
"""The option whose models are asserted wherever the case's default Pydantic v2 model decides like the validator."""
ENUM_MEMBERS_WITH_NEIGHBOURS: Final = 3
"""The first members of an enum that get neighbours; later members repeat the kinds of the first."""
LONGEST_NEIGHBOUR: Final = 1024
"""The longest string, array or object a neighbour builds; longer bounds get no length or count neighbour."""
SEED_CHECKS: Final = 5000
"""The candidate values the seed builder draws per case before it gives up, which bounds its search of branches."""
DEEPEST_SEED: Final = 12
"""The deepest nesting the seed builder follows before it leaves out optional values."""
_UNTYPED_KINDS: Final = ("string", "integer", "object", "array", "boolean", "null", "number")
_FORMAT_SAMPLES: Final = {
    "date": "2020-01-02",
    "date-time": "2020-01-02T03:04:05Z",
    "time": "03:04:05Z",
    "duration": "P1D",
    "email": "user@example.com",
    "idn-email": "user@example.com",
    "hostname": "example.com",
    "idn-hostname": "example.com",
    "ipv4": "127.0.0.1",
    "ipv6": "::1",
    "uri": "https://example.com/",
    "uri-reference": "https://example.com/",
    "iri": "https://example.com/",
    "iri-reference": "https://example.com/",
    "url": "https://example.com/",
    "uuid": "12345678-1234-5678-1234-567812345678",
    "byte": "YQ==",
    "binary": "a",
    "password": "a",
    "regex": "a",
    "json-pointer": "/a",
    "relative-json-pointer": "0",
    "uri-template": "https://example.com/{a}",
}


def _number_keywords(schema: dict[str, object]) -> dict[str, object]:
    """Return the number keywords of a canonical schema as JSON values."""
    return {keyword: _json_value(value) for keyword, value in numeric_keywords(schema).items()}


def _format_text(name: str, keywords: dict[str, object]) -> str | None:
    """Return a text of a string format: the strategy's for a numeric one, else a sample, else the strategy's."""
    if name not in NUMERIC_FORMAT_KINDS and (sample := _FORMAT_SAMPLES.get(name)) is not None:
        return sample
    return format_example(name, keywords)


_KIND_KEYWORDS: Final = (
    ("object", frozenset({"properties", "required", "additionalProperties", "patternProperties", "minProperties"})),
    ("array", frozenset({"items", "prefixItems", "contains", "minItems", "maxItems", "uniqueItems"})),
    ("string", frozenset({"minLength", "maxLength", "pattern", "format"})),
    ("integer", frozenset({*BOUND_KEYWORDS, "multipleOf"})),
    ("number", frozenset({*BOUND_KEYWORDS, "multipleOf"})),
)
"""The JSON kinds the keywords of an untyped schema suggest."""
_BRANCH_KEYWORDS: Final = ("anyOf", "oneOf", "then", "else")
"""The keywords whose subschemas the seed builder tries one at a time beside the rest of the schema, in this order."""
_MISSING: Final = object()


@dataclass(frozen=True)
class ValueNeighbour:
    """A payload one step from a keyword's boundary, with the verdict of the suite's validator."""

    keyword: str
    path: JsonPath
    value: object
    payload: object
    valid: bool
    formats: frozenset[str] = frozenset()
    """The string formats of the schemas that apply at the path, which the generated types may take as their own."""
    numeric_format: str = ""
    """The numeric string format whose number keywords the value, a number's text, is checked against, else empty."""

    @property
    def label(self) -> str:
        """The stable text naming the neighbour: its JSON pointer, keyword and JSON value.

        The root's pointer is empty, so its label starts with the keyword, while ``/`` names a property with an empty
        name: no two paths share a label.
        """
        pointer = "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in self.path)
        words = [pointer] if pointer else []
        words.append(self.keyword)
        if self.keyword != SEED_KEYWORD:
            words.append(json.dumps(self.value, sort_keys=True))
        return " ".join(words)


def json_kind_key(value: object) -> tuple[str, str]:
    """Return a key that tells JSON values apart by kind and value, where Python equality takes ``1 == 1.0 == True``."""
    kind = type(value).__name__
    match value:
        case bool():
            kind = "boolean"
        case int() | float() | Fraction():
            return "number", _number_text(Fraction(value))
        case str():
            kind = "string"
        case None:
            kind = "null"
        case list():
            return "array", json.dumps([json_kind_key(item) for item in value])
        case dict():
            return "object", json.dumps(sorted((key, json_kind_key(item)) for key, item in value.items()))
    return kind, json.dumps(value)


def json_form_key(value: object) -> str:
    """Return the JSON text of a payload value, which tells apart forms of one JSON value such as ``1`` and ``1.0``."""
    return json.dumps(value, sort_keys=True)


def _number_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else str(value)


def _json_value(value: object) -> object:
    """Return a canonical schema value as the JSON value it stands for: a Fraction as an int or a float."""
    match value:
        case Fraction() if value.denominator == 1:
            return value.numerator
        case Fraction():
            return _float(value)
        case list():
            return [_json_value(item) for item in value]
        case dict():
            return {key: _json_value(item) for key, item in value.items()}
        case _:
            return value


def _float(value: Fraction) -> float | None:
    """Return the nearest float, or None (no neighbour) for a value beyond the float range."""
    try:
        return float(value)
    except OverflowError:
        return None


def _is_number(value: object) -> TypeGuard[int | float | Fraction]:
    """Return whether a value is a JSON number, which a boolean is not."""
    return isinstance(value, int | float | Fraction) and not isinstance(value, bool)


def _decimal_step(value: Fraction) -> Fraction:
    """Return a tenth of the last decimal place of a bound: 0.1 beside 10, 0.01 beside 1.5."""
    places = (Decimal(value.numerator) / Decimal(value.denominator)).normalize().as_tuple().exponent
    return Fraction(1, 10 ** (1 - min(places, 0))) if isinstance(places, int) else Fraction(1, 10)


def _text_number(text: str) -> Fraction | None:
    """Return the number a string spells, or None."""
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def _member_number_and_text(value: object) -> tuple[Fraction, str]:
    """Return the number and the string that stand for a member in the other JSON kinds."""
    if isinstance(value, bool):
        return Fraction(int(value)), json.dumps(value)
    if _is_number(value):
        number = Fraction(value)
        return number, _number_text(number) if number.denominator == 1 else str(_json_value(number))
    if isinstance(value, str):
        return Fraction(1) if (number := _text_number(value)) is None else number, value
    return Fraction(1), json.dumps(_json_value(value), sort_keys=True)


def kind_forms(value: object) -> list[object]:
    """Return a member in every JSON kind: ``1`` gives ``1.0``, ``true``, ``"1"``, ``[1]``, ``{"a": 1}``, ``null``.

    Forms are told apart by their JSON text, so the integer-valued float ``1.0`` is drawn beside the integer ``1``.
    """
    number, text = _member_number_and_text(value)
    forms = [
        _json_value(number),
        _float(number),
        number != 0 if number in {0, 1} else True,
        text,
        [_json_value(value)],
        {"a": _json_value(value)},
        None,
    ]
    unique = {json_form_key(_json_value(value)): _json_value(value)}
    for form in forms:
        unique.setdefault(json_form_key(form), form)
    return list(unique.values())


def _item_schemas(parts: list[dict[str, object]]) -> tuple[list[object], list[object]]:
    """Return the prefix item schemas of an array schema and the schemas of its other items."""
    prefix: list[object] = []
    items: list[object] = []
    for part in parts:
        prefix_items, item_schema = part.get("prefixItems"), part.get("items")
        if not isinstance(prefix_items, list) and isinstance(item_schema, list):
            prefix_items, item_schema = item_schema, None
        if isinstance(prefix_items, list):
            prefix = prefix or prefix_items
        if isinstance(item_schema, dict | bool):
            items.append(item_schema)
    return prefix, items


def _matches(pattern: str, name: str) -> bool:
    """Return whether a ``patternProperties`` regex matches a name, as the suite's validator searches it."""
    try:
        return re.search(pattern, name) is not None
    except re.error:
        return False


def _property_schemas(schema: dict[str, object], name: str) -> list[object]:
    """Return the schemas a schema object applies to the property of a name.

    The property takes its ``properties`` schema and every ``patternProperties`` schema whose regex matches its name;
    ``additionalProperties`` applies only to a property neither of them names.
    """
    properties = schema.get("properties")
    patterns = schema.get("patternProperties")
    applying = [properties[name]] if isinstance(properties, dict) and name in properties else []
    if isinstance(patterns, dict):
        applying.extend(subschema for pattern, subschema in patterns.items() if _matches(pattern, name))
    if not applying and "additionalProperties" in schema:
        applying.append(schema["additionalProperties"])
    return applying


def _object_property_schemas(parts: list[dict[str, object]], name: str) -> list[object]:
    """Return the schemas the parts of an object schema apply to the property of a name, or ``[True]`` for none."""
    return [schema for part in parts for schema in _property_schemas(part, name)] or [True]


def _new_names(taken: dict[str, object], size: int) -> list[str]:
    """Return the names ``neighbour<n>`` an object adds to reach ``size`` properties, past the names it holds."""
    names = (f"neighbour{index}" for index in range(size + len(taken)))
    return [name for name in names if name not in taken][: max(size - len(taken), 0)]


def _subschemas(value: object) -> list[object]:
    """Return the subschemas a keyword holds: the members of a list, or the one schema."""
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) else []


def _written_values(parts: list[dict[str, object]]) -> Iterator[object]:
    """Yield the values a schema writes out: its constants and enum members first, then its defaults and examples."""
    for part in parts:
        if "const" in part:
            yield _json_value(part["const"])
        if isinstance(enum := part.get("enum"), list):
            yield from (_json_value(member) for member in enum)
    for part in parts:
        for keyword in ("default", "examples", "example"):
            if keyword in part:
                written = part[keyword]
                values = written if keyword == "examples" and isinstance(written, list) else [written]
                yield from (_json_value(deepcopy(value)) for value in values)


class _Seeds:
    """Build a source-valid value for a subschema from its keywords, checked by the case's validator."""

    def __init__(self, root: dict[str, object], validator: Validator) -> None:
        self.root = root
        self.validator = validator
        self.checks = SEED_CHECKS

    def anew(self) -> _Seeds:
        """Return the builder with its whole budget of checks again, for a value built after the seed."""
        self.checks = SEED_CHECKS
        return self

    def accepts(self, schema: object, value: object) -> bool:
        """Return whether the subschema accepts the value."""
        return self.validator.evolve(schema=schema).is_valid(_canonical_json_numbers(value))

    def parts(self, schema: object, seen: frozenset[str] = frozenset()) -> list[dict[str, object]]:
        """Return the schema and the schemas its ``$ref`` and ``allOf`` add to it."""
        if not isinstance(schema, dict):
            return []
        parts = [schema]
        if isinstance(ref := schema.get("$ref"), str) and ref not in seen and (target := self.resolve(ref)) is not None:
            parts.extend(self.parts(target, seen | {ref}))
        for member in schema.get("allOf", ()) if isinstance(schema.get("allOf"), list) else ():
            parts.extend(self.parts(member, seen))
        return parts

    def resolve(self, ref: str) -> object | None:
        return self.root if ref == "#" else _resolve_internal_ref(self.root, ref)

    def witness(self, schemas: list[object], depth: int = 0) -> object:
        """Return the first value built for the schemas together that each of them accepts, or ``_MISSING``."""
        return next(iter(self.distinct(schemas, 1, depth)), _MISSING)

    def distinct(self, schemas: list[object], count: int, depth: int) -> list[object]:
        """Return up to ``count`` values of different JSON kinds or values that every one of the schemas accepts."""
        if any(schema is False for schema in schemas):
            return []
        values: dict[tuple[str, str], object] = {}
        for candidate in self.candidates(schemas[0] if len(schemas) == 1 else {"allOf": schemas}, depth):
            self.checks -= 1
            if self.checks < 0:
                break
            if (key := json_kind_key(candidate)) not in values and all(
                self.accepts(schema, candidate) for schema in schemas
            ):
                values[key] = candidate
                if len(values) >= count:
                    break
        return list(values.values())

    def candidates(self, schema: object, depth: int) -> Iterator[object]:
        """Yield the values a schema writes out, then values built for each of its branches, then from its keywords."""
        if schema is True or schema == {}:
            yield from ("a", 0, True, None, {}, [])
            return
        self.checks -= 1
        parts = self.parts(schema)
        if not parts or depth > DEEPEST_SEED or self.checks < 0:
            return
        yield from _written_values(parts)
        yield from self.branch_candidates(parts, depth)
        for kind in self.kinds(parts):
            yield from self.kind_candidates(kind, parts, depth)

    def branch_candidates(self, parts: list[dict[str, object]], depth: int) -> Iterator[object]:
        """Yield the values built for each branch of the schema together with the rest of the schema."""
        flat = [{key: value for key, value in part.items() if key not in {"allOf", "$ref"}} for part in parts]
        for index, part in enumerate(flat):
            rest = flat[:index] + flat[index + 1 :]
            own = {key: value for key, value in part.items() if key not in {*_BRANCH_KEYWORDS, "if"}}
            for keyword in _BRANCH_KEYWORDS:
                for branch in _subschemas(part.get(keyword)):
                    yield from self.candidates({"allOf": [*rest, own, branch]}, depth + 1)

    @staticmethod
    def kinds(parts: list[dict[str, object]]) -> list[str]:
        """Return the JSON kinds to build, the ones the keywords suggest first, within every declared type."""
        declared: set[str] | None = None
        for part in parts:
            if isinstance(written := part.get("type"), str):
                types = {written}
            elif isinstance(written, list):
                types = {kind for kind in written if isinstance(kind, str)}
            else:
                continue
            if "number" in types:
                types.add("integer")
            declared = types if declared is None else declared & types
        keys = {key for part in parts for key in part}
        inferred = [kind for kind, keywords in _KIND_KEYWORDS if keys & keywords]
        order = [*inferred, *(kind for kind in _UNTYPED_KINDS if kind not in inferred)]
        return order if declared is None else [kind for kind in order if kind in declared]

    def kind_candidates(self, kind: str, parts: list[dict[str, object]], depth: int) -> Iterator[object]:
        match kind:
            case "object":
                yield from self.objects(parts, depth)
            case "array":
                yield from self.arrays(parts, depth)
            case "string":
                yield from self.strings(parts)
            case "integer":
                yield from self.numbers(parts, integral=True)
            case "number":
                yield from self.numbers(parts, integral=False)
            case "boolean":
                yield from (True, False)
            case "null":
                yield None

    def objects(self, parts: list[dict[str, object]], depth: int) -> Iterator[dict[str, object]]:
        names: dict[str, None] = {}
        required: dict[str, None] = {}
        fewest = 0
        for part in parts:
            if isinstance(part_properties := part.get("properties"), dict):
                names.update(dict.fromkeys(part_properties))
            if isinstance(part_required := part.get("required"), list):
                required.update(dict.fromkeys(name for name in part_required if isinstance(name, str)))
            if _is_number(least := part.get("minProperties")):
                fewest = max(fewest, int(least))
        built: dict[str, object] = {}
        for name in {**names, **required}:
            optional_too_deep = name not in required and depth >= DEEPEST_SEED // 2
            if optional_too_deep or (value := self.property_witness(parts, name, depth + 1)) is _MISSING:
                if name in required:
                    return
                continue
            built[name] = value
        if len(built) < fewest <= LONGEST_NEIGHBOUR:
            self.add_properties(parts, built, fewest, depth + 1)
        yield built
        yield {name: value for name, value in built.items() if name in required}

    def property_witness(self, parts: list[dict[str, object]], name: str, depth: int) -> object:
        """Return a value for the property of a name that every schema the parts apply to it accepts."""
        return self.witness(_object_property_schemas(parts, name), depth)

    def add_properties(self, parts: list[dict[str, object]], built: dict[str, object], size: int, depth: int) -> None:
        """Add properties named ``neighbour<n>`` to an object until it has ``size``, each a value its schemas accept."""
        values: dict[tuple[int, ...], object] = {}
        for name in _new_names(built, size):
            schemas = _object_property_schemas(parts, name)
            if (key := tuple(map(id, schemas))) not in values:
                values[key] = self.witness(schemas, depth)
            if values[key] is _MISSING:
                return
            built[name] = deepcopy(values[key])

    def arrays(self, parts: list[dict[str, object]], depth: int) -> Iterator[list[object]]:
        prefix, items = _item_schemas(parts)
        least = max((int(value) for part in parts if _is_number(value := part.get("minItems"))), default=0) or 1
        unique = any(part.get("uniqueItems") is True for part in parts)
        contains = [
            (part["contains"], int(count) if _is_number(count := part.get("minContains")) else 1)
            for part in parts
            if "contains" in part
        ]
        if least > LONGEST_NEIGHBOUR:
            return
        values = [
            value for value in (self.witness([schema], depth + 1) for schema in prefix[:least]) if value is not _MISSING
        ]
        if len(values) == len(prefix[:least]) and (rest := least - len(values)) > 0:
            values.extend(self.items(items or [True], rest, depth + 1, unique=unique))
        for matcher, count in contains:
            if (
                0 < count <= LONGEST_NEIGHBOUR
                and len(matching := self.items([matcher, *items], count, depth + 1, unique=unique)) == count
            ):
                taken = {json_kind_key(value) for value in matching} if unique else set()
                kept = [value for value in values if json_kind_key(value) not in taken]
                values = [*kept[: max(len(values) - count, 0)], *matching]
        yield values
        yield []

    def items(self, schemas: list[object], count: int, depth: int, *, unique: bool) -> list[object]:
        """Return ``count`` items the schemas accept, all different where the array's items must be unique."""
        if unique:
            return self.distinct(schemas, count, depth)
        value = self.witness(schemas, depth)
        return [] if value is _MISSING else [deepcopy(value) for _ in range(count)]

    @staticmethod
    def strings(parts: list[dict[str, object]]) -> Iterator[str]:
        least = max((int(value) for part in parts if _is_number(value := part.get("minLength"))), default=0)
        most = min((int(value) for part in parts if _is_number(value := part.get("maxLength"))), default=None)
        keywords = {key: value for part in parts for key, value in _number_keywords(part).items()}
        bases = [
            text
            for part in parts
            if isinstance(fmt := part.get("format"), str) and (text := _format_text(fmt, keywords)) is not None
        ]
        bases.extend(
            pattern.removeprefix("^").removesuffix("$")
            for part in parts
            if isinstance(pattern := part.get("pattern"), str)
        )
        for base in (*bases, "a", "A", "0", "1", "a1", "aA0", "x-y"):
            text = base + (base[-1:] or "a") * max(least - len(base), 0) if least <= LONGEST_NEIGHBOUR else base
            yield text if most is None else text[:most]

    @staticmethod
    def numbers(parts: list[dict[str, object]], *, integral: bool) -> Iterator[object]:
        low, high = _bounds(parts, "minimum", "exclusiveMinimum"), _bounds(parts, "maximum", "exclusiveMaximum")
        multiples = [
            Fraction(multiple) for part in parts if _is_number(multiple := part.get("multipleOf")) and multiple > 0
        ]
        candidates = [Fraction(0), Fraction(1)]
        for bound, sign in ((low, 1), (high, -1)):
            if bound is not None:
                candidates.append(bound.value)
                if bound.exclusive:
                    candidates.append(bound.value + sign * _decimal_step(bound.value))
        if low is not None and high is not None:
            candidates.append((low.value + high.value) / 2)
        step = math.lcm(*(multiple.numerator for multiple in multiples)) if multiples else 1
        unit = Fraction(step, math.gcd(*(multiple.denominator for multiple in multiples))) if multiples else Fraction(1)
        if integral and unit.denominator != 1:
            unit = Fraction(unit.numerator)
        for candidate in candidates:
            for rounded in (math.ceil(candidate / unit) * unit, math.floor(candidate / unit) * unit):
                if _within(rounded, low, high):
                    yield _json_value(rounded)
        if not integral and not multiples:
            yield from (
                number
                for candidate in candidates
                if _within(candidate, low, high) and (number := _json_value(candidate)) is not None
            )
        yield from (0, 1)


@dataclass(frozen=True)
class _Bound:
    """A least or greatest number a schema allows, and whether the number itself is left out."""

    value: Fraction
    exclusive: bool


def _within(number: Fraction, low: _Bound | None, high: _Bound | None) -> bool:
    """Return whether a number lies between the bounds, outside the ones that leave their number out."""
    above = low is None or number > low.value or (number == low.value and not low.exclusive)
    return above and (high is None or number < high.value or (number == high.value and not high.exclusive))


def _bounds(parts: list[dict[str, object]], keyword: str, exclusive: str) -> _Bound | None:
    """Return the tightest bound the parts set with a keyword and its exclusive form, a number or a draft-4 flag."""
    bounds: list[_Bound] = []
    for part in parts:
        if _is_number(bound := part.get(keyword)):
            bounds.append(_Bound(Fraction(bound), exclusive=part.get(exclusive) is True))
        if _is_number(bound := part.get(exclusive)):
            bounds.append(_Bound(Fraction(bound), exclusive=True))
    if keyword == "minimum":
        return max(bounds, key=lambda bound: (bound.value, bound.exclusive), default=None)
    return min(bounds, key=lambda bound: (bound.value, not bound.exclusive), default=None)


@dataclass(frozen=True)
class _Site:
    """A schema object that applies to the value at a path of the seed."""

    schema: dict[str, object]
    path: JsonPath
    value: object


def _iter_sites(
    seeds: _Seeds, schema: object, value: object, path: JsonPath, seen: frozenset[str] = frozenset()
) -> Iterator[_Site]:
    """Yield every schema object that applies to a value of the seed, with the path of the value.

    ``seen`` holds the references followed for the value at this path, so a reference cycle stops where it would
    come back to the same value, while a recursive schema still gets sites at every depth the seed reaches.
    """
    if not isinstance(schema, dict):
        return
    yield _Site(schema, path, value)
    if isinstance(ref := schema.get("$ref"), str) and ref not in seen and (target := seeds.resolve(ref)) is not None:
        yield from _iter_sites(seeds, target, value, path, seen | {ref})
    for keyword in (*COMBINED_SCHEMA_KEYS, "if", "then", "else"):
        for branch in _subschemas(schema.get(keyword)):
            yield from _iter_sites(seeds, branch, value, path, seen)
    match value:
        case dict():
            yield from _iter_object_sites(seeds, schema, value, path, seen)
        case list():
            yield from _iter_array_sites(seeds, schema, value, path)


def _iter_object_sites(
    seeds: _Seeds, schema: dict[str, object], value: dict[str, object], path: JsonPath, seen: frozenset[str]
) -> Iterator[_Site]:
    """Yield the sites of each property from every schema that applies to it, and of the ``dependentSchemas``."""
    for name, item in value.items():
        for subschema in _property_schemas(schema, name):
            yield from _iter_sites(seeds, subschema, item, (*path, name))
    if isinstance(dependent := schema.get("dependentSchemas"), dict):
        for name, subschema in dependent.items():
            if name in value:
                yield from _iter_sites(seeds, subschema, value, path, seen)


def _iter_array_sites(seeds: _Seeds, schema: dict[str, object], value: list[object], path: JsonPath) -> Iterator[_Site]:
    """Yield the sites of each prefix item and of the first item past the prefix, with the ``contains`` schema.

    Every item past the prefix takes the same ``items`` and ``contains`` schemas, so the first of them reaches each
    schema position once; later items would repeat its neighbours under another index, at a cost quadratic in the
    length of the array.
    """
    prefix = schema.get("prefixItems")
    items = schema.get("items")
    if not isinstance(prefix, list):
        prefix, items = (items, schema.get("additionalItems")) if isinstance(items, list) else ([], items)
    for index, item in enumerate(value[: len(prefix) + 1]):
        item_schema = prefix[index] if index < len(prefix) else items
        yield from _iter_sites(seeds, item_schema, item, (*path, index))
        yield from _iter_sites(seeds, schema.get("contains"), item, (*path, index))


def _replaced(payload: object, path: JsonPath, value: object) -> object:
    """Return a copy of the payload with the value at the path replaced."""
    if not path:
        return deepcopy(value)
    replaced = deepcopy(payload)
    target = replaced
    for part in path[:-1]:
        target = _child(target, part)
    last = path[-1]
    if isinstance(target, dict) and isinstance(last, str):
        target[last] = deepcopy(value)
    if isinstance(target, list) and isinstance(last, int):
        target[last] = deepcopy(value)
    return replaced


def _child(container: object, part: str | int) -> object:
    if isinstance(container, dict) and isinstance(part, str):
        return container[part]
    if isinstance(container, list) and isinstance(part, int):
        return container[part]
    return None


def _numbers_beside(bound: Fraction) -> list[Fraction]:
    """Return the bound and the numbers one integer step and one decimal step off on either side."""
    step = _decimal_step(bound)
    integers = (
        [bound - 1, bound, bound + 1]
        if bound.denominator == 1
        else [Fraction(math.floor(bound)), Fraction(math.ceil(bound))]
    )
    return [*integers, bound - step, bound, bound + step]


def _member_values(schema: dict[str, object]) -> Iterator[tuple[str, object]]:
    """Yield the first members of ``const`` and ``enum`` in every JSON kind."""
    for keyword in ("const", "enum"):
        members = [schema["const"]] if keyword == "const" and "const" in schema else schema.get(keyword)
        if isinstance(members, list):
            for member in members[:ENUM_MEMBERS_WITH_NEIGHBOURS]:
                yield from ((keyword, form) for form in kind_forms(member))


def _number_values(schema: dict[str, object]) -> Iterator[tuple[str, object]]:
    """Yield the numbers beside each bound, and a multiple of ``multipleOf`` with the numbers just past it."""
    for keyword in BOUND_KEYWORDS:
        if _is_number(bound := schema.get(keyword)):
            numbers = (_json_value(number) for number in _numbers_beside(Fraction(bound)))
            yield from ((keyword, number) for number in numbers if number is not None)
    if _is_number(multiple := schema.get("multipleOf")) and multiple > 0:
        multiple = Fraction(multiple)
        values = [3 * multiple, 3 * multiple + _decimal_step(multiple)]
        if multiple.denominator == 1 and multiple > 1:
            values.append(3 * multiple + 1)
        yield from (("multipleOf", number) for value in values if (number := _json_value(value)) is not None)


def _sized(seeds: _Seeds, site: _Site, size: int) -> object:
    """Return the seed's string, array or object at a size: cut, or padded with its last character, item or a property.

    The names an object adds are ``neighbour<n>``, each with a value the schemas that apply to it accept.
    """
    match value := site.value:
        case str():
            pad = value[-1:] or "a"
            return value[:size] + pad * max(size - len(value), 0)
        case list() if value:
            return value[:size] + [deepcopy(value[-1]) for _ in range(max(size - len(value), 0))]
        case dict():
            sized = {name: value[name] for name in list(value)[:size]}
            seeds.anew().add_properties(seeds.parts(site.schema), sized, size, 0)
            return sized if len(sized) == size else _MISSING
    return _MISSING


def _keeping_the_rest(
    seeds: _Seeds, site: _Site, keywords: tuple[str, ...], near: object, target: dict[str, object]
) -> object:
    """Return a value next to a keyword's boundary that every other keyword of the site's schema accepts, or none.

    A neighbour that broke a second keyword would test nothing about the one it varies. The value made from the
    seed's (``near``) comes first; else a value is built for the rest of the schema together with ``target``, the
    schema of the boundary's side.
    """
    rest = {key: value for key, value in site.schema.items() if key not in keywords}
    if near is not _MISSING and seeds.accepts(rest, near):
        return near
    return seeds.anew().witness([rest, target])


def _size_values(seeds: _Seeds, site: _Site) -> Iterator[tuple[str, object]]:
    """Yield a string, array or object one step either side of each length and count bound of the site."""
    for (least, most), kind, name in (
        (LENGTH_KEYWORDS, str, "string"),
        (ITEM_COUNT_KEYWORDS, list, "array"),
        (PROPERTY_COUNT_KEYWORDS, dict, "object"),
    ):
        if not isinstance(site.value, kind):
            continue
        for keyword in (least, most):
            if _is_number(size := site.schema.get(keyword)) and 0 <= size <= LONGEST_NEIGHBOUR:
                size = int(size)
                for each in (size - 1, size) if keyword == least else (size, size + 1):
                    target = {"type": name, least: each, most: each}
                    if (
                        each >= 0
                        and (value := _keeping_the_rest(seeds, site, (keyword,), _sized(seeds, site, each), target))
                        is not _MISSING
                    ):
                        yield keyword, value


def _contains_values(seeds: _Seeds, site: _Site) -> Iterator[tuple[str, object]]:
    """Yield arrays with one count either side of the least and greatest count of items the ``contains`` accepts."""
    schema = site.schema
    if "contains" not in schema or not isinstance(site.value, list):
        return
    least = int(value) if _is_number(value := schema.get("minContains")) else 1
    counts = [("minContains" if "minContains" in schema else "contains", count) for count in (least - 1, least)]
    if _is_number(most := schema.get("maxContains")):
        counts.extend(("maxContains", count) for count in (int(most), int(most) + 1))
    for keyword, count in counts:
        target: dict[str, object] = (
            {"type": "array", "contains": schema["contains"], "minContains": count, "maxContains": count}
            if count
            else {"type": "array", "not": {"contains": schema["contains"]}}
        )
        if (
            0 <= count <= LONGEST_NEIGHBOUR
            and (value := _keeping_the_rest(seeds, site, CONTAINS_COUNT_KEYWORDS, _MISSING, target)) is not _MISSING
        ):
            yield keyword, value


def _tag_values(site: _Site) -> Iterator[tuple[str, object]]:
    """Yield the seed's object with its discriminator tag in every other JSON kind."""
    discriminator, value = site.schema.get("discriminator"), site.value
    if isinstance(discriminator, dict) and isinstance(value, dict):
        name = discriminator.get("propertyName")
        if isinstance(name, str) and name in value:
            yield from (("discriminator", {**value, name: form}) for form in kind_forms(value[name]))


def _site_values(seeds: _Seeds, site: _Site, *, numeric_text: bool) -> Iterator[tuple[str, object]]:
    """Yield each keyword of a site with a value next to its boundary.

    The number keywords of a site whose value is the text of a numeric string format apply to the number the text
    writes, so their values are texts.
    """
    yield from _member_values(site.schema)
    if numeric_text:
        yield from ((keyword, json.dumps(number)) for keyword, number in _number_values(site.schema))
    else:
        yield from _number_values(site.schema)
    yield from _size_values(seeds, site)
    yield from _contains_values(seeds, site)
    yield from _tag_values(site)


_NEIGHBOURS: dict[str, tuple[object, tuple[ValueNeighbour, ...]]] = {}
"""The seed and the neighbours of each case met in this process, by case id."""


def _neighbours(case: SchemaCase) -> tuple[object, tuple[ValueNeighbour, ...]]:
    if (known := _NEIGHBOURS.get(case.id)) is None:
        known = _NEIGHBOURS[case.id] = _build_neighbours(case)
    return known


def _build_neighbours(case: SchemaCase) -> tuple[object, tuple[ValueNeighbour, ...]]:
    validator = source_schema_validator(case)
    agreement = _regex_agreement_validator(case)
    seeds = _Seeds(_canonical_json_numbers(case.source_schema), validator.validator)
    seed = seeds.witness([seeds.root])
    if seed is _MISSING or not validator.is_valid(seed) or not _agrees(agreement, seed):
        return _MISSING, ()
    neighbours: dict[tuple[JsonPath, str, tuple[str, str]], ValueNeighbour] = {}
    sites = list(_iter_sites(seeds, seeds.root, seed, ()))
    formats: dict[JsonPath, set[str]] = {}
    for site in sites:
        if isinstance(name := site.schema.get("format"), str):
            formats.setdefault(site.path, set()).add(name)
    texts = {
        site.path: site.value
        for site in sites
        if isinstance(site.value, str) and formats.get(site.path, set()) & NUMERIC_FORMAT_KINDS.keys()
    }
    numbers = numeric_text_schema(seeds.root) if texts else None
    for site in sites:
        for keyword, value in _site_values(seeds, site, numeric_text=site.path in texts):
            key = (site.path, keyword, json_form_key(value))
            if key in neighbours or json_form_key(value) == json_form_key(site.value):
                continue
            payload = _replaced(seed, site.path, value)
            if (valid := _agrees(agreement, payload)) is None:
                continue
            counted = site.path in texts and keyword in {*BOUND_KEYWORDS, "multipleOf"}
            if counted:
                valid = valid and _numbers_are_valid(seeds, numbers, _replaced_texts(seed, texts, site.path, value))
            neighbours[key] = ValueNeighbour(
                keyword,
                site.path,
                value,
                payload,
                valid,
                frozenset(formats.get(site.path, ())),
                min(formats[site.path] & NUMERIC_FORMAT_KINDS.keys()) if counted else "",
            )
    ordered = sorted(neighbours.values(), key=lambda neighbour: neighbour.label)
    return seed, (ValueNeighbour(SEED_KEYWORD, (), seed, seed, valid=True), *ordered)


def _replaced_texts(seed: object, texts: dict[JsonPath, str], path: JsonPath, value: object) -> object:
    """Return the seed with the text of every numeric format, and the value at the path, as the numbers they write."""
    payload = seed
    for at, text in {**texts, path: value}.items():
        payload = _replaced(payload, at, _number_written_by(str(text)))
    return payload


def _number_written_by(text: str) -> object:
    """Return the JSON number a text writes, or the text itself where it writes none, which no number kind accepts."""
    try:
        number = _json_value(_json_number(text))
    except (ValueError, ArithmeticError):
        return text
    return text if number is None else number


def _numbers_are_valid(seeds: _Seeds, schema: object, payload: object) -> bool:
    """Return whether the whole schema, read with the texts of numeric formats as numbers, accepts the payload."""
    return seeds.validator.evolve(schema=schema).is_valid(_canonical_json_numbers(payload))


def _agrees(agreement: SourceSchemaValidator, payload: object) -> bool | None:
    """Return the validator's verdict, or None where the regex engines decide a matched string differently."""
    try:
        return agreement.is_valid(payload)
    except _RegexEnginesDisagreeError:
        return None


def has_value_neighbour_seed(case: SchemaCase) -> bool:
    """Return whether the seed builder found a source-valid payload for the case to start the neighbours from."""
    return _neighbours(case)[0] is not _MISSING


def value_neighbours(case: SchemaCase) -> tuple[ValueNeighbour, ...]:
    """Return the seed of a case, under the keyword ``seed``, then its value neighbours ordered by label.

    A model that decides the seed otherwise than the validator decides every neighbour for that reason, so the
    neighbours of such a seed tell nothing about their keywords.
    """
    return _neighbours(case)[1]


def has_value_neighbour_keywords(case: SchemaCase) -> bool:
    """Return whether a case's schema has a keyword the suite builds neighbours for."""
    return _has_keyword(case.source_schema)


def _has_keyword(value: object) -> bool:
    match value:
        case dict():
            return bool(VALUE_NEIGHBOUR_KEYWORDS & value.keys()) or any(_has_keyword(item) for item in value.values())
        case list():
            return any(_has_keyword(item) for item in value)
        case _:
            return False


def schema_validator_case(case: SchemaCase) -> SchemaCase:
    """Return the case generated for Pydantic v2 with ``--generate-schema-validators`` beside its own arguments."""
    arguments = case.backend_extra_args.get(PayloadBackend.PYDANTIC_V2, ())
    return replace(
        case,
        backend_extra_args={
            **case.backend_extra_args,
            PayloadBackend.PYDANTIC_V2: (*arguments, *SCHEMA_VALIDATOR_ARGS),
        },
    )


def model_verdict(runtime: PayloadRuntime, payload: object) -> str:
    """Return whether the generated model accepts or rejects a payload, or the type of any other error it raises."""
    try:
        accepted = runtime.accepts_python(payload)
    except PayloadModelError as error:
        return f"raises {error}"
    return "accepts" if accepted else "rejects"


def schema_verdict(neighbour: ValueNeighbour, backend: PayloadBackend) -> str | None:
    """Return the verdict a model gives a neighbour where it decides like the suite's validator, none where it cannot.

    The generator turns some string formats into types of their own, which the validator does not assert. A numeric
    format's number keywords then apply to the number its text writes, and the neighbour's validity is that number's,
    decided by the validator over ``numeric_text_schema``; a backend that keeps the format a string has no such
    verdict. A text cut or padded to a length the validator accepts need not be a value of such a type, so a model
    rejecting it proves nothing: only a text the validator rejects is compared, where a model accepting it is a real
    difference. Which formats a backend types is ``typed_string_formats``, the one place the suite reads for it.
    """
    typed = typed_string_formats(backend)
    if neighbour.numeric_format and neighbour.numeric_format not in typed:
        return None
    if neighbour.keyword in LENGTH_KEYWORDS and neighbour.valid and neighbour.formats & typed:
        return None
    return "accepts" if neighbour.valid else "rejects"


def neighbour_is_asserted(
    neighbour: ValueNeighbour,
    backend: PayloadBackend,
    verdict: str,
    *,
    schema_validators: bool,
    default_agrees: bool,
    full_runtime: bool,
) -> bool:
    """Return whether a differing verdict fails the suite instead of going into the unasserted list of its backend.

    The keywords ``conformance.py`` asserts for a backend are asserted; the models of ``--generate-schema-validators``
    are asserted wherever this environment's default Pydantic v2 model of the case decides like the validator, on a
    Pydantic release the suite fully supports, because the checks the option adds may close the default model's gaps
    but never open one. A verdict that names a raised error is never asserted: it is recorded with the error's type.
    """
    decided = verdict in {"accepts", "rejects"}
    return decided and (
        rejected_mutation_reason(neighbour.keyword, backend) is not None
        or (schema_validators and default_agrees and full_runtime)
    )


def differing_line(neighbour: ValueNeighbour, verdict: str) -> str:
    """Return the line naming a neighbour the model decides otherwise than the validator.

    A seed is valid, so it differs only where the model rejects it or raises: that line is the ratchet's blind line.
    """
    if neighbour.keyword == SEED_KEYWORD:
        return REJECTED_SEED_LINE
    return f"{neighbour.label}: {'valid' if neighbour.valid else 'invalid'}, model {verdict}"
