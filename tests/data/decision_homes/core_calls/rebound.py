"""Fixture: the module binds the imported name again before the call."""

from __future__ import annotations

from .core import compute

compute = compute.wrap(compute)


def after_rebinding(x):
    """Call the name the module rebinds."""
    return compute(x, mode=1)
