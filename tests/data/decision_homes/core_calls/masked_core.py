"""Fixture: a core module whose method named like the function is not the name the module binds."""

from __future__ import annotations

from . import copy as other


class Helper:
    """Holds a method that the module-level name does not refer to."""

    def compute(self, x, *, mode):
        """Decide the value in a method."""
        return x if mode else None


compute = other.compute
