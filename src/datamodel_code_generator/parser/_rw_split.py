"""The request and response model split of the JSON Schema parser (L1): readOnly and writeOnly variants.

Under ``--read-only-write-only-model-type`` a model whose effective fields are flagged readOnly or writeOnly splits
into Request and Response variants. These functions read the read/write facts of a schema key (from its raw schema,
its allOf parents and the models it references, walking what the dispatch tables say each site types), collect the
effective fields of a model with those it inherits, decide whether it splits, and create the variant models with their
references and extra-items metadata retargeted to the variants.

Each function takes the parser through ``RwSplitHost``, which ``JsonSchemaParser`` satisfies structurally. The methods
that tests call or that other parts of the parser call stay on the parser as delegators to these functions
(``parser/jsonschema.py``), and these functions call them through the parser; among them the seam
``_get_rw_model_variant_reference``. ``_update_variant_additional_properties_metadata``, a frame a warning names,
``_copy_schema_runtime_validation_for_variant``, which writes the variant's template data as the parser's own state,
``_typed_model_children``, the typed declaration of the definition table, and the extension hook
``_generate_forced_base_models`` stay parser methods.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from itertools import chain
from string import digits
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeAlias, cast

from typing_extensions import Protocol

from datamodel_code_generator import AllOfMergeMode, Error, ReadOnlyWriteOnlyModelType
from datamodel_code_generator._graph_links import drop_tree
from datamodel_code_generator.model import DataModel
from datamodel_code_generator.model.base import _field_source_name
from datamodel_code_generator.model.runtime_validation import _is_internal_schema_runtime_validation
from datamodel_code_generator.parser._intersection import inherits_keyword
from datamodel_code_generator.parser._passes._keys import _RAW_SCHEMA_DEFAULT_UNDEFINED
from datamodel_code_generator.parser._passes._shared import _detach_deferred_inherited_field_parents
from datamodel_code_generator.parser._schema_dispatch import SchemaSite, TypedChild
from datamodel_code_generator.parser._schema_object import JsonSchemaObject
from datamodel_code_generator.parser._schema_reading import _RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY, _merges_ref_siblings
from datamodel_code_generator.parser.base import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
)
from datamodel_code_generator.python_literal import _make_referencing_type_expression
from datamodel_code_generator.reference import (
    _is_special_path,
    _rw_direction,
    _rw_variant_flag,
    _rw_variant_of,
    _rw_variant_path,
    location_of,
)

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Iterable, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from datamodel_code_generator.enums import DataclassArguments
    from datamodel_code_generator.model import DataModelFieldBase
    from datamodel_code_generator.model.output import OutputModelContext
    from datamodel_code_generator.model.runtime_validation import PropertyCountRule
    from datamodel_code_generator.parser._field_rows import FieldIdentity
    from datamodel_code_generator.parser._schema_dispatch import DispatchTable
    from datamodel_code_generator.parser._schema_graph import _TargetNode
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.reference import ModelResolver, Reference
    from datamodel_code_generator.types import DataType

__all__ = [
    "RwSplitHost",
    "clear_inherited_field_caches",
    "collect_all_fields_for_request_response",
    "collect_inherited_fields_for_request_response",
    "copy_unregistered_fields",
    "create_object_model",
    "create_request_response_models",
    "create_variant_model",
    "data_types_reference_rw_model_variant",
    "fields_reference_rw_model_variant",
    "get_parsed_ref_schema_rw_model_field_facts",
    "get_raw_inherited_fields",
    "get_ref_schema_rw_model_field_facts",
    "get_ref_schema_rw_model_reference_facts",
    "get_rw_model_field_facts",
    "get_rw_model_variant_graph_node",
    "get_rw_model_variant_reference",
    "get_rw_property_name_sources",
    "get_separate_model_fields",
    "inherited_default_source",
    "join_rw_model_property_flags",
    "legacy_rw_variants",
    "merge_inherited_field_overrides",
    "merged_ref_model_schemas",
    "merged_ref_sibling_schemas",
    "overlay_inherited_field",
    "preload_property_refs_for_rw_models",
    "prepare_required_inherited_field",
    "prepare_rw_model_variant",
    "ref_schema_generates_variant",
    "ref_schema_has_rw_model_facts",
    "register_object_model",
    "request_response_ref_schema_generates_variant",
    "resolve_field_flag",
    "resolve_rw_model_reference",
    "rw_model_fields_split",
    "rw_model_writes_variant_beside_fields",
    "schema_builds_map_model",
    "schema_has_rw_model_fields",
    "should_generate_base_model",
    "splits_into_rw_model_variants",
    "store_extra_items_type",
    "temporary_constraint_values",
    "typed_field_site",
    "unregister_temporary_field_references",
    "unregister_temporary_type",
    "update_data_type_ref_for_variant",
    "update_field_refs_for_variant",
    "writes_extra_items_type",
]

_Site: TypeAlias = "SchemaSite[JsonSchemaObject]"


def _has_map_keywords(obj: JsonSchemaObject) -> bool:
    """Return whether a schema has keywords that type the keys or values of an object map."""
    return bool(
        obj.patternProperties or obj.propertyNames is not None or isinstance(obj.additionalProperties, JsonSchemaObject)
    )


def _rw_flag_facts(flags: Iterable[tuple[bool, bool]]) -> tuple[bool, bool, bool, bool]:
    """Return the read/write facts of ``(readOnly, writeOnly)`` pairs.

    Whether any pair is read-only, any write-only, any not read-only and any not write-only.
    """
    pairs = set(flags)
    return (
        any(read_only for read_only, _ in pairs),
        any(write_only for _, write_only in pairs),
        any(not read_only for read_only, _ in pairs),
        any(not write_only for _, write_only in pairs),
    )


def _rw_facts_need_variant(facts: tuple[bool, bool, bool, bool], suffix: Literal["Request", "Response"]) -> bool:
    """Return whether fields of these read/write facts need the Request or Response variant.

    A variant drops the read-only (Request) or write-only (Response) fields, so it needs such a field and another one.
    """
    has_read_only, has_write_only, has_non_read_only, has_non_write_only = facts
    direction = _rw_direction(suffix)
    return _rw_variant_flag(direction, has_read_only, has_write_only) and _rw_variant_flag(
        direction, has_non_read_only, has_non_write_only
    )


def _rw_variant_fields(
    fields: Iterable[DataModelFieldBase], suffix: Literal["Request", "Response"]
) -> list[DataModelFieldBase]:
    """Return the fields the Request or Response variant keeps: all but the read-only or the write-only ones."""
    direction = _rw_direction(suffix)
    return [field for field in fields if not _rw_variant_flag(direction, field.read_only, field.write_only)]


def _get_rw_model_variant_source_path(
    base_reference: Reference,
    suffix: Literal["Request", "Response"],
) -> str:
    """Return the legacy logical source path for a generated variant.

    A model built from a member of a document root joins its path to the document with ``/`` (``doc.json/p#...``); its
    source path names the location in that document (``doc.json#/p#...``), as those of the members of other schemas do.
    """
    path = base_reference.path
    if (location := location_of(base_reference)) is not None and path.startswith(prefix := f"{location.document}/"):
        path = f"{location.document}#/{path.removeprefix(prefix)}"
    source_prefix, separator, source_name = path.rpartition("/")
    return f"{source_prefix}{separator}{source_name}{suffix}"


class RwSplitHost(Protocol):
    """The options, caches and hooks of the parser that the request and response model split reads."""

    _DEFINITION_TABLE: ClassVar[DispatchTable[Any, JsonSchemaObject, None]]
    _FIELD_TABLE: ClassVar[DispatchTable[Any, JsonSchemaObject, DataType]]
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
    _raw_inherited_fields_cache: dict[str, tuple[DataModelFieldBase, ...]]
    _raw_inherited_own_names_cache: dict[str, frozenset[str]]
    _ref_target_dumps: dict[tuple[_TargetNode, str], tuple[dict[str, Any], str]]
    _request_response_fields: dict[str, tuple[DataModelFieldBase, ...]]
    _rw_model_field_facts_cache: dict[str, tuple[bool, bool, bool, bool]]
    _rw_model_field_facts_in_progress: set[str]
    _rw_model_references_cache: dict[str, tuple[bool, tuple[str, ...]]]
    _rw_model_variant_references: dict[tuple[str, str], Reference]
    _rw_model_variant_requirement_cache: dict[tuple[str, str], bool]
    _unsatisfiable_schemas: set[tuple[str, tuple[str, ...]]]
    _untaken_pattern_schemas: set[tuple[str, tuple[str, ...]]]

    @property
    def allof_merge_mode(self) -> AllOfMergeMode:
        """How the constraints of allOf members merge (``--allof-merge-mode``)."""

    @property
    def apply_default_values_for_required_fields(self) -> bool:
        """Whether required fields keep their schema defaults (``--apply-default-values-for-required-fields``)."""

    @property
    def current_source_path(self) -> Path | None:
        """The path of the source being parsed."""

    @property
    def custom_template_dir(self) -> Path | None:
        """The directory of custom templates, if configured."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def dataclass_arguments(self) -> DataclassArguments | None:
        """The arguments of the dataclass decorator, if configured."""

    @property
    def dedupe_request_response_variants(self) -> bool:
        """Whether request/response variants are deduplicated (``--dedupe-request-response-variants``)."""

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
    def keyword_only(self) -> bool:
        """Whether the models take keyword-only arguments."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""

    @property
    def read_only_write_only_model_type(self) -> ReadOnlyWriteOnlyModelType | None:
        """The read and write variants the output generates, if any."""

    @property
    def treat_dot_as_module(self) -> bool | None:
        """Whether dots in schema names make modules."""

    @property
    def use_closed_typed_dict(self) -> bool:
        """Whether TypedDict models are closed (``--use-closed-typed-dict``)."""

    @property
    def use_schema_description(self) -> bool:
        """Whether a schema's description becomes its model's docstring."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The capability through which the passes change the graph of the generation store."""

    @property
    def _legacy_rw_variants(self) -> frozenset[str]:
        """The rows of the request/response split kept: every row without --dedupe-request-response-variants."""

    @property
    def _output_model_context(self) -> OutputModelContext:
        """The capabilities of the output model types."""

    @property
    def _property_count_rule_cache(self) -> dict[int, tuple[JsonSchemaObject, PropertyCountRule | None]]:
        """The property-count rule of each schema object, cached for this parse."""

    def _apply_inherited_field_default(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        class_name: str,
    ) -> None:
        """Resolve an inherited schema default in the derived class scope."""

    def _collect_inherited_fields_for_request_response(
        self,
        base_classes: list[Reference],
        active: frozenset[str] = frozenset(),
    ) -> list[DataModelFieldBase]:
        """Collect canonical inherited fields in declaration order with C3 winners."""

    @staticmethod
    def _copy_inherited_field(  # noqa: PLR0913
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        force_optional: bool = False,
        partial_merge_mode: AllOfMergeMode = AllOfMergeMode.All,
        register_references: bool = True,
        reserved_names: set[str] | None = None,
    ) -> DataModelFieldBase | None:
        """Resolve a deferred inherited field without copying its reference graph."""

    @staticmethod
    def _copy_model_field(
        field: DataModelFieldBase,
        *,
        data_type: DataType | None = None,
        register_references: bool = True,
        identity: FieldIdentity | None = None,
    ) -> DataModelFieldBase:
        """Copy a field and its mutable state without copying model references, under the names ``identity`` gives."""

    def _copy_schema_runtime_validation_for_variant(  # noqa: PLR0913
        self,
        source_path: str,
        target_path: str,
        fields: Sequence[DataModelFieldBase],
        suffix: Literal["Request", "Response"],
        *,
        obj: JsonSchemaObject,
        source_fields: Sequence[DataModelFieldBase] = (),
        is_root_model: bool = False,
    ) -> None:
        """Copy schema runtime rules and retarget their model references."""

    def _create_data_model(self, model_type: type[DataModel] | None = None, **kwargs: Any) -> DataModel:
        """Create data model instance with dataclass_arguments support for DataClass."""

    def _create_variant_model(  # noqa: PLR0913
        self,
        base_reference: Reference,
        suffix: Literal["Request", "Response"],
        model_fields: list[DataModelFieldBase],
        obj: JsonSchemaObject,
        data_model_type_class: type[DataModel],
        *,
        source_fields: Sequence[DataModelFieldBase] = (),
    ) -> None:
        """Create a Request or Response model variant."""

    def _get_allof_parent_references(
        self,
        schema: JsonSchemaObject,
        *,
        inherited: Iterable[Reference] = (),
        defining_ref: str | None = None,
    ) -> list[Reference]:
        """Collect effective allOf parents once, preserving declaration precedence."""

    def _get_inherited_property_order(self, base_classes: list[Reference]) -> tuple[str, ...]:
        """Return raw inherited property names in ancestor-first declaration order."""

    def _get_inherited_required_names(self, base_classes: list[Reference]) -> frozenset[str]:
        """Collect required names across the effective inherited allOf graph."""

    def _get_inline_property_names(self, schema: JsonSchemaObject) -> frozenset[str]:
        """Collect properties declared directly by one schema and its inline allOf items."""

    def _get_inline_required_names(self, schema: JsonSchemaObject) -> tuple[str, ...]:
        """Collect required names in declaration order from one schema and inline allOf items."""

    def _get_raw_inherited_fields(
        self,
        reference: Reference,
        active: frozenset[str],
    ) -> list[DataModelFieldBase]:
        """Recursively materialize one raw schema's effective fields without live reverse edges."""

    def _get_ref_schema_rw_model_field_facts(
        self,
        resolved_ref: str,
    ) -> tuple[bool, bool, bool, bool] | None:
        """Return the read/write facts of the effective fields of the raw schema at ``resolved_ref``."""

    def _get_ref_schema_rw_model_reference_facts(
        self,
        resolved_ref: str,
    ) -> tuple[bool, tuple[str, ...]]:
        """Return whether the raw schema at a key declares a flagged property, and else the keys its references name."""

    def _get_rw_model_variant_graph_node(self, resolved_ref: str) -> tuple[bool, tuple[str, ...]]:
        """Return whether the raw schema at a key declares a flagged property, and else the keys it references."""

    def _get_rw_model_variant_reference(
        self,
        base_reference: Reference,
        suffix: Literal["Request", "Response"],
        *,
        loaded: bool = False,
    ) -> Reference:
        """Create a variant reference, reserving request/response paths for recursive uses."""

    def _inherited_ref_context(self, resolved_ref: str) -> AbstractContextManager[None]:
        """Resolve nested references relative to the inherited schema's own file and schema resource."""

    def _is_all_of_mapping_item(self, schema: JsonSchemaObject) -> bool:
        """Return whether an allOf item is an object map, which a mapping-shaped allOf merges into one root map."""

    def _is_base_stand_in(self, reference: Reference) -> bool:
        """Return whether the model of ``reference`` is a stand-in: never written, never split into variants."""

    def _is_ref_circular(self, resolved_ref: str) -> bool:
        """Check if a resolved $ref target contains a circular reference (cached)."""

    def _key_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node at a reference key, or the key when the graph does not locate its value."""

    def _lenient_ref(self, ref: str) -> str:
        """Return a reference of the current document mapped as its lenient resolution maps it, if it has one."""

    def _linearize_inherited_schema_refs(self, base_classes: list[Reference]) -> tuple[str, ...]:
        """Return raw inherited schemas in the same C3 order as generated models."""

    def _load_inherited_schema_object(self, resolved_ref: str) -> JsonSchemaObject:
        """Load and cache a schema used only for inherited field inspection."""

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Return the schema object a reference names."""

    def _merge_all_of_object(self, obj: JsonSchemaObject) -> JsonSchemaObject | None:
        """Merge allOf items when they share object properties to avoid duplicate models."""

    def _merge_inherited_field_overrides(
        self,
        inherited_fields: list[DataModelFieldBase],
        fields: list[DataModelFieldBase],
    ) -> list[DataModelFieldBase]:
        """Overlay declarations on inherited fields while resolving deferred partial types."""

    def _merge_ref_with_schema(self, obj: JsonSchemaObject, where: str | None = None) -> JsonSchemaObject:
        """Return the schema a ``$ref`` names merged with the sibling keywords of the reference."""

    def _object_nullable(self, obj: JsonSchemaObject) -> bool:
        """Return whether an object model's schema admits null, by its types or by nullable under strict nullability."""

    def _parse_inherited_schema_fields(
        self,
        reference: Reference,
        schema: JsonSchemaObject,
        parent_refs: list[Reference],
        parent_fields: list[DataModelFieldBase],
    ) -> list[DataModelFieldBase]:
        """Parse one forward schema's own fields in its defining scope, at its own path."""

    def _prepare_required_inherited_field(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        overriding_field: DataModelFieldBase | None = None,
    ) -> None:
        """Remove inherited constructor defaults while retaining explicit child metadata."""

    def _record_schema_object(self, reference: Reference, obj: JsonSchemaObject) -> None:
        """Record the schema object the model of ``reference`` is built from, and take its template data and marks."""

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names."""

    def _ref_schema_exists(self, resolved_ref: str) -> bool:
        """Return whether a resolved JSON pointer identifies a source schema."""

    def _ref_schema_generates_variant(
        self,
        resolved_ref: str,
        suffix: Literal["Request", "Response"],
    ) -> bool:
        """Check whether a referenced schema generates the requested read/write variant."""

    def _ref_target_dump(self, ref: str, resolved_ref: str) -> tuple[dict[str, Any], str]:
        """Return a fresh dump of a referenced schema for the current document, and the reference it ends at."""

    def _request_response_ref_schema_generates_variant(
        self,
        resolved_ref: str,
    ) -> bool:
        """Resolve Request/Response variant reachability once per graph node."""

    def _resolve_base_class(
        self, class_name: str, custom_base_path: str | list[str] | None = None
    ) -> str | list[str] | None:
        """Resolve base class(es) with priority: base_class_map > customBasePath > base_class."""

    def _resolve_external_ref_mapping(self, ref: str) -> tuple[str, str] | None:
        """Return the package and fragment ``--external-ref-mapping`` maps a reference to, if any."""

    def _resolve_rw_model_reference(self, ref: str, defining_ref: str) -> str | None:
        """Resolve a type-producing ref while keeping configured imports as graph leaves."""

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw schema into a schema object."""

    def _split_constraint_values(
        self, obj: JsonSchemaObject, data_type: DataType | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the constraint values of the type-dependent keywords that a field takes, and those it leaves out."""

    def _unregister_temporary_field_references(
        self,
        fields: Iterable[DataModelFieldBase],
    ) -> None:
        """Keep temporary raw fields out of the live reverse-reference graph."""

    def _update_data_type_ref_for_variant(
        self,
        data_type: DataType,
        suffix: Literal["Request", "Response"],
    ) -> None:
        """Recursively update data type references to point to variant models."""

    def _update_variant_additional_properties_metadata(
        self,
        reference_path: str,
        obj: JsonSchemaObject,
        suffix: Literal["Request", "Response"],
    ) -> None:
        """Rewrite TypedDict extra-item metadata to the matching model variant."""


def resolve_field_flag(parser: RwSplitHost, obj: JsonSchemaObject, flag: Literal["readOnly", "writeOnly"]) -> bool:
    """Resolve a field flag (readOnly/writeOnly) from direct value, $ref, and compositions.

    Visits the $ref target, then the allOf, anyOf and oneOf members, depth first on an explicit stack; each schema
    node a $ref names is read once, in the contexts of the $refs leading to it.
    """
    visited: set[_TargetNode] = set()
    pending: list[tuple[JsonSchemaObject, tuple[str, ...]]] = [(obj, ())]
    while pending:
        schema, contexts = pending.pop()
        if getattr(schema, flag) is True:
            return True
        pending.extend(
            (sub, contexts)
            for schemas in (schema.oneOf, schema.anyOf, schema.allOf)
            for sub in reversed(schemas)
            if isinstance(sub, JsonSchemaObject)
        )
        if not (parser.read_only_write_only_model_type and schema.ref):
            continue
        with ExitStack() as scope:
            for context in contexts:
                scope.enter_context(parser._inherited_ref_context(context))  # noqa: SLF001
            if (node := parser._key_node(resolved_ref := parser._ref_path(schema.ref))) in visited:  # noqa: SLF001
                continue
            visited.add(node)
            pending.append((parser._load_ref_schema_object(schema.ref), (*contexts, resolved_ref)))  # noqa: SLF001
    return False


def collect_inherited_fields_for_request_response(
    parser: RwSplitHost,
    base_classes: list[Reference],
    active: frozenset[str] = frozenset(),
) -> list[DataModelFieldBase]:
    """Collect canonical inherited fields in declaration order with C3 winners."""
    if not base_classes:
        return []

    winners: dict[str, DataModelFieldBase] = {}
    unnamed_fields: list[DataModelFieldBase] = []
    for resolved_ref in parser._linearize_inherited_schema_refs(base_classes):  # noqa: SLF001
        reference = parser.model_resolver.add_ref(resolved_ref, resolved=True)
        own_names = parser._raw_inherited_own_names_cache.get(resolved_ref)  # noqa: SLF001
        raw_fields = parser._get_raw_inherited_fields(reference, active)  # noqa: SLF001
        if own_names is None:
            own_names = parser._raw_inherited_own_names_cache.get(resolved_ref, frozenset())  # noqa: SLF001
        for field in raw_fields:
            if (field_name := _field_source_name(field)) is None:
                unnamed_fields.append(field)
            elif field_name in own_names:
                winners.setdefault(field_name, field)

    result: list[DataModelFieldBase] = []
    emitted_names: set[str] = set()
    for field_name in parser._get_inherited_property_order(base_classes):  # noqa: SLF001
        if (field := winners.get(field_name)) is None:
            continue
        result.append(field)
        emitted_names.add(field_name)
    result.extend(field for name, field in winners.items() if name not in emitted_names)
    result.extend(unnamed_fields)
    if not parser.force_optional_for_required_fields:
        required_names = parser._get_inherited_required_names(base_classes)  # noqa: SLF001
        for field in result:
            if _field_source_name(field) in required_names:
                field.required = True
                field.use_default_with_required = parser.apply_default_values_for_required_fields and field.has_default
    return result


def unregister_temporary_field_references(
    parser: RwSplitHost,
    fields: Iterable[DataModelFieldBase],
) -> None:
    """Keep temporary raw fields out of the live reverse-reference graph."""
    for field in fields:
        unregister_temporary_type(parser, field.data_type)


def unregister_temporary_type(parser: RwSplitHost, data_type: DataType | None) -> None:  # noqa: ARG001
    """Keep a type built only to read facts from out of the live reverse-reference graph."""
    drop_tree(data_type)


def temporary_constraint_values(
    parser: RwSplitHost, obj: JsonSchemaObject, data_type: DataType | None
) -> dict[str, Any]:
    """Return the constraint values ``obj`` writes beside ``data_type``, a type built only to read them."""
    constraints = parser._split_constraint_values(obj, data_type)[0]  # noqa: SLF001
    unregister_temporary_type(parser, data_type)
    return constraints


def copy_unregistered_fields(
    parser: RwSplitHost,
    fields: Iterable[DataModelFieldBase],
) -> list[DataModelFieldBase]:
    """Copy fields for temporary inheritance work without registering reverse edges."""
    return [parser._copy_model_field(field, register_references=False) for field in fields]  # noqa: SLF001


def clear_inherited_field_caches(parser: RwSplitHost) -> None:
    """Release temporary inheritance state after parsing."""
    for cached_fields in chain(
        parser._raw_inherited_fields_cache.values(),  # noqa: SLF001
        parser._request_response_fields.values(),  # noqa: SLF001
    ):
        parser._unregister_temporary_field_references(cached_fields)  # noqa: SLF001
        for field in cached_fields:
            _detach_deferred_inherited_field_parents(field)
    parser._raw_inherited_fields_cache.clear()  # noqa: SLF001
    parser._raw_inherited_own_names_cache.clear()  # noqa: SLF001
    parser._request_response_fields.clear()  # noqa: SLF001
    parser._rw_model_field_facts_cache.clear()  # noqa: SLF001
    parser._rw_model_references_cache.clear()  # noqa: SLF001
    parser._rw_model_variant_requirement_cache.clear()  # noqa: SLF001
    parser._rw_model_variant_references.clear()  # noqa: SLF001
    parser._inherited_schema_cache.clear()  # noqa: SLF001
    parser._inherited_schema_ancestor_cache.clear()  # noqa: SLF001
    parser._inherited_schema_linearization_cache.clear()  # noqa: SLF001
    parser._inherited_required_cache.clear()  # noqa: SLF001
    parser._inherited_parent_property_cache.clear()  # noqa: SLF001
    parser._ref_target_dumps.clear()  # noqa: SLF001
    parser._unsatisfiable_schemas.clear()  # noqa: SLF001
    parser._untaken_pattern_schemas.clear()  # noqa: SLF001
    if parser.generate_schema_validators:
        parser._property_count_rule_cache.clear()  # noqa: SLF001


def merge_inherited_field_overrides(
    parser: RwSplitHost,
    inherited_fields: list[DataModelFieldBase],
    fields: list[DataModelFieldBase],
) -> list[DataModelFieldBase]:
    """Overlay declarations on inherited fields while resolving deferred partial types."""
    deduplicated: dict[str, DataModelFieldBase] = {}
    overridden_fields: dict[str, DataModelFieldBase] = {}
    for field in chain(inherited_fields, fields):
        if (key := _field_source_name(field)) is None:
            continue
        if inherited_field := deduplicated.get(key):
            overridden_fields[key] = inherited_field
        deduplicated[key] = field

    reserved_names = {field.name for field in deduplicated.values() if field.name}
    result: list[DataModelFieldBase] = []
    for key, field in deduplicated.items():
        inherited_field = overridden_fields.get(key)
        if (
            inherited_field is None
            or (
                resolved_field := overlay_inherited_field(
                    parser, field, inherited_field, reserved_names, register_references=False
                )
            )
            is None
        ):
            result.append(field)
            continue
        if class_name := field.__dict__.get(_DEFERRED_INHERITED_FIELD_KEY):
            parser._apply_inherited_field_default(resolved_field, inherited_field, class_name=class_name)  # noqa: SLF001
        elif class_name := field.__dict__.get(_DEFERRED_INHERITED_CLASS_KEY):
            source = inherited_default_source(parser, field, inherited_field)
            parser._apply_inherited_field_default(resolved_field, source, class_name=class_name)  # noqa: SLF001
        result.append(resolved_field)
    return result


def overlay_inherited_field(
    parser: RwSplitHost,
    field: DataModelFieldBase,
    inherited_field: DataModelFieldBase,
    reserved_names: set[str],
    *,
    register_references: bool = True,
) -> DataModelFieldBase | None:
    """Return the copy of ``inherited_field`` that the deferred override ``field`` resolves to, else None.

    The copy takes the overlay of both fields (``parser/_field_copies.py:_copy_resolved_inherited_field``) and
    the required state of the override, without the inherited constructor default.
    """
    if (
        resolved_field := parser._copy_inherited_field(  # noqa: SLF001
            field,
            inherited_field,
            force_optional=parser.force_optional_for_required_fields,
            partial_merge_mode=parser.allof_merge_mode,
            register_references=register_references,
            reserved_names=reserved_names,
        )
    ) is not None:
        parser._prepare_required_inherited_field(resolved_field, inherited_field, overriding_field=field)  # noqa: SLF001
    return resolved_field


def inherited_default_source(
    parser: RwSplitHost, field: DataModelFieldBase, inherited_field: DataModelFieldBase
) -> DataModelFieldBase:
    """Return the field whose default the override ``field`` of ``inherited_field`` takes.

    The inherited one where the merge mode inherits ``default`` (``parser/_intersection.py:inherits_keyword``) and
    ``field`` declares none.
    """
    if inherits_keyword(parser.allof_merge_mode.value, "default") and not (
        _RAW_SCHEMA_DEFAULT_KEY in field.__dict__
        and field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] is not _RAW_SCHEMA_DEFAULT_UNDEFINED
    ):
        return inherited_field
    return field


def prepare_required_inherited_field(
    parser: RwSplitHost,
    field: DataModelFieldBase,
    inherited_field: DataModelFieldBase,
    *,
    overriding_field: DataModelFieldBase | None = None,
) -> None:
    """Remove inherited constructor defaults while retaining explicit child metadata."""
    explicit_extras = (
        overriding_field.__dict__.pop(_RAW_SCHEMA_EXPLICIT_FIELD_EXTRAS_KEY, overriding_field.extras)
        if overriding_field is not None
        else ()
    )
    parser.data_model_type.prepare_required_inherited_field(
        field,
        inherited_field,
        explicit_extras=explicit_extras,
    )


def get_raw_inherited_fields(
    parser: RwSplitHost,
    reference: Reference,
    active: frozenset[str],
) -> list[DataModelFieldBase]:
    """Recursively materialize one raw schema's effective fields without live reverse edges."""
    resolved_ref = reference.path
    if (cached_fields := parser._raw_inherited_fields_cache.get(resolved_ref)) is not None:  # noqa: SLF001
        return copy_unregistered_fields(parser, cached_fields)
    if (cached_fields := parser._request_response_fields.get(resolved_ref)) is not None:  # noqa: SLF001
        if resolved_ref not in parser._raw_inherited_own_names_cache and not _is_special_path(resolved_ref):  # noqa: SLF001
            schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
            parser._raw_inherited_own_names_cache[resolved_ref] = parser._get_inline_property_names(schema)  # noqa: SLF001
        return copy_unregistered_fields(parser, cached_fields)
    if resolved_ref in active or _is_special_path(resolved_ref):
        return []

    schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
    parent_refs = parser._get_allof_parent_references(  # noqa: SLF001
        schema,
        defining_ref=resolved_ref,
    )
    parent_fields = parser._collect_inherited_fields_for_request_response(  # noqa: SLF001
        parent_refs,
        active | {resolved_ref},
    )
    if isinstance(reference.source, DataModel):
        own_fields = copy_unregistered_fields(parser, reference.source.fields)
    else:
        own_fields = parser._parse_inherited_schema_fields(  # noqa: SLF001
            reference,
            schema,
            parent_refs,
            parent_fields,
        )
    parser._raw_inherited_own_names_cache[resolved_ref] = frozenset(  # noqa: SLF001
        field_name for field in own_fields if (field_name := _field_source_name(field)) is not None
    )
    result = parser._merge_inherited_field_overrides(parent_fields, own_fields)  # noqa: SLF001

    required_names = {
        field_name
        for field in parent_fields
        if field.required and (field_name := _field_source_name(field)) is not None
    }
    required_names.update(parser._get_inline_required_names(schema))  # noqa: SLF001
    if not parser.force_optional_for_required_fields:
        for field in result:
            if _field_source_name(field) in required_names:
                field.required = True
                field.use_default_with_required = parser.apply_default_values_for_required_fields and field.has_default
            parser.data_model_type.finalize_required_inherited_field(field)

    parser._raw_inherited_fields_cache[resolved_ref] = tuple(result)  # noqa: SLF001
    return copy_unregistered_fields(parser, result)


def legacy_rw_variants(parser: RwSplitHost) -> frozenset[str]:
    """Return the rows of the request/response split kept: every row without --dedupe-request-response-variants.

    ``legacy-rw-parsed-facts``: the read/write facts and graph node of a key come from a temporary parse of its
    fields, the walk reads every reference of a key, and every model collects its inherited fields to decide its
    split. ``legacy-rw-unowned-parent-fields``: the inline models of an allOf parent's own fields are parsed at no
    owner path.
    """
    if parser.dedupe_request_response_variants:
        return frozenset()
    return frozenset({"legacy-rw-parsed-facts", "legacy-rw-unowned-parent-fields"})


def collect_all_fields_for_request_response(
    parser: RwSplitHost,
    fields: list[DataModelFieldBase],
    base_classes: list[Reference] | None,
) -> list[DataModelFieldBase]:
    """Collect parent → child fields, with each child declaration overriding its parent.

    The merge leaves out the root field of a root-model base, which types the base's value and is no property.
    """
    inherited_fields = parser._collect_inherited_fields_for_request_response(base_classes or [])  # noqa: SLF001
    if not inherited_fields:
        return fields
    return parser._merge_inherited_field_overrides(inherited_fields, fields)  # noqa: SLF001


def get_separate_model_fields(
    parser: RwSplitHost,
    reference: Reference,
    fields: list[DataModelFieldBase],
    base_classes: list[Reference] | None,
) -> list[DataModelFieldBase] | None:
    """Collect Request/Response fields once when separate models are needed.

    The model's own fields, the types it writes beside them and the schema facts of its allOf bases decide;
    inherited fields are collected only for a model that may split. A stand-in never splits.
    """
    if parser.read_only_write_only_model_type is None or parser._is_base_stand_in(reference):  # noqa: SLF001
        return None
    if "legacy-rw-parsed-facts" in parser._legacy_rw_variants:  # noqa: SLF001
        all_fields = collect_all_fields_for_request_response(parser, fields, base_classes)
        return all_fields if splits_into_rw_model_variants(parser, reference, all_fields, base_classes) else None
    all_fields: list[DataModelFieldBase] | None = None
    if (
        rw_model_fields_split(parser, fields)
        or any(ref_schema_has_rw_model_facts(parser, base.path) for base in base_classes or () if base.path)
        or rw_model_writes_variant_beside_fields(parser, reference.path)
    ):
        all_fields = collect_all_fields_for_request_response(parser, fields, base_classes)
        if not splits_into_rw_model_variants(parser, reference, all_fields, base_classes):
            all_fields = None
    return all_fields


def rw_model_fields_split(parser: RwSplitHost, fields: list[DataModelFieldBase]) -> bool:
    """Return whether fields split their model: one is flagged or, under request-response, references variants."""
    return any(field.read_only or field.write_only for field in fields) or (
        parser.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse
        and fields_reference_rw_model_variant(parser, fields, "Request")
    )


def splits_into_rw_model_variants(
    parser: RwSplitHost,
    reference: Reference,
    all_fields: list[DataModelFieldBase],
    base_classes: list[Reference] | None,
) -> bool:
    """Return whether the model of ``reference`` with these effective fields splits into read/write variants.

    Under request-response a model also splits where the types it writes beside its fields name a model with
    variants, where the effective fields of an allOf ancestor split it, or where the references to one of its allOf
    bases read variants, as those of a root model do: the variants replace the base's model, so nothing can inherit
    from it, even where the model's own declarations take the flags of the inherited fields away.
    """
    if rw_model_fields_split(parser, all_fields):
        return True
    if parser.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse:
        return False
    return (
        any(
            rw_model_fields_split(
                parser,
                parser._get_raw_inherited_fields(  # noqa: SLF001
                    parser.model_resolver.add_ref(resolved_ref, resolved=True), frozenset()
                ),
            )
            for resolved_ref in parser._linearize_inherited_schema_refs(base_classes or [])  # noqa: SLF001
        )
        or rw_model_writes_variant_beside_fields(parser, reference.path)
        or any(parser._ref_schema_generates_variant(base.path, "Request") for base in base_classes or ())  # noqa: SLF001
    )


def rw_model_writes_variant_beside_fields(parser: RwSplitHost, reference_path: str) -> bool:
    """Return whether a type the model at a key writes beside its fields names a model with variants.

    Under request-response, the types its schema validator rules name and the type of additional properties that
    TypedDict records as extra_items (``set_additional_properties``) name models as the fields do.
    """
    if parser.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse:
        return False
    template_data = parser.extra_template_data.get(reference_path, {})
    data_types: list[DataType] = []
    if _is_internal_schema_runtime_validation(rules := template_data.get("schema_runtime_validation")):
        data_types.extend(rules.data_types)
    if (source := parser._output_model_context.additional_properties_type_source(template_data)) is not None:  # noqa: SLF001
        data_types.append(cast("DataType", source))
    return data_types_reference_rw_model_variant(parser, data_types, "Request")


def should_generate_base_model(parser: RwSplitHost, *, generates_separate_models: bool = False) -> bool:
    """Determine if Base model should be generated."""
    return (
        parser.read_only_write_only_model_type in {None, ReadOnlyWriteOnlyModelType.All}
        or not generates_separate_models
    )


def get_rw_model_field_facts(
    parser: RwSplitHost,  # noqa: ARG001
    fields: Iterable[DataModelFieldBase],
) -> tuple[bool, bool, bool, bool]:
    """Return the read/write facts of ``fields`` (``_rw_flag_facts``)."""
    return _rw_flag_facts((field.read_only, field.write_only) for field in fields)


def get_ref_schema_rw_model_field_facts(
    parser: RwSplitHost,
    resolved_ref: str,
) -> tuple[bool, bool, bool, bool] | None:
    """Return the read/write facts of the effective fields of the raw schema at ``resolved_ref``.

    Properties are read in allOf C3 order with their flags joined per name, and required names without a
    declaration count as unflagged fields; nothing is parsed. Under ``legacy-rw-parsed-facts`` the facts of a
    temporary parse, ``None`` where it gives none (``_get_parsed_ref_schema_rw_model_field_facts``).
    """
    if (cached := parser._rw_model_field_facts_cache.get(resolved_ref)) is not None:  # noqa: SLF001
        return cached
    if "legacy-rw-parsed-facts" in parser._legacy_rw_variants:  # noqa: SLF001
        return get_parsed_ref_schema_rw_model_field_facts(parser, resolved_ref)
    flags: dict[str, tuple[bool, bool]] = {}
    reference = parser.model_resolver.add_ref(resolved_ref, resolved=True)
    for ref in reversed(parser._linearize_inherited_schema_refs([reference])):  # noqa: SLF001
        join_rw_model_property_flags(parser, ref, flags)
    if not parser.force_optional_for_required_fields:
        for name in parser._get_inherited_required_names([reference]):  # noqa: SLF001
            flags.setdefault(name, (False, False))
    result = _rw_flag_facts(flags.values())
    parser._rw_model_field_facts_cache[resolved_ref] = result  # noqa: SLF001
    return result


def get_parsed_ref_schema_rw_model_field_facts(
    parser: RwSplitHost,
    resolved_ref: str,
) -> tuple[bool, bool, bool, bool] | None:
    """Return the read/write facts of the fields a temporary parse of the schema at ``resolved_ref`` gives.

    The facts of 0.86.0, kept under ``legacy-rw-parsed-facts``: the parse registers the inline models it
    meets. A key whose parse is already in progress, reached again through the models that parse builds, has no
    facts, so the parse never re-enters itself; ``None`` also for a key that names no loadable schema.
    """
    if resolved_ref in parser._rw_model_field_facts_in_progress:  # noqa: SLF001
        return None
    parser._rw_model_field_facts_in_progress.add(resolved_ref)  # noqa: SLF001
    try:
        reference = parser.model_resolver.add_ref(resolved_ref, resolved=True)
        fields = parser._get_raw_inherited_fields(reference, frozenset())  # noqa: SLF001
    except (Error, OSError):
        return None
    finally:
        parser._rw_model_field_facts_in_progress.discard(resolved_ref)  # noqa: SLF001
    result = get_rw_model_field_facts(parser, fields)
    parser._rw_model_field_facts_cache[resolved_ref] = result  # noqa: SLF001
    return result


def join_rw_model_property_flags(parser: RwSplitHost, resolved_ref: str, flags: dict[str, tuple[bool, bool]]) -> None:
    """Join the readOnly/writeOnly flags of the properties the schema at a key declares, by name, into ``flags``.

    The properties of inline allOf members count as its own, as inherited fields collect them.
    """
    pending = [parser._load_inherited_schema_object(resolved_ref)]  # noqa: SLF001
    with parser._inherited_ref_context(resolved_ref):  # noqa: SLF001
        while pending:
            schema = pending.pop()
            for name, prop in (schema.properties or {}).items():
                read_only, write_only = flags.get(name, (False, False))
                if isinstance(prop, JsonSchemaObject):
                    read_only |= resolve_field_flag(parser, prop, "readOnly")
                    write_only |= resolve_field_flag(parser, prop, "writeOnly")
                flags[name] = read_only, write_only
            pending.extend(sub for sub in schema.allOf if isinstance(sub, JsonSchemaObject) and not sub.ref)


def ref_schema_has_rw_model_facts(parser: RwSplitHost, resolved_ref: str) -> bool:
    """Return whether inheriting the schema at ``resolved_ref`` can bring read/write facts into a model."""
    if parser.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
        return parser._ref_schema_generates_variant(resolved_ref, "Request")  # noqa: SLF001
    facts = parser._get_ref_schema_rw_model_field_facts(resolved_ref)  # noqa: SLF001
    return facts is not None and (facts[0] or facts[1])


def ref_schema_generates_variant(
    parser: RwSplitHost,
    resolved_ref: str,
    suffix: Literal["Request", "Response"],
) -> bool:
    """Check whether a referenced schema generates the requested read/write variant."""
    cache_key = resolved_ref, suffix
    if (cached := parser._rw_model_variant_requirement_cache.get(cache_key)) is not None:  # noqa: SLF001
        return cached
    if _rw_variant_of(resolved_ref):
        return False
    if parser.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
        return parser._request_response_ref_schema_generates_variant(resolved_ref)  # noqa: SLF001
    if (facts := parser._get_ref_schema_rw_model_field_facts(resolved_ref)) is None:  # noqa: SLF001
        return False
    result = _rw_facts_need_variant(facts, suffix)
    parser._rw_model_variant_requirement_cache[cache_key] = result  # noqa: SLF001
    return result


def request_response_ref_schema_generates_variant(
    parser: RwSplitHost,
    resolved_ref: str,
) -> bool:
    """Resolve Request/Response variant reachability once per graph node."""
    pending = [resolved_ref]
    parent_by_ref: dict[str, str | None] = {resolved_ref: None}
    matched_ref: str | None = None
    while pending:
        current_ref = pending.pop()
        match parser._rw_model_variant_requirement_cache.get((current_ref, "Request")):  # noqa: SLF001
            case True:
                matched_ref = current_ref
                break
            case False:
                continue
        if _rw_variant_of(current_ref):
            continue
        has_direct_variant, nested_refs = parser._get_rw_model_variant_graph_node(current_ref)  # noqa: SLF001
        if has_direct_variant:
            matched_ref = current_ref
            break
        for nested_ref in nested_refs:
            if nested_ref not in parent_by_ref:
                parent_by_ref[nested_ref] = current_ref
                pending.append(nested_ref)

    if matched_ref is None:
        for evaluated_ref in parent_by_ref:
            parser._rw_model_variant_requirement_cache[evaluated_ref, "Request"] = False  # noqa: SLF001
            parser._rw_model_variant_requirement_cache[evaluated_ref, "Response"] = False  # noqa: SLF001
        return False

    while matched_ref is not None:
        parser._rw_model_variant_requirement_cache[matched_ref, "Request"] = True  # noqa: SLF001
        parser._rw_model_variant_requirement_cache[matched_ref, "Response"] = True  # noqa: SLF001
        matched_ref = parent_by_ref[matched_ref]
    return True


def get_rw_model_variant_graph_node(parser: RwSplitHost, resolved_ref: str) -> tuple[bool, tuple[str, ...]]:
    """Return whether the raw schema at a key declares a flagged property, and else the keys it references.

    A special path has no raw schema of its own: its split is the one recorded when its model is parsed. Under
    ``legacy-rw-parsed-facts``, the 0.86.0 node: the facts of a temporary parse of the key also split it,
    and the models its parsed fields reference are edges too.
    """
    if "legacy-rw-parsed-facts" not in parser._legacy_rw_variants:  # noqa: SLF001
        if _is_special_path(resolved_ref):
            return False, ()
        return parser._get_ref_schema_rw_model_reference_facts(resolved_ref)  # noqa: SLF001
    facts = parser._get_ref_schema_rw_model_field_facts(resolved_ref)  # noqa: SLF001
    fields = parser._raw_inherited_fields_cache.get(resolved_ref)  # noqa: SLF001
    if fields is None:
        fields = parser._request_response_fields.get(resolved_ref, ())  # noqa: SLF001
    nested_refs = {
        reference.path: None
        for field in fields
        for data_type in field.data_type.all_data_types
        if (reference := data_type.reference) is not None
    }
    has_inline_variant, raw_refs = parser._get_ref_schema_rw_model_reference_facts(resolved_ref)  # noqa: SLF001
    nested_refs.update(dict.fromkeys(raw_refs))
    return (facts is not None and (facts[0] or facts[1])) or has_inline_variant, tuple(nested_refs)


def fields_reference_rw_model_variant(
    parser: RwSplitHost,
    fields: Iterable[DataModelFieldBase],
    suffix: Literal["Request", "Response"],
) -> bool:
    """Return whether any nested field reference needs the requested variant."""
    return data_types_reference_rw_model_variant(parser, (field.data_type for field in fields), suffix)


def data_types_reference_rw_model_variant(
    parser: RwSplitHost,
    data_types: Iterable[DataType],
    suffix: Literal["Request", "Response"],
) -> bool:
    """Return whether any reference these types name, nested ones included, needs the requested variant."""
    for data_type in data_types:
        for nested_data_type in data_type.all_data_types:
            if (reference := nested_data_type.reference) and parser._ref_schema_generates_variant(  # noqa: SLF001
                reference.path,
                suffix,
            ):
                return True
    return False


def writes_extra_items_type(parser: RwSplitHost) -> bool:
    """Return whether TypedDict writes the type of additional properties as extra_items, under --use-closed-typed-dict.

    That type is the lightweight type of additionalProperties (``_build_lightweight_type``), which names models
    and generates none.
    """
    return (
        parser.use_closed_typed_dict and parser._output_model_context.requires_additional_properties_reference_classes  # noqa: SLF001
    )


def get_ref_schema_rw_model_reference_facts(
    parser: RwSplitHost,
    resolved_ref: str,
) -> tuple[bool, tuple[str, ...]]:
    """Return whether the raw schema at a key declares a flagged property, and else the keys its references name.

    The walk asks the dispatch tables what the model of the key types (``_DEFINITION_TABLE``,
    ``_typed_model_children``) and follows what that types in turn. It stops at the first flagged property: the
    references of a node that needs variants are never read (under ``legacy-rw-parsed-facts`` it reads them all,
    as 0.86.0 did). A key that names no loadable schema has neither.
    """
    if (cached := parser._rw_model_references_cache.get(resolved_ref)) is not None:  # noqa: SLF001
        return cached
    try:
        root_schema = parser._load_inherited_schema_object(resolved_ref)  # noqa: SLF001
    except (Error, OSError):
        return False, ()

    has_inline_variant = False
    references: dict[str, None] = {}
    stack: list[_Site] = [SchemaSite("", root_schema, [resolved_ref])]
    while stack:
        for child in parser._DEFINITION_TABLE.typed_children(parser, stack.pop()):  # noqa: SLF001
            match child.position:
                case "fields":
                    if has_inline_variant or not schema_has_rw_model_fields(
                        parser, cast("JsonSchemaObject", child.schema), resolved_ref
                    ):
                        continue
                    if "legacy-rw-parsed-facts" not in parser._legacy_rw_variants:  # noqa: SLF001
                        parser._rw_model_references_cache[resolved_ref] = result = (True, ())  # noqa: SLF001
                        return result
                    has_inline_variant = True
                case "ref":
                    if nested_ref := parser._resolve_rw_model_reference(cast("str", child.reference), resolved_ref):  # noqa: SLF001
                        references.setdefault(nested_ref, None)
                case "key":
                    references.setdefault(cast("str", child.reference), None)
                case _:
                    stack.append(
                        SchemaSite("", cast("JsonSchemaObject", child.schema), [resolved_ref], parent=child.into)
                    )
    parser._rw_model_references_cache[resolved_ref] = result = (has_inline_variant, tuple(references))  # noqa: SLF001
    return result


def typed_field_site(parser: RwSplitHost, item: object, defining_ref: str) -> Iterable[TypedChild[JsonSchemaObject]]:
    """Return what a field site of a schema types, as the row ``parse_item`` chooses for it declares.

    Row ``legacy-rw-field-site-as-written``: the row is chosen from the site as written, its $ref merged with its
    sibling keywords only where the site itself writes a customTypePath.
    """
    if not isinstance(item, JsonSchemaObject):
        return ()
    site = SchemaSite("", item, [defining_ref])
    as_parsed = None
    if item.custom_type_path and _merges_ref_siblings(item):
        with parser._inherited_ref_context(defining_ref):  # noqa: SLF001
            as_parsed = replace(site, obj=parser._merge_ref_with_schema(item))  # noqa: SLF001
    return parser._FIELD_TABLE.typed_children(parser, site, as_parsed=as_parsed)  # noqa: SLF001


def merged_ref_model_schemas(
    parser: RwSplitHost, schema: JsonSchemaObject, defining_ref: str
) -> tuple[JsonSchemaObject, JsonSchemaObject]:
    """Return the schema the walk reads for ``schema`` and the schema whose keywords decide what its keywords type.

    A $ref with sibling keywords decides as ``_merged_ref_sibling_schemas`` says. An allOf whose members redeclare
    a property with different schemas builds its model from the one object they merge into
    (``_merge_all_of_object``), which keeps their additionalProperties, so the walk reads that object for both.
    """
    schema, model_schema = merged_ref_sibling_schemas(parser, schema, defining_ref)
    if schema is model_schema and schema.allOf:
        with parser._inherited_ref_context(defining_ref):  # noqa: SLF001
            if merged := parser._merge_all_of_object(schema):  # noqa: SLF001
                return merged, merged
    return schema, model_schema


def merged_ref_sibling_schemas(
    parser: RwSplitHost, schema: JsonSchemaObject, defining_ref: str
) -> tuple[JsonSchemaObject, JsonSchemaObject]:
    """Return the schema the walk reads for a $ref with sibling keywords and the schema that decides its keywords.

    The model of a $ref with sibling keywords is built from the $ref merged with them, so where map keywords sit
    beside a $ref outside allOf, that merged schema decides what they type. Where the target builds a map with map
    keywords and the merged schema builds none (``_schema_builds_map_model``), the model types none of the target's
    map keywords and never names the target's model, so the walk reads the merged schema in place of the reference
    to the target. Any other schema decides for itself.
    """
    if not _merges_ref_siblings(schema):
        return schema, schema
    with parser._inherited_ref_context(defining_ref):  # noqa: SLF001
        if parser._is_ref_circular(  # noqa: SLF001
            resolved_ref := parser._ref_path(ref := parser._lenient_ref(cast("str", schema.ref)))  # noqa: SLF001
        ):
            return schema, schema
        target = parser._schema_object(parser._ref_target_dump(ref, resolved_ref)[0])  # noqa: SLF001
        target_builds_map = _has_map_keywords(target) and schema_builds_map_model(parser, target, defining_ref)
        if not (target_builds_map or _has_map_keywords(schema)):
            return schema, schema
        merged = parser._merge_ref_with_schema(schema)  # noqa: SLF001
    if target_builds_map and not schema_builds_map_model(parser, merged, defining_ref):
        return merged, merged
    return schema, merged


def schema_builds_map_model(parser: RwSplitHost, schema: JsonSchemaObject, defining_ref: str) -> bool:
    """Return whether the model built from a schema is an object map, whose map keywords type its keys and values.

    A schema with properties builds a model with fields. An allOf builds a map only where the schema has no
    patternProperties or propertyNames and every item is an object map (``_is_all_of_mapping_item``), as
    ``_merge_all_of_root_schema`` merges it; otherwise the allOf builds a model with fields or bases.
    """
    if schema.properties:
        return False
    if not schema.allOf:
        return True
    if schema.patternProperties or schema.propertyNames is not None:
        return False
    with parser._inherited_ref_context(defining_ref):  # noqa: SLF001
        return all(
            isinstance(item, JsonSchemaObject)
            and parser._is_all_of_mapping_item(parser._load_ref_schema_object(item.ref) if item.ref else item)  # noqa: SLF001
            for item in schema.allOf
        )


def resolve_rw_model_reference(parser: RwSplitHost, ref: str, defining_ref: str) -> str | None:
    """Resolve a type-producing ref while keeping configured imports as graph leaves."""
    with parser._inherited_ref_context(defining_ref):  # noqa: SLF001
        if parser._resolve_external_ref_mapping(ref) is not None:  # noqa: SLF001
            return None
        return parser._ref_path(ref)  # noqa: SLF001


def get_rw_property_name_sources(
    parser: RwSplitHost,
    *,
    property_names: JsonSchemaObject | bool | None,
    defining_ref: str,
) -> tuple[tuple[str, ...], tuple[JsonSchemaObject, ...]]:
    """Mirror propertyNames key parsing without following ignored nested shapes."""
    if not isinstance(property_names, JsonSchemaObject):
        return (), ()
    if _merges_ref_siblings(property_names):
        with parser._inherited_ref_context(defining_ref):  # noqa: SLF001
            property_names = parser._merge_ref_with_schema(property_names)  # noqa: SLF001
    combined = tuple(
        item
        for item in chain(property_names.anyOf, property_names.oneOf, property_names.allOf)
        if isinstance(item, JsonSchemaObject)
    )
    if combined:
        return (), combined
    return ((property_names.ref,), ()) if property_names.ref else ((), ())


def schema_has_rw_model_fields(
    parser: RwSplitHost,
    schema: JsonSchemaObject,
    defining_ref: str,
) -> bool:
    """Resolve read/write flags on fields exactly as object parsing does."""
    with parser._inherited_ref_context(defining_ref):  # noqa: SLF001
        return any(
            isinstance(prop, JsonSchemaObject)
            and (resolve_field_flag(parser, prop, "readOnly") or resolve_field_flag(parser, prop, "writeOnly"))
            for prop in (schema.properties or {}).values()
        )


def preload_property_refs_for_rw_models(parser: RwSplitHost, obj: JsonSchemaObject) -> None:
    """Preload property refs needed for readOnly/writeOnly model splitting."""
    if parser.read_only_write_only_model_type is None or not obj.properties:
        return
    for prop in obj.properties.values():
        if isinstance(prop, JsonSchemaObject) and prop.ref and parser._resolve_external_ref_mapping(prop.ref) is None:  # noqa: SLF001
            parser._load_ref_schema_object(prop.ref)  # noqa: SLF001


def get_rw_model_variant_reference(
    parser: RwSplitHost,
    base_reference: Reference,
    suffix: Literal["Request", "Response"],
    *,
    loaded: bool = False,
) -> Reference:
    """Create a variant reference, reserving request/response paths for recursive uses."""
    cache_key: tuple[str, str] | None = None
    if parser.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
        cache_key = base_reference.path, suffix
        if (reference := parser._rw_model_variant_references.get(cache_key)) is not None:  # noqa: SLF001
            reference.loaded |= loaded
            return reference

    if parser._ref_schema_exists(source_variant_ref := _get_rw_model_variant_source_path(base_reference, suffix)):  # noqa: SLF001
        parser.model_resolver.add_ref(source_variant_ref, resolved=True)

    class_name_suffix = parser.model_resolver.class_name_suffix
    base_name_without_index = base_reference.name.rstrip(digits)
    numeric_index = base_reference.name.removeprefix(base_name_without_index)
    variant_name = (
        f"{base_name_without_index[: -len(class_name_suffix)]}{suffix}{class_name_suffix}{numeric_index}"
        if class_name_suffix and base_name_without_index.endswith(class_name_suffix)
        else f"{base_reference.name}{suffix}"
    )
    yielding = parser.model_resolver.is_yielding(base_reference)
    unique_name = parser.model_resolver.get_class_name(
        variant_name,
        unique=True,
        skip_affix=True,
        preserve_name=True,
        yields=yielding,
    ).name
    reference = parser.model_resolver.add(
        _rw_variant_path(base_reference.path, _rw_direction(suffix)),
        unique_name,
        class_name=False,
        unique=False,
        loaded=loaded,
        yields=yielding or None,
    )
    if cache_key is not None:
        parser._rw_model_variant_references[cache_key] = reference  # noqa: SLF001
    return reference


def update_data_type_ref_for_variant(
    parser: RwSplitHost,
    data_type: DataType,
    suffix: Literal["Request", "Response"],
) -> None:
    """Recursively update data type references to point to variant models."""
    for nested_data_type in data_type.all_data_types:
        if (reference := nested_data_type.reference) and parser._ref_schema_generates_variant(reference.path, suffix):  # noqa: SLF001
            base_reference = parser.model_resolver.add_ref(reference.path, resolved=True)
            variant_ref = parser._get_rw_model_variant_reference(base_reference, suffix)  # noqa: SLF001
            parser._graph_builder.replace_data_type_ref(nested_data_type, variant_ref)  # noqa: SLF001


def update_field_refs_for_variant(
    parser: RwSplitHost,
    model_fields: list[DataModelFieldBase],
    suffix: Literal["Request", "Response"],
) -> list[DataModelFieldBase]:
    """Update field references in model_fields to point to variant models.

    For Request models, refs should point to Request variants.
    For Response models, refs should point to Response variants.
    """
    if parser.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse:
        return model_fields
    for field in model_fields:
        parser._update_data_type_ref_for_variant(field.data_type, suffix)  # noqa: SLF001
    return model_fields


def store_extra_items_type(
    parser: RwSplitHost,
    metadata: dict[str, Any],
    data_type: DataType,
    *,
    reference_classes_required: bool,
    **kwargs: Any,
) -> None:
    """Store the type TypedDict writes as extra_items, with the model classes it names where they are required."""
    reference_classes = (
        {item.reference.path for item in data_type.all_data_types if item.reference}
        if reference_classes_required
        else None
    )
    parser._output_model_context.store_additional_properties_type(  # noqa: SLF001
        metadata,
        _make_referencing_type_expression(data_type) if reference_classes else data_type.type_hint,
        reference_classes,
        imports=tuple(data_type.all_imports),
        **kwargs,
    )


def create_variant_model(  # noqa: PLR0913, PLR0917
    parser: RwSplitHost,
    base_reference: Reference,
    suffix: Literal["Request", "Response"],
    model_fields: list[DataModelFieldBase],
    obj: JsonSchemaObject,
    data_model_type_class: type[DataModel],
    *,
    source_fields: Sequence[DataModelFieldBase] = (),
) -> None:
    """Create a Request or Response model variant."""
    if not model_fields and parser.read_only_write_only_model_type != ReadOnlyWriteOnlyModelType.RequestResponse:
        return
    reference, model_fields = prepare_rw_model_variant(
        parser, base_reference, suffix, model_fields, obj, source_fields=source_fields
    )
    create_object_model(parser, reference, obj, model_fields, model_type=data_model_type_class)


def prepare_rw_model_variant(  # noqa: PLR0913
    parser: RwSplitHost,
    base_reference: Reference,
    suffix: Literal["Request", "Response"],
    fields: Sequence[DataModelFieldBase],
    obj: JsonSchemaObject,
    *,
    source_fields: Sequence[DataModelFieldBase],
    is_root_model: bool = False,
) -> tuple[Reference, list[DataModelFieldBase]]:
    """Return the reference of the ``suffix`` variant of a model and copies of ``fields`` that name its variants.

    The variant takes the schema object, the additional properties metadata and the runtime validation rules of
    the model.
    """
    variant_fields = [parser._copy_model_field(field) for field in fields]  # noqa: SLF001
    reference = parser._get_rw_model_variant_reference(base_reference, suffix, loaded=True)  # noqa: SLF001
    update_field_refs_for_variant(parser, variant_fields, suffix)
    parser._record_schema_object(reference, obj)  # noqa: SLF001
    parser._update_variant_additional_properties_metadata(reference.path, obj, suffix)  # noqa: SLF001
    parser._copy_schema_runtime_validation_for_variant(  # noqa: SLF001
        base_reference.path,
        reference.path,
        variant_fields,
        suffix,
        obj=obj,
        source_fields=source_fields,
        is_root_model=is_root_model,
    )
    return reference, variant_fields


def create_object_model(  # noqa: PLR0913
    parser: RwSplitHost,
    reference: Reference,
    obj: JsonSchemaObject,
    fields: list[DataModelFieldBase],
    *,
    model_type: type[DataModel],
    base_classes: list[Reference] | None = None,
    nullable: bool | None = None,
) -> DataModel:
    """Create and register the model of an object schema, nullable as given or else where the schema admits null."""
    model = parser._create_data_model(  # noqa: SLF001
        model_type=model_type,
        reference=reference,
        fields=fields,
        base_classes=base_classes,
        custom_base_class=parser._resolve_base_class(reference.name, obj.custom_base_path),  # noqa: SLF001
        custom_template_dir=parser.custom_template_dir,
        extra_template_data=parser.extra_template_data,
        path=parser.current_source_path,
        description=obj.description if parser.use_schema_description else None,
        nullable=parser._object_nullable(obj) if nullable is None else nullable,  # noqa: SLF001
        keyword_only=parser.keyword_only,
        treat_dot_as_module=parser.treat_dot_as_module,
        dataclass_arguments=parser.dataclass_arguments,
    )
    parser._graph_builder.register_model(model)  # noqa: SLF001
    return model


def register_object_model(  # noqa: PLR0913
    parser: RwSplitHost,
    reference: Reference,
    obj: JsonSchemaObject,
    fields: list[DataModelFieldBase],
    base_classes: list[Reference] | None,
    *,
    model_type: type[DataModel],
    nullable: bool | None = None,
) -> DataModel | None:
    """Register the model of an object schema and the read/write variants its fields split it into.

    Returns the model, or None where only its variants are written.
    """
    if (all_fields := get_separate_model_fields(parser, reference, fields, base_classes)) is not None:
        create_request_response_models(
            parser,
            reference=reference,
            obj=obj,
            all_fields=all_fields,
            own_fields=fields,
            data_model_type_class=model_type,
        )
    if not should_generate_base_model(parser, generates_separate_models=all_fields is not None):
        parser._unregister_temporary_field_references(fields)  # noqa: SLF001
        return None
    return create_object_model(
        parser, reference, obj, fields, model_type=model_type, base_classes=base_classes, nullable=nullable
    )


def create_request_response_models(  # noqa: PLR0913, PLR0917
    parser: RwSplitHost,
    reference: Reference,
    obj: JsonSchemaObject,
    all_fields: list[DataModelFieldBase],
    own_fields: list[DataModelFieldBase],
    data_model_type_class: type[DataModel],
) -> None:
    """Generate Request and Response model variants."""
    facts = get_rw_model_field_facts(parser, all_fields)
    if request_response := parser.read_only_write_only_model_type == ReadOnlyWriteOnlyModelType.RequestResponse:
        if "legacy-rw-parsed-facts" in parser._legacy_rw_variants:  # noqa: SLF001
            parser._rw_model_field_facts_cache[reference.path] = facts  # noqa: SLF001
        parser._rw_model_variant_requirement_cache[reference.path, "Request"] = True  # noqa: SLF001
        parser._rw_model_variant_requirement_cache[reference.path, "Response"] = True  # noqa: SLF001
        parser._request_response_fields[reference.path] = tuple(copy_unregistered_fields(parser, all_fields))  # noqa: SLF001
        parser._raw_inherited_own_names_cache[reference.path] = frozenset(  # noqa: SLF001
            field_name for field in own_fields if (field_name := _field_source_name(field)) is not None
        )
    variants: list[tuple[Literal["Request", "Response"], list[DataModelFieldBase]]] = [
        (suffix, _rw_variant_fields(all_fields, suffix))
        for suffix in ("Request", "Response")
        if request_response or _rw_facts_need_variant(facts, suffix)
    ]
    for suffix, model_fields in variants:
        parser._create_variant_model(  # noqa: SLF001
            reference,
            suffix,
            model_fields,
            obj,
            data_model_type_class,
            source_fields=all_fields,
        )
