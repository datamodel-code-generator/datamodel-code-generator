# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel


class Root(RootModel[dict[int | str | Any, int]]):
    root: dict[int | str | Any, int] = Field(..., title='Root')
