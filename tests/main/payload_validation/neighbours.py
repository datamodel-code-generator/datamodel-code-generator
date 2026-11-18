"""Value neighbours: the nearest payloads on both sides of each value keyword of a schema case.

A neighbour replaces one value of a source-valid seed payload with a value next to a keyword's boundary: a ``const`` or
``enum`` member in every other JSON kind, a bound one step off, a length or count off by one, a multiple of
``multipleOf`` and its neighbour, a discriminator tag of another kind. The suite's validator decides each neighbour;
the generated models are compared with that verdict. The seed and the neighbours are pure functions of the schema.
"""

from __future__ import annotations

import json
import math
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Final, TypeGuard

from .conformance import rejected_mutation_reason
from .models import PayloadBackend
from .mutation import COMBINED_SCHEMA_KEYS, _resolve_internal_ref
from .strategy import (
    _canonical_json_numbers,
    _regex_agreement_validator,
    _RegexEnginesDisagreeError,
    source_schema_validator,
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
    "decimal": "1.5",
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

    @property
    def label(self) -> str:
        """The stable text naming the neighbour: its JSON pointer, keyword and JSON value."""
        pointer = "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in self.path) or "/"
        if self.keyword == SEED_KEYWORD:
            return f"{pointer} {self.keyword}"
        return f"{pointer} {self.keyword} {json.dumps(self.value, sort_keys=True)}"


def json_kind_key(value: object) -> tuple[str, str]:
    """Return a key that tells JSON values apart by kind and value, where Python equality takes ``1 == 1.0 == True``."""
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
        case _:
            kind = type(value).__name__
    return kind, json.dumps(value)


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


def kind_forms(value: object) -> list[object]:
    """Return a member in every JSON kind: ``1`` gives ``1.0``, ``true``, ``"1"``, ``[1]``, ``{"a": 1}``, ``null``."""
    match value:
        case bool():
            number: Fraction = Fraction(int(value))
            text = json.dumps(value)
        case int() | float() | Fraction():
            number = Fraction(value)
            text = _number_text(number) if number.denominator == 1 else str(_json_value(number))
        case str():
            number = Fraction(1)
            text = value
            with suppress(ValueError, ZeroDivisionError):
                number = Fraction(value)
        case _:
            number = Fraction(1)
            text = json.dumps(_json_value(value), sort_keys=True)
    forms = [
        _json_value(number),
        _float(number),
        number != 0 if number in {0, 1} else True,
        text,
        [_json_value(value)],
        {"a": _json_value(value)},
        None,
    ]
    unique = {json_kind_key(_json_value(value)): _json_value(value)}
    for form in forms:
        unique.setdefault(json_kind_key(form), form)
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
        properties: dict[str, list[object]] = {}
        required: dict[str, None] = {}
        extra: list[object] = []
        fewest = 0
        for part in parts:
            if isinstance(part_properties := part.get("properties"), dict):
                for name, subschema in part_properties.items():
                    properties.setdefault(name, []).append(subschema)
            if isinstance(part_required := part.get("required"), list):
                required.update(dict.fromkeys(name for name in part_required if isinstance(name, str)))
            if "additionalProperties" in part:
                extra.append(part["additionalProperties"])
            if _is_number(least := part.get("minProperties")):
                fewest = max(fewest, int(least))
        built: dict[str, object] = {}
        for name in (*properties, *(name for name in required if name not in properties)):
            schemas = properties.get(name) or extra or [True]
            optional_too_deep = name not in required and depth >= DEEPEST_SEED // 2
            if optional_too_deep or (value := self.witness(schemas, depth + 1)) is _MISSING:
                if name in required:
                    return
                continue
            built[name] = value
        if (
            len(built) < fewest <= LONGEST_NEIGHBOUR
            and (value := self.witness(extra or [True], depth + 1)) is not _MISSING
        ):
            built.update((f"neighbour{index}", deepcopy(value)) for index in range(fewest - len(built)))
        yield built
        yield {name: value for name, value in built.items() if name in required}

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
            schemas = items or [True]
            if unique:
                values.extend(self.distinct(schemas, rest, depth + 1))
            elif (value := self.witness(schemas, depth + 1)) is not _MISSING:
                values.extend(deepcopy(value) for _ in range(rest))
        for matcher, count in contains:
            if 0 < count <= LONGEST_NEIGHBOUR and (value := self.witness([matcher, *items], depth + 1)) is not _MISSING:
                values = [*values[: max(len(values) - count, 0)], *(deepcopy(value) for _ in range(count))]
        yield values
        yield []

    @staticmethod
    def strings(parts: list[dict[str, object]]) -> Iterator[str]:
        least = max((int(value) for part in parts if _is_number(value := part.get("minLength"))), default=0)
        most = min((int(value) for part in parts if _is_number(value := part.get("maxLength"))), default=None)
        bases = [
            _FORMAT_SAMPLES[fmt]
            for part in parts
            if isinstance(fmt := part.get("format"), str) and fmt in _FORMAT_SAMPLES
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
        lows: list[Fraction] = []
        highs: list[Fraction] = []
        multiples: list[Fraction] = []
        for part in parts:
            for keyword, exclusive, into, step in (
                ("minimum", "exclusiveMinimum", lows, 1),
                ("maximum", "exclusiveMaximum", highs, -1),
            ):
                if _is_number(bound := part.get(keyword)):
                    into.append(Fraction(bound) + (Fraction(step, 1000) if part.get(exclusive) is True else 0))
                if _is_number(bound := part.get(exclusive)):
                    into.append(Fraction(bound) + Fraction(step, 1000))
            if _is_number(multiple := part.get("multipleOf")) and multiple > 0:
                multiples.append(Fraction(multiple))
        low = max(lows, default=None)
        high = min(highs, default=None)
        candidates = [Fraction(0), Fraction(1)]
        candidates.extend(bound for bound in (low, high) if bound is not None)
        if low is not None and high is not None:
            candidates.append((low + high) / 2)
        step = math.lcm(*(multiple.numerator for multiple in multiples)) if multiples else 1
        unit = Fraction(step, math.gcd(*(multiple.denominator for multiple in multiples))) if multiples else Fraction(1)
        if integral and unit.denominator != 1:
            unit = Fraction(unit.numerator)
        for candidate in candidates:
            for rounded in (math.ceil(candidate / unit) * unit, math.floor(candidate / unit) * unit):
                if (low is None or rounded >= low) and (high is None or rounded <= high):
                    yield _json_value(rounded)
        if not integral and not multiples:
            yield from (number for candidate in candidates if (number := _json_value(candidate)) is not None)
        yield from (0, 1)


@dataclass(frozen=True)
class _Site:
    """A schema object that applies to the value at a path of the seed."""

    schema: dict[str, object]
    path: JsonPath
    value: object


def _iter_sites(
    seeds: _Seeds, schema: object, value: object, path: JsonPath, seen: frozenset[str] = frozenset()
) -> Iterator[_Site]:
    """Yield every schema object that applies to a value of the seed, with the path of the value."""
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
            yield from _iter_array_sites(seeds, schema, value, path, seen)


def _iter_object_sites(
    seeds: _Seeds, schema: dict[str, object], value: dict[str, object], path: JsonPath, seen: frozenset[str]
) -> Iterator[_Site]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    for name, item in value.items():
        if name in properties:
            yield from _iter_sites(seeds, properties[name], item, (*path, name), seen)
        elif isinstance(additional := schema.get("additionalProperties"), dict):
            yield from _iter_sites(seeds, additional, item, (*path, name), seen)
    if isinstance(dependent := schema.get("dependentSchemas"), dict):
        for name, subschema in dependent.items():
            if name in value:
                yield from _iter_sites(seeds, subschema, value, path, seen)


def _iter_array_sites(
    seeds: _Seeds, schema: dict[str, object], value: list[object], path: JsonPath, seen: frozenset[str]
) -> Iterator[_Site]:
    prefix = schema.get("prefixItems")
    items = schema.get("items")
    if not isinstance(prefix, list):
        prefix, items = (items, schema.get("additionalItems")) if isinstance(items, list) else ([], items)
    for index, item in enumerate(value):
        item_schema = prefix[index] if index < len(prefix) else items
        yield from _iter_sites(seeds, item_schema, item, (*path, index), seen)
        yield from _iter_sites(seeds, schema.get("contains"), item, (*path, index), seen)


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


def _sized(value: object, size: int, filler: object) -> object:
    """Return a string, array or object of the size built from the seed's value: cut, or padded with its last part."""
    match value:
        case str():
            pad = value[-1:] or "a"
            return value[:size] + pad * max(size - len(value), 0)
        case list():
            pad = deepcopy(value[-1]) if value else filler
            return value[:size] + [deepcopy(pad) for _ in range(max(size - len(value), 0))]
        case dict():
            names = list(value)[:size]
            sized = {name: value[name] for name in names}
            index = 0
            while len(sized) < size:
                sized.setdefault(f"neighbour{index}", deepcopy(filler))
                index += 1
            return sized
        case _:
            return None


def _filler(seeds: _Seeds, schema: dict[str, object]) -> object:
    """Return the value an object neighbour gives the names it adds: one its additional properties accept."""
    if isinstance(additional := schema.get("additionalProperties"), dict):
        value = seeds.witness([additional])
        return "a" if value is _MISSING else value
    return "a"


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


def _size_values(seeds: _Seeds, site: _Site) -> Iterator[tuple[str, object]]:
    """Yield the seed's string, array or object one step either side of each length and count bound."""
    for keywords, kind in ((LENGTH_KEYWORDS, str), (ITEM_COUNT_KEYWORDS, list), (PROPERTY_COUNT_KEYWORDS, dict)):
        if not isinstance(site.value, kind):
            continue
        for keyword in keywords:
            if _is_number(size := site.schema.get(keyword)) and 0 <= size <= LONGEST_NEIGHBOUR:
                size = int(size)
                sizes = (size - 1, size) if keyword.startswith("min") else (size, size + 1)
                yield from (
                    (keyword, _sized(site.value, each, _filler(seeds, site.schema))) for each in sizes if each >= 0
                )


def _contains_values(seeds: _Seeds, site: _Site) -> Iterator[tuple[str, object]]:
    """Yield arrays of items the ``contains`` schema accepts, one step either side of its least and greatest count."""
    schema = site.schema
    if "contains" not in schema or not isinstance(site.value, list):
        return
    if (matching := seeds.witness([schema["contains"]])) is _MISSING:
        return
    least = int(value) if _is_number(value := schema.get("minContains")) else 1
    counts = [("minContains" if "minContains" in schema else "contains", count) for count in (least - 1, least)]
    if _is_number(most := schema.get("maxContains")):
        counts.extend(("maxContains", count) for count in (int(most), int(most) + 1))
    for keyword, count in counts:
        if 0 <= count <= LONGEST_NEIGHBOUR:
            yield keyword, [deepcopy(matching) for _ in range(count)]


def _tag_values(site: _Site) -> Iterator[tuple[str, object]]:
    """Yield the seed's object with its discriminator tag in every other JSON kind."""
    discriminator, value = site.schema.get("discriminator"), site.value
    if isinstance(discriminator, dict) and isinstance(value, dict):
        name = discriminator.get("propertyName")
        if isinstance(name, str) and name in value:
            yield from (("discriminator", {**value, name: form}) for form in kind_forms(value[name]))


def _site_values(seeds: _Seeds, site: _Site) -> Iterator[tuple[str, object]]:
    """Yield each keyword of a site with a value next to its boundary."""
    yield from _member_values(site.schema)
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
    for site in _iter_sites(seeds, seeds.root, seed, ()):
        for keyword, value in _site_values(seeds, site):
            key = (site.path, keyword, json_kind_key(value))
            if key in neighbours or json_kind_key(value) == json_kind_key(site.value):
                continue
            payload = _replaced(seed, site.path, value)
            if (valid := _agrees(agreement, payload)) is None:
                continue
            neighbours[key] = ValueNeighbour(keyword, site.path, value, payload, valid)
    ordered = sorted(neighbours.values(), key=lambda neighbour: neighbour.label)
    return seed, (ValueNeighbour(SEED_KEYWORD, (), seed, seed, valid=True), *ordered)


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
    """Return whether the generated model accepts or rejects a payload; any other exception fails the test."""
    with suppress(*runtime.rejection_exceptions):
        runtime.validate_python(payload)
        return "accepts"
    return "rejects"


def schema_verdict(neighbour: ValueNeighbour) -> str:
    """Return the verdict a model gives a neighbour where it decides like the suite's validator."""
    return "accepts" if neighbour.valid else "rejects"


def neighbour_is_asserted(
    neighbour: ValueNeighbour, backend: PayloadBackend, *, schema_validators: bool, default_agrees: bool
) -> bool:
    """Return whether a differing verdict fails the suite instead of going into the unasserted list of its backend.

    The keywords ``conformance.py`` asserts for a backend are asserted; the models of ``--generate-schema-validators``
    are asserted wherever the default Pydantic v2 model of the case decides like the validator, because the checks the
    option adds may close the default model's gaps but never open one.
    """
    return rejected_mutation_reason(neighbour.keyword, backend) is not None or (schema_validators and default_agrees)


def differing_line(neighbour: ValueNeighbour, verdict: str) -> str:
    """Return the line naming a neighbour the model decides otherwise than the validator."""
    return f"{neighbour.label}: {'valid' if neighbour.valid else 'invalid'}, model {verdict}"
