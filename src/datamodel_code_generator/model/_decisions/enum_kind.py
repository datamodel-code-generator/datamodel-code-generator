"""Enum class kind and literal value kind: which class an enum is written as, and which values a ``Literal`` holds.

Every input is an immutable fact passed by keyword, so the functions read nothing else. The adapters read the facts
from the live objects and build the class through ``model/enum.py:_enum_class``:
``parser/jsonschema.py:JsonSchemaParser._get_enum_model_class``,
``parser/graphql.py:GraphQLParser.parse_enum_as_enum_class`` and ``parser/_passes/modules.py:extract_inherited_enum``.

The rows named ``legacy-*`` keep today's output where the kind does not follow from the member values; a fix of that
output deletes its row.

Not routed here yet: the enum branch of ``parser/jsonschema.py:JsonSchemaParser.parse_array_fields`` (a ``type`` list
holding ``array``) writes an Enum class without asking ``enum_as_literal``, so ``--enum-field-as-literal all`` leaves
that enum a class.
"""

from __future__ import annotations

from math import isnan
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

__all__ = [
    "EnumBase",
    "EnumKind",
    "EnumKindRule",
    "EnumOrigin",
    "LiteralSetting",
    "LiteralValueKind",
    "decide_enum_kind",
    "enum_as_literal",
    "holds_inherited_member_value",
    "holds_member_value",
    "is_literal_kind",
    "is_tag_kind",
    "keeps_member_value",
    "literal_value_kind",
]

EnumOrigin: TypeAlias = Literal["schema", "graphql", "merged"]
"""Where an enum comes from: a schema ``enum`` (JSON Schema, OpenAPI, AsyncAPI and the converted formats), a GraphQL
enum type, or the enums an ``allOf`` lists, merged into one that replaces the field-less model of the ``allOf``.
Schema and GraphQL enums follow the same rows."""

EnumBase: TypeAlias = Literal["Enum", "IntEnum", "StrEnum"]
"""The enum class a model is written with."""

EnumKindRule: TypeAlias = Literal[
    "enum",
    "enum-value-base-changes-a-member",
    "int-enum",
    "str-enum",
    "subclass-value-type",
    "legacy-value-base-changing-a-member",
    "legacy-msgspec-decodable-value-base",
    "legacy-inherited-first-base-class",
    "legacy-inherited-specialized-without-member-check",
]
"""The rows of ``decide_enum_kind``.

* ``enum``: a plain ``Enum``: no value base is asked for, or the schema type has no value type.
* ``enum-value-base-changes-a-member``: a plain ``Enum``, since the value base asked for would change or refuse a
  member value (``T(v) != v``, NaN equal to itself; ``bytes`` keeps no JSON value), as ``1.5`` or ``'x'`` beside an
  integer type would. Without ``--keep-enum-member-values`` only a base that refuses a member (``T(v)`` fails, so the
  class fails at import) gives this row.
* ``int-enum``: an ``IntEnum`` for an integer type whose members ``int`` keeps (ints, bools and integral floats). A
  bool counts as its int, as Python's ``==`` has it, so const branches ``[1, true]`` stay an ``IntEnum`` (``True``
  an alias of ``1``), as in 0.86.0.
* ``str-enum``: a ``StrEnum`` for a string type whose members are all str, on a target Python that has it. Like an
  ``IntEnum``, it subclasses its value type already, so ``--use-subclass-enum`` adds no value type base.
* ``subclass-value-type``: under ``--use-subclass-enum``, a plain ``Enum`` that also subclasses the value type of the
  schema type (``class A(str, Enum)``), which keeps every member.
* ``legacy-value-base-changing-a-member``: without ``--keep-enum-member-values``, the 0.86.0 ``IntEnum`` or
  ``(T, Enum)`` of the schema type where it changes a member value it constructs, as ``2.5`` written as ``2`` in an
  ``IntEnum`` or ``1`` as ``'1'`` in a ``(str, Enum)``. A ``StrEnum`` still needs every value a str, ``None``
  included, as in 0.86.0.
* ``legacy-msgspec-decodable-value-base``: with ``--keep-enum-member-values``, for an output that decodes only Enums
  whose values are all str or all int (msgspec), the 0.86.0 ``IntEnum`` or ``(T, Enum)`` where the plain ``Enum`` of
  ``enum-value-base-changes-a-member`` would hold values of other kinds and so fail every decode, and where ``T``
  (``int`` or ``str``) constructs every member, so the class imports and decodes, though with the changed members. It
  stays until msgspec Enums are decided as a whole (G16).
* ``legacy-inherited-first-base-class``: the merged enum takes the class of the first enum it extends and none of
  their value type bases, instead of a kind decided from the merged members.
* ``legacy-inherited-specialized-without-member-check``: the first enum it extends is an ``IntEnum`` or a
  ``StrEnum``, which the merged enum keeps whatever the members of the other enums are (an ``IntEnum`` holding ``2.5``
  as ``2``), except a member the class cannot hold (``holds_inherited_member_value``), as ``None`` or ``'bad'`` in an
  ``IntEnum``, which the merged enum leaves out.
"""

