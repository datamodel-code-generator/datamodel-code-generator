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
from enum import Enum
from functools import cached_property
from operator import is_not
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, NewType, Protocol, TypeAlias, TypeVar, cast
from urllib.parse import unquote, urljoin

from typing_extensions import ReadOnly, Self, TypedDict, final, override

from datamodel_code_generator._records import record
from datamodel_code_generator.reference import SchemaLocation

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

__all__ = [
    "SCHEMA_GRAPH",
    "ConvertedRefs",
    "DocumentId",
    "DocumentResources",
    "RefEdge",
    "RefKey",
    "RefOrigin",
    "RefReading",
    "ReservedTarget",
    "ResolvedRef",
    "ResourceRegistry",
    "SchemaGraphBuilder",
    "SchemaGraphView",
    "SchemaNodeId",
    "canonical_ref_spelling",
    "keep_resolved_ref",
    "same_values",
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


class RefOrigin(Enum):
    """The code that produced a reference string an L1 site reads."""

    KEYWORD = "keyword"
    """A `$ref` or `$dynamicRef` keyword of a loaded document, also after the parser rewrote it to name its target."""
    DISCRIMINATOR_MAPPING = "discriminator mapping"
    """A value of a discriminator `mapping`, as a reference: a schema name becomes one under `#/components/schemas`."""
    DISCRIMINATOR_SUBTYPE = "discriminator subtype"
    """A component schema whose `allOf` names a schema with a discriminator, as a subtype of that schema."""
    DISCRIMINATOR_SCHEMA = "discriminator schema"
    """A schema that declares a discriminator, as the reference that names it from its own document, which keys it."""
    TYPE_DEFINITION = "x-python-type definition"
    """A name of an `x-python-type` expression, as the `$defs` entry of the current document that may import it."""
    RECURSIVE_REF = "$recursiveRef with a fixed target"
    """A `$recursiveRef` of a schema merged from another document, as the `$ref` to the schema it resolves to there,
    which the parser writes when the document it is merged into declares no `$recursiveAnchor`."""
    ITEM_SCHEMA = "itemSchema projection"
    """The items of the array the api scope projects a stream of `itemSchema` values to, as the item schema."""
    DYNAMIC_SCOPE = "dynamic scope"
    """A `$recursiveRef`, or a plain-name `$dynamicRef` bound in the dynamic scope of the model being parsed or named
    by the first `$dynamicAnchor` of its name in the document being parsed: it resolves against that dynamic scope,
    not its own document, so it has no edge."""
    UNATTRIBUTED = "unattributed"
    """A plain string that no producer above names."""


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
    origin: RefOrigin = RefOrigin.KEYWORD
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
    document_dynamic_anchors: Mapping[str, str]
    """The `$dynamicAnchor` names of the whole document to their locations; the first one in walk order wins."""
    recursive_anchors: tuple[str, ...]
    """The location of each schema that declares `$recursiveAnchor: true`, in walk order."""
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
    document_dynamic_anchors: dict[str, str] = {}
    recursive_anchors: list[str] = []
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
                    document_dynamic_anchors.setdefault(anchor, location)
        if schema.get("$recursiveAnchor") is True:
            recursive_anchors.append(location)
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
        document_dynamic_anchors=MappingProxyType(document_dynamic_anchors),
        recursive_anchors=tuple(recursive_anchors),
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
    anchor_tables: dict[str, tuple[Mapping[str, str], tuple[str, ...]]] = field(default_factory=dict)
    """The `$dynamicAnchor` names of each whole document and its `$recursiveAnchor` schemas (``DocumentResources``)."""
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
        self.anchor_tables.pop(document, None)

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
        self.anchor_tables[document] = resources.document_dynamic_anchors, resources.recursive_anchors
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
        "_ref_sites",
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
        self._ref_sites: dict[tuple[str, RefOrigin], list[int]] = {}
        self._schema_reads: dict[int, Any] = {}
        self._schemas: dict[tuple[int, type[Any]], tuple[Any, object]] = {}

    @property
    def holds_false_all_of(self) -> bool:
        """Return whether a document indexed so far holds a JSON object whose ``allOf`` lists ``false``.

        Only such a document gives a schema object an ``allOf`` member that is ``false``; the parser writes none.
        """
        return bool(self._false_all_of_documents)

    def count_ref(self, site: str, ref: str, plain_origin: RefOrigin) -> None:
        """Count a reference that reached a check ``site``, by its origin, with or without its edge.

        A plain string counts under ``plain_origin``, the producer the parser names for it.
        """
        origin = ref.edge.origin if isinstance(ref, ResolvedRef) else plain_origin
        self._ref_sites.setdefault((site, origin), [0, 0])[isinstance(ref, ResolvedRef)] += 1

    def ref_site_counts(self) -> Mapping[tuple[str, RefOrigin], tuple[int, int]]:
        """Return, by site and origin, how many references reached it as plain strings and how many with their edge."""
        return {
            key: (plain, carried)
            for key, (plain, carried) in sorted(
                self._ref_sites.items(), key=lambda item: (item[0][0], item[0][1].value)
            )
        }

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

    def schema(
        self, raw: object, kind: type[SchemaT], validate: Callable[[], SchemaT], *, fingerprint: bool
    ) -> SchemaT:
        """Return the ``kind`` object a JSON object of a loaded document validates to, which ``validate`` makes once.

        Every reader shares it, so none writes it. A value the graph does not locate is validated at every read.
        ``fingerprint`` records what the object holds, which ``changed_schemas`` compares at the end of L1.
        A ``validate`` that raises records nothing, so the next read raises again.
        """
        if not isinstance(raw, dict) or id(raw) not in self.locations:
            return validate()
        if (cached := self._schemas.get(key := (id(raw), kind))) is None:
            schema = validate()
            cached = self._schemas[key] = schema, _fingerprint(schema) if fingerprint else None
        return cached[0]

    def node(self, raw: object) -> SchemaNodeId | None:
        """Return the node a JSON object of a loaded document is, or None for a value the graph does not locate."""
        location = self.locations.get(id(raw)) if isinstance(raw, dict) else None
        return None if location is None else SchemaNodeId(location)

    def changed_schemas(self) -> Iterator[SchemaLocation]:
        """Yield the location of each shared object whose fingerprint differs from the one recorded at validation."""
        for (raw_id, _), (schema, recorded) in self._schemas.items():
            if recorded is not None and _fingerprint(schema) != recorded:
                yield self.locations[raw_id]

    def target(self, edge: RefEdge) -> SchemaLocation | None:
        """Return the registered location an edge names, by registry document name, if its target is registered."""
        if (base := self.registry.root_bases.get(edge.document)) is None or (
            location := self.registry.location(urljoin(base, edge.ref))
        ) is None:
            return None
        document, _, pointer = location.partition("#")
        return SchemaLocation(
            document, tuple(unquote(token).replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:])
        )

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


