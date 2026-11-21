"""Review R34, must M3: comprehensions inside evaluated annotations, at module, class and parameter level.

questions: references
"""

from typing import Annotated, Literal


def f(x: Literal[tuple(i for i in "ab")], y: Annotated[int, [j for j in "cd"]]) -> None:
    pass


class Box:
    size: Annotated[int, {k: k for k in "ef"}] = 0


TOP: Annotated[int, [m for m in "gh"]] = 1
