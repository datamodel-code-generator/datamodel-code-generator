"""Fixture: every signature kind outside the home."""

from __future__ import annotations

LIMIT = 2**63
SHIFTED = 1 << 63
BYTE = 255
SMALL_SHIFT = 1 << 62
KEYWORDS = {"a", "b", "c", "d", "e"}
TWO = ("a", "b")
TABLE = {"a": 1, "b": 2, "c": 3}


class Other:
    """Reads and writes the decision itself."""

    def method(self, cfg, other, types):
        """Read twice, write, call, test a type list."""
        first = cfg.flag
        second = cfg.flag
        other.flag = first + second
        size = 3
        self.size = size
        compute(cfg.x, mode=1)
        compute(cfg.y, mode=1)
        compute(cfg.x, mode=1, fast=True)
        compute(cfg.x, mode=1, fast=False)
        compute(cfg.x, mode=1, **types)
        compute(cfg.x)
        return "null" in types, "x" in types, "null" not in types


def _merge_values(value):
    async def _merge_inner():
        return value

    return _merge_inner


def other_helper(types):
    """Is not a merge function."""
    return {"a", "b", "c"} & set(types)
