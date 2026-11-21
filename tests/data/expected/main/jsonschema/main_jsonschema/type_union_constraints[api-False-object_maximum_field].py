# Type-specific union constraints

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Root(BaseModel):
    before: bool | None = True
    value: dict[str, Any] | list[Any]
    after: int | None = 7
