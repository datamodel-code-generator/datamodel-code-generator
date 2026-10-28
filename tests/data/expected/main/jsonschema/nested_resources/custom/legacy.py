# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Address(BaseModel):
    # Template fields: n
    n: int


class Root(BaseModel):
    # Template fields: value
    value: Address
