"""Write the part of the source OpenAPI document the selected operations use, which the generated server serves.

The document keeps the source's own spelling: its OpenAPI version, info, servers, tags, security schemes, webhooks,
and the selected operations with their callbacks. Components stay when something kept references them, and what a
reference names in another loaded document joins the components, so the served document stands alone. It is
documentation: the generated models stay the contract the server validates with.
"""

from __future__ import annotations

import json
import math
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final, TypeAlias, cast
from urllib.parse import unquote, urljoin, urlsplit

from datamodel_code_generator._runtime.model_codecs.wire import escape_pointer_token, pointer_tokens

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator._openapi_generation import SourceLease
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator._target_contract import GeneratedTypeContractBatch, OperationContract

JSON: TypeAlias = "bool | int | float | str | list[JSON] | dict[str, JSON] | None"
Key: TypeAlias = "tuple[int, tuple[str, ...]]"

_SCHEMA: Final = "schemas"
_CONTAINERS: Final = {
    "parameters": "parameters",
    "responses": "responses",
    "headers": "headers",
    "examples": "examples",
    "links": "links",
    "callbacks": "callbacks",
}
_SINGLE: Final = {"schema": _SCHEMA, "requestBody": "requestBodies"}
_KEPT: Final = "securitySchemes"


