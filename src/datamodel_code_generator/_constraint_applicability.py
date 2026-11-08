"""Constraint applicability: which schema constraint keywords each emitted type takes, in one table.

The rows are static: the keyword groups of each JSON kind, the keywords a declared schema type holds, the keywords
each Python or library type takes in each output model, the classes that enforce a keyword themselves, the keywords
the ``con*`` types and ``msgspec.Meta`` take, and the rows that keep today's answer where two sites disagree
(``legacy-*``; a fix of that output deletes its row). Every function is pure: facts in, keywords out. The adapters
read the live objects and are listed in ``docs/architecture.md``.

Legacy rows, each named by the function that decides it:

* ``legacy-consumed-at-object-property`` (``property_consumed_keywords``): a field leaves out the keyword its zero-bound
  class enforces (``OutputKeywords.zero_bound_classes``) only at an object property whose own type is that class, not
  at a root, an item or a union member.
* ``legacy-all-of-root-format-kinds`` (``all_of_root_keyword_kinds``): an ``allOf`` root merged with constraints keeps
  the keywords of the kinds its format names, silently, before the field split decides the declared ones by the
  emitted type.
* ``legacy-exact-ints-as-floats`` (``exact_float``): the schema models read an int constraint as a float where one
  equals it and is written as it, and keep the int only where a float would differ.
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
* ``legacy-msgspec-float-bounds`` (``union_bound_arithmetic``): without ``--read-msgspec-union-bounds-exactly`` the
  check of a msgspec union reads the bound of a float member as the float it is, so an integral bound past ``2**53`` is
  the integer of its binary value, where the schema models read the decimal that is written.
* ``legacy-length-keywords-across-kinds`` (``declared_keywords``): without ``--read-length-keywords-by-type`` a
  declared string also holds ``minItems`` and ``maxItems`` and a declared array ``minLength`` and ``maxLength``, which
  are written as their length bounds.
* ``legacy-lenient-base64-str`` (``strict_encoded_str``): without ``--use-strict-str-for-base64``,
  ``--strict-types str`` leaves a ``format: byte`` string ``Base64Str``, or the ``str`` that ``EncodedStr`` decodes,
  which accept bytes.

This module is a leaf (the standard library, ``typing_extensions``, ``enums`` and ``_requirements``): ``types.py``
builds the public tables of ``DataTypeManager`` from it while it is imported, and the model package imports
``types.py``, so the table cannot live under ``model/``.
"""

from __future__ import annotations

import operator
from dataclasses import replace
from fractions import Fraction
from math import ceil, floor, inf, isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

from typing_extensions import TypeIs, final

from datamodel_code_generator._records import record
from datamodel_code_generator._regex_syntax import pattern_admits
from datamodel_code_generator._requirements import pydantic_target_meets

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.enums import TargetPydanticVersion

