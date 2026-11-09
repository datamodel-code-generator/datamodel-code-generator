"""Bounded maps of the most recently used keys, for the caches of a run and of a process."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["BoundedLru", "SeenKeys"]

_K = TypeVar("_K")
_V = TypeVar("_V")


class BoundedLru(OrderedDict[_K, _V]):
    """A map that orders its keys from the least to the most recently used and drops the least used past a bound.

    A value of None reads as no value. The caller gives the bound with each write, so a changed bound applies to the
    next write.
    """

    __slots__ = ()

    def recent(self, key: _K, accept: Callable[[_V], bool] | None = None) -> _V | None:
        """Return the value of ``key`` as the most recently used, None when it has none or ``accept`` refuses it."""
        if (value := self.get(key)) is None or (accept is not None and not accept(value)):
            return None
        self.move_to_end(key)
        return value

    def put(self, key: _K, value: _V, max_size: int) -> list[tuple[_K, _V]]:
        """Store ``value`` as the most recently used and return the entries dropped past ``max_size``, oldest first."""
        self[key] = value
        self.move_to_end(key)
        evicted: list[tuple[_K, _V]] = []
        while len(self) > max_size:
            evicted.append(self.popitem(last=False))
        return evicted


class SeenKeys(BoundedLru[_K, None]):
    """The most recently seen keys up to a bound: a cache that keeps a value from the second sighting of its key."""

    __slots__ = ()

    def mark(self, key: _K, max_size: int) -> bool:
        """Mark ``key`` as the most recently seen and return whether it was seen before."""
        seen = key in self
        self.put(key, None, max_size)
        return seen
