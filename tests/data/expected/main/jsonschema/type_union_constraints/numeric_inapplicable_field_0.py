# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel


class Root(BaseModel):
    before: bool | None = True
    value: int | float
    after: int | None = 7
