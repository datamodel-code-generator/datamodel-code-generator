# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel


class Root1(BaseModel):
    x: int | None = None


class Root(RootModel[Root1 | str]):
    root: Root1 | str = Field(..., title='Root')
