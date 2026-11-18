"""The other half of the cycle, with a function-level import and a missing name."""

from .loop import first as second


def late():
    from . import loop

    return loop.missing
