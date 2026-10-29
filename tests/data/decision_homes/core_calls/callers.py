"""Fixture: calls of the core outside it are no violation, however the file reaches the function."""

from __future__ import annotations

import core_calls.core as home_core
from core_calls import reexport

from . import core
from .reexport import compute


def by_name(x):
    """Call the function by the name it imports."""
    return compute(x, mode=1)


def by_module(x):
    """Call it through the module, through an absolute alias and through the re-export."""
    return core.compute(x, mode=1), home_core.compute(x, mode=2), reexport.compute(x, mode=3)


def raw_read(cfg):
    """Another signature is still reported beside the calls."""
    return compute(cfg.x, mode=cfg.flag)
