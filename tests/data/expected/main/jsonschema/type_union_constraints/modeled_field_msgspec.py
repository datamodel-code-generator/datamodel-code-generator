# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, TypeAlias

from msgspec import UNSET, Meta, Struct, UnsetType


class Value(Struct):
    x: int | UnsetType = UNSET


ValueString: TypeAlias = Annotated[str, Meta(min_length=2)]


class Root(Struct):
    value: Value | ValueString | None
    before: bool | UnsetType = True
    after: int | UnsetType = 7
