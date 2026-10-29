"""The schema graph (L0): the documents a parse loads, the location of each of their JSON objects, and their resources.

A document enters the graph when it loads, in the order L1 asks for it; the graph never loads one by itself.
``SchemaGraphBuilder.index_document`` makes the document a tree and indexes each of its JSON objects by identity in the
location table, which ``JsonSchemaObject`` reads through ``SCHEMA_GRAPH`` while ``Parser.parse`` runs. The same walk
makes each `$ref` and `$dynamicRef` keyword of the document a ``ResolvedRef``, the string as written that also carries
its ``RefEdge`` (``SchemaGraphBuilder.with_ref_edges`` does it alone for a schema a reference names that the walk read
as data). ``walk_schema_resources`` finds which absolute URI names which schema of the document (its root, every schema
with an `$id` or draft-04 `id`, the implicit component resources of OpenAPI, `$anchor` and `$dynamicAnchor`), and
``ResourceRegistry.register`` fills the registry dicts L1 resolves references through from that record.
``SchemaGraphBuilder.edge_key`` keeps the ``RefKey`` each edge resolves to in its own document, and
``SchemaGraphBuilder.schema`` the object each JSON object of a loaded document validates to, which every reader of that
object shares. ``SchemaGraphBuilder.node`` names a JSON object of a loaded document as the node walks visit once
(``SchemaNodeId``), and a ``ReservedTarget`` the node a reference a document reserves names in it.
``SchemaGraphBuilder.mount_root`` gives a document a root of the graph's own, under which
``SchemaGraphBuilder.mount_in`` mounts the schemas the parser converts from values of the document, so the document
itself is never written.
"""

from __future__ import annotations

from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, field
from operator import is_not
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, NewType, Protocol, TypeAlias, TypeVar, cast
from urllib.parse import urljoin

from typing_extensions import ReadOnly, Self, TypedDict, final, override

from datamodel_code_generator._records import record
from datamodel_code_generator.reference import SchemaLocation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

__all__ = [
    "SCHEMA_GRAPH",
    "ConvertedRefs",
    "DocumentId",
    "DocumentResources",
    "RefEdge",
    "RefKey",
    "RefReading",
    "ReservedTarget",
    "ResolvedRef",
    "ResourceRegistry",
    "SchemaGraphBuilder",
    "SchemaGraphView",
    "SchemaNodeId",
    "canonical_ref_spelling",
    "keep_resolved_ref",
    "walk_schema_resources",
]

DocumentId = NewType("DocumentId", str)
"""A loaded document as the resource registry names it: an absolute POSIX path or a URL, empty for unnamed input."""

SchemaNodeId = NewType("SchemaNodeId", SchemaLocation)
"""A JSON object of a loaded document as a node of the graph: its location, which names one object per parse."""


class ReservedTarget(TypedDict):
    """A reference a source document reserves, to parse its target from the document after the models it holds."""

    document: ReadOnly[DocumentId]
    """The source document, whose prepared tree holds the target."""
    key: ReadOnly[str]
    """The reference key the parser reserves (``reserved_refs``), which names the model of the target."""
    node: ReadOnly[SchemaNodeId | None]
    """The node the key names in the prepared tree, or None when no JSON object of it is there."""


SchemaPath: TypeAlias = tuple[str | int, ...]
"""The keys and indexes from a schema to a schema-valued keyword below it."""

REF_KEYWORDS: Final = ("$ref", "$dynamicRef")
"""The keywords whose values become ``ResolvedRef``s."""

SchemaT = TypeVar("SchemaT")


@final
@record
class RefReading:
    """How ``SchemaGraphBuilder.with_ref_edges`` reads the objects of a document, each reading named by a string."""

    child: Callable[[Any, object], str | None]
    """Says how the value under a key of a JSON object read as an entry is read, or None when it holds no schemas."""
    objects: str
    """How a schema or document object is read: array items and the objects where keys are keywords, not names."""
    root: str
    """How the root is read."""


