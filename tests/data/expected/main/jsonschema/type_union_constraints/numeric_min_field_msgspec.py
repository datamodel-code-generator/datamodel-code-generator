# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, TypeAlias

from msgspec import Meta, Struct, UnsetType

ValueInteger: TypeAlias = Annotated[int, Meta(ge=2)]


ValueNumber: TypeAlias = Annotated[float, Meta(ge=2.0)]


class Root(Struct):
    value: ValueInteger | ValueNumber
    before: bool | UnsetType = True
    after: int | UnsetType = 7
