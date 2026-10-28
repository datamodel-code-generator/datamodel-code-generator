"""Fixture: one adapter method of the sample home and a function that is not one."""

from __future__ import annotations


class Adapter:
    """Holds the adapter."""

    def read(self, cfg, types):
        """Adapters may use the shapes."""
        flag = cfg.flag
        return compute(cfg.x, mode=flag) if "null" in types else None

    def read_again(self, cfg):
        """A method beside the adapter is not covered."""
        return cfg.flag


def _merge_adapter_helper(value):
    return value
