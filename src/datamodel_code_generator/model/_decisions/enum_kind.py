"""Enum class kind and literal value kind: which class an enum is written as, and which values a ``Literal`` holds.

Every input is an immutable fact passed by keyword, so the functions read nothing else. The adapters read the facts
from the live objects and build the class through ``model/enum.py:_enum_class``:
``parser/jsonschema.py:JsonSchemaParser._get_enum_model_class``,
``parser/graphql.py:GraphQLParser.parse_enum_as_enum_class`` and ``parser/base.py:Parser.__extract_inherited_enum``.

The rows named ``legacy-*`` keep today's output where the kind does not follow from the member values; a fix of that
output deletes its row.

Not routed here yet: ``model/base.py:DataModelFieldBase._process_const_as_literal`` classifies a const on its own and
moves with the decision on const defaults and nullability; the enum branch of
``parser/jsonschema.py:JsonSchemaParser.parse_array_fields`` (a ``type`` list holding ``array``) writes an Enum class
without asking ``enum_as_literal``, so ``--enum-field-as-literal all`` leaves that enum a class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "EnumBase",
    "EnumKind",
    "EnumKindRule",
    "EnumOrigin",
    "LiteralSetting",
    "LiteralValueKind",
    "decide_enum_kind",
    "enum_as_literal",
    "is_literal_kind",
    "is_tag_kind",
    "literal_value_kind",
]

EnumOrigin: TypeAlias = Literal["schema", "graphql", "merged"]
"""Where an enum comes from: a schema ``enum`` (JSON Schema, OpenAPI, AsyncAPI and the converted formats), a GraphQL
enum type, or the enums an ``allOf`` lists, merged into one that replaces the field-less model of the ``allOf``."""

EnumBase: TypeAlias = Literal["Enum", "IntEnum", "StrEnum"]
"""The enum class a model is written with."""

EnumKindRule: TypeAlias = Literal[
    "enum",
    "str-enum",
    "legacy-int-type-without-value-check",
    "legacy-subclass-type-without-member-check",
    "legacy-graphql-subclass-str",
    "legacy-inherited-first-base-class",
    "legacy-inherited-specialized-without-member-check",
]
"""The rows of ``decide_enum_kind``.

* ``enum``: a plain ``Enum``.
* ``str-enum``: a ``StrEnum`` for a string type whose members are all str, on a target Python that has it.
* ``legacy-int-type-without-value-check``: an ``IntEnum`` for an integer type, whatever its members are, so a member
  ``1.5`` equals the member ``1`` and a member ``'x'`` fails at import.
* ``legacy-subclass-type-without-member-check``: under ``--use-subclass-enum``, a plain ``Enum`` that also subclasses
  the value type of the schema type (``class A(str, Enum)``), whatever its members are.
* ``legacy-graphql-subclass-str``: a GraphQL ``StrEnum`` that also subclasses ``str`` under ``--use-subclass-enum``,
  where a schema enum is a ``StrEnum`` alone. It has no consistent method resolution order and fails at import, as in
  every release since 0.50.0; every dated preset for Python 3.11 or later writes it for each GraphQL enum it does not
  write as a ``Literal``.
* ``legacy-inherited-first-base-class``: the merged enum takes the class of the first enum it extends and none of
  their value type bases, instead of a kind decided from the merged members.
* ``legacy-inherited-specialized-without-member-check``: the first enum it extends is an ``IntEnum`` or a
  ``StrEnum``, which the merged enum keeps whatever the members of the other enums are (a ``StrEnum`` holding ``1.5``).
