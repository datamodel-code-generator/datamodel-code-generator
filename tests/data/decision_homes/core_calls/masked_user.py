"""Fixture: importing a name that a core module rebinds does not lead to a definition of the core."""

from __future__ import annotations

from .masked_core import compute


def uses_masked(x):
    """Call the name the module binds to another function than its method."""
    return compute(x, mode=1)
