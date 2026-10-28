# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Nested(BaseModel):
    embedded_marker: str


class Child(BaseModel):
    nested: Nested


class Root(BaseModel):
    child: Child
