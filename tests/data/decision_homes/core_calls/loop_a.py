"""Fixture: two modules that import the name from each other never reach a definition."""

from __future__ import annotations

from .loop_b import compute


def loop(x):
    """Call the name that loops."""
    return compute(x, mode=1)
