# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Dep(BaseModel):
    # Template fields: marker
    marker: str


class Child(BaseModel):
    # Template fields: dep
    dep: Dep


class Root(BaseModel):
    # Template fields: child
    child: Child
