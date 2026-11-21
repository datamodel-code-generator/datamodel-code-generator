"""Write the part of the source OpenAPI document the selected operations use, which the generated server serves.

The document keeps the source's own spelling: its OpenAPI version, info, servers, tags, security schemes, webhooks,
and the selected paths with their callbacks. Components stay when something kept references them, or when they are
subtypes of a kept discriminator base. What a reference names in another loaded document, or in a path that is left
out, joins the components of the kind its position holds, so the served document stands alone; a kind the version
has no component for is copied in place. It is documentation: the generated models stay the contract the server
validates with.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final, TypeAlias, cast
from urllib.parse import urljoin, urlsplit

from datamodel_code_generator._document_reader import MISSING, as_mapping, descend, discriminator_mapping_ref
from datamodel_code_generator._free_names import first_free
from datamodel_code_generator._json_pointer import join_pointer, pointer_tokens, reference_tokens, uri_entry
from datamodel_code_generator._json_schema import INSTANCE_DATA_KEYWORDS, NAMED_SCHEMA_MAPS
from datamodel_code_generator._openapi_objects import COMPONENT_KINDS, COMPONENT_ROLES, OBJECT_MEMBERS
from datamodel_code_generator._paths import file_uri
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.enums import OpenAPIVersion

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator._openapi_generation import SourceLease
    from datamodel_code_generator._openapi_objects import Member
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_contract import OperationContract

JSON: TypeAlias = "bool | int | float | str | list[JSON] | dict[str, JSON] | None"
Key: TypeAlias = "tuple[int, tuple[str, ...]]"

_SINCE: Final = {
    "pathItems": frozenset({OpenAPIVersion.V31, OpenAPIVersion.V32}),
    "mediaTypes": frozenset({OpenAPIVersion.V32}),
}
_KEPT: Final = "securitySchemes"
_NAME: Final = re.compile(r"[^A-Za-z0-9._-]")
_URL: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


class SourceDocument:
    """Copy the selected operations' part of the loaded documents into one JSON document."""

    def __init__(self, lease: SourceLease, version: OpenAPIVersion) -> None:
        """Name the loaded documents, and what their schemas declare, by the URIs the parser's schema resources name.

        A document is named by the location it was read from and by its `$self`, each standing for the base the parser
        gives its root (``SourceLease.roots``) when that base names the document itself: two documents that declare
        one root `$id` keep their own locations, as the parser reads a reference by location. A schema's identifier,
        `$anchor` and `$dynamicAnchor` name what the parser's schema resources name (``SourceLease.declared``).
        `version` is the OpenAPI version the parser reads the root document by.
        """
        held = lease.documents()
        self.uris = [_uri(location) for location, _ in held]
        self.documents = [document for _, document in held]
        root = self.documents[0]
        self.version = version
        self.bases = [
            urljoin(uri, own) if isinstance(own := document.get("$self"), str) else uri
            for uri, document in zip(self.uris, self.documents, strict=True)
        ]
        self.declared = dict(lease.declared())
        self.aliases: dict[str, str] = {}
        self.roots = lease.roots()
        named = zip(self.uris, self.bases, self.roots, strict=True)
        for index, (*names, given) in enumerate(named):
            owner = self.declared.get(given) if given else None
            own = owner is not None and self.uris[owner[0]] == self.uris[index]
            for name in names:
                self.declared.setdefault(name, (index, ""))
                if own and name != given:
                    self.aliases.setdefault(name, given)
        self.schemas = as_mapping(as_mapping(root.get("components")).get("schemas"))
        self.problems: dict[str, None] = {}
        self.wanted: dict[tuple[str, str], None] = {}
        self.bundled: dict[Key, str] = {}
        self.extra: dict[str, dict[str, JSON]] = {}
        self.stack: set[int] = set()
        self.selected: frozenset[str] = frozenset()

    def resolve(self, document: int, base: str, ref: str) -> tuple[int, tuple[str, ...], str] | None:
        """Return the document, pointer tokens, and resource base of what a reference names, or None.

        The reference resolves against its schema resource's base, then, as the parser does for compatibility, against
        where its document was read: a pointer that names nothing in the resource is the document's. The resource base
        is the one the parser gives the resource, so an identifier the copy meets below it joins as the parser joins it.
        """
        for start in (base, self.uris[document]):
            resource, mark, fragment = urljoin(start, ref).partition("#")
            resource = self.aliases.get(resource, resource)
            if (found := uri_entry(self.declared, f"{resource}{mark}{fragment}")) is None:
                continue
            (target, pointer), below = found
            tokens = (*pointer_tokens(pointer), *(reference_tokens(below) or ()))
            if self.exists(target, tokens):
                return target, tokens, resource
        return None

    def exists(self, document: int, tokens: tuple[str, ...]) -> bool:
        """Return whether a pointer names a node of a loaded document."""
        return descend(self.documents[document], tokens, "canonical", MISSING) is not MISSING

    def text(self, operations: Iterable[OperationContract]) -> str:
        """Return the document of the operations as indented JSON text."""
        return json.dumps(self.build(operations), ensure_ascii=True, indent=1)

    def build(self, operations: Iterable[OperationContract]) -> dict[str, JSON]:
        """Return the document of the operations: the root's members, the paths they use, and what those reference.

        Selection keeps or leaves out a whole path, so a kept path item keeps every operation it declares.
        """
        self.selected = frozenset(operation.path for operation in operations)
        root = self.documents[0]
        base = self.bases[0]
        document: dict[str, JSON] = {}
        for key, value in root.items():
            name = str(key)
            if name == "paths":
                document[name] = {
                    str(path): self.copy(item, 0, base, ("paths", str(path)), "pathItem")
                    for path, item in as_mapping(value).items()
                    if str(path) in self.selected
                }
            elif name == "webhooks":
                document[name] = self.members(value, 0, base, (name,), ("map", "pathItem"))
            elif name != "components" and self.json(value, (name,)):
                document[name] = self.copy(value, 0, base, (name,), "any")
        if components := self.components(as_mapping(root.get("components"))):
            document["components"] = components
        return document

    def components(self, source: Mapping[str, YamlValue]) -> dict[str, JSON]:
        """Return the components something kept references, in source order, then those bundled from elsewhere."""
        base = self.bases[0]
        copied: dict[tuple[str, str], JSON] = {}
        while todo := [
            (str(kind), str(name), value)
            for wanted in (self.subtyped(),)
            for kind, items in source.items()
            for name, value in as_mapping(items).items()
            if (str(kind), str(name)) in wanted and (str(kind), str(name)) not in copied
        ]:
            for kind, name, value in todo:
                copied[kind, name] = self.copy(
                    value, 0, base, ("components", kind, name), COMPONENT_ROLES.get(kind, "any")
                )
        components: dict[str, JSON] = {}
        for key, value in source.items():
            kind = str(key)
            if kind == _KEPT:
                components[kind] = self.members(value, 0, base, ("components", kind), ("map", "securityScheme"))
            elif found := {
                str(name): copied[kind, str(name)] for name in as_mapping(value) if (kind, str(name)) in copied
            }:
                components[kind] = found
        for kind, items in self.extra.items():
            target = components.setdefault(kind, {})
            assert isinstance(target, dict)
            target.update(items)
        return components

    def subtyped(self) -> dict[tuple[str, str], None]:
        """Return the wanted components, with the schemas whose allOf extends a wanted discriminator base."""
        bases = {
            "#" + join_pointer(("components", "schemas", name))
            for kind, name in self.wanted
            if kind == "schemas" and isinstance(as_mapping(self.schemas.get(name)).get("discriminator"), dict)
        }
        for name, schema in self.schemas.items():
            parts = as_mapping(schema).get("allOf")
            if isinstance(parts, list) and any(as_mapping(part).get("$ref") in bases for part in parts):
                self.wanted.setdefault(("schemas", str(name)), None)
        return self.wanted

    def copy(self, value: YamlValue, document: int, base: str, at: tuple[str, ...], role: str) -> JSON:
        """Return a JSON copy of an object of one role, its references rewritten into the document.

        A value without a JSON form is left out. A reference to a kind the version has no component for is copied
        in place.
        """
        if not isinstance(value, dict | list):
            assert value is None or isinstance(value, str | int | float)
            return value
        self.stack.add(id(value))
        try:
            if isinstance(value, list):
                return [
                    self.copy(item, document, base, (*at, str(index)), role)
                    for index, item in enumerate(value)
                    if self.json(item, (*at, str(index)))
                ]
            if (inline := self.inline(value, document, base, role)) is not None:
                return self.copy(inline[2], inline[0], inline[1], at, role)
            return self.mapping(value, document, base, at, role)
        finally:
            self.stack.discard(id(value))

    def mapping(
        self, value: dict[str, YamlValue], document: int, base: str, at: tuple[str, ...], role: str
    ) -> dict[str, JSON]:
        """Return a JSON copy of a mapping of one role.

        A schema's `$id` only sets the base its references resolve against, which the copy has applied to them, so the
        copy leaves it out: every reference in the document is relative to the document itself. The `$id` of a
        document's root sets the base the parser gives that root, however the copy reached it, so it applies once.
        """
        if role == "schema" and isinstance(own := value.get("$id"), str) and self.version != OpenAPIVersion.V30:
            root = self.roots[document] if value is self.documents[document] else ""
            base = root or urljoin(base, own)
        children = OBJECT_MEMBERS.get(role, {})
        copied: dict[str, JSON] = {}
        for key, child in value.items():
            name, place = str(key), (*at, str(key))
            if not self.json(child, place) or (role == "schema" and name == "$id" and isinstance(child, str)):
                continue
            if name == "$ref" and isinstance(child, str) and role in COMPONENT_KINDS:
                copied[name] = self.reference(child, document, base, role, place)
            elif role == "schema":
                copied[name] = self.schema_member(name, child, document, base, place)
            elif role == "callback":
                copied[name] = self.copy(child, document, base, place, "pathItem")
            elif (shape := children.get(name)) is not None:
                copied[name] = self.members(child, document, base, place, shape)
            else:
                copied[name] = self.copy(child, document, base, place, "any")
        return copied

    def schema_member(self, name: str, value: YamlValue, document: int, base: str, at: tuple[str, ...]) -> JSON:
        """Return one member of a schema: a subschema, a map of them, a discriminator, or literal data."""
        if name == "discriminator" and isinstance(value, dict):
            return self.discriminator(value, document, base, at)
        if name in NAMED_SCHEMA_MAPS and isinstance(value, dict):
            return {
                str(key): self.copy(item, document, base, (*at, str(key)), "schema")
                for key, item in value.items()
                if self.json(item, (*at, str(key)))
            }
        return self.copy(
            value, document, base, at, "any" if name in INSTANCE_DATA_KEYWORDS or name.startswith("x-") else "schema"
        )

    def members(self, value: YamlValue, document: int, base: str, at: tuple[str, ...], shape: Member) -> JSON:
        """Return one object, or a list or map of objects, of a role, leaving out links to operations not served."""
        form, role = shape
        if form != "map" or not isinstance(value, dict):
            return self.copy(value, document, base, at, role)
        return {
            str(key): self.copy(child, document, base, (*at, str(key)), role)
            for key, child in value.items()
            if self.json(child, (*at, str(key))) and (role != "link" or self.served(child, (*at, str(key))))
        }

    def served(self, link: YamlValue, at: tuple[str, ...]) -> bool:
        """Return whether a link's operationRef names an operation the document keeps, reporting one it leaves out."""
        ref = as_mapping(link).get("operationRef")
        if not isinstance(ref, str) or not ref.startswith("#/paths/"):
            return True
        if cast("tuple[str, ...]", reference_tokens(ref[1:]))[1] in self.selected:
            return True
        problem = f"{join_pointer(at)}: The served document leaves out the link, whose operation it leaves out"
        self.problems[problem] = None
        return False

    def discriminator(self, value: dict[str, YamlValue], document: int, base: str, at: tuple[str, ...]) -> JSON:
        """Return a discriminator, keeping the schema each mapping value names, by reference or by component name."""
        copied = self.mapping({key: item for key, item in value.items() if key != "mapping"}, document, base, at, "any")
        if isinstance(mapping := value.get("mapping"), dict):
            copied["mapping"] = {
                str(key): self.discriminator_target(item, document, base, (*at, "mapping", str(key)))
                for key, item in mapping.items()
                if isinstance(item, str)
            }
        return copied

    def discriminator_target(self, value: str, document: int, base: str, at: tuple[str, ...]) -> str:
        """Return a mapping value as the served document spells it: a reference rewritten, a name as its component."""
        if (ref := discriminator_mapping_ref(value)) == value:
            return self.reference(value, document, base, "schema", at)
        return self.reference(ref, document, self.uris[document], "schema", at).rpartition("/")[2]

    def json(self, value: YamlValue, at: tuple[str, ...]) -> bool:
        """Return whether a value has a JSON form, reporting one the served document leaves out.

        A mapping or list that contains itself, through YAML aliases, has none.
        """
        if (
            (isinstance(value, dict | list) and id(value) not in self.stack)
            or isinstance(value, str | int | type(None))
            or (isinstance(value, float) and math.isfinite(value))
        ):
            return True
        self.problems[f"{join_pointer(at)}: The served document leaves out {at[-1]}, which has no JSON form"] = None
        return False

    def inline(
        self, value: dict[str, YamlValue], document: int, base: str, role: str
    ) -> tuple[int, str, dict[str, YamlValue]] | None:
        """Return the object a reference names when the version has no component kind for it, else None."""
        since = _SINCE.get(COMPONENT_KINDS.get(role, ""))
        if (
            since is None
            or self.version in since
            or not isinstance(ref := value.get("$ref"), str)
            or (resolved := self.resolve(document, base, ref)) is None
        ):
            return None
        found = self.node(resolved[0], resolved[1])
        return (resolved[0], resolved[2], found) if isinstance(found, dict) and id(found) not in self.stack else None

    def reference(self, ref: str, document: int, base: str, role: str, at: tuple[str, ...]) -> str:
        """Return a reference rewritten into the served document, bundling what it names outside the kept part.

        A reference that names nothing the generation loaded stays as it is, and is reported.
        """
        if (resolved := self.resolve(document, base, ref)) is None:
            self.problems[
                f"{join_pointer(at)}: The served document keeps {redact_reference(ref)}, "
                "which names nothing the generation loaded"
            ] = None
            return ref
        target, tokens, resource = resolved
        local = "#" + join_pointer(tokens)
        if target == 0:
            if tokens[:1] == ("components",) and len(tokens) >= 3:  # ruff: ignore[magic-value-comparison] - components, kind, name.
                self.wanted.setdefault((tokens[1], tokens[2]), None)
                return local
            if tokens[:1] != ("paths",) or tokens[1:2] == () or tokens[1] in self.selected:
                return local
        key = (target, tokens)
        if (found := self.bundled.get(key)) is not None:
            return found
        kind = COMPONENT_KINDS[role]
        node = self.node(target, tokens)
        if len(tokens) == 3 and tokens[0] == "components":  # ruff: ignore[magic-value-comparison] - components, kind, name.
            name = tokens[2]
        elif isinstance(own := as_mapping(node).get("name"), str) and own:
            name = own
        else:
            name = tokens[-1] if tokens else PurePosixPath(urlsplit(self.uris[target]).path).stem or kind
        name = _NAME.sub("_", name)
        items = self.extra.setdefault(kind, {})
        root = as_mapping(as_mapping(self.documents[0].get("components")).get(kind))
        unique, _ = first_free(name, lambda name: name in items or name in root, "underscore-from-2")
        self.bundled[key] = bundled = "#" + join_pointer(("components", kind, unique))
        items[unique] = None
        items[unique] = self.copy(node, target, resource, tokens, role)
        return bundled

    def node(self, document: int, tokens: tuple[str, ...]) -> YamlValue:
        """Return the node a pointer names in a loaded document, which ``exists`` or the index found there."""
        return descend(self.documents[document], tokens, "canonical")


def _uri(location: str) -> str:
    """Return the URI references resolve against: a URL as it is, an absolute file path as a `file:` URI.

    A file path is no URI on every platform: `C:` would read as a scheme that relative references cannot join.
    """
    if _URL.match(location) or not (path := Path(location)).is_absolute():
        return location.replace("\\", "/")
    return file_uri(path)
