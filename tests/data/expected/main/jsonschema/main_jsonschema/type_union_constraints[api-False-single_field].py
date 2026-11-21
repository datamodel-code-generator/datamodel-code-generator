# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, conint


class Root(BaseModel):
    before: bool | None = True
    value: conint(ge=2)
    after: int | None = 7
