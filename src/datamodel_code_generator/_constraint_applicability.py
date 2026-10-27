"""Constraint applicability: which schema constraint keywords each emitted type takes, in one table.

The rows are static: the keyword groups of each JSON kind, the keywords a declared schema type holds, the keywords
each Python or library type takes in each output model, the classes that enforce a keyword themselves, the keywords
the ``con*`` types and ``msgspec.Meta`` take, and the rows that keep today's answer where two sites disagree
(``legacy-*``; a fix of that output deletes its row). Every function is pure: facts in, keywords out. The adapters
read the live objects: ``types.py:DataTypeManager.constraint_keywords`` and ``type_constraint_keywords`` (emitted
types), ``parser/jsonschema.py:JsonSchemaParser._split_constraint_values`` and ``_keywords_taken`` (the L1 split),
``parser/base.py:Parser._decide_overridden_constraints`` (``--type-overrides``) and
``model/msgspec.py:DataModelField._meta_keywords`` (``msgspec.Meta``).

Legacy rows, each named by the function that decides it:

* ``legacy-consumed-at-object-property`` (``property_consumed_keywords``): a field leaves out the keyword its zero-bound
  class enforces (``OutputKeywords.zero_bound_classes``) only at an object property whose own type is that class, not
  at a root, an item or a union member.
* ``legacy-all-of-root-format-kinds`` (``all_of_root_keyword_kinds``): an ``allOf`` root merged with constraints keeps
  the keywords of the kinds its format names, silently, before the field split decides the declared ones by the
  emitted type.
* ``legacy-undeclared-keywords-off-literal`` (``undeclared_keywords_taken``): the ``Literal`` of a ``const`` or an
  ``enum`` takes no keyword of a kind its schema does not declare, though it takes those of its values' type for a
  declared one. The literal check (``literal_check``) decides such a keyword on the values where it can, so the row
  keeps only the keywords it cannot decide, such as ``pattern``, off the field.
* ``legacy-override-keeps-string-keywords`` (``overridden_declared_constraints``; G03b removes it):
  ``--type-overrides`` decides the string keywords a field left out again, but keeps those the replaced type took
  whatever the override type takes.
* ``legacy-anyurl-keeps-string-keywords`` (``declared_string_keywords_taken``; G03b removes it with the render-time
  drop): a type that names ``AnyUrl`` takes every keyword a declared string holds, and the Pydantic outputs leave out
  every constraint of a field whose type holds it outside a container when they write the field, with a warning.

This module is a leaf (the standard library, ``typing_extensions``, ``enums`` and ``_requirements``): ``types.py``
builds the public tables of ``DataTypeManager`` from it while it is imported, and the model package imports
``types.py``, so the table cannot live under ``model/``.
"""

from __future__ import annotations

import operator
from dataclasses import replace
from fractions import Fraction
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from typing_extensions import final

from datamodel_code_generator._records import record
from datamodel_code_generator._regex_syntax import pattern_admits
from datamodel_code_generator._requirements import pydantic_target_meets

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.enums import TargetPydanticVersion

