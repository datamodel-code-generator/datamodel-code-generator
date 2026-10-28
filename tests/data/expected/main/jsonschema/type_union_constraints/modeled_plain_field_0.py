# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel


class Value(BaseModel):
    x: int | None = None


class Root(BaseModel):
    before: bool | None = True
    value: Value | str
    after: int | None = 7