@final
@record
class RefEdge:
    """A reference of a loaded document: a `$ref` or `$dynamicRef` keyword, or a reference the parser derives.

    ``ref`` resolves against the base URI of the root of ``document``; for a keyword it is the value the prepared
    document holds.
    """

    source: SchemaLocation
    """The location of the object that holds the keyword."""
    keyword: str
    document: DocumentId
    ref: str
    spelling: str
    """The reference the parser resolves in ``document`` (``canonical_ref_spelling``): the value it reads there, which
    for a schema converted from another format is the path it keeps the schema at, not ``ref``."""
    home: str | None = None
    """The document, by reference-path name, that ``spelling`` resolves in when it is not the one holding ``source``:
    the parser keeps a schema converted from another format in a document of its own."""


@final
@record
class RefKey:
    """What a reference resolves to: the reference key of its target, or the Python package a mapping names."""

    key: str
    """The reference key of the target (``ModelResolver.resolve_ref``)."""
    spelling: str
    """The reference the key resolves from, after the parser normalized it; models are named after it."""
    mapping: tuple[str, str] | None = None
    """The package and fragment ``--external-ref-mapping`` maps the reference to; the key then only names it."""


@final
@record
class ConvertedRefs:
    """How the references of a schema converted from another format name their targets."""

    mounted: Callable[[str], str]
    """Gives the reference to the location a target is mounted at, which an edge carries for a reference."""
    home: str | None
    """The document, by reference-path name, the parser resolves the references in when it is not the one holding the
    schema's value: the parser keeps the schema in a document of its own."""


def canonical_ref_spelling(value: str) -> str:
    """Return a reference as the parser reads it: an empty pointer after a document (`x#/`) names its root (`x#`)."""
    return value[:-1] if "#" in value and value.endswith("#/") else value


@final
class ResolvedRef(str):  # noqa: FURB189 - it must be a str wherever the parser reads one
    """A `$ref` or `$dynamicRef` value of a loaded document, carrying its edge.

    It equals, hashes, prints and dumps as the string it holds; a deep copy is itself; it pickles as a plain ``str``.
    """

    __slots__ = ("edge",)
    edge: RefEdge

    def __new__(cls, value: str, edge: RefEdge) -> Self:
        """Return ``value`` carrying ``edge``."""
        resolved = super().__new__(cls, value)
        resolved.edge = edge
        return resolved

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        """Return this value: it is immutable."""
        return self

    @override
    def __reduce__(self) -> tuple[type[str], tuple[str]]:
        """Pickle as the plain string."""
        return str, (str(self),)


def keep_resolved_ref(value: object, result: str) -> str:
    """Return ``result``, the validated form of the reference keyword ``value``, with the edge of ``value``."""
    if not isinstance(value, ResolvedRef):
        return result
    return value if result == value else ResolvedRef(result, value.edge)


class ResourceChildren(Protocol):
    """The schema-valued keywords of a schema, by their path from it; examples, defaults and extensions are not."""

    def __call__(self, schema: dict[str, Any], /) -> Iterable[tuple[SchemaPath, dict[str, Any]]]:
        """Yield each schema-valued keyword below the schema with its path."""


class ResourceIdentifier(Protocol):
    """The identifier keyword in force below a schema (from its `$schema`), and the identifier the schema declares."""

    def __call__(self, schema: dict[str, Any], id_field: str | None, /, *, is_root: bool) -> tuple[str | None, object]:
        """Return the identifier keyword in force and the identifier the schema declares."""


@final
@record
class DocumentResources:
    """The schema resources of one loaded document and the absolute URIs that name its schemas.

    A location is ``document#pointer``, the form the registry keeps.
    """

    document: DocumentId
    root_base: str
    """The base URI of the document root, after its identifier."""
    names: Mapping[str, str]
    """Each URI an identifier, `$anchor` or `$dynamicAnchor` declares, and the root base, to the location it names.

    A later declaration of a URI replaces an earlier one.
    """
    defaults: frozenset[str]
    """The URIs that only the root base names; a document registered earlier keeps such a URI."""
    resources: frozenset[str]
    """The pointer of each resource root: the document root, each schema below it that declares an identifier other
    than a fragment, and each implicit resource."""
    embedded: Mapping[str, Mapping[str, str]]
    """The identifiers, as written, of the resources each resource embeds, to their pointers; the first one wins."""
    dynamic_anchors: Mapping[str, Mapping[str, str]]
    """The `$dynamicAnchor` names of each resource to their locations; the first one wins."""
    implicit_resources: bool
    """Whether the document evaluates schemas as resources without an identifier, as OpenAPI component schemas."""
    embeds_resources: bool
    """Whether a schema below the root, or an implicit resource, declares an identifier other than a fragment."""


