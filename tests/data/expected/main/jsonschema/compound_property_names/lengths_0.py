# Compound property names

from __future__ import annotations

from pydantic import Field, RootModel, constr


class Root(RootModel[dict[constr(min_length=3) | constr(max_length=1), int]]):
    root: dict[constr(min_length=3) | constr(max_length=1), int] = Field(
        ..., title='Root'
    )
