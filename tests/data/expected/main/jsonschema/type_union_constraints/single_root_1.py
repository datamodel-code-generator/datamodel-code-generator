# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel


class Root(RootModel[int]):
    root: int = Field(..., ge=2, title='Root')
