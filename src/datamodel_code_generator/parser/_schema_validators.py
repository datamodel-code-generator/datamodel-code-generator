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
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeVar, cast

from typing_extensions import Protocol

from datamodel_code_generator._alias_generators import generate_alias
from datamodel_code_generator._constraint_applicability import constrained_type_keyword_order
from datamodel_code_generator._container_kinds import MAPPINGS
from datamodel_code_generator._json_schema import MODELED_SCHEMA_MAPS, SCHEMA_MAP_KEYS, schema_positions
from datamodel_code_generator.enums import AliasGenerator
from datamodel_code_generator.model import DataModel
from datamodel_code_generator.model.base import _field_source_name
from datamodel_code_generator.model.runtime_validation import (
    UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_ADDITIONAL_VALUES_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_PATTERN_VALUES_PATH_STEP,
    UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP,
    ConditionalRequiredRule,
    IndependentDeclaredPatternPropertiesRule,
    IndependentModelPatternPropertiesRule,
    PatternPropertiesRule,
    PropertyCountRule,
    RequiredGroupsRule,
    SchemaRuntimeValidation,
    UniqueItemsRule,
    _is_internal_schema_runtime_validation,
)
from datamodel_code_generator.parser._intersection import (
    INTERSECTED_KEYWORDS,
    bounds_admit_no_value,
    declared_types,
    intersect_enum_values,
)
from datamodel_code_generator.parser._intersection import json_values_equal as _json_literal_values_equal
from datamodel_code_generator.parser._passes._shared import (
    CollapseRootModelsHost,
    DataModelTypeHost,
    TargetPythonVersionHost,
)
from datamodel_code_generator.parser._schema_object import _ANNOTATION_KEYWORDS, JsonSchemaObject, _is_false_all_of
from datamodel_code_generator.parser._schema_reading import (
    _constraint_values,
    _get_json_value_type,
    _intersect_dumps,
    _is_object_only_type,
    _schema_dump,
)
from datamodel_code_generator.parser.base import get_special_path
from datamodel_code_generator.types import DataType, Types

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from typing_extensions import TypeIs

    from datamodel_code_generator.config import JSONSchemaParserConfig
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.model.runtime_validation import UniqueItemsPath, UniqueItemsPathStep
    from datamodel_code_generator.parser._schema_graph import _TargetNode
    from datamodel_code_generator.parser.schema_version import JsonSchemaFeatures
    from datamodel_code_generator.reference import ModelResolver, Reference
    from datamodel_code_generator.types import DataTypeManager

__all__ = [
    "SchemaValidatorsHost",
    "add_conditional_validator",
    "add_group_validators",
    "add_pattern_properties_validator",
    "add_property_count_validator",
    "add_required_groups_validator",
    "add_schema_validators",
    "add_unique_items_validator",
    "data_type_may_accept_array",
    "declared_pattern_rejects_value",
    "field_alias_generator_input_name",
    "field_input_names",
    "first_schema_additional_properties",
    "get_common_executable_unique_items_paths",
    "get_conditional_schema",
    "get_executable_unique_items_paths",
    "get_input_names_by_property",
    "get_not_required_groups",
    "get_property_count_rule",
    "get_required_groups",
    "get_union_unique_items_branch_rules",
    "has_conditional_validator",
    "has_core_schema_runtime_rules",
    "has_pattern_properties_validator",
    "has_property_count_validator",
    "has_required_group_validators",
    "has_schema_validator_constraints",
    "iter_conditional_branches",
    "iter_property_count_validation_sources",
    "iter_schema_validation_sources",
    "iter_scoped_schema_validation_sources",
    "iter_union_unique_items_rules",
    "iter_unique_items_rules",
    "pattern_rejects_declared_value",
    "pattern_rule_additional_properties",
    "pattern_validation_intersection",
    "pattern_validation_loses_raw_value",
    "pattern_validation_with_undeclared_required",
    "plain_pattern_object",
    "plain_pattern_value_type",
    "required_group_input_names",
    "schema_item_may_accept_container",
    "schema_literal_values",
    "schema_may_accept_container",
    "should_parse_object_with_schema_validators",
    "unique_items_path_container_type",
]

_ValidatedT = TypeVar("_ValidatedT")


