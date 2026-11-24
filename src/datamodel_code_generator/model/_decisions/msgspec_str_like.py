"""Which members of a union msgspec counts as str-like, of which a union holds at most one.

msgspec decodes a union only if it holds at most one member that it decodes from a JSON string: ``str`` (with the str
values of a ``Literal``, which count as the same member), an Enum of str values, ``bytes``, ``bytearray``, the dates and
times, ``UUID`` and ``Decimal``. The msgspec decoding warnings (``parser/_passes/imports.py:_msgspec_member_slots``)
and the decision of ``--merge-msgspec-str-like-union-members`` (``parser/_str_like_unions.py``) walk a union through
``parser/_passes/_shared.py:msgspec_union_members``, read a member through ``types.py:_written_member`` and a leaf
member through ``msgspec_str_like_leaf``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from datamodel_code_generator.model._decisions.enum_kind import literal_value_kind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from datamodel_code_generator.types import DataType

__all__ = ["MSGSPEC_STR_LIKE_TYPES", "STR", "merged_str_like_union_message", "msgspec_str_like_leaf"]

MSGSPEC_STR_LIKE_TYPES: Final[frozenset[str]] = frozenset({
    "bytes",
    "bytearray",
    "datetime",
    "date",
    "time",
    "timedelta",
    "UUID",
    "Decimal",
})
"""The types besides ``str`` that msgspec decodes from a JSON string, of which a union holds at most one."""

STR: Final = "str"
"""The builtin str, which the str values of a ``Literal`` count as."""


def msgspec_str_like_leaf(leaf: DataType) -> str | None:
    """Return the str-like member msgspec counts a type that names no model as, or None when it counts as none.

    ``str`` and a ``Literal`` holding a str value count as ``str``; every other str-like type counts as its own name.
    A type with members, or one that writes enum member literals, is no leaf.
    """
    if leaf.literals:
        counted = STR if any(literal_value_kind(literal) == STR for literal in leaf.literals) else None
    else:
        counted = leaf.type if leaf.type == STR or leaf.type in MSGSPEC_STR_LIKE_TYPES else None
    return None if leaf.data_types or leaf.enum_member_literals else counted


def merged_str_like_union_message(name: str, members: Sequence[str]) -> str:
    """Describe a union whose str-like members ``--merge-msgspec-str-like-union-members`` wrote as one ``str``."""
    return (
        f"--merge-msgspec-str-like-union-members writes {name} with str in place of its str-like members "
        f"{', '.join(members)}, which a msgspec union holds only one of; the union accepts every string and decodes "
        "it as str, without the formats and constraints of those members."
    )
