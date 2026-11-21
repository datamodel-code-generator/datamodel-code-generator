# Type-specific union constraints

from __future__ import annotations

from pydantic.dataclasses import dataclass


@dataclass
class Root:
    value: str | int
    before: bool | None = True
    after: int | None = 7
