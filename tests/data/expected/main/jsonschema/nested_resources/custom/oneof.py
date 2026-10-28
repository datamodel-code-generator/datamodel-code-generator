# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Chosen(BaseModel):
    # Template fields: left
    left: str


class Chosen1(BaseModel):
    # Template fields: right
    right: int


class Field0(BaseModel):
    # Template fields: left
    left: str


class Root(BaseModel):
    # Template fields: chosen again
    chosen: Chosen | Chosen1
    again: Field0
