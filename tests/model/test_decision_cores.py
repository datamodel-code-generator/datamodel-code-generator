"""Check the pure cores of the three decision tables against the oracle each decision stands for, over its domain.

The decision tables of ``tests/main/test_decision_tables.py`` read the generator's decisions through whole
generations, a document per row. The cores those decisions call are pure functions over small facts, so their domain
is enumerated here completely, against the oracle of each table (the reason this module imports private names):

* constraint applicability: the JSON kinds each keyword applies to (``keyword_kinds``) and the keywords a declared
  type holds (``declared_keywords``) against the validator, and the keywords each type takes in the msgspec output
  (``MSGSPEC_OUTPUT`` through ``listed_type_keywords``, ``key_count_keywords`` and ``meta_keywords``) against the
  ``msgspec.Meta`` arguments msgspec accepts on that type;
* null: what each parse site reads from the null sources of a schema object (``resolve_null_facts``) against the
  validator's verdict on null for the keywords the site reads (``SITE_READS``);
* the tag plan: which unions of msgspec Structs msgspec refuses (``struct_union_refusal``), which values it refuses as
  a tag (``struct_tag_refused``, ``is_tag_kind``), against msgspec itself.

A null site's answer that differs from the oracle is no failure where ``KEPT_ANSWERS`` keeps it, cell by cell (the
site, its row, the answer, the schema object, the mode, the required state and default), each with its reason (the
``legacy-*`` rows that keep today's output and the rows that read an option); the kept answers are held equal to the
differing ones both ways, so a seeded wrong answer fails, also under a kept row's name, and a fixed one is removed.
The sources a site reads are those the parser's adapter gives it: the sites that read the answer the schema object
recorded (``_RECORDED_TYPE_NULL_SITES``) get it as ``JsonSchemaParser._type_has_null`` gives it.
"""

from __future__ import annotations

import functools
import itertools
import json
import operator
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Final

import msgspec
import pytest
from jsonschema import Draft202012Validator

from datamodel_code_generator._constraint_applicability import (
    META_SCHEMA_KEYWORDS,
    MSGSPEC_OUTPUT,
    TYPE_DEPENDENT_KEYWORDS,
    DeclaredKinds,
    declared_keywords,
    key_count_keywords,
    keyword_kinds,
    listed_type_keywords,
    meta_keywords,
)
from datamodel_code_generator.model._decisions.enum_kind import is_tag_kind, literal_value_kind
from datamodel_code_generator.model._decisions.tag_value import struct_tag_refused, struct_union_refusal
from datamodel_code_generator.parser._null_facts import (
    NullMode,
    NullSite,
    NullSources,
    null_sources,
    resolve_null_facts,
)
from datamodel_code_generator.parser.jsonschema import _RECORDED_TYPE_NULL_SITES

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.main.shape_families.families import Schema

KIND_INSTANCES: Final[dict[str, tuple[object, ...]]] = {
    "string": ("", "a", "abcd"),
    "integer": (0, 2, 5),
    "number": (0.5, 2, 5.5),
    "array": ([], [1], [1, 1, 1]),
    "object": ({}, {"a": 1}, {"a": 1, "b": 2, "c": 3}),
}
"""Instances of each JSON kind on both sides of every limit of ``LIMITS``."""
LIMITS: Final[dict[str, object]] = {
    "minLength": 2,
    "maxLength": 2,
    "pattern": "^a$",
    "minimum": 1,
    "maximum": 3,
    "exclusiveMinimum": 1,
    "exclusiveMaximum": 3,
    "multipleOf": 2,
    "minItems": 2,
    "maxItems": 2,
    "uniqueItems": True,
    "minProperties": 2,
    "maxProperties": 2,
}
"""A limit of each type-dependent keyword."""
DECLARED_KINDS: Final = {"numbers": ("integer", "number"), "string": ("string",), "array": ("array",)}
"""The JSON kinds each field of ``DeclaredKinds`` declares."""
LENGTH_ACROSS_KINDS: Final = {
    "string": frozenset({"minItems", "maxItems"}),
    "array": frozenset({"minLength", "maxLength"}),
}
"""What row ``legacy-length-keywords-across-kinds`` adds to a declared string and a declared array."""


