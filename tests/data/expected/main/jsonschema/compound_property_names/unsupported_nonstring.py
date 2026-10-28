# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel


class Root(RootModel[dict[dict[str, Any] | Any, int]]):
    root: dict[dict[str, Any] | Any, int] = Field(..., title='Root')
