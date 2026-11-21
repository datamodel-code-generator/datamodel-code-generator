# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, confloat, conint


class Root(BaseModel):
    before: bool | None = True
    value: confloat(ge=2.0, le=5.0) | conint(ge=2, le=5) | None
    after: int | None = 7