def same_values(left: object, right: object) -> bool:
    """Return whether two values of loaded documents hold the same data, their attributes and types included."""
    return _fingerprint(left) == _fingerprint(right)


def _fingerprint(value: object, active: frozenset[int] = frozenset()) -> object:
    """Return a comparable record of what an object holds, its attributes and those of the objects it holds included.

    The values ``functools.cached_property`` keeps in an object's ``__dict__`` are not part of it. A container that is
    its own ancestor is recorded by its type.
    """
    if (value_id := id(value)) in active:
        return type(value)
    active |= {value_id}
    match value:
        case dict():
            return dict, tuple((key, _fingerprint(item, active)) for key, item in value.items())
        case list() | tuple() | set() | frozenset():
            items = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
            return type(value), tuple(_fingerprint(item, active) for item in items)
    if not isinstance(attributes := getattr(value, "__dict__", None), dict):
        return type(value), value
    kind = type(value)
    return (
        kind,
        tuple(
            (name, _fingerprint(item, active))
            for name, item in attributes.items()
            if not isinstance(getattr(kind, name, None), cached_property)
        ),
        tuple(
            _fingerprint(getattr(value, name, None), active)
            for name in ("__pydantic_fields_set__", "__pydantic_extra__", "__pydantic_private__")
        ),
    )
