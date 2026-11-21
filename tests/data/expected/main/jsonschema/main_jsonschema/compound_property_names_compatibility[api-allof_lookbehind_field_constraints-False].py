# Compound property names

from __future__ import annotations

from pydantic import Field, RootModel


class Root1(RootModel[str]):
    root: str


class Root(RootModel[dict[str, int]]):
    root: dict[str, int] = Field(..., title='Root')
