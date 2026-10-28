# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel


class Address(BaseModel):
    n: int


class Root(BaseModel):
    value: Address
