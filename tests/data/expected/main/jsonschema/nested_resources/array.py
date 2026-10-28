# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Item(BaseModel):
    n: int


class Items(BaseModel):
    n: int


class Root(BaseModel):
    items: list[Item]
    again: Items
