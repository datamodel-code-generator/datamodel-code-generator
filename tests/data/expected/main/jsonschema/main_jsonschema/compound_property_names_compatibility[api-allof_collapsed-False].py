# Compound property names

from __future__ import annotations

from pydantic import Field, RootModel, constr


class Root(RootModel[dict[constr(pattern=r'^a', min_length=2), int]]):
    root: dict[constr(pattern=r'^a', min_length=2), int] = Field(..., title='Root')
