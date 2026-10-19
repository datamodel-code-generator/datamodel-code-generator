"""Constraint applicability: which schema constraint keywords each emitted type takes, in one table.

The rows are static: the keyword groups of each JSON kind, the keywords a declared schema type holds, the keywords
each Python or library type takes in each output model, the classes that enforce a keyword themselves, the keywords
the ``con*`` types and ``msgspec.Meta`` take, and the rows that keep today's answer where two sites disagree
(``legacy-*``; a fix of that output deletes its row). Every function is pure: facts in, keywords out. The adapters
read the live objects: ``types.py:DataTypeManager.constraint_keywords`` and ``type_constraint_keywords`` (emitted
types), ``parser/jsonschema.py:JsonSchemaParser._get_constraint_values`` (the L1 split),
``parser/base.py:Parser._decide_overridden_constraints`` (``--type-overrides``) and
``model/msgspec.py:DataModelField._meta_keywords`` (``msgspec.Meta``).

Legacy rows:

* ``legacy-declared-string-keywords-kept``: a keyword of a declared string schema stays on the field whatever type is
  emitted (``date-time`` + ``maxLength`` writes ``max_length`` on a datetime); only number keywords ask the emitted
  type (``number_keywords_taken``).
* ``legacy-consumed-at-object-property``: a field leaves out the keyword its zero-bound class enforces
  (``OutputKeywords.zero_bound_classes``) only at an object property whose own type is that class, not at a root, an
  item or a union member.
* ``legacy-all-of-root-format-kinds``: an ``allOf`` root merged with constraints keeps the keywords of the kinds
  ``all_of_root_keyword_kinds`` names for its format, while the field split keeps the declared string keywords.

This module is a leaf (the standard library, ``typing_extensions``, ``enums`` and ``_requirements``): ``types.py``
builds the public tables of ``DataTypeManager`` from it while it is imported, and the model package imports
``types.py``, so the table cannot live under ``model/``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from typing_extensions import final

from datamodel_code_generator._requirements import pydantic_target_meets

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.enums import TargetPydanticVersion

__all__ = [
    "ARRAY_KEYWORDS",
    "BASE64_BYTES_CARRIED_KEYWORDS",
    "BASE_OUTPUT",
    "LENGTH_KEYWORDS",
    "LENGTH_KEYWORD_ORDER",
    "LENGTH_META_KEYWORDS",
    "META_SCHEMA_KEYWORDS",
    "MSGSPEC_OUTPUT",
    "NO_KEYWORDS",
    "NUMBER_BOUND_KEYWORDS",
    "NUMBER_KEYWORDS",
    "NUMBER_KEYWORD_ORDER",
    "PROPERTY_COUNT_KEYWORDS",
    "PYDANTIC_OUTPUT",
    "SEQUENCE_KEYWORDS",
    "STRING_KEYWORDS",
    "STRING_KEYWORD_ORDER",
    "STRING_LENGTH_KEYWORD_ORDER",
    "TAG_LITERAL_UNTAKEN_KEYWORD",
    "TYPE_DEPENDENT_KEYWORDS",
    "OutputKeywords",
    "all_of_root_keyword_kinds",
    "constrained_type_keyword_order",
    "declared_keywords",
    "holds_number_format",
    "keyword_kinds",
    "listed_type_keywords",
    "literal_number_keywords",
    "member_keywords",
    "meta_keywords",
    "number_keywords_taken",
    "overridden_number_constraints",
    "resplit_overridden_constraints",
    "shape_keywords",
    "shape_meta_keywords",
    "unlisted_type_keywords",
    "untaken_minimums",
]

NUMBER_KEYWORD_ORDER: Final[tuple[str, ...]] = (
    "minimum",
    "maximum",
    "multipleOf",
    "exclusiveMaximum",
    "exclusiveMinimum",
)
"""The number keywords, in the order the ``con*`` arguments and the untaken-constraint warnings list them."""
STRING_KEYWORD_ORDER: Final[tuple[str, ...]] = ("pattern", "minLength", "maxLength")
"""The string keywords, in the order a string schema lists them."""
LENGTH_KEYWORD_ORDER: Final[tuple[str, ...]] = ("minLength", "maxLength")
"""The length keywords, in the order the ``conbytes`` arguments list them."""
STRING_LENGTH_KEYWORD_ORDER: Final[tuple[str, ...]] = (*STRING_KEYWORD_ORDER, "minItems", "maxItems")
"""The keywords a ``constr`` takes from a string schema, which reads ``minItems`` and ``maxItems`` as its lengths."""

NUMBER_KEYWORDS: Final[frozenset[str]] = frozenset(NUMBER_KEYWORD_ORDER)
NUMBER_BOUND_KEYWORDS: Final[frozenset[str]] = NUMBER_KEYWORDS - {"multipleOf"}
LENGTH_KEYWORDS: Final[frozenset[str]] = frozenset(LENGTH_KEYWORD_ORDER)
STRING_KEYWORDS: Final[frozenset[str]] = frozenset(STRING_KEYWORD_ORDER)
ARRAY_KEYWORDS: Final[frozenset[str]] = frozenset({"minItems", "maxItems", "uniqueItems"})
SEQUENCE_KEYWORDS: Final[frozenset[str]] = ARRAY_KEYWORDS | LENGTH_KEYWORDS
"""The keywords a sequence takes: the array keywords and the lengths, which count its items."""
PROPERTY_COUNT_KEYWORDS: Final[frozenset[str]] = frozenset({"maxProperties", "minProperties"})
TYPE_DEPENDENT_KEYWORDS: Final[frozenset[str]] = NUMBER_KEYWORDS | STRING_KEYWORDS | ARRAY_KEYWORDS
"""The keywords whose meaning depends on the type of the value; a type the generator cannot classify takes them all."""
NO_KEYWORDS: Final[frozenset[str]] = frozenset()

_KEYWORD_KINDS: Final[tuple[tuple[frozenset[str], frozenset[str]], ...]] = (
    (frozenset({"string"}), STRING_KEYWORDS),
    (frozenset({"integer", "number"}), NUMBER_KEYWORDS),
    (frozenset({"array"}), ARRAY_KEYWORDS),
    (frozenset({"object"}), PROPERTY_COUNT_KEYWORDS),
)
_DECLARED_STRING_KEYWORDS: Final[frozenset[str]] = STRING_KEYWORDS | {"minItems", "maxItems"}

_NUMBER_TYPES: Final[frozenset[str]] = frozenset({
    "int32",
    "int64",
    "integer",
    "float",
    "double",
    "number",
    "time",
    "decimal",
})
"""The mapped types (``types.py:Types`` names) a ``con*`` number type is built for."""
_NUMBER_STRING_FORMAT_TYPES: Final[frozenset[str]] = _NUMBER_TYPES - {"time"}
"""The mapped types whose string format holds a number, so a string schema of the format takes number keywords."""
_NO_STRING_KEYWORD_FORMAT_TYPES: Final[frozenset[str]] = frozenset({
    "date",
    "date_time",
    "date_time_local",
    "time",
    "timedelta",
    "uuid",
    "uuid1",
    "uuid2",
    "uuid3",
    "uuid4",
    "uuid5",
    "ipv4",
    "ipv6",
    "ipv4_network",
    "ipv6_network",
})
_STRING_FORMAT_TYPES: Final[frozenset[str]] = frozenset({"string", "byte", "binary", "password", "email", "hostname"})

BASE64_BYTES_CARRIED_KEYWORDS: Final[frozenset[str]] = LENGTH_KEYWORDS
"""The keywords that bytes decoded from base64 text write inside their own annotation, in front of the decoding, so a
field of the type leaves them out of its own constraints (``CARRIED_CONSTRAINT_KEYWORDS``)."""

TAG_LITERAL_UNTAKEN_KEYWORD: Final = "pattern"
"""The keyword a discriminator tag's ``Literal`` leaves out in an output model that writes ``Field()`` arguments, once
every tag value of the union satisfies it: Pydantic checks it by validating the tag again as a ``str``, which hides
the ``Literal`` from the discriminator."""

META_SCHEMA_KEYWORDS: Final[Mapping[str, str]] = MappingProxyType({
    "gt": "exclusiveMinimum",
    "ge": "minimum",
    "lt": "exclusiveMaximum",
    "le": "maximum",
    "multiple_of": "multipleOf",
    "min_length": "minLength",
    "max_length": "maxLength",
    "pattern": "pattern",
})
"""The ``msgspec.Meta`` arguments and the schema keyword each checks."""
LENGTH_META_KEYWORDS: Final[frozenset[str]] = frozenset({"min_length", "max_length"})

_SEQUENCE_TYPE_KEYWORDS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    dict.fromkeys(("list", "set", "frozenset", "tuple"), SEQUENCE_KEYWORDS)
)
_BASE_TYPE_KEYWORDS: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    **dict.fromkeys(("int", "float", "decimal.Decimal"), NUMBER_KEYWORDS),
    **dict.fromkeys(("datetime.timedelta", "datetime.datetime", "datetime.time"), NUMBER_BOUND_KEYWORDS),
    **dict.fromkeys(("str", "bytes"), STRING_KEYWORDS),
    **_SEQUENCE_TYPE_KEYWORDS,
    **dict.fromkeys(
        (
            "collections.deque",
            "collections.abc.Sized",
            "collections.abc.Iterable",
            "collections.abc.Generator",
        ),
        SEQUENCE_KEYWORDS,
    ),
    **dict.fromkeys(("collections.abc.ByteString", "typing.AnyStr"), STRING_KEYWORDS),
})
_PYDANTIC_TYPE_KEYWORDS: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    **_BASE_TYPE_KEYWORDS,
    **dict.fromkeys(
        (
            f"pydantic.{name}"
            for name in (
                "conint",
                "confloat",
                "condecimal",
                "StrictInt",
                "StrictFloat",
                "PositiveInt",
                "NegativeInt",
                "NonNegativeInt",
                "NonPositiveInt",
                "PositiveFloat",
                "NegativeFloat",
                "NonNegativeFloat",
                "NonPositiveFloat",
            )
        ),
        NUMBER_KEYWORDS,
    ),
    **dict.fromkeys(
        (f"pydantic.{name}" for name in ("AwareDatetime", "NaiveDatetime", "PastDatetime", "FutureDatetime")),
        NUMBER_BOUND_KEYWORDS,
    ),
    **dict.fromkeys((f"pydantic.{name}" for name in ("constr", "StrictStr", "StrictBytes")), STRING_KEYWORDS),
    **dict.fromkeys((f"pydantic.{name}" for name in ("conbytes", "EmailStr", "SecretStr")), LENGTH_KEYWORDS),
})
_MSGSPEC_TYPE_KEYWORDS: Final[Mapping[str, frozenset[str]]] = MappingProxyType({
    **dict.fromkeys(("int", "float"), NUMBER_KEYWORDS),
    "str": STRING_KEYWORDS,
    **dict.fromkeys(("bytes", "bytearray"), LENGTH_KEYWORDS),
    **_SEQUENCE_TYPE_KEYWORDS,
})
_BASE_NUMBER_VALUE_TYPES: Final[frozenset[str]] = frozenset({
    "bool",
    "object",
    "typing.Any",
    "typing.Hashable",
    "collections.abc.Hashable",
    "enum.IntEnum",
    "enum.IntFlag",
    *(f"typing.Supports{name}" for name in ("Abs", "Complex", "Float", "Index", "Int", "Round")),
})
_ZERO_BOUND_CLASSES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "PositiveInt": ("exclusiveMinimum",),
    "NegativeInt": ("exclusiveMaximum",),
    "NonNegativeInt": ("minimum",),
    "NonPositiveInt": ("maximum",),
    "PositiveFloat": ("exclusiveMinimum",),
    "NegativeFloat": ("exclusiveMaximum",),
    "NonNegativeFloat": ("minimum",),
    "NonPositiveFloat": ("maximum",),
})


@final
@dataclass(frozen=True, slots=True)
class OutputKeywords:
    """The constraint rows of one output model's type manager; its public ``ClassVar`` tables read them.

    ``type_keywords`` maps a type path to the keywords the output model enforces on the type across its supported
    versions. A type of ``number_value_types`` holds numbers as values, so it meets the number keywords of a number
    schema. ``target_number_keywords`` adds, by type path, the number keywords a type takes once an explicit target
    model version has the construct. ``annotation_unions_take`` tells that a union written by a Python type annotation
    takes what every member takes (else none), ``fixed_tuples_take_length`` that a fixed tuple takes the sequence
    keywords, and ``literals_take`` that a ``Literal`` takes the keywords its values take. ``zero_bound_classes`` maps
    a class built for one zero bound to the keyword it enforces itself (row ``legacy-consumed-at-object-property``),
    and ``writes_constrained_types`` tells that the type manager writes scalar constraints into ``con*`` types.
    """

    type_keywords: Mapping[str, frozenset[str]]
    number_value_types: frozenset[str]
    target_number_keywords: Mapping[str, tuple[PydanticConstruct, frozenset[str]]]
    annotation_unions_take: bool
    fixed_tuples_take_length: bool
    literals_take: bool
    zero_bound_classes: Mapping[str, tuple[str, ...]]
    writes_constrained_types: bool


BASE_OUTPUT: Final = OutputKeywords(
    type_keywords=_BASE_TYPE_KEYWORDS,
    number_value_types=_BASE_NUMBER_VALUE_TYPES,
    target_number_keywords=MappingProxyType({}),
    annotation_unions_take=True,
    fixed_tuples_take_length=True,
    literals_take=True,
    zero_bound_classes=MappingProxyType({}),
    writes_constrained_types=False,
)
"""The rows of the type manager of the standard-library outputs (dataclasses, TypedDict)."""
PYDANTIC_OUTPUT: Final = replace(
    BASE_OUTPUT,
    type_keywords=_PYDANTIC_TYPE_KEYWORDS,
    number_value_types=_BASE_NUMBER_VALUE_TYPES | {"pydantic.StrictBool"},
    target_number_keywords=MappingProxyType({
        "datetime.timedelta": ("pydantic.timedelta_multiple_of", frozenset({"multipleOf"})),
    }),
    zero_bound_classes=_ZERO_BOUND_CLASSES,
    writes_constrained_types=True,
)
"""The rows of the Pydantic type manager: its constrained and strict types, and ``multipleOf`` on a ``timedelta``."""
MSGSPEC_OUTPUT: Final = replace(
    BASE_OUTPUT,
    type_keywords=_MSGSPEC_TYPE_KEYWORDS,
    annotation_unions_take=False,
    fixed_tuples_take_length=False,
    literals_take=False,
)
"""The rows of the msgspec type manager, as the msgspec type table lists what ``msgspec.Meta`` checks."""


def keyword_kinds() -> tuple[tuple[frozenset[str], frozenset[str]], ...]:
    """Return the JSON types each group of type-dependent keywords applies to, with the group.

    A keyword applies to the instances of its kind only (JSON Schema): the string keywords to strings, the number
    keywords to integers and numbers, the array keywords to arrays and the property counts to objects.
    """
    return _KEYWORD_KINDS


def declared_keywords(*, numbers: bool, string: bool, array: bool) -> frozenset[str]:
    """Return the type-dependent keywords whose kind a declared schema type holds.

    ``numbers`` tells that a declared type holds numbers (a number, an integer or a numeric string format). A declared
    string also holds ``minItems`` and ``maxItems`` and a declared array ``minLength`` and ``maxLength``, which are
    written as their length bounds.
    """
    return frozenset().union(
        NUMBER_KEYWORDS if numbers else NO_KEYWORDS,
        _DECLARED_STRING_KEYWORDS if string else NO_KEYWORDS,
        SEQUENCE_KEYWORDS if array else NO_KEYWORDS,
    )


def number_keywords_taken(declared: frozenset[str], taken: frozenset[str]) -> frozenset[str]:
    """Return the declared keywords of a field with only the number keywords that a number value of its type meets.

    An output model that writes constraints as ``Field()`` arguments asks so (``taken`` is what a number value of the
    emitted type meets), as ``str`` or ``date`` hold no number; the other declared keywords stay (row
    ``legacy-declared-string-keywords-kept``).
    """
    return (declared - NUMBER_KEYWORDS) | (taken & NUMBER_KEYWORDS)


def holds_number_format(mapped_type: str) -> bool:
    """Return whether a string format mapped to the type (a ``types.py:Types`` name) holds a number."""
    return mapped_type in _NUMBER_STRING_FORMAT_TYPES


def constrained_type_keyword_order(mapped_type: str) -> tuple[str, ...]:
    """Return the keywords a ``con*`` type of the mapped type (a ``types.py:Types`` name) takes, in argument order."""
    if mapped_type in _NUMBER_TYPES:
        return NUMBER_KEYWORD_ORDER
    if mapped_type == "string":
        return STRING_LENGTH_KEYWORD_ORDER
    if mapped_type == "binary":
        return LENGTH_KEYWORD_ORDER
    return ()


def all_of_root_keyword_kinds(schema_type: str, mapped_type: str) -> frozenset[str] | None:
    """Return the JSON kinds whose keywords an ``allOf`` root keeps for its format, or None to keep its schema type's.

    A string format mapped to a date, time, UUID or IP type keeps no keyword, one mapped to a number type keeps the
    number keywords and one mapped to a string type the string keywords (row ``legacy-all-of-root-format-kinds``).
    """
    if schema_type == "string" and mapped_type in _NO_STRING_KEYWORD_FORMAT_TYPES:
        return NO_KEYWORDS
    if mapped_type in _NUMBER_TYPES:
        return frozenset({"number"})
    if mapped_type in _STRING_FORMAT_TYPES:
        return frozenset({"string"})
    return None


def shape_keywords(*, mapping: bool, sequence: bool, reference: bool) -> frozenset[str] | None:
    """Return the keywords a container or a referenced model takes, or None when the type itself decides.

    A mapping takes none, a sequence the sequence keywords, and a referenced model all of them, as its own root type
    decides them.
    """
    if mapping:
        return NO_KEYWORDS
    if sequence:
        return SEQUENCE_KEYWORDS
    return TYPE_DEPENDENT_KEYWORDS if reference else None


def member_keywords(members: Iterable[frozenset[str]], *, number_values: bool) -> frozenset[str]:
    """Return the keywords a union takes from those of its members.

    Every member must take a keyword, as a value validates as one member only; a number value meets the keywords one
    member takes. A union of no member takes none.
    """
    keywords = tuple(members)
    combine = frozenset.union if number_values else frozenset.intersection
    return combine(*keywords) if keywords else NO_KEYWORDS


def literal_number_keywords(*, literals_take: bool, has_non_string_value: bool) -> frozenset[str]:
    """Return the keywords a number value of the ``Literal`` of a const or an enum meets.

    It meets the number keywords when the output model checks keywords on a ``Literal`` and one value is no string.
    """
    return NUMBER_KEYWORDS if literals_take and has_non_string_value else NO_KEYWORDS


def listed_type_keywords(  # noqa: PLR0913
    path: str,
    /,
    *,
    type_keywords: Mapping[str, frozenset[str]],
    number_value_types: frozenset[str],
    target_number_keywords: Mapping[str, tuple[PydanticConstruct, frozenset[str]]],
    number_values: bool = False,
    target_model_version: TargetPydanticVersion | str | None = None,
) -> frozenset[str] | None:
    """Return the keywords that a type manager's table lists for a type path, or None for a type it does not list.

    The tables are the type manager's (``OutputKeywords`` rows, or a subclass's own). With ``number_values`` a type of
    ``number_value_types`` takes the number keywords, and a type of ``target_number_keywords`` also those an explicit
    ``target_model_version`` reaches.
    """
    if number_values:
        if path in number_value_types:
            return NUMBER_KEYWORDS
        if (added := target_number_keywords.get(path)) is not None and pydantic_target_meets(
            added[0], target_model_version
        ):
            return type_keywords.get(path, NO_KEYWORDS) | added[1]
    return type_keywords.get(path)


def unlisted_type_keywords(*, known: bool) -> frozenset[str]:
    """Return the keywords a type the table does not list takes: none for a known type, else all of them.

    A known type is a Python builtin or a standard-library type the target has, or a type the generator imports
    itself; a type the generator cannot classify keeps the keywords the schema declares.
    """
    return NO_KEYWORDS if known else TYPE_DEPENDENT_KEYWORDS


def shape_meta_keywords(*, mapping: bool, unconstrained: bool) -> frozenset[str] | None:
    """Return the ``msgspec.Meta`` arguments a mapping or an unconstrained shape takes, or None when its type decides.

    A mapping takes the lengths, and a fixed tuple or a union (``unconstrained``) takes none.
    """
    if mapping:
        return LENGTH_META_KEYWORDS
    return NO_KEYWORDS if unconstrained else None


def meta_keywords(taken: frozenset[str]) -> frozenset[str]:
    """Return the ``msgspec.Meta`` arguments that check the schema keywords a type takes."""
    return frozenset(meta for meta, schema in META_SCHEMA_KEYWORDS.items() if schema in taken)


def overridden_number_constraints(
    values: Mapping[str, Any], *, type_dependent: Mapping[str, Any], untaken: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the number constraints of a declared number that an override type decides again.

    They are the field's number constraints the schema type decided (not those ``type_dependent`` remembers as
    decided by the emitted type) and the ``untaken`` ones it was generated without.
    """
    return {
        **{key: value for key, value in values.items() if key in NUMBER_KEYWORDS and key not in type_dependent},
        **untaken,
    }


