# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Dep(BaseModel):
    right_marker: str


class Nested(BaseModel):
    dep: Dep


class Child(BaseModel):
    nested: Nested


class Root(BaseModel):
    child: Child
