# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from pydantic import BaseModel, RootModel


class Value(RootModel[str]):
    root: str


class Root(BaseModel):
    value: Value | None
