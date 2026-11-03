"""Copy on change: a rewrite keeps the original value when it changed none of its parts.

A rewrite of a node or a container rewrites each part, then asks ``unchanged`` whether every rewritten part is its
original part, the same object; then it returns the original, so that the value stays shared with every holder, and
builds a new value only otherwise. This module imports only the standard library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def unchanged(rewritten: Iterable[object], original: Iterable[object]) -> bool:
    """Return whether each rewritten part is the original part at its position, the same object."""
    return all(new is old for new, old in zip(rewritten, original, strict=True))


__all__ = ["unchanged"]
