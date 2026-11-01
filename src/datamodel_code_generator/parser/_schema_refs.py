"""The reference reading of the JSON Schema parser (L0/L1): reference keys, `$ref` siblings, cycles and dynamic scope.

These functions resolve what a reference names (the key of its edge in the schema graph, its spelling in the current
document and the node it reaches), load and share the schema objects references name, merge a `$ref` with its sibling
keywords into their intersection (``parser/_intersection.py``), tell whether a referenced schema reaches itself, and
resolve `$recursiveRef` and `$dynamicRef` against the dynamic scope of the model being parsed (rule DS), parsing a
referenced schema again for each scope that binds its `$dynamicAnchor` names differently.

Each function takes the parser through ``SchemaRefsHost``, which ``JsonSchemaParser`` satisfies structurally. The
methods that tests call or that other parts of the parser call stay on the parser as delegators to these functions
(``parser/jsonschema.py``), and these functions call them through the parser. Parser methods too: ``resolve_ref`` and
``_add_ref``, since functions named ``resolve_ref`` and ``add_ref`` would add to the calls the generation observer
counts; ``_warn_unsatisfiable``, ``_warn_untaken_patterns``,
``_warn_intersection`` and ``_conjoined_pattern``, frames a warning names; ``_uses_builtin_false_ref_facts`` and
``_walk_schema_objects``, which read names of ``parser/jsonschema.py``; the extension hook ``_document_self_context``;
and the context managers ``_home_context``, ``_ref_document_context``, ``_schema_location_context``,
``_dynamic_target_ref``, ``_dynamic_scope_context`` and ``_ref_file_context``, which ``--debug`` traces as parser
methods. They read a loaded document by JSON pointer with the walk of ``parser/_schema_reading.py``, which the
document reading (``parser/_schema_documents.py``) shares.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from functools import partial
from itertools import chain
from typing import TYPE_CHECKING, Any, ClassVar, Literal, NamedTuple, TypeVar, cast
from urllib.parse import unquote, urljoin

from typing_extensions import Protocol, final

from datamodel_code_generator import Error
from datamodel_code_generator._json_pointer import escape_token, join_pointer
from datamodel_code_generator._json_schema import REF_PRE_PASS_FIELDS, REF_PRE_PASS_POSITIONS, SCHEMA_SHAPES
from datamodel_code_generator._records import record
from datamodel_code_generator._scc import strongly_connected_components
from datamodel_code_generator._url_redaction import named_location, redact_reference
from datamodel_code_generator.parser._intersection import concatenating_row
from datamodel_code_generator.parser._schema_graph import (
    DocumentId,
    RefEdge,
    RefKey,
    RefReading,
    ResolvedRef,
    SchemaGraphBuilder,
    _TargetNode,
    canonical_ref_spelling,
)
from datamodel_code_generator.parser._schema_object import JSONReference, JsonSchemaObject, _locate, get_ref_type
from datamodel_code_generator.parser._schema_reading import (
    _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS,
    _MISSING_JSON_POINTER,
    _fragment_value,
    _get_model_by_decoded_path,
    _intersect_dumps,
    _is_plain_name_fragment,
    _ref_edge_child_entries,
    _without_contains_keywords,
    get_model_by_path,
    split_json_pointer,
)
from datamodel_code_generator.parser.base import get_special_path
from datamodel_code_generator.reference import ModelResolver, Reference, SchemaLocation, is_url

if TYPE_CHECKING:
    from collections import deque
    from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
    from contextlib import AbstractContextManager

    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.parser._intersection import KeywordIntersection
    from datamodel_code_generator.types import DataType

__all__ = [
    "SchemaRefsHost",
    "analysis_ref_target",
    "anchor_ref_path",
    "bound_dynamic_anchor",
    "build_anchor_indexes",
    "compute_edge_key",
    "compute_ref_key",
    "current_document_root",
    "current_dynamic_document",
    "current_dynamic_scope",
    "declares_recursive_anchor",
    "derived_ref",
    "dynamic_anchor_bindings",
    "dynamic_anchors_used",
    "dynamic_scope_resource",
    "dynamic_scope_target",
    "dynamic_specialization_reference",
    "dynamic_target_bindings",
    "get_contains_bounds",
    "get_dynamic_scope_data_type",
    "get_ref_raw_schema",
    "graph",
    "has_ref_cycle",
    "intersect_ref_siblings",
    "intersect_ref_siblings_schema",
    "is_local_ref",
    "is_local_ref_false_schema",
    "is_ref_circular",
    "iter_referencing_schemas",
    "key_node",
    "lexical_dynamic_anchor",
    "load_ref_schema_object",
    "location_ref",
    "mark_reported_value_conflict",
    "merge_ref_with_schema",
    "outside_home",
    "parse_dynamic_specializations",
    "pin_recursive_refs",
    "prepared_ref_document",
    "push_ref_cycle_frame",
    "read_as_schema",
    "ref_file_document",
    "ref_key",
    "ref_path",
    "ref_target_dump",
    "resolve_dynamic_ref",
    "resolve_recursive_ref",
    "resolve_ref_callback",
    "schema_document_context",
    "schema_location",
    "shared_object",
    "shared_schema_object",
    "subschema_scope",
    "traverse_schema_objects",
]

_MERGED_REF_TARGET_KEY = "_merged_ref_target"
_REPORTED_VALUE_CONFLICT_KEY = "_reported_value_conflict"
_VALUE_CONFLICT_KEYWORDS = frozenset({"enum", "const"})
_ValidatedT = TypeVar("_ValidatedT")
_REF_PRE_PASS_KEYWORDS = frozenset(REF_PRE_PASS_POSITIONS)


def _iter_ref_walk_children(data: dict[str, Any]) -> Iterator[dict[str, Any] | list[Any]]:
    """Yield the subschemas of a raw schema in keyword order, leaving instance values and metadata out."""
    for keyword, value in data.items():
        match SCHEMA_SHAPES.get(keyword), value:
            case "map", dict():
                yield from (schema for schema in value.values() if isinstance(schema, dict))
            case "sequence" | "single" | "single-or-sequence", dict() | list():
                yield value


def _ref_path_parts(file_part: str) -> list[str]:
    """Split the document part of a resolved reference into model resolver root parts."""
    if is_url(file_part):
        return [file_part]
    return file_part.split("/") if file_part else []


def _nearest_recursive_anchor(anchors: Iterable[str], current_ref: str) -> str:
    """Return the longest $recursiveAnchor reference enclosing a location reference, or the document root."""
    nearest = "#"
    for anchor in anchors:
        if (
            len(anchor) > len(nearest)
            and current_ref.startswith(anchor)
            and current_ref[len(anchor) : len(anchor) + 1] in {"", "/"}
        ):
            nearest = anchor
    return nearest


@final
@record
class _DynamicEdges:
    """What the `$dynamicRef` analysis reads of a referenced schema (``JsonSchemaParser._dynamic_ref_edges``)."""

    node: _TargetNode
    """The schema graph node of the schema, or its key when the graph does not locate it."""
    anchors: frozenset[str]
    """The `$dynamicAnchor` names its `$dynamicRef` initially resolve to."""
    targets: tuple[str, ...]
    """The keys of the schemas its `$ref` and those `$dynamicAnchor` name."""


class _DynamicSpecialization(NamedTuple):
    """A referenced schema to parse again with the $dynamicAnchor bindings of its dynamic scope."""

    target: str
    bindings: dict[str, str]
    reference: Reference
    path: tuple[str, ...]


@final
@record
class _DynamicTarget:
    """Where rule DS (``JsonSchemaParser._dynamic_scope_target``) resolves a reference in the dynamic scope."""

    ref: str = "#"
    """The reference to resolve from the current document, a plain string: it has no edge."""
    location: str | None = None
    """The registry location of the `$dynamicAnchor` the dynamic scope binds the reference to, which is resolved
    instead of ``ref`` (``_schema_location_context``)."""


def _pointer_ref(parts: Sequence[str]) -> str:
    """Return the local reference of a JSON pointer given as decoded parts."""
    return "#" + join_pointer(parts)


def _unanchored_resource_ref(raw: dict[str, Any], parts: Sequence[str]) -> str | None:
    """Return the nearest schema along a pointer that declares an $id resource, when it has no $recursiveAnchor.

    A $recursiveRef initially resolves to the root of its schema resource, and stays there without one.
    """
    for index in range(len(parts), 0, -1):
        match _get_model_by_decoded_path(raw, list(parts[:index]), or_missing=True):
            case {"$id": str() as identifier} as schema if not identifier.startswith("#"):
                return None if schema.get("$recursiveAnchor") is True else _pointer_ref(parts[:index])
    return None


class SchemaRefsHost(Protocol):
    """The options, caches and hooks of the parser that the reference reading reads."""

    SCHEMA_OBJECT_TYPE: ClassVar[type[JsonSchemaObject]]
    _named_entry_maps: ClassVar[frozenset[str]]
    raw_obj: dict[str, YamlValue]
    _circular_ref_cache: dict[str, bool]
    _draining_dynamic_specializations: bool
    _dynamic_anchor_index: dict[tuple[str, ...], dict[str, str]]
    _dynamic_anchor_usage: dict[_TargetNode, frozenset[str]]
    _dynamic_anchors: dict[tuple[str, str], dict[str, str]]
    _dynamic_bindings: dict[str, str]
    _dynamic_document: str | None
    _dynamic_ref_graph: dict[_TargetNode, _DynamicEdges]
    _dynamic_ref_nodes: dict[str, _TargetNode]
    _dynamic_scope_path: tuple[str, ...]
    _dynamic_scope_pointer: str
    _dynamic_specialization_paths: set[tuple[str, ...]]
    _dynamic_specializations: dict[tuple[_TargetNode, frozenset[tuple[str, str]]], Reference]
    _embedded_resources: dict[tuple[str, str], dict[str, str]]
    _false_schema_refs: set[str] | None
    _has_embedded_schema_resources: bool
    _pending_dynamic_specializations: deque[_DynamicSpecialization]
    _recursive_anchor_index: dict[tuple[str, ...], list[str]]
    _ref_data_type_facts: dict[str, tuple[Any, bool]]
    _ref_target_dumps: dict[tuple[_TargetNode, str], tuple[dict[str, Any], str]]
    _schema_resource_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]]
    _schema_resource_locations: dict[str, str]
    _schema_resource_path_parts: dict[str, list[str]]
    _schema_resource_root_bases: dict[str, str]
    _schema_resources: dict[str, set[str]]

    @property
    def generate_schema_validators(self) -> bool:
        """Whether the models validate the schema rules their fields cannot express."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""

    @property
    def schema_paths(self) -> list[tuple[str, list[str]]]:
        """Get schema paths for definitions and defs."""

    @property
    def _graph(self) -> SchemaGraphBuilder:
        """Return the schema graph of the parse."""

    @property
    def _legacy_intersection(self) -> frozenset[str]:
        """The rows of ``parser/_intersection.py:LEGACY_ROWS`` kept: every row without --intersect-allof-keywords."""

    @property
    def _schema_graph(self) -> SchemaGraphBuilder | None:
        """The schema graph of the parse, once it is built."""

    def get_ref_data_type(self, ref: str) -> DataType:
        """Return the data type a reference names."""

    def resolve_ref(self, object_ref: str) -> Reference:
        """Resolve a reference by loading and parsing the referenced schema."""

    def _add_ref(self, ref: str) -> Reference:
        """Return the Reference at the key of a reference's edge, named after the edge's spelling if it is new."""

    def _analysis_ref_target(self, document: str, file_part: str, ref: str) -> str | None:
        """Resolve a $ref met while analyzing $dynamicRef reachability, without loading its target."""

    def _anchor_ref_path(self, root_key: tuple[str, ...], path: list[str]) -> str:
        """Return the local ref path for an anchor under the current root."""

    def _compute_edge_key(self, edge: RefEdge) -> RefKey:
        """Resolve the reference of an edge as the document that holds it resolves it (``_compute_ref_key``)."""

    def _current_dynamic_document(self) -> str:
        """Return the document of the model root whose dynamic scope is being resolved."""

    def _current_dynamic_scope(self) -> dict[str, str]:
        """Return the $dynamicAnchor bindings that schemas entered from the model being parsed see."""

    def _declares_recursive_anchor(self) -> bool:
        """Return whether the document being parsed has registered a `$recursiveAnchor` so far."""

    def _derived_ref(self, value: str, source: SchemaLocation | None, keyword: str, ref: str) -> str:
        """Return ``value``, a reference the parser derives from the JSON object at ``source`` of a loaded document."""

    def _dynamic_scope_context(
        self,
        path: Sequence[str],
        bindings: dict[str, str],
        pointer: str | None = None,
        document: str | None = None,
    ) -> AbstractContextManager[None]:
        """Track the model root being parsed and the $dynamicAnchor bindings of its dynamic scope."""

    def _dynamic_target_bindings(self, resolved_ref: str) -> dict[str, str]:
        """Return the bindings under which a referenced schema resolves $dynamicRef differently."""

    def _dynamic_target_ref(self, target: _DynamicTarget) -> AbstractContextManager[str]:
        """Yield the reference to a target of rule DS that resolves from the current document."""

    @classmethod
    def _get_array_items_constraints(cls, obj: JsonSchemaObject) -> dict[str, int]:
        """Return array length constraints derived from boolean items."""

    def _get_ref_body(self, resolved_ref: str) -> dict[str, YamlValue]:
        """Get the body of a reference from URL or remote file."""

    def _get_ref_raw_schema(self, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
        """Return the value of a loaded document at a reference key, with the references it holds as a schema."""

    def _home_context(self, document: str) -> AbstractContextManager[None]:
        """Resolve references as a loaded document resolves its own: from its location, its root `$id` and `$self`."""

    def _inherited_ref_context(self, resolved_ref: str) -> AbstractContextManager[None]:
        """Resolve nested references relative to the inherited schema's own file and schema resource."""

    def _is_ref_circular(self, resolved_ref: str) -> bool:
        """Check if a resolved $ref target contains a circular reference (cached)."""

    def _iter_referencing_schemas(self, schema: object, pointer: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Visit the raw subschemas that the parser follows, with their JSON pointers."""

    def _iter_schema_definition_entries(
        self,
        definitions: dict[str, YamlValue],
        base_path: list[str],
    ) -> Iterator[tuple[str, YamlValue, list[str]]]:
        """Yield the name, value and path of each definition, descending into namespaces of definitions."""

    def _iter_schema_resource_children(
        self, schema: dict[str, Any]
    ) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
        """Visit schema-valued keywords without inspecting examples, defaults or extensions."""

    def _key_node(self, resolved_ref: str) -> _TargetNode:
        """Return the schema graph node at a reference key, or the key when the graph does not locate its value."""

    def _lenient_ref(self, ref: str) -> str:
        """Return a reference of the current document mapped as its lenient resolution maps it, if it has one."""

    def _lexical_dynamic_anchor(self, document: str, pointer: str, ref: str) -> str | None:
        """Return the $dynamicAnchor location a plain-name $dynamicRef at a location initially resolves to."""

    def _load_ref_schema_object(self, ref: str) -> JsonSchemaObject:
        """Return the schema object a reference names."""

    def _location_ref(self, location: str) -> str:
        """Return the resolved reference of a registry location in a registered document."""

    def _mark_reported_value_conflict(self, schema: JsonSchemaObject, keywords: Sequence[str]) -> None:
        """Mark a merged schema whose ``enum`` or ``const`` conflict the intersection warning already reports."""

    def _merge_ref_with_schema(self, obj: JsonSchemaObject, where: str | None = None) -> JsonSchemaObject:
        """Return the schema a ``$ref`` names merged with the sibling keywords of the reference."""

    def _merge_schema_constraints(
        self,
        base_dict: dict[str, Any],
        items: list[JsonSchemaObject],
        *,
        intersect: bool,
    ) -> None:
        """Merge the constraints of ``items`` into ``base_dict``, intersecting them when ``intersect`` is set."""

    def _normalize_external_ref(self, ref: str) -> str:
        """Resolve an external anchor before falling back to legacy shorthand pointers."""

    def _outside_home(self, ref: str) -> bool:
        """Return whether a reference with an edge is read outside the document that holds it, the one parsed now."""

    def _parse_schema_obj(
        self,
        name: str,
        obj: JsonSchemaObject,
        path: list[str],
    ) -> None:
        """Dispatch a JsonSchemaObject to the parse method for its schema shape (``_DEFINITION_TABLE``)."""

    def _prepared_ref_document(self, file_part: str) -> tuple[str, dict[str, Any]]:
        """Return the document a resolved reference points into and its prepared schema, loading it once."""

    def _read_as_schema(self, raw: YamlValue) -> YamlValue:
        """Return a value of a loaded document that a reference names, with the references it holds as a schema."""

    def _ref_document_context(self, file_part: str) -> AbstractContextManager[tuple[str, dict[str, Any]]]:
        """Enter the resolution context of the document that a resolved reference points into."""

    def _ref_file_context(self, file_part: str) -> AbstractContextManager[None]:
        """Resolve references relative to the file or URL a reference key names, or to the current one without it."""

    def _ref_key(self, ref: str, *, embedded: bool = True) -> RefKey:
        """Return what a reference resolves to: the key of its edge, the same at every site and in every context."""

    def _ref_path(self, ref: str) -> str:
        """Return the reference key a reference names."""

    def _ref_reading(self, root: Literal["root", "object"]) -> RefReading:
        """Return how ``SchemaGraphBuilder.with_ref_edges`` reads a document (``root``) or a schema (``object``)."""

    def _ref_siblings_are_parts_of(self, schema: JsonSchemaObject) -> bool:
        """Return whether the keywords beside the ``$ref`` of ``schema`` are parts of it."""

    def _ref_target_dump(self, ref: str, resolved_ref: str) -> tuple[dict[str, Any], str]:
        """Return a fresh dump of a referenced schema for the current document, and the reference it ends at."""

    def _resolve_external_ref_mapping(self, ref: str) -> tuple[str, str] | None:
        """Return the package and fragment ``--external-ref-mapping`` maps a reference to, if any."""

    def _resolve_recursive_ref(self, item: JsonSchemaObject, path: list[str]) -> str | None:
        """Resolve $recursiveRef to an equivalent $ref."""

    def _resolve_schema_resource_ref(self, reference: str, base: str, document: str, *, nested_scope: bool) -> str:
        """Resolve registered resources before considering a physical document fetch."""

    def _schema_location(self, resolved_ref: str) -> str:
        """Return the registry location of a resolved reference."""

    def _schema_object(self, raw: object) -> JsonSchemaObject:
        """Validate a raw schema into a schema object."""

    def _schema_resource_document(self, path_parts: list[str]) -> str:
        """Identify one physical schema document without changing its public reference names."""

    def _shared_object(self, raw: object, kind: type[_ValidatedT], validate: Callable[[], _ValidatedT]) -> _ValidatedT:
        """Return the ``kind`` object a value of a loaded document validates to, validated once by the schema graph."""

    def _shared_schema_object(self, raw: dict[str, YamlValue] | YamlValue, path: list[str]) -> JsonSchemaObject:
        """Return the schema object a value of a loaded document validates to, shared by its readers."""

    def _subschema_scope(self, obj: JsonSchemaObject) -> AbstractContextManager[None] | None:
        """Return the dynamic scope of a subschema that stands for another schema resource, if any."""

    def _validate_schema_object(
        self,
        raw: dict[str, YamlValue] | YamlValue,
        path: list[str],
    ) -> JsonSchemaObject:
        """Validate raw data as JsonSchemaObject with path context in errors; a too deep nesting stops the parse."""

    def _walk_schema_objects(
        self,
        obj: JsonSchemaObject,
        path: list[str],
        callback: Callable[[JsonSchemaObject, list[str]], None],
    ) -> None:
        """Apply callback to a schema object and traverse its child schemas at ``REF_PRE_PASS_POSITIONS``."""

    def _warn_intersection(self, where: str, intersection: KeywordIntersection, *parts: str) -> None:
        """Warn about the keywords of an intersection whose parts have no value in common, and its untaken patterns."""


def get_ref_raw_schema(parser: SchemaRefsHost, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
    return read_ref_value(parser, resolved_ref)


def ref_file_document(parser: SchemaRefsHost, file_part: str) -> dict[str, YamlValue]:
    """Return the loaded document the file part of a reference key names: the document being parsed when it is empty."""
    return parser._get_ref_body(file_part) if file_part else parser.raw_obj  # noqa: SLF001


def read_ref_value(parser: SchemaRefsHost, resolved_ref: str) -> dict[str, YamlValue] | YamlValue:
    """Return the value of a loaded document at a reference key, with the references it holds as a schema."""
    file_part, _, fragment = resolved_ref.partition("#")
    return parser._read_as_schema(_fragment_value(ref_file_document(parser, file_part), fragment))  # noqa: SLF001


def read_as_schema(parser: SchemaRefsHost, raw: YamlValue) -> YamlValue:
    """Return a value of a loaded document that a reference names, with the references it holds as a schema."""
    if (
        (graph := parser._schema_graph) is None  # noqa: SLF001
        or not isinstance(raw, dict)
        or (location := graph.locations.get(id(raw))) is None
    ):
        return raw
    document = DocumentId(parser._schema_resource_document([location.document]))  # noqa: SLF001
    return graph.read_as_schema(raw, location, document, parser._ref_reading("object"))  # noqa: SLF001


def ref_reading(parser: SchemaRefsHost, root: Literal["root", "object"]) -> RefReading:
    """Return how ``SchemaGraphBuilder.with_ref_edges`` reads a document (``root``) or a schema (``object``)."""
    return RefReading(partial(_ref_edge_child_entries, named_entry_maps=parser._named_entry_maps), "object", root)  # noqa: SLF001


def ref_schema_exists(parser: SchemaRefsHost, resolved_ref: str) -> bool:
    """Return whether a resolved JSON pointer identifies a source schema."""
    file_part, _, fragment = resolved_ref.partition("#")
    return _fragment_value(ref_file_document(parser, file_part), fragment, or_missing=True) is not _MISSING_JSON_POINTER


def load_ref_schema_object(parser: SchemaRefsHost, ref: str) -> JsonSchemaObject:
    """Load a JsonSchemaObject from a $ref using standard resolve/load pipeline."""
    resolved_ref = parser._ref_path(ref)  # noqa: SLF001
    return parser._shared_schema_object(parser._get_ref_raw_schema(resolved_ref), [resolved_ref])  # noqa: SLF001


def graph(parser: SchemaRefsHost) -> SchemaGraphBuilder:
    """Return the schema graph of the parse."""
    return cast("SchemaGraphBuilder", parser._schema_graph)  # noqa: SLF001


def key_node(parser: SchemaRefsHost, resolved_ref: str) -> _TargetNode:
    """Return the schema graph node at a reference key, or the key when the graph does not locate its value.

    The value is read as ``_load_ref_schema_object`` reads it, without validating it.
    """
    node = parser._graph.node(read_ref_value(parser, resolved_ref))  # noqa: SLF001
    return resolved_ref if node is None else node


def shared_schema_object(
    parser: SchemaRefsHost, raw: dict[str, YamlValue] | YamlValue, path: list[str]
) -> JsonSchemaObject:
    """Return the schema object a value of a loaded document validates to, validated once and shared by its readers.

    ``path`` names the reference in the error of a value that does not validate.
    """
    return parser._shared_object(raw, parser.SCHEMA_OBJECT_TYPE, partial(parser._validate_schema_object, raw, path))  # noqa: SLF001


def shared_object(
    parser: SchemaRefsHost, raw: object, kind: type[_ValidatedT], validate: Callable[[], _ValidatedT]
) -> _ValidatedT:
    """Return the ``kind`` object a value of a loaded document validates to, validated once by the schema graph."""
    return parser._graph.schema(raw, kind, validate)  # noqa: SLF001


def current_document_root(parser: SchemaRefsHost) -> SchemaLocation:
    """Return the location of the root of the document whose schemas are being parsed."""
    return SchemaLocation(
        parser.model_resolver.join_path(tuple(parser.model_resolver.current_root)).partition("#")[0], ()
    )


def derived_ref(parser: SchemaRefsHost, value: str, source: SchemaLocation | None, keyword: str, ref: str) -> str:
    """Return ``value``, a reference the parser derives from the JSON object at ``source`` of a loaded document.

    It carries the edge of ``ref``, the reference under ``keyword`` of that object that names the same target from
    its document. An object the schema graph does not locate gives no edge; a ``ResolvedRef`` keeps its own.
    """
    if source is None or isinstance(value, ResolvedRef):
        return value
    document = DocumentId(parser._schema_resource_document([source.document]))  # noqa: SLF001
    return ResolvedRef(value, RefEdge(source, keyword, document, ref, canonical_ref_spelling(str(ref))))


def compute_edge_key(parser: SchemaRefsHost, edge: RefEdge) -> RefKey:
    """Resolve the reference of an edge as the document that holds it resolves it (``_compute_ref_key``)."""
    with parser._home_context(edge.home or edge.source.document):  # noqa: SLF001
        return compute_ref_key(parser, edge.spelling, embedded=True)


def compute_ref_key(parser: SchemaRefsHost, ref: str, *, embedded: bool) -> RefKey:
    """Resolve a reference in the current context: its mapping, embedded resource, normalized spelling and key.

    ``embedded`` resolves a reference through the schema resource an `$id` of the current document embeds first.
    """
    if (mapping := parser._resolve_external_ref_mapping(ref)) is not None:  # noqa: SLF001
        return RefKey(parser.model_resolver.resolve_ref(ref), ref, mapping)
    if embedded and parser._has_embedded_schema_resources:  # noqa: SLF001
        document = parser._schema_resource_document(list(parser.model_resolver.current_root))  # noqa: SLF001
        ref = parser._resolve_schema_resource_ref(  # noqa: SLF001
            ref,
            parser._schema_resource_root_bases.get(document, document),  # noqa: SLF001
            document,
            nested_scope=False,
        )
    ref = parser._normalize_external_ref(ref)  # noqa: SLF001
    return RefKey(parser.model_resolver.resolve_ref(ref), ref)


def ref_key(parser: SchemaRefsHost, ref: str, *, embedded: bool = True) -> RefKey:
    """Return what a reference resolves to: the key of its edge, the same at every site and in every context.

    A plain string has no edge: it resolves in the current context (``_compute_ref_key``).
    """
    if isinstance(ref, ResolvedRef):
        return parser._graph.edge_key(ref.edge, parser._compute_edge_key)  # noqa: SLF001
    return compute_ref_key(parser, ref, embedded=embedded)


def ref_path(parser: SchemaRefsHost, ref: str) -> str:
    """Return the reference key a reference names: its edge's key, or a plain string's ``resolve_ref``."""
    return parser._ref_key(ref).key if isinstance(ref, ResolvedRef) else parser.model_resolver.resolve_ref(ref)  # noqa: SLF001


def outside_home(parser: SchemaRefsHost, ref: str) -> bool:
    """Return whether a reference with an edge is read outside the document that holds it, the one parsed now."""
    return isinstance(ref, ResolvedRef) and ref.edge.document != parser._schema_resource_document(  # noqa: SLF001
        list(parser.model_resolver.current_root)
    )


def is_local_ref(parser: SchemaRefsHost, ref: str) -> bool:
    """Return whether a reference is a fragment of the document parsed now: written as one there, by its edge."""
    return get_ref_type(ref) == JSONReference.LOCAL and not parser._outside_home(ref)  # noqa: SLF001


def is_local_ref_false_schema(
    parser: SchemaRefsHost,
    ref: str,
    *,
    use_builtin_facts: bool,
) -> bool:
    """Return whether a local ref targets false without repeating default validation."""
    if not use_builtin_facts:
        return parser._load_ref_schema_object(ref).is_boolean_schema_false  # noqa: SLF001

    resolved_ref = parser._ref_path(ref)  # noqa: SLF001
    if (false_schema_refs := parser._false_schema_refs) is not None and resolved_ref in false_schema_refs:  # noqa: SLF001
        return True
    if resolved_ref in parser._ref_data_type_facts:  # noqa: SLF001
        return False
    return parser._load_ref_schema_object(ref).is_boolean_schema_false  # noqa: SLF001


def anchor_ref_path(parser: SchemaRefsHost, root_key: tuple[str, ...], path: list[str]) -> str:  # noqa: ARG001
    """Return the local ref path for an anchor under the current root."""
    root_len = len(root_key)
    if root_len >= len(path):
        return "#"
    suffix_parts = path[root_len:]
    first = suffix_parts[0]
    if first.startswith("#"):
        suffix_parts = [first[1:].lstrip("/"), *suffix_parts[1:]]
    return "#/" + "/".join(escape_token(part) for part in suffix_parts if part)


def build_anchor_indexes(parser: SchemaRefsHost, obj: JsonSchemaObject, path: list[str]) -> None:
    """Build $recursiveAnchor and $dynamicAnchor indexes for a schema object."""
    root_key = tuple(parser.model_resolver.current_root)
    ref_path = parser._anchor_ref_path(root_key, path)  # noqa: SLF001
    if obj.recursiveAnchor:
        anchors = parser._recursive_anchor_index.setdefault(root_key, [])  # noqa: SLF001
        if ref_path not in anchors:
            anchors.append(ref_path)
    if obj.dynamicAnchor:
        parser._dynamic_anchor_index.setdefault(root_key, {}).setdefault(obj.dynamicAnchor, ref_path)  # noqa: SLF001


def enclosing_unanchored_resource_ref(parser: SchemaRefsHost) -> str | None:
    """Return the nearest $id resource enclosing the schema being parsed, when it has no $recursiveAnchor."""
    if (
        not parser._has_embedded_schema_resources  # noqa: SLF001
        or (cached := parser._schema_resource_cache.get(parser._current_dynamic_document())) is None  # noqa: SLF001
    ):
        return None
    raw = cached[1]
    return _unanchored_resource_ref(raw, split_json_pointer(raw, parser._dynamic_scope_pointer))  # noqa: SLF001


def resolve_recursive_ref(parser: SchemaRefsHost, item: JsonSchemaObject, path: list[str]) -> str | None:
    """Resolve $recursiveRef to an equivalent $ref.

    Per JSON Schema 2019-09, $recursiveRef only allows "#" as value.
    Resolves to the root of the nearest enclosing $id resource when it has no $recursiveAnchor,
    and otherwise to the nearest enclosing schema with $recursiveAnchor: true.
    For standalone JSON Schema files, this is the root "#".
    For OpenAPI, this is the component schema definition path.
    """
    if item.recursiveRef != "#":  # pragma: no cover
        return None
    if (resource := enclosing_unanchored_resource_ref(parser)) is not None:
        return resource
    root_key = tuple(parser.model_resolver.current_root)
    current_ref = parser._anchor_ref_path(root_key, path)  # noqa: SLF001
    return _nearest_recursive_anchor(parser._recursive_anchor_index.get(root_key, ()), current_ref)  # noqa: SLF001


def resolve_dynamic_ref(parser: SchemaRefsHost, item: JsonSchemaObject) -> str | None:
    """Return the first $dynamicAnchor of the name of a fragment $dynamicRef in the document being parsed, if any.

    Per JSON Schema 2020-12, a $dynamicRef first resolves like a $ref, and an anchor of its name in the dynamic
    scope then overrides that target. In code generation, the dynamic scope is resolved statically via index lookup.
    """
    if not (ref := item.dynamicRef) or not ref.startswith("#"):
        return None
    return parser._dynamic_anchor_index.get(tuple(parser.model_resolver.current_root), {}).get(ref[1:])  # noqa: SLF001


def dynamic_scope_resource(parser: SchemaRefsHost, document: str, pointer: str) -> str | None:
    """Return the root pointer of the schema resource enclosing a location in a registered document."""
    if (resources := parser._schema_resources.get(document)) is None:  # noqa: SLF001
        return None
    while pointer and pointer not in resources:
        pointer = pointer.rpartition("/")[0]
    return pointer


def lexical_dynamic_anchor(parser: SchemaRefsHost, document: str, pointer: str, ref: str) -> str | None:
    """Return the $dynamicAnchor location a plain-name $dynamicRef at a location initially resolves to.

    Plain-name fragments resolve within the enclosing schema resource, so embedded resources
    that reuse an anchor name keep their own targets.
    """
    if not ref.startswith("#") or (resource := dynamic_scope_resource(parser, document, pointer)) is None:
        return None
    return parser._dynamic_anchors.get((document, resource), {}).get(ref[1:])  # noqa: SLF001


def dynamic_anchor_bindings(
    parser: SchemaRefsHost, document: str, pointer: str, outer: dict[str, str]
) -> dict[str, str]:
    """Bind each $dynamicAnchor name to its outermost declaration in a dynamic scope.

    Outer bindings come from the schemas that referenced the model being parsed, which entered the
    dynamic scope first; the schema resource enclosing the location declares the others.
    """
    if (resource := dynamic_scope_resource(parser, document, pointer)) is None:
        return dict(outer)
    return {**parser._dynamic_anchors.get((document, resource), {}), **outer}  # noqa: SLF001


def current_dynamic_document(parser: SchemaRefsHost) -> str:
    """Return the document of the model root whose dynamic scope is being resolved."""
    if (document := parser._dynamic_document) is not None:  # noqa: SLF001
        return document
    return parser._schema_resource_document(list(parser.model_resolver.current_root))  # noqa: SLF001


def current_dynamic_scope(parser: SchemaRefsHost) -> dict[str, str]:
    """Return the $dynamicAnchor bindings that schemas entered from the model being parsed see."""
    return dynamic_anchor_bindings(
        parser,
        parser._current_dynamic_document(),  # noqa: SLF001
        parser._dynamic_scope_pointer,  # noqa: SLF001
        parser._dynamic_bindings,  # noqa: SLF001
    )


def bound_dynamic_anchor(parser: SchemaRefsHost, ref: str) -> str | None:
    """Return the location a plain-name $dynamicRef resolves to in the current dynamic scope.

    A fragment that initially resolves to a $dynamicAnchor binds to the outermost schema resource
    in the dynamic scope that declares the same anchor name.
    """
    document = parser._current_dynamic_document()  # noqa: SLF001
    if (location := parser._lexical_dynamic_anchor(document, parser._dynamic_scope_pointer, ref)) is None:  # noqa: SLF001
        return None
    return parser._dynamic_bindings.get(ref[1:], location)  # noqa: SLF001


def schema_location(parser: SchemaRefsHost, resolved_ref: str) -> str:
    """Return the registry location of a resolved reference."""
    file_part, _, pointer = resolved_ref.partition("#")
    return f"{parser._schema_resource_document(_ref_path_parts(file_part))}#{pointer}"  # noqa: SLF001


def location_ref(parser: SchemaRefsHost, location: str) -> str:
    """Return the resolved reference of a registry location in a registered document."""
    document, _, pointer = location.partition("#")
    path_parts = parser._schema_resource_path_parts[document]  # noqa: SLF001
    return f"{parser.model_resolver.join_path(tuple(path_parts)).partition('#')[0]}#{pointer}"


def dynamic_target_bindings(parser: SchemaRefsHost, resolved_ref: str) -> dict[str, str]:
    """Return the bindings under which a referenced schema resolves $dynamicRef differently.

    The referenced schema sees the current dynamic scope plus its own schema resource. Anchors
    count when a $dynamicRef reachable from it resolves to them, following each bound anchor to the
    schema it names, and a binding that matches the schema's own resource keeps the shared model.
    """
    if not parser._dynamic_anchors or not (scope := parser._current_dynamic_scope()):  # noqa: SLF001
        return {}
    if not (used := set(dynamic_anchors_used(parser, resolved_ref))):
        return {}
    target_document, _, target_pointer = parser._schema_location(resolved_ref).partition("#")  # noqa: SLF001
    defaults = dynamic_anchor_bindings(parser, target_document, target_pointer, {})
    bindings = {**defaults, **scope}
    pending = [anchor for anchor in used if anchor in bindings]
    while pending:
        found = dynamic_anchors_used(parser, parser._location_ref(bindings[pending.pop()])) - used  # noqa: SLF001
        used |= found
        pending.extend(anchor for anchor in found if anchor in bindings)
    return {
        anchor: bindings[anchor]
        for anchor in sorted(used)
        if anchor in bindings and bindings[anchor] != defaults.get(anchor)
    }


def dynamic_ref_edges(parser: SchemaRefsHost, resolved_ref: str) -> _DynamicEdges:
    """Return the node at a reference key, the anchors its $dynamicRef initially resolve to and what it references.

    The schema is read from the prepared tree of its document; a key whose value the graph does not locate is its
    own node, and the first key that reaches a node gives the node its analysis.
    """
    if (node := parser._dynamic_ref_nodes.get(resolved_ref)) is not None:  # noqa: SLF001
        return parser._dynamic_ref_graph[node]  # noqa: SLF001
    file_part, _, fragment = resolved_ref.partition("#")
    document, raw = parser._prepared_ref_document(file_part)  # noqa: SLF001
    schema = _fragment_value(raw, fragment, or_missing=True)
    located = parser._graph.node(schema)  # noqa: SLF001
    node = parser._dynamic_ref_nodes[resolved_ref] = resolved_ref if located is None else located  # noqa: SLF001
    anchors: set[str] = set()
    targets: list[str] = []
    for pointer, subschema in parser._iter_referencing_schemas(schema, fragment):  # noqa: SLF001
        match subschema:
            case {"$ref": str() as ref}:
                if (target := parser._analysis_ref_target(document, file_part, ref)) is not None:  # noqa: SLF001
                    targets.append(target)
            case {"$dynamicRef": str() as dynamic_ref} if location := parser._lexical_dynamic_anchor(  # noqa: SLF001
                document, pointer, dynamic_ref
            ):
                anchors.add(dynamic_ref[1:])
                targets.append(parser._location_ref(location))  # noqa: SLF001
    return parser._dynamic_ref_graph.setdefault(node, _DynamicEdges(node, frozenset(anchors), tuple(targets)))  # noqa: SLF001


def dynamic_anchors_used(parser: SchemaRefsHost, resolved_ref: str) -> frozenset[str]:
    """Return the $dynamicAnchor names that $dynamicRef reachable from a referenced schema resolve to.

    Results are memoized per schema graph node and computed per strongly connected component of the reference
    graph, so each schema is analyzed once however many references, under whichever key, reach it.
    """
    start = dynamic_ref_edges(parser, resolved_ref)
    if (cached := parser._dynamic_anchor_usage.get(start.node)) is not None:  # noqa: SLF001
        return cached
    successors: dict[_TargetNode, set[_TargetNode]] = {start.node: set()}
    work = [(start.node, iter(start.targets))]
    while work:
        node, targets = work[-1]
        for target in targets:
            if (successor := dynamic_ref_edges(parser, target).node) in parser._dynamic_anchor_usage:  # noqa: SLF001
                continue
            successors[node].add(successor)
            if successor not in successors:
                successors[successor] = set()
                work.append((successor, iter(parser._dynamic_ref_graph[successor].targets)))  # noqa: SLF001
                break
        else:
            work.pop()
    nodes = list(successors)
    position = {node: index for index, node in enumerate(nodes)}
    graph = {position[node]: {position[successor] for successor in adjacent} for node, adjacent in successors.items()}
    for component in strongly_connected_components(graph):
        members = {nodes[index] for index in component}
        anchors: set[str] = set()
        for node in members:
            edges = parser._dynamic_ref_graph[node]  # noqa: SLF001
            anchors |= edges.anchors
            anchors.update(
                *(
                    parser._dynamic_anchor_usage[successor]  # noqa: SLF001
                    for target in edges.targets
                    if (successor := parser._dynamic_ref_nodes[target]) not in members  # noqa: SLF001
                )
            )
        parser._dynamic_anchor_usage.update(dict.fromkeys(members, frozenset(anchors)))  # noqa: SLF001
    return parser._dynamic_anchor_usage[start.node]  # noqa: SLF001


def prepared_ref_document(parser: SchemaRefsHost, file_part: str) -> tuple[str, dict[str, Any]]:
    """Return the document a resolved reference points into and its prepared schema, loading it once."""
    document = parser._schema_resource_document(_ref_path_parts(file_part))  # noqa: SLF001
    if (cached := parser._schema_resource_cache.get(document)) is not None:  # noqa: SLF001
        return document, cached[1]
    with parser._ref_document_context(file_part) as prepared:  # noqa: SLF001
        return prepared


def iter_referencing_schemas(
    parser: SchemaRefsHost, schema: object, pointer: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Visit the raw subschemas that the parser follows, with their JSON pointers.

    Keywords are those of the reference pre-pass plus the additionalItems of tuple-style arrays,
    which array parsing appends as the tail item. References under keywords the parser ignores and
    definitions that apply only through references are not analyzed.
    """
    keywords = _REF_PRE_PASS_KEYWORDS if parser.generate_schema_validators else REF_PRE_PASS_FIELDS
    stack = [(pointer, cast("dict[str, Any]", schema))] if isinstance(schema, dict) else []
    while stack:
        current_pointer, current = stack.pop()
        yield current_pointer, current
        tuple_items = isinstance(current.get("items"), list)
        stack.extend(
            (
                current_pointer + join_pointer(path),
                child,
            )
            for path, child in parser._iter_schema_resource_children(current)  # noqa: SLF001
            if path[0] in keywords or (tuple_items and path[0] == "additionalItems")
        )


def analysis_ref_target(parser: SchemaRefsHost, document: str, file_part: str, ref: str) -> str | None:
    """Resolve a $ref met while analyzing $dynamicRef reachability, without loading its target.

    Pointers into the same document need no resolution. Plain-name anchors come from the resource
    registry, so the result does not depend on which documents were parsed first, and references
    mapped to Python packages are not followed.
    """
    if ref == "#" or ref.startswith("#/"):
        return f"{file_part}{ref}"
    if parser._resolve_external_ref_mapping(ref) is not None:  # noqa: SLF001
        return None
    if location := parser._schema_resource_locations.get(urljoin(parser._schema_resource_root_bases[document], ref)):  # noqa: SLF001
        return parser._location_ref(location)  # noqa: SLF001
    with parser._ref_document_context(file_part):  # noqa: SLF001
        return parser._ref_key(ref, embedded=False).key  # noqa: SLF001


def schema_document_context(parser: SchemaRefsHost, path_parts: list[str]) -> AbstractContextManager[None]:
    """Resolve references relative to the document identified by model resolver root parts."""
    resolver = parser.model_resolver
    document = resolver.join_path(tuple(path_parts)).partition("#")[0]
    if is_url(document):
        return resolver.document_context(document, path_parts, None)
    resolve = partial(resolver.document_directory, document)
    graph = parser._schema_graph  # noqa: SLF001
    return resolver.document_context(
        document, path_parts, resolve() if graph is None else graph.document_directory(document, resolve)
    )


def dynamic_specialization_reference(parser: SchemaRefsHost, ref: str, resolved_ref: str) -> Reference | None:
    """Return a model of the referenced schema specialized for the current dynamic scope, if it differs.

    The shared model is registered first so it keeps naming priority. The specialized model is
    registered under the referencing model and named like the shared model before parsing, so
    parsing names both the same way once the referencing model is named.
    """
    if not (bindings := parser._dynamic_target_bindings(resolved_ref)):  # noqa: SLF001
        return None
    key = (dynamic_ref_edges(parser, resolved_ref).node, frozenset(bindings.items()))
    if (reference := parser._dynamic_specializations.get(key)) is not None:  # noqa: SLF001
        return reference
    shared = parser._add_ref(ref)  # noqa: SLF001
    path = get_special_path(f"dynamic-scope-{len(parser._dynamic_specializations)}", list(parser._dynamic_scope_path))  # noqa: SLF001
    reference = parser.model_resolver.add(
        path, parser.model_resolver.ref_original_name(ref, resolved_ref) or shared.original_name, class_name=True
    )
    parser._dynamic_specializations[key] = reference  # noqa: SLF001
    parser._dynamic_specialization_paths.add(tuple(path))  # noqa: SLF001
    parser._pending_dynamic_specializations.append(  # noqa: SLF001
        _DynamicSpecialization(resolved_ref, bindings, reference, tuple(path))
    )
    return reference


def parse_dynamic_specializations(parser: SchemaRefsHost) -> None:
    """Parse pending specialized models iteratively, outside of any specialization being parsed."""
    if parser._draining_dynamic_specializations or not parser._pending_dynamic_specializations:  # noqa: SLF001
        return
    parser._draining_dynamic_specializations = True  # noqa: SLF001
    try:
        while parser._pending_dynamic_specializations:  # noqa: SLF001
            parse_dynamic_specialization(parser, parser._pending_dynamic_specializations.popleft())  # noqa: SLF001
    finally:
        parser._draining_dynamic_specializations = False  # noqa: SLF001


def parse_dynamic_specialization(parser: SchemaRefsHost, specialization: _DynamicSpecialization) -> None:
    """Parse a referenced schema again under the $dynamicAnchor bindings of its dynamic scope."""
    file_part, _, fragment = specialization.target.partition("#")
    with parser._ref_document_context(file_part) as (_, raw):  # noqa: SLF001
        obj = parser._validate_schema_object(_fragment_value(raw, fragment), list(specialization.path))  # noqa: SLF001
        with parser._dynamic_scope_context(specialization.path, specialization.bindings, fragment):  # noqa: SLF001
            parser._parse_schema_obj(specialization.reference.name, obj, list(specialization.path))  # noqa: SLF001


def dynamic_scope_target(parser: SchemaRefsHost, item: JsonSchemaObject, path: list[str]) -> _DynamicTarget | None:
    """Return where rule DS resolves the `$recursiveRef` or `$dynamicRef` of a schema; None resolves its edge.

    Rule DS: a `$recursiveRef`, and a plain-name `$dynamicRef`, resolve against the dynamic scope of the model
    being parsed, not against their own document; every other reference resolves through its edge. A
    `$recursiveRef` names the root of its enclosing `$id` resource without `$recursiveAnchor`, else the innermost
    `$recursiveAnchor` that encloses it, else `#` (``_resolve_recursive_ref``). A `$dynamicRef` names the
    `$dynamicAnchor` the dynamic scope binds its name to (``_bound_dynamic_anchor``), else the first
    `$dynamicAnchor` of its name in the document being parsed (``_resolve_dynamic_ref``), else its target through
    its edge. In code generation, the dynamic scope is the chain of schema resources that referenced the model
    being parsed; schemas reached under a different scope are parsed again for it.
    """
    if item.recursiveRef:
        return _DynamicTarget(parser._resolve_recursive_ref(item, path) or "#")  # noqa: SLF001
    if not (dynamic_ref := item.dynamicRef):
        return None
    if (location := bound_dynamic_anchor(parser, dynamic_ref)) is not None:
        return _DynamicTarget(location=location)
    if (anchor := resolve_dynamic_ref(parser, item)) is not None:
        return _DynamicTarget(anchor)
    return None


def get_dynamic_scope_data_type(parser: SchemaRefsHost, item: JsonSchemaObject, path: list[str]) -> DataType:
    """Return the data type of a `$recursiveRef` or `$dynamicRef` (rule DS, ``_dynamic_scope_target``)."""
    if (target := dynamic_scope_target(parser, item, path)) is None:
        return parser.get_ref_data_type(cast("str", item.dynamicRef))
    with parser._dynamic_target_ref(target) as ref:  # noqa: SLF001
        return parser.get_ref_data_type(ref)


def subschema_scope(parser: SchemaRefsHost, obj: JsonSchemaObject) -> AbstractContextManager[None] | None:
    """Return the dynamic scope of a subschema that stands for another schema resource, if any.

    A $ref merged with its sibling keywords is parsed in place of the schema it names, and an inline
    subschema with its own $id is an embedded schema resource. The $dynamicRef in either initially
    resolve within that resource, while the $dynamicAnchor bindings of the enclosing resources and the
    referencing schemas stay in effect, so the outermost declaration wins.
    """
    if (resolved_ref := obj.__dict__.get(_MERGED_REF_TARGET_KEY)) is not None:
        if not parser._dynamic_anchors:  # noqa: SLF001
            return None
        document, _, pointer = parser._schema_location(resolved_ref).partition("#")  # noqa: SLF001
        pointer = unquote(pointer)
    elif (identifier := obj.id) and parser._embedded_resources:  # noqa: SLF001
        document = parser._current_dynamic_document()  # noqa: SLF001
        resource = dynamic_scope_resource(parser, document, parser._dynamic_scope_pointer)  # noqa: SLF001
        if (pointer := parser._embedded_resources.get((document, resource or ""), {}).get(identifier)) is None:  # noqa: SLF001
            return None
    else:
        return None
    return parser._dynamic_scope_context(parser._dynamic_scope_path, parser._current_dynamic_scope(), pointer, document)  # noqa: SLF001


def get_contains_bounds(parser: SchemaRefsHost, obj: JsonSchemaObject) -> JsonSchemaObject | None:
    """Return the item bounds that the contains keywords of a schema imply, as a schema of those bounds alone."""
    if not (keys := _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS & obj.extras.keys()):
        return None
    contains = parser._schema_object({key: obj.extras[key] for key in keys})  # noqa: SLF001
    return parser._schema_object(type(parser)._get_array_items_constraints(contains))  # noqa: SLF001


def ref_target_dump(parser: SchemaRefsHost, ref: str, resolved_ref: str) -> tuple[dict[str, Any], str]:
    """Return a fresh dump of a referenced schema for the current document, and the reference it ends at.

    A $recursiveRef inside a schema from another document is pinned to its target there (``_pin_recursive_refs``),
    and a referenced schema that is itself a $ref is merged with its own target. The dump depends only on the schema
    graph node the reference names and the current document, so each parse computes it once per pair and hands out
    copies. A chain of such aliases is loaded first and merged from its far end, so each merge finds the dump of the
    next alias computed.
    """
    document = parser._schema_resource_document(list(parser.model_resolver.current_root))  # noqa: SLF001
    key = node, _ = parser._key_node(resolved_ref), document  # noqa: SLF001
    chain: list[tuple[str, _TargetNode, dict[str, Any], JsonSchemaObject | None]] = []
    while (node, document) not in parser._ref_target_dumps:  # noqa: SLF001
        ref_dict = parser._load_ref_schema_object(ref).model_dump(exclude_unset=True, by_alias=True)  # noqa: SLF001
        if parser._schema_location(resolved_ref).partition("#")[0] != document:  # noqa: SLF001
            pin_recursive_refs(parser, ref_dict, resolved_ref)
        alias = parser._schema_object(ref_dict) if isinstance(ref_dict.get("$ref"), str) else None  # noqa: SLF001
        chain.append((resolved_ref, node, ref_dict, alias))
        if (
            alias is None
            or not alias.ref
            or parser._is_ref_circular(resolved_ref := parser._ref_path(ref := parser._lenient_ref(alias.ref)))  # noqa: SLF001
        ):
            break
        node = parser._key_node(resolved_ref)  # noqa: SLF001
    for chain_ref, chain_node, ref_dict, alias in reversed(chain):
        dump, target_ref = ref_dict, chain_ref
        if alias is not None:
            target = parser._merge_ref_with_schema(alias)  # noqa: SLF001
            dump = target.model_dump(exclude_unset=True, by_alias=True)
            target_ref = target.__dict__.get(_MERGED_REF_TARGET_KEY, chain_ref)
        parser._ref_target_dumps[chain_node, document] = dump, target_ref  # noqa: SLF001
    cached = parser._ref_target_dumps[key]  # noqa: SLF001
    return deepcopy(cached[0]), cached[1]


def intersect_ref_siblings(
    parser: SchemaRefsHost, target: dict[str, Any], siblings: dict[str, Any], where: str, *, siblings_are_parts: bool
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Merge the dump of a referenced schema with the sibling keywords of its $ref into their intersection.

    The earlier merge (``concatenating_row``: lists concatenated, a sibling's value winning) stays wherever it
    enforces what the intersection enforces; ``intersect_keywords`` replaces the keywords where it does not. A
    keyword whose parts have no value in common keeps the earlier merge's value, with a warning. Returns the merged
    keywords and the keywords with no value in common. Under ``legacy-merge-row`` the earlier merge alone.
    """
    intersection = _intersect_dumps(
        [target, siblings],
        concatenating_row(target, siblings),
        ref_siblings_are_parts=siblings_are_parts,
        legacy_rows=parser._legacy_intersection,  # noqa: SLF001
    )
    parser._warn_intersection(where, intersection)  # noqa: SLF001
    return intersection.keywords, intersection.unsatisfiable


def intersect_ref_siblings_schema(
    parser: SchemaRefsHost, target: dict[str, Any], siblings: dict[str, Any], where: str, *, siblings_are_parts: bool
) -> JsonSchemaObject:
    """Return the schema of the intersection of a referenced schema's dump and the sibling keywords of its $ref."""
    merged, unsatisfiable = intersect_ref_siblings(
        parser, target, siblings, where, siblings_are_parts=siblings_are_parts
    )
    merged.pop("$ref", None)
    merged_schema = parser._schema_object(merged)  # noqa: SLF001
    parser._mark_reported_value_conflict(merged_schema, unsatisfiable)  # noqa: SLF001
    return merged_schema


def mark_reported_value_conflict(
    parser: SchemaRefsHost,  # noqa: ARG001
    schema: JsonSchemaObject,
    keywords: Sequence[str],
) -> None:
    """Mark a merged schema whose ``enum`` or ``const`` conflict the intersection warning already reports."""
    if _VALUE_CONFLICT_KEYWORDS.intersection(keywords):
        schema.__dict__[_REPORTED_VALUE_CONFLICT_KEY] = True


def merge_ref_with_schema(parser: SchemaRefsHost, obj: JsonSchemaObject, where: str | None = None) -> JsonSchemaObject:
    """Merge $ref schema with current schema's additional keywords.

    JSON Schema 2020-12 allows $ref alongside other keywords, which apply together with the referenced schema:
    the merged schema is their intersection, keyword by keyword (``_intersect_ref_siblings``).

    A local contains does not replace the referenced one: both contains then apply, so the referenced contains
    keywords become the item bounds they imply and intersect with the local bounds. A referenced schema that is
    itself a $ref is merged with its own target, so aliases keep the definition they name. A warning for literals
    no value meets names ``where``, by default the location of ``obj``.
    """
    if not obj.ref:
        return obj

    resolved_ref = parser._ref_path(ref := parser._lenient_ref(obj.ref))  # noqa: SLF001
    if parser._is_ref_circular(resolved_ref):  # noqa: SLF001
        return obj

    ref_dict, resolved_ref = parser._ref_target_dump(ref, resolved_ref)  # noqa: SLF001
    current_dict = obj.model_dump(exclude={"ref"}, exclude_unset=True, by_alias=True)
    merged, unsatisfiable = intersect_ref_siblings(
        parser,
        ref_dict,
        current_dict,
        where or named_location(obj.schema_location, redact_reference(obj.ref), row="legacy-unredacted-locations"),
        siblings_are_parts=parser._ref_siblings_are_parts_of(obj),  # noqa: SLF001
    )
    merged.pop("$ref", None)
    if (
        "contains" in obj.extras
        and (contains_bounds := get_contains_bounds(parser, parser._schema_object(ref_dict))) is not None  # noqa: SLF001
    ):
        sibling_contains = _INHERITED_ARRAY_EXTRA_CONSTRAINT_FIELDS & obj.extras.keys()
        extras_key = JsonSchemaObject.__extra_key__
        merged[extras_key] = {
            **_without_contains_keywords(merged[extras_key]),
            **{key: obj.extras[key] for key in sibling_contains},
        }
        parser._merge_schema_constraints(merged, [contains_bounds], intersect=True)  # noqa: SLF001

    merged_obj = parser._schema_object(merged)  # noqa: SLF001
    parser._mark_reported_value_conflict(merged_obj, unsatisfiable)  # noqa: SLF001
    merged_obj.__dict__[_MERGED_REF_TARGET_KEY] = resolved_ref
    return _locate(merged_obj, obj.schema_location)


def recursive_ref_target(parser: SchemaRefsHost, defining_ref: str, path: tuple[str | int, ...]) -> str | None:
    """Return the location a $recursiveRef in a schema dumped from another document names there.

    It resolves as it does when that document is parsed, see _resolve_recursive_ref. The outermost
    $recursiveAnchor of the dynamic scope extends an anchored target, so the reference is left to it
    when the document the schema is merged into declares $recursiveAnchor.
    """
    file_part, _, pointer = defining_ref.partition("#")
    _, document = parser._prepared_ref_document(file_part)  # noqa: SLF001
    parts = [*split_json_pointer(document, pointer), *map(str, path)]
    if (target := _unanchored_resource_ref(document, parts)) is None:
        anchors = document_recursive_anchors(parser, document)
        current_ref = _pointer_ref(parts)
        target = _nearest_recursive_anchor(anchors, current_ref)
        if (target != "#" or "#" in anchors) and parser._declares_recursive_anchor():  # noqa: SLF001
            return None
    with parser._inherited_ref_context(defining_ref):  # noqa: SLF001
        location = parser._schema_location(parser.model_resolver.resolve_ref(target))  # noqa: SLF001
    return parser._derived_ref(location, SchemaLocation(file_part, tuple(parts)), "$recursiveRef", target)  # noqa: SLF001


def pin_recursive_refs(parser: SchemaRefsHost, schema: dict[str, Any], defining_ref: str) -> None:
    """Rewrite in place each $recursiveRef of a schema dumped from another document that has a fixed target there.

    It becomes a $ref to that target in the defining document. The other references keep their edges, which
    resolve them in their own document wherever the schema is read.
    """
    stack: list[tuple[dict[str, Any], tuple[str | int, ...]]] = [(schema, ())]
    while stack:
        node, path = stack.pop()
        match node:
            case {"$recursiveRef": "#"} if "$ref" not in node and (
                target := recursive_ref_target(parser, defining_ref, path)
            ):
                del node["$recursiveRef"]
                node["$ref"] = target
        stack.extend((child, (*path, *child_path)) for child_path, child in parser._iter_schema_resource_children(node))  # noqa: SLF001


def declares_recursive_anchor(parser: SchemaRefsHost) -> bool:
    """Return whether the document being parsed has registered a `$recursiveAnchor` so far."""
    return bool(parser._recursive_anchor_index.get(tuple(parser.model_resolver.current_root)))  # noqa: SLF001


def document_recursive_anchors(parser: SchemaRefsHost, raw: dict[str, Any]) -> list[str]:
    """Return the $recursiveAnchor references a document registers when parsed: its root and definitions."""
    anchors = ["#"] if raw.get("$recursiveAnchor") is True else []
    for schema_path, split_schema_path in parser.schema_paths:
        if definitions := cast("dict[str, YamlValue]", get_model_by_path(raw, split_schema_path)):
            entries = chain(
                ((str(key), model, [schema_path, str(key)]) for key, model in definitions.items()),
                parser._iter_schema_definition_entries(definitions, [schema_path]),  # noqa: SLF001
            )
            anchors.extend(
                parser._anchor_ref_path((), path)  # noqa: SLF001
                for _, model, path in entries
                if isinstance(model, dict) and model.get("$recursiveAnchor") is True
            )
            break
    return anchors


def is_ref_circular(parser: SchemaRefsHost, resolved_ref: str) -> bool:
    """Check if a resolved $ref target contains a circular reference (cached).

    A target whose references reach a document or pointer that cannot be loaded counts as circular.
    """
    if resolved_ref in parser._circular_ref_cache:  # noqa: SLF001
        return parser._circular_ref_cache[resolved_ref]  # noqa: SLF001
    try:
        result = has_ref_cycle(parser, resolved_ref)
    except (Error, NotImplementedError, OSError, ValueError):
        result = True
    parser._circular_ref_cache[resolved_ref] = result  # noqa: SLF001
    return result


def ref_cycle_document(parser: SchemaRefsHost, ref_to_check: str) -> Generator[object, None, None]:
    """Yield the value ref_to_check names, resolving references relative to its document until closed."""
    file_part, _, fragment = ref_to_check.partition("#")
    with parser._ref_file_context(file_part):  # noqa: SLF001
        raw_doc = ref_file_document(parser, file_part)
        yield get_model_by_path(raw_doc, [p for p in fragment.split("/") if p]) if fragment else raw_doc


def has_ref_cycle(parser: SchemaRefsHost, target: str) -> bool:
    """Check if the schema at target reaches a reference back to target.

    References are followed depth first, each one once, on an explicit stack: a frame per followed reference
    holds the resolution context of its document and the schemas left to walk there, so any chain length works.
    Reaching a boolean, number or null schema counts as a cycle, since it has no keywords to merge.
    """
    visited: set[str] = set()
    frames: list[tuple[Generator[object, None, None], list[dict[str, Any] | list[Any]]]] = []
    try:
        if push_ref_cycle_frame(parser, target, visited, frames):
            return True
        while frames:
            if not (nodes := frames[-1][1]):
                frames.pop()[0].close()
                continue
            if isinstance(node := nodes.pop(), list):
                nodes.extend(item for item in reversed(node) if isinstance(item, (dict, list)))
                continue
            nodes.extend(reversed(list(_iter_ref_walk_children(node))))
            if isinstance(ref_value := node.get("$ref"), str):
                try:
                    resolved = parser._ref_path(ref_value)  # noqa: SLF001
                except (Error, ValueError):
                    resolved = ref_value
                if resolved == target:
                    return True
                if resolved not in visited and push_ref_cycle_frame(parser, resolved, visited, frames):
                    return True
    finally:
        while frames:
            frames.pop()[0].close()
    return False


def push_ref_cycle_frame(
    parser: SchemaRefsHost,
    ref_to_check: str,
    visited: set[str],
    frames: list[tuple[Generator[object, None, None], list[dict[str, Any] | list[Any]]]],
) -> bool:
    """Enter the resolution context of ref_to_check and push a frame with the schema it names.

    Returns whether that schema is a boolean, number or null, which has no keywords to walk.
    """
    visited.add(ref_to_check)
    document = ref_cycle_document(parser, ref_to_check)
    raw_obj = next(document)
    frames.append((document, [raw_obj] if isinstance(raw_obj, (dict, list)) else []))
    return not isinstance(raw_obj, (dict, list, str))


def traverse_schema_objects(
    parser: SchemaRefsHost,
    obj: JsonSchemaObject,
    path: list[str],
    callback: Callable[[JsonSchemaObject, list[str]], None],
) -> None:
    """Traverse schema objects recursively and apply callback, within the schema resources they declare."""
    with nullcontext() if (scope := parser._subschema_scope(obj)) is None else scope:  # noqa: SLF001
        parser._walk_schema_objects(obj, path, callback)  # noqa: SLF001


def resolve_ref_callback(parser: SchemaRefsHost, obj: JsonSchemaObject, path: list[str]) -> None:
    """Resolve $ref in schema object, and the targets of $dynamicRef and $recursiveRef as their data types do."""
    if obj.ref:
        parser.resolve_ref(obj.ref)
        return
    if not obj.recursiveRef and (dynamic_ref := obj.dynamicRef) and not _is_plain_name_fragment(dynamic_ref):
        parser.resolve_ref(dynamic_ref)
    elif (target := dynamic_scope_target(parser, obj, path)) is not None and (
        obj.recursiveRef or target.location is not None
    ):
        with parser._dynamic_target_ref(target) as ref:  # noqa: SLF001
            parser.resolve_ref(ref)
