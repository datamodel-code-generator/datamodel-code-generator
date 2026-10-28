# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Left(BaseModel):
    # Template fields: left
    left: int


class Right(BaseModel):
    # Template fields: right
    right: str


class Root(BaseModel):
    # Template fields: left right
    left: Left
    right: Right