__all__ = [
    "ARRAY_KEYWORDS",
    "BASE_OUTPUT",
    "COUNT_KEYWORDS",
    "ITEM_COUNT_KEYWORDS",
    "LENGTH_KEYWORDS",
    "LENGTH_KEYWORD_ORDER",
    "LENGTH_META_KEYWORDS",
    "LITERAL_DECIDABLE_KEYWORDS",
    "META_SCHEMA_KEYWORDS",
    "MSGSPEC_OUTPUT",
    "NO_KEYWORDS",
    "NUMBER_BOUND_KEYWORDS",
    "NUMBER_FIELD_KEYWORDS",
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
    "UNION_SIBLING_CONSTRAINTS_KEY",
    "WIDENED_VALUES_KEY",
    "ConstraintOptions",
    "DeclaredKinds",
    "OutputKeywords",
    "RenderDrop",
    "all_of_root_keyword_kinds",
    "base64_text_consumed_keywords",
    "constrained_type_keyword_order",
    "decimal_field_form",
    "declared_keywords",
    "declared_string_keywords_taken",
    "enforced_keywords",
    "engine_pattern_types",
    "exact_float",
    "field_form_zero_bound",
    "holds_number_format",
    "inexact_float_divisor",
    "int_beyond_values",
    "int_constraint_beyond_values",
    "integer_bound",
    "is_non_finite",
    "json_integer",
    "json_number",
    "key_count_keywords",
    "keyword_kinds",
    "length_keywords_by_type",
    "length_reading",
    "listed_type_keywords",
    "literal_check",
    "literal_enforces_every_keyword",
    "literal_met_keywords",
    "literal_number_keywords",
    "member_keywords",
    "meta_keywords",
    "non_finite_written",
    "none_type_keywords",
    "other_kind_keywords",
    "overridden_declared_constraints",
    "property_consumed_keywords",
    "render_drop",
    "resplit_overridden_constraints",
    "saturating_float",
    "shape_keywords",
    "shape_meta_keywords",
    "split_declared_keywords",
    "strict_decimal_multiple",
    "undeclared_keywords_taken",
    "union_bound_arithmetic",
    "unlisted_type_keywords",
    "untaken_minimums",
    "untaken_values",
    "writes_beside_literal",
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
ITEM_COUNT_KEYWORDS: Final[frozenset[str]] = frozenset({"minItems", "maxItems"})
"""The keywords that count the items of an array, which a type that takes them takes as the lengths of the array."""
COUNT_KEYWORDS: Final[frozenset[str]] = LENGTH_KEYWORDS | ITEM_COUNT_KEYWORDS
"""The keywords that count the items of a container: its lengths and its item counts."""
ARRAY_KEYWORDS: Final[frozenset[str]] = ITEM_COUNT_KEYWORDS | {"uniqueItems"}
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
UNION_SIBLING_CONSTRAINTS_KEY: Final = "_union_sibling_constraints"
"""The key under which a field keeps the constraints it writes from the keywords a schema declares beside a union."""
DROPPED_MULTIPLE_OF_KEY: Final = "_dropped_multiple_of"
"""The key under which a constrained type keeps the ``multipleOf`` it is written without, not being finite in it."""
WIDENED_VALUES_KEY: Final = "_widened_values"
"""The key under which the type of an enum or a const keeps the values it is written in place of, which no ``Literal``
holds (``values_no_literal_holds``)."""

_KEYWORD_KINDS: Final[tuple[tuple[frozenset[str], frozenset[str]], ...]] = (
    (frozenset({"string"}), STRING_KEYWORDS),
    (frozenset({"integer", "number"}), NUMBER_KEYWORDS),
    (frozenset({"array"}), ARRAY_KEYWORDS),
    (frozenset({"object"}), PROPERTY_COUNT_KEYWORDS),
)
_DECLARED_STRING_KEYWORDS: Final[frozenset[str]] = STRING_KEYWORDS | ITEM_COUNT_KEYWORDS
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
_BASE64_BYTES_KEYWORDS: Final[frozenset[str]] = LENGTH_KEYWORDS
"""The keywords bytes decoded from base64 text take: the lengths, but no ``pattern``, which Pydantic cannot check on
the text: it refuses one in ``Field()`` beside ``Base64Bytes`` at import and checks one beside ``EncodedBytes`` on the
decoded value."""
_BASE64_TEXT_ARGUMENTS: Final[Mapping[str, str]] = MappingProxyType({
    "pattern": "pattern",
    "min_length": "minLength",
    "max_length": "maxLength",
})
"""The ``Field()`` arguments of the constraints that a value decoded from base64 text writes in front of the decoding:
the lengths of bytes, and also the ``pattern`` of a string."""

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

_ORDERED_AND_COUNTER: Final[frozenset[str]] = frozenset({
    "collections.OrderedDict",
    "typing.OrderedDict",
    "collections.Counter",
    "typing.Counter",
})
"""The ``OrderedDict`` and ``Counter`` classes, by the path that classifies them."""
_MAPPING_CLASS_PATHS: Final[frozenset[str]] = (
    frozenset({
        "dict",
        "typing.Dict",
        "collections.abc.Mapping",
        "collections.abc.MutableMapping",
        "typing.Mapping",
        "typing.MutableMapping",
    })
    | _ORDERED_AND_COUNTER
)
"""The classes, by the path that classifies them, whose length counts their keys and on which Pydantic checks one:
the classes that take the property counts of a map (``key_count_keywords``, ``OutputKeywords.mapping_classes``)."""
_MSGSPEC_MAPPING_CLASS_PATHS: Final[frozenset[str]] = _MAPPING_CLASS_PATHS - _ORDERED_AND_COUNTER
"""The mapping classes msgspec decodes as a dict, on which ``msgspec.Meta`` checks the number of keys as a length;
``Meta`` refuses a length on an ``OrderedDict`` or a ``Counter``."""

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
    **dict.fromkeys(("pydantic.Base64Str", "pydantic.EncodedStr"), _BASE64_DECODED_KEYWORDS),
    **dict.fromkeys(("pydantic.Base64Bytes", "pydantic.EncodedBytes"), _BASE64_BYTES_KEYWORDS),
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
    ``writes_constrained_types`` tells that the type manager writes scalar constraints into ``con*`` types, and
    ``mapping_classes`` are the classes whose length counts their keys and on which the output checks one.

    The number rows say which values the output writes: ``int_values`` is the range of the ints it takes as a number
    constraint, an ``Enum`` or ``Literal`` value or a tag (None when it takes every int), and
    ``drops_non_finite_bounds`` that a bound or divisor with no finite float is left out (else it is written as the
    infinity or NaN). ``refused_literal_types`` are the types of the values a ``Literal`` of the output cannot hold.
    """

    type_keywords: Mapping[str, frozenset[str]]
    number_value_types: frozenset[str]
    target_number_keywords: Mapping[str, tuple[PydanticConstruct, frozenset[str]]]
    annotation_unions_take: bool
    fixed_tuples_take_length: bool
    literals_take: bool
    zero_bound_classes: Mapping[str, tuple[str, ...]]
    writes_constrained_types: bool
    mapping_classes: frozenset[str]
    int_values: range | None
    drops_non_finite_bounds: bool
    refused_literal_types: tuple[type, ...]


BASE_OUTPUT: Final = OutputKeywords(
    type_keywords=_BASE_TYPE_KEYWORDS,
    number_value_types=_BASE_NUMBER_VALUE_TYPES,
    target_number_keywords=MappingProxyType({}),
    annotation_unions_take=True,
    fixed_tuples_take_length=True,
    literals_take=True,
    zero_bound_classes=MappingProxyType({}),
    writes_constrained_types=False,
    mapping_classes=_MAPPING_CLASS_PATHS,
    int_values=None,
    drops_non_finite_bounds=False,
    refused_literal_types=(),
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
    mapping_classes=_MSGSPEC_MAPPING_CLASS_PATHS,
    int_values=range(-(2**63), 2**63),
    drops_non_finite_bounds=True,
    refused_literal_types=(float, bytes),
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
    and ``array`` that the schema declares a string or an array, and ``typed`` that it declares a type at all.
    """

    numbers: bool = False
    string: bool = False
    array: bool = False
    typed: bool = False


def _kind_union(kinds: DeclaredKinds, string: frozenset[str], array: frozenset[str]) -> frozenset[str]:
    """Return the number keywords of a declared number, ``string`` of a declared string and ``array`` of an array."""
    return frozenset().union(
        NUMBER_KEYWORDS if kinds.numbers else NO_KEYWORDS,
        string if kinds.string else NO_KEYWORDS,
        array if kinds.array else NO_KEYWORDS,
    )


def declared_keywords(kinds: DeclaredKinds, *, by_type: bool = False) -> frozenset[str]:
    """Return the type-dependent keywords whose kind a declared schema type holds.

    Under ``by_type`` (``length_keywords_by_type``) each kind holds its own keywords only, the groups of
    ``keyword_kinds``. Row ``legacy-length-keywords-across-kinds``: otherwise a declared string also holds ``minItems``
    and ``maxItems`` and a declared array ``minLength`` and ``maxLength``, which are written as their length bounds.
    """
    if by_type:
        return _kind_union(kinds, STRING_KEYWORDS, ARRAY_KEYWORDS)
    return _kind_union(kinds, _DECLARED_STRING_KEYWORDS, SEQUENCE_KEYWORDS)


def other_kind_keywords(kinds: DeclaredKinds, *, by_type: bool) -> frozenset[str]:
    """Return the string and array keywords a field leaves out whatever its emitted type takes.

    Under ``by_type`` (``length_keywords_by_type``) those of a kind that a schema declaring a type does not declare:
    they apply to no value the schema accepts, as ``maxLength`` beside an array, also where ``--type-overrides`` writes
    the field as a ``str``. Otherwise, and for a schema that declares no type, the emitted type decides them.
    """
    if not (by_type and kinds.typed):
        return NO_KEYWORDS
    return frozenset().union(
        NO_KEYWORDS if kinds.string else STRING_KEYWORDS,
        NO_KEYWORDS if kinds.array else ARRAY_KEYWORDS,
    )


def length_reading(keyword: str) -> str:
    """Return the keyword a declared string writes a keyword as: ``minItems`` and ``maxItems`` as its lengths."""
    return _LENGTH_READINGS.get(keyword, keyword)


def split_declared_keywords(
    kinds: DeclaredKinds,
    *,
    number_taken: frozenset[str] | None,
    string_taken: frozenset[str] | None,
    by_type: bool = False,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return the declared keywords a field writes, and those it leaves out, to warn about.

    The emitted type decides two kinds: a number keyword is written when a number value of the type meets it
    (``number_taken``), and a keyword that the declared string alone holds when the type takes the keyword it is
    written as (``string_taken``; ``length_reading``), as a ``date`` or a ``UUID`` takes neither. None tells that the
    schema does not declare the kind; the keywords of a declared array stay. ``by_type`` reads each kind's own
    keywords (``declared_keywords``).
    """
    across_kinds = _DECLARED_STRING_KEYWORDS - (SEQUENCE_KEYWORDS if kinds.array else NO_KEYWORDS)
    string_alone = (STRING_KEYWORDS if by_type else across_kinds) if kinds.string else NO_KEYWORDS
    untaken = frozenset().union(
        NUMBER_KEYWORDS - number_taken if kinds.numbers and number_taken is not None else NO_KEYWORDS,
        (
            frozenset(keyword for keyword in string_alone if length_reading(keyword) not in string_taken)
            if string_taken is not None
            else NO_KEYWORDS
        ),
    )
    return declared_keywords(kinds, by_type=by_type) - untaken, untaken


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


@final
@record
class ConstraintOptions:
    """The options that decide which constraints a field writes beside its type, read only by this table.

    ``omit_redundant_constraints`` (``--omit-redundant-constraints``) leaves out of a field every keyword its type
    enforces itself (``enforced_keywords``, ``literal_enforces_every_keyword``, ``writes_beside_literal``,
    ``field_form_zero_bound``). ``use_decimal_in_field_forms`` (``--use-decimal-in-field-forms``) writes the number
    of a field form with ``multipleOf`` as a ``Decimal`` too, strict in every form (``decimal_field_form``,
    ``strict_decimal_multiple``). ``use_encoded_str_for_constrained_base64``
    (``--use-encoded-str-for-constrained-base64``) writes the constraints of a string decoded from base64 text in
    front of its decoding in the field forms (``encoded_str_field_form``). ``use_strict_str_for_base64``
    (``--use-strict-str-for-base64``) decodes the base64 text of a string under ``--strict-types str`` to a
    ``StrictStr`` (``strict_encoded_str``). ``read_length_keywords_by_type``
    (``--read-length-keywords-by-type``) reads the string and the array keywords for their own kind only
    (``length_keywords_by_type``). ``read_msgspec_union_bounds_exactly`` (``--read-msgspec-union-bounds-exactly``)
    reads the bounds the check of a msgspec union compares as the schema writes them (``union_bound_arithmetic``).
    """

    omit_redundant_constraints: bool = False
    use_decimal_in_field_forms: bool = False
    use_encoded_str_for_constrained_base64: bool = False
    use_strict_str_for_base64: bool = False
    read_length_keywords_by_type: bool = False
    read_msgspec_union_bounds_exactly: bool = False


def _class_keywords(zero_bound_classes: Mapping[str, tuple[str, ...]], type_name: str | None) -> tuple[str, ...]:
    """Return the keywords a type enforces by its class: the zero bound of a zero-bound class."""
    return zero_bound_classes.get(type_name, ()) if type_name else ()


def property_consumed_keywords(
    zero_bound_classes: Mapping[str, tuple[str, ...]], type_name: str | None
) -> tuple[str, ...]:
    """Return the keywords a field at an object property leaves out, as its own type is a class that enforces them.

    Row ``legacy-consumed-at-object-property``: only an object property whose own type is a zero-bound class leaves
    them out; a root, an item or a union member of the class writes them again, unless ``enforced_keywords`` leaves
    them out at every position.
    """
    return _class_keywords(zero_bound_classes, type_name)


def enforced_keywords(
    options: ConstraintOptions,
    leaves: Iterable[tuple[str | None, Sequence[object]]],
    constraints: Mapping[str, float | str],
    zero_bound_classes: Mapping[str, tuple[str, ...]],
) -> frozenset[str]:
    """Return the keywords of a schema that its field's type enforces itself, which the field leaves out.

    Only under ``omit_redundant_constraints``. ``leaves`` gives each type a value of the field's type validates as,
    but ``None``: its class name (None for a container) and the values of its ``Literal`` (empty for another type).
    Every leaf must enforce a keyword: a zero-bound class its bound, a ``Literal`` each keyword of ``constraints``
    all its values meet (``literal_met_keywords``).
    """
    if not options.omit_redundant_constraints:
        return NO_KEYWORDS
    return frozenset.intersection(
        *(
            literal_met_keywords(values, constraints)
            if values
            else frozenset(_class_keywords(zero_bound_classes, type_name))
            for type_name, values in leaves
        )
    )


def literal_enforces_every_keyword(
    options: ConstraintOptions,
    values: Sequence[object],
    constraints: Mapping[str, float | str],
    declared: AbstractSet[str],
) -> bool:
    """Return whether the ``Literal`` of values enforces every constraint beside them, so the field writes none.

    Only under ``omit_redundant_constraints``: the literal check decides each keyword the schema sets (``declared``)
    and every value meets it.
    """
    return (
        options.omit_redundant_constraints
        and constraints.keys() == declared
        and len(literal_met_keywords(values, constraints)) == len(constraints)
    )


def writes_beside_literal(options: ConstraintOptions, *, writes_field_arguments: bool) -> bool:
    """Return whether a field writes the keywords it takes beside the ``Literal`` of its values.

    A field that writes ``Field()`` arguments does, which Pydantic checks on the ``Literal``; under
    ``omit_redundant_constraints`` the literal check decides them all and the field writes none of them.
    """
    return writes_field_arguments and not options.omit_redundant_constraints


def field_form_zero_bound(
    options: ConstraintOptions, values: Mapping[str, object], *, zero_bound_types: bool, strict: bool
) -> str | None:
    """Return the one bound equal to 0 that the type of a field form takes, as its zero-bound class, or None.

    Under ``--use-non-positive-negative-number-constrained-types`` (``zero_bound_types``) a schema with exactly one
    bound equal to 0 keeps it on its type (row ``legacy-consumed-at-object-property`` writes it again); under
    ``omit_redundant_constraints`` a strict number takes none, so its field writes the bound beside ``StrictInt`` or
    ``StrictFloat`` instead of a strict ``con*`` type that repeats it.
    """
    if not zero_bound_types or (options.omit_redundant_constraints and strict):
        return None
    return bounds[0] if len(bounds := [key for key in NUMBER_BOUND_KEYWORDS if values.get(key) == 0]) == 1 else None


def decimal_field_form(options: ConstraintOptions, *, decimal_for_multiple_of: bool, float_multiple_of: bool) -> bool:
    """Return whether a field form writes a float number with ``multipleOf`` (``float_multiple_of``) as a ``Decimal``.

    It does under ``--use-decimal-for-multiple-of`` with ``use_decimal_in_field_forms``; without the latter the
    field forms write a float, as in 0.86.0.
    """
    return decimal_for_multiple_of and options.use_decimal_in_field_forms and float_multiple_of


def strict_decimal_multiple(options: ConstraintOptions, *, strict: bool) -> bool:
    """Return whether the ``Decimal`` a float number with ``multipleOf`` is written as is strict.

    It is under ``--strict-types float`` (``strict``) with ``use_decimal_in_field_forms``.
    """
    return strict and options.use_decimal_in_field_forms


def encoded_str_field_form(options: ConstraintOptions) -> bool:
    """Return whether a field form writes a string decoded from base64 text with its constraints in front of decoding.

    It does under ``use_encoded_str_for_constrained_base64``, so Pydantic checks them on the text, as the schema does;
    otherwise ``Annotated[Base64Str, Field(...)]`` and ``Base64Str | None = Field(...)`` check them on the decoded
    text, as in 0.88.0.
    """
    return options.use_encoded_str_for_constrained_base64


def strict_encoded_str(options: ConstraintOptions, *, strict: bool) -> bool:
    """Return whether a string decoded from base64 text is the ``StrictStr`` that ``EncodedStr`` decodes.

    It is under ``--strict-types str`` (``strict``) with ``use_strict_str_for_base64``, so Pydantic 2.10 or later
    refuses bytes for it; otherwise row ``legacy-lenient-base64-str`` writes ``Base64Str``, or the ``str`` of
    ``encoded_str_field_form``, which accept bytes, as in 0.90.0.
    """
    return strict and options.use_strict_str_for_base64


def length_keywords_by_type(options: ConstraintOptions) -> bool:
    """Return whether the string and the array keywords apply to their own kind only, as JSON Schema reads them.

    They do under ``read_length_keywords_by_type``: ``minLength``, ``maxLength`` and ``pattern`` to strings and
    ``minItems``, ``maxItems`` and ``uniqueItems`` to arrays; otherwise row ``legacy-length-keywords-across-kinds``
    reads the lengths and the item counts across the two, as in 0.88.0.
    """
    return options.read_length_keywords_by_type


def union_bound_arithmetic(options: ConstraintOptions) -> Literal["exact", "float"]:
    """Return how the check of a msgspec union reads the bounds of its members (``integer_bound``).

    ``exact`` under ``read_msgspec_union_bounds_exactly``: each bound is the number the schema writes, as the schema
    models read it. Row ``legacy-msgspec-float-bounds``: otherwise ``float``, the ``Meta`` value, so the bound of a
    float member past ``2**53`` is the integer of its binary value, as in 0.88.0.
    """
    return "exact" if options.read_msgspec_union_bounds_exactly else "float"


RenderDrop: TypeAlias = Literal["self-reference-container", "strict-kwargs", "anyurl"]
"""Why a field writes none of its constraints when it is written."""


def render_drop(
    *, self_reference_outside_container: bool, strict_arguments: bool, anyurl_outside_container: bool = False
) -> RenderDrop | None:
    """Return why a field writes none of its constraints when it is written, or None when it writes them.

    A field naming its own model outside a container takes none, and a strict type with arguments writes them
    itself. Row ``legacy-anyurl-keeps-string-keywords``: the Pydantic outputs leave out every constraint of a field
    whose type holds ``AnyUrl`` outside a container, with a warning.
    """
    if self_reference_outside_container:
        return "self-reference-container"
    return "strict-kwargs" if strict_arguments else "anyurl" if anyurl_outside_container else None


def none_type_keywords(*, none_type: bool) -> frozenset[str]:
    """Return the number constraints of a field a ``None`` type leaves out when the field is written (``none_type``)."""
    return NUMBER_FIELD_KEYWORDS if none_type else NO_KEYWORDS


def base64_text_consumed_keywords(arguments: Mapping[str, object]) -> frozenset[str]:
    """Return the keywords that a value decoded from base64 text writes inside its own annotation, by its arguments.

    The type consumes them, so a field of the type leaves them out of its own constraints.
    """
    return frozenset(keyword for argument, keyword in _BASE64_TEXT_ARGUMENTS.items() if argument in arguments)


def holds_number_format(mapped_type: str) -> bool:
    """Return whether a string format mapped to the type (a ``types.py:Types`` name) holds a number."""
    return mapped_type in _NUMBER_STRING_FORMAT_TYPES


def constrained_type_keyword_order(
    mapped_type: str, *, encoded_str: bool = False, by_type: bool = False
) -> tuple[str, ...]:
    """Return the keywords a ``con*`` type of the mapped type (a ``types.py:Types`` name) takes, in argument order.

    A string decoded from base64 text takes the string keywords where a field form writes them in front of its
    decoding (``encoded_str``, ``encoded_str_field_form``). A string reads ``minItems`` and ``maxItems`` as its lengths
    unless ``by_type`` (``length_keywords_by_type``; row ``legacy-length-keywords-across-kinds``).
    """
    if mapped_type in _NUMBER_TYPES:
        return NUMBER_KEYWORD_ORDER
    if mapped_type == "string":
        return STRING_KEYWORD_ORDER if by_type else STRING_LENGTH_KEYWORD_ORDER
    if mapped_type == "binary":
        return LENGTH_KEYWORD_ORDER
    return STRING_KEYWORD_ORDER if mapped_type == "byte" and encoded_str else ()


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


def shape_keywords(
    *, mapping: bool, sequence: bool, reference: bool, counts_keys: bool = False
) -> frozenset[str] | None:
    """Return the keywords a container or a referenced model takes, or None when the type itself decides.

    A mapping takes none, a sequence the sequence keywords, and a referenced model all of them, as its own root type
    decides them. With ``counts_keys`` the keywords a type takes as the property counts of a map are returned instead
    (``key_count_keywords``): the lengths for a mapping, which count its keys, none for a sequence.
    """
    if mapping:
        return LENGTH_KEYWORDS if counts_keys else NO_KEYWORDS
    if sequence:
        return NO_KEYWORDS if counts_keys else SEQUENCE_KEYWORDS
    return TYPE_DEPENDENT_KEYWORDS if reference else None


def key_count_keywords(path: str, keywords: frozenset[str], mapping_classes: AbstractSet[str]) -> frozenset[str]:
    """Return the keywords a type takes as the property counts of a map, from its path and the ``keywords`` it takes.

    A mapping class of the output (``mapping_classes``, ``OutputKeywords.mapping_classes``) takes the lengths, which
    count its keys, and a type the generator cannot classify, which takes every keyword, keeps them all; any other type
    takes none.
    """
    if path in mapping_classes:
        return LENGTH_KEYWORDS
    return keywords if keywords >= TYPE_DEPENDENT_KEYWORDS else NO_KEYWORDS


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


def engine_pattern_types(output: OutputKeywords) -> frozenset[str]:
    """Return the names of the types whose ``pattern`` the regex engine choice reads, as a data type holds them.

    They are the types that take ``pattern`` in ``output``, whose pattern the output model compiles, by the last part
    of their path (``constr``).
    """
    return frozenset(
        path.rpartition(".")[2] for path, keywords in output.type_keywords.items() if "pattern" in keywords
    )


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


def json_integer(value: object) -> TypeIs[int]:
    """Return whether a JSON value is an integer: an int, a ``bool`` being none."""
    return isinstance(value, int) and not isinstance(value, bool)


def json_number(value: object) -> TypeIs[int | float]:
    """Return whether a JSON value is a number: an int or a float, a ``bool`` being none."""
    return isinstance(value, int | float) and not isinstance(value, bool)


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
    if not json_number(value):
        return True
    if (check := _BOUND_CHECKS.get(keyword)) is not None:
        return check(value, limit)
    if not (_is_finite(limit) and limit > 0 and _is_finite(value)):
        return None
    return (Fraction(str(value)) / Fraction(str(limit))).denominator == 1


def _is_finite(number: float) -> bool:
    """Return whether a JSON number is finite: every ``int`` is, also one too large for a float."""
    return isinstance(number, int) or isfinite(number)


def _decided(values: Sequence[object], constraints: Mapping[str, float | str]) -> dict[str, tuple[bool | None, ...]]:
    """Return, for each keyword of ``constraints`` the check decides on every value, whether each value meets it."""
    met = {keyword: tuple(_meets(value, keyword, limit) for value in values) for keyword, limit in constraints.items()}
    return {keyword: results for keyword, results in met.items() if None not in results}


def literal_check(
    values: Sequence[object], constraints: Mapping[str, float | str]
) -> tuple[tuple[str, ...], tuple[bool, ...]]:
    """Return the keywords of ``constraints`` the check decides on every value, and whether each value meets them all.

    ``constraints`` holds keywords of ``LITERAL_DECIDABLE_KEYWORDS`` with their limits, in the order to report them.
    """
    met = _decided(values, constraints)
    return tuple(met), tuple(all(results[index] for results in met.values()) for index in range(len(values)))


def literal_met_keywords(
    values: Sequence[object], constraints: Mapping[str, float | str], *, unmet_too: bool = False
) -> frozenset[str]:
    """Return the keywords of ``constraints`` the literal check decides that every one of the values meets.

    With ``unmet_too`` also those no value meets, whose values stay as written with the unsatisfiable warning.
    """
    return frozenset(
        keyword
        for keyword, results in _decided(values, constraints).items()
        if all(results) or (unmet_too and not any(results))
    )


def inexact_float_divisor(divisor: float) -> bool:
    """Return whether a float holds a ``multipleOf`` divisor only approximately: it is no dyadic rational it holds.

    msgspec checks a float ``multipleOf`` by the floating-point remainder, so it leaves out such a divisor, as 0.01,
    which would reject its own multiples.
    """
    exact = Fraction(str(divisor))
    return bool(exact.denominator & (exact.denominator - 1)) or Fraction(float(divisor)) != exact


NUMBER_FIELD_KEYWORDS: Final[frozenset[str]] = frozenset({"gt", "ge", "lt", "le", "multiple_of"})
"""The number constraints of a field, as the Pydantic ``Field`` and ``msgspec.Meta`` call them."""


def saturating_float(number: float) -> float:
    """Return a number as a float: an int beyond the float range is the infinity of its sign, as ``1e400`` is."""
    try:
        return float(number)
    except OverflowError:
        return inf if number > 0 else -inf


def exact_float(number: int) -> float | None:
    """Return an int as the float that equals it and is written as it, or None when a float would differ.

    Row ``legacy-exact-ints-as-floats``: the schema models read an int constraint as a float where one equals it
    (``-2**63``, ``2**53``) and keep the int where the float range or its precision cannot hold it, or where the
    decimal the float is written as is another number (``2**64`` is written ``18446744073709552000``), since the
    constraints of an integer are read as the decimal written (``integer_bound`` ``exact``).
    """
    try:
        float_value = float(number)
    except OverflowError:
        return None
    return float_value if float_value == number == Fraction(str(float_value)) else None


_INTEGER_BOUNDS: Final[Mapping[str, tuple[Literal["ge", "le"], Callable[[Any], int], int]]] = MappingProxyType({
    "ge": ("ge", ceil, 0),
    "gt": ("ge", floor, 1),
    "le": ("le", floor, 0),
    "lt": ("le", ceil, -1),
})


def integer_bound(
    keyword: str, value: float, *, arithmetic: Literal["exact", "float"]
) -> tuple[Literal["ge", "le"], int] | None:
    """Return the ``ge`` or ``le`` that the integers a ``ge``, ``gt``, ``le`` or ``lt`` bound admits come to, or None.

    The least integer a lower bound admits and the greatest an upper bound admits: the ceiling or the floor of the
    bound, one more or less for an exclusive one. ``exact`` reads the bound as the decimal it is written (``1e300`` is
    ``10**300``); ``float`` reads the float it is (``int(1e300)``).

    The check of a msgspec union reads a bound as ``union_bound_arithmetic`` says (row ``legacy-msgspec-float-bounds``),
    the schema models read it ``exact``.
    """
    if (row := _INTEGER_BOUNDS.get(keyword)) is None:
        return None
    side, rounding, offset = row
    return side, rounding(Fraction(str(value)) if arithmetic == "exact" else value) + offset


def is_non_finite(number: float) -> bool:
    """Return whether a number has no finite float: an infinity, NaN, or an int beyond the float range."""
    return not isfinite(saturating_float(number))


def non_finite_written(number: float, *, as_float: bool) -> float | None:
    """Return a number as the infinity or NaN that a type writes it as, or None when it writes it finite.

    A float type writes an int beyond the float range as an infinity too (``saturating_float``); an int or a
    ``Decimal`` type writes an int as it is.
    """
    if not as_float and not isinstance(number, float):
        return None
    return None if isfinite(written := saturating_float(number)) else written


def _within(values: range, value: int) -> bool:
    """Return whether an int lies in a range, by its bounds: ``in`` counts the range for an ``int`` subclass."""
    return values.start <= value < values.stop


def int_beyond_values(output: OutputKeywords, value: object) -> bool:
    """Return whether the output refuses an int: one outside ``OutputKeywords.int_values``. A ``bool`` lies within."""
    return output.int_values is not None and isinstance(value, int) and not _within(output.int_values, value)


def int_constraint_beyond_values(output: OutputKeywords, keyword: str, value: object) -> bool:
    """Return whether the output refuses a number constraint of an int: one outside the range its keyword takes.

    A ``gt`` or ``lt`` takes the range less its greatest or least int, whose ``ge`` or ``le`` the output writes
    instead; a keyword that is no number constraint of a field (``NUMBER_FIELD_KEYWORDS``) is never refused.
    """
    if output.int_values is None or not isinstance(value, int) or keyword not in NUMBER_FIELD_KEYWORDS:
        return False
    values = output.int_values
    taken = values[:-1] if keyword == "gt" else values[1:] if keyword == "lt" else values
    return not _within(taken, value)
