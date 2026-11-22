"""The tag plan table: how a discriminated union of an OpenAPI 3.1 document is tagged, by the form of its tags.

A row is a union ``Pet`` of the variants ``Cat`` and ``Dog`` (and ``Bird`` where ``count`` is 3) with a discriminator
on ``kind``. Its dimensions:

``form``
    how a variant writes its tag: a string ``const``, a one-value or two-value string ``enum``, an integer ``const``, a
    two-value integer ``enum``, a plain string whose tag is the variant's schema name or mapping key (``schema-name``),
    or a ``const`` that a variant adds in an ``allOf`` member over a base declaring ``kind`` as a string
    (``inherited``);
``count``
    two or three variants;
``nullable``
    the tag also admits null (a ``type`` list with null, and null in an ``enum``); a ``const`` excludes null by itself,
    so the ``const`` and ``inherited`` forms have no nullable row;
``mapping``
    the discriminator maps every tag to its variant, or has no mapping;
``shared``
    an earlier union ``Zoo`` lists ``Cat`` and ``Dog`` too, with a mapping of their first tags only where ``Pet`` has
    a mapping;
``kind``
    ``oneOf`` or ``anyOf``.

The validator reads the document's schemas as JSON Schema with the discriminator's meaning added where the schema
leaves it out: a variant whose tag property has no ``const`` or ``enum`` takes the tags that name it (its mapping
keys, else its schema name), as OpenAPI reads them. A row is read on payloads of ``Pet``: each tag of each variant
with that variant's own field, a tag with another variant's field, an unknown tag, a null tag, and no tag.

The bound: 11 forms with nullability x 2 counts x 2 mappings x 2 shared x 2 kinds, 176 rows, each generated for
Pydantic v2, msgspec and TypedDict.
"""

from __future__ import annotations

import copy
import itertools
from typing import TYPE_CHECKING, Final

from tests.main.shape_families.families import MSGSPEC, PYDANTIC_V2, TYPED_DICT
from tests.main.shape_families.verdict import Batch, Row

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tests.main.shape_families.families import Schema

TABLE: Final = "tags"
BACKENDS: Final = (PYDANTIC_V2, MSGSPEC, TYPED_DICT)
REF: Final = "#/components/schemas/"
VARIANTS: Final = ("Cat", "Dog", "Bird")
FIELDS: Final[Mapping[str, Schema]] = {
    "Cat": {"name": {"type": "string"}},
    "Dog": {"bark": {"type": "boolean"}},
    "Bird": {"wings": {"type": "integer"}},
}
"""Each variant's own property besides the tag, which a payload of another variant lacks or mistypes."""
OWN_VALUES: Final[Mapping[str, Mapping[str, object]]] = {
    "Cat": {"name": "a"},
    "Dog": {"bark": True},
    "Bird": {"wings": 2},
}
OTHER_VALUES: Final[Mapping[str, Mapping[str, object]]] = {
    "Cat": {"name": 1},
    "Dog": {"bark": "a"},
    "Bird": {"wings": "a"},
}
"""A value of each variant's own property that the variant rejects."""

TAGS: Final[Mapping[str, Mapping[str, tuple[object, ...]]]] = {
    "const-str": {"Cat": ("cat",), "Dog": ("dog",), "Bird": ("bird",)},
    "enum1-str": {"Cat": ("cat",), "Dog": ("dog",), "Bird": ("bird",)},
    "enum2-str": {"Cat": ("cat", "kitty"), "Dog": ("dog",), "Bird": ("bird",)},
    "const-int": {"Cat": (1,), "Dog": (2,), "Bird": (3,)},
    "enum2-int": {"Cat": (1, 4), "Dog": (2,), "Bird": (3,)},
    "schema-name": {"Cat": ("cat",), "Dog": ("dog",), "Bird": ("bird",)},
    "inherited": {"Cat": ("cat",), "Dog": ("dog",), "Bird": ("bird",)},
}
"""Each form's tags of each variant; ``schema-name`` tags by these mapping keys, or by the schema name unmapped."""
CONSTS: Final = frozenset({"const-str", "const-int", "inherited"})
"""The forms whose tag is a ``const``, which excludes null by itself."""


def _tag_schema(form: str, tags: tuple[object, ...], *, nullable: bool) -> Schema:
    """Return the schema of a variant's tag property in a form."""
    kind = "integer" if isinstance(tags[0], int) else "string"
    declared: object = [kind, "null"] if nullable else kind
    if form in {"const-str", "const-int", "inherited"}:
        return {"type": declared, "const": tags[0]}
    if form == "schema-name":
        return {"type": declared}
    return {"type": declared, "enum": [*tags, None] if nullable else list(tags)}


