"""Fixture: the core of the calls home defines the function its signature names."""

from __future__ import annotations


def compute(x, *, mode):
    """Decide the value."""
    return x if mode else None


def read_flag(cfg):
    """Read the flag at home."""
    return cfg.flag
