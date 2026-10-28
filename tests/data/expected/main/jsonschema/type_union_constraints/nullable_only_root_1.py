# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel


class Root(RootModel[str | None]):
    root: str | None = Field(..., min_length=2, title='Root')