def _applies(keyword: str, kind: str) -> bool:
    """Return whether the validator rejects an instance of a JSON kind for a keyword with its limit."""
    validator = Draft202012Validator({keyword: LIMITS[keyword]})
    return not all(validator.is_valid(value) for value in KIND_INSTANCES[kind])


@pytest.mark.allow_direct_assert("A pure table of keyword kinds is compared with the validator over every keyword.")
def test_keyword_kinds_are_the_kinds_the_validator_applies_them_to() -> None:
    """Each keyword group applies to the JSON kinds ``keyword_kinds`` names, and to no other."""
    stated = {keyword: kinds for kinds, group in keyword_kinds() for keyword in group}
    applied = {keyword: frozenset(kind for kind in KIND_INSTANCES if _applies(keyword, kind)) for keyword in LIMITS}
    assert stated == applied


def _declared_cases(*, by_type: bool) -> list[tuple[DeclaredKinds, frozenset[str]]]:
    """Return every set of declared kinds with the type-dependent keywords the validator applies to them.

    Without ``by_type`` row ``legacy-length-keywords-across-kinds`` adds the item counts to a string and the lengths to
    an array.
    """
    cases = []
    for numbers, string, array in itertools.product([False, True], repeat=3):
        declared = {field for field, flag in (("numbers", numbers), ("string", string), ("array", array)) if flag}
        expected = frozenset(
            keyword
            for keyword in TYPE_DEPENDENT_KEYWORDS
            if any(_applies(keyword, kind) for field in declared for kind in DECLARED_KINDS[field])
        )
        added = (
            frozenset()
            if by_type
            else frozenset().union(*(LENGTH_ACROSS_KINDS.get(field, frozenset()) for field in declared))
        )
        kinds = DeclaredKinds(numbers=numbers, string=string, array=array, typed=bool(declared))
        cases.append((kinds, expected | added))
    return cases


@pytest.mark.allow_direct_assert("A pure core is compared with the validator over every declared kind.")
@pytest.mark.parametrize("by_type", [True, False])
def test_declared_keywords_are_the_keywords_of_the_declared_kinds(by_type: bool) -> None:
    """A declared type holds the type-dependent keywords that apply to its kinds (``_declared_cases``)."""
    assert [
        (kinds, sorted(declared_keywords(kinds, by_type=by_type) ^ expected))
        for kinds, expected in _declared_cases(by_type=by_type)
        if declared_keywords(kinds, by_type=by_type) != expected
    ] == []


MSGSPEC_TYPES: Final[dict[str, object]] = {
    "int": int,
    "float": float,
    "str": str,
    "bytes": bytes,
    "bytearray": bytearray,
    "list": list[int],
    "set": set[int],
    "frozenset": frozenset[int],
    "tuple": tuple[int, ...],
}
"""Each type path of the msgspec rows, as the type it names."""
MSGSPEC_MAPS: Final[dict[str, object]] = {
    "dict": dict[str, int],
    "collections.OrderedDict": OrderedDict[str, int],
    "collections.Counter": Counter[str],
}
"""Each mapping class by the path that classifies it, as the type it names."""


def _msgspec_takes(python_type: object, argument: str) -> bool:
    """Return whether msgspec builds a decoder for a type with a ``Meta`` argument."""
    try:
        msgspec.json.Decoder(Annotated[python_type, msgspec.Meta(**{argument: "^a" if argument == "pattern" else 1})])
    except TypeError:
        return False
    return True


def _msgspec_row(path: str) -> frozenset[str]:
    """Return the ``Meta`` arguments the msgspec rows give a type path, as a field of the type writes them."""
    if path in MSGSPEC_MAPS:
        return meta_keywords(key_count_keywords(path, frozenset(), MSGSPEC_OUTPUT.mapping_classes))
    taken = listed_type_keywords(
        path,
        type_keywords=MSGSPEC_OUTPUT.type_keywords,
        number_value_types=MSGSPEC_OUTPUT.number_value_types,
        target_number_keywords=MSGSPEC_OUTPUT.target_number_keywords,
    )
    return meta_keywords(taken or frozenset())


