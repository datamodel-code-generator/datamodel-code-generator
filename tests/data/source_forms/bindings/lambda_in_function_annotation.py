"""Review R19, must M3: a lambda inside an evaluated parameter annotation (3.14: inside __annotate__).

questions: references
"""

from typing import Annotated


def f(x: Annotated[int, lambda v: v]) -> None:
    local: Annotated[int, lambda: 0] = 1
    return local
