# Type-specific union constraints

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class Root(BaseModel):
    before: bool | None = True
    value: Annotated[str, Field(min_length=2)] | int
    after: int | None = 7
