"""Internal leaf utilities shared by parser and model modules."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel
from typing_extensions import Sentinel


@runtime_checkable
class HashableComparable(Hashable, Protocol):
    """Protocol for types that are both hashable and support comparison."""

    def __lt__(self, value: Any, /) -> bool:
        raise NotImplementedError

    def __le__(self, value: Any, /) -> bool:
        raise NotImplementedError

    def __gt__(self, value: Any, /) -> bool:
        raise NotImplementedError

    def __ge__(self, value: Any, /) -> bool:
        raise NotImplementedError


def to_hashable(item: Any) -> HashableComparable:
    """Convert an item to a hashable and comparable representation."""
    if isinstance(
        item,
        (
            list,
            tuple,
        ),
    ):
        try:
            return tuple(sorted((to_hashable(i) for i in item), key=lambda v: (str(type(v)), v)))
        except TypeError:
            return tuple(to_hashable(i) for i in item)
    if isinstance(item, dict):
        return tuple(
            sorted(
                (
                    k,
                    to_hashable(v),
                )
                for k, v in item.items()
            )
        )
    if isinstance(item, set):
        return frozenset(to_hashable(i) for i in item)
    if isinstance(item, BaseModel):
        return to_hashable(item.model_dump())
    return item


@runtime_checkable
class Child(Protocol):
    """Protocol for objects with a parent reference."""

    @property
    def parent(self) -> Any | None:
        """Get the parent object reference."""
        raise NotImplementedError


T = TypeVar("T")
_NO_PARENT = Sentinel("_NO_PARENT")


def get_most_of_parent(value: Any, type_: type[T] | None = None) -> T | None:
    """Traverse parent chain to find the outermost matching parent."""
    parent = getattr(value, "parent", _NO_PARENT)
    match (parent is not _NO_PARENT, type_ is None, type_ is not None and isinstance(value, type_)):
        case (True, True, _) | (True, False, False):
            return get_most_of_parent(parent, type_)
        case (_, False, False):
            return None
        case _:
            return value
