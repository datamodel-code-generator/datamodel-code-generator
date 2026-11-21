"""Annotations are evaluated at module and class level and for parameters, never for function locals.

questions: bindings
"""

from typing import Annotated

top: int = 1


class Box:
    size: int


def f(x: int) -> str:
    local: Annotated[str, lambda: 0] = ""
    return local