def _pointer(path: SchemaPath) -> str:
    """Return the RFC 6901 pointer of a path below a schema."""
    return "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in path)


def walk_schema_resources(  # noqa: PLR0913
    raw: dict[str, Any],
    implicit: Sequence[tuple[SchemaPath, dict[str, Any]]],
    *,
    document: DocumentId,
    base: str,
    id_field: str | None,
    children: ResourceChildren,
    identifier: ResourceIdentifier,
) -> DocumentResources:
    """Find the schema resources of a document and the URIs that name its schemas, root first, in document order.

    ``base`` is the URI of the document itself and ``id_field`` the identifier keyword a configured schema version
    fixes (None to read it from `$schema`). The walk descends into the keywords ``children`` yields and then visits
    each ``implicit`` resource with the document's base and ``id_field``. An identifier other than a fragment, below
    the root, declares a resource whose base URI applies below it.
    """
    names: dict[str, str] = {}
    declared: set[str] = set()
    resources: set[str] = set()
    embedded: dict[str, dict[str, str]] = {}
    dynamic_anchors: dict[str, dict[str, str]] = {}
    root_base = base

    def name(uri: str, location: str) -> None:
        names[uri] = location
        declared.add(uri)

    def visit(schema: dict[str, Any], pointer: str, base: str, id_field: str | None, resource: str) -> bool:
        nonlocal root_base
        id_field, identifier_value = identifier(schema, id_field, is_root=not pointer)
        location = f"{document}#{pointer}"
        embeds = False
        if isinstance(identifier_value, str):
            name(absolute := urljoin(base, identifier_value), location)
            base = absolute.split("#", 1)[0]
            if embeds := bool(pointer) and not identifier_value.startswith("#"):
                embedded.setdefault(resource, {}).setdefault(identifier_value, pointer)
        if not pointer:
            root_base = base
            names.setdefault(base, location)
        if embeds or not pointer:
            resources.add(resource := pointer)
        for keyword in ("$anchor", "$dynamicAnchor"):
            if isinstance(anchor := schema.get(keyword), str):
                name(urljoin(base, f"#{anchor}"), location)
                if keyword == "$dynamicAnchor":
                    dynamic_anchors.setdefault(resource, {}).setdefault(anchor, location)
        for path, child in children(schema):
            embeds |= visit(child, pointer + _pointer(path), base, id_field, resource)
        return embeds

    embeds_resources = visit(raw, "", base, id_field, "")
    for path, schema in implicit:
        resources.add(pointer := _pointer(path))
        embeds_resources |= visit(schema, pointer, base, id_field, pointer)
    return DocumentResources(
        document=document,
        root_base=root_base,
        names=MappingProxyType(names),
        defaults=frozenset(names.keys() - declared),
        resources=frozenset(resources),
        embedded=MappingProxyType({key: MappingProxyType(value) for key, value in embedded.items()}),
        dynamic_anchors=MappingProxyType({key: MappingProxyType(value) for key, value in dynamic_anchors.items()}),
        implicit_resources=bool(implicit),
        embeds_resources=embeds_resources,
    )


