# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Value(BaseModel):
    # Template fields: n
    n: int


class Root(BaseModel):
    # Template fields: value
    value: Value
