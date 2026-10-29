"""Fixture: a hand-written copy of the core function is no core."""

from __future__ import annotations


def compute(x, *, mode):
    """Decide the value again."""
    return x if mode else None


def uses_copy(x):
    """Call the copy that this file defines."""
    return compute(x, mode=1)
