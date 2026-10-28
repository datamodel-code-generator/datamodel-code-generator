# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Dep(BaseModel):
    marker: str


class Child(BaseModel):
    dep: Dep


class Root(BaseModel):
    child: Child
