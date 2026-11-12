"""The lightweight types of the JSON Schema parser (L1): types read from a schema without building a model.

The primitive members of an allOf merge into one schema (the intersection of their constraints), an array becomes a
list or tuple of the types of its items, beside the other members of its type list, and a false schema contributes no
type. Each function takes the parser through ``LightweightTypesHost``, which ``JsonSchemaParser`` satisfies
structurally; the methods other parts of the parser and tests call stay on the parser as delegators to these functions
(``parser/jsonschema.py``), and these functions call them through the parser.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Final

from typing_extensions import Protocol

from datamodel_code_generator._constraint_applicability import NUMBER_KEYWORD_ORDER
from datamodel_code_generator._url_redaction import named_location
from datamodel_code_generator.imports import IMPORT_ANY
from datamodel_code_generator.parser import _value_keywords
from datamodel_code_generator.parser._intersection import (
    intersect_constraint,
    intersect_multiple_of,
    intersects_all_of_constraints,
)
from datamodel_code_generator.parser._origins import with_unique_items
from datamodel_code_generator.parser._schema_object import JsonSchemaObject
from datamodel_code_generator.parser._schema_reading import _numeric_type_domain
from datamodel_code_generator.parser._value_keywords import ValueKeywordsHost
from datamodel_code_generator.types import ANY, DataType, UnionIntFloat, build_union

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from datamodel_code_generator.enums import AllOfMergeMode

__all__ = [
    "LightweightTypesHost",
    "build_lightweight_array_type",
    "contains_false_schema",
    "is_false_schema_item",
    "is_list_with_any_item_type",
    "merge_primitive_schemas",
    "merge_primitive_schemas_for_allof",
    "merge_schema_constraints",
]

_ALL_OF_MEMBER_SUFFIX: Final = re.compile(r"/allOf/\d+$")
"""The end of an allOf member's location, which a warning about the site leaves out."""


class LightweightTypesHost(ValueKeywordsHost, Protocol):
    """The options and hooks of the parser that the lightweight types read."""

    @property
    def allof_merge_mode(self) -> AllOfMergeMode:
        """How the constraints of allOf members merge (``--allof-merge-mode``)."""

    def _conjoined_pattern(self, patterns: Sequence[str], where: str) -> str:
        """Return the conjoined pattern, warning about the patterns it leaves out."""

    @staticmethod
    def _first_typed_schema_dict(items: list[JsonSchemaObject]) -> dict[str, Any]:
        """Return the dump of the first typed member, its type narrowed to the numeric types the members share."""

    def _build_lightweight_item_type(
        self,
        item_schema: JsonSchemaObject | bool,  # noqa: FBT001
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
    ) -> DataType | None:
        """Return the lightweight type of an array item schema."""

    def _get_array_union_non_array_types(self, obj: JsonSchemaObject) -> tuple[str, ...]:
        """Return the members of a type list beside ``array`` other than null."""

    def _get_format_data_type(self, obj: JsonSchemaObject, type_: str) -> DataType:
        """Map a type and the format of ``obj`` without constraints."""


def merge_primitive_schemas(parser: LightweightTypesHost, items: list[JsonSchemaObject]) -> JsonSchemaObject:
    """Merge multiple primitive schemas by computing the intersection of their constraints."""
    if len(items) == 1:
        return items[0]

    base_dict = type(parser)._first_typed_schema_dict(items)  # noqa: SLF001
    parser._merge_schema_constraints(base_dict, items, intersect=True)  # noqa: SLF001

    return parser._schema_object(base_dict)  # noqa: SLF001


def merge_primitive_schemas_for_allof(
    parser: LightweightTypesHost, items: list[JsonSchemaObject]
) -> JsonSchemaObject | None:
    """Merge primitive schemas for allOf, respecting allof_merge_mode setting."""
    if len(items) == 1:
        return items[0]  # pragma: no cover

    formats = {item.format for item in items if item.format}
    if len(formats) > 1:
        return None

    if intersects_all_of_constraints(parser.allof_merge_mode.value):
        base_dict = merge_primitive_schemas(parser, items).model_dump(exclude_unset=True, by_alias=True)
    else:
        base_dict = type(parser)._first_typed_schema_dict(items)  # noqa: SLF001
        parser._merge_schema_constraints(base_dict, items, intersect=False)  # noqa: SLF001
    if formats:
        base_dict["format"] = formats.pop()
    return parser._schema_object(base_dict)  # noqa: SLF001


def _schema_constraint_value(parser: LightweightTypesHost, item: JsonSchemaObject, field: str) -> Any:  # noqa: ARG001
    return value if (value := getattr(item, field, None)) is not None else item.extras.get(field)