class SchemaValidatorsHost(CollapseRootModelsHost, DataModelTypeHost, TargetPythonVersionHost, Protocol):
    """The members of ``JsonSchemaParser`` the schema validator rules read: options, state and the methods they call."""

    CONDITIONAL_SCHEMA_ALLOWED_FIELDS: ClassVar[frozenset[str]]
    REQUIRED_ONLY_SCHEMA_ALLOWED_FIELDS: ClassVar[frozenset[str]]
    SCHEMA_OBJECT_TYPE: ClassVar[type[JsonSchemaObject]]
    _pattern_validation_document_root: JsonSchemaObject | None
    _root_pattern_string_constraints: JsonSchemaObject | None

    @property
    def config(self) -> JSONSchemaParserConfig:
        """The configuration of the parser."""

    @property
    def data_type_manager(self) -> DataTypeManager:
        """The type manager of the output."""

    @property
    def extra_template_data(self) -> defaultdict[str, Any]:
        """The template data of each reference path, which holds its ``schema_runtime_validation``."""

    @property
    def force_optional_for_required_fields(self) -> bool:
        """Whether required fields are generated as optional."""

    @property
    def generate_schema_validators(self) -> bool:
        """Whether the models validate the schema rules their fields cannot express."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of the references and class names of the parse."""

    @property
    def schema_features(self) -> JsonSchemaFeatures:
        """The features of the JSON Schema version being parsed."""

    @property
    def _legacy_object_maps(self) -> frozenset[str]:
        """The rows of 0.86.0 object maps kept."""

    @property
    def _ref_sibling_keywords_enabled(self) -> bool:
        """Whether the sibling keywords of a ``$ref`` apply beside it in the document being parsed."""

    def _ref_siblings_are_parts_of(self, schema: JsonSchemaObject) -> bool:
        """Whether the keywords beside a ``$ref`` are parts of the schema object."""

    def _schema_object_is_root_ref(self, schema: JsonSchemaObject) -> bool:
        """Whether the located object represents a document root whose schema has a ``$ref``."""

    @property
    def _property_count_rule_cache(self) -> dict[int, tuple[JsonSchemaObject, PropertyCountRule | None]]:
        """The property-count rule of each schema object, cached for this parse."""

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

    def _add_pattern_properties_validator(  # noqa: PLR0913, PLR0917
        self,
        reference_path: str,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
    ) -> None:
        """Register the patternProperties rule of ``obj`` and its allOf sources."""

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

    def _add_unique_items_validator(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        fields: Sequence[DataModelFieldBase],
        base_classes: Sequence[Reference],
        *,
        is_root_model: bool,
    ) -> None:
        """Register ``uniqueItems`` checks as compact paths through raw input data."""

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

    def _first_schema_additional_properties(
        self, obj: JsonSchemaObject
    ) -> tuple[int, JsonSchemaObject, JsonSchemaObject] | None:
        """Return the index, schema and value of the first schema-valued additionalProperties of ``obj``."""

    def _get_common_executable_unique_items_paths(
        self,
        data_types: Sequence[DataType],
        path: UniqueItemsPath,
        visited: set[int],
        *,
        filter_array_branches: bool = True,
    ) -> set[UniqueItemsPath]:
        """Return paths every branch delegates to an executable validator."""

    def _get_conditional_predicate(
        self,
        obj: JsonSchemaObject,
    ) -> tuple[tuple[str, tuple[object, ...]], ...] | None:
        """Extract supported required-property predicates without dropping other conditions."""

    def _get_conditional_schema(
        self,
        obj: JsonSchemaObject,
        keyword: str,
    ) -> JsonSchemaObject | bool | None:
        """Return the schema of an applicator kept in extras, such as ``if``, which the schema graph validates once."""

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

    def _get_required_groups(self, items: Sequence[JsonSchemaObject | bool]) -> tuple[tuple[str, ...], ...]:
        """Return the property names each item requires, or nothing unless every item only requires properties."""

    def _get_union_unique_items_branch_rules(
        self,
        items: Sequence[JsonSchemaObject | bool],
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode],
        property_input_names: Mapping[str, tuple[str, ...]] | None,
    ) -> tuple[list[tuple[JsonSchemaObject | bool, tuple[UniqueItemsRule, ...]]], list[UniqueItemsRule]]:
        """Collect ordered uniqueItems rules produced by each union branch."""

    def _has_pattern_properties_validator(self, obj: JsonSchemaObject) -> bool:
        """Return whether schema runtime validation owns patternProperties dispatch."""

    def _has_property_count_validator(self, obj: JsonSchemaObject) -> bool:
        """Return whether an object or its allOf sources constrain property counts."""

    def _is_required_only_schema(
        self,
        item: JsonSchemaObject | bool,  # noqa: FBT001
        *,
        allow_empty: bool = False,
    ) -> TypeIs[JsonSchemaObject]:
        """Return whether a combined-schema branch is only a property presence rule."""

    def _iter_conditional_branches(self, obj: JsonSchemaObject) -> Iterator[JsonSchemaObject]:
        """Yield the ``then`` and ``else`` branches of ``obj`` that are schema objects."""

    def _iter_inherited_schema_objects(
        self, base_classes: list[Reference], visited: frozenset[_TargetNode]
    ) -> Iterator[tuple[JsonSchemaObject, frozenset[_TargetNode], str]]:
        """Yield inherited schema objects not visited yet, with the visited schema nodes they add to."""

    def _iter_property_count_validation_sources(
        self,
        obj: JsonSchemaObject,
        visited_refs: frozenset[_TargetNode] = frozenset(),
    ) -> Iterator[JsonSchemaObject]:
        """Yield count constraints from allOf, preserving JSON Schema $ref siblings."""

    def _iter_schema_validation_sources(
        self,
        obj: JsonSchemaObject,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        *,
        include_references: bool = True,
    ) -> Iterator[JsonSchemaObject]:
        """Yield the schema and its allOf sources, without the document each was read from."""

    def _iter_scoped_schema_validation_sources(
        self,
        obj: JsonSchemaObject,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        scope: str | None = "",
        *,
        include_references: bool = True,
    ) -> Iterator[tuple[JsonSchemaObject, str | None]]:
        """Yield the schema and its allOf sources with the document each was read from."""

    def _iter_union_unique_items_rules(
        self,
        items: Sequence[JsonSchemaObject | bool],
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode],
        *,
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
    ) -> Iterator[UniqueItemsRule]:
        """Yield paths unconditionally required by every shape-compatible union branch."""

    def _iter_unique_items_rules(
        self,
        obj: JsonSchemaObject,
        path: UniqueItemsPath,
        visited_refs: frozenset[_TargetNode] = frozenset(),
        property_input_names: Mapping[str, tuple[str, ...]] | None = None,
        *,
        include_properties: bool = False,
    ) -> Iterator[UniqueItemsRule]:
        """Yield compact uniqueItems rules required by all applicable union branches."""

    def _key_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node at a reference key, or the key when the graph does not locate its value."""

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Load a JsonSchemaObject from a $ref using standard resolve/load pipeline."""

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

    def _plain_pattern_root_annotations(self, obj: JsonSchemaObject, patterns: list[tuple[str, DataType]]) -> bool:
        """Recognize inert root annotations only in the standard generation context."""

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names: its edge's key, or a plain string's ``resolve_ref``."""

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

    def _schema_literal_values(self, obj: JsonSchemaObject) -> tuple[object, ...] | None:
        """Return direct const/enum values after their JSON Schema intersection."""

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw or dumped schema as ``SCHEMA_OBJECT_TYPE`` in the parser's validation context."""

    def _schema_runtime_validation(self, reference_path: str) -> SchemaRuntimeValidation:
        """Return the runtime validation rules of ``reference_path``, created on first use."""

    def _shared_object(self, raw: object, kind: type[_ValidatedT], validate: Callable[[], _ValidatedT]) -> _ValidatedT:
        """Return the ``kind`` object a value of a loaded document validates to, validated once by the schema graph."""

    def _unique_items_path_container_type(
        self,
        path: UniqueItemsPath,
    ) -> Literal["array", "object"]:
        """Return the raw container shape needed for one relative uniqueItems path: an array for an item step."""

    def _unvisited_ref_target(
        self, ref: str, visited_refs: frozenset[_TargetNode]
    ) -> tuple[JsonSchemaObject, frozenset[_TargetNode]] | None:
        """Return the schema ``ref`` names with ``visited_refs`` and its node, or None when its node was visited."""


