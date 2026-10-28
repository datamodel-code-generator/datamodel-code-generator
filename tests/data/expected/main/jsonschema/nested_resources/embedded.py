# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Nested(BaseModel):
    kind: Literal['nested']
    value: str


class Child(BaseModel):
    nested: Nested


class Root(BaseModel):
    child: Child
