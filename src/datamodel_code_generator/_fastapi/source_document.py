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
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final, TypeAlias, cast
from urllib.parse import unquote, urljoin, urlsplit

from datamodel_code_generator._runtime.model_codecs.wire import escape_pointer_token, pointer_tokens

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator._openapi_generation import SourceLease
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_contract import OperationContract

JSON: TypeAlias = "bool | int | float | str | list[JSON] | dict[str, JSON] | None"
Key: TypeAlias = "tuple[int, tuple[str, ...]]"
Child: TypeAlias = "tuple[str, str]"

_METHODS: Final = ("get", "put", "post", "delete", "options", "head", "patch", "trace", "query")
_MEDIA: Final[dict[str, Child]] = {
    "schema": ("one", "schema"),
    "itemSchema": ("one", "schema"),
    "examples": ("map", "example"),
    "encoding": ("map", "encoding"),
    "prefixEncoding": ("list", "encoding"),
    "itemEncoding": ("one", "encoding"),
}
_PARAMETER: Final[dict[str, Child]] = {
    "schema": ("one", "schema"),
    "content": ("map", "mediaType"),
    "examples": ("map", "example"),
}
_CHILDREN: Final[dict[str, dict[str, Child]]] = {
    "pathItem": {
        **dict.fromkeys(_METHODS, ("one", "operation")),
        "additionalOperations": ("map", "operation"),
        "parameters": ("list", "parameter"),
    },
    "operation": {
        "parameters": ("list", "parameter"),
        "requestBody": ("one", "requestBody"),
        "responses": ("map", "response"),
        "callbacks": ("map", "callback"),
    },
    "parameter": _PARAMETER,
    "header": _PARAMETER,
    "mediaType": _MEDIA,
    "encoding": {
        "headers": ("map", "header"),
        **{key: _MEDIA[key] for key in ("encoding", "prefixEncoding", "itemEncoding")},
    },
    "requestBody": {"content": ("map", "mediaType")},
    "response": {"headers": ("map", "header"), "content": ("map", "mediaType"), "links": ("map", "link")},
}
_KINDS: Final = {
    "pathItem": "pathItems",
    "parameter": "parameters",
    "header": "headers",
    "mediaType": "mediaTypes",
    "requestBody": "requestBodies",
    "response": "responses",
    "callback": "callbacks",
    "example": "examples",
    "link": "links",
    "schema": "schemas",
    "securityScheme": "securitySchemes",
}
_ROLES: Final = {kind: role for role, kind in _KINDS.items()}
_SINCE: Final = {"pathItems": (3, 1), "mediaTypes": (3, 2)}
_SCHEMA_MAPS: Final = frozenset({"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})
_LITERALS: Final = frozenset({"example", "examples", "default", "enum", "const"})
_KEPT: Final = "securitySchemes"
_NAME: Final = re.compile(r"[^A-Za-z0-9._-]")


class SourceDocument:
    """Copy the selected operations' part of the loaded documents into one JSON document."""

    def __init__(self, lease: SourceLease) -> None:
        """Index the loaded documents, and the schema resources and anchors they declare, by their URIs.

        A document is a resource at the location it was read from and at its `$self`; from OpenAPI 3.1 on, a schema's
        `$id` declares a resource, and `$anchor` and `$dynamicAnchor` a name in the resource around them.
        """
        held = lease.documents()
        self.uris = [location for location, _ in held]
        self.documents = [document for _, document in held]
        root = self.documents[0]
        version = re.match(r"(\d+)\.(\d+)", str(root.get("openapi", "")))
        self.version = (int(version[1]), int(version[2])) if version else (3, 1)
        self.bases = [
            urljoin(uri, own) if isinstance(own := document.get("$self"), str) else uri
            for uri, document in zip(self.uris, self.documents, strict=True)
        ]
        self.resources: dict[str, tuple[int, tuple[str, ...]]] = {}
        self.anchors: dict[tuple[str, str], tuple[int, tuple[str, ...]]] = {}
        for index, document in enumerate(self.documents):
            self.index(index, document, (self.uris[index], self.bases[index]))
        self.schemas = _mapping(_mapping(root.get("components")).get("schemas"))
        self.problems: dict[str, None] = {}
        self.wanted: dict[tuple[str, str], None] = {}
        self.bundled: dict[Key, str] = {}
        self.extra: dict[str, dict[str, JSON]] = {}
        self.stack: set[int] = set()
        self.selected: frozenset[str] = frozenset()

    def index(self, document: int, value: dict[str, YamlValue], names: tuple[str, ...]) -> None:
        """Record a document's resources and anchors, walking it once with the resource each node belongs to."""
        pending: list[tuple[tuple[str, ...], YamlValue, tuple[str, ...]]] = [((), value, names)]
        seen: set[int] = set()
        for name in names:
            self.resources.setdefault(name, (document, ()))
        while pending:
            at, node, around = pending.pop()
            if not isinstance(node, dict | list) or id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, list):
                pending.extend(((*at, str(index)), child, around) for index, child in enumerate(node))
                continue
            if isinstance(own := node.get("$id"), str) and self.version >= (3, 1) and at:
                around = (urljoin(around[0], own).partition("#")[0],)
                self.resources.setdefault(around[0], (document, at))
            for keyword in ("$anchor", "$dynamicAnchor"):
                if isinstance(anchor := node.get(keyword), str):
                    for name in around:
                        self.anchors.setdefault((name, anchor), (document, at))
            pending.extend(((*at, str(key)), child, around) for key, child in node.items())

    def resolve(self, document: int, base: str, ref: str) -> tuple[int, tuple[str, ...], str] | None:
        """Return the document, pointer tokens, and resource base of what a reference names, or None.

        The reference resolves against its schema resource's base, then, as the parser does for compatibility, against
        where its document was read: a pointer that names nothing in the resource is the document's.
        """
        for start in (base, self.uris[document]):
            resource, _, fragment = urljoin(start, ref).partition("#")
            fragment = unquote(fragment)
            if (found := self.resources.get(resource)) is None:
                continue
            tokens = (*found[1], *pointer_tokens(fragment))
            target = (
                self.anchors.get((resource, fragment))
                if fragment and not fragment.startswith("/")
                else (found[0], tokens)
                if self.exists(found[0], tokens)
                else None
            )
            if target is not None:
                return target[0], target[1], resource
        return None

    def exists(self, document: int, tokens: tuple[str, ...]) -> bool:
        """Return whether a pointer names a node of a loaded document."""
        value: YamlValue = self.documents[document]
        for token in tokens:
            if isinstance(value, list) and token.isdecimal() and int(token) < len(value):
                value = value[int(token)]
            elif isinstance(value, dict) and (token in value or (token.isdecimal() and int(token) in value)):
                value = _member(value, token)
            else:
                return False
        return True

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
                    for path, item in _mapping(value).items()
                    if str(path) in self.selected
                }
            elif name == "webhooks":
                document[name] = self.members(value, 0, base, (name,), ("map", "pathItem"))
            elif name != "components" and self.json(value, (name,)):
                document[name] = self.copy(value, 0, base, (name,), "any")
        if components := self.components(_mapping(root.get("components"))):
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
            for name, value in _mapping(items).items()
            if (str(kind), str(name)) in wanted and (str(kind), str(name)) not in copied
        ]:
            for kind, name, value in todo:
                copied[kind, name] = self.copy(value, 0, base, ("components", kind, name), _ROLES.get(kind, "any"))
        components: dict[str, JSON] = {}
        for key, value in source.items():
            kind = str(key)
            if kind == _KEPT:
                components[kind] = self.members(value, 0, base, ("components", kind), ("map", "securityScheme"))
            elif found := {
                str(name): copied[kind, str(name)] for name in _mapping(value) if (kind, str(name)) in copied
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
            f"#/components/schemas/{escape_pointer_token(name)}"
            for kind, name in self.wanted
            if kind == "schemas" and isinstance(_mapping(self.schemas.get(name)).get("discriminator"), dict)
        }
        for name, schema in self.schemas.items():
            parts = _mapping(schema).get("allOf")
            if isinstance(parts, list) and any(_mapping(part).get("$ref") in bases for part in parts):
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
        copy leaves it out: every reference in the document is relative to the document itself.
        """
        if role == "schema" and isinstance(own := value.get("$id"), str) and self.version >= (3, 1):
            base = urljoin(base, own)
        children = _CHILDREN.get(role, {})
        copied: dict[str, JSON] = {}
        for key, child in value.items():
            name, place = str(key), (*at, str(key))
            if not self.json(child, place) or (role == "schema" and name == "$id" and isinstance(child, str)):
                continue
            if name == "$ref" and isinstance(child, str) and role in _KINDS:
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
        if name in _SCHEMA_MAPS and isinstance(value, dict):
            return {
                str(key): self.copy(item, document, base, (*at, str(key)), "schema")
                for key, item in value.items()
                if self.json(item, (*at, str(key)))
            }
        return self.copy(value, document, base, at, "any" if name in _LITERALS or name.startswith("x-") else "schema")

    def members(self, value: YamlValue, document: int, base: str, at: tuple[str, ...], shape: Child) -> JSON:
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
        ref = _mapping(link).get("operationRef")
        if not isinstance(ref, str) or not ref.startswith("#/paths/"):
            return True
        if pointer_tokens(unquote(ref[1:]))[1] in self.selected:
            return True
        self.problems[f"{_pointer(at)}: The served document leaves out the link, whose operation it leaves out"] = None
        return False

    def discriminator(self, value: dict[str, YamlValue], document: int, base: str, at: tuple[str, ...]) -> JSON:
        """Return a discriminator, keeping the schema each mapping value names, by reference or by component name."""
        copied = self.mapping({key: item for key, item in value.items() if key != "mapping"}, document, base, at, "any")
        if isinstance(mapping := value.get("mapping"), dict):
            copied["mapping"] = {
                str(key): self.reference(item, document, base, "schema", (*at, "mapping", str(key)))
                if "/" in item or "#" in item
                else self.reference(
                    f"#/components/schemas/{item}", document, self.uris[document], "schema", (*at, "mapping", str(key))
                ).rpartition("/")[2]
                for key, item in mapping.items()
                if isinstance(item, str)
            }
        return copied

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
        self.problems[f"{_pointer(at)}: The served document leaves out {at[-1]}, which has no JSON form"] = None
        return False

    def inline(
        self, value: dict[str, YamlValue], document: int, base: str, role: str
    ) -> tuple[int, str, dict[str, YamlValue]] | None:
        """Return the object a reference names when the version has no component kind for it, else None."""
        since = _SINCE.get(_KINDS.get(role, ""))
        if (
            since is None
            or self.version >= since
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
                f"{_pointer(at)}: The served document keeps {ref}, which names nothing the generation loaded"
            ] = None
            return ref
        target, tokens, resource = resolved
        local = "#" + "".join(f"/{escape_pointer_token(token)}" for token in tokens)
        if target == 0:
            if tokens[:1] == ("components",) and len(tokens) >= 3:  # ruff: ignore[magic-value-comparison] - components, kind, name.
                self.wanted.setdefault((tokens[1], tokens[2]), None)
                return local
            if tokens[:1] != ("paths",) or tokens[1:2] == () or tokens[1] in self.selected:
                return local
        key = (target, tokens)
        if (found := self.bundled.get(key)) is not None:
            return found
        kind = _KINDS[role]
        node = self.node(target, tokens)
        if len(tokens) == 3 and tokens[0] == "components":  # ruff: ignore[magic-value-comparison] - components, kind, name.
            name = tokens[2]
        elif isinstance(own := _mapping(node).get("name"), str) and own:
            name = own
        else:
            name = tokens[-1] if tokens else PurePosixPath(urlsplit(self.uris[target]).path).stem or kind
        name = _NAME.sub("_", name)
        items = self.extra.setdefault(kind, {})
        root = _mapping(_mapping(self.documents[0].get("components")).get(kind))
        unique, count = name, 1
        while unique in items or unique in root:
            count += 1
            unique = f"{name}_{count}"
        self.bundled[key] = bundled = f"#/components/{escape_pointer_token(kind)}/{escape_pointer_token(unique)}"
        items[unique] = None
        items[unique] = self.copy(node, target, resource, tokens, role)
        return bundled

    def node(self, document: int, tokens: tuple[str, ...]) -> YamlValue:
        """Return the node a pointer names in a loaded document."""
        value: YamlValue = self.documents[document]
        for token in tokens:
            value = value[int(token)] if isinstance(value, list) else _member(_mapping(value), token)
        return value


def _pointer(at: tuple[str, ...]) -> str:
    return "/" + "/".join(escape_pointer_token(token) for token in at)


def _member(mapping: dict[str, YamlValue], token: str) -> YamlValue:
    """Return a mapping's member by key, by its integer YAML key when no string key matches."""
    return (
        mapping[token]
        if token in mapping
        else cast("dict[object, YamlValue]", mapping).get(int(token))
        if token.isdecimal()
        else None
    )


def _mapping(value: YamlValue) -> dict[str, YamlValue]:
    return value if isinstance(value, dict) else {}
