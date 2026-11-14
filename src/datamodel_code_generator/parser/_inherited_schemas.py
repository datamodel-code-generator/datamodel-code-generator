"""The inherited schema reading of the JSON Schema parser (L1): allOf parents, their properties and fields.

It reads the raw allOf inheritance graph (parents, their linearization through ``_mro.py`` and the properties and
required names a schema inherits), merges the properties a schema overrides with the inherited ones
(``--allof-merge-mode``) and builds the fields a model takes from its parents.

Each function takes the parser through ``InheritedSchemasHost``, which ``JsonSchemaParser`` satisfies structurally. The
methods that tests call or that other parts of the parser call stay on the parser as delegators to these functions
(``parser/jsonschema.py``), and these functions call them through the parser. ``_get_inherited_field_type``, a frame a
warning names, and the context manager ``_inherited_ref_context`` stay parser methods.
"""

from __future__ import annotations

import operator
from collections import Counter
from functools import partial, reduce
from itertools import islice
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, cast

from typing_extensions import Protocol, Sentinel

from datamodel_code_generator import AllOfMergeMode
from datamodel_code_generator._constraint_applicability import (
    ITEM_COUNT_KEYWORDS,
    NUMBER_KEYWORDS,
    PROPERTY_COUNT_KEYWORDS,
    STRING_KEYWORDS,
    keyword_kinds,
)
from datamodel_code_generator._copy_on_change import unchanged
from datamodel_code_generator._json_schema import (
    COMBINATOR_POSITIONS,
    MODELED_SCHEMA_MAPS,
    SCHEMA_POSITIONS,
    admitted_types,
    non_null_types,
    schema_positions,
)
from datamodel_code_generator._mro import linearize, sort_descendants_first
from datamodel_code_generator._url_redaction import named_location
from datamodel_code_generator._warning_control import quiet
from datamodel_code_generator.model import DataModel, DataModelFieldBase
from datamodel_code_generator.model.base import _field_source_name, get_inherited_fields
from datamodel_code_generator.parser import _rw_split, _schema_refs, _value_keywords
from datamodel_code_generator.parser._all_of_values import holds_union_member
from datamodel_code_generator.parser._intersection import (
    INHERITED_SCHEMA_KEYWORDS,
    POSITIONAL_SCHEMA_KEYWORDS,
    all_of_member_row,
    declared_types,
    inherited_override_row,
    inherited_positional_tail,
    json_values_equal,
    merge_inherited_keyword,
    merge_inherited_value,
)
from datamodel_code_generator.parser._null_facts import dump_null_sources, resolve_null_facts
from datamodel_code_generator.parser._origins import with_field_origin
from datamodel_code_generator.parser._rw_split import RwSplitHost
from datamodel_code_generator.parser._schema_documents import current_document
from datamodel_code_generator.parser._schema_object import (
    _ANNOTATION_KEYWORDS,
    _MODELED_POSITIONS,
    _REF_KEYWORDS,
    JsonSchemaObject,
    _dump_location,
    _dump_without_keywords,
    _located,
    _schema_node,
)
from datamodel_code_generator.parser._schema_reading import (
    _ARRAY_ITEMS_CONSTRAINT_FIELDS,
    _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS,
    _RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY,
    _declares_key_keywords,
    _get_json_value_type,
    _intersect_dumps,
    _intersect_parts,
    _schema_dump,
    _without_contains_keywords,
)
from datamodel_code_generator.parser._schema_refs import mark_conjunction_parts
from datamodel_code_generator.parser._value_keywords import get_effective_array_length
from datamodel_code_generator.parser.base import (
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _get_inherited_type_modifiers,
)
from datamodel_code_generator.python_literal import stable_repr
from datamodel_code_generator.reference import (
    Reference,
    _is_special_path,
    context_variable,
    get_inferred_module_name,
)
from datamodel_code_generator.types import DataType, untyped_value_data_type

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from typing_extensions import Unpack

    from datamodel_code_generator._user_keys import ClassScope
    from datamodel_code_generator.parser._field_rows import (
        FieldAliasRow,
        FieldFacts,
        FieldRow,
    )
    from datamodel_code_generator.parser._schema_graph import _TargetNode
    from datamodel_code_generator.reference import ModelType, SchemaLocation

__all__ = [
    "InheritedParentRow",
    "InheritedSchemasHost",
    "ModelDeclarations",
    "all_of_members",
    "build_missing_required_field",
    "checked_declarations",
    "drop_incompatible_inherited_constraints",
    "get_allof_parent_references",
    "get_deferred_inherited_parse_object",
    "get_deferred_inherited_property_names",
    "get_flattenable_inherited_constraint_items",
    "get_generated_base_models",
    "get_inherited_constraint_fields",
    "get_inherited_distributed_fields",
    "get_inherited_field",
    "get_inherited_field_compatible_types",
    "get_inherited_field_map",
    "get_inherited_field_owner_reference",
    "get_inherited_field_schema",
    "get_inherited_override_shape",
    "get_inherited_property_map",
    "get_inherited_property_order",
    "get_inherited_required_names",
    "get_inherited_schema_ancestor_paths",
    "get_inherited_schema_types",
    "get_inherited_type_shape",
    "get_inline_property_names",
    "get_inline_required_names",
    "get_nested_inherited_active",
    "has_inherited_constraints",
    "inferred_module_name",
    "inherited_node",
    "inherited_override_schema",
    "inherited_schema_parents",
    "inline_all_of_parts",
    "is_inherited_type_narrowing",
    "is_partial_inherited_nested_value",
    "is_partial_inherited_property",
    "is_unconstrained_inherited_schema",
    "iter_allof_refs",
    "iter_inherited_schema_objects",
    "linearize_inherited_schema_refs",
    "load_inherited_schema_object",
    "mark_inherited_materialized_type_shapes",
    "mark_partial_inherited_fields",
    "merge_inherited_type_shape_dict",
    "merge_inherited_union_branch",
    "merge_properties_with_parent_constraints",
    "model_declarations",
    "normalize_inherited_constraint_compositions",
    "normalize_inherited_nested_schema_value",
    "override_row",
    "parse_fields_beside_parents",
    "parse_inherited_schema_fields",
    "preserve_inherited_materialized_type_shape",
    "property_conjunctions",
    "remove_inherited_distributed_shape",
    "remove_unconstrained_compositions",
    "required_names_at",
    "resolve_inherited_parent_property",
    "sanitize_untyped_boolean_inherited_property",
    "select_inherited_distributed_shape",
    "sort_inherited_base_classes_for_mro",
    "union_model_properties",
    "with_conjoined_properties",
]

_INHERITED_TYPE_SHAPE_FIELDS = frozenset({
    *_MODELED_POSITIONS,
    "$dynamicRef",
    "$recursiveRef",
    "$ref",
    "customBasePath",
    "customTypePath",
    "discriminator",
    "required",
    "type",
})
_INHERITED_CONTAINER_TYPE_FIELDS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"array"}), _ARRAY_ITEMS_CONSTRAINT_FIELDS),
    (frozenset({"object"}), frozenset({*schema_positions("modeled", "object"), "required"})),
)
_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY = "_inherited_materialized_type_shape"
_INHERITED_OVERRIDE_KEY = "_inherited_override"
_READ_THROUGH_KEY = "_all_of_read_through"
"""Marks a member ``all_of_members`` reads in place of a ``$ref``, or within one, with the locations read through."""
ModelDeclarations: TypeAlias = "tuple[dict[str, JsonSchemaObject | None], frozenset[str]]"
InheritedParentRow: TypeAlias = Literal["class-bases", "legacy-document-order"]
"""The order the allOf parents of an inherited schema are read in (``inherited_schema_parents``)."""
"""The property declarations of a model, None for one it merges, and the names it requires (``model_declarations``)."""
_CHECK_KEYWORDS: Final = NUMBER_KEYWORDS | STRING_KEYWORDS | ITEM_COUNT_KEYWORDS
"""The keywords that narrow the values of the type a schema is written as, never that type: bounds and pattern."""
_DOCUMENTATION_KEYWORDS: Final = frozenset({"$comment", "deprecated", "description", "example", "examples", "title"})
"""The annotations of a schema that give no value and do not split request and response models."""
_MODEL_BUILDING_KEYWORDS: Final = frozenset({*SCHEMA_POSITIONS, *_REF_KEYWORDS, "enum"})
"""The keywords a declaration may build a model of its own with: its subschemas, references and ``enum``."""
_NO_INHERITED_SCHEMA_MERGE = Sentinel("_NO_INHERITED_SCHEMA_MERGE")


def inline_all_of_parts(
    parser: InheritedSchemasHost, schema: JsonSchemaObject, *, members_first: bool = False
) -> Iterator[JsonSchemaObject]:
    """Yield ``schema`` and its inline (not ``$ref``) allOf members, recursively; ``members_first`` yields it last.

    The members are read as ``all_of_members`` reads them, so the keywords beside a ``$ref`` member are one where they
    are parts of it.
    """
    if not members_first:
        yield schema
    for item in all_of_members(parser, schema):
        if isinstance(item, JsonSchemaObject) and not item.ref:
            yield from inline_all_of_parts(parser, item, members_first=members_first)
    if members_first:
        yield schema


def _nested_schema_objects(value: object) -> Iterator[JsonSchemaObject]:
    """Yield the schema objects a keyword value holds: the value itself, or the members of its list or map."""
    match value:
        case JsonSchemaObject():
            yield value
        case list() | dict():
            yield from (
                item
                for item in (value.values() if isinstance(value, dict) else value)
                if isinstance(item, JsonSchemaObject)
            )


def _dump_drops_inherited_null(dump: Mapping[str, Any]) -> bool:
    """Return whether a dumped override drops the inherited null (row ``inherited-typed-without-null``)."""
    return resolve_null_facts(dump_null_sources(dump), "inherited-child").rule == "inherited-typed-without-null"


