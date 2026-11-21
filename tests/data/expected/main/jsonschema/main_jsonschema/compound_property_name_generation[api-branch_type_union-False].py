# Compound property names

from __future__ import annotations

from pydantic import Field, RootModel, constr


class Root(RootModel[dict[constr(pattern=r'^a') | constr(pattern=r'^b'), int]]):
    root: dict[constr(pattern=r'^a') | constr(pattern=r'^b'), int] = Field(
        ..., title='Root'
    )
