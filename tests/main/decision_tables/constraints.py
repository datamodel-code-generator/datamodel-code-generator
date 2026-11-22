"""The constraint applicability table: which keyword a generated model enforces on which declared type, by position.

A row is a keyword beside a declared type of ``TYPES`` (a JSON type with a format) that applies to it, one of the
keywords of its JSON kind (``Kind.keywords``; every value of a date or a UUID has one length, so a length keyword
beside them decides nothing and is no row), written at one of the ``SITES`` (an object property, a definition a
property references, the items of an array property) in one of the ``POSITIONS`` (beside the type, in a second
``allOf`` member after a ``$ref`` to the type, beside a ``$ref`` to the type, in a union member beside a boolean). A
keyword beside a type of another kind is no row: no ordinary schema writes ``maxItems`` beside a bare string (the
string-or-array unions that do are the ``extend`` family's).

The rows of one type, site and position share a document, each keyword the property ``f`` of an object of its own
with definitions of its own, so one generation reads every keyword of the type. A row is read on the instances of its
type (``Kind.instances``), values of the format that straddle each limit: the keyword's limit is the middle instance's
size (a string's length, an array's items, an object's keys), ``3`` for a number keyword and the type's pattern for
``pattern``, so the validator accepts some instances and rejects others wherever the keyword applies. Instances are
values of the format, so the format, which the validator does not assert, decides nothing.

The bound: 45 keywords of 14 types (3 of each of six string formats, the pattern of a date and of a UUID, 5 of two
integers and two numbers, 3 of an array, 2 of a map) x 3 sites x 4 positions, 540 rows, each generated for Pydantic v2
and msgspec.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from tests.main.shape_families.families import DIALECT, MSGSPEC, PYDANTIC_V2, REF
from tests.main.shape_families.verdict import Batch, Row

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from tests.main.shape_families.families import Schema

TABLE: Final = "constraints"
BACKENDS: Final = (PYDANTIC_V2, MSGSPEC)
"""The output model types that write constraints; TypedDict and standard dataclasses write none."""

STRING_KEYWORDS: Final = ("minLength", "maxLength", "pattern")
NUMBER_KEYWORDS: Final = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")
ARRAY_KEYWORDS: Final = ("minItems", "maxItems", "uniqueItems")
OBJECT_KEYWORDS: Final = ("minProperties", "maxProperties")
SIZE_KEYWORDS: Final = frozenset({"minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"})
NUMBER_LIMIT: Final = 3


@dataclass(frozen=True)
class Kind:
    """A declared type: its schema, its keywords, the instances that read them, its pattern and its size measure."""

    schema: Schema
    keywords: tuple[str, ...]
    instances: tuple[object, ...]
    pattern: str = "^a"
    size: int = NUMBER_LIMIT
    """The size of the middle instance, the limit of a size keyword (``SIZE_KEYWORDS``)."""


def _string(fmt: str | None, instances: tuple[str, ...], pattern: str) -> Kind:
    schema: Schema = {"type": "string"} if fmt is None else {"type": "string", "format": fmt}
    keywords = STRING_KEYWORDS if len({len(value) for value in instances}) > 1 else ("pattern",)
    return Kind(schema, keywords, instances, pattern, len(instances[len(instances) // 2]))


TYPES: Final[Mapping[str, Kind]] = {
    "string": _string(None, ("ba", "abcd", "abcdefgh"), "^a"),
    "date": _string("date", ("2020-01-01", "2021-06-15", "2022-12-31"), "^202[12]"),
    "date-time": _string(
        "date-time",
        ("2020-01-01T00:00:00Z", "2021-01-01T00:00:00+09:00", "2022-01-01T00:00:00.123456+09:00"),
        "^202[12]",
    ),
    "uri": _string("uri", ("http://a.io", "https://ab.io/x", "https://abc.example/abc"), "^https"),
    "uuid": _string(
        "uuid",
        (
            "00000000-0000-4000-8000-000000000000",
            "10000000-0000-4000-8000-000000000000",
            "20000000-0000-4000-8000-000000000000",
        ),
        "^[12]",
    ),
    "email": _string("email", ("a@b.io", "ab@cd.io", "abcd@efgh.io"), "^ab"),
    "ipv4": _string("ipv4", ("1.2.3.4", "10.20.30.40", "100.200.100.200"), "^1[0-9]"),
    "byte": _string("byte", ("YQ==", "YWJjZA==", "YWJjZGVmZ2g="), "^YW"),
    "integer": Kind({"type": "integer"}, NUMBER_KEYWORDS, (1, 3, 6)),
    "int64": Kind({"type": "integer", "format": "int64"}, NUMBER_KEYWORDS, (1, 3, 6)),
    "number": Kind({"type": "number"}, NUMBER_KEYWORDS, (1.5, 3, 6)),
    "double": Kind({"type": "number", "format": "double"}, NUMBER_KEYWORDS, (1.5, 3, 6)),
    "array": Kind(
        {"type": "array", "items": {"type": "string"}},
        ARRAY_KEYWORDS,
        ([], ["a"], ["a", "b"], ["a", "b", "c"], ["a", "a"]),
        size=2,
    ),
    "map": Kind(
        {"type": "object", "additionalProperties": {"type": "integer"}},
        OBJECT_KEYWORDS,
        ({}, {"a": 1}, {"a": 1, "b": 2}, {"a": 1, "b": 2, "c": 3}),
        size=2,
    ),
}
"""Every declared type of the table by name: a string in eight formats, two integers, two numbers, an array, a map."""


def limit(keyword: str, kind: Kind) -> object:
    """Return the value a keyword of a declared type takes: its size, its pattern, ``3`` or true."""
    if keyword in SIZE_KEYWORDS:
        return kind.size
    if keyword == "pattern":
        return kind.pattern
    return True if keyword == "uniqueItems" else NUMBER_LIMIT


def _direct(kind: Schema, keyword: Schema, definitions: Schema, name: str) -> Schema:
    del definitions, name
    return {**kind, **keyword}


def _all_of_member(kind: Schema, keyword: Schema, definitions: Schema, name: str) -> Schema:
    definitions[name] = kind
    return {"allOf": [{"$ref": REF + name}, keyword]}


def _ref_sibling(kind: Schema, keyword: Schema, definitions: Schema, name: str) -> Schema:
    definitions[name] = kind
    return {"$ref": REF + name, **keyword}


def _union_member(kind: Schema, keyword: Schema, definitions: Schema, name: str) -> Schema:
    del definitions, name
    return {"anyOf": [{**kind, **keyword}, {"type": "boolean"}]}


POSITIONS: Final[Mapping[str, Callable[[Schema, Schema, Schema, str], Schema]]] = {
    "direct": _direct,
    "all-of-member": _all_of_member,
    "ref-sibling": _ref_sibling,
    "union-member": _union_member,
}
"""Where the keyword stands relative to the declared type; the last two arguments name a definition to write."""
SITES: Final = ("property", "root", "item")


def batch(type_name: str, site: str, position: str) -> Batch:
    """Return the rows of one declared type at one site and position, each keyword the property ``f`` of its object."""
    kind = TYPES[type_name]
    definitions: Schema = {}
    rows = []
    for keyword in kind.keywords:
        name = keyword[0].upper() + keyword[1:]
        schema = POSITIONS[position](kind.schema, {keyword: limit(keyword, kind)}, definitions, f"Type{name}")
        if site == "root":
            definitions[f"Root{name}"] = schema
            schema = {"$ref": REF + f"Root{name}"}
        elif site == "item":
            schema = {"type": "array", "items": schema}
        definitions[f"Row{name}"] = {"type": "object", "properties": {"f": schema}}
        payloads = tuple({"f": [value] if site == "item" else value} for value in kind.instances)
        params = (("keyword", keyword), ("type", type_name), ("site", site), ("position", position))
        rows.append(Row(params, f"Row{name}", payloads))
    return Batch(TABLE, {"$schema": DIALECT, "$defs": definitions}, tuple(rows), REF, {"$defs": definitions})


DIMENSIONS: Final = (("type", tuple(TYPES)), ("site", SITES), ("position", tuple(POSITIONS)))
"""The dimensions of a batch, beside the keyword of each row and the backend."""


def batches() -> tuple[Batch, ...]:
    """Return every batch of the table, in the order of its dimensions."""
    return tuple(itertools.starmap(batch, itertools.product(*(values for _, values in DIMENSIONS))))
