"""The phases of the pipeline of ``Parser.parse()``, which the generation store enters in order.

What each phase may write, and which attribute holds which fact, is checked outside the package: the fact tables are
``scripts/fact_tables.py`` and the pass declarations ``scripts/pass_decls.py``.
"""

from __future__ import annotations

from enum import Enum, unique

__all__ = ["Phase"]


@unique
class Phase(Enum):
    """Pipeline phases in execution order.

    ``PARSE`` is L0 and L1, ``SETTLE`` L2, ``STRUCTURE`` L3, ``PLAN`` L4, ``MODULES`` L5, ``CROSS_MODULE`` the L6 passes
    up to the generic base class, ``DECIDE`` the decision phase after B1, ``RENDER_PREPARATION`` the passes that
    prepare rendering, ``IMPORTS`` final names and module imports, ``RENDER`` L7.
    """

    PARSE = 0
    SETTLE = 1
    STRUCTURE = 2
    PLAN = 3
    MODULES = 4
    CROSS_MODULE = 5
    DECIDE = 6
    RENDER_PREPARATION = 7
    IMPORTS = 8
    RENDER = 9
