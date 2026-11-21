from __future__ import annotations

from typing import Set

from pydantic import BaseModel, ConfigDict


@(lambda cls: cls)
class Item(BaseModel):
    model_config = ConfigDict(
        frozen=True,
    )
    __hash__ = object.__hash__
    value: int


@(lambda cls: cls)
class Container(BaseModel):
    model_config = ConfigDict(
        frozen=True,
    )
    items: Set[Item]
