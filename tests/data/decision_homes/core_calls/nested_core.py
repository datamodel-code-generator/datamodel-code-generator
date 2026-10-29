"""Fixture: a core module whose nested function named like the function is not the name the module binds."""

from __future__ import annotations

from . import copy as other


def factory():
    """Define a function that only this function sees."""

    def compute(x, *, mode):
        return x if mode else None

    return compute


compute = other.compute
