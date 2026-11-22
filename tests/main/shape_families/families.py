"""Shape families: ordinary modelling idioms written as pure functions from parameters to schema documents.

A family is the product of a few dimensions, each a short tuple of values, and is enumerated completely: every
combination is one shape, except the combinations a family leaves out because no author writes them. A shape is a JSON
Schema 2020-12 document, the name of the definition whose generated model is checked, and a relation key: shapes with
equal keys state one schema meaning in several spellings (definition order, member order, how a base is named), so
their models should decide alike wherever the validator does.

The bound: the five families below, with exactly these dimensions, and nothing else.

``extend`` (432 shapes)
    A child extends an object ``Base`` with a property ``p`` of one of the nine ``LEAVES`` and a boolean ``q``. The
    child requires ``p``, narrows it (``NARROWINGS``) or adds a required integer ``r``; it names the base directly,
    through an alias (a definition that is only a ``$ref``), through a ``$ref`` with sibling ``properties``, or through
    an ``allOf`` with sibling ``properties``; it writes its addition as a second ``allOf`` member or beside the
    ``allOf``; and it is defined before or after the others. 9 x 3 x 4 x 2 x 2.
``union`` (72 shapes)
    A child extends a union ``Pet`` of the objects ``Cat``, ``BigDog`` and ``SmallDog``: the union written with
    ``oneOf`` or ``anyOf``, flat, nested through a named union ``Dog`` or nested inline; the child requires the shared
    ``name``, adds an optional ``owner`` or adds a required ``owner``, as a member or beside the ``allOf``, defined
    first or last. 2 x 3 x 3 x 2 x 2.
``mixin`` (70 shapes)
    An object mixes two objects ``A`` and ``B`` that both declare ``p``, each with one of the nine ``LEAVES``, in both
    member orders. Only pairs whose two declarations have a common value are kept (18 equal pairs and 52 different
    ones): a pair without one has no valid ``p`` at all, which no author writes. 70 of 9 x 9 x 2.
``container`` (144 shapes)
    An object ``Holder`` with a property ``f`` that is a container in one of six spellings (``items``, ``prefixItems``
    with an open tail, ``prefixItems`` closed by ``items: false``, ``additionalProperties``, ``patternProperties``,
    ``propertyNames`` beside ``additionalProperties``) of one of six ``ELEMENTS``, nullable or not, required or not.
    6 x 6 x 2 x 2.
``tagged`` (32 shapes)
    A union ``Pet`` of ``Cat`` and ``Dog`` with a discriminator on ``kind``: the tag a string ``const``, a one-member or
    two-member string ``enum``, an integer ``const`` or a two-member integer ``enum``; the tag also admitting null or
    not; the discriminator with or without a ``mapping``; ``oneOf`` or ``anyOf``. A ``const`` tag that also admits null
    is left out (the const already excludes null): 32 of 5 x 2 x 2 x 2.

``FAMILY_BACKENDS`` names the output model types each family is generated for by default: Pydantic v2 for the first
three, four backends for ``container`` and three for ``tagged``. A cell is a shape and one of its backends: 1,246
cells in all.

Every shape is also spelled as an OpenAPI 3.1 document (``Shape.openapi``): its definitions are the components, and
one operation sends and returns the checked definition, so a client and a server generated from it carry the shape in
a request body and a response. The behaviour gate generates both spellings (``scripts/behaviour_diff.py``, family
cells).
"""

from __future__ import annotations

import functools
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias

import jsonschema

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

Schema: TypeAlias = dict[str, object]
"""A JSON Schema document or subschema, as JSON decodes it."""

DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
REF: Final = "#/$defs/"
COMPONENT_REF: Final = "#/components/schemas/"
OPENAPI_VERSION: Final = "3.1.0"
OPENAPI_SERVER: Final = "https://api.example.com"
"""The server of the OpenAPI spelling: a client needs an absolute http or https URL to send to."""

PYDANTIC_V2: Final = "pydantic_v2.BaseModel"
TYPED_DICT: Final = "typing.TypedDict"
DATACLASSES: Final = "dataclasses.dataclass"
MSGSPEC: Final = "msgspec.Struct"

