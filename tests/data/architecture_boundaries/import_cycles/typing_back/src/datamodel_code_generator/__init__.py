"""A module-level import closed by a TYPE_CHECKING import back: lazy keys only.

Only type checkers run the import back, so no import fails; code scanning counts it at import time
(its open py/unsafe-cyclic-import alerts on parser/_rw_split.py and parser/_inherited_schemas.py).
"""
