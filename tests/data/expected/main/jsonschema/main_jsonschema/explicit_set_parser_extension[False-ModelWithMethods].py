from __future__ import annotations

from typing import Set

from pydantic import BaseModel, ConfigDict


class Item(BaseModel):
    model_config = ConfigDict(
        frozen=True,
    )
    __hash__ = object.__hash__
    value: int

    def __hash__(self) -> int: return 7


class Container(BaseModel):
    model_config = ConfigDict(
        frozen=True,
    )
    items: Set[Item]

    def __hash__(self) -> int: return 7
