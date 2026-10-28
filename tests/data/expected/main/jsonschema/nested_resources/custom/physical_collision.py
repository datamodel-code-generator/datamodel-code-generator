# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Nested(BaseModel):
    # Template fields: embedded_marker
    embedded_marker: str


class Child(BaseModel):
    # Template fields: nested
    nested: Nested


class Root(BaseModel):
    # Template fields: child
    child: Child
