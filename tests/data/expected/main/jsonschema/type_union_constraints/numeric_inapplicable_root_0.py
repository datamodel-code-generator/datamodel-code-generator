# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel


class Root(RootModel[int | float]):
    root: int | float = Field(..., title='Root')
