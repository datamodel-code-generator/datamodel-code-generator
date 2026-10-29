"""Fixture: a relative import from a package below the root leads to the core."""

from __future__ import annotations

from ..core import compute


def deep(x):
    """Call the function of the core."""
    return compute(x, mode=1)
