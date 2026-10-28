# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Nested(BaseModel):
    # Template fields: kind value
    kind: Literal['nested']
    value: str


class Child(BaseModel):
    # Template fields: nested
    nested: Nested


class Root(BaseModel):
    # Template fields: child
    child: Child