LEAVES: Final[Mapping[str, Schema]] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "num": {"type": "number"},
    "str_min": {"type": "string", "minLength": 2},
    "int_min": {"type": "integer", "minimum": 2},
    "enum": {"type": "string", "enum": ["a", "b"]},
    "nullable_str": {"type": ["string", "null"]},
    "arr": {"type": "array", "items": {"type": "string"}},
    "str_or_arr": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}]},
}
"""The property schemas of ``extend`` and ``mixin``: scalars, bounded scalars, an enum, a nullable, arrays."""

NARROWINGS: Final[Mapping[str, Schema]] = {
    "str": {"minLength": 3},
    "int": {"minimum": 3},
    "num": {"type": "integer"},
    "str_min": {"maxLength": 4},
    "int_min": {"maximum": 4},
    "enum": {"enum": ["a"]},
    "nullable_str": {"type": "string"},
    "arr": {"maxItems": 2},
    "str_or_arr": {"maxItems": 2},
}
"""What a child of ``extend`` writes for ``p`` when it narrows the leaf of that name."""

ELEMENTS: Final[Mapping[str, Schema]] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "nullable_str": {"type": ["string", "null"]},
    "anyof_null": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "ref": {"$ref": REF + "Item"},
    "ref_nullable": {"$ref": REF + "NItem"},
}
"""The item or value schemas of ``container``: scalars, two spellings of a nullable, a model and a nullable alias."""

_COMMON_VALUES: Final = (None, True, 0, 1, 2, 5, 1.5, "", "a", "b", "abc", [], ["a"], ["a", "b", "c"], {})
"""The values the ``mixin`` filter tries: a pair of leaves is kept where one of them meets both."""


@dataclass(frozen=True)
class Shape:
    """One document of a family, the definition whose model is checked and the relation key of its meaning."""

    family: str
    params: tuple[tuple[str, object], ...]
    """The family's parameters of this shape, in the order of its dimensions."""
    document: Schema
    name: str
    relation: tuple[object, ...]

    @property
    def schema(self) -> Schema:
        """The schema of the checked definition: a reference to it beside the document's definitions."""
        return {"$schema": DIALECT, "$ref": REF + self.name, "$defs": self.document["$defs"]}

    @property
    def openapi(self) -> Schema:
        """The shape as an OpenAPI 3.1 document: the definitions as components, the checked one an operation's body.

        One ``POST /check`` operation takes the checked definition as its required JSON request body and returns it as
        its response, so a generated client and server read the shape in both directions.
        """
        body = {"application/json": {"schema": {"$ref": COMPONENT_REF + self.name}}}
        operation = {
            "operationId": "check",
            "requestBody": {"required": True, "content": body},
            "responses": {"200": {"description": "The checked definition.", "content": body}},
        }
        return {
            "openapi": OPENAPI_VERSION,
            "info": {"title": self.family, "version": "1"},
            "servers": [{"url": OPENAPI_SERVER}],
            "paths": {"/check": {"post": operation}},
            "components": {"schemas": _as_components(self.document["$defs"])},
        }


