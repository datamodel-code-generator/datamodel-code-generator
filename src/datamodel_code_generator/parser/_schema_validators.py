"""The schema validator rules that ``--generate-schema-validators`` records for a model of the JSON Schema parser.

The functions read a schema object and its allOf sources and register the runtime validation rules of a model
(required-property groups, if/then/else, property counts, ``uniqueItems`` paths and patternProperties) in the template
data of its reference path. They take the parser through ``SchemaValidatorsHost``; ``JsonSchemaParser`` keeps a
method for each one that tests or other parser code call.
"""

from __future__ import annotations

import math
import re
from functools import partial
from itertools import chain
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeAlias, cast

from typing_extensions import Protocol

from datamodel_code_generator._alias_generators import generate_alias
from datamodel_code_generator._constraint_applicability import constrained_type_keyword_order
from datamodel_code_generator._container_kinds import ARRAYS, ITEM_CONTAINERS, MAPPINGS
from datamodel_code_generator._json_pointer import document_part
from datamodel_code_generator._json_schema import MODELED_SCHEMA_MAPS, SCHEMA_MAP_KEYS, admitted_types, schema_positions
from datamodel_code_generator._regex_syntax import python_rejects
from datamodel_code_generator.enums import AliasGenerator
from datamodel_code_generator.model import DataModel
from datamodel_code_generator.model.base import _field_source_name
from datamodel_code_generator.model.runtime_validation import (
    UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP,
    ConditionalBoundsRule,
    ConditionalRequiredRule,
    ConstantTypesRule,
    ContainsRule,
    IndependentDeclaredPatternPropertiesRule,
    IndependentModelPatternPropertiesRule,
    PatternPropertiesRule,
    PropertyCountRule,
    RequiredGroupsRule,
    SchemaRuntimeValidation,
    UniqueItemsRule,
    _is_internal_schema_runtime_validation,
    conditional_value_uses_json_equality,
)
from datamodel_code_generator.parser import _inherited_schemas, _schema_refs, _value_keywords
from datamodel_code_generator.parser._inherited_schemas import InheritedSchemasHost
from datamodel_code_generator.parser._intersection import (
    INTERSECTED_KEYWORDS,
    bounds_admit_no_value,
    declared_types,
    intersect_enum_values,
)
from datamodel_code_generator.parser._intersection import json_values_equal as _json_literal_values_equal
from datamodel_code_generator.parser._naming import composed_name
from datamodel_code_generator.parser._passes._shared import (
    CollapseRootModelsHost,
    DataModelTypeHost,
    TargetPythonVersionHost,
)
from datamodel_code_generator.parser._schema_object import _ANNOTATION_KEYWORDS, JsonSchemaObject, _is_false_all_of
from datamodel_code_generator.parser._schema_reading import (
    _constraint_values,
    _intersect_dumps,
    _is_object_only_type,
    _json_value_admitted,
    _schema_dump,
)
from datamodel_code_generator.parser.base import get_special_path
from datamodel_code_generator.python_literal import stable_repr
from datamodel_code_generator.types import DataType, Types

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from typing_extensions import TypeIs

    from datamodel_code_generator.config import JSONSchemaParserConfig
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.model.runtime_validation import (
        BoundKeyword,
        Condition,
        ConditionalBounds,
        ContainsMatcher,
        UniqueItemsPath,
        UniqueItemsPathStep,
    )
    from datamodel_code_generator.parser._schema_graph import _TargetNode
    from datamodel_code_generator.reference import Reference

__all__ = [
    "SchemaValidatorsHost",
    "add_conditional_bounds_validator",
    "add_conditional_validator",
    "add_group_validators",
    "add_path_rules_validator",
    "add_pattern_properties_validator",
    "add_property_count_validator",
    "add_required_groups_validator",
    "add_schema_validators",
    "data_type_may_accept_array",
    "declared_pattern_rejects_value",
    "field_alias_generator_input_name",
    "field_input_names",
    "get_common_executable_unique_items_paths",
    "get_conditional_schema",
    "get_executable_unique_items_paths",
    "get_input_names_by_property",
    "get_not_required_groups",
    "get_property_count_rule",
    "get_required_groups",
    "get_union_path_branch_rules",
    "has_conditional_validator",
    "has_core_schema_runtime_rules",
    "has_pattern_properties_validator",
    "has_property_count_validator",
    "has_required_group_validators",
    "has_schema_validator_constraints",
    "iter_conditional_branches",
    "iter_path_rules",
    "iter_property_count_validation_sources",
    "iter_schema_validation_sources",
    "iter_scoped_schema_validation_sources",
    "iter_union_path_rules",
    "pattern_rejects_declared_value",
    "pattern_rule_additional_properties",
    "pattern_validation_intersection",
    "pattern_validation_loses_raw_value",
    "pattern_validation_with_undeclared_required",
    "plain_pattern_object",
    "plain_pattern_value_type",
    "required_group_input_names",
    "rule_rejects_pattern",
    "schema_item_may_accept_container",
    "schema_literal_values",
    "schema_may_accept_container",
    "should_parse_object_with_schema_validators",
    "unique_items_path_container_type",
]