class InheritedSchemasHost(RwSplitHost, Protocol):
    """The options, caches and hooks of the parser that the inherited schema reading reads."""

    @property
    def field_name_model_type(self) -> ModelType:
        """The model type that field names are validated for."""

    @property
    def results(self) -> list[DataModel]:
        """The parsed models."""

    @property
    def strict_dotted_module_names(self) -> bool:
        """Whether dotted schema names keep their dots in module names (``--strict-dotted-module-names``)."""

    @property
    def _inherited_parent_row(self) -> InheritedParentRow:
        """The order the allOf parents of an inherited schema are read in (``inherited_schema_parents``)."""

    def get_field_extras(self, obj: JsonSchemaObject) -> dict[str, Any]:
        """Extract extra field metadata from a JSON Schema object."""

    def parse_object_fields(
        self,
        obj: JsonSchemaObject,
        path: list[str],
        module_name: str | None = None,
        class_name: str | None = None,
        *,
        output_names: tuple[str, ...] = (),
    ) -> list[DataModelFieldBase]:
        """Parse object properties into a list of data model fields."""

    def _build_field(
        self,
        row: FieldRow,
        /,
        data_type: DataType,
        *,
        source_name: str | None = None,
        scope: ClassScope | None = None,
        **facts: Unpack[FieldFacts],
    ) -> DataModelFieldBase:
        """Build a field of a model the parser writes, with the configured options its ``row`` takes."""

    def _get_inherited_field_type(
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[_TargetNode] | None = None
    ) -> DataType | None:
        """Get the data type for an inherited property from parent schemas."""

    def _take_field_aliases(  # noqa: PLR0913
        self,
        field: DataModelFieldBase,
        name: str,
        alias: str | list[str] | None,
        *,
        source_name: str,
        scope: ClassScope,
        row: FieldAliasRow,
        inherits: bool | None = None,
    ) -> None:
        """Give a field built earlier the name ``name`` and the aliases ``_field_aliases`` derives, by ``row``."""


def get_inherited_override_shape(
    parser: InheritedSchemasHost,  # noqa: ARG001
    schema: JsonSchemaObject,
) -> dict[str, Any]:
    """Return only keywords that can change an inherited property's type shape."""
    return _dump_without_keywords(
        schema,
        {
            *JsonSchemaObject.__constraint_fields__,
            *_INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS,
            *PROPERTY_COUNT_KEYWORDS,
            *_ANNOTATION_KEYWORDS,
            "nullable",
        },
    )


def get_inherited_type_shape(
    parser: InheritedSchemasHost,
    schema: JsonSchemaObject,
    canonical_ref: Callable[[str], str],
) -> dict[str, Any]:
    """Return structural schema keywords without inherited value restrictions; a tuple keeps its length.

    The length is that of explicit equal ``minItems`` and ``maxItems``, else that of the tuple
    ``--use-tuple-for-closed-arrays`` writes, and unless ``legacy-type-shape-length-from-max-items`` is kept also the
    bounds the effective length bounds fix it by (``parser/_value_keywords.py:get_effective_array_length``: a closed
    tuple whose tail or a position is ``false``).
    ``canonical_ref`` reads the ``$ref`` values of ``schema`` in the document that defines it.
    """
    schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
    shape = {key: value for key, value in schema_dict.items() if key in _INHERITED_TYPE_SHAPE_FIELDS}
    positional_items = schema.prefixItems or (schema.items if isinstance(schema.items, list) else None)
    if positional_items and schema.minItems == schema.maxItems == len(positional_items):
        shape["minItems"] = schema.minItems
        shape["maxItems"] = schema.maxItems
    elif (
        parser.use_tuple_for_closed_arrays
        and (fixed_items := _value_keywords.get_fixed_positional_items(parser, schema, canonical_ref)) is not None
    ):
        shape["minItems"] = shape["maxItems"] = len(fixed_items)
    elif (
        positional_items
        and "legacy-type-shape-length-from-max-items" not in parser._legacy_intersection  # noqa: SLF001
        and get_effective_array_length(type(parser), schema) == len(positional_items)
    ):
        shape["minItems"] = schema.minItems
        shape["maxItems"] = schema.maxItems
    return shape


def get_inherited_schema_types(
    parser: InheritedSchemasHost,
    schema: JsonSchemaObject,
    parent_ref: str,
    active: frozenset[_TargetNode] = frozenset(),
) -> frozenset[str]:
    """Infer JSON instance types accepted by an inherited schema."""
    schema, parent_ref, active = resolve_inherited_parent_property(parser, schema, parent_ref, active)
    nullable_types = frozenset(("null",)) if parser._null_facts(schema, "inherited-types").admits_null else frozenset()  # noqa: SLF001
    if direct_types := frozenset(declared_types(schema.type)):
        return direct_types | nullable_types

    structural_types = (
        frozenset(("array",))
        if schema.is_array
        else frozenset(("object",))
        if (schema.is_object or schema.additionalProperties is not None or _declares_key_keywords(schema))
        else frozenset()
    )
    if structural_types:
        return structural_types | nullable_types

    values = schema.enum
    if "const" in schema.extras:
        values = [schema.extras["const"]]
    if values:
        value_types = {_get_json_value_type(value) for value in values}
        value_types.discard("")
        if value_types:
            return frozenset(value_types) | nullable_types

    combined_types: set[str] = set()
    for item in (*schema.anyOf, *schema.oneOf):
        if isinstance(item, JsonSchemaObject):
            combined_types.update(get_inherited_schema_types(parser, item, parent_ref, active))
    if combined_types:
        return frozenset(combined_types) | nullable_types

    all_of_types: frozenset[str] | None = None
    for item in schema.allOf:
        if not isinstance(item, JsonSchemaObject):
            continue
        if not (item_types := get_inherited_schema_types(parser, item, parent_ref, active)):
            continue
        all_of_types = item_types if all_of_types is None else all_of_types & item_types
    return (all_of_types or frozenset()) | nullable_types


