# Type-specific union constraints

from __future__ import annotations

from pydantic import Field
from pydantic.dataclasses import dataclass


@dataclass
class Root:
    value: int | float = Field(..., ge=2.0)
    before: bool | None = True
    after: int | None = 7