@pytest.mark.allow_direct_assert("A pure table row is compared with msgspec's own refusal of each Meta argument.")
@pytest.mark.parametrize("path", [*MSGSPEC_TYPES, *MSGSPEC_MAPS])
def test_msgspec_rows_are_the_meta_arguments_msgspec_takes(path: str) -> None:
    """A type of the msgspec output takes exactly the ``Meta`` arguments msgspec accepts on it."""
    python_type = {**MSGSPEC_TYPES, **MSGSPEC_MAPS}[path]
    assert _msgspec_row(path) == {
        argument for argument in META_SCHEMA_KEYWORDS if _msgspec_takes(python_type, argument)
    }


_NULLABLE: Final = frozenset({"nullable"})
_TYPE_AND_NULLABLE: Final = frozenset({"type", "nullable"})
SITE_READS: Final[dict[NullSite, frozenset[str]]] = {
    "schema-type": frozenset({"type", "anyOf"}),
    "field": _NULLABLE,
    "scalar": _TYPE_AND_NULLABLE,
    "object-model": frozenset({"type", "anyOf", "nullable"}),
    "root-model": frozenset({"type", "anyOf", "nullable"}),
    "ref-target": _TYPE_AND_NULLABLE,
    "type-union": frozenset({"type"}),
    "object-map": _TYPE_AND_NULLABLE,
    "all-of-value": _TYPE_AND_NULLABLE,
    "all-of-union": frozenset({"type", "enum", "const", "nullable"}),
    "root-type": _TYPE_AND_NULLABLE,
    "array-field": _NULLABLE,
    "enum": frozenset({"type", "enum", "nullable"}),
    "enum-literal": frozenset({"type", "enum"}),
    "combined-branch": _TYPE_AND_NULLABLE,
    "inherited-types": _NULLABLE,
    "enforced-value": _NULLABLE,
    "enum-as-const": frozenset({"enum", "nullable"}),
}
"""The keywords of a schema object each site reads its null from, as its row docstrings state them. ``nullable: true``
declares null whatever stands beside it, as the generator reads it (the null table pins where OpenAPI 3.0.3 reads it
otherwise). The sites that read more than one schema object (the parts of an
``allOf`` beyond the site's own, the branches of a ``const-enum``), a schema they rewrite (``ref-nullable-only``,
``multiple-types-root``, ``inherited-child``) or GraphQL are left to the decision tables."""

OBJECT_SITES: Final = frozenset({"object-model", "object-map"})
"""The sites that read an object schema only, which see no schema whose type excludes an object."""
ENUM_SITES: Final = frozenset({"enum", "enum-literal", "enum-as-const"})
"""The sites that read a schema with an enum only."""

MEMBERS: Final[tuple[tuple[str, ...], ...]] = (("string", "null"), ("string", "integer"))
STRING_TYPES: Final[tuple[object, ...]] = (None, "string", ["string", "null"])
"""The types an enum or a const of strings stands beside."""
MODES: Final[dict[str, NullMode]] = {
    "default": NullMode(),
    "strict": NullMode(strict_nullable=True),
    "sentinel": NullMode(use_missing_sentinel=True),
    "openapi-strict": NullMode(strict_nullable=True, nullable_as_type_list=True),
    "force-optional": NullMode(force_optional=True),
    "intersect": NullMode(type_excludes_member_null=True),
    "strict-beside-list": NullMode(strict_nullable=True, read_nullable_beside_type_list=True),
}
"""The option sets of ``NullMode`` a run can give: the default, each option alone, and OpenAPI under
``--strict-nullable`` with and without ``--read-nullable-beside-type-list``."""


def _schemas() -> Iterator[Schema]:
    """Yield the schema objects of the null domain, as ordinary documents write them.

    A type (none, string, object, or a type list of string or object with null; a lone type null is the None type,
    which the type mapping writes), the members of an ``anyOf`` beside no type, OpenAPI's ``nullable`` beside a type
    that is one string or none, and an enum or a const of strings beside a string type or none, the enum with or
    without null.
    """
    for type_ in (None, "string", "object", ["string", "null"], ["object", "null"]):
        for nullable in (None, True, False) if not isinstance(type_, list) else (None,):
            base: Schema = {} if type_ is None else {"type": type_}
            if nullable is not None:
                base["nullable"] = nullable
            yield base
            if type_ is None:
                yield from ({**base, "anyOf": [{"type": member} for member in members]} for members in MEMBERS)
            if type_ in STRING_TYPES:
                yield from ({**base, "enum": list(enum)} for enum in (("a",), ("a", None)))
                yield {**base, "const": "a"}