def _variant(form: str, name: str, *, nullable: bool) -> Schema:
    tag = _tag_schema(form, TAGS[form][name], nullable=nullable)
    own = {"type": "object", "required": ["kind"], "properties": {"kind": tag, **FIELDS[name]}}
    if form != "inherited":
        return own
    return {"allOf": [{"$ref": REF + "Base"}, own]}


def _discriminator(form: str, variants: tuple[str, ...], *, mapping: bool, first_only: bool) -> Schema:
    discriminator: Schema = {"propertyName": "kind"}
    if mapping:
        discriminator["mapping"] = {
            str(tag): REF + name for name in variants for tag in TAGS[form][name][: 1 if first_only else None]
        }
    return discriminator


def _tags_naming(form: str, name: str, discriminator: Schema) -> list[object]:
    """Return the tags a discriminator gives a variant whose tag property has no const or enum (OpenAPI)."""
    if form != "schema-name":
        return list(TAGS[form][name])
    mapping = discriminator.get("mapping", {})
    return [key for key, ref in mapping.items() if ref == REF + name] or [name]


def _with_discriminator_meaning(
    schemas: Schema, form: str, discriminator: Schema, variants: tuple[str, ...], *, nullable: bool
) -> Schema:
    """Return the schemas as the validator reads them: the tags that name a variant added where it writes none."""
    meant = copy.deepcopy(schemas)
    if form == "schema-name":
        for name in variants:
            meant[name]["properties"]["kind"]["enum"] = [*_tags_naming(form, name, discriminator), *[None][:nullable]]
    return meant


def row(*, form: str, count: int, nullable: bool, mapping: bool, shared: bool, kind: str) -> Batch:
    """Return the batch of one row: its document, the validator's schema and the payloads of ``Pet``."""
    variants = VARIANTS[:count]
    discriminator = _discriminator(form, variants, mapping=mapping, first_only=False)
    schemas: Schema = {}
    if shared:
        schemas["Zoo"] = {
            kind: [{"$ref": REF + name} for name in VARIANTS[:2]],
            "discriminator": _discriminator(form, VARIANTS[:2], mapping=mapping, first_only=True),
        }
    if form == "inherited":
        schemas["Base"] = {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}}}
    schemas.update({name: _variant(form, name, nullable=nullable) for name in variants})
    schemas["Pet"] = {kind: [{"$ref": REF + name} for name in variants], "discriminator": discriminator}
    document = {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": {},
        "components": {"schemas": schemas},
    }
    meant = _with_discriminator_meaning(schemas, form, discriminator, variants, nullable=nullable)
    payloads: list[object] = []
    for index, name in enumerate(variants):
        other = variants[(index + 1) % count]
        for tag in _tags_naming(form, name, discriminator):
            payloads.extend([
                {"kind": tag, **OWN_VALUES[name]},
                {"kind": tag, **OTHER_VALUES[name], **OWN_VALUES[other]},
            ])
    payloads.extend([{"kind": "zzz", **OWN_VALUES["Cat"]}, {"kind": None, **OWN_VALUES["Cat"]}, OWN_VALUES["Cat"]])
    params = (
        ("form", form),
        ("count", count),
        ("nullable", nullable),
        ("mapping", mapping),
        ("shared", shared),
        ("kind", kind),
    )
    rows = (Row(params, "Pet", tuple(payloads)),)
    return Batch(TABLE, document, rows, REF, {"components": {"schemas": meant}}, "openapi")


_BOOLS: Final = (False, True)
DIMENSIONS: Final = (
    ("form", tuple(TAGS)),
    ("count", (2, 3)),
    ("nullable", _BOOLS),
    ("mapping", _BOOLS),
    ("shared", _BOOLS),
    ("kind", ("oneOf", "anyOf")),
)
"""The dimensions of a row, beside the backend."""


def batches() -> tuple[Batch, ...]:
    """Return every row of the table, in the order of its dimensions, but the nullable rows of a ``const`` form."""
    names = [name for name, _ in DIMENSIONS]
    return tuple(
        row(**params)
        for values in itertools.product(*(values for _, values in DIMENSIONS))
        if not ((params := dict(zip(names, values, strict=True)))["nullable"] and params["form"] in CONSTS)
    )
