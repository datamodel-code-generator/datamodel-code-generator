"""Review MA (3.14+): comprehensions in a function-local annotation, which is never evaluated, directly and in a lambda.

questions: references
"""

from typing import Annotated


def collect() -> list[int]:
    values: Annotated[list[int], lambda: [i for i in "ab"]] = []
    names: Annotated[list[str], [j for j in "cd"]] = []
    return values + [len(names)]