"""

LiteralSetting: TypeAlias = Literal["all", "one", "none"]
"""The ``--enum-field-as-literal`` values."""

LiteralValueKind: TypeAlias = Literal["none", "bool", "int", "float", "str", "other"]
"""The kind of a JSON value, named like the Python type of the value."""


@final
@dataclass(frozen=True, slots=True)
class EnumKind:
    """The class an enum is written as.

    ``base`` is None for the merged rows, which keep the class of the first merged enum (a parser may build its own
    ``Enum`` subclass). ``subclasses_value_type`` tells that the class also subclasses the value type of the schema
    type, before ``base``.
    """

    base: EnumBase | None
    subclasses_value_type: bool
    rule: EnumKindRule


_ENUM = EnumKind(base="Enum", subclasses_value_type=False, rule="enum")
_STR_ENUM = EnumKind(base="StrEnum", subclasses_value_type=False, rule="str-enum")
_INT_ENUM = EnumKind(base="IntEnum", subclasses_value_type=False, rule="legacy-int-type-without-value-check")
_SUBCLASS_ENUM = EnumKind(base="Enum", subclasses_value_type=True, rule="legacy-subclass-type-without-member-check")
_GRAPHQL_SUBCLASS_STR_ENUM = EnumKind(base="StrEnum", subclasses_value_type=True, rule="legacy-graphql-subclass-str")
_INHERITED = EnumKind(base=None, subclasses_value_type=False, rule="legacy-inherited-first-base-class")
_INHERITED_SPECIALIZED = EnumKind(
    base=None, subclasses_value_type=False, rule="legacy-inherited-specialized-without-member-check"
)


def decide_enum_kind(  # noqa: PLR0913
    origin: EnumOrigin,
    /,
    *,
    value_type: str | None = None,
    values: Iterable[object] = (),
    use_specialized_enum: bool = False,
    use_subclass_enum: bool = False,
    has_strenum: bool = False,
    first_specialized: bool = False,
) -> EnumKind:
    """Return the class an enum is written as (``EnumKindRule`` names the rows).

    A schema or GraphQL enum reads ``value_type``, the Python type the members of its schema type are (``int``,
    ``float``, ``bytes`` or ``str``, or None for any other type), its member ``values``, the options and whether the
    target Python has ``StrEnum``. A merged enum reads only ``first_specialized``, which tells that the first enum it
    extends is an ``IntEnum`` or a ``StrEnum``.
    """
    if origin == "merged":
        return _INHERITED_SPECIALIZED if first_specialized else _INHERITED
    if use_specialized_enum:
        if value_type == "int":
            return _INT_ENUM
        if value_type == "str" and has_strenum and all(isinstance(value, str) for value in values):
            return _GRAPHQL_SUBCLASS_STR_ENUM if origin == "graphql" and use_subclass_enum else _STR_ENUM
    if use_subclass_enum and value_type is not None:
        return _SUBCLASS_ENUM
    return _ENUM


def enum_as_literal(
    *, extension: bool | None, mapped: str | None, setting: LiteralSetting | None, member_count: int
) -> bool:
    """Return whether an enum is written as a ``Literal`` instead of an Enum class.

    The schema's ``x-enum-field-as-literal`` decides first, then the ``--enum-field-as-literal-map`` entry of the
    property (``literal`` or not), then ``--enum-field-as-literal``: ``all`` writes every enum as a ``Literal``, ``one``
    only an enum of one member. GraphQL has neither the extension nor the map.
    """
    if extension is not None:
        return extension
    if mapped is not None:
        return mapped == "literal"
    return setting == "all" or (setting == "one" and member_count == 1)


def literal_value_kind(value: object, /) -> LiteralValueKind:
    """Return the kind of a JSON value; a bool is no int."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "other"


def is_literal_kind(kind: LiteralValueKind, /, *, bool_literals: bool = True) -> bool:
    """Return whether a ``Literal`` holds a value of the kind.

    It holds a str, an int, and a bool where the output writes bool literals. Consts, one-value enums and enum member
    values given as discriminator tags read this.
    """
    return kind in {"int", "str"} or (bool_literals and kind == "bool")


def is_tag_kind(kind: LiteralValueKind, /) -> bool:
    """Return whether a value of the kind can tag a union variant for msgspec, which takes a str or an int, no bool."""
    return kind in {"int", "str"}
