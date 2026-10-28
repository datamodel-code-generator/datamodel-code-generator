# Compound property names

from __future__ import annotations

from pydantic import Field, RootModel, constr


class Root(RootModel[dict[constr(min_length=2, max_length=3), int]]):
    root: dict[constr(min_length=2, max_length=3), int] = Field(..., title='Root')
