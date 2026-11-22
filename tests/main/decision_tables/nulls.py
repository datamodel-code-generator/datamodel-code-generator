"""The null table: whether a generated model admits null where the schema does, by where the null comes from.

A row is a value of one of the ``KINDS`` (a string, an enum, a reference to an object model, an array, a map) whose
schema admits null through one of the ``SOURCES`` of its input, or through none, written at one of the ``POSITIONS`` (an
object property, a definition a property references, the items of an array property, the values of a map property),
required or not where it is a property. JSON Schema 2020-12 documents admit null through a ``type`` list, an ``anyOf``
member typed null or an ``enum`` with null beside such a list; OpenAPI 3.0 documents through ``nullable`` (beside the
type, or beside an ``allOf`` of one ``$ref``, the idiom 3.0 has for a nullable reference) and an ``enum`` with null
beside it. The validator reads OpenAPI's ``nullable`` as OpenAPI 3.0.3 does: it adds null to the ``type`` beside it,
and to nothing where no ``type`` stands beside it.

The rows of one input, kind and position share a document, each source and required state the property ``f`` of an
object of its own. A row is read on a null and a value of its kind at its position, and on the object without ``f``
where ``f`` itself holds the value.

The bound: 26 sources of the two inputs over the five kinds x 6 positions with required states, 156 rows, each
generated for Pydantic v2, msgspec, TypedDict and standard dataclasses.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Final

from tests.main.payload_validation.schema import _to_json_schema
from tests.main.shape_families.families import DATACLASSES, DIALECT, MSGSPEC, PYDANTIC_V2, TYPED_DICT
from tests.main.shape_families.verdict import Batch, Row

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from tests.main.shape_families.families import Schema

TABLE: Final = "nulls"
BACKENDS: Final = (PYDANTIC_V2, MSGSPEC, TYPED_DICT, DATACLASSES)
INPUTS: Final[Mapping[str, str]] = {"jsonschema": "#/$defs/", "openapi": "#/components/schemas/"}
"""Each input file type with the prefix its references take."""

ITEM: Final[Schema] = {"type": "object", "properties": {"x": {"type": "integer"}}}
KINDS: Final[Mapping[str, tuple[Callable[[str], Schema], object]]] = {
    "string": (lambda _ref: {"type": "string"}, "a"),
    "enum": (lambda _ref: {"type": "string", "enum": ["a", "b"]}, "a"),
    "ref": (lambda ref: {"$ref": ref + "Item"}, {"x": 1}),
    "array": (lambda _ref: {"type": "array", "items": {"type": "string"}}, ["a"]),
    "map": (lambda _ref: {"type": "object", "additionalProperties": {"type": "string"}}, {"k": "a"}),
}
"""Each kind of value: its schema without null, given the reference prefix, and a value of it."""


def _type_list(schema: Schema) -> Schema | None:
    return None if "$ref" in schema else {**schema, "type": [schema["type"], "null"]}


def _any_of(schema: Schema) -> Schema:
    return {"anyOf": [schema, {"type": "null"}]}


def _enum_null(schema: Schema) -> Schema | None:
    return (
        None if "enum" not in schema else {**schema, "type": [schema["type"], "null"], "enum": [*schema["enum"], None]}
    )


def _nullable(schema: Schema) -> Schema:
    return {"allOf": [schema], "nullable": True} if "$ref" in schema else {**schema, "nullable": True}


def _nullable_enum(schema: Schema) -> Schema | None:
    return None if "enum" not in schema else {**schema, "nullable": True, "enum": [*schema["enum"], None]}


def _no_null(schema: Schema) -> Schema:
    return schema


SOURCES: Final[Mapping[str, Mapping[str, Callable[[Schema], Schema | None]]]] = {
    "jsonschema": {"none": _no_null, "type-list": _type_list, "any-of": _any_of, "enum-null": _enum_null},
    "openapi": {"none": _no_null, "nullable": _nullable, "enum-null": _nullable_enum},
}
"""Each input's null sources: a function from a kind's schema to the schema that admits null, None where it has no
such spelling (a ``type`` list beside a ``$ref``, an ``enum`` beside a non-enum)."""
POSITIONS: Final = ("property", "root", "item", "value")
_BOOLS: Final = (False, True)


def _placed(schema: Schema, position: str, name: str, definitions: Schema, ref: str) -> Schema:
    """Return the property schema that holds ``schema`` at a position; a root becomes a definition named ``name``."""
    if position == "root":
        definitions[name] = schema
        return {"$ref": ref + name}
    if position == "item":
        return {"type": "array", "items": schema}
    return {"type": "object", "additionalProperties": schema} if position == "value" else schema


def _at(value: object, position: str) -> object:
    """Return a property value that holds ``value`` at a position."""
    if position == "item":
        return [value]
    return {"k": value} if position == "value" else value


def batch(input_file_type: str, kind: str, position: str) -> Batch:
    """Return the rows of one input, kind and position: each source and required state, ``f`` of an object."""
    ref = INPUTS[input_file_type]
    build, value = KINDS[kind]
    definitions: Schema = {"Item": ITEM}
    rows = []
    for source, spell in SOURCES[input_file_type].items():
        if (schema := spell(build(ref))) is None:
            continue
        for is_required in _BOOLS[: 2 if position in {"property", "root"} else 1]:
            name = "Row" + "".join(
                part.capitalize() for part in (*source.split("-"), "required" if is_required else "optional")
            )
            placed = _placed(schema, position, f"Root{name}", definitions, ref)
            definitions[name] = {
                "type": "object",
                "properties": {"f": placed},
                **({"required": ["f"]} if is_required else {}),
            }
            payloads: tuple[object, ...] = ({"f": _at(None, position)}, {"f": _at(value, position)})
            params = (
                ("input", input_file_type),
                ("source", source),
                ("kind", kind),
                ("position", position),
                ("required", is_required),
            )
            rows.append(Row(params, name, (*payloads, {}) if position in {"property", "root"} else payloads))
    if input_file_type == "jsonschema":
        return Batch(TABLE, {"$schema": DIALECT, "$defs": definitions}, tuple(rows), ref, {"$defs": definitions})
    document = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "paths": {},
        "components": {"schemas": definitions},
    }
    validated = {"components": {"schemas": {name: _to_json_schema(schema) for name, schema in definitions.items()}}}
    return Batch(TABLE, document, tuple(rows), ref, validated, input_file_type)


DIMENSIONS: Final = (("input", tuple(INPUTS)), ("kind", tuple(KINDS)), ("position", POSITIONS))
"""The dimensions of a batch, beside the source and required state of each row and the backend."""


def batches() -> tuple[Batch, ...]:
    """Return every batch of the table, in the order of its dimensions."""
    return tuple(itertools.starmap(batch, itertools.product(*(values for _, values in DIMENSIONS))))
