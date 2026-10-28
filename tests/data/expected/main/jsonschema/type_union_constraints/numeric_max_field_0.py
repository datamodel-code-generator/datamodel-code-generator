# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, confloat, conint


class Root(BaseModel):
    before: bool | None = True
    value: confloat(le=5.0) | conint(le=5)
    after: int | None = 7
