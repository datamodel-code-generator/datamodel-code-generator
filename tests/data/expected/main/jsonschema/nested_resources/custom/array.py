# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Item(BaseModel):
    # Template fields: n
    n: int


class Items(BaseModel):
    # Template fields: n
    n: int


class Root(BaseModel):
    # Template fields: items again
    items: list[Item]
    again: Items
