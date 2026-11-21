# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel


class Root(RootModel[dict[Any, Any]]):
    root: dict[Any, Any] = Field(..., title='Root')
