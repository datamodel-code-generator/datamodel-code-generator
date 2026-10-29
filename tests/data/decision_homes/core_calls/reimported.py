"""Fixture: the module imports the name from a copy as well."""

from __future__ import annotations

from .copy import compute
from .core import compute  # noqa: F811


def after_both_imports(x):
    """Call the name that both imports bind."""
    return compute(x, mode=1)
