# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel, RootModel


class Value(RootModel[str]):
    root: str


class Legacy(BaseModel):
    # Template fields: value
    value: Value


class Child(BaseModel):
    # Template fields: value
    value: Value


class Current(BaseModel):
    # Template fields: child
    child: Child


class Root(BaseModel):
    # Template fields: legacy current
    legacy: Legacy
    current: Current
