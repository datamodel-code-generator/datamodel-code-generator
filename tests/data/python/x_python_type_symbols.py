"""Runtime symbols that end-to-end fixtures import into generated models."""

from __future__ import annotations

from typing import TypeVar

_T = TypeVar("_T")


class StrEnum(str):  # noqa: FURB189  # The mapped target intentionally has string semantics.
    """A target-independent stand-in for an explicitly mapped schema type."""

    __slots__ = ()


def Literal_aliased(cls: _T) -> _T:
    """Return a class unchanged, under the name that an alias of ``typing.Literal`` would take."""
    return cls
