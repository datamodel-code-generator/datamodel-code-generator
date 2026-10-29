"""Fixture: importing the copy does not lead to the core."""

from __future__ import annotations

from .copy import compute


def uses_copy(x):
    """Call the copy of another file."""
    return compute(x, mode=1)
