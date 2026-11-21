# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, RootModel
from typing_extensions import TypeAliasType

RootArray = TypeAliasType("RootArray", Annotated[list[int], Field(min_length=1)])


class Root(RootModel[dict[str, Any] | RootArray]):
    root: dict[str, Any] | RootArray = Field(..., title='Root')
