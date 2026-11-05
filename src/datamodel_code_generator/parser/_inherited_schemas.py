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

from functools import partial, reduce
from itertools import islice
from typing import TYPE_CHECKING, Any, cast

from typing_extensions import Protocol, Sentinel

from datamodel_code_generator import AllOfMergeMode, Error
from datamodel_code_generator._constraint_applicability import PROPERTY_COUNT_KEYWORDS, keyword_kinds
from datamodel_code_generator._copy_on_change import unchanged
from datamodel_code_generator._json_schema import (
    COMBINATOR_POSITIONS,
    MODELED_SCHEMA_MAPS,
    admitted_types,
    non_null_types,
    schema_positions,
)
from datamodel_code_generator._mro import linearize, sort_descendants_first
from datamodel_code_generator._url_redaction import named_location
from datamodel_code_generator.imports import IMPORT_ANY
from datamodel_code_generator.model import DataModel, DataModelFieldBase
from datamodel_code_generator.model.base import _field_source_name, get_inherited_fields
from datamodel_code_generator.parser._all_of_values import holds_union_member
from datamodel_code_generator.parser._intersection import (
    INHERITED_SCHEMA_KEYWORDS,
    POSITIONAL_SCHEMA_KEYWORDS,
    all_of_member_row,
    declared_types,
    inherited_override_row,
    inherited_positional_tail,
    merge_inherited_keyword,
    merge_inherited_value,
)
from datamodel_code_generator.parser._null_facts import dump_null_sources, resolve_null_facts
from datamodel_code_generator.parser._origins import with_field_origin
from datamodel_code_generator.parser._schema_object import (
    _ANNOTATION_KEYWORDS,
    _MODELED_POSITIONS,
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
    _get_json_value_type,
    _intersect_dumps,
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
from datamodel_code_generator.types import ANY, DataType

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager

    from typing_extensions import Unpack

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._user_keys import ClassScope
    from datamodel_code_generator.parser._field_rows import (
        FieldAliasRow,
        FieldCopyFacts,
        FieldFacts,
        FieldIdentity,
        FieldRow,
    )
    from datamodel_code_generator.parser._intersection import KeywordIntersection
    from datamodel_code_generator.parser._null_facts import NullFacts, NullMode, NullSite, NullSources
    from datamodel_code_generator.parser._schema_graph import _TargetNode
    from datamodel_code_generator.reference import ModelResolver, ModelType, SchemaLocation

__all__ = [
    "InheritedSchemasHost",
    "all_of_members",
    "build_missing_required_field",
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
    "normalize_inherited_constraint_compositions",
    "normalize_inherited_nested_schema_value",
    "override_row",
    "parse_fields_beside_parents",
    "parse_inherited_schema_fields",
    "preserve_inherited_materialized_type_shape",
    "read_through_property_conjunctions",
    "remove_inherited_distributed_shape",
    "remove_unconstrained_compositions",
    "resolve_inherited_parent_property",
    "sanitize_untyped_boolean_inherited_property",
    "select_inherited_distributed_shape",
    "sort_inherited_base_classes_for_mro",
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
_NO_INHERITED_SCHEMA_MERGE = Sentinel("_NO_INHERITED_SCHEMA_MERGE")


def _inline_all_of_closure(
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
            yield from _inline_all_of_closure(parser, item, members_first=members_first)
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


class InheritedSchemasHost(Protocol):
    """The options, caches and hooks of the parser that the inherited schema reading reads."""

    _inherited_parent_property_cache: dict[
        tuple[str | int, str, frozenset[_TargetNode]],
        tuple[
            JsonSchemaObject,
            tuple[JsonSchemaObject, str, frozenset[_TargetNode]],
        ],
    ]
    _inherited_required_cache: dict[tuple[str, ...], frozenset[str]]
    _inherited_schema_ancestor_cache: dict[str, frozenset[str]]
    _inherited_schema_cache: dict[str, JsonSchemaObject]
    _inherited_schema_linearization_cache: dict[tuple[str, ...], tuple[str, ...]]
    _parses_unowned_fields: bool

    @property
    def allof_merge_mode(self) -> AllOfMergeMode:
        """How the constraints of allOf members merge (``--allof-merge-mode``)."""

    @property
    def data_type(self) -> type[DataType]:
        """The data type class of the type manager."""

    @property
    def field_name_model_type(self) -> ModelType:
        """The model type that field names are validated for."""

    @property
    def force_optional_for_required_fields(self) -> bool:
        """Whether required fields are generated as optional."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""

    @property
    def results(self) -> list[DataModel]:
        """The parsed models."""

    @property
    def strict_dotted_module_names(self) -> bool:
        """Whether dotted schema names keep their dots in module names (``--strict-dotted-module-names``)."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    @property
    def treat_dot_as_module(self) -> bool | None:
        """Whether dots in schema names make modules."""

    @property
    def use_tuple_for_closed_arrays(self) -> bool:
        """Whether closed arrays are written as tuples (``--use-tuple-for-closed-arrays``)."""

    @property
    def _legacy_intersection(self) -> frozenset[str]:
        """The rows of ``parser/_intersection.py:LEGACY_ROWS`` kept: those of each of its options that is off."""

    @property
    def _legacy_rw_variants(self) -> frozenset[str]:
        """The rows of the request/response split kept: every row without --dedupe-request-response-variants."""

    @property
    def _null_mode(self) -> NullMode:
        """The options that change how parse sites read null (``parser/_null_facts.py``)."""

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

    def _add_ref(self, ref: str) -> Reference:
        """Return the Reference at the key of a reference's edge, named after the edge's spelling if it is new."""

    def _apply_inherited_field_default(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        scope: ClassScope,
    ) -> None:
        """Resolve an inherited schema default in the derived class scope."""

    def _apply_title_as_name(self, name: str, obj: JsonSchemaObject) -> str:
        """Apply title as name if use_title_as_name is enabled."""

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

    def _build_missing_required_field(
        self,
        required_field_name: str,
        excludes: set[str],
        base_classes: list[Reference],
        scope: ClassScope,
        inherited_fields: dict[str, DataModelFieldBase] | None = None,
    ) -> DataModelFieldBase:
        """Build a field for a required name that is not declared in properties."""

    def _collect_inherited_fields_for_request_response(
        self,
        base_classes: list[Reference],
        active: frozenset[str] = frozenset(),
    ) -> list[DataModelFieldBase]:
        """Collect canonical inherited fields in declaration order with C3 winners."""

    def _copy_model_field(
        self,
        field: DataModelFieldBase,
        *,
        data_type: DataType | None = None,
        register_references: bool = True,
        identity: FieldIdentity | None = None,
        **facts: Unpack[FieldCopyFacts],
    ) -> DataModelFieldBase:
        """Copy a field and its mutable state without copying model references, made with ``identity`` and ``facts``."""

    def _drop_incompatible_inherited_constraints(
        self,
        child: dict[str, Any],
        parent_types: frozenset[str],
    ) -> dict[str, Any]:
        """Avoid applying schema constraints to incompatible Python value types."""

    def _get_allof_parent_references(
        self,
        schema: JsonSchemaObject,
        *,
        inherited: Iterable[Reference] = (),
        defining_ref: str | None = None,
    ) -> list[Reference]:
        """Collect effective allOf parents once, preserving declaration precedence."""

    def _get_deferred_inherited_parse_object(
        self,
        source_obj: JsonSchemaObject,
        deferred_property_names: frozenset[str],
    ) -> JsonSchemaObject:
        """Strip nested type work that late canonical inheritance will replace."""

    def _get_deferred_inherited_property_names(
        self,
        source_obj: JsonSchemaObject,
        parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]],
    ) -> frozenset[str]:
        """Find partial properties that need canonical generated-type resolution."""

    def _get_fixed_positional_items(
        self,
        obj: JsonSchemaObject,
        canonical_ref: Callable[[str], str] | None = None,
    ) -> list[JsonSchemaObject | bool] | None:
        """Return the positions of a positional array written as a tuple, or None when it is written as a list."""

    @classmethod
    def _get_array_items_constraints(cls, obj: JsonSchemaObject) -> dict[str, int]:
        """Return array length constraints derived from boolean items."""

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Load a JsonSchemaObject from a $ref using standard resolve/load pipeline."""

    def _get_flattenable_inherited_constraint_items(
        self,
        keyword: str,
        raw_items: list[Any],
    ) -> list[dict[str, Any]] | None:
        """Return normalized constraint-only branches when composition flattening is equivalent."""

    def _get_inherited_field_map(self, base_classes: list[Reference]) -> dict[str, DataModelFieldBase]:
        """Build the effective generated field map once for a set of direct bases."""

    def _get_inherited_field_schema(
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[_TargetNode] | None = None
    ) -> JsonSchemaObject | None:
        """Get the schema for an inherited property from parent schemas."""

    def _get_inherited_field_type(
        self, prop_name: str, base_classes: list[Reference], visited: frozenset[_TargetNode] | None = None
    ) -> DataType | None:
        """Get the data type for an inherited property from parent schemas."""

    def _get_inherited_property_map(
        self,
        base_classes: list[Reference],
    ) -> dict[str, tuple[JsonSchemaObject | bool, str]]:
        """Build the effective raw property map once in inherited C3 order."""

    def _get_inherited_schema_types(
        self,
        schema: JsonSchemaObject,
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> frozenset[str]:
        """Infer JSON instance types accepted by an inherited schema."""

    def _get_inline_required_names(self, schema: JsonSchemaObject) -> tuple[str, ...]:
        """Collect required names in declaration order from one schema and inline allOf items."""

    def _get_ref_raw_schema(self, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
        """Return the value of a loaded document at a reference key, with the references it holds as a schema."""

    def _has_inherited_constraints(self, schema: JsonSchemaObject) -> bool:
        """Return whether a partial schema contains constraints at any container depth."""

    def _inherited_override_row(self, parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """Return ``parser/_intersection.py:inherited_override_row`` under the merge mode of this parse."""

    def _inherited_override_schema(
        self, parent: JsonSchemaObject, child: JsonSchemaObject, parent_ref: str, where: str
    ) -> JsonSchemaObject:
        """Return the schema of a property ``child`` that overrides the inherited property ``parent``."""

    def _inherited_ref_context(self, resolved_ref: str) -> AbstractContextManager[None]:
        """Resolve nested references relative to the inherited schema's own file and schema resource."""

    def _intersect_ref_siblings_schema(
        self, target: dict[str, Any], siblings: dict[str, Any], where: str, *, siblings_are_parts: bool
    ) -> JsonSchemaObject:
        """Return the schema of the intersection of a referenced schema's dump and the sibling keywords of its $ref."""

    def _is_inherited_type_narrowing(
        self,
        child_types: frozenset[str],
        parent_types: frozenset[str],
    ) -> bool:
        """Return whether explicit child JSON types are contained by the inherited types."""

    def _is_partial_inherited_nested_value(
        self,
        child: object,
        parent: object,
        *,
        parent_tail: object = True,
        context: tuple[str, frozenset[_TargetNode]] = ("", frozenset()),
        schema_map: bool = False,
    ) -> bool:
        """Compare one nested schema keyword using JSON Schema intersection semantics."""

    def _is_partial_inherited_property(
        self,
        child: JsonSchemaObject,
        parent: JsonSchemaObject,
        context: tuple[str, frozenset[_TargetNode]] = ("", frozenset()),
    ) -> bool:
        """Return whether a child schema only narrows metadata/constraints of its inherited type."""

    def _is_unconstrained_inherited_schema(self, schema: object) -> bool:
        """Return whether a raw subschema adds no validation to an inherited type."""

    def _iter_inherited_schema_objects(
        self, base_classes: list[Reference], visited: frozenset[_TargetNode]
    ) -> Iterator[tuple[JsonSchemaObject, frozenset[_TargetNode], str]]:
        """Yield inherited schema objects not visited yet, with the visited schema nodes they add to."""

    def _linearize_inherited_schema_refs(self, base_classes: list[Reference]) -> tuple[str, ...]:
        """Return raw inherited schemas in the same C3 order as generated models."""

    def _load_inherited_schema_object(self, resolved_ref: str) -> JsonSchemaObject:
        """Load and cache a schema used only for inherited field inspection."""

    def _mark_inherited_materialized_type_shapes(self, schema: JsonSchemaObject) -> None:
        """Mark synthesized schemas whose container constraints need a root wrapper."""

    def _mark_reported_value_conflict(self, schema: JsonSchemaObject, keywords: Sequence[str]) -> None:
        """Mark a merged schema whose ``enum`` or ``const`` conflict the intersection warning already reports."""

    def _merge_inherited_type_shape_dict(
        self,
        parent: dict[str, Any],
        child: dict[str, Any],
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> dict[str, Any]:
        """Fill missing type-shape keywords while keeping child validation precedence."""

    def _merge_inherited_union_branch(
        self,
        parent: object,
        child: dict[str, Any],
        distributed_fields: frozenset[str],
        context: tuple[str, frozenset[_TargetNode]],
        *,
        excludes_null: bool,
    ) -> object:
        """Apply type-specific child keywords to one compatible inherited branch."""

    def _merge_properties_with_parent_constraints(
        self,
        child_obj: JsonSchemaObject,
        base_classes: list[Reference],
        parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]] | None = None,
        deferred_property_names: frozenset[str] | None = None,
    ) -> JsonSchemaObject:
        """Merge child properties with parent property constraints for allOf inheritance."""

    def _normalize_inherited_constraint_compositions(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        """Flatten constraint-only compositions before resolving their inherited type."""

    def _null_facts(
        self,
        obj: JsonSchemaObject,
        site: NullSite,
        *,
        required: bool = False,
        has_default: bool = False,
        parts: Sequence[NullSources] = (),
    ) -> NullFacts:
        """Return what ``site`` reads from where the null of ``obj`` comes from (``parts``: of its typed parts)."""

    def _prepare_required_inherited_field(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        overriding_field: DataModelFieldBase | None = None,
    ) -> None:
        """Remove inherited constructor defaults while retaining explicit child metadata."""

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names."""

    def _ref_target_dump(self, ref: str, resolved_ref: str) -> tuple[dict[str, Any], str]:
        """Return a fresh dump of a referenced schema for the current document, and the reference it ends at."""

    def _ref_siblings_are_parts_of(self, schema: JsonSchemaObject) -> bool:
        """Return whether the keywords beside the ``$ref`` of ``schema`` are parts of it."""

    def _remove_inherited_distributed_shape(
        self,
        child: dict[str, Any],
        distributed_fields: frozenset[str],
    ) -> dict[str, Any]:
        """Remove distributed keywords while preserving unrelated extension values."""

    def _remove_unconstrained_compositions(self, schema_dict: dict[str, Any]) -> dict[str, Any]:
        """Remove composition branches that make an inherited override universally valid."""

    def _resolve_inherited_parent_property(
        self,
        schema: JsonSchemaObject,
        parent_ref: str,
        active: frozenset[_TargetNode] = frozenset(),
    ) -> tuple[JsonSchemaObject, str, frozenset[_TargetNode]]:
        """Resolve pure property references before comparing nested override shapes."""

    def _sanitize_untyped_boolean_inherited_property(
        self,
        child: JsonSchemaObject,
    ) -> JsonSchemaObject | None:
        """Drop type-specific constraints when a boolean parent supplies no usable type."""

    def _schema_location(self, resolved_ref: str) -> str:
        """Return the registry location of a resolved reference."""

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw schema into a schema object."""

    def _schema_resource_document(self, path_parts: list[str]) -> str:
        """Identify one physical schema document without changing its public reference names."""

    def _select_inherited_distributed_shape(
        self,
        child: dict[str, Any],
        distributed_fields: frozenset[str],
        parent_types: frozenset[str],
    ) -> dict[str, Any]:
        """Select child keywords that apply to one inherited JSON type branch."""

    def _shared_schema_object(self, raw: dict[str, YamlValue] | YamlValue, path: list[str]) -> JsonSchemaObject:
        """Return the schema object a value of a loaded document validates to, shared by its readers."""

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

    def _unregister_temporary_field_references(
        self,
        fields: Iterable[DataModelFieldBase],
    ) -> None:
        """Keep temporary raw fields out of the live reverse-reference graph."""

    def _warn_intersection(self, where: str, intersection: KeywordIntersection, *parts: str) -> None:
        """Warn about the keywords of an intersection whose parts have no value in common, and its untaken patterns."""


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
        and (fixed_items := parser._get_fixed_positional_items(schema, canonical_ref)) is not None  # noqa: SLF001
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
    schema, parent_ref, active = parser._resolve_inherited_parent_property(schema, parent_ref, active)  # noqa: SLF001
    nullable_types = frozenset(("null",)) if parser._null_facts(schema, "inherited-types").admits_null else frozenset()  # noqa: SLF001
    if direct_types := frozenset(declared_types(schema.type)):
        return direct_types | nullable_types

    structural_types = (
        frozenset(("array",))
        if schema.is_array
        else frozenset(("object",))
        if (
            schema.is_object
            or schema.additionalProperties is not None
            or schema.patternProperties
            or schema.propertyNames is not None
        )
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
            combined_types.update(parser._get_inherited_schema_types(item, parent_ref, active))  # noqa: SLF001
    if combined_types:
        return frozenset(combined_types) | nullable_types

    all_of_types: frozenset[str] | None = None
    for item in schema.allOf:
        if not isinstance(item, JsonSchemaObject):
            continue
        if not (item_types := parser._get_inherited_schema_types(item, parent_ref, active)):  # noqa: SLF001
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
            if not selected_value:
                continue
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
        parser._is_unconstrained_inherited_schema(item)  # noqa: SLF001
        for item in any_of
    ):
        result.pop("anyOf")
    if (
        isinstance(one_of := result.get("oneOf"), list)
        and len(one_of) == 1
        and parser._is_unconstrained_inherited_schema(one_of[0])  # noqa: SLF001
    ):
        result.pop("oneOf")
    if isinstance(all_of := result.get("allOf"), list):
        if constrained_items := [item for item in all_of if not parser._is_unconstrained_inherited_schema(item)]:  # noqa: SLF001
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

    normalized_items: list[dict[str, Any]] = []
    for item in items:
        match item:
            case True:
                normalized_items.append({})
            case dict():
                normalized_item = parser._normalize_inherited_constraint_compositions(parser._schema_object(item))  # noqa: SLF001
                if get_inherited_override_shape(parser, normalized_item):
                    return None
                normalized_items.append(normalized_item.model_dump(exclude_unset=True, by_alias=True))
            case _:
                return None
    return normalized_items


def normalize_inherited_nested_schema_value(parser: InheritedSchemasHost, value: object) -> object | None:
    """Normalize schema-valued container keywords without copying unchanged branches."""
    match value:
        case JsonSchemaObject() as nested_schema:
            if (normalized := parser._normalize_inherited_constraint_compositions(nested_schema)) is nested_schema:  # noqa: SLF001
                return None
            return normalized.model_dump(exclude_unset=True, by_alias=True)
        case list() | dict():
            members = value if isinstance(value, dict) else dict(enumerate(value))
            normalized = {
                key: parser._normalize_inherited_constraint_compositions(item)  # noqa: SLF001
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
                    normalized_items := parser._get_flattenable_inherited_constraint_items(  # noqa: SLF001
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
    child_dict = parser._remove_unconstrained_compositions(get_inherited_override_shape(parser, child))  # noqa: SLF001
    if not child_dict:
        return True

    if (child_type := child_dict.pop("type", None)) is not None:
        child_types = frozenset(declared_types(child_type))
        parent_types = parser._get_inherited_schema_types(parent, parent_ref)  # noqa: SLF001
        if not parser._is_inherited_type_narrowing(child_types, parent_types):  # noqa: SLF001
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
        if not parser._is_partial_inherited_nested_value(  # noqa: SLF001
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
    if not schema_map and isinstance(child, dict) and parser._is_unconstrained_inherited_schema(child):  # noqa: SLF001
        return True
    result = False
    match child, parent:
        case dict() as child_schema, JsonSchemaObject() as parent_schema:
            child_obj = parser._schema_object(child_schema)  # noqa: SLF001
            effective_parent = parent_schema
            effective_ref = parent_ref
            next_active = active
            if parent_schema.ref:
                effective_parent, effective_ref, next_active = parser._resolve_inherited_parent_property(  # noqa: SLF001
                    parent_schema,
                    parent_ref,
                    active - {inherited_node(parser, parser._ref_path(parent_schema.ref))},  # noqa: SLF001
                )
            result = parser._is_partial_inherited_property(  # noqa: SLF001
                child_obj,
                effective_parent,
                (effective_ref, next_active),
            )
        case list() as child_schemas, list() as parent_schemas:
            result = all(
                parser._is_partial_inherited_nested_value(  # noqa: SLF001
                    child_schema,
                    parent_schemas[index] if index < len(parent_schemas) else parent_tail,
                    context=context,
                )
                for index, child_schema in enumerate(child_schemas)
            )
        case dict() as child_schema_map, dict() as parent_schema_map:
            result = all(
                key in parent_schema_map
                and parser._is_partial_inherited_nested_value(  # noqa: SLF001
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
        parser._has_inherited_constraints(nested_schema)  # noqa: SLF001
        for field_name in INHERITED_SCHEMA_KEYWORDS
        for nested_schema in _nested_schema_objects(getattr(schema, field_name))
    )


def mark_inherited_materialized_type_shapes(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> None:
    """Mark synthesized schemas whose container constraints need a root wrapper."""
    schema.__dict__[_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY] = True
    for field_name in _MODELED_POSITIONS:
        for nested_schema in _nested_schema_objects(getattr(schema, field_name)):
            parser._mark_inherited_materialized_type_shapes(nested_schema)  # noqa: SLF001


def preserve_inherited_materialized_type_shape(
    parser: InheritedSchemasHost,
    source: JsonSchemaObject,
    target: JsonSchemaObject,
) -> JsonSchemaObject:
    """Propagate the internal materialization marker across schema normalization."""
    if source.__dict__.get(_INHERITED_MATERIALIZED_TYPE_SHAPE_KEY):
        parser._mark_inherited_materialized_type_shapes(target)  # noqa: SLF001
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
            cache_identity = parser._ref_path(schema_ref)  # noqa: SLF001
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
        resolved_ref = parser._ref_path(schema.ref)  # noqa: SLF001
        if (node := inherited_node(parser, resolved_ref)) in active:
            break
        active |= {node}
        referenced_schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
        if schema.has_ref_with_schema_keywords:
            schema = parser._intersect_ref_siblings_schema(  # noqa: SLF001
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
        return active - {inherited_node(parser, parser._ref_path(parent_ref))}  # noqa: SLF001
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
    parent_types = parser._get_inherited_schema_types(parent_obj, parent_ref, active)  # noqa: SLF001
    if excludes_null and parent_types == {"null"}:
        return _NO_INHERITED_SCHEMA_MERGE
    branch_child = parser._select_inherited_distributed_shape(  # noqa: SLF001
        child,
        distributed_fields,
        parent_types,
    )
    if excludes_null and "null" in parent_types:
        branch_child["nullable"] = False
    return _located(
        parser._merge_inherited_type_shape_dict(  # noqa: SLF001
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
    effective_parent, effective_ref, next_active = parser._resolve_inherited_parent_property(  # noqa: SLF001
        parent_obj,
        parent_ref,
        active,
    )
    parent_types = parser._get_inherited_schema_types(effective_parent, effective_ref, next_active)  # noqa: SLF001
    parent_shape = get_inherited_type_shape(parser, effective_parent, parser._ref_path)  # noqa: SLF001
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
                merged_branch := parser._merge_inherited_union_branch(  # noqa: SLF001
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
        parent_shape = parser._remove_inherited_distributed_shape(  # noqa: SLF001
            parent_shape,
            frozenset(parent_distributed_fields),
        )
        parent_shape.pop("type", None)
        parent_shape["anyOf"] = [
            _located(
                parser._merge_inherited_type_shape_dict(  # noqa: SLF001
                    {
                        "type": parent_type,
                        **parser._select_inherited_distributed_shape(  # noqa: SLF001
                            branch_parent_shape,
                            frozenset(parent_distributed_fields),
                            frozenset((parent_type,)),
                        ),
                    },
                    parser._select_inherited_distributed_shape(  # noqa: SLF001
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
        child_shape = parser._remove_inherited_distributed_shape(child, distributed_fields)  # noqa: SLF001
        if union_key is None and len(parent_types) <= 1:
            child_shape.update(parser._select_inherited_distributed_shape(child, distributed_fields, parent_types))  # noqa: SLF001
    if (
        parent_shape.get("allOf")
        and len(parent_types) == 1
        and (parent_type := next(iter(parent_types))) not in {"array", "object"}
    ):
        parent_shape = {"type": parent_type}
    child_shape = parser._drop_incompatible_inherited_constraints(  # noqa: SLF001
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
        merge_schemas=lambda parent_schema, child_schema: parser._merge_inherited_type_shape_dict(  # noqa: SLF001
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
    (``_merge_inherited_type_shape_dict``) where the child only narrows it, and keeps the child otherwise; ``all``
    and ``constraints`` give the intersection of both (``intersect_keywords``) over their presentation row
    (``parser/_intersection.py:inherited_override_row``). A child without constraints that only narrows the inherited
    property is written on the type shape too, unless ``legacy-type-narrowing-override-deferred`` is kept, where it
    is the copy of the inherited field and none reaches here (``get_deferred_inherited_property_names``).
    """
    if parser.allof_merge_mode == AllOfMergeMode.NoMerge:
        if (
            "legacy-type-narrowing-override-deferred" in parser._legacy_intersection  # noqa: SLF001
            and not parser._has_inherited_constraints(child)  # noqa: SLF001
        ):
            return child
        effective_parent, effective_ref, active = parser._resolve_inherited_parent_property(parent, parent_ref)  # noqa: SLF001
        if not parser._is_partial_inherited_property(child, effective_parent, (effective_ref, active)):  # noqa: SLF001
            return child
        child_dict = child.model_dump(exclude_unset=True, by_alias=True)
        merged_dict = parser._merge_inherited_type_shape_dict(  # noqa: SLF001
            parent.model_dump(exclude_unset=True, by_alias=True), child_dict, parent_ref
        )
        if merged_dict == child_dict:
            return child
        merged_schema = parser._schema_object(merged_dict)  # noqa: SLF001
        parser._mark_inherited_materialized_type_shapes(merged_schema)  # noqa: SLF001
        return merged_schema
    parent_dict = parent.model_dump(exclude_unset=True, by_alias=True)
    child_dict = child.model_dump(exclude_unset=True, by_alias=True)
    intersection = _intersect_dumps(
        [parent_dict, child_dict],
        parser._inherited_override_row(parent_dict, child_dict),  # noqa: SLF001
        legacy_rows=parser._legacy_intersection,  # noqa: SLF001
        removes_enum_outside_type="legacy-enum-outside-type-kept" not in parser._legacy_intersection,  # noqa: SLF001
    )
    parser._warn_intersection(where, intersection)  # noqa: SLF001
    merged_property = parser._schema_object(intersection.keywords)  # noqa: SLF001
    parser._mark_reported_value_conflict(merged_property, intersection.unsatisfiable)  # noqa: SLF001
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
    shape = parser._remove_unconstrained_compositions(get_inherited_override_shape(parser, child))  # noqa: SLF001
    if (child_type := shape.pop("type", None)) is not None and not set(
        non_null_types(parser._get_inherited_schema_types(parent, parent_ref, active))  # noqa: SLF001
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
                return parser._is_unconstrained_inherited_schema(child_dump)  # noqa: SLF001
            return child_dump == parent or parser._is_partial_inherited_nested_value(  # noqa: SLF001
                parent, child_schema, context=context
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
                normalized_child = parser._normalize_inherited_constraint_compositions(child_schema)  # noqa: SLF001
                effective_parent, effective_ref, active = parser._resolve_inherited_parent_property(  # noqa: SLF001
                    parent_property,
                    inherited_property[1],
                )
                if not parser._is_partial_inherited_property(  # noqa: SLF001
                    normalized_child,
                    effective_parent,
                    (effective_ref, active),
                ):
                    continue
                if parser._has_inherited_constraints(normalized_child):  # noqa: SLF001
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
    if not parser._has_inherited_constraints(child):  # noqa: SLF001
        return None
    child_dict = child.model_dump(exclude_unset=True, by_alias=True)
    sanitized_dict = parser._drop_incompatible_inherited_constraints(  # noqa: SLF001
        parser._remove_inherited_distributed_shape(  # noqa: SLF001
            child_dict,
            get_inherited_distributed_fields(parser, child),
        ),
        frozenset(),
    )
    return parser._schema_object(sanitized_dict)  # noqa: SLF001


def merge_properties_with_parent_constraints(  # noqa: PLR0912
    parser: InheritedSchemasHost,
    child_obj: JsonSchemaObject,
    base_classes: list[Reference],
    parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]] | None = None,
    deferred_property_names: frozenset[str] | None = None,
) -> JsonSchemaObject:
    """Merge child properties with parent property constraints for allOf inheritance."""
    if not child_obj.properties:
        return child_obj

    if parent_properties is None:
        parent_properties = parser._get_inherited_property_map(base_classes)  # noqa: SLF001
    if not parent_properties:
        return child_obj
    if deferred_property_names is None:
        deferred_property_names = parser._get_deferred_inherited_property_names(  # noqa: SLF001
            child_obj,
            parent_properties,
        )

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
        effective_child = parser._normalize_inherited_constraint_compositions(child_prop)  # noqa: SLF001
        if effective_child is not child_prop:
            merged_changed = True
        if not isinstance(parent_prop, JsonSchemaObject):
            if isinstance(parent_prop, bool) and (
                sanitized_child := parser._sanitize_untyped_boolean_inherited_property(effective_child)  # noqa: SLF001
            ):
                merged_properties[prop_name] = sanitized_child
                merged_changed = True
            else:
                merged_properties[prop_name] = effective_child
            continue
        if prop_name in deferred_property_names:
            child_dict = effective_child.model_dump(exclude_unset=True, by_alias=True)
            normalized_dict = parser._remove_unconstrained_compositions(child_dict)  # noqa: SLF001
            merged_properties[prop_name] = (
                parser._schema_object(normalized_dict) if normalized_dict != child_dict else effective_child  # noqa: SLF001
            )
            merged_changed = merged_changed or normalized_dict != child_dict
            continue
        merged_property = parser._inherited_override_schema(  # noqa: SLF001
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
    """Return the schema graph node of the inherited schema at a key (``_load_inherited_schema_object``).

    A schema that does not load is its key: the load that reads it raises.
    """
    try:
        schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
    except (Error, OSError):
        return resolved_ref
    return _schema_node(schema, resolved_ref)


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

    A schema of another document is its dump for this document (``_ref_target_dump``), where each ``$recursiveRef``
    that has a fixed target there names it, as a schema merged from there reads it.
    """
    resolved_ref = parser._ref_path(ref)  # noqa: SLF001
    document = parser._schema_resource_document(list(parser.model_resolver.current_root))  # noqa: SLF001
    if parser._schema_location(resolved_ref).partition("#")[0] == document:  # noqa: SLF001
        return target
    return parser._schema_object(parser._ref_target_dump(ref, resolved_ref)[0])  # noqa: SLF001


def _read_within(schema: JsonSchemaObject, chain: frozenset[SchemaLocation | None]) -> JsonSchemaObject:
    """Return a copy of an allOf member marked as read within the locations ``chain`` (``_read_through_members``)."""
    copied = schema.model_copy()
    copied.__dict__[_READ_THROUGH_KEY] = chain
    return copied


def read_through_property_conjunctions(
    parser: InheritedSchemasHost, schema: JsonSchemaObject
) -> dict[str, JsonSchemaObject]:
    """Return, by name, the one schema of each property that allOf members read within different locations declare.

    A ``$ref`` member read in place (``_read_through_members``) is no base class whose properties the other members
    override, so a property it declares and a member outside it declares too would be two fields of the site, the
    first of which wins. The property is one field instead, the conjunction of every declaration
    (``_conjoined_declarations``). The members are read in the order ``parse_all_of`` parses them; a property declared
    only within one location, or that one of them declares as a boolean schema, keeps its first declaration. Empty
    under ``legacy-ref-member-union-dropped``, where no member is read in place.
    """
    if "legacy-ref-member-union-dropped" in parser._legacy_intersection:  # noqa: SLF001
        return {}
    declarations: dict[str, list[tuple[JsonSchemaObject | bool, object]]] = {}
    for source in islice(_inline_all_of_closure(parser, schema), 1, None):
        for name, declared in (source.properties or {}).items():
            declarations.setdefault(name, []).append((declared, source.__dict__.get(_READ_THROUGH_KEY)))
    return {
        name: _conjoined_declarations(parser, name, schemas)
        for name, declared_within in declarations.items()
        if len({within for _, within in declared_within}) > 1
        and len(schemas := [declared for declared, _ in declared_within if isinstance(declared, JsonSchemaObject)])
        == len(declared_within)
    }


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
    intersection = _intersect_dumps(
        dumps,
        reduce(all_of_member_row, dumps[1:], dumps[0]),
        removes_enum_outside_type="legacy-enum-outside-type-kept" not in parser._legacy_intersection,  # noqa: SLF001
        own_last=False,
    )
    location = declarations[0].schema_location
    parser._warn_intersection(  # noqa: SLF001
        named_location(location, f"property {stable_repr(name)}"), intersection, "the allOf members that declare it"
    )
    merged = parser._schema_object(intersection.keywords).with_schema_location(location)  # noqa: SLF001
    parser._mark_reported_value_conflict(merged, intersection.unsatisfiable)  # noqa: SLF001
    mark_conjunction_parts(merged)
    return merged


def with_conjoined_properties(
    schema: JsonSchemaObject, conjunctions: Mapping[str, JsonSchemaObject], written: set[str]
) -> JsonSchemaObject:
    """Return an allOf member with the properties of ``conjunctions`` (``read_through_property_conjunctions``) once.

    The first member that declares one of them declares its conjunction, and a later member leaves it out; ``written``
    holds the names written so far, and takes those this member writes.
    """
    if not (names := conjunctions.keys() & (schema.properties or {}).keys()):
        return schema
    properties = {
        name: conjunctions[name] if name in names else declared
        for name, declared in (schema.properties or {}).items()
        if name not in names or name not in written
    }
    written.update(names)
    return schema.model_copy(update={"properties": properties})


def alias_target(parser: InheritedSchemasHost, ref: str) -> str:
    """Return the reference a chain of aliases from ``ref`` ends at, ``ref`` itself when it names no alias.

    An alias is a schema that is a ``$ref`` beside annotations only (``has_ref_with_schema_keywords`` false): it is
    the same schema as its target in every output form, unless an ``x-python-import`` types its references by the
    class it imports. A reference the chain has passed stops it.
    """
    seen = {parser._ref_path(ref)}  # noqa: SLF001
    while (
        (target := parser._load_ref_schema_object(ref)).ref  # noqa: SLF001
        and not target.has_ref_with_schema_keywords
        and "x-python-import" not in target.extras
        and (path := parser._ref_path(target.ref)) not in seen  # noqa: SLF001
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
            else parser.model_resolver.add_ref(parser._ref_path(ref), resolved=True)  # noqa: SLF001
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
    schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
    if (node := _schema_node(schema, resolved_ref)) in active:
        return frozenset()
    ancestors: set[str] = set()
    for ref in iter_allof_refs(parser, schema):
        parent_ref = parser._ref_path(ref)  # noqa: SLF001
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


def linearize_inherited_schema_refs(parser: InheritedSchemasHost, base_classes: list[Reference]) -> tuple[str, ...]:
    """Return raw inherited schemas in the same C3 order as generated models.

    The parents of each schema are its allOf references in document order.
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
                lambda ref: [
                    parser._ref_path(parent)  # noqa: SLF001
                    for parent in iter_allof_refs(parser, parser._load_inherited_schema_object(ref))  # noqa: SLF001
                ],
                lambda ref: _schema_node(parser._load_inherited_schema_object(ref), ref),  # noqa: SLF001
            )
        )
    return parser._inherited_schema_linearization_cache[direct_refs]  # noqa: SLF001


def get_inherited_property_map(
    parser: InheritedSchemasHost,
    base_classes: list[Reference],
) -> dict[str, tuple[JsonSchemaObject | bool, str]]:
    """Build the effective raw property map once in inherited C3 order."""
    properties: dict[str, tuple[JsonSchemaObject | bool, str]] = {}
    for ref in parser._linearize_inherited_schema_refs(base_classes):  # noqa: SLF001
        for source in _inline_all_of_closure(parser, parser._load_inherited_schema_object(ref)):  # noqa: SLF001
            for name, prop_schema in (source.properties or {}).items():
                properties.setdefault(name, (prop_schema, ref))
    return properties


def get_inherited_required_names(parser: InheritedSchemasHost, base_classes: list[Reference]) -> frozenset[str]:
    """Collect required names across the effective inherited allOf graph."""
    linearized_refs = parser._linearize_inherited_schema_refs(base_classes)  # noqa: SLF001
    if linearized_refs in parser._inherited_required_cache:  # noqa: SLF001
        return parser._inherited_required_cache[linearized_refs]  # noqa: SLF001
    result = parser._inherited_required_cache[linearized_refs] = frozenset(  # noqa: SLF001
        name
        for ref in linearized_refs
        for source in _inline_all_of_closure(parser, parser._load_inherited_schema_object(ref))  # noqa: SLF001
        for name in source.required
    )
    return result


def get_inline_required_names(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> tuple[str, ...]:
    """Collect required names in declaration order from one schema and inline allOf items."""
    return tuple(dict.fromkeys(name for source in _inline_all_of_closure(parser, schema) for name in source.required))


def get_inline_property_names(parser: InheritedSchemasHost, schema: JsonSchemaObject) -> frozenset[str]:
    """Collect properties declared directly by one schema and its inline allOf items."""
    return frozenset(name for source in _inline_all_of_closure(parser, schema) for name in source.properties or ())


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
        schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
        if (node := _schema_node(schema, resolved_ref)) in visited_refs:
            return
        visited_refs.add(node)
        parent_refs = parser._get_allof_parent_references(  # noqa: SLF001
            schema,
            defining_ref=resolved_ref,
        )
        for parent in sort_inherited_base_classes_for_mro(parser, parent_refs):
            collect_ref(parent.path)
        for source in _inline_all_of_closure(parser, schema, members_first=True):
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


def parse_fields_beside_parents(  # noqa: PLR0913, PLR0917
    parser: InheritedSchemasHost,
    source: JsonSchemaObject,
    parent_refs: list[Reference],
    parent_properties: dict[str, tuple[JsonSchemaObject | bool, str]],
    path: list[str],
    scope: ClassScope,
) -> list[DataModelFieldBase]:
    """Parse the properties of ``source`` for the class ``scope``, merged with the parent properties they override.

    A property that only narrows its parent's is parsed without the nested types the parent's type replaces
    and marked for that late resolution.
    """
    deferred_names = parser._get_deferred_inherited_property_names(source, parent_properties)  # noqa: SLF001
    merged = parser._merge_properties_with_parent_constraints(source, parent_refs, parent_properties, deferred_names)  # noqa: SLF001
    fields = parser.parse_object_fields(
        parser._get_deferred_inherited_parse_object(merged, deferred_names),  # noqa: SLF001
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
    inherited_properties = parser._get_inherited_property_map(parent_refs)  # noqa: SLF001
    reference = get_inherited_field_owner_reference(parser, reference, schema)
    scope = parser.model_resolver.class_scope("reference-name", reference, reference.path)
    fields: list[DataModelFieldBase] = []
    owner_path = [] if "legacy-rw-unowned-parent-fields" in parser._legacy_rw_variants else [reference.path]  # noqa: SLF001

    _, *members = _inline_all_of_closure(parser, schema)
    with (
        parser._inherited_ref_context(reference.path),  # noqa: SLF001
        context_variable(
            partial(setattr, parser, "_parses_unowned_fields"),
            parser._parses_unowned_fields,  # noqa: SLF001
            parser._parses_unowned_fields or not owner_path,  # noqa: SLF001
        ),
    ):
        for source in (*members, schema):
            if not source.properties:
                continue
            parsed_fields = parse_fields_beside_parents(
                parser, source, parent_refs, inherited_properties, owner_path, scope
            )
            parser._unregister_temporary_field_references(parsed_fields)  # noqa: SLF001
            fields.extend(parsed_fields)
    if not parser.force_optional_for_required_fields:
        existing_names = {field_name for field in fields if (field_name := _field_source_name(field)) is not None}
        inherited_fields = {
            field_name: field for field in parent_fields if (field_name := _field_source_name(field)) is not None
        }
        for required_name in parser._get_inline_required_names(schema):  # noqa: SLF001
            if required_name in existing_names:
                continue
            field = parser._build_missing_required_field(  # noqa: SLF001
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
            parent_schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
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
    schema = parser._shared_schema_object(parser._get_ref_raw_schema(resolved_ref), [resolved_ref])  # noqa: SLF001
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

    for field in parser._collect_inherited_fields_for_request_response(base_classes):  # noqa: SLF001
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
    return (inherited_fields if inherited_fields is not None else parser._get_inherited_field_map(base_classes)).get(  # noqa: SLF001
        prop_name
    )


def get_inherited_field_schema(
    parser: InheritedSchemasHost,
    prop_name: str,
    base_classes: list[Reference],
    visited: frozenset[_TargetNode] | None = None,
) -> JsonSchemaObject | None:
    """Get the schema for an inherited property from parent schemas."""
    if visited is None:
        visited = frozenset()

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
            resolved_ref = parser._ref_path(item.ref)  # noqa: SLF001
            parent_schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
            if (node := _schema_node(parent_schema, resolved_ref)) in current_visited:
                continue
            if property_schema := find_property(parent_schema, current_visited | {node}):
                return property_schema
        return None

    for parent_schema, next_visited, _ in parser._iter_inherited_schema_objects(base_classes, visited):  # noqa: SLF001
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
        parser._prepare_required_inherited_field(copied_field, inherited_field)  # noqa: SLF001
        parser._apply_inherited_field_default(copied_field, inherited_field, scope=scope)  # noqa: SLF001
        return copied_field

    deferred = parser._get_inherited_field_schema(required_field_name, base_classes) is not None  # noqa: SLF001
    field = parser._build_field(  # noqa: SLF001
        "legacy-alias-option-only",
        parser.data_type()
        if deferred
        else parser._get_inherited_field_type(required_field_name, base_classes)  # noqa: SLF001
        or DataType(type=ANY, import_=IMPORT_ANY),
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