_IMPLIED_SUBSCHEMA_KEYWORDS = frozenset({"items", "propertyNames"})


_UNMATCHED_SCHEMA_KEYWORDS = frozenset(schema_positions("unmatched"))


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
    """Extract a presence predicate or literals without discarding property constraints."""
    if item is None or item is True:
        return ()
    if not isinstance(item, JsonSchemaObject):
        return None
    if "const" in item.extras:
        keyword, values = "const", (item.extras["const"],)
    elif item.enum:
        keyword, values = "enum", tuple(item.enum)
    else:
        return () if _is_empty_property_schema(item) else None
    if not _is_empty_property_schema(item, allowed_keywords=frozenset({keyword, "type"})):
        return None
    if item.type is None:
        return values
    schema_types = declared_types(item.type)
    return (
        tuple(
            value
            for value in values
            if (value_type := _get_json_value_type(value)) in schema_types
            or (value_type == "integer" and "number" in schema_types)
            or (isinstance(value, float) and value.is_integer() and "integer" in schema_types)
        )
        or None
    )


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
        if not parser._is_required_only_schema(item):  # noqa: SLF001
            return ()
        groups.append(tuple(item.required))
    return tuple(groups)


def get_not_required_groups(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> tuple[tuple[str, ...], ...]:
    """Return the required-only property group `not` forbids, or an empty tuple."""
    not_schema = parser._get_conditional_schema(obj, "not")  # noqa: SLF001
    if not_schema is None or not parser._is_required_only_schema(not_schema, allow_empty=True):  # noqa: SLF001
        return ()
    return (tuple(not_schema.required),)


def has_required_group_validators(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether obj has a oneOf, anyOf, or not required-property group."""
    return bool(
        parser._get_required_groups(obj.oneOf)  # noqa: SLF001
        or parser._get_required_groups(obj.anyOf)  # noqa: SLF001
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
        return parser._shared_object(item, parser.SCHEMA_OBJECT_TYPE, partial(parser._schema_object, item))  # noqa: SLF001
    return None


def iter_conditional_branches(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> Iterator[JsonSchemaObject]:
    """Yield the ``then`` and ``else`` branches of ``obj`` that are schema objects."""
    for keyword in ("then", "else"):
        branch = parser._get_conditional_schema(obj, keyword)  # noqa: SLF001
        if isinstance(branch, JsonSchemaObject):
            yield branch


def has_conditional_validator(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether ``obj`` has a supported ``if`` and a ``then`` or ``else`` that requires properties."""
    return parser._get_conditional_predicate(obj) is not None and any(  # noqa: SLF001
        branch.required
        for branch in parser._iter_conditional_branches(obj)  # noqa: SLF001
    )


def has_schema_validator_constraints(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether ``obj`` has a required group, conditional, patternProperties or property-count rule."""
    return bool(
        has_required_group_validators(parser, obj)
        or has_conditional_validator(parser, obj)
        or parser._has_pattern_properties_validator(obj)  # noqa: SLF001
        or parser._has_property_count_validator(obj)  # noqa: SLF001
    )


def has_pattern_properties_validator(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether schema runtime validation owns patternProperties dispatch."""
    return parser.generate_schema_validators and any(
        source.patternProperties
        for source in parser._iter_schema_validation_sources(obj)  # noqa: SLF001
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
    return parser._get_property_count_rule(obj) is not None  # noqa: SLF001


def get_property_count_rule(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> PropertyCountRule | None:
    """Aggregate one immutable count rule and cache it for this parse only."""
    if not parser.generate_schema_validators:
        return None
    cache_key = id(obj)
    if (cached := parser._property_count_rule_cache.get(cache_key)) is not None and cached[0] is obj:  # noqa: SLF001
        return cached[1]

    min_properties: int | None = None
    max_properties: int | None = None
    for source in parser._iter_property_count_validation_sources(obj):  # noqa: SLF001
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
    parser._property_count_rule_cache[cache_key] = obj, rule  # noqa: SLF001
    return rule


def iter_property_count_validation_sources(
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    visited_refs: frozenset[_TargetNode] = frozenset(),
) -> Iterator[JsonSchemaObject]:
    """Yield count constraints from allOf, preserving JSON Schema $ref siblings."""
    if not obj.ref or parser._ref_siblings_are_parts_of(obj):  # noqa: SLF001
        yield obj
    if obj.ref and (target := parser._unvisited_ref_target(obj.ref, visited_refs)) is not None:  # ruff: ignore[private-member-access]
        yield from parser._iter_property_count_validation_sources(*target)  # ruff: ignore[private-member-access]
    if obj.ref and not parser._ref_siblings_are_parts_of(obj):  # ruff: ignore[private-member-access]
        return
    if (
        not obj.ref
        and parser._schema_object_is_root_ref(obj)  # ruff: ignore[private-member-access]
        and not parser._ref_siblings_are_parts_of(obj)  # ruff: ignore[private-member-access]
    ):
        return
    for item in obj.allOf:
        if not isinstance(item, JsonSchemaObject):
            continue
        if not item.ref:
            yield from parser._iter_property_count_validation_sources(item, visited_refs)  # noqa: SLF001
            continue
        if parser._ref_siblings_are_parts_of(item):  # noqa: SLF001
            yield item
        if (target := parser._unvisited_ref_target(item.ref, visited_refs)) is not None:  # noqa: SLF001
            yield from parser._iter_property_count_validation_sources(*target)  # noqa: SLF001


def should_parse_object_with_schema_validators(parser: SchemaValidatorsHost, obj: JsonSchemaObject) -> bool:
    """Return whether ``obj`` is parsed as an object model that carries schema validator rules."""
    if not parser.generate_schema_validators:
        return False
    has_pattern_properties = bool(obj.patternProperties)
    if has_pattern_properties:
        return True
    if obj.properties and has_required_group_validators(parser, obj):
        return True
    if obj.properties and parser._has_property_count_validator(obj):  # noqa: SLF001
        return True
    has_conditional_properties = any(
        branch.properties or branch.patternProperties
        for branch in parser._iter_conditional_branches(obj)  # noqa: SLF001
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
            names_by_property[property_name] = parser._field_input_names(field)  # noqa: SLF001

    for base_class in base_classes:
        data_model = base_class.source if isinstance(base_class.source, DataModel) else None
        if data_model is not None:
            for field in data_model.iter_all_fields():
                if field.name == parser.data_model_type.TYPED_EXTRA_FIELD_NAME:
                    continue
                property_name = _field_source_name(field)
                if property_name is not None:
                    names_by_property.setdefault(property_name, parser._field_input_names(field))  # noqa: SLF001
            continue

        for schema, _, _ in parser._iter_inherited_schema_objects([base_class], frozenset()):  # noqa: SLF001
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
) -> Iterator[JsonSchemaObject]:
    for source, _ in parser._iter_scoped_schema_validation_sources(  # noqa: SLF001
        obj, visited_refs, include_references=include_references
    ):
        yield source


def iter_scoped_schema_validation_sources(
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    visited_refs: frozenset[_TargetNode] = frozenset(),
    scope: str | None = "",
    *,
    include_references: bool = True,
) -> Iterator[tuple[JsonSchemaObject, str | None]]:
    """Yield the schema and its allOf sources with the document each was read from.

    The scope of a source is the file part of its resolved reference, ``""`` for the schema itself, and None
    when the document is not known: a reference followed inside another document resolves against the current
    one.
    """
    yield obj, scope
    for item in obj.allOf:
        if not isinstance(item, JsonSchemaObject):
            continue
        if item.ref:
            if parser._ref_sibling_keywords_enabled:  # noqa: SLF001
                has_sibling_constraints = (
                    item.patternProperties
                    or item.additionalProperties is not None
                    or item.unevaluatedProperties is not None
                )
                if item.allOf or item.oneOf or item.anyOf or "if" in item.extras or has_sibling_constraints:
                    yield from parser._iter_scoped_schema_validation_sources(  # noqa: SLF001
                        item.model_copy(update={"ref": None}), visited_refs, scope, include_references=False
                    )
            if not include_references:
                continue
            resolved_ref = parser._ref_path(item.ref)  # noqa: SLF001
            if (node := parser._key_node(resolved_ref)) in visited_refs:  # noqa: SLF001
                continue
            file_part = resolved_ref.split("#", 1)[0]
            root_file = parser.model_resolver.resolve_ref("#").split("#", 1)[0]
            yield from parser._iter_scoped_schema_validation_sources(  # noqa: SLF001
                parser._load_ref_schema_object(item.ref),  # noqa: SLF001
                visited_refs | {node},
                None if scope is None or scope else ("" if file_part == root_file else file_part),
            )
            continue
        yield from parser._iter_scoped_schema_validation_sources(  # noqa: SLF001
            item, visited_refs, scope, include_references=include_references
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
    has_const = parser.schema_features.const_support and "const" in obj.extras
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
        node = parser._key_node(parser._ref_path(obj.ref))  # noqa: SLF001
        accepts = node in visited_refs or schema_may_accept_container(
            parser,
            parser._load_ref_schema_object(obj.ref),  # noqa: SLF001
            container_type,
            visited_refs | {node},
        )
        if not (accepts and parser._ref_sibling_keywords_enabled):  # noqa: SLF001
            return accepts
    if (values := parser._schema_literal_values(obj)) is not None and not any(  # noqa: SLF001
        isinstance(value, list if container_type == "array" else dict) for value in values
    ):
        return False
    if obj.type is not None and container_type not in declared_types(obj.type):
        return False

    if any(not parser._schema_item_may_accept_container(item, container_type, visited_refs) for item in obj.allOf):  # noqa: SLF001
        return False
    return all(
        not items or any(parser._schema_item_may_accept_container(item, container_type, visited_refs) for item in items)  # noqa: SLF001
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


def get_union_unique_items_branch_rules(
    parser: SchemaValidatorsHost,
    items: Sequence[JsonSchemaObject | bool],
    path: UniqueItemsPath,
    visited_refs: frozenset[_TargetNode],
    property_input_names: Mapping[str, tuple[str, ...]] | None,
) -> tuple[list[tuple[JsonSchemaObject | bool, tuple[UniqueItemsRule, ...]]], list[UniqueItemsRule]]:
    """Collect ordered uniqueItems rules produced by each union branch."""
    branch_rules: list[tuple[JsonSchemaObject | bool, tuple[UniqueItemsRule, ...]]] = []
    candidates: list[UniqueItemsRule] = []
    for item in items:
        if item is False:
            continue
        if item is True:
            branch_rules.append((item, ()))
            continue
        branch = item
        rules = tuple(
            parser._iter_unique_items_rules(  # noqa: SLF001
                branch,
                path,
                visited_refs,
                property_input_names,
                include_properties=True,
            )
        )
        if (
            branch.ref
            and not parser.collapse_root_models
            and (target := parser._unvisited_ref_target(branch.ref, visited_refs))  # noqa: SLF001
        ):
            rules = (
                *rules,
                *parser._iter_unique_items_rules(  # noqa: SLF001
                    target[0], path, target[1], property_input_names, include_properties=True
                ),
            )
        branch_rules.append((branch, rules))
        candidates.extend(rule for rule in rules if all(rule.path != candidate.path for candidate in candidates))
    return branch_rules, candidates


def iter_union_unique_items_rules(
    parser: SchemaValidatorsHost,
    items: Sequence[JsonSchemaObject | bool],
    path: UniqueItemsPath,
    visited_refs: frozenset[_TargetNode],
    *,
    property_input_names: Mapping[str, tuple[str, ...]] | None = None,
) -> Iterator[UniqueItemsRule]:
    """Yield paths unconditionally required by every shape-compatible union branch."""
    branch_rules, candidates = parser._get_union_unique_items_branch_rules(  # noqa: SLF001
        items, path, visited_refs, property_input_names
    )

    for candidate in candidates:
        container_type = parser._unique_items_path_container_type(candidate.path[len(path) :])  # noqa: SLF001
        for branch, rules in branch_rules:
            if not parser._schema_item_may_accept_container(branch, container_type, visited_refs):  # noqa: SLF001
                continue
            if not any(rule.path == candidate.path for rule in rules):
                break
        else:
            yield candidate


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
        tuple(data_type for data_type in data_types if parser._data_type_may_accept_array(data_type))  # noqa: SLF001
        if filter_array_branches
        else data_types
    )
    if not executable_data_types:
        return set()
    common_paths = parser._get_executable_unique_items_paths(executable_data_types[0], path, visited)  # noqa: SLF001
    for data_type in executable_data_types[1:]:
        common_paths.intersection_update(parser._get_executable_unique_items_paths(data_type, path, visited))  # noqa: SLF001
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
        case DataType(is_list=True) | DataType(is_set=True) | DataType(is_sequence=True) | DataType(is_tuple=True):
            return True
        case DataType(reference=reference) if reference is not None and isinstance(reference.source, DataModel):
            source = reference.source
            return bool(
                source.IS_ROOT_MODEL
                and source.fields
                and parser._data_type_may_accept_array(source.fields[0].data_type, visited)  # noqa: SLF001
            )
        case DataType(data_types=data_types) if data_types:
            return any(parser._data_type_may_accept_array(child, visited) for child in data_types)  # noqa: SLF001
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
            case DataType(is_list=True) | DataType(is_set=True) | DataType(is_sequence=True):
                return parser._get_common_executable_unique_items_paths(  # noqa: SLF001
                    data_type.data_types,
                    (*path, None),
                    visited,
                    filter_array_branches=False,
                )
            case DataType(is_tuple=True, tuple_item_count=int() as item_count):
                owned_paths = cast("set[UniqueItemsPath]", set())
                for index, child in enumerate(data_type.data_types[:item_count]):
                    owned_paths.update(parser._get_executable_unique_items_paths(child, (*path, index), visited))  # noqa: SLF001
                return owned_paths
            case DataType(is_tuple=True):
                return parser._get_common_executable_unique_items_paths(  # noqa: SLF001
                    data_type.data_types,
                    (*path, None),
                    visited,
                    filter_array_branches=False,
                )
            case DataType() if data_type in MAPPINGS:
                return parser._get_common_executable_unique_items_paths(  # noqa: SLF001
                    data_type.data_types,
                    (*path, UNIQUE_ITEMS_MAPPING_VALUES_PATH_STEP),
                    visited,
                    filter_array_branches=False,
                )
            case _:
                pass
        return parser._get_common_executable_unique_items_paths(data_type.data_types, path, visited)  # noqa: SLF001
    finally:
        visited.remove(data_type_id)


def iter_unique_items_rules(  # noqa: PLR0912, PLR0913
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
    path: UniqueItemsPath,
    visited_refs: frozenset[_TargetNode] = frozenset(),
    property_input_names: Mapping[str, tuple[str, ...]] | None = None,
    *,
    include_properties: bool = False,
) -> Iterator[UniqueItemsRule]:
    """Yield compact uniqueItems rules required by all applicable union branches."""
    if obj.ref:
        if parser.collapse_root_models and (target := parser._unvisited_ref_target(obj.ref, visited_refs)):  # noqa: SLF001
            yield from parser._iter_unique_items_rules(  # noqa: SLF001
                target[0], path, target[1], property_input_names, include_properties=include_properties
            )
        if not parser._ref_sibling_keywords_enabled:  # noqa: SLF001
            return
    if obj.uniqueItems is True:
        yield UniqueItemsRule(path=path)

    for item in obj.allOf:
        if not isinstance(item, JsonSchemaObject):
            continue
        yield from parser._iter_unique_items_rules(  # noqa: SLF001
            item,
            path,
            visited_refs,
            property_input_names,
            include_properties=include_properties,
        )
        if item.ref and (target := parser._unvisited_ref_target(item.ref, visited_refs)):  # noqa: SLF001
            yield from parser._iter_unique_items_rules(  # noqa: SLF001
                target[0], path, target[1], property_input_names, include_properties=include_properties
            )

    for members in (obj.anyOf, obj.oneOf):
        if members:
            yield from parser._iter_union_unique_items_rules(  # noqa: SLF001
                members, path, visited_refs, property_input_names=property_input_names
            )

    if include_properties:
        for property_name, property_schema in (obj.properties or {}).items():
            if isinstance(property_schema, JsonSchemaObject):
                yield from parser._iter_unique_items_rules(  # noqa: SLF001
                    property_schema,
                    (*path, _input_names(property_input_names, property_name)),
                    visited_refs,
                    include_properties=True,
                )

    if isinstance(obj.items, JsonSchemaObject):
        items_path_step: UniqueItemsPathStep = (
            (UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP, len(obj.prefixItems))
            if parser.schema_features.prefix_items and obj.prefixItems
            else None
        )
        yield from parser._iter_unique_items_rules(  # noqa: SLF001
            obj.items, (*path, items_path_step), visited_refs, include_properties=include_properties
        )
    for index, item in chain(
        enumerate(obj.items if isinstance(obj.items, list) else ()),
        enumerate((obj.prefixItems or ()) if parser.schema_features.prefix_items else ()),
    ):
        if isinstance(item, JsonSchemaObject):
            yield from parser._iter_unique_items_rules(  # noqa: SLF001
                item, (*path, index), visited_refs, include_properties=include_properties
            )

    if (
        not parser.schema_features.prefix_items
        and isinstance(obj.additionalItems, JsonSchemaObject)
        and isinstance(obj.items, list)
    ):
        yield from parser._iter_unique_items_rules(  # noqa: SLF001
            obj.additionalItems,
            (*path, (UNIQUE_ITEMS_ARRAY_TAIL_PATH_STEP, len(obj.items))),
            visited_refs,
            include_properties=include_properties,
        )

    for pattern, item in (obj.patternProperties or {}).items():
        if isinstance(item, JsonSchemaObject):
            yield from parser._iter_unique_items_rules(  # noqa: SLF001
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
        yield from parser._iter_unique_items_rules(  # noqa: SLF001
            obj.additionalProperties,
            (*path, additional_path_step),
            visited_refs,
            include_properties=include_properties,
        )


def add_unique_items_validator(  # noqa: PLR0913
    parser: SchemaValidatorsHost,
    reference_path: str,
    obj: JsonSchemaObject,
    fields: Sequence[DataModelFieldBase],
    base_classes: Sequence[Reference],
    *,
    is_root_model: bool,
) -> None:
    """Register ``uniqueItems`` checks as compact paths through raw input data."""
    rules: list[UniqueItemsRule] = []
    replace_inherited_rules = False

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
                property_name: parser._field_input_names(field)  # noqa: SLF001
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
        for rule in parser._iter_unique_items_rules(  # noqa: SLF001
            schema,
            path,
            property_input_names=property_input_names,
        ):
            if owned_paths is not None and is_owned_path(rule.path, owned_paths):
                continue
            if all(existing_rule.path != rule.path for existing_rule in rules):
                rules.append(rule)

    if is_root_model:
        root_type = fields[0].data_type if fields else None
        owned_paths = parser._get_executable_unique_items_paths(root_type, ()) if root_type is not None else None  # noqa: SLF001
        add_rules(
            obj,
            (),
            owned_paths,
            parser._get_input_names_by_property(fields, base_classes),  # noqa: SLF001
        )
    else:
        names_by_property = parser._get_input_names_by_property(fields, base_classes)  # noqa: SLF001
        fields_by_property = {
            property_name: field for field in fields if (property_name := _field_source_name(field)) is not None
        }
        add_rules(obj, (), property_input_names=names_by_property)
        overridden_properties: set[str] = set()
        for source in parser._iter_schema_validation_sources(obj):  # noqa: SLF001
            if not source.properties:
                continue
            for property_name, property_schema in source.properties.items():
                if not isinstance(property_schema, JsonSchemaObject):
                    continue
                if property_name in overridden_properties:
                    if next(parser._iter_unique_items_rules(property_schema, ()), None) is not None:  # noqa: SLF001
                        replace_inherited_rules = True
                    continue
                if (input_names := names_by_property.get(property_name)) is not None:
                    field = fields_by_property.get(property_name)
                    owned_paths = (
                        parser._get_executable_unique_items_paths(field.data_type, (input_names,))  # noqa: SLF001
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

    if not rules and not replace_inherited_rules:
        return
    runtime_validation = parser._schema_runtime_validation(reference_path)  # noqa: SLF001
    runtime_validation.unique_items.extend(rules)
    runtime_validation.replace_unique_items = replace_inherited_rules


def first_schema_additional_properties(
    parser: SchemaValidatorsHost, obj: JsonSchemaObject
) -> tuple[int, JsonSchemaObject, JsonSchemaObject] | None:
    """Return the index, schema and value of the first schema-valued additionalProperties of ``obj``.

    It is read from ``obj`` and then its allOf sources (``_iter_schema_validation_sources``).
    """
    for index, source in enumerate(parser._iter_schema_validation_sources(obj)):  # noqa: SLF001
        if isinstance(additional_properties := source.additionalProperties, JsonSchemaObject):
            return index, source, additional_properties
    return None


def pattern_rule_additional_properties(
    parser: SchemaValidatorsHost, obj: JsonSchemaObject
) -> tuple[int, JsonSchemaObject, JsonSchemaObject] | None:
    """Return the index, schema and value of the additionalProperties the patternProperties rules of ``obj`` type.

    They are the first schema-valued additionalProperties (``_first_schema_additional_properties``), except one
    that is ``false`` written as an allOf (``_is_false_all_of``): the rules forbid the keys it covers instead.
    """
    if (additional_property := parser._first_schema_additional_properties(obj)) is None or _is_false_all_of(  # noqa: SLF001
        additional_property[2]
    ):
        return None
    return additional_property


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
        if pattern is False or _is_false_all_of(pattern):
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


def add_pattern_properties_validator(  # noqa: PLR0913, PLR0917
    parser: SchemaValidatorsHost,
    reference_path: str,
    name: str,
    obj: JsonSchemaObject,
    path: list[str],
    fields: Sequence[DataModelFieldBase],
    base_classes: Sequence[Reference],
) -> None:
    """Register the patternProperties rule of ``obj`` and its allOf sources."""
    pattern_value_types, rejected_patterns, additional_property_type, allow_unmatched = (
        parser._collect_pattern_property_validators(name, obj, path)  # noqa: SLF001
    )
    if not pattern_value_types and not rejected_patterns:
        return

    names_by_property = parser._get_input_names_by_property(fields, base_classes)  # noqa: SLF001
    declared_names = tuple(sorted({name for names in names_by_property.values() for name in names}))
    rule_type = PatternPropertiesRule
    helpers_kept = (
        any((parser.config.base_class, parser.config.base_class_map, parser.config.custom_template_dir))
        or parser.config.extra_template_data is not None
    )
    declared_witness = "legacy-validated-pattern-maps" in parser._legacy_object_maps  # noqa: SLF001
    intersection = (
        None
        if helpers_kept
        else pattern_validation_loses_raw_value(
            type(parser), obj, pattern_value_types, declared_witness=declared_witness
        )
    )
    if obj is parser._pattern_validation_document_root:  # noqa: SLF001
        if intersection is None and not parser.force_optional_for_required_fields:
            intersection = pattern_validation_with_undeclared_required(
                type(parser), obj, pattern_value_types, declared_witness=declared_witness
            )
        if (
            intersection is None
            and not obj.extras.keys() <= {"title", "description"}
            and parser._plain_pattern_root_annotations(obj, pattern_value_types)  # noqa: SLF001
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
    parser._schema_runtime_validation(reference_path).pattern_properties.append(  # noqa: SLF001
        rule_type(
            declared_properties=declared_names,
            pattern_properties=tuple(pattern_value_types),
            rejected_patterns=tuple(rejected_patterns),
            additional_property_type=additional_property_type,
            allow_unmatched=allow_unmatched,
        )
    )
    if obj is parser._pattern_validation_document_root:  # noqa: SLF001
        parser._pattern_validation_document_root = None  # noqa: SLF001


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
        groups=parser._required_group_input_names(groups, names_by_property),  # noqa: SLF001
    )
    runtime_validation = parser._schema_runtime_validation(reference_path)  # noqa: SLF001
    if rule not in runtime_validation.required_groups:
        runtime_validation.required_groups.append(rule)


def add_property_count_validator(parser: SchemaValidatorsHost, reference_path: str, obj: JsonSchemaObject) -> None:
    """Add raw-object property-count rules from this schema and its allOf sources."""
    if (property_count_rule := parser._get_property_count_rule(obj)) is None:  # noqa: SLF001
        return
    parser._schema_runtime_validation(reference_path).property_count = property_count_rule  # noqa: SLF001


def add_conditional_validator(
    parser: SchemaValidatorsHost,
    reference_path: str,
    obj: JsonSchemaObject,
    names_by_property: dict[str, tuple[str, ...]],
) -> None:
    """Register the if/then/else required-property rule of ``obj``, deduplicated by value."""
    if (predicate := parser._get_conditional_predicate(obj)) is None:  # noqa: SLF001
        return

    then_groups, else_groups = (
        (tuple(schema.required),) if isinstance(schema, JsonSchemaObject) and schema.required else ()
        for schema in (parser._get_conditional_schema(obj, "then"), parser._get_conditional_schema(obj, "else"))  # noqa: SLF001
    )
    if not then_groups and not else_groups:
        return

    condition = tuple(
        (names_by_property.get(property_name, (property_name,)), expected_values)
        for property_name, expected_values in predicate
    )
    rule = ConditionalRequiredRule(
        condition=condition,
        then_groups=parser._required_group_input_names(then_groups, names_by_property),  # noqa: SLF001
        else_groups=parser._required_group_input_names(else_groups, names_by_property),  # noqa: SLF001
    )
    runtime_validation = parser._schema_runtime_validation(reference_path)  # noqa: SLF001
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

    parser._add_pattern_properties_validator(reference_path, name, obj, path, fields, base_classes)  # noqa: SLF001
    names_by_property = parser._get_input_names_by_property(fields, base_classes)  # noqa: SLF001
    if (
        not parser.force_optional_for_required_fields
        and not obj.custom_base_path
        and not any((parser.config.base_class, parser.config.base_class_map, parser.config.custom_template_dir))
        and parser.config.extra_template_data is None
        and (missing_required := [name for name in obj.required if name not in names_by_property])
    ):
        parser._add_required_groups_validator(reference_path, "anyOf", [missing_required], names_by_property)  # noqa: SLF001
    add_group_validators(parser, reference_path, obj, names_by_property)
    if include_property_count:
        parser._add_property_count_validator(reference_path, obj)  # noqa: SLF001
    parser._add_unique_items_validator(  # noqa: SLF001
        reference_path,
        obj,
        fields,
        base_classes,
        is_root_model=False,
    )
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
        for source in parser._iter_schema_validation_sources(obj, include_references=include_references):  # noqa: SLF001
            if source is not obj:
                add_group_validators(parser, reference_path, source, names_by_property)


def add_group_validators(
    parser: SchemaValidatorsHost,
    reference_path: str,
    source: JsonSchemaObject,
    names_by_property: dict[str, tuple[str, ...]],
) -> None:
    """Register the required-property groups of the oneOf, anyOf and not of ``source``, then its if/then/else."""
    oneof_groups = parser._get_required_groups(source.oneOf)  # noqa: SLF001
    parser._add_required_groups_validator(reference_path, "oneOf", oneof_groups, names_by_property)  # noqa: SLF001
    anyof_groups = parser._get_required_groups(source.anyOf)  # noqa: SLF001
    parser._add_required_groups_validator(reference_path, "anyOf", anyof_groups, names_by_property)  # noqa: SLF001
    not_groups = get_not_required_groups(parser, source)
    parser._add_required_groups_validator(reference_path, "not", not_groups, names_by_property)  # noqa: SLF001
    parser._add_conditional_validator(reference_path, source, names_by_property)  # noqa: SLF001


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
    for branch in parser._iter_conditional_branches(obj):  # noqa: SLF001
        if not branch.properties:
            continue
        for key, value in branch.properties.items():
            if key not in merged_properties:
                merged_properties[key] = _schema_dump(value)

    if not merged_properties or set(merged_properties) == set(obj.properties or {}):
        return obj

    schema_dict = obj.model_dump(exclude_unset=True, by_alias=True)
    schema_dict["properties"] = merged_properties
    return parser._schema_object(schema_dict)  # noqa: SLF001


def collect_pattern_property_validators(
    parser: SchemaValidatorsHost,
    name: str,
    obj: JsonSchemaObject,
    path: list[str],
) -> tuple[list[tuple[str, DataType]], list[str], DataType | None, bool]:
    pattern_value_types: list[tuple[str, DataType]] = []
    rejected_patterns: list[str] = []
    allow_unmatched = True

    for source_index, source in enumerate(parser._iter_schema_validation_sources(obj)):  # noqa: SLF001
        if source.additionalProperties is False or source.unevaluatedProperties is False:
            allow_unmatched = False
        if not source.patternProperties:
            continue
        for pattern_index, (pattern, schema) in enumerate(source.patternProperties.items()):
            match schema:
                case True:
                    pattern_value_types.append((pattern, parser.data_type_manager.get_data_type(Types.any)))
                case JsonSchemaObject() if not _is_false_all_of(schema):
                    pattern_value_types.append((
                        pattern,
                        parser.data_type_manager.get_data_type(
                            Types.string,
                            field_constraints=False,
                            **_constraint_values(schema, constrained_type_keyword_order("string"), as_numbers=True),
                        )
                        if schema is parser._root_pattern_string_constraints  # noqa: SLF001
                        else parser.parse_item(
                            name,
                            schema,
                            get_special_path(
                                f"schemaValidators/patternProperties/{source_index}/{pattern_index}",
                                path,
                            ),
                        ),
                    ))
                case False | JsonSchemaObject():
                    rejected_patterns.append(pattern)

    if not pattern_value_types and not rejected_patterns:
        return pattern_value_types, rejected_patterns, None, allow_unmatched
    if (additional_property := parser._first_schema_additional_properties(obj)) is None:  # noqa: SLF001
        return pattern_value_types, rejected_patterns, None, allow_unmatched

    source_index, source, additional_properties = additional_property
    if _is_false_all_of(additional_properties):
        return pattern_value_types, rejected_patterns, None, False
    additional_property_name = f"{name}AdditionalProperty"
    additional_property_type = parser._parse_additional_properties_value(  # noqa: SLF001
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
    for source, scope in parser._iter_scoped_schema_validation_sources(obj):  # noqa: SLF001
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


def get_conditional_predicate(
    parser: SchemaValidatorsHost,
    obj: JsonSchemaObject,
) -> tuple[tuple[str, tuple[object, ...]], ...] | None:
    """Extract supported required-property predicates without dropping other conditions."""
    if_schema = parser._get_conditional_schema(obj, "if")  # noqa: SLF001
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
