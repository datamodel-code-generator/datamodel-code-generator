"""The objects of an OpenAPI document by their role, and the objects and schemas each of them holds.

``OBJECT_MEMBERS`` is the one table of the members of an object of one role (a path item, an operation, a parameter, a
media type, ...) that hold other objects: whether a member holds one, a list or a map of them, and their role.
``COMPONENT_KINDS`` names the component kind of each role a reference can name, ``COMPONENT_ROLES`` the role of each
kind. ``object_members`` reads the members of one object by the table, the root of a document as an OpenAPI Object, and
``held_schemas`` the schemas an object holds, through the objects between them. A document a reference names at its
root holds an object of the role that the reference's position expects (OpenAPI: "Detecting a document containing a
referenceable Object at its root based on the expected type of the reference"), so the schemas that declare its `$id`
and `$anchor` values are the ones ``held_schemas`` yields for that role; the parser's schema resources and the served
document read them so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, TypeAlias

from datamodel_code_generator._openapi_paths import PATH_ITEM_METHODS

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from datamodel_code_generator._source import YamlValue

__all__ = ["COMPONENT_KINDS", "COMPONENT_ROLES", "OBJECT_MEMBERS", "Member", "held_schemas", "object_members"]

Member: TypeAlias = "tuple[str, str]"
"""How a member holds objects (``one``, ``list`` or ``map``) and their role."""

_MEDIA: Final[dict[str, Member]] = {
    "schema": ("one", "schema"),
    "itemSchema": ("one", "schema"),
    "examples": ("map", "example"),
    "encoding": ("map", "encoding"),
    "prefixEncoding": ("list", "encoding"),
    "itemEncoding": ("one", "encoding"),
}
_PARAMETER: Final[dict[str, Member]] = {
    "schema": ("one", "schema"),
    "content": ("map", "mediaType"),
    "examples": ("map", "example"),
}
OBJECT_MEMBERS: Final[Mapping[str, Mapping[str, Member]]] = {
    "pathItem": {
        **dict.fromkeys(PATH_ITEM_METHODS, ("one", "operation")),
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
"""The members of an object of each role that hold other objects; a callback maps its expressions to path items."""
COMPONENT_KINDS: Final[Mapping[str, str]] = {
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
"""The component kind of each role a reference can name."""
COMPONENT_ROLES: Final[Mapping[str, str]] = {kind: role for role, kind in COMPONENT_KINDS.items()}
"""The role of the objects of each component kind."""
_HOLDERS: Final[Mapping[str, Mapping[str, Member]]] = {
    **OBJECT_MEMBERS,
    "root": {"paths": ("map", "pathItem"), "webhooks": ("map", "pathItem"), "components": ("one", "components")},
    "components": {kind: ("map", role) for kind, role in COMPONENT_ROLES.items()},
}
_CALLBACK_MEMBER: Final[Member] = ("one", "pathItem")


def object_members(node: Mapping[str, YamlValue], role: str) -> Iterator[tuple[tuple[str, ...], YamlValue, str]]:
    """Yield each object the members of an object of one role hold, with its path from the object and its role.

    The role ``root`` reads a document's root as an OpenAPI Object, and ``components`` its Components Object.
    """
    table = _HOLDERS.get(role, {})
    for key, value in node.items():
        name = str(key)
        form, member = _CALLBACK_MEMBER if role == "callback" else table.get(name, ("", ""))
        match form, value:
            case "one", _:
                yield (name,), value, member
            case "map", dict():
                for item, child in value.items():
                    yield (name, str(item)), child, member
            case "list", list():
                for index, child in enumerate(value):
                    yield (name, str(index)), child, member


def held_schemas(node: YamlValue, role: str) -> Iterator[tuple[tuple[str, ...], dict[str, Any]]]:
    """Yield each schema an object of one role holds, with its path from the object, in document order.

    The path is the JSON pointer tokens of the schema; ``_document_reader.member_key`` names the key that holds each.
    The walk reads the objects between them through ``object_members``; a Reference Object holds none, and its other
    members are ignored, a boolean schema declares nothing, an object that holds itself (a YAML alias) is not
    descended again, as ``SchemaGraph.index_document`` does not, and what a schema holds is the schema walker's to
    read.
    """
    return _held_below(node, role, set())


def _held_below(node: YamlValue, role: str, ancestors: set[int]) -> Iterator[tuple[tuple[str, ...], dict[str, Any]]]:
    """Yield the schemas ``held_schemas`` yields for an object, which is not one of its ``ancestors`` (by identity)."""
    if not isinstance(node, dict):
        return
    if role == "schema":
        yield (), node
        return
    if "$ref" in node or id(node) in ancestors:
        return
    ancestors.add(id(node))
    for path, child, member in object_members(node, role):
        yield from (((*path, *tokens), schema) for tokens, schema in _held_below(child, member, ancestors))
    ancestors.discard(id(node))
