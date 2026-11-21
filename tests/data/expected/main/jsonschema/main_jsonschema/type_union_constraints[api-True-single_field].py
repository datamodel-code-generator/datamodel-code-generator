# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, Field


class Root(BaseModel):
    before: bool | None = True
    value: int = Field(..., ge=2)
    after: int | None = 7