def _intersect_constraint(parser: LightweightTypesHost, field: str, val1: Any, val2: Any) -> Any:  # noqa: ARG001
    """Compute the intersection of two constraint values; ``multipleOf`` takes their least common multiple."""
    if field == "multipleOf":
        with suppress(TypeError, ValueError, ZeroDivisionError):
            return intersect_multiple_of(val1, val2)
        return val1  # pragma: no cover
    v1 = val1.value if isinstance(val1, UnionIntFloat) else val1
    v2 = val2.value if isinstance(val2, UnionIntFloat) else val2
    return val1 if intersect_constraint(field, v1, v2) is v1 else val2


def merge_schema_constraints(
    parser: LightweightTypesHost,
    base_dict: dict[str, Any],
    items: list[JsonSchemaObject],
    *,
    intersect: bool,
) -> None:
    patterns: list[str] | None = None
    for item in items:
        for field in JsonSchemaObject.__constraint_fields__:
            if (value := _schema_constraint_value(parser, item, field)) is None:
                continue
            if intersect and field == "pattern":
                if patterns is None:
                    patterns = [] if (base_pattern := base_dict.get(field)) is None else [base_pattern]
                patterns.append(value)
                continue
            if intersect and field in base_dict and base_dict[field] is not None:
                base_dict[field] = _intersect_constraint(parser, field, base_dict[field], value)
            else:
                base_dict[field] = value
    if patterns:
        base_dict["pattern"] = parser._conjoined_pattern(  # noqa: SLF001
            patterns,
            _ALL_OF_MEMBER_SUFFIX.sub(
                "",
                named_location(
                    next((item.schema_location for item in items if item.schema_location), None),
                    "allOf",
                ),
            ),
        )
    if (
        base_dict.get("type") in ("null", ["null"])
        and all(not item.type or _numeric_type_domain(item.type) is not None for item in items)
        and any("integer" in (_numeric_type_domain(item.type or "") or ()) for item in items)
    ):
        for field in NUMBER_KEYWORD_ORDER:
            base_dict.pop(field, None)


def build_lightweight_array_type(  # noqa: PLR0913, PLR0917
    parser: LightweightTypesHost,
    schema: JsonSchemaObject,
    depth: int,
    visited: frozenset[int],
    max_depth: int,
    max_union_elements: int,
) -> DataType:
    """Build a lightweight list type from array item schemas, in a union with the other members of a type list.

    The members beside ``array`` and null are their types and formats without constraints, ordered as the field of
    the schema orders them (``legacy-non-array-first``), and the union admits null where the field does.
    """
    item_schemas, _, _, tuple_item_count = _value_keywords.get_array_item_schemas(
        parser,
        schema,
        include_true_tail_schema=True,
        force_prefix_items=True,
    )
    is_homogeneous_tuple = tuple_item_count is not None
    item_types = (
        []
        if is_homogeneous_tuple and tuple_item_count == 0
        else [
            item_type
            for item_schema in item_schemas
            if (
                item_type := parser._build_lightweight_item_type(  # noqa: SLF001
                    item_schema, depth, visited, max_depth, max_union_elements
                )
            )
            is not None
        ]
    )
    if not item_types and not (is_homogeneous_tuple and tuple_item_count == 0):
        item_types = [DataType(type=ANY, import_=IMPORT_ANY)]
    array_type = with_unique_items(
        parser.data_type(
            data_types=item_types,
            is_list=not is_homogeneous_tuple,
            is_tuple=is_homogeneous_tuple,
            tuple_item_count=tuple_item_count if is_homogeneous_tuple else None,
        ),
        schema.uniqueItems,
    )
    other_types = [
        parser._get_format_data_type(schema, type_)  # noqa: SLF001
        for type_ in parser._get_array_union_non_array_types(schema)  # noqa: SLF001
    ]
    return build_union(
        parser.data_type,
        [array_type, *other_types],
        "legacy-non-array-first",
        nullable=bool(parser._null_facts(schema, "type-union").admits_null),  # noqa: SLF001
        roles=("member", *("non-array",) * len(other_types)),
    )


def is_false_schema_item(parser: LightweightTypesHost, item: JsonSchemaObject | bool) -> bool:  # noqa: ARG001, FBT001
    return item is False or (isinstance(item, JsonSchemaObject) and item.is_boolean_schema_false)


def contains_false_schema(parser: LightweightTypesHost, items: Iterable[JsonSchemaObject | bool]) -> bool:
    return any(is_false_schema_item(parser, item) for item in items)


def is_list_with_any_item_type(parser: LightweightTypesHost, data_type: DataType | None) -> bool:  # noqa: ARG001
    """Return True when data_type represents List[Any] (including nested lists)."""
    if not data_type:  # pragma: no cover
        return False

    candidate = data_type
    if candidate._wraps_one_list:  # noqa: SLF001
        candidate = candidate.data_types[0]

    if not candidate.is_list or len(candidate.data_types) != 1:
        return False

    item_type = candidate.data_types[0]
    while len(item_type.data_types) == 1:
        inner = item_type.data_types[0]
        if item_type.is_list or item_type._wraps_one_list:  # noqa: SLF001
            item_type = inner
        else:
            break
    return item_type.type == ANY