def resplit_overridden_constraints(
    values: Mapping[str, Any],
    *,
    type_dependent: Mapping[str, Any],
    declared: Mapping[str, Any],
    taken: frozenset[str],
    declared_taken: frozenset[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the constraints a field keeps once ``--type-overrides`` replaces its type, and those it leaves out.

    The constraints the emitted type decided (``type_dependent``, kept when the override type takes them, ``taken``)
    and the ``declared`` ones (kept when a number value of the override type meets them, ``declared_taken``) are
    decided again; the other constraints stay. The declared ones it does not meet are left out, to warn about.
    """
    kept = {key: value for key, value in values.items() if key not in type_dependent and key not in declared}
    kept.update({key: value for key, value in type_dependent.items() if key in taken})
    kept.update({key: value for key, value in declared.items() if key in declared_taken})
    return kept, {key: value for key, value in declared.items() if key not in declared_taken}


def untaken_minimums(untaken: Iterable[str], taken_by_minimum: Iterable[tuple[str, frozenset[str]]]) -> dict[str, str]:
    """Return, for each untaken keyword that a later target model version takes, the first such version.

    ``taken_by_minimum`` gives, from the oldest version, each version that adds a keyword and what it takes then.
    """
    keys = tuple(untaken)
    minimums: dict[str, str] = {}
    for minimum, taken in taken_by_minimum:
        minimums.update({key: minimum for key in keys if key in taken and key not in minimums})
    return minimums
