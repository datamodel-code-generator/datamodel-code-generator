# Compound property names

from __future__ import annotations

from pydantic import ConfigDict, Field, RootModel, constr


class Root1(RootModel[constr(pattern=r'^a', min_length=2)]):
    model_config = ConfigDict(
        frozen=True,
    )
    root: constr(pattern=r'^a', min_length=2)


class Root(RootModel[dict[Root1, int]]):
    model_config = ConfigDict(
        frozen=True,
    )
    root: dict[Root1, int] = Field(..., title='Root')
