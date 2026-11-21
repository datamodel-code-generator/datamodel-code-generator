# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any, TypeAlias

from msgspec import Meta, Struct, UnsetType

ValueArray: TypeAlias = Annotated[list[Any], Meta(min_length=1)]


class Root(Struct):
    value: ValueArray | dict[str, Any]
    before: bool | UnsetType = True
    after: int | UnsetType = 7
