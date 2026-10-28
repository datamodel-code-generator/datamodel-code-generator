# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel, RootModel


class Value(RootModel[str]):
    root: str


class ValueModel(RootModel[int]):
    root: int


class Child(BaseModel):
    value: ValueModel


class Root(BaseModel):
    child: Child
