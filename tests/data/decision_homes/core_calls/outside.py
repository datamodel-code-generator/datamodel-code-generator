"""Fixture: names that do not lead to the core."""

from __future__ import annotations

import helpers as core
from helpers import compute


class Holder:
    """Calls through values."""

    def method(self, x):
        """Call a method and a function held by an attribute."""
        return self.compute(x, mode=1), self.helper.compute(x, mode=1)


def external(x):
    """Call the name that a module outside the package binds."""
    return compute(x, mode=1), core.compute(x, mode=1)


def parameter(compute, x):
    """Call a parameter."""
    return compute(x, mode=1)
