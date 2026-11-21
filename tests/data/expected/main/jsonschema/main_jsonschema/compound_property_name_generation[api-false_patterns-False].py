# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel, constr


class Root(RootModel[dict[constr(pattern=r'^a') | constr(pattern=r'^b'), Any]]):
    root: dict[constr(pattern=r'^a') | constr(pattern=r'^b'), Any] = Field(
        ..., title='Root'
    )
