"""A module that reads another module's constants, at import time and in a function, also through a local import."""

from .rows import KEPT

AT_IMPORT = KEPT


def read(value: str) -> str:
    from .rows import DOCUMENTED as LOCAL

    return KEPT + value + str(LOCAL)
