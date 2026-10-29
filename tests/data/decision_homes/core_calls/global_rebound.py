"""Fixture: a function rebinds the imported name through a global statement."""

from __future__ import annotations

from .core import compute


def install(holder):
    """Replace the function for the whole module."""
    global compute  # noqa: PLW0603
    compute = holder.compute


def caller(x):
    """Call the name that another function may have replaced."""
    return compute(x, mode=1)
