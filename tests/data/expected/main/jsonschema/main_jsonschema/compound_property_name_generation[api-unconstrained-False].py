# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel


class Root(RootModel[dict[Any, int]]):
    root: dict[Any, int] = Field(..., title='Root')