__all__ = [
    "ARRAY_KEYWORDS",
    "BASE_OUTPUT",
    "LENGTH_KEYWORDS",
    "LENGTH_KEYWORD_ORDER",
    "LENGTH_META_KEYWORDS",
    "LITERAL_DECIDABLE_KEYWORDS",
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
    "TYPE_DEPENDENT_CONSTRAINTS_KEY",
    "TYPE_DEPENDENT_KEYWORDS",
    "DeclaredKinds",
    "OutputKeywords",
    "all_of_root_keyword_kinds",
    "base64_bytes_consumed_keywords",
    "constrained_type_keyword_order",
    "declared_keywords",
    "declared_string_keywords_taken",
    "holds_number_format",
    "keyword_kinds",
    "length_reading",
    "listed_type_keywords",
    "literal_check",
    "literal_number_keywords",
    "member_keywords",
    "meta_keywords",
    "overridden_declared_constraints",
    "property_consumed_keywords",
    "resplit_overridden_constraints",
    "shape_keywords",
    "shape_meta_keywords",
    "split_declared_keywords",
    "undeclared_keywords_taken",
    "unlisted_type_keywords",
    "untaken_minimums",
    "untaken_values",
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
LITERAL_DECIDABLE_KEYWORDS: Final[frozenset[str]] = NUMBER_KEYWORDS | LENGTH_KEYWORDS | {"pattern"}
"""The keywords a literal check decides on the values of a ``const`` or an ``enum`` (``literal_check``)."""

VALUES_AS_WRITTEN_KEY: Final = "_values_as_written"
"""The key under which a type, model or schema holding only the values a literal check admits keeps them as written."""
TYPE_DEPENDENT_CONSTRAINTS_KEY: Final = "_type_dependent_constraints"
"""The key under which a field keeps the declared constraints whose keywords depend on the type it emits."""
DROPPED_MULTIPLE_OF_KEY: Final = "_dropped_multiple_of"
"""The key under which a constrained type keeps the ``multipleOf`` it is written without, not being finite in it."""

_KEYWORD_KINDS: Final[tuple[tuple[frozenset[str], frozenset[str]], ...]] = (
    (frozenset({"string"}), STRING_KEYWORDS),
    (frozenset({"integer", "number"}), NUMBER_KEYWORDS),
    (frozenset({"array"}), ARRAY_KEYWORDS),
    (frozenset({"object"}), PROPERTY_COUNT_KEYWORDS),
)
_DECLARED_STRING_KEYWORDS: Final[frozenset[str]] = STRING_KEYWORDS | {"minItems", "maxItems"}
_LENGTH_READINGS: Final[Mapping[str, str]] = MappingProxyType({"minItems": "minLength", "maxItems": "maxLength"})
_UNTAKEN_KEYWORD_ORDER: Final[tuple[str, ...]] = (*NUMBER_KEYWORD_ORDER, *_LENGTH_READINGS, *STRING_KEYWORD_ORDER)

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
_STRING_KEYWORDS_KEPT_TYPE: Final = "pydantic.AnyUrl"
"""The type that takes every keyword a declared string holds (row ``legacy-anyurl-keeps-string-keywords``)."""

_BASE64_DECODED_KEYWORDS: Final[frozenset[str]] = STRING_KEYWORDS
"""The keywords a type that decodes base64 text takes, as written beside it in every release: Pydantic checks them on
the decoded value, not on the text the schema constrains."""
_BASE64_BYTES_LENGTH_ARGUMENTS: Final[Mapping[str, str]] = MappingProxyType({
    "min_length": "minLength",
    "max_length": "maxLength",
})
"""The ``Field()`` arguments of the lengths that bytes decoded from base64 text write in front of the decoding."""

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
    **dict.fromkeys(
        (f"pydantic.{name}" for name in ("constr", "StrictStr", "StrictBytes", "EmailStr")), STRING_KEYWORDS
    ),
    **dict.fromkeys(
        (f"pydantic.{name}" for name in ("Base64Str", "Base64Bytes", "EncodedBytes")), _BASE64_DECODED_KEYWORDS
    ),
    **dict.fromkeys((f"pydantic.{name}" for name in ("conbytes", "SecretStr")), LENGTH_KEYWORDS),
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
@record
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


@final
@record
class DeclaredKinds:
    """The JSON kinds whose keywords the declared types of a schema hold.

    ``numbers`` tells that a declared type holds numbers (a number, an integer or a numeric string format), ``string``
    and ``array`` that the schema declares a string or an array.
    """

    numbers: bool = False
    string: bool = False
    array: bool = False


def declared_keywords(kinds: DeclaredKinds) -> frozenset[str]:
    """Return the type-dependent keywords whose kind a declared schema type holds.

    A declared string also holds ``minItems`` and ``maxItems`` and a declared array ``minLength`` and ``maxLength``,
    which are written as their length bounds.
    """
    return frozenset().union(
        NUMBER_KEYWORDS if kinds.numbers else NO_KEYWORDS,
        _DECLARED_STRING_KEYWORDS if kinds.string else NO_KEYWORDS,
        SEQUENCE_KEYWORDS if kinds.array else NO_KEYWORDS,
    )


def length_reading(keyword: str) -> str:
    """Return the keyword a declared string writes a keyword as: ``minItems`` and ``maxItems`` as its lengths."""
    return _LENGTH_READINGS.get(keyword, keyword)


def split_declared_keywords(
    kinds: DeclaredKinds, *, number_taken: frozenset[str] | None, string_taken: frozenset[str] | None
) -> tuple[frozenset[str], frozenset[str]]:
    """Return the declared keywords a field writes, and those it leaves out, to warn about.

    The emitted type decides two kinds: a number keyword is written when a number value of the type meets it
    (``number_taken``), and a keyword that the declared string alone holds when the type takes the keyword it is
    written as (``string_taken``; ``length_reading``), as a ``date`` or a ``UUID`` takes neither. None tells that the
    schema does not declare the kind; the keywords of a declared array stay.
    """
    string_alone = (
        _DECLARED_STRING_KEYWORDS - (SEQUENCE_KEYWORDS if kinds.array else NO_KEYWORDS) if kinds.string else NO_KEYWORDS
    )
    untaken = frozenset().union(
        NUMBER_KEYWORDS - number_taken if kinds.numbers and number_taken is not None else NO_KEYWORDS,
        (
            frozenset(keyword for keyword in string_alone if length_reading(keyword) not in string_taken)
            if string_taken is not None
            else NO_KEYWORDS
        ),
    )
    return declared_keywords(kinds) - untaken, untaken


def declared_string_keywords_taken(taken: frozenset[str], type_paths: Iterable[str]) -> frozenset[str]:
    """Return the keywords a declared string's field takes: those its type takes (``taken``), given its type paths.

    Row ``legacy-anyurl-keeps-string-keywords`` (G03b removes it with the render-time drop): a type that names
    ``AnyUrl`` takes them all, as in every release, so the root-model and collapsing decisions see them; the Pydantic
    outputs leave every constraint out of a field whose type holds ``AnyUrl`` outside a container when they write it,
    with a warning, though Pydantic checks ``maxLength`` on it, and ``minLength`` from 2.10 on.
    """
    return taken | STRING_KEYWORDS if _STRING_KEYWORDS_KEPT_TYPE in type_paths else taken


def untaken_values(values: Mapping[str, Any], untaken: frozenset[str]) -> dict[str, Any]:
    """Return the values of the ``untaken`` keywords by the keyword each is written as (``length_reading``).

    The number keywords come first, in their order; ``minItems`` and ``maxItems`` win over the lengths they are read as,
    as they do where they are written.
    """
    left_out: dict[str, Any] = {}
    for keyword in _UNTAKEN_KEYWORD_ORDER:
        if keyword in untaken and keyword in values:
            left_out.setdefault(length_reading(keyword), values[keyword])
    return left_out


def undeclared_keywords_taken(taken: frozenset[str], *, value_literal: bool) -> frozenset[str]:
    """Return the keywords of the kinds a schema does not declare that its field writes: those the emitted type takes.

    Row ``legacy-undeclared-keywords-off-literal``: the ``Literal`` of a ``const`` or an ``enum`` (``value_literal``)
    takes none of them, so an untyped ``{"const": "a", "pattern": "^a"}`` writes no ``pattern``, though Pydantic
    checks it on the ``Literal`` of a string and a declared string writes it; the literal check decides the others.
    """
    return NO_KEYWORDS if value_literal else taken


def property_consumed_keywords(
    zero_bound_classes: Mapping[str, tuple[str, ...]], type_name: str | None
) -> tuple[str, ...]:
    """Return the keywords a field at an object property leaves out, as its own type is a class that enforces them.

    Row ``legacy-consumed-at-object-property``: only an object property whose own type is a zero-bound class leaves
    them out; a root, an item or a union member of the class writes them again.
    """
    return zero_bound_classes.get(type_name, ()) if type_name else ()


def base64_bytes_consumed_keywords(arguments: Mapping[str, object]) -> frozenset[str]:
    """Return the lengths that bytes decoded from base64 text write inside their own annotation, by its arguments.

    The type consumes them, so a field of the type leaves them out of its own constraints.
    """
    return frozenset(keyword for argument, keyword in _BASE64_BYTES_LENGTH_ARGUMENTS.items() if argument in arguments)


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


def overridden_declared_constraints(
    values: Mapping[str, Any], *, type_dependent: Mapping[str, Any], untaken: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the constraints of a declared schema type that an override type decides again.

    They are the field's number constraints the schema type decided (not those ``type_dependent`` remembers as decided
    by the emitted type) and the ``untaken`` number and string ones it was generated without. Row
    ``legacy-override-keeps-string-keywords`` (G03b removes it): the string keywords the replaced type took stay
    whatever the override type takes, as the field's lengths no longer tell a string's from an array's or a mapping's.
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
    and the ``declared`` ones (kept when the override type, or a number value of it, takes them, ``declared_taken``)
    are decided again; the other constraints stay. The declared ones it does not take are left out, to warn about.
    """
    kept = {key: value for key, value in values.items() if key not in type_dependent and key not in declared}
    kept.update({key: value for key, value in type_dependent.items() if key in taken})
    kept.update({key: value for key, value in declared.items() if key in declared_taken})
    return kept, {key: value for key, value in declared.items() if key not in declared_taken}


def untaken_minimums(untaken: Iterable[str], taken_by_minimum: Iterable[tuple[str, frozenset[str]]]) -> dict[str, str]:
    """Return, for each untaken number keyword that a later target model version takes, the first such version.

    ``taken_by_minimum`` gives, from the oldest version, each version that adds a number keyword and the keywords a
    number value of the type meets then; a later version adds no other kind.
    """
    keys = tuple(key for key in untaken if key in NUMBER_KEYWORDS)
    minimums: dict[str, str] = {}
    for minimum, taken in taken_by_minimum:
        minimums.update({key: minimum for key in keys if key in taken and key not in minimums})
    return minimums


_BOUND_CHECKS: Final[Mapping[str, Callable[[float, float], bool]]] = MappingProxyType({
    "minimum": operator.ge,
    "maximum": operator.le,
    "exclusiveMinimum": operator.gt,
    "exclusiveMaximum": operator.lt,
})


def _meets_length(value: object, keyword: str, limit: float) -> bool:
    """Return whether a JSON value meets a length keyword, counting the code points of a string."""
    if not isinstance(value, str):
        return True
    return len(value) >= limit if keyword == "minLength" else len(value) <= limit


def _meets(value: object, keyword: str, limit: float | str) -> bool | None:
    """Return whether a JSON value meets a keyword with the limit, or None when the check cannot decide it.

    A keyword of a kind that does not apply to the value's JSON type is met, as is every keyword by null (JSON Schema).
    The bounds compare a number by IEEE rules, so NaN meets none; a ``bool`` is no number. ``multipleOf`` is decided on
    the decimal text of the values, and not for a divisor that is not positive and finite or a value that is not finite.
    A ``pattern`` is decided only where the static scan certifies that every engine reads it alike (``pattern_admits``).
    """
    if isinstance(limit, str):
        return pattern_admits(limit, value) if isinstance(value, str) else True
    if keyword in LENGTH_KEYWORDS:
        return _meets_length(value, keyword, limit)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return True
    if (check := _BOUND_CHECKS.get(keyword)) is not None:
        return check(value, limit)
    if not (_is_finite(limit) and limit > 0 and _is_finite(value)):
        return None
    return (Fraction(str(value)) / Fraction(str(limit))).denominator == 1


def _is_finite(number: float) -> bool:
    """Return whether a JSON number is finite: every ``int`` is, also one too large for a float."""
    return isinstance(number, int) or isfinite(number)


def literal_check(
    values: Sequence[object], constraints: Mapping[str, float | str]
) -> tuple[tuple[str, ...], tuple[bool, ...]]:
    """Return the keywords of ``constraints`` the check decides on every value, and whether each value meets them all.

    ``constraints`` holds keywords of ``LITERAL_DECIDABLE_KEYWORDS`` with their limits, in the order to report them.
    """
    met = {keyword: tuple(_meets(value, keyword, limit) for value in values) for keyword, limit in constraints.items()}
    decided = tuple(keyword for keyword, results in met.items() if None not in results)
    return decided, tuple(all(met[keyword][index] for keyword in decided) for index in range(len(values)))
