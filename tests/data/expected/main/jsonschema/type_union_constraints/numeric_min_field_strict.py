# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, Field, StrictInt


class Root(BaseModel):
    before: bool | None = True
    value: StrictInt | float = Field(..., ge=2.0)
    after: StrictInt | None = 7
