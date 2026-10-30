"""What the concern modules of the JSON Schema parser read alike from schema objects, their dumps and JSON values.

A module-level helper of ``parser/jsonschema.py`` that the functions of two or more concern modules
(``parser/_<concern>.py``) use lives here, below them all, so that no concern module imports another or the public
module. None of them reads the parser. ``parser/jsonschema.py`` imports them too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, NamedTuple

from datamodel_code_generator._json_schema import schema_positions
from datamodel_code_generator.parser._intersection import intersect_keywords
from datamodel_code_generator.parser._null_facts import dump_null_sources, resolve_null_facts
from datamodel_code_generator.parser._schema_object import JsonSchemaObject, _type_null_sources
from datamodel_code_generator.types import DataType, UnionIntFloat

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from datamodel_code_generator.parser._intersection import KeywordIntersection


_MODELED_SCHEMA_MAPS = schema_positions("modeled", shapes=("map",))
"""The maps of named subschemas ``JsonSchemaObject`` models: ``properties`` and ``patternProperties``."""


def _merges_ref_siblings(obj: JsonSchemaObject) -> bool:
    """Return whether a definition reads the sibling keywords of its `$ref` with the schema it names."""
    return obj.has_ref_with_schema_keywords and not obj.is_ref_with_nullable_only


def _schema_dump(schema: JsonSchemaObject | bool) -> dict[str, Any] | bool:  # ruff: ignore[boolean-type-hint-positional-argument]
    """Return the keywords a schema object sets as a dict, or a boolean schema itself."""
    return schema.model_dump(exclude_unset=True, by_alias=True) if isinstance(schema, JsonSchemaObject) else schema


_JSON_VALUE_TYPES: Final = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
    (type(None), "null"),
)


def _get_json_value_type(value: object) -> str:
    """Return a JSON Schema primitive type name for a concrete value."""
    return next((name for kind, name in _JSON_VALUE_TYPES if isinstance(value, kind)), "")


def _constraint_values(obj: JsonSchemaObject, keys: Iterable[str], *, as_numbers: bool = False) -> dict[str, Any]:
    """Return the value of each of ``keys`` that a schema sets, in their order; ``as_numbers`` unwraps a number."""
    return {
        key: value.value if as_numbers and isinstance(value, UnionIntFloat) else value
        for key in keys
        if (value := getattr(obj, key, None)) is not None
    }


def _dump_parts_admit_null(parts: Sequence[Mapping[str, Any]]) -> bool:
    """Return whether the dumped parts of an intersection admit null (site ``all-of-parts``); the last is its own."""
    return bool(
        resolve_null_facts(
            dump_null_sources(parts[-1]),
            "all-of-parts",
            parts=[dump_null_sources(part) for part in parts if "type" in part],
        ).admits_null
    )


def _intersect_dumps(
    parts: Sequence[Mapping[str, Any]],
    row: dict[str, Any],
    *,
    ref_siblings_are_parts: bool = True,
    legacy_rows: Collection[str] = (),
) -> KeywordIntersection:
    """Return ``parser/_intersection.py:intersect_keywords`` of dumped schema objects, null by ``all-of-parts``."""
    return intersect_keywords(
        parts,
        row,
        extras_key=JsonSchemaObject.__extra_key__,
        parts_admit_null=_dump_parts_admit_null,
        ref_siblings_are_parts=ref_siblings_are_parts,
        legacy_rows=legacy_rows,
    )


def _is_object_only_type(type_: str | list[str] | None) -> bool:
    """Return whether a schema type permits only object values."""
    match type_:
        case None | "object" | ["object"]:
            return True
    return False


def _object_map_other_types(obj: JsonSchemaObject) -> list[str] | None:
    """Return the type array of an object map whose types other than object and null join the dict in a union."""
    match obj.type:
        case list() as types if "object" in types and not set(types) <= {"object", "null"}:
            return types
    return None


_ARRAY_ITEMS_CONSTRAINT_FIELDS = frozenset(schema_positions("modeled", "array"))


def _enum_without_positions(obj: JsonSchemaObject, excluded: Collection[int]) -> JsonSchemaObject:
    """Return a copy of an enum schema without the values at the ``excluded`` positions, with their x-enum entries.

    Each entry stays with its value, matched as ``parse_enum`` matches them.
    """
    skip_null = _enum_entries_skip_null(obj)
    entry_positions = (position for position, value in enumerate(obj.enum) if value is not None or not skip_null)
    dropped = {index for index, position in enumerate(entry_positions) if position in excluded}
    return obj.model_copy(
        update={
            "enum": [value for position, value in enumerate(obj.enum) if position not in excluded],
            **{
                field: [entry for index, entry in enumerate(entries) if index not in dropped]
                for field in _ENUM_ENTRY_FIELDS
                if (entries := getattr(obj, field))
            },
        }
    )


_ENUM_ENTRY_FIELDS = ("x_enum_varnames", "x_enum_names", "x_enum_descriptions")


def _enum_entries_skip_null(obj: JsonSchemaObject) -> bool:
    """Return whether the x-enum entries of a schema are matched to its enum values without null.

    The class of a string or nullable enum leaves out the null the enum lists, and its entries skip it too.
    """
    return resolve_null_facts(_type_null_sources(obj), "enum").rule == "enum-null-as-nullable"


_LIGHTWEIGHT_MAX_DEPTH: Final = 3
_LIGHTWEIGHT_MAX_UNION_ELEMENTS: Final = 5


class _AllOfItemGroups(NamedTuple):
    """The allOf members of one schema by the part of its lightweight type each one gives."""

    refs: list[JsonSchemaObject]
    ref_data_types: list[DataType]
    primitives: list[JsonSchemaObject]
    constraint_only: list[JsonSchemaObject]
    objects: list[JsonSchemaObject]


_NUMERIC_TYPE_DOMAINS = {
    "integer": frozenset({"integer"}),
    "number": frozenset({"integer", "number"}),
    "null": frozenset({"null"}),
}


def _numeric_type_domain(schema_type: str | list[str]) -> frozenset[str] | None:
    """Expand number to its numeric subtypes within the numeric/null domain."""
    if isinstance(schema_type, str):
        return _NUMERIC_TYPE_DOMAINS.get(schema_type)
    types = frozenset(schema_type)
    if not types.issubset(_NUMERIC_TYPE_DOMAINS):
        return None
    return types | _NUMERIC_TYPE_DOMAINS["integer"] if "number" in types else types
