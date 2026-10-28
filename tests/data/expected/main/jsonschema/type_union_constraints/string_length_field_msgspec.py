# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, TypeAlias

from msgspec import Meta, Struct, UnsetType

ValueString: TypeAlias = Annotated[str, Meta(min_length=2)]


class Root(Struct):
    value: ValueString | int
    before: bool | UnsetType = True
    after: int | UnsetType = 7