class SourceDocument:
    """Copy the selected operations' part of the loaded documents into one JSON document."""

    def __init__(self, batch: GeneratedTypeContractBatch, lease: SourceLease) -> None:
        """Index the loaded documents by the URI the parser loaded each from."""
        self.uris = [document.uri for document in batch.documents]
        self.documents = [lease.document(document.id) for document in batch.documents]
        self.bases = [
            urljoin(uri, own) if isinstance(own := document.get("$self"), str) else uri
            for uri, document in zip(self.uris, self.documents, strict=True)
        ]
        self.ids = {uri: index for index, uri in enumerate(self.uris)} | {
            base: index for index, base in enumerate(self.bases)
        }
        self.problems: dict[str, None] = {}
        self.wanted: dict[tuple[str, str], None] = {}
        self.bundled: dict[Key, str] = {}
        self.extra: dict[str, dict[str, JSON]] = {}
        self.stack: set[int] = set()

    def text(self, operations: Iterable[OperationContract]) -> str:
        """Return the document of the operations as indented JSON text."""
        return json.dumps(self.build(operations), ensure_ascii=True, indent=1)

    def build(self, operations: Iterable[OperationContract]) -> dict[str, JSON]:
        """Return the document of the operations: the root's members, the paths they use, and what those reference.

        Selection keeps or leaves out a whole path, so a kept path item keeps every operation it declares.
        """
        selected = {operation.path for operation in operations}
        root = self.documents[0]
        document: dict[str, JSON] = {}
        for key, value in root.items():
            name = str(key)
            if name == "paths":
                document[name] = {
                    str(path): self.path_item(str(path), item)
                    for path, item in _mapping(value).items()
                    if str(path) in selected
                }
            elif name != "components" and self.json(value, (name,)):
                document[name] = self.copy(value, 0, (name,), None)
        if components := self.components(_mapping(root.get("components"))):
            document["components"] = components
        return document

    def path_item(self, path: str, item: YamlValue) -> JSON:
        """Return a path item, the one its reference chain ends at in place of the reference."""
        document, at, value = self.resolve(0, ("paths", path), _mapping(item))
        return self.copy({key: child for key, child in value.items() if key != "$ref"}, document, at, None)

    def components(self, source: Mapping[str, YamlValue]) -> dict[str, JSON]:
        """Return the components something kept references, in source order, then those bundled from elsewhere."""
        copied: dict[tuple[str, str], JSON] = {}
        while todo := [
            (kind, name, value)
            for kind, items in source.items()
            for name, value in _mapping(items).items()
            if (str(kind), str(name)) in self.wanted and (str(kind), str(name)) not in copied
        ]:
            for kind, name, value in todo:
                at = ("components", str(kind), str(name))
                copied[at[1:]] = self.copy(value, 0, at, str(kind))
        components: dict[str, JSON] = {}
        for key, value in source.items():
            kind = str(key)
            if kind == _KEPT:
                components[kind] = self.copy(value, 0, ("components", kind), None)
            elif found := {
                str(name): copied[kind, str(name)] for name in _mapping(value) if (kind, str(name)) in copied
            }:
                components[kind] = found
        for kind, items in self.extra.items():
            target = components.setdefault(kind, {})
            assert isinstance(target, dict)
            target.update(items)
        return components

    def copy(self, value: YamlValue, document: int, at: tuple[str, ...], kind: str | None) -> JSON:
        """Return a JSON copy of a value with its references rewritten into the document, leaving out non-JSON values.

        `kind` names the component a reference of this value would be: a schema below any schema.
        """
        if not isinstance(value, dict | list):
            assert value is None or isinstance(value, str | int | float)
            return value
        self.stack.add(id(value))
        try:
            return self.container(value, document, at, kind)
        finally:
            self.stack.discard(id(value))

    def container(
        self, value: dict[str, YamlValue] | list[YamlValue], document: int, at: tuple[str, ...], kind: str | None
    ) -> JSON:
        """Return a JSON copy of a mapping or list, which `copy` keeps on its stack meanwhile."""
        if isinstance(value, dict):
            copied: dict[str, JSON] = {}
            for key, child in value.items():
                name = str(key)
                if name == "$ref" and isinstance(child, str):
                    copied[name] = self.reference(child, document, kind or _SCHEMA)
                elif kind == _SCHEMA and name == "discriminator" and isinstance(child, dict):
                    copied[name] = self.discriminator(child, document, (*at, name))
                elif (container := _CONTAINERS.get(name)) is not None and kind != _SCHEMA:
                    copied[name] = self.members(child, document, (*at, name), container)
                elif self.json(child, (*at, name)):
                    child_kind = kind if kind == _SCHEMA else _SINGLE.get(name, kind)
                    copied[name] = self.copy(child, document, (*at, name), child_kind)
            return copied
        return [
            self.copy(item, document, (*at, str(index)), kind)
            for index, item in enumerate(value)
            if self.json(item, (*at, str(index)))
        ]

    def members(self, value: YamlValue, document: int, at: tuple[str, ...], kind: str) -> JSON:
        """Return a list or map of objects of one component kind, such as an operation's parameters."""
        if isinstance(value, dict):
            return {
                str(key): self.copy(child, document, (*at, str(key)), kind)
                for key, child in value.items()
                if self.json(child, (*at, str(key)))
            }
        return self.copy(value, document, at, kind)

    def discriminator(self, value: dict[str, YamlValue], document: int, at: tuple[str, ...]) -> JSON:
        """Return a discriminator, keeping the schema each mapping value names, by reference or by component name."""
        copied = _object(self.copy({key: item for key, item in value.items() if key != "mapping"}, document, at, None))
        if isinstance(mapping := value.get("mapping"), dict):
            copied["mapping"] = {
                str(key): self.reference(item, document, _SCHEMA)
                if "/" in item or "#" in item
                else self.reference(f"#/components/schemas/{item}", document, _SCHEMA).rpartition("/")[2]
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
        pointer = "/" + "/".join(escape_pointer_token(token) for token in at)
        self.problems[f"{pointer}: The served document leaves out {at[-1]}, which has no JSON form"] = None
        return False

    def reference(self, ref: str, document: int, kind: str) -> str:
        """Return a reference rewritten into the served document, bundling what it names in another document."""
        target = self.locate(document, ref)
        if target is None or (tokens := _fragment(ref)) is None:
            return ref
        if target == 0:
            if tokens[:1] == ("components",) and len(tokens) >= 3:  # ruff: ignore[magic-value-comparison] - components, kind, name.
                self.wanted.setdefault((tokens[1], tokens[2]), None)
            return "#" + "".join(f"/{escape_pointer_token(token)}" for token in tokens)
        key = (target, tokens)
        if (found := self.bundled.get(key)) is not None:
            return found
        if len(tokens) == 3 and tokens[0] == "components":  # ruff: ignore[magic-value-comparison] - components, kind, name.
            kind, name = tokens[1], tokens[2]
        else:
            name = tokens[-1] if tokens else PurePosixPath(urlsplit(self.uris[target]).path).stem or kind
        items = self.extra.setdefault(kind, {})
        root = _mapping(_mapping(self.documents[0].get("components")).get(kind))
        unique, count = name, 1
        while unique in items or unique in root:
            count += 1
            unique = f"{name}_{count}"
        self.bundled[key] = bundled = f"#/components/{escape_pointer_token(kind)}/{escape_pointer_token(unique)}"
        items[unique] = None
        node = self.node(target, tokens)
        items[unique] = self.copy(node, target, tokens, kind)
        return bundled

    def locate(self, document: int, ref: str) -> int | None:
        """Return the loaded document a reference names: against the document's `$self`, then where it was read."""
        if ref.startswith("#"):
            return document
        target = ref.partition("#")[0]
        return next(
            (
                self.ids[uri]
                for uri in (urljoin(self.bases[document], target), urljoin(self.uris[document], target))
                if uri in self.ids
            ),
            None,
        )

    def resolve(
        self, document: int, at: tuple[str, ...], value: dict[str, YamlValue]
    ) -> tuple[int, tuple[str, ...], dict[str, YamlValue]]:
        """Follow a path item's reference chain through the loaded documents."""
        seen = {(document, at)}
        while (
            isinstance(ref := value.get("$ref"), str)
            and (target := self.locate(document, ref)) is not None
            and (tokens := _fragment(ref)) is not None
            and (target, tokens) not in seen
        ):
            seen.add((target, tokens))
            document, at, value = target, tokens, _mapping(self.node(target, tokens))
        return document, at, value

    def node(self, document: int, tokens: tuple[str, ...]) -> YamlValue:
        """Return the node a pointer names in a loaded document."""
        value: YamlValue = self.documents[document]
        for token in tokens:
            value = value[int(token)] if isinstance(value, list) else _member(_mapping(value), token)
        return value


def _fragment(ref: str) -> tuple[str, ...] | None:
    """Return the tokens of a reference's JSON pointer fragment, or None for a named anchor."""
    if not (fragment := unquote(ref.partition("#")[2])):
        return ()
    return tuple(pointer_tokens(fragment)) if fragment.startswith("/") else None


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


def _object(value: JSON) -> dict[str, JSON]:
    assert isinstance(value, dict)
    return value
