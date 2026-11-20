"""What the table module reads, and a table that is no literal."""

from ._lazy_exports import module_hooks

LIMIT = 16


class Base:
    pass


class Other:
    pass


class Remote:
    pass


__getattr__, __dir__ = module_hooks(__name__, dict(name="Base"))
