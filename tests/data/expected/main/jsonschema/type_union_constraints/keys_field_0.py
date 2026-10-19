# Type-specific union constraints

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, constr


class Root(BaseModel):
    before: bool | None = True
    value: dict[constr(pattern=r'^a'), Any] | list[Any]
    after: int | None = 7
