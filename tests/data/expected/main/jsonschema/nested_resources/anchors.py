# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Left(BaseModel):
    left: int


class Right(BaseModel):
    right: str


class Root(BaseModel):
    left: Left
    right: Right
