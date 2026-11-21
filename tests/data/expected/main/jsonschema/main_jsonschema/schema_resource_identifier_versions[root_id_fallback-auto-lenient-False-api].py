# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Child(BaseModel):
    value: str


class Root(BaseModel):
    child: Child
