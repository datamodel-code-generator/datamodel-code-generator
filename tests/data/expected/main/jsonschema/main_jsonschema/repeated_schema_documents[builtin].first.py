from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, RootModel


class Item(RootModel[str]):
    root: str


class First(BaseModel):
    value: Optional[Item] = None
