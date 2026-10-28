# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel


class Root1(RootModel[Any]):
    root: Any


class Root(RootModel[dict[Root1, int]]):
    root: dict[Root1, int] = Field(..., title='Root')
