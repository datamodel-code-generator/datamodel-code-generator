"""Fixture: names that only look like a rebinding leave the call a call of the core."""

from __future__ import annotations

from .core import compute


class Holder:
    """A class attribute is not in scope inside its methods."""

    compute = None

    def method(self, x):
        """Call the imported function."""
        return compute(x, mode=1)


def outer(limit):
    """Bind other names around the call."""

    def inner(x):
        return compute(x, mode=limit)

    return inner


def comprehension(values):
    """A comprehension variable of another name leaves the import alone."""
    return [compute(value, mode=1) for value in values]


def lambda_body(values):
    """So does the parameter of a lambda."""
    return list(map(lambda value: compute(value, mode=1), values))
