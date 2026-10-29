"""Fixture: a relative import that climbs above the root leaves the package."""

from __future__ import annotations

from .. import compute


def above(x):
    """Call the name that the import binds."""
    return compute(x, mode=1)
