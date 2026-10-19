"""The schema graph (L0): the documents a parse loads, the location of each of their JSON objects, and their resources.

A document enters the graph when it loads, in the order L1 asks for it; the graph never loads one by itself.
``SchemaGraphBuilder.index_document`` makes the document a tree and indexes each of its JSON objects by identity in the
location table, which ``JsonSchemaObject`` reads through ``SCHEMA_GRAPH`` while ``Parser.parse`` runs.
``walk_schema_resources`` finds which absolute URI names which schema of the document (its root, every schema with an
`$id` or draft-04 `id`, the implicit component resources of OpenAPI, `$anchor` and `$dynamicAnchor`), and
``ResourceRegistry.register`` fills the registry dicts L1 resolves references through from that record.
"""

from __future__ import annotations

from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, NewType, Protocol, TypeAlias
from urllib.parse import urljoin

from typing_extensions import final

from datamodel_code_generator.reference import SchemaLocation

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "SCHEMA_GRAPH",
    "DocumentId",
    "DocumentResources",
    "ResourceRegistry",
    "SchemaGraphBuilder",
    "SchemaGraphView",
    "walk_schema_resources",
]

DocumentId = NewType("DocumentId", str)
"""A loaded document as the resource registry names it: an absolute POSIX path or a URL, empty for unnamed input."""

SchemaPath: TypeAlias = tuple[str | int, ...]
"""The keys and indexes from a schema to a schema-valued keyword below it."""


class ResourceChildren(Protocol):
    """The schema-valued keywords of a schema, by their path from it; examples, defaults and extensions are not."""

    def __call__(self, schema: dict[str, Any], /) -> Iterable[tuple[SchemaPath, dict[str, Any]]]:
        """Yield each schema-valued keyword below the schema with its path."""


class ResourceIdentifier(Protocol):
    """The identifier keyword in force below a schema (from its `$schema`), and the identifier the schema declares."""

    def __call__(self, schema: dict[str, Any], id_field: str | None, /, *, is_root: bool) -> tuple[str | None, object]:
        """Return the identifier keyword in force and the identifier the schema declares."""


@final
@dataclass(frozen=True, slots=True)
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

    def copy(self) -> ResourceRegistry:
        """Return a copy whose dicts and sets can change without changing this registry."""
        return ResourceRegistry(
            locations=dict(self.locations),
            root_bases=dict(self.root_bases),
            resources={key: set(value) for key, value in self.resources.items()},
            dynamic_anchors={key: dict(value) for key, value in self.dynamic_anchors.items()},
            embedded={key: dict(value) for key, value in self.embedded.items()},
            keys={key: set(value) for key, value in self.keys.items()},
        )


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

    __slots__ = ("_documents", "_mounted", "locations", "registry")

    def __init__(self) -> None:
        """Start an empty graph."""
        self.locations: dict[int, SchemaLocation] = {}
        self.registry = ResourceRegistry()
        self._documents: list[object] = []
        self._mounted: set[int] = set()

    def is_mounted(self, value: object) -> bool:
        """Return whether a converted schema was indexed at the location of the value it was converted from."""
        return id(value) in self._mounted

    def index_document(self, raw: Any, root: SchemaLocation) -> Any:
        """Return a loaded document as a tree and index each of its JSON objects by identity.

        An object that already has another location (a YAML alias, an object the caller shares, a document served
        under several names) is copied for this position, and its holders are copied on write up to the root, so
        neither the loader's nor the caller's objects are written and each object has one location. A container that
        is its own ancestor is a cycle: it is copied once, the back edge points to the copy and is not descended.
        Arrays are not indexed: an array shared between positions is copied when an object in it is.
        """
        document = root.document
        locations = self.locations
        ancestors: dict[int, Any] = {}

        def visit(value: dict[Any, Any] | list[Any], tokens: tuple[str, ...]) -> Any:
            if (ancestor := ancestors.get(value_id := id(value))) is not None:
                if ancestor is value:
                    ancestors[value_id] = ancestor = copy(value)
                return ancestor
            location: SchemaLocation | None = None
            if isinstance(value, dict):
                location = SchemaLocation(document, tokens)
                seen = locations.get(value_id)
                ancestors[value_id] = copy(value) if seen is not None and seen != location else value
            else:
                ancestors[value_id] = value
            for key, child in value.items() if isinstance(value, dict) else enumerate(value):
                if isinstance(child, (dict, list)) and (updated := visit(child, (*tokens, str(key)))) is not child:
                    if (node := ancestors[value_id]) is value:
                        ancestors[value_id] = node = copy(value)
                    node[key] = updated
            node = ancestors.pop(value_id)
            if location is not None:
                locations[id(node)] = location
            return node

        result = visit(raw, root.tokens)
        self._documents.append(result)
        return result

    def mount(self, converted: dict[str, Any], location: SchemaLocation) -> dict[str, Any]:
        """Index a schema converted from a value of a loaded document at the location of that value."""
        mounted = self.index_document(converted, location)
        self._mounted.add(id(mounted))
        return mounted