LiteralSetting: TypeAlias = Literal["all", "one", "none"]
"""The ``--enum-field-as-literal`` values."""

LiteralValueKind: TypeAlias = Literal["none", "bool", "int", "float", "str", "other"]
"""The kind of a JSON value, named like the Python type of the value."""


@final
@record
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
_MEMBER_CHANGING_ENUM = EnumKind(base="Enum", subclasses_value_type=False, rule="enum-value-base-changes-a-member")
_INT_ENUM = EnumKind(base="IntEnum", subclasses_value_type=False, rule="int-enum")
_STR_ENUM = EnumKind(base="StrEnum", subclasses_value_type=False, rule="str-enum")
_SUBCLASS_ENUM = EnumKind(base="Enum", subclasses_value_type=True, rule="subclass-value-type")
_CHANGING_INT_ENUM = EnumKind(base="IntEnum", subclasses_value_type=False, rule="legacy-value-base-changing-a-member")
_CHANGING_SUBCLASS_ENUM = EnumKind(base="Enum", subclasses_value_type=True, rule="legacy-value-base-changing-a-member")
_MSGSPEC_INT_ENUM = EnumKind(base="IntEnum", subclasses_value_type=False, rule="legacy-msgspec-decodable-value-base")
_MSGSPEC_SUBCLASS_ENUM = EnumKind(base="Enum", subclasses_value_type=True, rule="legacy-msgspec-decodable-value-base")
_INHERITED = EnumKind(base=None, subclasses_value_type=False, rule="legacy-inherited-first-base-class")
_INHERITED_SPECIALIZED = EnumKind(
    base=None, subclasses_value_type=False, rule="legacy-inherited-specialized-without-member-check"
)


def decide_enum_kind(  # noqa: PLR0913
    origin: EnumOrigin,
    /,
    *,
    value_type: str | None = None,
    values: Collection[object] = (),
    use_specialized_enum: bool = False,
    use_subclass_enum: bool = False,
    has_strenum: bool = False,
    first_specialized: bool = False,
    decodes_mixed_values: bool = True,
    keep_member_values: bool = False,
) -> EnumKind:
    """Return the class an enum is written as (``EnumKindRule`` names the rows).

    A schema or GraphQL enum reads ``value_type``, the Python type the members of its schema type are (``int``,
    ``float``, ``bytes`` or ``str``, or None for any other type), its member ``values``, the options and whether the
    target Python has ``StrEnum``. A merged enum reads only ``first_specialized``, which tells that the first enum it
    extends is an ``IntEnum`` or a ``StrEnum``. An ``IntEnum`` or ``StrEnum`` subclasses its value type already, so
    only a plain ``Enum`` takes the value type base of ``--use-subclass-enum``. A ``None`` among the ``values`` is no
    member of a class with a value base. With ``keep_member_values`` (``--keep-enum-member-values``) a value base is
    taken only when it keeps every member value (``keeps_member_value``), and ``decodes_mixed_values`` tells that the
    output decodes an Enum whose values are not all str or all int; without it the base follows the schema type, as
    in 0.86.0, unless it refuses a member (``holds_member_value``).
    """
    if origin == "merged":
        return _INHERITED_SPECIALIZED if first_specialized else _INHERITED
    specialized = use_specialized_enum and (
        value_type == "int"
        or (
            value_type == "str"
            and has_strenum
            and (keep_member_values or all(isinstance(value, str) for value in values))
        )
    )
    if value_type is None or not (specialized or use_subclass_enum):
        return _ENUM
    members = [value for value in values if value is not None]
    if not all(holds_member_value(value_type, value, keep_member_values=keep_member_values) for value in members):
        if (
            keep_member_values
            and not decodes_mixed_values
            and (
                legacy := _decodable_legacy_kind(value_type, members, int_enum=specialized, subclass=use_subclass_enum)
            )
        ):
            return legacy
        return _MEMBER_CHANGING_ENUM
    if not all(keeps_member_value(value_type, value) for value in members):
        return _CHANGING_INT_ENUM if specialized else _CHANGING_SUBCLASS_ENUM
    return (_INT_ENUM if value_type == "int" else _STR_ENUM) if specialized else _SUBCLASS_ENUM


