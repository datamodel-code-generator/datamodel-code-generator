# Type-specific union constraints

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Root(BaseModel):
    before: bool | None = True
    value: list[Any] | dict[str, Any]
    after: int | None = 7
