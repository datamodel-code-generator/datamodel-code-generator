# Compound property names

from __future__ import annotations

from pydantic import Field, RootModel, constr


class Root1(RootModel[str]):
    root: str = Field(..., min_length=2, pattern='^a')


class Root(RootModel[dict[constr(min_length=2, pattern=r'^a'), int]]):
    root: dict[constr(min_length=2, pattern=r'^a'), int] = Field(..., title='Root')
