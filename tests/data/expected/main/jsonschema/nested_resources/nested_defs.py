# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel


class Value(BaseModel):
    inner: str


class Nested(BaseModel):
    value: Value


class Root(RootModel[Nested]):
    root: Nested = Field(..., title='Root')