def _as_components(value: object) -> object:
    """Return a copy of a schema whose references to ``$defs`` point to the same names among the components.

    A reference is a ``$ref`` or the value of a discriminator's ``mapping``; no other string of a family document
    starts with ``#/$defs/``.
    """
    if isinstance(value, dict):
        return {key: _as_components(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_components(item) for item in value]
    if isinstance(value, str) and value.startswith(REF):
        return COMPONENT_REF + value.removeprefix(REF)
    return value


@dataclass(frozen=True)
class Family:
    """A family's dimensions, the function that writes one shape and the backends it is generated for."""

    name: str
    dimensions: tuple[tuple[str, tuple[object, ...]], ...]
    build: Callable[..., tuple[Schema, str, tuple[object, ...]] | None]
    """Write the definitions, the checked name and the relation key of one combination, or None to leave it out."""
    backends: tuple[str, ...]

    def shapes(self) -> tuple[Shape, ...]:
        """Return every shape of the family, in the order of its dimensions."""
        return tuple(self._iter_shapes())

    def _iter_shapes(self) -> Iterator[Shape]:
        names = [name for name, _ in self.dimensions]
        for values in itertools.product(*(values for _, values in self.dimensions)):
            params = dict(zip(names, values, strict=True))
            if (built := self.build(**params)) is not None:
                definitions, name, relation = built
                yield Shape(
                    self.name,
                    tuple(params.items()),
                    {"$schema": DIALECT, "$defs": definitions},
                    name,
                    (self.name, *relation),
                )


def _ordered(definitions: Schema, first: str, *, child_first: bool) -> Schema:
    rest = {key: value for key, value in definitions.items() if key != first}
    return {first: definitions[first], **rest} if child_first else {**rest, first: definitions[first]}


def _extend(*, leaf: str, how: str, via: str, form: str, child_first: bool) -> tuple[Schema, str, tuple[object, ...]]:
    addition: Schema = {
        "require": {"required": ["p"]},
        "narrow": {"properties": {"p": NARROWINGS[leaf]}},
        "add": {"properties": {"r": {"type": "integer"}}, "required": ["r"]},
    }[how]
    definitions: Schema = {"Base": {"type": "object", "properties": {"p": LEAVES[leaf], "q": {"type": "boolean"}}}}
    siblings = {"properties": {"extra": {"type": "string"}}}
    parent = {
        "direct": None,
        "alias": {"$ref": REF + "Base"},
        "ref-siblings": {"$ref": REF + "Base", **siblings},
        "allof-siblings": {"allOf": [{"$ref": REF + "Base"}], **siblings},
    }[via]
    if parent is not None:
        definitions["Parent"] = parent
    reference = {"$ref": REF + ("Base" if parent is None else "Parent")}
    definitions["Child"] = (
        {"allOf": [reference, {"type": "object", **addition}]}
        if form == "member"
        else {"allOf": [reference], **addition}
    )
    return _ordered(definitions, "Child", child_first=child_first), "Child", (leaf, how)


def _union(*, kind: str, shape: str, how: str, form: str, child_first: bool) -> tuple[Schema, str, tuple[object, ...]]:
    def variant(flag: str) -> Schema:
        return {
            "type": "object",
            "properties": {"name": {"type": "string"}, flag: {"type": "boolean"}},
            "required": [flag],
        }

    definitions: Schema = {"Cat": variant("meow"), "BigDog": variant("big"), "SmallDog": variant("small")}
    dogs = [{"$ref": REF + "BigDog"}, {"$ref": REF + "SmallDog"}]
    match shape:
        case "flat":
            definitions["Pet"] = {kind: [{"$ref": REF + "Cat"}, *dogs]}
        case "nested-ref":
            definitions["Dog"] = {kind: dogs}
            definitions["Pet"] = {kind: [{"$ref": REF + "Cat"}, {"$ref": REF + "Dog"}]}
        case _:
            definitions["Pet"] = {kind: [{"$ref": REF + "Cat"}, {kind: dogs}]}
    addition: Schema = {
        "require": {"required": ["name"]},
        "add": {"properties": {"owner": {"type": "string"}}},
        "require-added": {"properties": {"owner": {"type": "string"}}, "required": ["owner"]},
    }[how]
    definitions["Owned"] = (
        {"allOf": [{"$ref": REF + "Pet"}, {"type": "object", **addition}]}
        if form == "member"
        else {"allOf": [{"$ref": REF + "Pet"}], **addition}
    )
    return _ordered(definitions, "Owned", child_first=child_first), "Owned", (kind, how)


def has_common_value(first: Schema, second: Schema) -> bool:
    """Return whether one of ``_COMMON_VALUES`` meets both schemas, so that their conjunction admits some value."""
    validator = jsonschema.Draft202012Validator({"allOf": [first, second]})
    return any(validator.is_valid(value) for value in _COMMON_VALUES)


def _mixin(*, first: str, second: str, swapped: bool) -> tuple[Schema, str, tuple[object, ...]] | None:
    if not has_common_value(LEAVES[first], LEAVES[second]):
        return None
    members = [{"$ref": REF + "A"}, {"$ref": REF + "B"}]
    definitions = {
        "A": {"type": "object", "properties": {"p": LEAVES[first], "a": {"type": "boolean"}}},
        "B": {"type": "object", "properties": {"p": LEAVES[second], "b": {"type": "boolean"}}},
        "Mixed": {"allOf": members[::-1] if swapped else members},
    }
    return definitions, "Mixed", tuple(sorted((first, second)))


_CONTAINERS: Final[Mapping[str, Callable[[Schema], Schema]]] = {
    "items": lambda element: {"type": "array", "items": element},
    "prefix-tail": lambda element: {
        "type": "array",
        "prefixItems": [{"type": "string"}, element],
        "items": {"type": "string"},
    },
    "prefix-closed": lambda element: {"type": "array", "prefixItems": [{"type": "string"}, element], "items": False},
    "map": lambda element: {"type": "object", "additionalProperties": element},
    "pattern-map": lambda element: {
        "type": "object",
        "patternProperties": {"^a": element},
        "additionalProperties": False,
    },
    "names-map": lambda element: {
        "type": "object",
        "propertyNames": {"pattern": "^a"},
        "additionalProperties": element,
    },
}


def _container(
    *, container: str, element: str, nullable: bool, required: bool
) -> tuple[Schema, str, tuple[object, ...]]:
    schema = _CONTAINERS[container](ELEMENTS[element])
    if nullable:
        schema = {**schema, "type": [schema["type"], "null"]}
    definitions = {
        "Item": {"type": "object", "properties": {"x": {"type": "integer"}}},
        "NItem": {"type": ["string", "null"]},
        "Holder": {"type": "object", "properties": {"f": schema}, **({"required": ["f"]} if required else {})},
    }
    return definitions, "Holder", (container, element, nullable, required)


_TAGS: Final[Mapping[str, tuple[Schema, Schema, dict[str, str]]]] = {
    "const-str": ({"const": "cat"}, {"const": "dog"}, {"cat": "Cat", "dog": "Dog"}),
    "enum1-str": (
        {"type": "string", "enum": ["cat"]},
        {"type": "string", "enum": ["dog"]},
        {"cat": "Cat", "dog": "Dog"},
    ),
    "enum2-str": (
        {"type": "string", "enum": ["cat", "kitty"]},
        {"type": "string", "enum": ["dog"]},
        {"cat": "Cat", "kitty": "Cat", "dog": "Dog"},
    ),
    "const-int": ({"type": "integer", "const": 1}, {"type": "integer", "const": 2}, {"1": "Cat", "2": "Dog"}),
    "enum2-int": (
        {"type": "integer", "enum": [1, 3]},
        {"type": "integer", "const": 2},
        {"1": "Cat", "3": "Cat", "2": "Dog"},
    ),
}
"""Each tag form: the tag schema of ``Cat``, that of ``Dog``, and the mapping from tag values to variants."""


def _admitting_null(tag: Schema) -> Schema:
    nullable = {**tag, "type": [tag.get("type", "string"), "null"]}
    if "enum" in tag:
        nullable["enum"] = [*tag["enum"], None]
    return nullable


def _tagged(
    *, tag: str, nullable: bool, mapping: bool, kind: str, inline: bool = False
) -> tuple[Schema, str, tuple[object, ...]] | None:
    cat_tag, dog_tag, keys = _TAGS[tag]
    if nullable and "const" in cat_tag:
        return None
    if nullable:
        cat_tag, dog_tag = _admitting_null(cat_tag), _admitting_null(dog_tag)
    discriminator: Schema = {"propertyName": "kind"}
    if mapping:
        discriminator["mapping"] = {key: REF + value for key, value in keys.items()}
    variants: list[Schema] = [{"$ref": REF + "Cat"}, {"$ref": REF + "Dog"}]
    if inline:
        variants = [{"allOf": [variant, {"properties": {"note": {"type": "string"}}}]} for variant in variants]
    definitions = {
        "Cat": {"type": "object", "required": ["kind"], "properties": {"kind": cat_tag, "name": {"type": "string"}}},
        "Dog": {"type": "object", "required": ["kind"], "properties": {"kind": dog_tag, "bark": {"type": "boolean"}}},
        "Pet": {kind: variants, "discriminator": discriminator},
    }
    return definitions, "Pet", (tag, nullable)


_BOOLS: Final = (True, False)

FAMILIES: Final[Mapping[str, Family]] = {
    family.name: family
    for family in (
        Family(
            "extend",
            (
                ("leaf", tuple(LEAVES)),
                ("how", ("require", "narrow", "add")),
                ("via", ("direct", "alias", "ref-siblings", "allof-siblings")),
                ("form", ("member", "beside")),
                ("child_first", _BOOLS),
            ),
            _extend,
            (PYDANTIC_V2,),
        ),
        Family(
            "union",
            (
                ("kind", ("oneOf", "anyOf")),
                ("shape", ("flat", "nested-ref", "nested-inline")),
                ("how", ("require", "add", "require-added")),
                ("form", ("member", "beside")),
                ("child_first", _BOOLS),
            ),
            _union,
            (PYDANTIC_V2,),
        ),
        Family(
            "mixin",
            (("first", tuple(LEAVES)), ("second", tuple(LEAVES)), ("swapped", (False, True))),
            _mixin,
            (PYDANTIC_V2,),
        ),
        Family(
            "container",
            (
                ("container", tuple(_CONTAINERS)),
                ("element", tuple(ELEMENTS)),
                ("nullable", (False, True)),
                ("required", (False, True)),
            ),
            _container,
            (PYDANTIC_V2, TYPED_DICT, DATACLASSES, MSGSPEC),
        ),
        Family(
            "tagged",
            (
                ("tag", tuple(_TAGS)),
                ("nullable", (False, True)),
                ("mapping", (False, True)),
                ("kind", ("oneOf", "anyOf")),
            ),
            _tagged,
            (PYDANTIC_V2, MSGSPEC, TYPED_DICT),
        ),
    )
}
"""Every family by name, each with its dimensions and the backends it is generated for by default."""

FAMILY_BACKENDS: Final[Mapping[str, tuple[str, ...]]] = {name: family.backends for name, family in FAMILIES.items()}


_REDECLARED: Final[Mapping[str, tuple[Schema, Schema]]] = {
    "model": (
        {"type": "object", "properties": {"name": {"type": "string"}}},
        {"allOf": [{"$ref": REF + "Pet"}, {"type": "object", "properties": {"bark": {"type": "boolean"}}}]},
    ),
    "enum": ({"type": "string", "enum": ["cat", "dog"]}, {"type": "string", "enum": ["dog"]}),
}
"""The definitions ``Pet`` and ``Dog`` of ``redeclare``: an object and an object that extends it, or a string enum and
an enum of some of its members, so that ``Dog`` narrows ``Pet``."""


def _redeclare(*, inherited: str, name: str, override: str, form: str) -> tuple[Schema, str, tuple[object, ...]]:
    pet, dog = _REDECLARED[inherited]
    addition: Schema = {"properties": {name: {"$ref": REF + ("Pet" if override == "same" else "Dog")}}}
    definitions: Schema = {
        "Pet": pet,
        "Dog": dog,
        "Base": {"type": "object", "properties": {name: {"$ref": REF + "Pet"}, "q": {"type": "boolean"}}},
        "Owner": (
            {"allOf": [{"$ref": REF + "Base"}, {"type": "object", **addition}]}
            if form == "member"
            else {"allOf": [{"$ref": REF + "Base"}], **addition}
        ),
    }
    return definitions, "Owner", (inherited, name, override)


REDECLARING_FAMILIES: Final[Mapping[str, Family]] = {
    family.name: family
    for family in (
        Family(
            "redeclare",
            (
                ("inherited", tuple(_REDECLARED)),
                ("name", ("pet", "Pet")),
                ("override", ("same", "other")),
                ("form", ("member", "beside")),
            ),
            _redeclare,
            (PYDANTIC_V2, TYPED_DICT, DATACLASSES, MSGSPEC),
        ),
        Family(
            "tagged-allof",
            FAMILIES["tagged"].dimensions,
            functools.partial(_tagged, inline=True),
            FAMILIES["tagged"].backends,
        ),
    )
}
"""The families whose classes redeclare a field they inherit, which the invariants check beside ``FAMILIES``.

``redeclare`` (16 shapes)
    An object ``Owner`` extends ``Base``, whose property ``pet`` or ``Pet`` (named like the definition it references)
    is a ``$ref`` to ``Pet``, and redeclares that property as a ``$ref`` to ``Pet`` again or to ``Dog``, which narrows
    ``Pet``, as a second ``allOf`` member or beside the ``allOf``; ``Pet`` and ``Dog`` are objects (``Dog`` extends
    ``Pet``) or string enums (``Dog`` has some of the members of ``Pet``). 2 x 2 x 2 x 2.
``tagged-allof`` (32 shapes)
    ``tagged`` with each variant written inline as an ``allOf`` of the reference to ``Cat`` or ``Dog`` and a member that
    adds an optional ``note``: the generated variants subclass ``Cat`` and ``Dog`` and redeclare the tag.
"""