def _declares_null(schema: Schema, reads: frozenset[str]) -> bool:
    """Return whether the keywords of ``schema`` a site reads declare null, by the validator's verdict on null.

    ``nullable: true`` declares it in place of the type, so only an enum or a const beside it can exclude it; keywords
    that constrain nothing declare no null.
    """
    read = {key: schema[key] for key in ("type", "anyOf", "enum", "const") if key in reads and key in schema}
    if "nullable" in reads and schema.get("nullable") is True:
        read = {key: value for key, value in read.items() if key in {"enum", "const"}}
        return not read or Draft202012Validator(read).is_valid(None)
    return bool(read) and Draft202012Validator(read).is_valid(None)


def _read_by(site: NullSite, schema: Schema) -> bool:
    """Return whether a site reads a schema object.

    An object site reads one whose type admits an object, an enum site one with an enum, the scalar site one whose type
    is no list (the type-union site reads those), the others every one.
    """
    type_ = schema.get("type")
    may_be_object = type_ is None or type_ == "object" or (isinstance(type_, list) and "object" in type_)
    return (
        (site not in OBJECT_SITES or may_be_object)
        and (site not in ENUM_SITES or "enum" in schema)
        and (site != "scalar" or not isinstance(type_, list))
    )


def _sources(schema: Schema, site: NullSite, mode: NullMode) -> NullSources:
    """Return the null sources of a schema object as the parser's adapter gives them to a site.

    The sites of ``_RECORDED_TYPE_NULL_SITES`` read the answer ``JsonSchemaObject.type_has_null`` records (the
    ``schema-type`` row under no option), false where the mode's ``type`` excludes the null of the members
    (``JsonSchemaParser._type_has_null``); the others read none.
    """
    written = null_sources(
        schema.get("type"),
        nullable=schema.get("nullable"),
        type_has_null=False,
        member_types=[member["type"] for member in schema.get("anyOf", [])],
        enum=schema.get("enum"),
        const=(schema["const"],) if "const" in schema else (),
    )
    excluded = resolve_null_facts(written, "schema-type", mode).rule == "type-excludes-member-null"
    recorded = bool(resolve_null_facts(written, "schema-type").admits_null) and not excluded
    return null_sources(
        schema.get("type"),
        nullable=schema.get("nullable"),
        type_has_null=site in _RECORDED_TYPE_NULL_SITES and recorded,
        member_types=[member["type"] for member in schema.get("anyOf", [])],
        enum=schema.get("enum"),
        const=(schema["const"],) if "const" in schema else (),
    )


Cell = tuple[str, str, bool, str, str, bool, bool]
"""A site answer: the site, its row, the answer, the schema object (sorted JSON), the mode, required, has a default."""


def _key(schema: Schema) -> str:
    return json.dumps(schema, sort_keys=True)


def _differing_answers() -> set[Cell]:
    """Return every site answer that differs from the validator's verdict on the keywords the site reads."""
    return {
        (site, facts.rule, bool(facts.admits_null), _key(schema), name, required, has_default)
        for schema in _schemas()
        for site, reads in SITE_READS.items()
        if _read_by(site, schema)
        for (name, mode), required, has_default in itertools.product(MODES.items(), (False, True), (False, True))
        if (
            facts := resolve_null_facts(
                _sources(schema, site, mode), site, mode, required=required, has_default=has_default
            )
        ).admits_null
        not in {None, _declares_null(schema, reads)}
    }


@dataclass(frozen=True)
class KeptAnswer:
    """Site answers that differ from the validator and are kept: the row, its answer, the inputs, and why."""

    site: NullSite
    rule: str
    answer: bool
    schemas: tuple[Schema, ...]
    modes: tuple[str, ...]
    reason: str

    def cells(self) -> set[Cell]:
        """Return the answers the entry keeps, under every required state and default."""
        return {
            (self.site, self.rule, self.answer, _key(schema), mode, required, has_default)
            for schema in self.schemas
            for mode in self.modes
            for required, has_default in itertools.product((False, True), (False, True))
        }


