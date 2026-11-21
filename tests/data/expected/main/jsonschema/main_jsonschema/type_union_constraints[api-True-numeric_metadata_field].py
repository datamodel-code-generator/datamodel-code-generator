# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, Field


class Root(BaseModel):
    before: bool | None = True
    value: int | float = Field(..., ge=2.0)
    after: int | None = 7
