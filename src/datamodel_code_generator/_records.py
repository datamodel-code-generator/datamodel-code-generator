"""The decorator of the generator's internal records: frozen for the type checker, not guarded at run time.

A record is immutable by a convention that ``ty`` enforces (every assignment to a field is an error), so the run time
does not pay the ``__setattr__`` guard that ``@dataclass(frozen=True)`` adds to each ``__init__``. Equality, hashing,
``replace``, ``fields`` and ``__post_init__`` behave as for a frozen dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar, overload

from typing_extensions import dataclass_transform

if TYPE_CHECKING:
    from collections.abc import Callable

_T = TypeVar("_T")


@overload
def record(cls: type[_T], /) -> type[_T]:  # noqa: D418 - CodeQL flags an ellipsis body.
    """Declare ``cls`` a record."""


@overload
def record(*, eq: bool = True, slots: bool = True) -> Callable[[type[_T]], type[_T]]:  # noqa: D418
    """Return the decorator that declares a class a record with these options."""


@dataclass_transform(frozen_default=True, field_specifiers=(field,))
def record(cls: type[Any] | None = None, /, *, eq: bool = True, slots: bool = True) -> Any:
    """Declare ``cls`` a record: a dataclass the type checker treats as frozen, with value-based hash when ``eq``."""

    def wrap(target: type[_T]) -> type[_T]:
        return dataclass(target, slots=slots, eq=eq, unsafe_hash=eq)

    return wrap if cls is None else wrap(cls)
