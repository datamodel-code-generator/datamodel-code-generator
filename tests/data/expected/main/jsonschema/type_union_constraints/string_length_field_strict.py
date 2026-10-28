# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, StrictInt, constr


class Root(BaseModel):
    before: bool | None = True
    value: constr(min_length=2, strict=True) | StrictInt
    after: StrictInt | None = 7