def get_inherited_constraint_fields(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> frozenset[str]:  # noqa: ARG001
    """Return top-level validation constraints that need type-compatible placement."""
    fields = {field for _, fields in keyword_kinds() for field in fields if field in schema.model_fields_set}
    if schema.extras.keys() & _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS:
        fields.add(JsonSchemaObject.__extra_key__)
    return frozenset(fields)


def get_inherited_distributed_fields(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> frozenset[str]:
    """Return type-specific keywords that must follow compatible union branches."""
    fields = set(get_inherited_constraint_fields(parser, schema))
    for _, container_fields in _INHERITED_CONTAINER_TYPE_FIELDS:
        fields.update(container_fields & schema.model_fields_set)
    return frozenset(fields)


def get_inherited_field_compatible_types(parser: InheritedSchemasHost, field: str) -> frozenset[str]:  # noqa: ARG001
    """Return the JSON instance types to which one schema keyword applies."""
    if field == JsonSchemaObject.__extra_key__:
        return frozenset({"array"})
    return next(
        (
            compatible_types
            for compatible_types, fields in (
                *keyword_kinds(),
                *_INHERITED_CONTAINER_TYPE_FIELDS,
            )
            if field in fields
        ),
        frozenset(),
    )


def select_inherited_distributed_shape(
    parser: InheritedSchemasHost,
    child: dict[str, Any],
    distributed_fields: frozenset[str],
    parent_types: frozenset[str],
) -> dict[str, Any]:
    """Select child keywords that apply to one inherited JSON type branch."""
    result: dict[str, Any] = {}
    for field, value in child.items():
        if field not in distributed_fields:
            continue
        compatible_types = get_inherited_field_compatible_types(parser, field)
        if parent_types and parent_types.isdisjoint(compatible_types):
            continue
        selected_value = value
        if field == JsonSchemaObject.__extra_key__ and isinstance(value, dict):
            selected_value = {
                key: nested_value
                for key, nested_value in value.items()
                if key in _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
            }
        result[field] = selected_value
    return result


def remove_inherited_distributed_shape(
    parser: InheritedSchemasHost,  # noqa: ARG001
    child: dict[str, Any],
    distributed_fields: frozenset[str],
) -> dict[str, Any]:
    """Remove distributed keywords while preserving unrelated extension values."""
    result = {field: value for field, value in child.items() if field not in distributed_fields}
    if (
        JsonSchemaObject.__extra_key__ in distributed_fields
        and isinstance(extras := child.get(JsonSchemaObject.__extra_key__), dict)
        and (remaining_extras := _without_contains_keywords(extras))
    ):
        result[JsonSchemaObject.__extra_key__] = remaining_extras
    return result


def drop_incompatible_inherited_constraints(
    parser: InheritedSchemasHost,  # noqa: ARG001
    child: dict[str, Any],
    parent_types: frozenset[str],
) -> dict[str, Any]:
    """Avoid applying schema constraints to incompatible Python value types."""
    result = child
    for compatible_types, constraint_fields in keyword_kinds():
        if parent_types and parent_types <= compatible_types:
            continue
        for field in constraint_fields & child.keys():
            if result is child:
                result = child.copy()
            result.pop(field)
    if (
        (not parent_types or not parent_types <= {"array"})
        and isinstance(extras := child.get(JsonSchemaObject.__extra_key__), dict)
        and extras.keys() & _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
    ):
        if result is child:
            result = child.copy()
        if filtered_extras := _without_contains_keywords(extras):
            result[JsonSchemaObject.__extra_key__] = filtered_extras
        else:
            result.pop(JsonSchemaObject.__extra_key__, None)
    return result


def is_unconstrained_inherited_schema(parser: InheritedSchemasHost, schema: object) -> bool:
    """Return whether a raw subschema adds no validation to an inherited type."""
    if schema is True:
        return True
    if not isinstance(schema, dict):
        return False
    schema_obj = parser._schema_object(schema)  # noqa: SLF001
    return not schema_obj.has_constraint and not get_inherited_override_shape(parser, schema_obj)


def remove_unconstrained_compositions(parser: InheritedSchemasHost, schema_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove composition branches that make an inherited override universally valid."""
    result = schema_dict.copy()
    if isinstance(any_of := result.get("anyOf"), list) and any(
        is_unconstrained_inherited_schema(parser, item) for item in any_of
    ):
        result.pop("anyOf")
    if (
        isinstance(one_of := result.get("oneOf"), list)
        and len(one_of) == 1
        and is_unconstrained_inherited_schema(parser, one_of[0])
    ):
        result.pop("oneOf")
    if isinstance(all_of := result.get("allOf"), list):
        if constrained_items := [item for item in all_of if not is_unconstrained_inherited_schema(parser, item)]:
            result["allOf"] = constrained_items
        else:
            result.pop("allOf")
    return result


def get_flattenable_inherited_constraint_items(
    parser: InheritedSchemasHost,
    keyword: str,
    raw_items: list[Any],
) -> list[dict[str, Any]] | None:
    """Return normalized constraint-only branches when composition flattening is equivalent."""
    items = raw_items
    match keyword:
        case "allOf":
            if any(item is False for item in raw_items):
                return None
        case "anyOf" | "oneOf":
            items = [item for item in raw_items if item is not False]
        case _:  # pragma: no cover
            return None
    if len(items) != 1:
        return None
    if (item := items[0]) is True:
        return [{}]
    normalized_item = normalize_inherited_constraint_compositions(parser, parser._schema_object(item))  # noqa: SLF001
    if get_inherited_override_shape(parser, normalized_item):
        return None
    return [normalized_item.model_dump(exclude_unset=True, by_alias=True)]


def normalize_inherited_nested_schema_value(parser: InheritedSchemasHost, value: object) -> object | None:
    """Normalize schema-valued container keywords without copying unchanged branches."""
    match value:
        case JsonSchemaObject() as nested_schema:
            if (normalized := normalize_inherited_constraint_compositions(parser, nested_schema)) is nested_schema:
                return None
            return normalized.model_dump(exclude_unset=True, by_alias=True)
        case list() | dict():
            members = value if isinstance(value, dict) else dict(enumerate(value))
            normalized = {
                key: normalize_inherited_constraint_compositions(parser, item)
                if isinstance(item, JsonSchemaObject)
                else item
                for key, item in members.items()
            }
            if not unchanged(normalized.values(), members.values()):
                dumped = {key: _schema_dump(item) for key, item in normalized.items()}
                return dumped if isinstance(value, dict) else list(dumped.values())
    return None


def normalize_inherited_constraint_compositions(
    parser: InheritedSchemasHost, schema: JsonSchemaObject
) -> JsonSchemaObject:
    """Flatten constraint-only compositions before resolving their inherited type."""
    nested_updates = {
        field_name: normalized
        for field_name in INHERITED_SCHEMA_KEYWORDS
        if (normalized := normalize_inherited_nested_schema_value(parser, getattr(schema, field_name))) is not None
    }
    if not (nested_updates or schema.allOf or schema.anyOf or schema.oneOf):
        return schema
    schema_dict = schema.model_dump(exclude_unset=True, by_alias=True)
    schema_dict.update(nested_updates)
    changed = bool(nested_updates)
    for keyword in COMBINATOR_POSITIONS:
        match schema_dict.get(keyword):
            case list() as raw_items if (
                raw_items
                and (
                    normalized_items := get_flattenable_inherited_constraint_items(
                        parser,
                        keyword,
                        raw_items,
                    )
                )
                is not None
            ):
                schema_dict.pop(keyword)
                for normalized_item in normalized_items:
                    for key, value in normalized_item.items():
                        schema_dict.setdefault(key, value)
                changed = True
            case _:
                continue
    return parser._schema_object(schema_dict) if changed else schema  # noqa: SLF001


def is_inherited_type_narrowing(
    parser: InheritedSchemasHost,  # noqa: ARG001
    child_types: frozenset[str],
    parent_types: frozenset[str],
) -> bool:
    """Return whether explicit child JSON types are contained by the inherited types."""
    return not parent_types or child_types <= admitted_types(parent_types)


def is_partial_inherited_property(
    parser: InheritedSchemasHost,
    child: JsonSchemaObject,
    parent: JsonSchemaObject,
    context: tuple[str, frozenset[_TargetNode]] = ("", frozenset()),
) -> bool:
    """Return whether a child schema only narrows metadata/constraints of its inherited type."""
    parent_ref, _ = context
    child_dict = remove_unconstrained_compositions(parser, get_inherited_override_shape(parser, child))
    if not child_dict:
        return True

    if (child_type := child_dict.pop("type", None)) is not None:
        child_types = frozenset(declared_types(child_type))
        parent_types = get_inherited_schema_types(parser, parent, parent_ref)
        if not is_inherited_type_narrowing(parser, child_types, parent_types):
            return False

    for field_name, nested_child in child_dict.items():
        if field_name not in INHERITED_SCHEMA_KEYWORDS:
            return False
        nested_parent = getattr(parent, field_name)
        if nested_parent is None:
            continue
        parent_tail = (
            inherited_positional_tail(partial(getattr, parent), field_name)
            if field_name in POSITIONAL_SCHEMA_KEYWORDS and isinstance(nested_parent, list)
            else True
        )
        if not is_partial_inherited_nested_value(
            parser,
            nested_child,
            nested_parent,
            parent_tail=parent_tail,
            context=context,
            schema_map=field_name in MODELED_SCHEMA_MAPS,
        ):
            return False
    return True


def is_partial_inherited_nested_value(  # noqa: PLR0913
    parser: InheritedSchemasHost,
    child: object,
    parent: object,
    *,
    parent_tail: object = True,
    context: tuple[str, frozenset[_TargetNode]] = ("", frozenset()),
    schema_map: bool = False,
) -> bool:
    """Compare one nested schema keyword using JSON Schema intersection semantics."""
    parent_ref, active = context
    if child is None or child is True or parent is True or parent is False:
        return True
    if not schema_map and isinstance(child, dict) and is_unconstrained_inherited_schema(parser, child):
        return True
    result = False
    match child, parent:
        case dict() as child_schema, JsonSchemaObject() as parent_schema:
            child_obj = parser._schema_object(child_schema)  # noqa: SLF001
            effective_parent = parent_schema
            effective_ref = parent_ref
            next_active = active
            if parent_schema.ref:
                effective_parent, effective_ref, next_active = resolve_inherited_parent_property(
                    parser,
                    parent_schema,
                    parent_ref,
                    active - {inherited_node(parser, _schema_refs.ref_path(parser, parent_schema.ref))},
                )
            result = is_partial_inherited_property(
                parser,
                child_obj,
                effective_parent,
                (effective_ref, next_active),
            )
        case list() as child_schemas, list() as parent_schemas:
            result = all(
                is_partial_inherited_nested_value(
                    parser,
                    child_schema,
                    parent_schemas[index] if index < len(parent_schemas) else parent_tail,
                    context=context,
                )
                for index, child_schema in enumerate(child_schemas)
            )
        case dict() as child_schema_map, dict() as parent_schema_map:
            result = all(
                key in parent_schema_map
                and is_partial_inherited_nested_value(
                    parser,
                    child_schema,
                    parent_schema_map[key],
                    context=context,
                )
                for key, child_schema in child_schema_map.items()
            )
        case _:
            pass
    return result


def has_inherited_constraints(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> bool:
    """Return whether a partial schema contains constraints at any container depth."""
    if (
        schema.has_constraint
        or schema.enum
        or "const" in schema.extras
        or schema.extras.keys() & _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS
        or schema.model_fields_set & PROPERTY_COUNT_KEYWORDS
    ):
        return True
    return any(
        has_inherited_constraints(parser, nested_schema)
        for field_name in INHERITED_SCHEMA_KEYWORDS
        for nested_schema in _nested_schema_objects(getattr(schema, field_name))
    )


def mark_inherited_materialized_type_shapes(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> None:
    """Mark synthesized schemas whose container constraints need a root wrapper."""
    schema.__dict__[_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY] = True
    for field_name in _MODELED_POSITIONS:
        for nested_schema in _nested_schema_objects(getattr(schema, field_name)):
            mark_inherited_materialized_type_shapes(parser, nested_schema)


def preserve_inherited_materialized_type_shape(
    parser: InheritedSchemasHost,
    source: JsonSchemaObject,
    target: JsonSchemaObject,
) -> JsonSchemaObject:
    """Propagate the internal materialization marker across schema normalization."""
    if source.__dict__.get(_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY):
        mark_inherited_materialized_type_shapes(parser, target)
    return target


def resolve_inherited_parent_property(
    parser: InheritedSchemasHost,
    schema: JsonSchemaObject,
    parent_ref: str,
    active: frozenset[_TargetNode] = frozenset(),
) -> tuple[JsonSchemaObject, str, frozenset[_TargetNode]]:
    """Resolve pure property references before comparing nested override shapes.

    A reference is cached by the key it resolves to, which its edge gives wherever the schema is read.
    """
    source_schema = schema
    cache_by_ref = False
    cache_identity: str | int = id(schema)
    match schema.ref:
        case str() as schema_ref if schema_ref:
            cache_by_ref = True
            cache_identity = _schema_refs.ref_path(parser, schema_ref)
        case _:
            pass
    cache_key: tuple[str | int, str, frozenset[_TargetNode]] | None = None
    if not cache_by_ref or not schema.has_ref_with_schema_keywords:
        cache_key = (
            cache_identity,
            parent_ref,
            active,
        )
        if (cached := parser._inherited_parent_property_cache.get(cache_key)) is not None and (  # noqa: SLF001
            cache_by_ref or cached[0] is schema
        ):
            return cached[1]
    while schema.ref:
        resolved_ref = _schema_refs.ref_path(parser, schema.ref)
        if (node := inherited_node(parser, resolved_ref)) in active:
            break
        active |= {node}
        referenced_schema = load_inherited_schema_object(parser, resolved_ref)
        if schema.has_ref_with_schema_keywords:
            schema = _schema_refs.intersect_ref_siblings_schema(
                parser,
                referenced_schema.model_dump(exclude_unset=True, by_alias=True),
                schema.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True),
                named_location(schema.schema_location, resolved_ref),
                siblings_are_parts=parser._ref_siblings_are_parts_of(schema),  # noqa: SLF001
            )
        else:
            schema = referenced_schema
        parent_ref = resolved_ref
    result = schema, parent_ref, active
    if cache_key is not None:
        parser._inherited_parent_property_cache[cache_key] = source_schema, result  # noqa: SLF001
    return result


def get_nested_inherited_active(
    parser: InheritedSchemasHost,
    parent: dict[str, Any],
    child: dict[str, Any],
    active: frozenset[_TargetNode],
) -> frozenset[_TargetNode]:
    """Permit recursive expansion only along the finite child override path."""
    if child and isinstance(parent_ref := parent.get("$ref"), str):
        return active - {inherited_node(parser, _schema_refs.ref_path(parser, parent_ref))}
    return active


def merge_inherited_union_branch(  # noqa: PLR0913
    parser: InheritedSchemasHost,
    parent: object,
    child: dict[str, Any],
    distributed_fields: frozenset[str],
    context: tuple[str, frozenset[_TargetNode]],
    *,
    excludes_null: bool,
) -> object:
    """Apply type-specific child keywords to one compatible inherited branch."""
    if not isinstance(parent, dict):
        return parent
    parent = cast("dict[str, Any]", parent)
    parent_ref, active = context
    parent_obj = parser._schema_object(parent)  # noqa: SLF001
    parent_types = get_inherited_schema_types(parser, parent_obj, parent_ref, active)
    if excludes_null and parent_types == {"null"}:
        return _NO_INHERITED_SCHEMA_MERGE
    branch_child = select_inherited_distributed_shape(
        parser,
        child,
        distributed_fields,
        parent_types,
    )
    if excludes_null and "null" in parent_types:
        branch_child["nullable"] = False
    return _located(
        merge_inherited_type_shape_dict(
            parser,
            parent,
            branch_child,
            parent_ref,
            get_nested_inherited_active(
                parser,
                parent,
                branch_child,
                active,
            ),
        ),
        _dump_location(parent),
    )


def merge_inherited_type_shape_dict(  # noqa: PLR0914
    parser: InheritedSchemasHost,
    parent: dict[str, Any],
    child: dict[str, Any],
    parent_ref: str,
    active: frozenset[_TargetNode] = frozenset(),
) -> dict[str, Any]:
    """Fill missing type-shape keywords while keeping child validation precedence."""
    parent_obj = parser._schema_object(parent)  # noqa: SLF001
    effective_parent, effective_ref, next_active = resolve_inherited_parent_property(
        parser,
        parent_obj,
        parent_ref,
        active,
    )
    parent_types = get_inherited_schema_types(parser, effective_parent, effective_ref, next_active)
    parent_shape = get_inherited_type_shape(parser, effective_parent, partial(_schema_refs.ref_path, parser))
    child_obj = parser._schema_object(child)  # noqa: SLF001
    excludes_null = parser._null_facts(child_obj, "inherited-child").admits_null is False  # noqa: SLF001
    if excludes_null:
        parent_types -= {"null"}
    distributed_fields = get_inherited_distributed_fields(parser, child_obj)
    child_shape = child
    parent_type_shape = parent_shape.get("type")
    if excludes_null and isinstance(parent_type_shape, list) and (filtered_types := non_null_types(parent_type_shape)):
        parent_shape["type"] = filtered_types[0] if len(filtered_types) == 1 else filtered_types
    if (union_key := next((key for key in ("anyOf", "oneOf") if parent_shape.get(key)), None)) is not None:
        merged_branches = [
            merged_branch
            for branch in cast("list[object]", parent_shape[union_key])
            if (
                merged_branch := merge_inherited_union_branch(
                    parser,
                    branch,
                    child,
                    distributed_fields,
                    (effective_ref, next_active),
                    excludes_null=excludes_null,
                )
            )
            is not _NO_INHERITED_SCHEMA_MERGE
        ]
        parent_shape[union_key] = merged_branches
    elif distributed_fields and len(parent_types) > 1:
        parent_distributed_fields = get_inherited_distributed_fields(parser, effective_parent) & parent_shape.keys()
        branch_parent_shape = parent_shape
        parent_shape = remove_inherited_distributed_shape(
            parser,
            parent_shape,
            frozenset(parent_distributed_fields),
        )
        parent_shape.pop("type", None)
        parent_shape["anyOf"] = [
            _located(
                merge_inherited_type_shape_dict(
                    parser,
                    {
                        "type": parent_type,
                        **select_inherited_distributed_shape(
                            parser,
                            branch_parent_shape,
                            frozenset(parent_distributed_fields),
                            frozenset((parent_type,)),
                        ),
                    },
                    select_inherited_distributed_shape(
                        parser,
                        child,
                        distributed_fields,
                        frozenset((parent_type,)),
                    )
                    | ({"nullable": False} if excludes_null and parent_type == "null" else {}),
                    effective_ref,
                    next_active,
                ),
                _dump_location(child),
            )
            for parent_type in sorted(parent_types)
        ]
    if distributed_fields:
        child_shape = remove_inherited_distributed_shape(parser, child, distributed_fields)
        if union_key is None and len(parent_types) <= 1:
            child_shape.update(select_inherited_distributed_shape(parser, child, distributed_fields, parent_types))
    if (
        parent_shape.get("allOf")
        and len(parent_types) == 1
        and (parent_type := next(iter(parent_types))) not in {"array", "object"}
    ):
        parent_shape = {"type": parent_type}
    child_shape = drop_incompatible_inherited_constraints(
        parser,
        child_shape,
        parent_types,
    )
    if (
        "type" not in parent_shape
        and not any(parent_shape.get(key) for key in COMBINATOR_POSITIONS)
        and len(parent_types) == 1
    ):
        parent_shape["type"] = next(iter(parent_types))
    merge_value = partial(
        merge_inherited_value,
        merge_schemas=lambda parent_schema, child_schema: merge_inherited_type_shape_dict(
            parser,
            parent_schema,
            child_schema,
            effective_ref,
            get_nested_inherited_active(parser, parent_schema, child_schema, next_active),
        ),
        row="type-shape",
    )
    result: dict[str, Any] = parent_shape.copy()
    for key, value in child_shape.items():
        if key in INHERITED_SCHEMA_KEYWORDS and key in parent_shape:
            result[key] = merge_inherited_keyword(
                key, parent_shape[key], value, parent_shape.get, merge_value, maps_by_name=True
            )
        else:
            result[key] = value
    return _located(result, _dump_location(child))


def override_row(parser: InheritedSchemasHost, parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Return ``parser/_intersection.py:inherited_override_row`` under the merge mode of this parse.

    An override drops the inherited ``nullable`` by the null rule ``inherited-typed-without-null``, read from its
    own ``type`` and ``nullable`` (``parser/_null_facts.py:dump_null_sources``). A map of named schemas merges name by
    name unless ``legacy-schema-maps-merged-as-schemas`` is kept.
    """
    return inherited_override_row(
        parent,
        child,
        merge_mode=parser.allof_merge_mode.value,
        drops_null=_dump_drops_inherited_null,
        maps_by_name="legacy-schema-maps-merged-as-schemas" not in parser._legacy_intersection,  # noqa: SLF001
    )


def inherited_override_schema(
    parser: InheritedSchemasHost, parent: JsonSchemaObject, child: JsonSchemaObject, parent_ref: str, where: str
) -> JsonSchemaObject:
    """Return the schema of a property ``child`` that overrides the inherited property ``parent``.

    The row of ``--allof-merge-mode`` decides: ``none`` writes the child's constraints on the inherited type shape
    (``merge_inherited_type_shape_dict``) where the child only narrows it, and keeps the child otherwise; ``all``
    and ``constraints`` give the intersection of both (``intersect_keywords``) over their presentation row
    (``parser/_intersection.py:inherited_override_row``). A child without constraints that only narrows the inherited
    property is written on the type shape too, unless ``legacy-type-narrowing-override-deferred`` is kept, where it
    is the copy of the inherited field and none reaches here (``get_deferred_inherited_property_names``).
    """
    if parser.allof_merge_mode == AllOfMergeMode.NoMerge:
        if (
            "legacy-type-narrowing-override-deferred" in parser._legacy_intersection  # noqa: SLF001
            and not has_inherited_constraints(parser, child)
        ):
            return child
        effective_parent, effective_ref, active = resolve_inherited_parent_property(parser, parent, parent_ref)
        if not is_partial_inherited_property(parser, child, effective_parent, (effective_ref, active)):
            return child
        child_dict = child.model_dump(exclude_unset=True, by_alias=True)
        merged_dict = merge_inherited_type_shape_dict(
            parser,
            parent.model_dump(exclude_unset=True, by_alias=True),
            child_dict,
            parent_ref,
        )
        if merged_dict == child_dict:
            return child
        merged_schema = parser._schema_object(merged_dict)  # noqa: SLF001
        mark_inherited_materialized_type_shapes(parser, merged_schema)
        return merged_schema
    parent_dict = parent.model_dump(exclude_unset=True, by_alias=True)
    child_dict = child.model_dump(exclude_unset=True, by_alias=True)
    intersection = _intersect_parts(
        parser,
        [parent_dict, child_dict],
        override_row(parser, parent_dict, child_dict),
        where=where,
        legacy_rows=parser._legacy_intersection,  # noqa: SLF001
    )
    merged_property = parser._schema_object(intersection.keywords)  # noqa: SLF001
    _schema_refs.mark_reported_value_conflict(parser, merged_property, intersection.unsatisfiable)
    merged_property.__dict__[_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY] = frozenset(parser.get_field_extras(child))
    merged_property.__dict__[_INHERITED_OVERRIDE_KEY] = True
    return merged_property


def copy_carries_override(
    parser: InheritedSchemasHost,
    child: JsonSchemaObject,
    parent: JsonSchemaObject,
    context: tuple[str, frozenset[_TargetNode]],
) -> bool:
    """Return whether the copy of an inherited field carries a partial override without constraints.

    The copy keeps the inherited type and drops at most its null, so it carries an override whose ``type`` admits
    every non-null type the inherited property admits and whose nested schema keywords admit every value the
    inherited ones do: the inherited value narrows the override's (``is_partial_inherited_nested_value`` read the
    other way round), or the override writes the inherited value as it is.
    """
    parent_ref, active = context
    shape = remove_unconstrained_compositions(parser, get_inherited_override_shape(parser, child))
    if (child_type := shape.pop("type", None)) is not None and not set(
        non_null_types(get_inherited_schema_types(parser, parent, parent_ref, active))
    ) <= admitted_types(declared_types(child_type)):
        return False
    parent_dump = parent.model_dump(exclude_unset=True, by_alias=True)
    return all(
        _admits_inherited_value(parser, getattr(child, keyword), parent_dump.get(keyword), context) for keyword in shape
    )


def _admits_inherited_value(
    parser: InheritedSchemasHost, child: object, parent: object, context: tuple[str, frozenset[_TargetNode]]
) -> bool:
    """Return whether the value an override gives a nested schema keyword admits every value of the inherited one.

    ``child`` is the override's value (schema objects), ``parent`` the dump of the inherited value; a map of named
    schemas and the positions of a tuple compare entry by entry of the override, and a value the inherited schema
    does not set is admitted only where the override's constrains nothing.
    """
    match child, parent:
        case JsonSchemaObject() as child_schema, _:
            child_dump = child_schema.model_dump(exclude_unset=True, by_alias=True)
            if parent is None:
                return is_unconstrained_inherited_schema(parser, child_dump)
            return child_dump == parent or is_partial_inherited_nested_value(
                parser, parent, child_schema, context=context
            )
        case dict() as child_map, dict() as parent_map:
            return all(
                _admits_inherited_value(parser, value, parent_map.get(name), context)
                for name, value in child_map.items()
            )
        case list() as child_items, list() as parent_items:
            return all(
                _admits_inherited_value(
                    parser, value, parent_items[index] if index < len(parent_items) else None, context
                )
                for index, value in enumerate(child_items)
            )
    return child is True or child == parent


def get_deferred_inherited_property_names(
    parser: InheritedSchemasHost,
    source_obj: JsonSchemaObject,
    parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]],
) -> frozenset[str]:
    """Find partial properties that need canonical generated-type resolution.

    A partial override without constraints is resolved as a copy of the inherited field, unless
    ``legacy-type-narrowing-override-deferred`` is dropped and the copy does not carry it (``copy_carries_override``):
    such an override is the intersection with the inherited property, as a constrained one is.
    """
    if not source_obj.properties:
        return frozenset()
    deferred_names: set[str] = set()
    for field_name, child_property in source_obj.properties.items():
        if not (
            (inherited_property := parent_properties.get(field_name))
            and isinstance(parent_property := inherited_property[0], JsonSchemaObject)
        ):
            continue
        match child_property:
            case True:
                deferred_names.add(field_name)
            case JsonSchemaObject() as child_schema:
                normalized_child = normalize_inherited_constraint_compositions(parser, child_schema)
                effective_parent, effective_ref, active = resolve_inherited_parent_property(
                    parser,
                    parent_property,
                    inherited_property[1],
                )
                if not is_partial_inherited_property(
                    parser,
                    normalized_child,
                    effective_parent,
                    (effective_ref, active),
                ):
                    continue
                if has_inherited_constraints(parser, normalized_child):
                    continue
                if "legacy-type-narrowing-override-deferred" not in parser._legacy_intersection and not (  # noqa: SLF001
                    copy_carries_override(parser, normalized_child, effective_parent, (effective_ref, active))
                ):
                    continue
                deferred_names.add(field_name)
            case _:
                continue
    return frozenset(deferred_names)


def mark_partial_inherited_fields(
    parser: InheritedSchemasHost,
    fields: list[DataModelFieldBase],
    deferred_property_names: frozenset[str],
    source_obj: JsonSchemaObject,
) -> None:
    """Mark partial properties so late forward resolution uses the canonical parent type."""
    if not deferred_property_names:
        return
    properties = source_obj.properties or {}
    for field in fields:
        field_name = _field_source_name(field)
        if field_name not in deferred_property_names:
            continue
        source_property = properties.get(field_name)
        facts = (
            parser._null_facts(source_property, "inherited-child")  # noqa: SLF001
            if isinstance(source_property, JsonSchemaObject)
            else None
        )
        field.__dict__[_DEFERRED_INHERITED_TYPE_KEY] = _get_inherited_type_modifiers(
            field.data_type,
            null_rule=facts.rule if facts is not None and facts.admits_null is False else None,
            drops_null_members=parser._null_mode.type_excludes_member_null  # noqa: SLF001
            and facts is not None
            and facts.rule == "inherited-typed-without-null",
        )


def get_deferred_inherited_parse_object(
    parser: InheritedSchemasHost,  # noqa: ARG001
    source_obj: JsonSchemaObject,
    deferred_property_names: frozenset[str],
) -> JsonSchemaObject:
    """Strip nested type work that late canonical inheritance will replace."""
    if not (deferred_property_names and source_obj.properties):
        return source_obj
    properties = source_obj.properties.copy()
    changed = False
    for field_name in deferred_property_names:
        if not isinstance(property_schema := properties.get(field_name), JsonSchemaObject):
            continue
        updates = {
            schema_field: None
            for schema_field in INHERITED_SCHEMA_KEYWORDS
            if getattr(property_schema, schema_field) is not None
        }
        if property_schema.title is not None:
            updates["title"] = None
        if not updates:
            continue
        properties[field_name] = property_schema.model_copy(update=updates)
        changed = True
    return source_obj.model_copy(update={"properties": properties}) if changed else source_obj


def sanitize_untyped_boolean_inherited_property(
    parser: InheritedSchemasHost,
    child: JsonSchemaObject,
) -> JsonSchemaObject | None:
    """Drop type-specific constraints when a boolean parent supplies no usable type."""
    if parser.allof_merge_mode != AllOfMergeMode.NoMerge or child.type is not None:
        return None
    if any((child.ref, child.allOf, child.anyOf, child.oneOf, child.enum, "const" in child.extras)):
        return None
    if not has_inherited_constraints(parser, child):
        return None
    child_dict = child.model_dump(exclude_unset=True, by_alias=True)
    sanitized_dict = drop_incompatible_inherited_constraints(
        parser,
        remove_inherited_distributed_shape(
            parser,
            child_dict,
            get_inherited_distributed_fields(parser, child),
        ),
        frozenset(),
    )
    return parser._schema_object(sanitized_dict)  # noqa: SLF001


def merge_properties_with_parent_constraints(
    parser: InheritedSchemasHost,
    child_obj: JsonSchemaObject,
    parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]],
    deferred_property_names: frozenset[str],
) -> JsonSchemaObject:
    """Merge child properties with parent property constraints for allOf inheritance."""
    if not child_obj.properties or not parent_properties:
        return child_obj

    merged_properties: dict[str, JsonSchemaObject | bool] = {}
    merged_changed = False
    for prop_name, child_prop in child_obj.properties.items():
        if not isinstance(child_prop, JsonSchemaObject):
            merged_properties[prop_name] = child_prop
            continue
        inherited_property = parent_properties.get(prop_name)
        if inherited_property is None:
            merged_properties[prop_name] = child_prop
            continue

        parent_prop, parent_ref = inherited_property
        effective_child = normalize_inherited_constraint_compositions(parser, child_prop)
        if effective_child is not child_prop:
            merged_changed = True
        if not isinstance(parent_prop, JsonSchemaObject):
            if isinstance(parent_prop, bool) and (
                sanitized_child := sanitize_untyped_boolean_inherited_property(parser, effective_child)
            ):
                merged_properties[prop_name] = sanitized_child
                merged_changed = True
            else:
                merged_properties[prop_name] = effective_child
            continue
        if prop_name in deferred_property_names:
            child_dict = effective_child.model_dump(exclude_unset=True, by_alias=True)
            normalized_dict = remove_unconstrained_compositions(parser, child_dict)
            merged_properties[prop_name] = (
                parser._schema_object(normalized_dict) if normalized_dict != child_dict else effective_child  # noqa: SLF001
            )
            merged_changed = merged_changed or normalized_dict != child_dict
            continue
        merged_property = inherited_override_schema(
            parser,
            parent_prop,
            effective_child,
            parent_ref,
            named_location(child_prop.schema_location, f"property {stable_repr(prop_name)}"),
        )
        merged_properties[prop_name] = merged_property
        merged_changed = merged_changed or merged_property is not effective_child

    if not merged_changed:
        return child_obj
    return child_obj.model_copy(
        update={
            "properties": {
                prop_name: merged.with_schema_location(child_prop.schema_location)
                if isinstance(merged, JsonSchemaObject)
                and isinstance(child_prop := child_obj.properties[prop_name], JsonSchemaObject)
                else merged
                for prop_name, merged in merged_properties.items()
            }
        }
    )


