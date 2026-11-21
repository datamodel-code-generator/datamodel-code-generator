# first
# second

from __future__ import annotations

from pydantic import BaseModel, RootModel


class Item(BaseModel):
    id: int | None = None


class ItemsGetResponse(RootModel[list[Item]]):
    root: list[Item]
