"""Fixture: an import inside the function leads to the core as well."""

from __future__ import annotations


def late(x):
    """Import the function where it is used."""
    from .core import compute

    return compute(x, mode=1)
