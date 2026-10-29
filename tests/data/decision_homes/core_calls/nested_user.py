"""Fixture: a nested definition of a core module is not the function the module exports."""

from __future__ import annotations

from .nested_core import compute


def uses_nested(x):
    """Call the name the module binds to another function than its nested one."""
    return compute(x, mode=1)