_STRING_MEMBERS: Final = [{"type": "string"}, {"type": "integer"}]
_NULL_MEMBERS: Final = [{"type": "string"}, {"type": "null"}]
_NULLABLE_BESIDE_NO_TYPE: Final[tuple[Schema, ...]] = (
    {"nullable": True},
    {"nullable": True, "anyOf": _STRING_MEMBERS},
    {"nullable": True, "enum": ["a"]},
    {"nullable": True, "enum": ["a", None]},
    {"nullable": True, "const": "a"},
)
_NULLABLE_NULL_MEMBER: Final[Schema] = {"nullable": True, "anyOf": _NULL_MEMBERS}
_NULLABLE_OBJECT: Final[Schema] = {"type": "object", "nullable": True}
_NULLABLE_STRINGS: Final[tuple[Schema, ...]] = (
    {"type": "string", "nullable": True},
    {"type": "string", "nullable": True, "enum": ["a"]},
    {"type": "string", "nullable": True, "enum": ["a", None]},
    {"type": "string", "nullable": True, "const": "a"},
)
_TYPE_LISTS: Final[tuple[Schema, ...]] = (
    {"type": ["object", "null"]},
    {"type": ["string", "null"]},
    {"type": ["string", "null"], "enum": ["a"]},
    {"type": ["string", "null"], "enum": ["a", None]},
    {"type": ["string", "null"], "const": "a"},
)
_UNTYPED: Final[tuple[Schema, ...]] = (
    {},
    {"nullable": False},
    {"anyOf": _STRING_MEMBERS},
    {"anyOf": _NULL_MEMBERS},
    {"nullable": False, "anyOf": _STRING_MEMBERS},
    {"nullable": False, "anyOf": _NULL_MEMBERS},
)
_ALL_MODES: Final = tuple(MODES)
_WITHOUT_STRICT_NULLABLE: Final = tuple(name for name, mode in MODES.items() if not mode.strict_nullable)

KEPT_ANSWERS: Final[tuple[KeptAnswer, ...]] = (
    KeptAnswer(
        "root-model",
        "legacy-root-model-type-only",
        answer=False,
        schemas=(*_NULLABLE_BESIDE_NO_TYPE, _NULLABLE_OBJECT, *_NULLABLE_STRINGS),
        modes=_ALL_MODES,
        reason="a root model reads no nullable",
    ),
    KeptAnswer(
        "ref-target",
        "legacy-ref-target-null-type-only",
        answer=False,
        schemas=_TYPE_LISTS,
        modes=_ALL_MODES,
        reason="a reference reads no type list of its target",
    ),
    KeptAnswer(
        "ref-target",
        "legacy-ref-target-null-type-only",
        answer=False,
        schemas=(*_NULLABLE_BESIDE_NO_TYPE, _NULLABLE_NULL_MEMBER, _NULLABLE_OBJECT, *_NULLABLE_STRINGS),
        modes=_WITHOUT_STRICT_NULLABLE,
        reason="a reference reads its target's nullable only under --strict-nullable (#1496)",
    ),
    KeptAnswer(
        "scalar",
        "scalar-not-null",
        answer=False,
        schemas=(*_NULLABLE_BESIDE_NO_TYPE, _NULLABLE_NULL_MEMBER, _NULLABLE_OBJECT, *_NULLABLE_STRINGS),
        modes=_WITHOUT_STRICT_NULLABLE,
        reason="OpenAPI's nullable admits null only under --strict-nullable (#1496)",
    ),
    KeptAnswer(
        "object-map",
        "map-strict-nullable",
        answer=False,
        schemas=(*_NULLABLE_BESIDE_NO_TYPE, _NULLABLE_NULL_MEMBER, _NULLABLE_OBJECT),
        modes=_WITHOUT_STRICT_NULLABLE,
        reason="OpenAPI's nullable admits null only under --strict-nullable (#1496)",
    ),
    KeptAnswer(
        "object-model",
        "object-type-or-strict-nullable",
        answer=False,
        schemas=(*_NULLABLE_BESIDE_NO_TYPE, _NULLABLE_OBJECT),
        modes=_WITHOUT_STRICT_NULLABLE,
        reason="OpenAPI's nullable admits null only under --strict-nullable (#1496)",
    ),
    KeptAnswer(
        "enum",
        "enum-null-as-nullable",
        answer=True,
        schemas=({"type": "string", "enum": ["a", None]}, {"type": "string", "nullable": False, "enum": ["a", None]}),
        modes=_ALL_MODES,
        reason="the null of a string enum makes its class nullable, though the string type excludes null",
    ),
    KeptAnswer(
        "enum-literal",
        "legacy-enum-literal-value-only",
        answer=True,
        schemas=(
            {"type": "string", "enum": ["a", None]},
            {"type": "string", "nullable": False, "enum": ["a", None]},
            {"type": "string", "nullable": True, "enum": ["a", None]},
        ),
        modes=_ALL_MODES,
        reason="a Literal enum reads its null value without the type",
    ),
    KeptAnswer(
        "enum-as-const",
        "nullable-enum-not-const",
        answer=True,
        schemas=({"nullable": True, "enum": ["a"]}, {"type": "string", "nullable": True, "enum": ["a"]}),
        modes=_ALL_MODES,
        reason="nullable beside a one-value enum admits null (the null table's nullable-beside-enum)",
    ),
    KeptAnswer(
        "all-of-union",
        "all-of-union-part-admits-null",
        answer=True,
        schemas=_UNTYPED,
        modes=_ALL_MODES,
        reason="a part that constrains no type admits null by its own keywords",
    ),
)
"""The site answers that differ from the validator and are kept, each with its inputs and why."""


