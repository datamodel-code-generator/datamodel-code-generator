"""Install the wrappers of ``seams.SEAMS``."""

from __future__ import annotations

from importlib import import_module

from tests.checks.seams import MODULES, apply


def install() -> None:
    """Import every module that defines or holds a seam and wrap its seams; a second call changes nothing."""
    for name in sorted(MODULES):
        apply(import_module(name))