def _decodable_legacy_kind(
    value_type: str, values: Collection[object], *, int_enum: bool, subclass: bool
) -> EnumKind | None:
    """Return the 0.86.0 kind of an enum whose plain ``Enum`` an output decoding only str or int Enums would refuse.

    That is the ``IntEnum`` of a specialized integer type, else the ``(T, Enum)`` of ``--use-subclass-enum``, when
    ``T`` is ``int`` or ``str`` and constructs every member; None when the plain ``Enum`` decodes (all its values are
    str or all are int) or when the 0.86.0 class failed at import.
    """
    if {literal_value_kind(value) for value in values} in ({"str"}, {"int"}) or value_type not in {"int", "str"}:
        return None
    if not all(_constructs(value_type, value) for value in values):
        return None
    if int_enum and value_type == "int":
        return _MSGSPEC_INT_ENUM
    return _MSGSPEC_SUBCLASS_ENUM if subclass else None


def _constructs(value_type: str, value: object) -> bool:
    """Return whether the value type constructs a member from the value, as an enum class subclassing it does.

    ``bytes`` of an int allocates that many bytes, so an int up to the size of a ``bytes`` counts as constructed
    without building it; a negative one or one beyond that size raises at import.
    """
    if value_type == "bytes" and isinstance(value, int):
        return 0 <= value <= _MAX_BYTES_SIZE
    try:
        _CONSTRUCTORS[value_type](value)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


_MAX_BYTES_SIZE: Final = 2**63 - 1
"""The largest size ``bytes`` accepts (``Py_ssize_t``); a larger count raises ``OverflowError``."""

_VALUE_TYPES: Final[Mapping[str, Callable[[Any], object]]] = {"int": int, "float": float, "str": str}
"""The constructors of the value types a value base can subclass; ``bytes`` keeps no JSON value, so it has none."""

_CONSTRUCTORS: Final[Mapping[str, Callable[[Any], object]]] = {**_VALUE_TYPES, "bytes": bytes}
"""The constructors of every value type a value base can subclass, ``bytes`` included."""


def holds_member_value(value_type: str, value: object, /, *, keep_member_values: bool) -> bool:
    """Return whether a class subclassing the value type can hold the member value.

    With ``keep_member_values`` (``--keep-enum-member-values``) it must keep the value as written
    (``keeps_member_value``); without it, as in 0.86.0, it only must construct a member from it, so the class imports.
    """
    if keep_member_values:
        return keeps_member_value(value_type, value)
    return _constructs(value_type, value)


def holds_inherited_member_value(value_type: str, value: object, /) -> bool:
    """Return whether the ``IntEnum`` (``int``) or ``StrEnum`` (``str``) a merged enum keeps imports with the value.

    An ``IntEnum`` constructs a member with ``int(value)``, as ``holds_member_value`` does without
    ``--keep-enum-member-values``; a ``StrEnum`` takes only a str, whatever ``str(value)`` gives.
    """
    return isinstance(value, str) if value_type == "str" else _constructs(value_type, value)


def keeps_member_value(value_type: str, value: object, /) -> bool:
    """Return whether a member of an enum whose class subclasses the value type keeps its value as written.

    The value type ``T`` keeps ``v`` when ``T(v) == v``, NaN being equal to itself: ``int`` keeps ints, bools (a bool
    equals its int) and integral floats, ``float`` any number, ``str`` only strings, and ``bytes`` no JSON value.
    """
    if value_type == "float" and isinstance(value, float) and isnan(value):
        return True
    if (construct := _VALUE_TYPES.get(value_type)) is None:
        return False
    try:
        return construct(value) == value
    except (TypeError, ValueError, OverflowError):
        return False


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
