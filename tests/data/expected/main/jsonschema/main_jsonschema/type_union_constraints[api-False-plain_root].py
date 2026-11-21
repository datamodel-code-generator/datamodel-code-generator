# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel


class Root(RootModel[str | int]):
    root: str | int = Field(..., title='Root')
