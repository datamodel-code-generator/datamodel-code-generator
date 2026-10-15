# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel


class Root1(RootModel[Any]):
    root: Any


class Root(RootModel[dict[Any | Root1, Any]]):
    root: dict[Any | Root1, Any] = Field(..., title='Root')
