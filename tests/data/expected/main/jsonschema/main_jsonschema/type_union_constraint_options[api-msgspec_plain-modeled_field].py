# Type-specific union constraints

from __future__ import annotations

from msgspec import UNSET, Struct, UnsetType


class Value(Struct):
    x: int | UnsetType = UNSET


class Root(Struct):
    value: Value | str | None
    before: bool | UnsetType = True
    after: int | UnsetType = 7
