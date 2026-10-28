"""Fixture: the core of the sample home may contain every shape."""

from __future__ import annotations

MAX_BITS = 2**63
LETTERS = ("a", "b", "c")


def decide(cfg, types):
    """Read the flag and test the type list."""
    cfg.flag = cfg.flag + 1
    return compute(cfg.x, mode=1) if "null" in types else None


def _merge_in_core(value):
    return value
