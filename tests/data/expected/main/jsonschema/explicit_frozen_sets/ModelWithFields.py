from __future__ import annotations

from typing import Set

from pydantic import BaseModel, ConfigDict


class Item(BaseModel):
    model_config = ConfigDict(
        frozen=True,
    )
    __hash__ = object.__hash__
    value: object


class Container(BaseModel):
    model_config = ConfigDict(
        frozen=True,
    )
    items: Set[Item]
