# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Value(BaseModel):
    n: int


class Root(BaseModel):
    value: Value
