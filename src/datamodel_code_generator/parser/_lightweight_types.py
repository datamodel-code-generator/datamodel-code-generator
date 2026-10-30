"""The lightweight types of the JSON Schema parser (L1): types read from a schema without building a model.

The primitive members of an allOf merge into one schema (the intersection of their constraints), an array becomes a
list or tuple of the types of its items, and a false schema contributes no type. Each function takes the parser
through ``LightweightTypesHost``, which ``JsonSchemaParser`` satisfies structurally; the methods other parts of the
parser and tests call stay on the parser as delegators to these functions (``parser/jsonschema.py``), and these
functions call them through the parser.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Final

from typing_extensions import Protocol

from datamodel_code_generator import AllOfMergeMode
from datamodel_code_generator._constraint_applicability import NUMBER_KEYWORD_ORDER
from datamodel_code_generator.imports import IMPORT_ANY
from datamodel_code_generator.parser._intersection import (
    intersect_constraint,
    intersect_multiple_of,
)
from datamodel_code_generator.parser._schema_object import JsonSchemaObject
from datamodel_code_generator.types import ANY, DataType, UnionIntFloat

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from datamodel_code_generator.parser._schema_graph import _TargetNode

__all__ = [
    "LightweightTypesHost",
    "build_lightweight_array_type",
    "contains_false_schema",
    "is_false_schema_item",
    "is_list_with_any_item_type",
    "merge_primitive_schemas",
    "merge_primitive_schemas_for_allof",
    "merge_schema_constraints",
    "unvisited_ref_target",
]

_NUMERIC_TYPE_DOMAINS = {
    "integer": frozenset({"integer"}),
    "number": frozenset({"integer", "number"}),
    "null": frozenset({"null"}),
}
_ALL_OF_MEMBER_SUFFIX: Final = re.compile(r"/allOf/\d+$")
"""The end of an allOf member's location, which a warning about the site leaves out."""


class LightweightTypesHost(Protocol):
    """The options and hooks of the parser that the lightweight types read."""

    @property
    def allof_merge_mode(self) -> AllOfMergeMode:
        """How the constraints of allOf members merge (``--allof-merge-mode``)."""

    @property
    def data_type(self) -> type[DataType]:
        """The data type class of the output."""

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw or dumped schema as the parser's schema object type."""

    def _conjoined_pattern(self, patterns: Sequence[str], where: str) -> str:
        """Return the conjoined pattern, warning about the patterns it leaves out."""

    @staticmethod
    def _first_typed_schema_dict(items: list[JsonSchemaObject]) -> dict[str, Any]:
        """Return the dump of the first typed member, its type narrowed to the numeric types the members share."""

    def _merge_primitive_schemas(self, items: list[JsonSchemaObject]) -> JsonSchemaObject:
        """Merge primitive schemas into the intersection of their constraints."""

    def _merge_schema_constraints(
        self,
        base_dict: dict[str, Any],
        items: list[JsonSchemaObject],
        *,
        intersect: bool,
    ) -> None:
        """Merge the constraints of ``items`` into ``base_dict``, intersecting them when ``intersect`` is set."""

    def _get_array_item_schemas(
        self,
        obj: JsonSchemaObject,
        *,
        include_true_tail_schema: bool = False,
        force_prefix_items: bool = False,
    ) -> tuple[list[JsonSchemaObject | bool], bool, bool, int | None]:
        """Return item schemas plus tuple/constraint flags and homogeneous tuple length."""

    def _build_lightweight_item_type(
        self,
        item_schema: JsonSchemaObject | bool,  # noqa: FBT001
        depth: int,
        visited: frozenset[int],
        max_depth: int,
        max_union_elements: int,
    ) -> DataType | None:
        """Return the lightweight type of an array item schema."""

    def _is_false_schema_item(self, item: JsonSchemaObject | bool) -> bool:  # noqa: FBT001
        """Return whether ``item`` is a false schema."""

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names."""

    def _key_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node at a reference key, or the key when the graph does not locate its value."""

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Load the schema object a reference names."""


def _numeric_type_domain(schema_type: str | list[str]) -> frozenset[str] | None:
    """Expand number to its numeric subtypes within the numeric/null domain."""
    if isinstance(schema_type, str):
        return _NUMERIC_TYPE_DOMAINS.get(schema_type)
    types = frozenset(schema_type)
    if not types.issubset(_NUMERIC_TYPE_DOMAINS):
        return None
    return types | _NUMERIC_TYPE_DOMAINS["integer"] if "number" in types else types


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

    if parser.allof_merge_mode != AllOfMergeMode.NoMerge:
        base_dict = parser._merge_primitive_schemas(items).model_dump(exclude_unset=True, by_alias=True)  # noqa: SLF001
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
                "", next((str(item.schema_location) for item in items if item.schema_location), "allOf")
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
    """Build a lightweight list type from array item schemas."""
    item_schemas, _, _, tuple_item_count = parser._get_array_item_schemas(  # noqa: SLF001
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
    return parser.data_type(
        data_types=item_types,
        is_list=not is_homogeneous_tuple,
        is_tuple=is_homogeneous_tuple,
        tuple_item_count=tuple_item_count if is_homogeneous_tuple else None,
    )


def is_false_schema_item(parser: LightweightTypesHost, item: JsonSchemaObject | bool) -> bool:  # noqa: ARG001, FBT001
    return item is False or (isinstance(item, JsonSchemaObject) and item.is_boolean_schema_false)


def contains_false_schema(parser: LightweightTypesHost, items: Iterable[JsonSchemaObject | bool]) -> bool:
    return any(parser._is_false_schema_item(item) for item in items)  # noqa: SLF001


def unvisited_ref_target(
    parser: LightweightTypesHost, ref: str, visited_refs: frozenset[_TargetNode]
) -> tuple[JsonSchemaObject, frozenset[_TargetNode]] | None:
    """Return the schema ``ref`` names with ``visited_refs`` and its node, or None when its node was visited."""
    if (node := parser._key_node(parser._ref_path(ref))) in visited_refs:  # noqa: SLF001
        return None
    return parser._load_ref_schema_object(ref), visited_refs | {node}  # noqa: SLF001


def is_list_with_any_item_type(parser: LightweightTypesHost, data_type: DataType | None) -> bool:  # noqa: ARG001
    """Return True when data_type represents List[Any] (including nested lists)."""
    if not data_type:  # pragma: no cover
        return False

    candidate = data_type
    if not candidate.is_list and len(candidate.data_types) == 1 and candidate.data_types[0].is_list:
        candidate = candidate.data_types[0]

    if not candidate.is_list or len(candidate.data_types) != 1:
        return False

    item_type = candidate.data_types[0]
    while len(item_type.data_types) == 1:
        inner = item_type.data_types[0]
        if (not item_type.is_list and inner.is_list) or item_type.is_list:
            item_type = inner
        else:
            break
    return item_type.type == ANY
