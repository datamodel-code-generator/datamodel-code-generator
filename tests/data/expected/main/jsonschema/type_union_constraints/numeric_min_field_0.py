# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, confloat, conint


class Root(BaseModel):
    before: bool | None = True
    value: conint(ge=2) | confloat(ge=2.0)
    after: int | None = 7
