# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Dep(BaseModel):
    # Template fields: right_marker
    right_marker: str


class Nested(BaseModel):
    # Template fields: dep
    dep: Dep


class Child(BaseModel):
    # Template fields: nested
    nested: Nested


class Root(BaseModel):
    # Template fields: child
    child: Child
