# Type-specific union constraints

from __future__ import annotations

from msgspec import Struct, UnsetType


class Root(Struct):
    value: str | int
    before: bool | UnsetType = True
    after: int | UnsetType = 7
