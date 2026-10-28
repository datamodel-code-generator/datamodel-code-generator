# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, constr


class Root(BaseModel):
    before: bool | None = True
    value: constr(min_length=2) | int
    after: int | None = 7
