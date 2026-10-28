# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel


class Value(BaseModel):
    # Template fields: inner
    inner: str


class Nested(BaseModel):
    # Template fields: value
    value: Value


class Root(RootModel[Nested]):
    root: Nested = Field(..., title='Root')
