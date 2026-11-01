"""All or none: a sequence read item by item reads nothing when one of its items reads nothing.

A reader that turns each item into a value, or into None when the item has none, gives the values of every item, in
order, or None for the whole sequence. ``all_or_none`` stops at the first None of a lazy iterable, so a caller whose
reads must all run (each records its own problem) passes them as a list. This module imports only the standard
library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterable

_T = TypeVar("_T")


def all_or_none(values: Iterable[_T | None], /) -> tuple[_T, ...] | None:
    """Return the values in order, or None when one of them is None."""
    found: list[_T] = []
    for value in values:
        if value is None:
            return None
        found.append(value)
    return tuple(found)


__all__ = ["all_or_none"]