@pytest.mark.allow_direct_assert("A pure core is compared with the validator over a finite domain of schema objects.")
def test_null_sites_read_the_validator_s_verdict() -> None:
    """Every site's answer is the validator's verdict on null for the keywords it reads, or a kept answer.

    The kept answers are exactly the differing ones, cell by cell: a new difference under a kept row's name fails, and
    so does a kept answer that agrees again until its entry is narrowed or removed.
    """
    differing = _differing_answers()
    kept = set().union(*(entry.cells() for entry in KEPT_ANSWERS))
    assert sorted(differing - kept)[:20] == []
    assert sorted(kept - differing)[:20] == []


TAG_SPECS: Final[tuple[tuple[str, object] | None, ...]] = (
    None,
    *itertools.product(("kind", "type"), ("a", "b", 1, 2)),
)
"""The tags a Struct of a union can have: none, or a tag field with a str or an int tag."""


def _msgspec_refuses(specs: tuple[tuple[str, object] | None, ...]) -> bool:
    """Return whether msgspec refuses the union of distinct Structs with these tags."""
    structs = [
        msgspec.defstruct(
            f"S{index}", [("x", int, 0)], **({} if spec is None else {"tag_field": spec[0], "tag": spec[1]})
        )
        for index, spec in enumerate(specs)
    ]
    try:
        msgspec.json.Decoder(functools.reduce(operator.or_, structs))
    except TypeError:
        return True
    return False


@pytest.mark.allow_direct_assert("A pure core is compared with msgspec over every union of up to three Structs.")
def test_struct_union_refusal_is_msgspec_s() -> None:
    """``struct_union_refusal`` refuses a union of one to three Structs exactly where msgspec does."""
    assert [
        specs
        for size in (1, 2, 3)
        for specs in itertools.product(TAG_SPECS, repeat=size)
        if (struct_union_refusal(specs) is not None) != _msgspec_refuses(specs)
    ] == []


TAG_VALUES: Final = ("a", "", 0, 1, -1, 2**63 - 1, 2**63, -(2**63), -(2**63) - 1, 1.5, True)
"""Tag values of every kind a ``Literal`` writes, an int on each side of each 64-bit bound."""


def _msgspec_takes_tag(value: object) -> bool:
    try:
        msgspec.defstruct("S", [], tag=value)
    except (TypeError, ValueError):
        return False
    return True


@pytest.mark.allow_direct_assert("Pure predicates are compared with msgspec's own refusal of each tag value.")
def test_struct_tag_values_are_the_ones_msgspec_takes() -> None:
    """A value is a msgspec tag where its kind is a tag kind and ``struct_tag_refused`` keeps it, as msgspec takes it.

    A bool is the one exception: msgspec takes ``True`` as the int 1, which is not the JSON ``true`` a schema means,
    so the generator gives it no bool tag.
    """
    assert [
        (type(value), value)
        for value in TAG_VALUES
        if (is_tag_kind(literal_value_kind(value)) and not struct_tag_refused(value)) != _msgspec_takes_tag(value)
    ] == [(bool, True)]
