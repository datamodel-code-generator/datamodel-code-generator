# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, conint, constr


class Root(BaseModel):
    before: bool | None = True
    value: conint(ge=2) | constr(max_length=2)
    after: int | None = 7
