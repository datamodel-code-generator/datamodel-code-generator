# Type-specific union constraints

from __future__ import annotations

from pydantic import Field
from pydantic.dataclasses import dataclass


@dataclass
class Root:
    before: bool | None = True
    value: int | float = Field(..., ge=2.0)
    after: int | None = 7
