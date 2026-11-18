"""Review MA (3.14+): a comprehension inside a lambda or generator expression inside an evaluated annotation.

The comprehension is inlined into the lambda's or the generator expression's own table, not the annotation's.

questions: references
"""

from typing import Annotated, Literal

from fastapi import Query
from pydantic import BaseModel, Field


class Order(BaseModel):
    items: Annotated[list[int], Field(default_factory=lambda: [i * 2 for i in range(3)])]
    tags: Annotated[dict[str, str], Field(default_factory=lambda: {k: k for k in "ab"})]
    nested: Annotated[list[str], lambda: lambda: [s for s in "cd"]] = []


def read(q: Annotated[list[str], Query(default_factory=lambda: [s for s in ("a", "b")])]) -> list[str]:
    return q


def pick(x: Literal[tuple([j for j in i] for i in ("ef",))]) -> None:
    pass


TOP: Annotated[set[int], lambda: {n for n in range(2)}] = set()