@final
@dataclass(slots=True)
class ResourceRegistry:
    """The registry dicts L1 resolves references through, keyed by document; filled from the schema graph."""

    locations: dict[str, str] = field(default_factory=dict)
    """Absolute URI to the location it names, over every registered document."""
    root_bases: dict[str, str] = field(default_factory=dict)
    resources: dict[str, set[str]] = field(default_factory=dict)
    dynamic_anchors: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    embedded: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    keys: dict[str, set[str]] = field(default_factory=dict)
    """The URIs each document registered, which a later registration of the document drops while they name it."""

    def forget(self, document: str) -> None:
        """Drop what an earlier registration of a document left, before it is registered again."""
        for key in self.keys.get(document, ()):
            if self.locations.get(key, "").split("#", 1)[0] == document:
                self.locations.pop(key, None)
        for resource in self.resources.pop(document, ()):
            self.dynamic_anchors.pop((document, resource), None)
            self.embedded.pop((document, resource), None)

    def register(self, resources: DocumentResources) -> None:
        """Register the resources of a document, as the walk over the document registered them before the graph."""
        document = resources.document
        for uri, location in resources.names.items():
            if uri in resources.defaults:
                self.locations.setdefault(uri, location)
            else:
                self.locations[uri] = location
        self.root_bases[document] = resources.root_base
        self.resources[document] = set(resources.resources)
        for resource, anchors in resources.dynamic_anchors.items():
            self.dynamic_anchors[document, resource] = dict(anchors)
        for resource, identifiers in resources.embedded.items():
            self.embedded[document, resource] = dict(identifiers)
        self.keys[document] = set(resources.names)

    def location(self, absolute: str) -> str | None:
        """Return the location an absolute reference names in a registered schema resource, if any."""
        if (location := self.locations.get(absolute)) is not None:
            return location
        resource, _, fragment = absolute.partition("#")
        if (not fragment or fragment.startswith("/")) and (root := self.locations.get(resource)):
            return f"{root}{fragment}" if fragment else root
        return None


class SchemaGraphView(Protocol):
    """What L1 reads of the schema graph."""

    @property
    def locations(self) -> Mapping[int, SchemaLocation]:
        """The location of every JSON object of the loaded documents, by identity."""


SCHEMA_GRAPH: Final[ContextVar[SchemaGraphView | None]] = ContextVar("schema_graph", default=None)
"""The schema graph of the parse that runs, which ``Parser.parse`` sets and resets around the whole run."""