def inherited_node(parser: InheritedSchemasHost, resolved_ref: str) -> _TargetNode:
    """Return the schema graph node of the inherited schema at a key (``_load_inherited_schema_object``)."""
    return _schema_node(load_inherited_schema_object(parser, resolved_ref), resolved_ref)


def all_of_members(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> list[JsonSchemaObject | bool]:
    """Return the members of an allOf as its readers read them, in order.

    ``JsonSchemaParser.parse_all_of`` (the members it parses and the properties they declare), ``iter_allof_refs``
    (the parents of the inherited property map) and the request/response walk (``_typed_model_children``) read the
    members through it. A member is as written under ``legacy-all-of-ref-siblings-ignored`` and
    ``legacy-all-of-alias-member-base``. Without the first, a ``$ref`` member whose sibling keywords are parts of it
    (``_ref_siblings_are_parts_of``, keywords beyond annotations) is read as two members, the ``$ref`` and its
    siblings at its location, as an inline member is read. Without the second, a ``$ref`` member names the schema that
    a chain of aliases ends at (``alias_target``).
    """
    legacy = parser._legacy_intersection  # noqa: SLF001
    splits = "legacy-all-of-ref-siblings-ignored" not in legacy
    follows = "legacy-all-of-alias-member-base" not in legacy
    reads_through = "legacy-ref-member-union-dropped" not in legacy
    if not (splits or follows or reads_through):
        return list(schema.allOf)
    members: list[JsonSchemaObject | bool] = []
    for item in schema.allOf:
        if not (isinstance(item, JsonSchemaObject) and item.ref):
            members.append(item)
            continue
        target = alias_target(parser, item.ref) if follows else item.ref
        siblings = splits and item.has_ref_with_schema_keywords and parser._ref_siblings_are_parts_of(item)  # noqa: SLF001
        if target is item.ref and not siblings:
            members.append(item)
            continue
        members.append(parser._schema_object({"$ref": target}).with_schema_location(item.schema_location))  # noqa: SLF001
        if siblings:
            members.append(
                parser._schema_object(_dump_without_keywords(item, {"$ref"})).with_schema_location(  # noqa: SLF001
                    item.schema_location
                )
            )
    return _read_through_members(parser, schema, members) if reads_through else members


def _read_through_members(
    parser: InheritedSchemasHost, schema: JsonSchemaObject, members: list[JsonSchemaObject | bool]
) -> list[JsonSchemaObject | bool]:
    """Return the members of an allOf with each ``$ref`` member that names an allOf holding a union read in place.

    Such a member (an allOf beside its own ``anyOf`` or ``oneOf``, or one whose members hold one,
    ``holds_union_member``) is read as an inline member, the schema it names, so its union parts join those of the
    site. The member is marked with the locations read through so far (the site's first), and so are the inline
    members within it; a member naming one of them adds nothing (A and A is A). A schema that an ``x-python-import``
    types is the class it imports, as ``alias_target`` reads it, so a member naming it stays a base. A schema of another
    document is read as the current document reads it (``_read_through_target``).
    """
    within = schema.__dict__.get(_READ_THROUGH_KEY)
    read: list[JsonSchemaObject | bool] = []
    for member in members:
        if not isinstance(member, JsonSchemaObject) or (not member.ref and within is None):
            read.append(member)
        elif not member.ref:
            read.append(_read_within(member, within))
        elif (
            not (target := parser._load_ref_schema_object(member.ref)).allOf  # noqa: SLF001
            or "x-python-import" in target.extras
            or not (target.anyOf or target.oneOf or holds_union_member(parser, target))
        ):
            read.append(member)
        elif (location := target.schema_location) not in (chain := within or frozenset({schema.schema_location})):
            read.append(_read_within(_read_through_target(parser, member.ref, target), chain | {location}))
    return read


def _read_through_target(parser: InheritedSchemasHost, ref: str, target: JsonSchemaObject) -> JsonSchemaObject:
    """Return the schema ``target`` that a member read in place names, as the document being parsed reads it.

    A schema of another document is its dump for this document (``_schema_refs.ref_target_dump``), where each
    ``$recursiveRef`` that has a fixed target there names it, as a schema merged from there reads it.
    """
    resolved_ref = _schema_refs.ref_path(parser, ref)
    document = current_document(parser)
    if _schema_refs.schema_location(parser, resolved_ref).partition("#")[0] == document:
        return target
    return parser._schema_object(_schema_refs.ref_target_dump(parser, ref, resolved_ref)[0])  # noqa: SLF001


def _read_within(schema: JsonSchemaObject, chain: frozenset[SchemaLocation | None]) -> JsonSchemaObject:
    """Return a copy of an allOf member marked as read within the locations ``chain`` (``_read_through_members``)."""
    copied = schema.model_copy()
    copied.__dict__[_READ_THROUGH_KEY] = chain
    return copied


def property_conjunctions(
    parser: InheritedSchemasHost, schema: JsonSchemaObject
) -> dict[str, list[JsonSchemaObject | None]]:
    """Return, by name, how each declaration of a property that several inline allOf members declare is read.

    The list holds one entry per declaration, in the order ``parse_all_of`` parses the members: the schema the
    declaration is read as, None where it is left out. Each declaration would be a field of the site, the first of
    which wins (``DataModel._validate_fields``); the request/response variants of the site take the last. A ``$ref``
    member read in place (``_read_through_members``) is no base class whose properties the other members override, so
    a property that members read within different locations declare is one field, the conjunction of every declaration
    (``_conjoined_declarations``) where it is first declared; no member is read in place under
    ``legacy-ref-member-union-dropped``. Each declaration of a property that members within one location declare
    takes the check keywords of all of them (``checked_declarations``). A property that one of them declares as a
    boolean schema keeps its declarations.
    """
    reads_through = "legacy-ref-member-union-dropped" not in parser._legacy_intersection  # noqa: SLF001
    declarations: dict[str, list[tuple[JsonSchemaObject | bool, object]]] = {}
    for source in islice(inline_all_of_parts(parser, schema), 1, None):
        for name, declared in (source.properties or {}).items():
            declarations.setdefault(name, []).append((declared, source.__dict__.get(_READ_THROUGH_KEY)))
    conjunctions: dict[str, list[JsonSchemaObject | None]] = {}
    for name, declared_within in declarations.items():
        schemas = [declared for declared, _ in declared_within if isinstance(declared, JsonSchemaObject)]
        if len(declared_within) == 1 or len(schemas) < len(declared_within):
            continue
        if reads_through and len({within for _, within in declared_within}) > 1:
            conjunctions[name] = [_conjoined_declarations(parser, name, schemas), *([None] * (len(schemas) - 1))]
        elif (checked := checked_declarations(parser, name, schemas)) is not None:
            conjunctions[name] = [*checked]
    return conjunctions


def checked_declarations(
    parser: InheritedSchemasHost, name: str, declarations: Sequence[JsonSchemaObject]
) -> tuple[JsonSchemaObject, ...] | None:
    """Return several declarations of one property, each with the check keywords of all of them; None if none changes.

    The check keywords (``_CHECK_KEYWORDS``: the bounds, ``multipleOf`` and ``pattern``) narrow the values of the type
    a declaration is written as, never that type or a value it parses to, so whichever declaration a model writes
    checks what every one of them does. Each takes the value all of them admit (``intersect_keywords`` over a row in
    which its own values win), keeping its own with a warning for keywords without a common value. Item counts beside
    positional items decide whether an array is a tuple (``get_fixed_positional_items``), so such a declaration takes
    none; a reference (``_REF_KEYWORDS``) takes none and gives none, since keywords beside it change how it is written
    and may be no part of it in its dialect.
    """
    dumps = [declared.model_dump(exclude_unset=True, by_alias=True) for declared in declarations]
    takes = [
        frozenset()
        if any(keyword in dump for keyword in _REF_KEYWORDS)
        else _CHECK_KEYWORDS - ITEM_COUNT_KEYWORDS
        if "prefixItems" in dump or isinstance(dump.get("items"), list)
        else _CHECK_KEYWORDS
        for dump in dumps
    ]
    parts = [
        {keyword: dump[keyword] for keyword in _CHECK_KEYWORDS.intersection(dump)} if taken else {}
        for dump, taken in zip(dumps, takes, strict=True)
    ]
    checked: list[JsonSchemaObject] = []
    for index, (declared, dump, taken) in enumerate(zip(declarations, dumps, takes, strict=True)):
        intersection = _intersect_dumps(
            parts, reduce(lambda row, part: {**part, **row}, parts, parts[index]), own_last=False
        )
        if index == 0:
            parser._warn_intersection(  # noqa: SLF001
                named_location(declared.schema_location, f"property {stable_repr(name)}"),
                intersection,
                "the allOf members that declare it",
            )
        updates = {
            keyword: value
            for keyword, value in intersection.keywords.items()
            if keyword in taken and dump.get(keyword) != value
        }
        checked.append(
            parser._schema_object({**dump, **updates}).with_schema_location(declared.schema_location)  # noqa: SLF001
            if updates
            else declared
        )
    return None if all(map(operator.is_, checked, declarations)) else tuple(checked)


def _conjoined_declarations(
    parser: InheritedSchemasHost, name: str, declarations: list[JsonSchemaObject]
) -> JsonSchemaObject:
    """Return the schema of a property several allOf members declare: the intersection of their declarations.

    The row is their merge as allOf members (``all_of_member_row``), and every keyword the intersection reads keeps
    the value all the declarations admit, also under ``legacy-merge-row``, with a warning for keywords without a
    common value. Siblings of a ``$ref`` it keeps are parts of it in every dialect (``_CONJUNCTION_PARTS_KEY``). It
    stands at the first declaration.
    """
    dumps = [declared.model_dump(exclude_unset=True, by_alias=True) for declared in declarations]
    location = declarations[0].schema_location
    intersection = _intersect_parts(
        parser,
        dumps,
        reduce(all_of_member_row, dumps[1:], dumps[0]),
        where=named_location(location, f"property {stable_repr(name)}"),
        parts=("the allOf members that declare it",),
        own_last=False,
    )
    merged = parser._schema_object(intersection.keywords).with_schema_location(location)  # noqa: SLF001
    _schema_refs.mark_reported_value_conflict(parser, merged, intersection.unsatisfiable)
    mark_conjunction_parts(merged)
    return merged


def with_conjoined_properties(
    schema: JsonSchemaObject, conjunctions: Mapping[str, Sequence[JsonSchemaObject | None]], written: Counter[str]
) -> JsonSchemaObject:
    """Return an allOf member with its declarations of the properties of ``conjunctions`` read as they say.

    ``conjunctions`` holds the entry of each declaration in parse order (``property_conjunctions``): a member takes the
    next entry of each name it declares, which ``written`` counts, leaving out a declaration whose entry is None.
    """
    if not (names := conjunctions.keys() & (schema.properties or {}).keys()):
        return schema
    properties: dict[str, JsonSchemaObject | bool] = {}
    for name, declared in (schema.properties or {}).items():
        if name not in names:
            properties[name] = declared
            continue
        if (read := conjunctions[name][written[name]]) is not None:
            properties[name] = read
        written[name] += 1
    return schema.model_copy(update={"properties": properties})


def alias_target(parser: InheritedSchemasHost, ref: str) -> str:
    """Return the reference a chain of aliases from ``ref`` ends at, ``ref`` itself when it names no alias.

    An alias is a schema that is a ``$ref`` beside annotations only (``has_ref_with_schema_keywords`` false): it is
    the same schema as its target in every output form, unless an ``x-python-import`` types its references by the
    class it imports. A reference the chain has passed stops it.
    """
    seen = {_schema_refs.ref_path(parser, ref)}
    while (
        (target := parser._load_ref_schema_object(ref)).ref  # noqa: SLF001
        and not target.has_ref_with_schema_keywords
        and "x-python-import" not in target.extras
        and (path := _schema_refs.ref_path(parser, target.ref)) not in seen
    ):
        seen.add(path)
        ref = target.ref
    return ref


def iter_allof_refs(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> Iterator[str]:
    """Yield refs nested only through inline allOf branches in document order (members by ``all_of_members``)."""
    for item in all_of_members(parser, schema):
        match item:
            case JsonSchemaObject(ref=str() as ref) if ref:
                yield ref
            case JsonSchemaObject() as inline_schema:
                yield from iter_allof_refs(parser, inline_schema)


def get_allof_parent_references(
    parser: InheritedSchemasHost,
    schema: JsonSchemaObject,
    *,
    inherited: Iterable[Reference] = (),
    defining_ref: str | None = None,
) -> list[Reference]:
    """Collect effective allOf parents once, preserving declaration precedence."""
    references = {reference.path: reference for reference in inherited}
    for ref in iter_allof_refs(parser, schema):
        reference = (
            parser._add_ref(ref)  # noqa: SLF001
            if defining_ref is None
            else parser.model_resolver.add_ref(_schema_refs.ref_path(parser, ref), resolved=True)
        )
        references.setdefault(reference.path, reference)
    return list(references.values())


def get_inherited_schema_ancestor_paths(
    parser: InheritedSchemasHost,
    resolved_ref: str,
    active: frozenset[_TargetNode] = frozenset(),
) -> frozenset[str]:
    """Collect raw-schema ancestors for stable base ordering before generation."""
    if resolved_ref in parser._inherited_schema_ancestor_cache:  # noqa: SLF001
        return parser._inherited_schema_ancestor_cache[resolved_ref]  # noqa: SLF001
    if _is_special_path(resolved_ref):
        return frozenset()
    schema = load_inherited_schema_object(parser, resolved_ref)
    if (node := _schema_node(schema, resolved_ref)) in active:
        return frozenset()
    ancestors: set[str] = set()
    for ref in iter_allof_refs(parser, schema):
        parent_ref = _schema_refs.ref_path(parser, ref)
        ancestors.add(parent_ref)
        ancestors.update(
            get_inherited_schema_ancestor_paths(
                parser,
                parent_ref,
                active | {node},
            )
        )
    result = frozenset(ancestors)
    parser._inherited_schema_ancestor_cache[resolved_ref] = result  # noqa: SLF001
    return result


def sort_inherited_base_classes_for_mro(parser: InheritedSchemasHost, base_classes: list[Reference]) -> list[Reference]:
    """Match the stable descendant-before-ancestor ordering used at render time."""
    return sort_descendants_first(
        base_classes,
        lambda base: base.path or None,
        lambda base: get_inherited_schema_ancestor_paths(parser, base.path),
    )


def inherited_schema_parents(parser: InheritedSchemasHost, ref: str) -> list[str]:
    """Return the allOf parents of the inherited schema at ``ref`` in the order the C3 merge reads them.

    Row ``class-bases`` (``--read-allof-parents-in-mro-order``) orders them descendants first like the bases of its
    class, so a property each declares takes the nearer base's declaration, as the class does. Row
    ``legacy-document-order`` keeps the document order of its allOf references, as in 0.90.0: with
    ``allOf: [A, B]`` and ``B = allOf: [A, ...]`` the merge finds no free head and takes the farther base ``A`` first.
    """
    parents = [
        _schema_refs.ref_path(parser, parent)
        for parent in iter_allof_refs(parser, load_inherited_schema_object(parser, ref))
    ]
    if parser._inherited_parent_row == "legacy-document-order":  # noqa: SLF001
        return parents
    return sort_descendants_first(parents, lambda path: path, partial(get_inherited_schema_ancestor_paths, parser))


def linearize_inherited_schema_refs(parser: InheritedSchemasHost, base_classes: list[Reference]) -> tuple[str, ...]:
    """Return raw inherited schemas in the same C3 order as generated models.

    The parents of each schema are those ``inherited_schema_parents`` reads.
    """
    direct_refs = tuple(
        base.path
        for base in sort_inherited_base_classes_for_mro(parser, base_classes)
        if base.path and not _is_special_path(base.path)
    )
    if direct_refs not in parser._inherited_schema_linearization_cache:  # noqa: SLF001
        parser._inherited_schema_linearization_cache[direct_refs] = tuple(  # noqa: SLF001
            linearize(
                list(direct_refs),
                lambda ref: ref,
                partial(inherited_schema_parents, parser),
                lambda ref: _schema_node(load_inherited_schema_object(parser, ref), ref),
            )
        )
    return parser._inherited_schema_linearization_cache[direct_refs]  # noqa: SLF001


def model_declarations(
    parser: InheritedSchemasHost, schema: JsonSchemaObject | None, parents: list[Reference]
) -> ModelDeclarations:
    """Return the property declarations and the required names of a model built from ``schema`` and ``parents``.

    The sources are ``schema`` with its inline allOf members and the schemas ``parents`` inherit, in C3 order
    (``linearize_inherited_schema_refs``). A property one source declares as a schema is that declaration, as the
    model writes it; one that several declare, or one declares as a boolean schema, is None: the model writes their
    merge.
    """
    declared: dict[str, JsonSchemaObject | None] = {}
    required: set[str] = set()
    for source in (
        *(inline_all_of_parts(parser, schema) if schema is not None else ()),
        *(
            source
            for ref in linearize_inherited_schema_refs(parser, parents)
            for source in inline_all_of_parts(parser, load_inherited_schema_object(parser, ref))
        ),
    ):
        required.update(source.required)
        for name, declaration in (source.properties or {}).items():
            declared[name] = declaration if name not in declared and isinstance(declaration, JsonSchemaObject) else None
    return declared, frozenset(required)


def union_model_properties(
    parser: InheritedSchemasHost, site: JsonSchemaObject, declarations: Sequence[ModelDeclarations]
) -> dict[str, tuple[JsonSchemaObject, bool]]:
    """Return the properties a union model of an allOf declares itself, and whether a base requires each.

    A union model is a class of the site's properties and bases: one branch of each union and the class of the other
    parts, whose ``declarations`` (``model_declarations``) come in that order. A property two of its bases declare is
    written from the first in Python's method resolution order, and from the last by msgspec, so the check keywords
    of the others would be lost. The class declares it with the check keywords of all (``checked_declarations``) where
    each base declares it once: a property of the site is its own declaration, which the class writes already; another
    is the first declaration, where every declaration writes the same keywords but for check keywords and
    documentation and none holds a subschema, an ``enum`` or a reference, so the class writes the type its bases
    write and builds no model of its own.
    """
    own = site.properties or {}
    properties: dict[str, tuple[JsonSchemaObject, bool]] = {}
    for name in dict.fromkeys(name for declared, _ in declarations for name in declared):
        entries = [declared[name] for declared, _ in declarations if name in declared]
        if len(entries) == 1 or None in entries:
            continue
        schemas = cast("list[JsonSchemaObject]", entries)
        if (site_declaration := own.get(name)) is not None:
            schemas = [
                cast("JsonSchemaObject", site_declaration),
                *(entry for entry in schemas if entry is not site_declaration),
            ]
        elif any(keyword in (written := _without_checks(schemas[0])) for keyword in _MODEL_BUILDING_KEYWORDS) or any(
            not json_values_equal(_without_checks(other), written) for other in schemas[1:]
        ):
            continue
        if (checked := checked_declarations(parser, name, schemas)) is not None:
            properties[name] = checked[0], any(name in required for _, required in declarations)
    return properties


def _without_checks(declaration: JsonSchemaObject) -> dict[str, Any]:
    """Return the dump of a declaration without its check keywords and documentation."""
    return _dump_without_keywords(declaration, _CHECK_KEYWORDS | _DOCUMENTATION_KEYWORDS)


def get_inherited_property_map(
    parser: InheritedSchemasHost,
    base_classes: list[Reference],
) -> dict[str, tuple[JsonSchemaObject | bool, str]]:
    """Build the effective raw property map once in inherited C3 order.

    The declarations of a property that several inline allOf members of an inherited schema declare are read as its
    model reads them (``property_conjunctions``); the parse of that schema reports their warnings.
    """
    properties: dict[str, tuple[JsonSchemaObject | bool, str]] = {}
    for ref in linearize_inherited_schema_refs(parser, base_classes):
        schema, *members = inline_all_of_parts(parser, load_inherited_schema_object(parser, ref))
        with quiet():
            conjunctions = property_conjunctions(parser, schema)
        for name, prop_schema in (schema.properties or {}).items():
            properties.setdefault(name, (prop_schema, ref))
        written: Counter[str] = Counter()
        for source in members:
            for name, prop_schema in (
                with_conjoined_properties(source, conjunctions, written).properties or {}
            ).items():
                properties.setdefault(name, (prop_schema, ref))
    return properties


def get_inherited_required_names(parser: InheritedSchemasHost, base_classes: list[Reference]) -> frozenset[str]:
    """Collect required names across the effective inherited allOf graph."""
    linearized_refs = linearize_inherited_schema_refs(parser, base_classes)
    if linearized_refs in parser._inherited_required_cache:  # noqa: SLF001
        return parser._inherited_required_cache[linearized_refs]  # noqa: SLF001
    result = parser._inherited_required_cache[linearized_refs] = _required_names(parser, linearized_refs)  # noqa: SLF001
    return result


def required_names_at(parser: InheritedSchemasHost, resolved_ref: str) -> frozenset[str]:
    """Return the names the schema at a reference key requires, with its inline allOf members and its ancestors.

    These are the names ``get_inherited_required_names`` reads for the model of that schema as a base.
    """
    return _required_names(parser, (resolved_ref, *get_inherited_schema_ancestor_paths(parser, resolved_ref)))


def _required_names(parser: InheritedSchemasHost, refs: Iterable[str]) -> frozenset[str]:
    """Return the names the schemas at reference keys ``refs`` require, with their inline allOf members."""
    return frozenset(
        name
        for ref in refs
        for source in inline_all_of_parts(parser, load_inherited_schema_object(parser, ref))
        for name in source.required
    )


def get_inline_required_names(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> tuple[str, ...]:
    """Collect required names in declaration order from one schema and inline allOf items."""
    return tuple(dict.fromkeys(name for source in inline_all_of_parts(parser, schema) for name in source.required))


def get_inline_property_names(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> frozenset[str]:
    """Collect properties declared directly by one schema and its inline allOf items."""
    return frozenset(name for source in inline_all_of_parts(parser, schema) for name in source.properties or ())


def get_inherited_field_owner_reference(
    parser: InheritedSchemasHost,
    reference: Reference,
    schema: JsonSchemaObject,
) -> Reference:
    """Apply the same title-based class scope as normal object parsing."""
    if schema.allOf or (owner_name := parser._apply_title_as_name(reference.name, schema)) == reference.name:  # noqa: SLF001
        return reference

    source_path, marker, fragment = reference.path.partition("#")
    if not marker:  # pragma: no cover
        return reference
    path = [f"{source_path}#", *fragment.removeprefix("/").split("/")]
    return parser.model_resolver.add(
        path,
        owner_name,
        class_name=True,
        loaded=reference.loaded,
    )


def get_inherited_property_order(parser: InheritedSchemasHost, base_classes: list[Reference]) -> tuple[str, ...]:
    """Return raw inherited property names in ancestor-first declaration order."""
    ordered_names: dict[str, None] = {}
    visited_refs: set[_TargetNode] = set()

    def collect_ref(resolved_ref: str) -> None:
        if _is_special_path(resolved_ref):
            return
        schema = load_inherited_schema_object(parser, resolved_ref)
        if (node := _schema_node(schema, resolved_ref)) in visited_refs:
            return
        visited_refs.add(node)
        parent_refs = get_allof_parent_references(
            parser,
            schema,
            defining_ref=resolved_ref,
        )
        for parent in sort_inherited_base_classes_for_mro(parser, parent_refs):
            collect_ref(parent.path)
        for source in inline_all_of_parts(parser, schema, members_first=True):
            ordered_names.update(dict.fromkeys(source.properties or {}))

    for base in sort_inherited_base_classes_for_mro(parser, base_classes):
        collect_ref(base.path)
    return tuple(ordered_names)


def inferred_module_name(parser: InheritedSchemasHost, name: str) -> str:
    """Return the module a dotted model name places its model in (``get_inferred_module_name``)."""
    return get_inferred_module_name(
        name,
        treat_dot_as_module=parser.treat_dot_as_module,
        strict_dotted_module_names=parser.strict_dotted_module_names,
        target_python_version=parser.target_python_version,
    )


def parse_fields_beside_parents(
    parser: InheritedSchemasHost,
    source: JsonSchemaObject,
    parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]],
    path: list[str],
    scope: ClassScope,
) -> list[DataModelFieldBase]:
    """Parse the properties of ``source`` for the class ``scope``, merged with the parent properties they override.

    A property that only narrows its parent's is parsed without the nested types the parent's type replaces
    and marked for that late resolution.
    """
    deferred_names = get_deferred_inherited_property_names(parser, source, parent_properties)
    merged = merge_properties_with_parent_constraints(parser, source, parent_properties, deferred_names)
    fields = parser.parse_object_fields(
        get_deferred_inherited_parse_object(parser, merged, deferred_names),
        path,
        inferred_module_name(parser, scope.name),
        class_name=scope.name,
        output_names=scope.output_names,
    )
    mark_partial_inherited_fields(parser, fields, deferred_names, merged)
    return fields


def parse_inherited_schema_fields(
    parser: InheritedSchemasHost,
    reference: Reference,
    schema: JsonSchemaObject,
    parent_refs: list[Reference],
    parent_fields: list[DataModelFieldBase],
) -> list[DataModelFieldBase]:
    """Parse one forward schema's own fields in its defining scope, at its own path.

    The inline models the fields name are the ones the schema's own parse builds, under the same names.
    """
    inherited_properties = get_inherited_property_map(parser, parent_refs)
    reference = get_inherited_field_owner_reference(parser, reference, schema)
    scope = parser.model_resolver.class_scope("reference-name", reference, reference.path)
    fields: list[DataModelFieldBase] = []
    owner_path = [] if "legacy-rw-unowned-parent-fields" in parser._legacy_rw_variants else [reference.path]  # noqa: SLF001

    _, *members = inline_all_of_parts(parser, schema)
    written: Counter[str] = Counter()
    with (
        parser._inherited_ref_context(reference.path),  # noqa: SLF001
        context_variable(
            partial(setattr, parser, "_parses_unowned_fields"),
            parser._parses_unowned_fields,  # noqa: SLF001
            parser._parses_unowned_fields or not owner_path,  # noqa: SLF001
        ),
    ):
        conjunctions = property_conjunctions(parser, schema)
        for source in (*(with_conjoined_properties(member, conjunctions, written) for member in members), schema):
            if not source.properties:
                continue
            parsed_fields = parse_fields_beside_parents(parser, source, inherited_properties, owner_path, scope)
            _rw_split.unregister_temporary_field_references(parser, parsed_fields)
            fields.extend(parsed_fields)
    if not parser.force_optional_for_required_fields:
        existing_names = {field_name for field in fields if (field_name := _field_source_name(field)) is not None}
        inherited_fields = {
            field_name: field for field in parent_fields if (field_name := _field_source_name(field)) is not None
        }
        for required_name in get_inline_required_names(parser, schema):
            if required_name in existing_names:
                continue
            field = build_missing_required_field(
                parser,
                required_name,
                excludes={field.name for field in fields if field.name},
                base_classes=parent_refs,
                scope=scope,
                inherited_fields=inherited_fields,
            )
            fields.append(field)
            existing_names.add(required_name)
    return fields


def iter_inherited_schema_objects(
    parser: InheritedSchemasHost, base_classes: list[Reference], visited: frozenset[_TargetNode]
) -> Iterator[tuple[JsonSchemaObject, frozenset[_TargetNode], str]]:
    """Yield inherited schema objects not visited yet, with the visited schema nodes they add to."""
    for base in sort_inherited_base_classes_for_mro(parser, base_classes):
        if not base.path:  # pragma: no cover
            continue
        resolved_ref = base.path
        try:
            parent_schema = load_inherited_schema_object(parser, resolved_ref)
        except RecursionError:  # pragma: no cover
            raise
        except Exception:  # pragma: no cover  # noqa: BLE001, S112
            continue
        if (node := _schema_node(parent_schema, resolved_ref)) in visited:  # pragma: no cover
            continue
        yield parent_schema, visited | {node}, resolved_ref


def load_inherited_schema_object(parser: InheritedSchemasHost, resolved_ref: str) -> JsonSchemaObject:
    """Load and cache a schema used only for inherited field inspection."""
    if cached := parser._inherited_schema_cache.get(resolved_ref):  # noqa: SLF001
        return cached
    schema = _schema_refs.shared_schema_object(parser, parser._get_ref_raw_schema(resolved_ref), [resolved_ref])  # noqa: SLF001
    parser._inherited_schema_cache[resolved_ref] = schema  # noqa: SLF001
    return schema


def get_generated_base_models(parser: InheritedSchemasHost, base_classes: list[Reference]) -> list[DataModel]:
    """Resolve generated direct bases without traversing their fields."""
    data_models: list[DataModel] = []
    for base in base_classes:
        data_model = base.source if isinstance(base.source, DataModel) else None
        if data_model is None:
            data_model = next((result for result in parser.results if result.reference.path == base.path), None)
        if data_model is not None:
            data_models.append(data_model)
    return data_models


def get_inherited_field_map(
    parser: InheritedSchemasHost, base_classes: list[Reference]
) -> dict[str, DataModelFieldBase]:
    """Build the effective generated field map once for a set of direct bases."""
    inherited_fields = get_inherited_fields(get_generated_base_models(parser, base_classes))
    deferred_names = {
        name
        for name, field in inherited_fields.items()
        if _DEFERRED_INHERITED_TYPE_KEY in field.__dict__ or _DEFERRED_INHERITED_FIELD_KEY in field.__dict__
    }
    if not deferred_names:
        return inherited_fields

    for field in _rw_split.collect_inherited_fields_for_request_response(parser, base_classes):
        if (field_name := _field_source_name(field)) in deferred_names:
            inherited_fields[field_name] = field
    return inherited_fields


def get_inherited_field(
    parser: InheritedSchemasHost,
    prop_name: str,
    base_classes: list[Reference],
    inherited_fields: dict[str, DataModelFieldBase] | None = None,
) -> DataModelFieldBase | None:
    """Get an inherited generated field from parsed base models."""
    return (inherited_fields if inherited_fields is not None else get_inherited_field_map(parser, base_classes)).get(
        prop_name
    )


def get_inherited_field_schema(
    parser: InheritedSchemasHost, prop_name: str, base_classes: list[Reference]
) -> JsonSchemaObject | None:
    """Get the schema for an inherited property from parent schemas."""

    def find_property(schema: JsonSchemaObject, current_visited: frozenset[_TargetNode]) -> JsonSchemaObject | None:
        if schema.properties and isinstance(
            property_schema := schema.properties.get(prop_name),
            JsonSchemaObject,
        ):
            return property_schema

        for item in schema.allOf:
            if (
                isinstance(item, JsonSchemaObject)
                and not item.ref
                and (inline_property := find_property(item, current_visited))
            ):
                return inline_property

        for item in schema.allOf:
            if not isinstance(item, JsonSchemaObject) or not item.ref:
                continue
            resolved_ref = _schema_refs.ref_path(parser, item.ref)
            parent_schema = load_inherited_schema_object(parser, resolved_ref)
            if (node := _schema_node(parent_schema, resolved_ref)) in current_visited:
                continue
            if property_schema := find_property(parent_schema, current_visited | {node}):
                return property_schema
        return None

    for parent_schema, next_visited, _ in iter_inherited_schema_objects(parser, base_classes, frozenset()):
        if property_schema := find_property(parent_schema, next_visited):
            return property_schema

    return None


def build_missing_required_field(  # noqa: PLR0913, PLR0917
    parser: InheritedSchemasHost,
    required_field_name: str,
    excludes: set[str],
    base_classes: list[Reference],
    scope: ClassScope,
    inherited_fields: dict[str, DataModelFieldBase] | None = None,
) -> DataModelFieldBase:
    """Build a field for a required name that is not declared in properties.

    A copy of the inherited field keeps that field's origin; a new field records the origin ``required-only``.
    """
    field_name, alias = parser.model_resolver.get_valid_field_name_and_alias(
        required_field_name,
        excludes=excludes,
        model_type=parser.field_name_model_type,
        class_name=scope.name,
        output_names=scope.output_names,
    )
    inherited_field = get_inherited_field(parser, required_field_name, base_classes, inherited_fields)
    if inherited_field is not None and inherited_field.name and inherited_field.name not in excludes:
        field_name = inherited_field.name
    if inherited_field is not None:
        copied_field = parser._copy_model_field(inherited_field, required=True)  # noqa: SLF001
        parser._take_field_aliases(  # noqa: SLF001
            copied_field,
            field_name,
            alias,
            source_name=required_field_name,
            scope=scope,
            row="required-copy",
            inherits=bool(base_classes),
        )
        _rw_split.prepare_required_inherited_field(parser, copied_field, inherited_field)
        parser._apply_inherited_field_default(copied_field, inherited_field, scope=scope)  # noqa: SLF001
        return copied_field

    deferred = get_inherited_field_schema(parser, required_field_name, base_classes) is not None
    field = parser._build_field(  # noqa: SLF001
        "legacy-alias-option-only",
        parser.data_type()
        if deferred
        else parser._get_inherited_field_type(required_field_name, base_classes)  # noqa: SLF001
        or untyped_value_data_type(DataType, parser.data_type_manager.untyped_value),
        name=field_name,
        alias=alias,
        source_name=required_field_name,
        scope=scope,
        required=True,
        original_name=required_field_name,
    )
    with_field_origin(field, "required-only", None)
    if deferred:
        field.__dict__[_DEFERRED_INHERITED_FIELD_KEY] = scope
    return field
