# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Chosen(BaseModel):
    left: str


class Chosen1(BaseModel):
    right: int


class Field0(BaseModel):
    left: str


class Root(BaseModel):
    chosen: Chosen | Chosen1
    again: Field0
