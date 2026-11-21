# Type-specific union constraints

from __future__ import annotations

from pydantic import constr
from pydantic.dataclasses import dataclass


@dataclass
class Root:
    value: constr(min_length=2) | int
    before: bool | None = True
    after: int | None = 7
