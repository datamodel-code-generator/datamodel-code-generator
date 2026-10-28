# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel, RootModel


class Value(RootModel[str]):
    root: str


class Child(BaseModel):
    value: Value


class Root(BaseModel):
    legacy: Child
    modern: Child
