# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, conint, constr


class Root(BaseModel):
    before: bool | None = True
    value: constr(pattern=r'^a') | conint(le=3) | None
    after: int | None = 7