class SchemaValidatorsHost(
    InheritedSchemasHost, CollapseRootModelsHost, DataModelTypeHost, TargetPythonVersionHost, Protocol
):
    """The members of ``JsonSchemaParser`` the schema validator rules read: options, state and the methods they call."""

    CONDITIONAL_SCHEMA_ALLOWED_FIELDS: ClassVar[frozenset[str]]
    REQUIRED_ONLY_SCHEMA_ALLOWED_FIELDS: ClassVar[frozenset[str]]
    _pattern_validation_document_root: JsonSchemaObject | None
    ignore_enum_constraints: bool
    _ref_constant_values_cache: dict[int, tuple[JsonSchemaObject, tuple[object, ...] | None]]
    _root_pattern_string_constraints: JsonSchemaObject | None

    def parse_item(
        self,
        name: str,
        item: JsonSchemaObject,
        path: list[str],
        singular_name: bool = False,  # noqa: FBT001, FBT002
        parent: JsonSchemaObject | None = None,
    ) -> DataType:
        """Parse a schema into the data type of a field or a value."""

    def _add_conditional_validator(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        names_by_property: dict[str, tuple[str, ...]],
    ) -> None:
        """Register the if/then/else required-property rule of ``obj``, deduplicated by value."""

    def _add_property_count_validator(self, reference_path: str, obj: JsonSchemaObject) -> None:
        """Add raw-object property-count rules from this schema and its allOf sources."""

    def _add_required_groups_validator(
        self,
        reference_path: str,
        keyword: Literal["anyOf", "oneOf", "not"],
        groups: Sequence[Sequence[str]],
        names_by_property: dict[str, tuple[str, ...]],
    ) -> None:
        """Register a required-property group runtime rule for keyword, deduplicated by value."""

    def _add_path_rules_validator(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
        *,
        is_root_model: bool,
    ) -> None:
        """Register the ``uniqueItems`` and the constant checks as compact paths through raw input data."""

    def _collect_pattern_property_validators(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> tuple[list[tuple[str, DataType]], list[str], DataType | None, bool]:
        """Return the pattern value types and rejected patterns, the additionalProperties type and unmatched keys."""

    def _data_type_may_accept_array(
        self,
        data_type: DataType,
        visited: frozenset[int] = frozenset(),
    ) -> bool:
        """Return whether a generated type can receive an array from raw input."""

    def _field_input_names(self, field: DataModelFieldBase) -> tuple[str, ...]:
        """Return Pydantic's ordered raw input names for one declared field."""

    def _get_common_executable_unique_items_paths(
        self,
        data_types: Sequence[DataType],
        path: UniqueItemsPath,
        visited: set[int],
        *,
        filter_array_branches: bool = True,
    ) -> set[UniqueItemsPath]:
        """Return paths every branch delegates to an executable validator."""

    def _get_executable_unique_items_paths(
        self,
        data_type: DataType,
        path: UniqueItemsPath,
        visited: set[int] | None = None,
    ) -> set[UniqueItemsPath]:
        """Return raw paths delegated by every applicable generated type branch."""

    def _get_input_names_by_property(
        self,
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
    ) -> dict[str, tuple[str, ...]]:
        """Return the raw input names of each property that the fields and the base classes declare."""

    def _get_property_count_rule(self, obj: JsonSchemaObject) -> PropertyCountRule | None:
        """Aggregate one immutable count rule and cache it for this parse only."""

    def _has_property_count_validator(self, obj: JsonSchemaObject) -> bool:
        """Return whether an object or its allOf sources constrain property counts."""

    def _is_required_only_schema(
        self,
        item: JsonSchemaObject | bool,  # noqa: FBT001
        *,
        allow_empty: bool = False,
    ) -> TypeIs[JsonSchemaObject]:
        """Return whether a combined-schema branch is only a property presence rule."""

    def _parse_additional_properties_value(
        self,
        name: str,
        path: list[str],
        parent: JsonSchemaObject,
        *,
        additional_properties: JsonSchemaObject,
        constrained_name: str | None = None,
    ) -> DataType:
        """Parse a mapping value schema while preserving supported Annotated constraints."""

    def _pattern_value_alias(
        self, name: str, schema: JsonSchemaObject, value_type: DataType, path: list[str]
    ) -> DataType:
        """Return the type alias that writes a patternProperties value with its constraints, else ``value_type``."""

    def _plain_pattern_root_annotations(self, obj: JsonSchemaObject, patterns: list[tuple[str, DataType]]) -> bool:
        """Recognize inert root annotations only in the standard generation context."""

    def _required_group_input_names(
        self,
        groups: Sequence[Sequence[str]],
        names_by_property: dict[str, tuple[str, ...]],
    ) -> tuple[tuple[tuple[str, ...], ...], ...]:
        """Return each group of property names as the raw input names of its properties."""

    def _schema_item_may_accept_container(
        self,
        item: JsonSchemaObject | bool,  # noqa: FBT001
        container_type: Literal["array", "object"],
        visited_refs: frozenset[_TargetNode],
    ) -> bool:
        """Return whether a schema item can accept one raw JSON container shape."""

    def _schema_runtime_validation(self, reference_path: str) -> SchemaRuntimeValidation:
        """Return the runtime validation rules of ``reference_path``, created on first use."""

    def _unique_items_path_container_type(
        self,
        path: UniqueItemsPath,
    ) -> Literal["array", "object"]:
        """Return the raw container shape needed for one relative uniqueItems path: an array for an item step."""


_IMPLIED_SUBSCHEMA_KEYWORDS = frozenset({"items", "propertyNames"})
_CONSTANT_TYPES = frozenset({"boolean", "integer", "number"})
_PathRule: TypeAlias = UniqueItemsRule | ConstantTypesRule | ContainsRule
_BOUND_KEYWORDS: tuple[BoundKeyword, ...] = ("minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum")
_CONTAINS_STRING_KEYWORDS = ("pattern", "minLength", "maxLength")
_CONTAINS_NUMBER_KEYWORDS = (*_BOUND_KEYWORDS, "multipleOf")
_CONTAINS_MATCHER_KEYWORDS = frozenset({
    "type",
    "const",
    "enum",
    *_CONTAINS_STRING_KEYWORDS,
    *_CONTAINS_NUMBER_KEYWORDS,
})


_UNMATCHED_SCHEMA_KEYWORDS = frozenset(schema_positions("unmatched"))


def customizes_models(config: JSONSchemaParserConfig) -> bool:
    """Return whether the run customizes the models: a base class, a base class map, custom templates or template data.

    The validators that rewrite a model's rules keep the generic helpers then.
    """
    return (
        any((config.base_class, config.base_class_map, config.custom_template_dir))
        or config.extra_template_data is not None
    )


def _schema_keywords(schema: object) -> dict[str, Any] | None:
    """Return the dumped keywords of a schema that assert something (``true`` as ``{}``), or None for ``false``."""
    if schema is True:
        return {}
    if not isinstance(schema, dict):
        return None
    return {key: value for key, value in schema.items() if key not in _ANNOTATION_KEYWORDS}


def _writes_reference(keywords: dict[str, Any]) -> bool:
    """Return whether dumped schema keywords write a reference, whose target depends on the document read."""
    return any(keyword in keywords for keyword in ("$ref", "$dynamicRef", "$recursiveRef"))


def _schema_implies(declared: object, other: object, *, same_document: bool) -> bool:
    """Return whether every value the dumped schema ``declared`` accepts is shown to meet the dumped schema ``other``.

    ``intersect_keywords`` decides the keywords it intersects (bounds, ``multipleOf``, ``uniqueItems``, ``required``,
    ``pattern``, ``type``, null and ``enum``): ``other`` adds nothing there when the intersection is ``declared``'s
    own value. ``items``, ``propertyNames`` and the schemas of ``properties`` and ``patternProperties`` follow this rule
    recursively, and ``additionalProperties`` where both name the same properties and patterns. ``other`` adds
    nothing with any other keyword applied to the values the others leave (``additionalItems``,
    ``unevaluatedItems``, ``unevaluatedProperties``) only when it is ``true``, and with every other keyword only
    when ``declared`` writes the same value. A schema whose own bounds admit no value adds that to ``declared``,
    and a ``$ref`` of ``other`` is the same reference only when both schemas are read from one document
    (``same_document``).
    """
    if (other_keywords := _schema_keywords(other)) == {} or declared is False:
        return True
    if other_keywords is None or (declared_keywords := _schema_keywords(declared)) is None:
        return False
    if (not same_document and _writes_reference(other_keywords)) or (
        bounds_admit_no_value(other_keywords) and not bounds_admit_no_value(declared_keywords)
    ):
        return False
    intersection = _intersect_dumps([declared_keywords, other_keywords], {**other_keywords, **declared_keywords})
    return (
        not intersection.unsatisfiable
        and not intersection.untaken_patterns
        and intersection.keywords == declared_keywords
        and all(
            _keyword_implies(keyword, declared_keywords, other_keywords, same_document=same_document)
            for keyword in other_keywords
            if keyword not in INTERSECTED_KEYWORDS
        )
    )


def _keyword_implies(keyword: str, declared: dict[str, Any], other: dict[str, Any], *, same_document: bool) -> bool:
    """Return whether the value of one keyword of ``other``, which ``declared`` also writes, adds no assertion."""
    declared_value = declared[keyword]
    other_value = other[keyword]
    if keyword in SCHEMA_MAP_KEYS and isinstance(other_value, dict):
        return isinstance(declared_value, dict) and all(
            _schema_implies(declared_value[name], schema, same_document=same_document)
            if name in declared_value
            else _schema_keywords(schema) == {}
            for name, schema in other_value.items()
        )
    if keyword in _IMPLIED_SUBSCHEMA_KEYWORDS and not isinstance(other_value, list):
        return _schema_implies(declared_value, other_value, same_document=same_document)
    if keyword in _UNMATCHED_SCHEMA_KEYWORDS:
        return _schema_keywords(other_value) == {} or (
            keyword == "additionalProperties"
            and all(set(declared.get(names) or ()) == set(other.get(names) or ()) for names in MODELED_SCHEMA_MAPS)
            and _schema_implies(declared_value, other_value, same_document=same_document)
        )
    if keyword == JsonSchemaObject.__extra_key__ and isinstance(other_value, dict):
        return isinstance(declared_value, dict) and all(
            key in declared_value and _json_literal_values_equal(declared_value[key], value)
            for key, value in other_value.items()
        )
    return _json_literal_values_equal(declared_value, other_value)


def _searches_any(pattern: str, names: Iterable[str]) -> bool:
    """Return whether ``pattern`` searches one of ``names`` as the generated validator does; an invalid one, none."""
    try:
        return any(re.search(pattern, name) for name in names)
    except re.error:
        return False


def _is_empty_property_schema(item: JsonSchemaObject, *, allowed_keywords: frozenset[str] = frozenset()) -> bool:
    """Return whether a schema contains only metadata and explicitly handled keywords."""
    if item.dynamicRef is not None or item.recursiveRef is not None:
        return False
    other_fields = item.model_fields_set - item.__metadata_only_fields__ - {"extras"} - allowed_keywords
    if other_fields:
        return False
    return not any(
        key not in item.__metadata_only_fields__ and key not in allowed_keywords and not key.startswith("x-")
        for key in item.extras
    )


def _get_conditional_property_values(item: JsonSchemaObject | bool | None) -> tuple[object, ...] | None:  # noqa: FBT001
    """Extract a presence predicate or literals without discarding property constraints.

    The literals are the values that ``const``, ``enum`` and ``type`` all admit: a ``const`` beside an ``enum`` is the
    value when the enum holds it. A property that admits no value never matches, so the predicate has none (None).
    """
    if item is None or item is True:
        return ()
    if not isinstance(item, JsonSchemaObject):
        return None
    declared = {
        keyword: values
        for keyword, values in (
            ("const", (item.extras["const"],) if "const" in item.extras else ()),
            ("enum", tuple(item.enum or ())),
        )
        if values
    }
    if not declared:
        return () if _is_empty_property_schema(item) else None
    if not _is_empty_property_schema(item, allowed_keywords=frozenset({"type", *declared})):
        return None
    first, *others = declared.values()
    values = tuple(intersect_enum_values(first, others[0]) if others else first)
    if item.type is not None:
        schema_types = admitted_types(declared_types(item.type))
        values = tuple(value for value in values if _json_value_admitted(schema_types, value))
    return values or None


def _input_names(names_by_property: Mapping[str, tuple[str, ...]] | None, name: str) -> tuple[str, ...]:
    """Return the raw input names of a property, the property name itself where ``names_by_property`` has none."""
    return (name,) if names_by_property is None else names_by_property.get(name, (name,))


def get_required_groups(
    parser: SchemaValidatorsHost, items: Sequence[JsonSchemaObject | bool]
) -> tuple[tuple[str, ...], ...]:
    """Return the property names each item requires, or nothing unless every item only requires properties."""
    if not items:
        return ()

    groups: list[tuple[str, ...]] = []
    for item in items:
        if not parser._is_required_only_schema(item):
            return ()
        groups.append(tuple(item.required))
    return tuple(groups)


def get_not_required_groups(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> tuple[tuple[str, ...], ...]:
    """Return the required-only property group `not` forbids, or an empty tuple."""
    not_schema = get_conditional_schema(parser, obj, "not")
    if not_schema is None or not parser._is_required_only_schema(not_schema, allow_empty=True):
        return ()
    return (tuple(not_schema.required),)


def has_required_group_validators(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether obj has a oneOf, anyOf, or not required-property group."""
    return bool(
        get_required_groups(parser, obj.oneOf)
        or get_required_groups(parser, obj.anyOf)
        or get_not_required_groups(parser, obj)
    )


def get_conditional_schema(
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    keyword: str,
) -> JsonSchemaObject | bool | None:
    """Return the schema of an applicator kept in extras, such as ``if``, which the schema graph validates once."""
    item = obj.extras.get(keyword)
    if isinstance(item, (JsonSchemaObject, bool)):
        return item
    if isinstance(item, dict):
        return _schema_refs.shared_object(parser, item, parser.SCHEMA_OBJECT_TYPE, partial(parser._schema_object, item))
    return None


def iter_conditional_branches(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> Iterator[JsonSchemaObject]:
    """Yield the ``then`` and ``else`` branches of ``obj`` that are schema objects."""
    for keyword in ("then", "else"):
        branch = get_conditional_schema(parser, obj, keyword)
        if isinstance(branch, JsonSchemaObject):
            yield branch


def has_conditional_validator(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether ``obj`` has a supported ``if`` and a ``then`` or ``else`` that requires properties."""
    return get_conditional_predicate(parser, obj) is not None and any(
        branch.required for branch in iter_conditional_branches(parser, obj)
    )


def has_schema_validator_constraints(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether ``obj`` has a required group, conditional, patternProperties or property-count rule."""
    return bool(
        has_required_group_validators(parser, obj)
        or has_conditional_validator(parser, obj)
        or parser._has_pattern_properties_validator(obj)
        or parser._has_property_count_validator(obj)
    )


def has_pattern_properties_validator(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether schema runtime validation owns patternProperties dispatch."""
    return parser.generate_schema_validators and any(
        source.patternProperties for source in parser._iter_schema_validation_sources(obj)
    )


def has_core_schema_runtime_rules(parser: SchemaValidatorsHost, runtime_validation: SchemaRuntimeValidation) -> bool:  # noqa: ARG001
    """Return whether rendering this model shadows an inherited core validator."""
    return bool(
        runtime_validation.pattern_properties
        or runtime_validation.required_groups
        or runtime_validation.conditional_required
        or runtime_validation.unique_items
    )


def has_property_count_validator(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether an object or its allOf sources constrain property counts."""
    return parser._get_property_count_rule(obj) is not None


def get_property_count_rule(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> PropertyCountRule | None:
    """Aggregate one immutable count rule and cache it for this parse only."""
    if not parser.generate_schema_validators:
        return None
    cache_key = id(obj)
    if (cached := parser._property_count_rule_cache.get(cache_key)) is not None and cached[0] is obj:
        return cached[1]

    min_properties: int | None = None
    max_properties: int | None = None
    for source in iter_property_count_validation_sources(parser, obj):
        match source.minProperties:
            case int() as source_minimum if source_minimum > 0:
                min_properties = source_minimum if min_properties is None else max(min_properties, source_minimum)
            case _:
                pass
        if (source_maximum := source.maxProperties) is not None:
            max_properties = source_maximum if max_properties is None else min(max_properties, source_maximum)
    rule = (
        PropertyCountRule(min_properties=min_properties, max_properties=max_properties)
        if min_properties is not None or max_properties is not None
        else None
    )
    parser._property_count_rule_cache[cache_key] = obj, rule
    return rule


def iter_property_count_validation_sources(
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    visited_refs: frozenset[_TargetNode] = frozenset(),
) -> Iterator[JsonSchemaObject]:
    """Yield count constraints from allOf, preserving JSON Schema $ref siblings.

    A ``$ref`` member yields its own count keywords where its schema version applies the keywords beside a ``$ref``
    (``_ref_siblings_apply_to``), as the other source walkers do; without --read-schema-version-per-document in every
    version (row ``legacy-count-ref-member-as-written``). They are the sources of
    ``iter_scoped_schema_validation_sources`` under that row, with each such member yielded as written.
    """
    for source, _ in iter_scoped_schema_validation_sources(
        parser, obj, visited_refs, ref_siblings="as-written", sibling_yield="member", track_scope=False
    ):
        yield source


def should_parse_object_with_schema_validators(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether ``obj`` is parsed as an object model that carries schema validator rules."""
    if not parser.generate_schema_validators:
        return False
    has_pattern_properties = bool(obj.patternProperties)
    if has_pattern_properties:
        return True
    if obj.properties and has_required_group_validators(parser, obj):
        return True
    if obj.properties and parser._has_property_count_validator(obj):
        return True
    has_conditional_properties = any(
        branch.properties or branch.patternProperties for branch in iter_conditional_branches(parser, obj)
    )
    return bool((obj.properties or has_conditional_properties) and has_conditional_validator(parser, obj))


def field_input_names(parser: SchemaValidatorsHost, field: DataModelFieldBase) -> tuple[str, ...]:
    """Return Pydantic's ordered raw input names for one declared field."""
    names: list[str] = []

    def append_name(value: str | None) -> None:
        if value is not None and value not in names:
            names.append(value)

    if field.validation_aliases:
        for alias in field.validation_aliases:
            append_name(alias)
    elif field.use_serialization_alias:
        append_name(field_alias_generator_input_name(parser, field))
    else:
        append_name(field.alias)
        if field.alias is None:
            append_name(field_alias_generator_input_name(parser, field))
    if (
        field.use_serialization_alias
        or field.alias is None
        or parser.config.allow_population_by_field_name
        or parser.config.alias_generator
    ):
        append_name(field.name)
    return tuple(names)


def field_alias_generator_input_name(parser: SchemaValidatorsHost, field: DataModelFieldBase) -> str | None:
    """Return the generated Pydantic validation alias for one field."""
    if field.name is None or not isinstance(generator := parser.config.alias_generator, AliasGenerator):
        return None
    return generate_alias(generator.value, field.name, parser.target_python_version)


def get_input_names_by_property(
    parser: SchemaValidatorsHost,
    fields: Sequence[DataModelFieldBase],
    base_classes: Sequence[Reference],
) -> dict[str, tuple[str, ...]]:
    """Return the raw input names of each property that the fields and the base classes declare."""
    names_by_property: dict[str, tuple[str, ...]] = {}
    for field in fields:
        if field.name == parser.data_model_type.TYPED_EXTRA_FIELD_NAME:
            continue
        property_name = _field_source_name(field)
        if property_name is not None:
            names_by_property[property_name] = parser._field_input_names(field)

    for base_class in base_classes:
        data_model = base_class.source if isinstance(base_class.source, DataModel) else None
        if data_model is not None:
            for field in data_model.iter_all_fields():
                if field.name == parser.data_model_type.TYPED_EXTRA_FIELD_NAME:
                    continue
                property_name = _field_source_name(field)
                if property_name is not None:
                    names_by_property.setdefault(property_name, parser._field_input_names(field))
            continue

        for schema, _, _ in _inherited_schemas.iter_inherited_schema_objects(parser, [base_class], frozenset()):
            if not schema.properties:
                continue
            for property_name in schema.properties:
                names_by_property.setdefault(property_name, (property_name,))

    return names_by_property


def required_group_input_names(
    parser: SchemaValidatorsHost,  # noqa: ARG001
    groups: Sequence[Sequence[str]],
    names_by_property: dict[str, tuple[str, ...]],
) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Return each group of property names as the raw input names of its properties."""
    return tuple(tuple(_input_names(names_by_property, property_name) for property_name in group) for group in groups)


def iter_schema_validation_sources(
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    visited_refs: frozenset[_TargetNode] = frozenset(),
    *,
    include_references: bool = True,
    sibling_yield: Literal["ref-less-copy", "applicator-copy"] = "ref-less-copy",
) -> Iterator[JsonSchemaObject]:
    for source, _ in iter_scoped_schema_validation_sources(
        parser, obj, visited_refs, include_references=include_references, sibling_yield=sibling_yield
    ):
        yield source


def _iter_all_of_reads(  # noqa: PLR0913
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    visited_refs: frozenset[_TargetNode],
    scope: str | None,
    *,
    include_references: bool,
    ref_siblings: Literal["as-written", "first-read"] | None,
    track_scope: bool,
) -> Iterator[tuple[JsonSchemaObject, frozenset[_TargetNode], str | None, bool]]:
    """Yield what the validator rules of ``obj`` read in its allOf, as (schema, visited refs, scope, is a target).

    In document order, each schema-object member, a ``$ref`` member only where ``ref_siblings`` reads the keywords
    beside its ``$ref`` (``_ref_siblings_apply_to`` with that row; None: always, its reader decides), then, with
    ``include_references``, the schema the ``$ref`` names unless the reference is mapped or visited
    (``unmapped_ref_target``). A target is read after the reader consumed the member before it. With ``track_scope``
    a target's scope is that of ``iter_scoped_schema_validation_sources``; without it a target keeps ``scope``.
    """
    for item in obj.allOf:
        if not isinstance(item, JsonSchemaObject):
            continue
        if not item.ref:
            yield item, visited_refs, scope, False
            continue
        if ref_siblings is None or parser._ref_siblings_apply_to(item, legacy=ref_siblings):
            yield item, visited_refs, scope, False
        if not include_references:
            continue
        target_scope = scope
        if track_scope:
            file_part = document_part(_schema_refs.ref_path(parser, item.ref))
            root_file = document_part(parser.model_resolver.resolve_ref("#"))
            target_scope = None if scope is None or scope else ("" if file_part == root_file else file_part)
        if (target := _schema_refs.unmapped_ref_target(parser, item.ref, visited_refs)) is not None:
            yield *target, target_scope, True


def iter_scoped_schema_validation_sources(  # noqa: PLR0913
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    visited_refs: frozenset[_TargetNode] = frozenset(),
    scope: str | None = "",
    *,
    include_references: bool = True,
    ref_siblings: Literal["as-written", "first-read"] = "first-read",
    sibling_yield: Literal["member", "ref-less-copy", "applicator-copy"] = "ref-less-copy",
    track_scope: bool = True,
) -> Iterator[tuple[JsonSchemaObject, str | None]]:
    """Yield the schema and its allOf sources with the document each was read from.

    The scope of a source is the file part of its resolved reference, ``""`` for the schema itself, and None
    when the document is not known: a reference followed inside another document resolves against the current
    one.

    The keywords beside an allOf ``$ref`` are read where ``ref_siblings`` applies them (``_iter_all_of_reads``), as
    ``sibling_yield`` says: ``ref-less-copy`` walks a copy of the member without its ``$ref`` and without following
    references where it has a keyword beside its ``$ref``; ``member`` yields the member as written (the property
    counts); row ``legacy-closing-ref-siblings-with-applicators`` (``applicator-copy``, what closes an allOf in
    ``parser/_all_of_values.py`` without ``--read-ref-siblings-in-closed-allof-members``) walks the copy only where the
    member has an applicator, ``if`` or a map keyword.
    Without ``track_scope`` every source keeps ``scope``.
    """
    yield obj, scope
    if not obj.allOf:
        return
    for node, node_visited_refs, node_scope, is_target in _iter_all_of_reads(
        parser,
        obj,
        visited_refs,
        scope,
        include_references=include_references,
        ref_siblings=ref_siblings,
        track_scope=track_scope,
    ):
        source, follows_references = node, include_references
        if node.ref and not is_target:
            if sibling_yield == "member":
                yield node, node_scope
                continue
            if node.model_fields_set == {"ref"}:
                continue
            has_sibling_constraints = (
                node.patternProperties
                or node.additionalProperties is not None
                or node.unevaluatedProperties is not None
            )
            if sibling_yield == "applicator-copy" and not (
                node.allOf or node.oneOf or node.anyOf or "if" in node.extras or has_sibling_constraints
            ):
                continue
            source, follows_references = node.model_copy(update={"ref": None}), False
        yield from iter_scoped_schema_validation_sources(
            parser,
            source,
            node_visited_refs,
            node_scope,
            include_references=follows_references,
            ref_siblings=ref_siblings,
            sibling_yield=sibling_yield,
            track_scope=track_scope,
        )


def schema_item_may_accept_container(
    parser: SchemaValidatorsHost,
    item: JsonSchemaObject | bool,  # noqa: FBT001
    container_type: Literal["array", "object"],
    visited_refs: frozenset[_TargetNode],
) -> bool:
    """Return whether a schema item can accept one raw JSON container shape."""
    if isinstance(item, JsonSchemaObject):
        return schema_may_accept_container(parser, item, container_type, visited_refs)
    return item


def schema_literal_values(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> tuple[object, ...] | None:
    """Return direct const/enum values after their JSON Schema intersection."""
    has_const = "const" in obj.extras and parser._features_at(obj).const_support
    const = obj.extras.get("const") if has_const else None
    has_enum = "enum" in obj.model_fields_set
    if not has_const and not has_enum:
        return None
    if not has_const:
        return tuple(obj.enum)
    if not has_enum or intersect_enum_values([const], obj.enum):
        return (const,)
    return ()


def schema_may_accept_container(
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    container_type: Literal["array", "object"],
    visited_refs: frozenset[_TargetNode],
) -> bool:
    """Return whether a schema can accept one raw JSON container shape."""
    if obj.is_boolean_schema_false:
        return False
    if obj.ref:
        accepts = (target := _schema_refs.unmapped_ref_target(parser, obj.ref, visited_refs)) is None or (
            schema_may_accept_container(parser, target[0], container_type, target[1])
        )
        if not (accepts and parser._ref_siblings_apply_to(obj, legacy="first-read")):
            return accepts
    if (values := parser._schema_literal_values(obj)) is not None and not any(
        isinstance(value, list if container_type == "array" else dict) for value in values
    ):
        return False
    if obj.type is not None and container_type not in declared_types(obj.type):
        return False

    if any(not parser._schema_item_may_accept_container(item, container_type, visited_refs) for item in obj.allOf):
        return False
    return all(
        not items or any(parser._schema_item_may_accept_container(item, container_type, visited_refs) for item in items)
        for items in (obj.anyOf, obj.oneOf)
    )


def unique_items_path_container_type(
    parser: SchemaValidatorsHost,  # noqa: ARG001
    path: UniqueItemsPath,
) -> Literal["array", "object"]:
    """Return the raw container shape needed for one relative uniqueItems path: an array for an item step."""
    if not path:
        return "array"
    match path[0]:
        case None | int():
            return "array"
        case (marker, int()) if marker == UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP:
            return "array"
    return "object"


def get_union_path_branch_rules(
    parser: SchemaValidatorsHost,
    items: Sequence[JsonSchemaObject | bool],
    path: UniqueItemsPath,
    visited_refs: frozenset[_TargetNode],
    property_input_names: Mapping[str, tuple[str, ...]] | None,
) -> tuple[list[tuple[JsonSchemaObject | bool, tuple[_PathRule, ...]]], list[_PathRule]]:
    """Collect ordered path rules produced by each union branch."""
    branch_rules: list[tuple[JsonSchemaObject | bool, tuple[_PathRule, ...]]] = []
    candidates: list[_PathRule] = []
    for item in items:
        if item is False:
            continue
        if item is True:
            branch_rules.append((item, ()))
            continue
        branch = item
        rules = tuple(
            iter_path_rules(parser, branch, path, visited_refs, property_input_names, include_properties=True)
        )
        if (
            branch.ref
            and not parser.collapse_root_models
            and (target := _schema_refs.unmapped_ref_target(parser, branch.ref, visited_refs))
        ):
            rules = (
                *rules,
                *iter_path_rules(parser, target[0], path, target[1], property_input_names, include_properties=True),
            )
        branch_rules.append((branch, rules))
        candidates.extend(rule for rule in rules if rule not in candidates)
    return branch_rules, candidates


def iter_union_path_rules(
    parser: SchemaValidatorsHost,
    items: Sequence[JsonSchemaObject | bool],
    path: UniqueItemsPath,
    visited_refs: frozenset[_TargetNode],
    *,
    property_input_names: Mapping[str, tuple[str, ...]] | None = None,
) -> Iterator[_PathRule]:
    """Yield paths unconditionally required by every shape-compatible union branch.

    The constants of the union itself are one rule over the values of every branch (``_union_constant_values``).
    """
    branch_rules, candidates = get_union_path_branch_rules(parser, items, path, visited_refs, property_input_names)

    for candidate in candidates:
        relative_path = candidate.path[len(path) :]
        if isinstance(candidate, ConstantTypesRule) and not relative_path:
            continue
        container_type = parser._unique_items_path_container_type(relative_path)
        for branch, rules in branch_rules:
            if not parser._schema_item_may_accept_container(branch, container_type, visited_refs):
                continue
            if candidate not in rules:
                break
        else:
            yield candidate
    if rule := _constant_types_rule(path, _union_constant_values(parser, items, visited_refs)):
        yield rule


def _rule_literal_values(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> tuple[object, ...] | None:
    """Return the const or enum values a constant rule checks; ``--ignore-enum-constraints`` leaves out the enum."""
    if not parser.ignore_enum_constraints:
        return parser._schema_literal_values(obj)
    if "const" in obj.extras and parser._features_at(obj).const_support:
        return (obj.extras["const"],)
    return None


def _ref_constant_values(
    parser: SchemaValidatorsHost, obj: JsonSchemaObject, visited_refs: frozenset[_TargetNode]
) -> tuple[object, ...] | None:
    """Return the const or enum values of the schema ``obj`` references, through references to references.

    Each referring schema object is read once in a parse (``_ref_constant_values_cache``).
    """
    if not obj.ref:
        return None
    if (cached := parser._ref_constant_values_cache.get(id(obj))) is not None and cached[0] is obj:
        return cached[1]
    values = None
    if (target := _schema_refs.unmapped_ref_target(parser, obj.ref, visited_refs)) is not None:
        values = _rule_literal_values(parser, target[0])
        if values is None:
            values = _ref_constant_values(parser, target[0], target[1])
    parser._ref_constant_values_cache[id(obj)] = obj, values
    return values


def _union_constant_values(
    parser: SchemaValidatorsHost, items: Sequence[JsonSchemaObject | bool], visited_refs: frozenset[_TargetNode]
) -> tuple[object, ...]:
    """Return the const and enum values of a union's branches, none where a branch may hold any boolean or number.

    A branch whose ``type`` admits neither a boolean nor a number, such as ``null``, holds none of them.
    """
    values: list[object] = []
    for item in items:
        if item is False:
            continue
        if item is True:
            return ()
        if (item_values := _rule_literal_values(parser, item)) is not None or (
            item_values := _ref_constant_values(parser, item, visited_refs)
        ) is not None:
            values.extend(item_values)
        elif item.ref or item.type is None or admitted_types(declared_types(item.type)) & _CONSTANT_TYPES:
            return ()
    return tuple(values)


def _same_json_value(value: object, other: object) -> bool:
    """Return whether two booleans or numbers are one JSON value: equal, and both booleans or both numbers."""
    return isinstance(value, bool) is isinstance(other, bool) and value == other


def _constant_types_rule(path: UniqueItemsPath, values: Iterable[object]) -> ConstantTypesRule | None:
    """Return the rule that rejects the other JSON type for ``values``, None where Python conflates none of them.

    It keeps the booleans and the numbers equal to one (``conditional_value_uses_json_equality``) and is written only
    where one of them has no value of the other type equal to it: ``[1, true]`` admits both ``1`` and ``true``.
    """
    kept: list[bool | float] = []
    for value in values:
        if (
            isinstance(value, int | float)
            and conditional_value_uses_json_equality(value)
            and not any(_same_json_value(value, other) for other in kept)
        ):
            kept.append(value)
    if all(any(value == other and not _same_json_value(value, other) for other in kept) for value in kept):
        return None
    return ConstantTypesRule(path=path, values=tuple(kept))


def get_common_executable_unique_items_paths(
    parser: SchemaValidatorsHost,
    data_types: Sequence[DataType],
    path: UniqueItemsPath,
    visited: set[int],
    *,
    filter_array_branches: bool = True,
) -> set[UniqueItemsPath]:
    """Return paths every branch delegates to an executable validator."""
    executable_data_types = (
        tuple(data_type for data_type in data_types if parser._data_type_may_accept_array(data_type))
        if filter_array_branches
        else data_types
    )
    if not executable_data_types:
        return set()
    common_paths = parser._get_executable_unique_items_paths(executable_data_types[0], path, visited)
    for data_type in executable_data_types[1:]:
        common_paths.intersection_update(parser._get_executable_unique_items_paths(data_type, path, visited))
        if not common_paths:
            return common_paths
    return common_paths


def data_type_may_accept_array(
    parser: SchemaValidatorsHost,
    data_type: DataType,
    visited: frozenset[int] = frozenset(),
) -> bool:
    """Return whether a generated type can receive an array from raw input."""
    data_type_id = id(data_type)
    if data_type_id in visited:
        return False
    visited |= {data_type_id}

    match data_type:
        case DataType() if data_type in ARRAYS:
            return True
        case DataType(reference=reference) if reference is not None and isinstance(reference.source, DataModel):
            source = reference.source
            return bool(
                source.IS_ROOT_MODEL
                and source.fields
                and parser._data_type_may_accept_array(source.fields[0].data_type, visited)
            )
        case DataType(data_types=data_types) if data_types:
            return any(parser._data_type_may_accept_array(child, visited) for child in data_types)
        case DataType(type="None" | "str" | "int" | "float" | "bool" | "bytes"):
            return False
        case _:
            pass
    return True


def get_executable_unique_items_paths(  # noqa: PLR0911, PLR0912
    parser: SchemaValidatorsHost,
    data_type: DataType,
    path: UniqueItemsPath,
    visited: set[int] | None = None,
) -> set[UniqueItemsPath]:
    """Return raw paths delegated by every applicable generated type branch."""
    if visited is None:
        visited = set()
    data_type_id = id(data_type)
    if data_type_id in visited:
        return set()
    visited.add(data_type_id)
    try:
        if data_type.reference and isinstance(data_type.reference.source, DataModel):
            source = data_type.reference.source
            runtime_validation = source.schema_runtime_validation
            if not _is_internal_schema_runtime_validation(runtime_validation):
                runtime_validation = parser.extra_template_data[data_type.reference.path].get(
                    "schema_runtime_validation"
                )
            if (
                not source.is_alias
                and source.SUPPORTS_SCHEMA_RUNTIME_VALIDATION
                and isinstance(runtime_validation, SchemaRuntimeValidation)
                and _is_internal_schema_runtime_validation(runtime_validation)
            ):
                if parser.collapse_root_models and source.IS_ROOT_MODEL:
                    return set()
                return {(*path, *rule.path) for rule in runtime_validation.unique_items}

        match data_type:
            case DataType() if data_type in ITEM_CONTAINERS:
                return parser._get_common_executable_unique_items_paths(
                    data_type.data_types,
                    (*path, None),
                    visited,
                    filter_array_branches=False,
                )
            case DataType(is_tuple=True, tuple_item_count=int() as item_count):
                owned_paths = cast("set[UniqueItemsPath]", set())
                for index, child in enumerate(data_type.data_types[:item_count]):
                    owned_paths.update(parser._get_executable_unique_items_paths(child, (*path, index), visited))
                return owned_paths
            case DataType(is_tuple=True):
                return parser._get_common_executable_unique_items_paths(
                    data_type.data_types,
                    (*path, None),
                    visited,
                    filter_array_branches=False,
                )
            case DataType() if data_type in MAPPINGS:
                return parser._get_common_executable_unique_items_paths(
                    data_type.data_types,
                    (*path, UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP),
                    visited,
                    filter_array_branches=False,
                )
            case _:
                pass
        return parser._get_common_executable_unique_items_paths(data_type.data_types, path, visited)
    finally:
        visited.remove(data_type_id)


def iter_path_rules(  # noqa: PLR0912, PLR0913
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    path: UniqueItemsPath,
    visited_refs: frozenset[_TargetNode] = frozenset(),
    property_input_names: Mapping[str, tuple[str, ...]] | None = None,
    *,
    include_properties: bool = False,
) -> Iterator[_PathRule]:
    """Yield the compact path rules required by all applicable union branches.

    A ``uniqueItems`` array yields a ``UniqueItemsRule``, a ``contains`` the matcher reads a ``ContainsRule``
    (``_contains_rule``), and a const or enum a ``ConstantTypesRule``, also that of a schema it references: an Enum
    class cannot check it.
    """
    if obj.ref:
        if parser.collapse_root_models and (target := _schema_refs.unmapped_ref_target(parser, obj.ref, visited_refs)):
            yield from iter_path_rules(
                parser,
                target[0],
                path,
                target[1],
                property_input_names,
                include_properties=include_properties,
            )
        elif (values := _ref_constant_values(parser, obj, visited_refs)) and (
            rule := _constant_types_rule(path, values)
        ):
            yield rule
        if not parser._ref_siblings_apply_to(obj, legacy="first-read"):
            return
    if obj.uniqueItems is True:
        yield UniqueItemsRule(path=path)
    if "contains" in obj.extras and (contains_rule := _contains_rule(parser, obj, path)) is not None:
        yield contains_rule
    if (
        (obj.enum or "const" in obj.extras)
        and (values := _rule_literal_values(parser, obj))
        and (rule := _constant_types_rule(path, values))
    ):
        yield rule

    if obj.allOf:
        for node, node_visited_refs, _, _ in _iter_all_of_reads(
            parser, obj, visited_refs, "", include_references=True, ref_siblings=None, track_scope=False
        ):
            yield from iter_path_rules(
                parser,
                node,
                path,
                node_visited_refs,
                property_input_names,
                include_properties=include_properties,
            )

    for members in (obj.anyOf, obj.oneOf):
        if members:
            yield from iter_union_path_rules(
                parser, members, path, visited_refs, property_input_names=property_input_names
            )

    if include_properties:
        for property_name, property_schema in (obj.properties or {}).items():
            if isinstance(property_schema, JsonSchemaObject):
                yield from iter_path_rules(
                    parser,
                    property_schema,
                    (*path, _input_names(property_input_names, property_name)),
                    visited_refs,
                    include_properties=True,
                )

    if isinstance(obj.items, JsonSchemaObject):
        items_path_step: UniqueItemsPathStep = (
            (UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP, len(obj.prefixItems))
            if parser._features_at(obj).prefix_items and obj.prefixItems
            else None
        )
        yield from iter_path_rules(
            parser, obj.items, (*path, items_path_step), visited_refs, include_properties=include_properties
        )
    for index, item in chain(
        enumerate(obj.items if isinstance(obj.items, list) else ()),
        enumerate((obj.prefixItems or ()) if parser._features_at(obj).prefix_items else ()),
    ):
        if isinstance(item, JsonSchemaObject):
            yield from iter_path_rules(
                parser, item, (*path, index), visited_refs, include_properties=include_properties
            )

    if (
        not parser._features_at(obj).prefix_items
        and isinstance(obj.additionalItems, JsonSchemaObject)
        and isinstance(obj.items, list)
    ):
        yield from iter_path_rules(
            parser,
            obj.additionalItems,
            (*path, (UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP, len(obj.items))),
            visited_refs,
            include_properties=include_properties,
        )

    for pattern, item in (obj.patternProperties or {}).items():
        if isinstance(item, JsonSchemaObject):
            yield from iter_path_rules(
                parser,
                item,
                (*path, (UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP, pattern, None)),
                visited_refs,
                include_properties=include_properties,
            )

    if isinstance(obj.additionalProperties, JsonSchemaObject):
        if obj.properties or obj.patternProperties:
            additional_path_step: UniqueItemsPathStep = (
                UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP,
                tuple(_input_names(property_input_names, property_name) for property_name in obj.properties or {}),
                tuple(obj.patternProperties or {}),
            )
        else:
            additional_path_step = UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP
        yield from iter_path_rules(
            parser,
            obj.additionalProperties,
            (*path, additional_path_step),
            visited_refs,
            include_properties=include_properties,
        )


def _contains_rule(parser: SchemaValidatorsHost, obj: JsonSchemaObject, path: UniqueItemsPath) -> ContainsRule | None:
    """Return the count check of the ``contains`` of ``obj``, None where no count is set or the matcher reads none.

    A boolean ``contains`` and one that matches every item give item counts instead
    (``_get_array_items_constraints``); the counts are read as there (``get_contains_count_constraints``:
    ``minContains`` is 1 when absent). ``minContains`` and ``maxContains`` exist from draft 2019-09 on; before, a
    ``contains`` needs one match and the two keywords are ignored.
    """
    if _value_keywords.contains_matches_every_item(parser, obj):
        return None
    features = parser._features_at(obj)
    min_contains, max_contains = (
        _value_keywords.get_contains_count_constraints(type(parser), obj) if features.contains_counts else (1, None)
    )
    if (min_contains is None and max_contains is None) or not isinstance(
        contains := get_conditional_schema(parser, obj, "contains"), JsonSchemaObject
    ):
        return None
    if (matcher := _contains_matcher(parser, contains, const_support=features.const_support)) is None:
        return None
    return ContainsRule(path=path, matcher=matcher, min_contains=min_contains, max_contains=max_contains)


def _contains_matcher(
    parser: SchemaValidatorsHost, schema: JsonSchemaObject, *, const_support: bool
) -> ContainsMatcher | None:
    """Return the keywords of a ``contains`` schema that the generated matcher checks exactly, as (keyword, value).

    The matcher reads ``type``, ``const``, ``enum``, ``pattern``, ``minLength``, ``maxLength``, the numeric bounds and
    ``multipleOf``, beside metadata only; any other keyword, a pattern Python's ``re`` rejects, a
    ``multipleOf`` that is not an integer (float division and the mathematical multiple disagree), an enum that
    ``--ignore-enum-constraints`` drops and a const the draft does not define give None, so that no count is checked
    with a matcher that accepts other items than the schema. An integral ``multipleOf`` is the integer it spells.
    """
    if not _is_empty_property_schema(schema, allowed_keywords=_CONTAINS_MATCHER_KEYWORDS):
        return None
    matcher: list[tuple[str, object]] = []
    if schema.type is not None:
        matcher.append(("type", declared_types(schema.type)))
    if "const" in schema.extras:
        if not const_support:
            return None
        matcher.append(("const", schema.extras["const"]))
    if "enum" in schema.model_fields_set:
        if parser.ignore_enum_constraints:
            return None
        matcher.append(("enum", tuple(schema.enum)))
    if schema.pattern is not None and python_rejects(schema.pattern):
        return None
    for keyword, value in _constraint_values(
        schema, (*_CONTAINS_STRING_KEYWORDS, *_CONTAINS_NUMBER_KEYWORDS), as_numbers=True
    ).items():
        if keyword == "multipleOf" and isinstance(value, float):
            if not value.is_integer():
                return None
            matcher.append((keyword, int(value)))
        else:
            matcher.append((keyword, value))
    return tuple(matcher)


def add_path_rules_validator(  # noqa: PLR0913
    parser: SchemaValidatorsHost,
    reference_path: str,
    obj: JsonSchemaObject,
    fields: Sequence[DataModelFieldBase],
    base_classes: Sequence[Reference],
    *,
    is_root_model: bool,
) -> None:
    """Register the ``uniqueItems`` and the constant checks as compact paths through raw input data.

    The constant checks are not left to the models a path reaches, as uniqueItems checks are: an Enum class has none.
    """
    rules: list[_PathRule] = []
    replaced_kinds: set[type[_PathRule]] = set()

    def is_owned_path(unique_items_path: UniqueItemsPath, owned_paths: set[UniqueItemsPath]) -> bool:
        if unique_items_path in owned_paths:
            return True
        for owned_path in owned_paths:
            if len(unique_items_path) != len(owned_path):
                continue
            for path_step, owned_step in zip(unique_items_path, owned_path, strict=True):
                if path_step == owned_step:
                    continue
                match path_step, owned_step:
                    case (
                        (marker, tuple(), tuple() as patterns),
                        (owned_marker, tuple(), tuple() as owned_patterns),
                    ) if (
                        marker == UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP
                        and owned_marker == UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP
                        and patterns == owned_patterns
                    ):
                        continue
                break
            else:
                return True
        return False

    def get_data_type_property_input_names(data_type: DataType) -> dict[str, tuple[str, ...]] | None:
        if data_type.reference and isinstance(data_type.reference.source, DataModel):
            return {
                property_name: parser._field_input_names(field)
                for field in data_type.reference.source.iter_all_fields()
                if (property_name := _field_source_name(field)) is not None
            }
        return None

    def add_rules(
        schema: JsonSchemaObject,
        path: UniqueItemsPath,
        owned_paths: set[UniqueItemsPath] | None = None,
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        for rule in iter_path_rules(parser, schema, path, property_input_names=property_input_names):
            if owned_paths is not None and isinstance(rule, UniqueItemsRule) and is_owned_path(rule.path, owned_paths):
                continue
            if rule not in rules:
                rules.append(rule)

    if is_root_model:
        root_type = fields[0].data_type if fields else None
        owned_paths = parser._get_executable_unique_items_paths(root_type, ()) if root_type is not None else None
        add_rules(
            obj,
            (),
            owned_paths,
            parser._get_input_names_by_property(fields, base_classes),
        )
    else:
        names_by_property = parser._get_input_names_by_property(fields, base_classes)
        fields_by_property = {
            property_name: field for field in fields if (property_name := _field_source_name(field)) is not None
        }
        add_rules(obj, (), property_input_names=names_by_property)
        overridden_properties: set[str] = set()
        for source in iter_schema_validation_sources(parser, obj):
            if not source.properties:
                continue
            for property_name, property_schema in source.properties.items():
                if not isinstance(property_schema, JsonSchemaObject):
                    continue
                if property_name in overridden_properties:
                    replaced_kinds.update(type(rule) for rule in iter_path_rules(parser, property_schema, ()))
                    continue
                if (input_names := names_by_property.get(property_name)) is not None:
                    field = fields_by_property.get(property_name)
                    owned_paths = (
                        parser._get_executable_unique_items_paths(field.data_type, (input_names,))
                        if field is not None
                        and any(data_type.reference for data_type in field.data_type.all_data_types)
                        else None
                    )
                    add_rules(
                        property_schema,
                        (input_names,),
                        owned_paths,
                        get_data_type_property_input_names(field.data_type) if field is not None else None,
                    )
            if overrides := source.python_field_overrides:
                overridden_properties.update(overrides)
                overridden_properties.update(overrides.values())

    _register_path_rules(parser, reference_path, rules, replaced_kinds)


def _register_path_rules(
    parser: SchemaValidatorsHost, reference_path: str, rules: list[_PathRule], replaced_kinds: set[type[_PathRule]]
) -> None:
    """Add the rules of each kind that has some, or whose inherited rules the model replaces, to its validation."""
    if not rules and not replaced_kinds:
        return
    runtime_validation = parser._schema_runtime_validation(reference_path)
    unique_items = [rule for rule in rules if isinstance(rule, UniqueItemsRule)]
    constant_types = [rule for rule in rules if isinstance(rule, ConstantTypesRule)]
    contains = [rule for rule in rules if isinstance(rule, ContainsRule)]
    if unique_items or UniqueItemsRule in replaced_kinds:
        runtime_validation.unique_items.extend(unique_items)
        runtime_validation.replace_unique_items = UniqueItemsRule in replaced_kinds
    if constant_types or ConstantTypesRule in replaced_kinds:
        runtime_validation.constant_types.extend(constant_types)
        runtime_validation.replace_constant_types = ConstantTypesRule in replaced_kinds
    if contains or ContainsRule in replaced_kinds:
        runtime_validation.contains.extend(contains)
        runtime_validation.replace_contains = ContainsRule in replaced_kinds


def pattern_rule_additional_properties(
    parser: SchemaValidatorsHost, obj: JsonSchemaObject
) -> tuple[int, JsonSchemaObject, JsonSchemaObject] | Literal[False] | None:
    """Return what the patternProperties rules of ``obj`` do with the keys no pattern matches.

    They type them by the first schema-valued additionalProperties of ``obj`` and then its allOf sources
    (``_iter_schema_validation_sources``) as ``(index, source, value)``; ``False`` where that value is false written as
    an allOf (``_is_false_all_of``): the rules forbid the keys; None where no source has one: the keys stay open.
    """
    for index, source in enumerate(parser._iter_schema_validation_sources(obj)):
        if isinstance(additional_properties := source.additionalProperties, JsonSchemaObject):
            return False if _is_false_all_of(additional_properties) else (index, source, additional_properties)
    return None


def rule_rejects_pattern(value: object) -> bool:
    """Return whether the patternProperties rules reject the keys of a pattern instead of typing its value."""
    return value is False or _is_false_all_of(value)


def plain_pattern_value_type(
    parser: SchemaValidatorsHost | type[SchemaValidatorsHost],  # noqa: ARG001
    schema: object,
    *,
    allow_minimum: bool = False,
) -> str | None:
    """Identify inhabited primitives, optionally allowing integer minimum-only constraints."""
    if not isinstance(schema, JsonSchemaObject) or schema.extras:
        return None
    if schema.model_fields_set <= {"type", "default", "title", "description"}:
        return schema.type if isinstance(schema.type, str) and schema.type in {"string", "integer", "boolean"} else None
    if not allow_minimum or schema.type != "integer" or schema.model_fields_set != {"type", "minimum"}:
        return None
    if schema.minimum is not None and (isinstance(schema.minimum.value, int) or math.isfinite(schema.minimum.value)):
        return "integer"
    return None


def plain_pattern_object(cls: type[SchemaValidatorsHost], schema: object) -> dict[str, str] | None:
    """Recognize open objects whose primitive properties have a common JSON witness."""
    if not isinstance(schema, JsonSchemaObject):
        return None
    if (
        schema.type != "object"
        or schema.additionalProperties is not True
        or schema.extras
        or not schema.model_fields_set <= {"type", "properties", "required", "additionalProperties"}
        or not schema.properties
    ):
        return None
    if not set(schema.required or ()) <= schema.properties.keys():
        return None
    properties = {}
    for name, field in schema.properties.items():
        if (field_type := plain_pattern_value_type(cls, field, allow_minimum=True)) is None:
            return None
        properties[name] = field_type
    return properties


def declared_pattern_rejects_value(cls: type[SchemaValidatorsHost], declared: object, pattern: object) -> bool:
    """Prove a skipped pattern rejects some value accepted by a simple declared field."""
    declared_type = plain_pattern_value_type(cls, declared)
    if declared_type is not None:
        if rule_rejects_pattern(pattern):
            return True
        return (
            declared_type == "integer"
            and isinstance(pattern, JsonSchemaObject)
            and pattern.model_fields_set == {"type", "minimum"}
            and plain_pattern_value_type(cls, pattern, allow_minimum=True) == "integer"
        )
    pattern_properties = plain_pattern_object(cls, pattern)
    if declared is True:
        return pattern_properties is not None and bool(cast("JsonSchemaObject", pattern).required)
    declared_properties = plain_pattern_object(cls, declared)
    return (
        declared_properties is not None
        and pattern_properties is not None
        and bool(
            set(cast("JsonSchemaObject", pattern).required or ())
            - set(cast("JsonSchemaObject", declared).required or ())
        )
    )


def pattern_rejects_declared_value(
    cls: type[SchemaValidatorsHost],
    pattern_properties: dict[str, JsonSchemaObject | bool],
    declared_properties: dict[str, Any],
) -> bool:
    """Return whether a pattern matches a declared key and rejects a value of its simple declared schema."""
    for pattern, schema in pattern_properties.items():
        for name, declared in declared_properties.items():
            try:
                matches = re.search(pattern, name)
            except re.error:
                continue
            if matches and declared_pattern_rejects_value(cls, declared, schema):
                return True
    return False


def pattern_validation_loses_raw_value(
    cls: type[SchemaValidatorsHost],
    obj: JsonSchemaObject,
    pattern_value_types: list[tuple[str, DataType]],
    *,
    declared_witness: bool = False,
) -> Literal["declared", "models"] | None:
    """Limit changed helpers to incompatible model instances, or skipped constraints (``declared_witness``)."""
    if not obj.extras.keys() <= {"title", "description"}:
        return None
    return pattern_validation_intersection(cls, obj, pattern_value_types, declared_witness=declared_witness)


def pattern_validation_intersection(
    cls: type[SchemaValidatorsHost],
    obj: JsonSchemaObject,
    pattern_value_types: list[tuple[str, DataType]],
    *,
    declared_witness: bool = False,
) -> Literal["declared", "models"] | None:
    """Prove the same bounded intersection after root annotation eligibility.

    With ``declared_witness``, the check of 0.86.0 without ``--check-pattern-maps-strictly``, a pattern that
    matches a declared key and rejects a value of its simple declared schema gives ``declared``.
    """
    if (
        obj.type != "object"
        or not obj.model_fields_set
        <= {
            "type",
            "properties",
            "required",
            "patternProperties",
            "additionalProperties",
            "title",
            "description",
            "extras",
        }
        or not obj.patternProperties
    ):
        return None
    declared_properties = obj.properties or {}
    if not set(obj.required or ()) <= declared_properties.keys() or any(
        field is not True and plain_pattern_value_type(cls, field) is None and plain_pattern_object(cls, field) is None
        for field in declared_properties.values()
    ):
        return None
    if declared_witness and pattern_rejects_declared_value(cls, obj.patternProperties, declared_properties):
        return "declared"
    pair_size = 2
    if len(obj.patternProperties) != pair_size or len(pattern_value_types) != pair_size:
        return None
    # A literal prefix followed by a literal suffix supplies an explicit common key.
    witness = None
    patterns = tuple(obj.patternProperties)
    for prefix, suffix in (patterns, patterns[::-1]):
        if (start := re.fullmatch(r"\^([A-Za-z0-9_ -]+)", prefix)) and (
            end := re.fullmatch(r"([A-Za-z0-9_ -]+)\$", suffix)
        ):
            witness = start[1] + end[1] if start[1] != end[1] else None
            break
    literal_models: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for pattern, data_type in pattern_value_types:
        if (
            data_type.reference is None
            or (match := re.fullmatch(r"\^?([A-Za-z0-9_ -]+)\$?", pattern)) is None
            or (witness or match[1]) in declared_properties
            or (properties := plain_pattern_object(cls, obj.patternProperties.get(pattern, False))) is None
        ):
            continue
        previous = literal_models.setdefault(witness or match[1], [])
        if any(
            reference != data_type.reference.path
            and all(properties[name] == field_type for name, field_type in fields.items() if name in properties)
            for reference, fields in previous
        ):
            return "models"
        previous.append((data_type.reference.path, properties))
    return None


def pattern_validation_with_undeclared_required(
    cls: type[SchemaValidatorsHost],
    obj: JsonSchemaObject,
    pattern_value_types: list[tuple[str, DataType]],
    *,
    declared_witness: bool = False,
) -> Literal["models"] | None:
    """Reuse the object compatibility proof after missing names receive raw required validation."""
    pair_size = 2
    if not obj.patternProperties or len(obj.patternProperties) != pair_size:
        return None
    patterns = {}
    for pattern, schema in obj.patternProperties.items():
        if (
            isinstance(schema, JsonSchemaObject)
            and schema.properties
            and not set(schema.required) <= schema.properties.keys()
        ):
            patterns[pattern] = schema.model_copy(
                update={"required": [name for name in schema.required if name in schema.properties]}
            )
    if (
        patterns
        and pattern_validation_loses_raw_value(
            cls,
            obj.model_copy(update={"patternProperties": {**obj.patternProperties, **patterns}}),
            pattern_value_types,
            declared_witness=declared_witness,
        )
        == "models"
    ):
        return "models"
    return None


def add_pattern_properties_validator(
    parser: SchemaValidatorsHost,
    reference_path: str,
    obj: JsonSchemaObject,
    pattern_validators: tuple[list[tuple[str, DataType]], list[str], DataType | None, bool],
    names_by_property: dict[str, tuple[str, ...]],
) -> None:
    """Register the patternProperties rule of ``obj`` and its allOf sources.

    ``pattern_validators`` is what ``_collect_pattern_property_validators`` collected for ``obj``.
    """
    pattern_value_types, rejected_patterns, additional_property_type, allow_unmatched = pattern_validators
    if not pattern_value_types and not rejected_patterns:
        return

    declared_names = tuple(sorted({name for names in names_by_property.values() for name in names}))
    rule_type = PatternPropertiesRule
    helpers_kept = customizes_models(parser.config)
    declared_witness = "legacy-validated-pattern-maps" in parser._legacy_object_maps
    intersection = (
        None
        if helpers_kept
        else pattern_validation_loses_raw_value(
            type(parser), obj, pattern_value_types, declared_witness=declared_witness
        )
    )
    if obj is parser._pattern_validation_document_root:
        if intersection is None and not parser.force_optional_for_required_fields:
            intersection = pattern_validation_with_undeclared_required(
                type(parser), obj, pattern_value_types, declared_witness=declared_witness
            )
        if (
            intersection is None
            and not obj.extras.keys() <= {"title", "description"}
            and parser._plain_pattern_root_annotations(obj, pattern_value_types)
        ):
            intersection = pattern_validation_intersection(
                type(parser), obj, pattern_value_types, declared_witness=declared_witness
            )
    if intersection == "declared" or (
        not declared_witness and not helpers_kept and pattern_checks_declared_key(parser, obj, names_by_property)
    ):
        rule_type = IndependentDeclaredPatternPropertiesRule
    elif intersection == "models":
        rule_type = IndependentModelPatternPropertiesRule
    parser._schema_runtime_validation(reference_path).pattern_properties.append(
        rule_type(
            declared_properties=declared_names,
            pattern_properties=tuple(pattern_value_types),
            rejected_patterns=tuple(rejected_patterns),
            additional_property_type=additional_property_type,
            allow_unmatched=allow_unmatched,
        )
    )
    if obj is parser._pattern_validation_document_root:
        parser._pattern_validation_document_root = None


def add_required_groups_validator(
    parser: SchemaValidatorsHost,
    reference_path: str,
    keyword: Literal["anyOf", "oneOf", "not"],
    groups: Sequence[Sequence[str]],
    names_by_property: dict[str, tuple[str, ...]],
) -> None:
    """Register a required-property group runtime rule for keyword, deduplicated by value."""
    if not groups:
        return

    rule = RequiredGroupsRule(
        keyword=keyword,
        groups=parser._required_group_input_names(groups, names_by_property),
    )
    runtime_validation = parser._schema_runtime_validation(reference_path)
    if rule not in runtime_validation.required_groups:
        runtime_validation.required_groups.append(rule)


def add_property_count_validator(parser: SchemaValidatorsHost, reference_path: str, obj: JsonSchemaObject) -> None:
    """Add raw-object property-count rules from this schema and its allOf sources."""
    if (property_count_rule := parser._get_property_count_rule(obj)) is None:
        return
    parser._schema_runtime_validation(reference_path).property_count = property_count_rule


def add_conditional_validator(
    parser: SchemaValidatorsHost,
    reference_path: str,
    obj: JsonSchemaObject,
    names_by_property: dict[str, tuple[str, ...]],
) -> None:
    """Register the if/then/else required-property rule of ``obj``, deduplicated by value."""
    if (predicate := get_conditional_predicate(parser, obj)) is None:
        return

    then_groups, else_groups = (
        (tuple(schema.required),) if isinstance(schema, JsonSchemaObject) and schema.required else ()
        for schema in (get_conditional_schema(parser, obj, "then"), get_conditional_schema(parser, obj, "else"))
    )
    if not then_groups and not else_groups:
        return

    rule = ConditionalRequiredRule(
        condition=_condition_input_names(predicate, names_by_property),
        then_groups=parser._required_group_input_names(then_groups, names_by_property),
        else_groups=parser._required_group_input_names(else_groups, names_by_property),
    )
    runtime_validation = parser._schema_runtime_validation(reference_path)
    if rule not in runtime_validation.conditional_required:
        runtime_validation.conditional_required.append(rule)


def add_schema_validators(  # noqa: PLR0913, PLR0917
    parser: SchemaValidatorsHost,
    reference_path: str,
    name: str,
    obj: JsonSchemaObject,
    path: list[str],
    fields: Sequence[DataModelFieldBase],
    base_classes: Sequence[Reference],
    *,
    include_property_count: bool = True,
) -> None:
    """Register every runtime validation rule this schema and its allOf sources contribute."""
    if not parser.generate_schema_validators:
        return

    pattern_validators = parser._collect_pattern_property_validators(name, obj, path)
    names_by_property = parser._get_input_names_by_property(fields, base_classes)
    add_pattern_properties_validator(parser, reference_path, obj, pattern_validators, names_by_property)
    if (
        not parser.force_optional_for_required_fields
        and not obj.custom_base_path
        and not customizes_models(parser.config)
        and (missing_required := [name for name in obj.required if name not in names_by_property])
    ):
        parser._add_required_groups_validator(reference_path, "anyOf", [missing_required], names_by_property)
    add_group_validators(parser, reference_path, obj, names_by_property)
    add_conditional_bounds_validator(parser, reference_path, obj, names_by_property)
    if include_property_count:
        parser._add_property_count_validator(reference_path, obj)
    parser._add_path_rules_validator(reference_path, obj, fields, base_classes, is_root_model=False)
    if not obj.allOf:
        return
    # Inline rules belong to this model even when it has no other runtime rules.
    # Once it defines core rules, also merge those shadowed on referenced bases.
    for include_references in (False, True):
        runtime_validation = parser.extra_template_data[reference_path].get("schema_runtime_validation")
        has_core_rules = _is_internal_schema_runtime_validation(runtime_validation) and has_core_schema_runtime_rules(
            parser, runtime_validation
        )
        if include_references:
            if not has_core_rules:
                return
        elif has_core_rules:
            continue
        for source in parser._iter_schema_validation_sources(obj, include_references=include_references):
            if source is not obj:
                add_group_validators(parser, reference_path, source, names_by_property)


def add_group_validators(
    parser: SchemaValidatorsHost,
    reference_path: str,
    source: JsonSchemaObject,
    names_by_property: dict[str, tuple[str, ...]],
) -> None:
    """Register the required-property groups of the oneOf, anyOf and not of ``source``, then its if/then/else."""
    oneof_groups = get_required_groups(parser, source.oneOf)
    parser._add_required_groups_validator(reference_path, "oneOf", oneof_groups, names_by_property)
    anyof_groups = get_required_groups(parser, source.anyOf)
    parser._add_required_groups_validator(reference_path, "anyOf", anyof_groups, names_by_property)
    not_groups = get_not_required_groups(parser, source)
    parser._add_required_groups_validator(reference_path, "not", not_groups, names_by_property)
    parser._add_conditional_validator(reference_path, source, names_by_property)


def is_required_only_schema(
    parser: SchemaValidatorsHost,
    item: JsonSchemaObject | bool,  # noqa: FBT001
    *,
    allow_empty: bool = False,
) -> TypeIs[JsonSchemaObject]:
    """Return whether a combined-schema branch is only a property presence rule."""
    if not isinstance(item, JsonSchemaObject):
        return False
    if not item.required and not (allow_empty and "required" in item.model_fields_set):
        return False

    return _is_object_only_type(item.type) and _is_empty_property_schema(
        item, allowed_keywords=parser.REQUIRED_ONLY_SCHEMA_ALLOWED_FIELDS
    )


def merge_conditional_properties(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> JsonSchemaObject:
    if not parser.generate_schema_validators:
        return obj

    merged_properties: dict[str, Any] = {key: _schema_dump(value) for key, value in (obj.properties or {}).items()}
    for branch in iter_conditional_branches(parser, obj):
        if not branch.properties:
            continue
        for key, value in branch.properties.items():
            if key not in merged_properties:
                merged_properties[key] = _schema_dump(value)

    if not merged_properties or set(merged_properties) == set(obj.properties or {}):
        return obj

    schema_dict = obj.model_dump(exclude_unset=True, by_alias=True)
    schema_dict["properties"] = merged_properties
    return parser._schema_object(schema_dict)


def collect_pattern_property_validators(
    parser: SchemaValidatorsHost,
    name: str,
    obj: JsonSchemaObject,
    path: list[str],
) -> tuple[list[tuple[str, DataType]], list[str], DataType | None, bool]:
    pattern_value_types: list[tuple[str, DataType]] = []
    rejected_patterns: list[str] = []
    allow_unmatched = True

    for source_index, source in enumerate(parser._iter_schema_validation_sources(obj)):
        if source.additionalProperties is False or source.unevaluatedProperties is False:
            allow_unmatched = False
        if not source.patternProperties:
            continue
        for pattern_index, (pattern, schema) in enumerate(source.patternProperties.items()):
            match schema:
                case True:
                    pattern_value_types.append((pattern, parser.data_type_manager.get_data_type(Types.any)))
                case JsonSchemaObject() if not rule_rejects_pattern(schema):
                    if schema is parser._root_pattern_string_constraints:
                        value_type = parser.data_type_manager.get_data_type(
                            Types.string,
                            field_constraints=False,
                            **_constraint_values(schema, constrained_type_keyword_order("string"), as_numbers=True),
                        )
                    else:
                        value_path = get_special_path(
                            f"schemaValidators/patternProperties/{source_index}/{pattern_index}", path
                        )
                        value_type = parser.parse_item(name, schema, value_path)
                        value_type = parser._pattern_value_alias(name, schema, value_type, value_path)
                    pattern_value_types.append((pattern, value_type))
                case False | JsonSchemaObject():
                    rejected_patterns.append(pattern)

    if not pattern_value_types and not rejected_patterns:
        return pattern_value_types, rejected_patterns, None, allow_unmatched
    if (rule := pattern_rule_additional_properties(parser, obj)) is None:
        return pattern_value_types, rejected_patterns, None, allow_unmatched
    if rule is False:
        return pattern_value_types, rejected_patterns, None, False
    source_index, source, additional_properties = rule
    additional_property_name = composed_name(name, "additional-property")
    additional_property_type = parser._parse_additional_properties_value(
        additional_property_name,
        get_special_path(f"schemaValidators/additionalProperties/{source_index}", path),
        source,
        additional_properties=additional_properties,
        constrained_name=additional_property_name,
    )
    return pattern_value_types, rejected_patterns, additional_property_type, allow_unmatched


def pattern_checks_declared_key(
    parser: SchemaValidatorsHost, obj: JsonSchemaObject, names_by_property: dict[str, tuple[str, ...]]
) -> bool:
    """Return whether a pattern matches a declared key and its schema adds an assertion to the declared schema.

    A key declared by several sources skips a pattern whose schema one of the declarations implies; a key with no
    declared schema among the sources is checked.
    """
    declared: dict[str, list[tuple[object, str | None]]] = {}
    patterns: list[tuple[str, object, str | None]] = []
    for source, scope in iter_scoped_schema_validation_sources(parser, obj):
        for property_name, schema in (source.properties or {}).items():
            declared.setdefault(property_name, []).append((_schema_dump(schema), scope))
        patterns.extend(
            (pattern, _schema_dump(schema), scope) for pattern, schema in (source.patternProperties or {}).items()
        )
    return any(
        _searches_any(pattern, input_names)
        and not any(
            _schema_implies(schema, pattern_schema, same_document=scope is not None and scope == pattern_scope)
            for schema, scope in declared.get(property_name, ())
        )
        for property_name, input_names in names_by_property.items()
        for pattern, pattern_schema, pattern_scope in patterns
    )


def _condition_input_names(
    predicate: tuple[tuple[str, tuple[object, ...]], ...], names_by_property: Mapping[str, tuple[str, ...]]
) -> Condition:
    """Return the condition of an if/then/else rule: each property of ``predicate`` by its raw input names."""
    return tuple((_input_names(names_by_property, property_name), values) for property_name, values in predicate)


def add_conditional_bounds_validator(
    parser: SchemaValidatorsHost,
    reference_path: str,
    obj: JsonSchemaObject,
    names_by_property: dict[str, tuple[str, ...]],
) -> None:
    """Register the numeric bounds the then and else of each if/then/else of ``obj`` and its allOf sources put.

    The sources include the references, as for the path rules: a model whose own rules replace those of its base
    class still checks the base's. The condition is that of the conditional required rule; rules are told apart by
    what they write (``stable_repr``), where a boolean is no number.
    """
    for source in iter_schema_validation_sources(parser, obj):
        if (predicate := get_conditional_predicate(parser, source)) is None:
            continue
        then_bounds, else_bounds = (
            _branch_bounds(parser, branch, names_by_property)
            if isinstance(branch := get_conditional_schema(parser, source, keyword), JsonSchemaObject)
            else ()
            for keyword in ("then", "else")
        )
        if not then_bounds and not else_bounds:
            continue
        rule = ConditionalBoundsRule(
            condition=_condition_input_names(predicate, names_by_property),
            then_bounds=then_bounds,
            else_bounds=else_bounds,
        )
        runtime_validation = parser._schema_runtime_validation(reference_path)
        if all(stable_repr(rule) != stable_repr(existing) for existing in runtime_validation.conditional_bounds):
            runtime_validation.conditional_bounds.append(rule)


def _branch_bounds(
    parser: SchemaValidatorsHost, branch: JsonSchemaObject, names_by_property: Mapping[str, tuple[str, ...]]
) -> ConditionalBounds:
    """Return the numeric bounds a then or else branch puts on properties, by their raw input names."""
    bounds: list[tuple[tuple[str, ...], BoundKeyword, int | float]] = []
    for source in _bound_sources(parser, branch):
        for property_name, property_schema in (source.properties or {}).items():
            if not isinstance(property_schema, JsonSchemaObject):
                continue
            input_names = _input_names(names_by_property, property_name)
            for property_source in _bound_sources(parser, property_schema):
                limits = _constraint_values(property_source, _BOUND_KEYWORDS, as_numbers=True)
                for keyword in _BOUND_KEYWORDS:
                    if keyword in limits and (bound := (input_names, keyword, limits[keyword])) not in bounds:
                        bounds.append(bound)
    return tuple(bounds)


def _bound_sources(
    parser: SchemaValidatorsHost, schema: JsonSchemaObject, visited_refs: frozenset[_TargetNode] = frozenset()
) -> Iterator[JsonSchemaObject]:
    """Yield the schemas whose keywords apply to the instance of ``schema``: its allOf sources and what a $ref names.

    The keywords beside a ``$ref`` count where they apply to it (``_ref_siblings_apply_to``).
    """
    for source in iter_schema_validation_sources(parser, schema, visited_refs):
        if not source.ref:
            yield source
            continue
        if parser._ref_siblings_apply_to(source, legacy="first-read"):
            yield source
        if (target := _schema_refs.unmapped_ref_target(parser, source.ref, visited_refs)) is not None:
            yield from _bound_sources(parser, *target)


def get_conditional_predicate(
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
) -> tuple[tuple[str, tuple[object, ...]], ...] | None:
    """Extract supported required-property predicates without dropping other conditions."""
    if_schema = get_conditional_schema(parser, obj, "if")
    if not isinstance(if_schema, JsonSchemaObject) or not if_schema.required:
        return None
    if not _is_object_only_type(if_schema.type) or not _is_empty_property_schema(
        if_schema, allowed_keywords=parser.CONDITIONAL_SCHEMA_ALLOWED_FIELDS
    ):
        return None

    properties = if_schema.properties or {}
    if any(
        property_name not in if_schema.required
        and property_schema is not True
        and not (isinstance(property_schema, JsonSchemaObject) and _is_empty_property_schema(property_schema))
        for property_name, property_schema in properties.items()
    ):
        return None
    predicates: list[tuple[str, tuple[object, ...]]] = []
    for property_name in if_schema.required:
        if (values := _get_conditional_property_values(properties.get(property_name))) is None:
            return None
        predicates.append((property_name, values))
    return tuple(predicates)