@final
class SchemaGraphBuilder:
    """The schema graph of one parse, built as its documents load (L0); the parser that owns it is its only writer.

    The graph keeps the documents alive, so an identity in the location table stays valid for the whole parse.
    """

    __slots__ = (
        "_directories",
        "_documents",
        "_edge_keys",
        "_false_all_of_documents",
        "_mount_containers",
        "_mount_roots",
        "_mounted",
        "_schema_reads",
        "_schemas",
        "locations",
        "registry",
    )

    def __init__(self) -> None:
        """Start an empty graph."""
        self.locations: dict[int, SchemaLocation] = {}
        self.registry = ResourceRegistry()
        self._documents: list[object] = []
        self._false_all_of_documents: set[str] = set()
        self._mounted: set[int] = set()
        self._mount_roots: set[int] = set()
        self._mount_containers: dict[int, Any] = {}
        self._edge_keys: dict[RefEdge, RefKey] = {}
        self._directories: dict[str, Path] = {}
        self._schema_reads: dict[int, Any] = {}
        self._schemas: dict[tuple[int, type[Any]], Any] = {}

    @property
    def holds_false_all_of(self) -> bool:
        """Return whether a document indexed so far holds a JSON object whose ``allOf`` lists ``false``.

        Only such a document gives a schema object an ``allOf`` member that is ``false``; the parser writes none.
        """
        return bool(self._false_all_of_documents)

    def edge_key(self, edge: RefEdge, compute: Callable[[RefEdge], RefKey]) -> RefKey:
        """Return the key of an edge, which ``compute`` resolves in the edge's document once, at its first call.

        A ``compute`` that raises records nothing, so the next call raises again.
        """
        if (key := self._edge_keys.get(edge)) is None:
            key = self._edge_keys[edge] = compute(edge)
        return key

    def document_directory(self, document: str, resolve: Callable[[], Path]) -> Path:
        """Return the directory of a local document, which ``resolve`` reads from the file system at the first call.

        ``document`` names the document as reference paths do. The directory is a fact of the document as it loads.
        """
        if (directory := self._directories.get(document)) is None:
            directory = self._directories[document] = resolve()
        return directory

    def schema(self, raw: object, kind: type[SchemaT], validate: Callable[[], SchemaT]) -> SchemaT:
        """Return the ``kind`` object a JSON object of a loaded document validates to, which ``validate`` makes once.

        Every reader shares it, so none writes it. A value the graph does not locate is validated at every read.
        A ``validate`` that raises records nothing, so the next read raises again.
        """
        if not isinstance(raw, dict) or id(raw) not in self.locations:
            return validate()
        if (schema := self._schemas.get(key := (id(raw), kind))) is None:
            schema = self._schemas[key] = validate()
        return schema

    def node(self, raw: object) -> SchemaNodeId | None:
        """Return the node a JSON object of a loaded document is, or None for a value the graph does not locate."""
        location = self.locations.get(id(raw)) if isinstance(raw, dict) else None
        return None if location is None else SchemaNodeId(location)

    @staticmethod
    def with_ref_edges(
        raw: Any,
        root: SchemaLocation,
        document: DocumentId,
        reading: RefReading,
        converted: ConvertedRefs | None = None,
    ) -> Any:
        """Return a loaded document whose `$ref` and `$dynamicRef` keywords are ``ResolvedRef``s, copied on write.

        ``reading`` says how the root, array items and the value under each key are read, and which of these hold
        keywords rather than names; instance data and extensions hold no schemas. ``converted`` says how the
        references of a schema converted from another format name their targets; the value itself is the reference an
        edge carries otherwise.
        A container that is its own ancestor is not descended again. A value that is already a ``ResolvedRef`` stays.
        A copied container keeps its type and attributes, as ``index_document`` copies them.
        """
        active: set[int] = set()
        mounted, home = (converted.mounted, converted.home) if converted is not None else (str, None)

        def visit(value: Any, tokens: tuple[str, ...], entries: str) -> Any:
            if not isinstance(value, (dict, list)) or (value_id := id(value)) in active:
                return value
            active.add(value_id)
            if isinstance(value, list):
                items: list[Any] = []
                for index, item in enumerate(value):
                    items.append(visit(item, (*tokens, str(index)), reading.objects))
                active.discard(value_id)
                if not any(map(is_not, items, value)):
                    return value
                copied = copy(value)
                copied[:] = items
                return copied
            changed: dict[str, Any] = {}
            for key, child in value.items():
                if (child_kind := reading.child(entries, key)) is not None and (
                    updated := visit(child, (*tokens, str(key)), child_kind)
                ) is not child:
                    changed[key] = updated
            if entries in {reading.objects, reading.root}:
                source = SchemaLocation(root.document, tokens)
                for keyword in REF_KEYWORDS:
                    if type(ref := changed.get(keyword, value.get(keyword))) is str:
                        changed[keyword] = ResolvedRef(
                            ref,
                            RefEdge(source, keyword, document, mounted(ref), canonical_ref_spelling(ref), home=home),
                        )
            active.discard(value_id)
            if not changed:
                return value
            copied = copy(value)
            copied.update(changed)
            return copied

        return visit(raw, root.tokens, reading.root)

    def read_as_schema(
        self, raw: dict[str, Any], location: SchemaLocation, document: DocumentId, reading: RefReading
    ) -> dict[str, Any]:
        """Return the JSON object at ``location`` of a loaded document that a reference names, read as a schema.

        Its `$ref` and `$dynamicRef` keywords, and those of the schemas below it, are ``ResolvedRef``s also where the
        walk of the whole document read it as names or data, as a key of a nested definitions container; ``reading``
        reads the object itself as a schema. A changed object is copied once, at its location.
        """
        if (read := self._schema_reads.get(id(raw))) is None:
            edged = self.with_ref_edges(raw, location, document, reading)
            read = self._schema_reads[id(raw)] = raw if edged is raw else self.mount(edged, location)
        return read

    def is_mounted(self, value: object) -> bool:
        """Return whether a converted schema was indexed at the location of the value it was converted from."""
        return id(value) in self._mounted

    def index_document(
        self,
        raw: Any,
        root: SchemaLocation,
        document: DocumentId | None = None,
        reading: RefReading | None = None,
        converted: ConvertedRefs | None = None,
    ) -> Any:
        """Return a loaded document as a tree and index each of its JSON objects by identity.

        An object that already has another location (a YAML alias, an object the caller shares, a document served
        under several names) is copied for this position, and its holders are copied on write up to the root, so
        neither the loader's nor the caller's objects are written and each object has one location. A container that
        is its own ancestor is a cycle: it is copied once, the back edge points to the copy and is not descended.
        Arrays are not indexed: an array shared between positions is copied when an object in it is. A document with
        a JSON object whose ``allOf`` lists ``false`` is recorded for ``holds_false_all_of``.

        With ``reading``, the same walk makes the `$ref` and `$dynamicRef` keywords of the schemas it reads
        ``ResolvedRef``s of ``document``, as ``with_ref_edges`` reads them, and copies their objects on write.
        """
        locations = self.locations
        false_all_of = self._false_all_of_documents
        ancestors: dict[int, Any] = {}
        mounted, home = (converted.mounted, converted.home) if converted is not None else (str, None)
        reads_refs = set() if reading is None else {reading.objects, reading.root}

        def write(value: dict[Any, Any] | list[Any], value_id: int, key: Any, updated: Any) -> None:
            if (node := ancestors[value_id]) is value:
                ancestors[value_id] = node = copy(value)
            node[key] = updated

        def visit(value: dict[Any, Any] | list[Any], tokens: tuple[str, ...], entries: str | None) -> Any:
            if (ancestor := ancestors.get(value_id := id(value))) is not None:
                if ancestor is value:
                    ancestors[value_id] = ancestor = copy(value)
                return ancestor
            location: SchemaLocation | None = None
            children: Iterable[tuple[Any, Any]]
            if isinstance(value, dict):
                location = SchemaLocation(root.document, tokens)
                seen = locations.get(value_id)
                ancestors[value_id] = copy(value) if seen is not None and seen != location else value
                if isinstance(all_of := value.get("allOf"), (list, tuple)) and any(item is False for item in all_of):
                    false_all_of.add(root.document)
                children = value.items()
            else:
                ancestors[value_id] = value
                children = enumerate(value)
            for key, child in children:
                if isinstance(child, (dict, list)):
                    kind = (
                        None
                        if reading is None or entries is None
                        else reading.child(entries, key)
                        if location is not None
                        else reading.objects
                    )
                    if (updated := visit(child, (*tokens, str(key)), kind)) is not child:
                        write(value, value_id, key, updated)
            if location is None:
                return ancestors.pop(value_id)
            if entries in reads_refs:
                for keyword in REF_KEYWORDS:
                    if type(ref := ancestors[value_id].get(keyword)) is str:
                        edge = RefEdge(
                            location,
                            keyword,
                            cast("DocumentId", document),
                            mounted(ref),
                            canonical_ref_spelling(ref),
                            home=home,
                        )
                        write(value, value_id, keyword, ResolvedRef(ref, edge))
            node = ancestors.pop(value_id)
            locations[id(node)] = location
            return node

        result = visit(raw, root.tokens, None if reading is None else reading.root)
        self._documents.append(result)
        return result

    def mount(
        self,
        converted: dict[str, Any],
        location: SchemaLocation,
        document: DocumentId | None = None,
        reading: RefReading | None = None,
        refs: ConvertedRefs | None = None,
    ) -> dict[str, Any]:
        """Index a schema converted from a value of a loaded document at the location of that value.

        With ``reading``, its references become ``ResolvedRef``s in the same walk (``index_document``).
        """
        mounted = self.index_document(converted, location, document, reading, refs)
        self._mounted.add(id(mounted))
        return mounted

    def mount_root(self, document: dict[str, Any]) -> dict[str, Any]:
        """Return the root that ``mount_in`` mounts converted schemas under for a loaded document.

        It is a copy of the document's root, located as that root, which the graph owns; a converted schema and a root
        the graph already owns are their own.
        """
        if id(document) in self._mounted or id(document) in self._mount_roots:
            return document
        root = copy(document)
        self.locations[id(root)] = self.locations[id(document)]
        self._documents.append(root)
        self._mount_roots.add(id(root))
        return root

    def mount_in(self, root: dict[str, Any], container: str, key: str, converted: dict[str, Any]) -> None:
        """Mount a converted schema at ``key`` of ``container`` under a root from ``mount_root``.

        The first mount copies the container the document holds, or starts an empty one, so the document is not
        written; a container that is not a JSON object cannot hold the schema.
        """
        if (mounts := self._mount_containers.get(id(root))) is None:
            mounts = self._mount_containers[id(root)] = root[container] = copy(root.get(container, {}))
        mounts[key] = converted
