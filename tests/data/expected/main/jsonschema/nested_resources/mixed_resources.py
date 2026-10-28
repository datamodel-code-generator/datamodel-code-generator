# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel, RootModel


class Value(RootModel[str]):
    root: str


class Legacy(BaseModel):
    value: Value


class Child(BaseModel):
    value: Value


class Current(BaseModel):
    child: Child


class Root(BaseModel):
    legacy: Legacy
    current: Current
