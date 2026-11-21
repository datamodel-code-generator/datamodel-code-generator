# Type-specific union constraints

from __future__ import annotations

from typing import Any

from msgspec import Struct, UnsetType


class Root(Struct):
    value: list[Any] | dict[str, Any]
    before: bool | UnsetType = True
    after: int | UnsetType = 7
