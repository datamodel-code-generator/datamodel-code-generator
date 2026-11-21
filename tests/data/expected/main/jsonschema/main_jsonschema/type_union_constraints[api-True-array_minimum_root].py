# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, RootModel
from typing_extensions import TypeAliasType

RootArray = TypeAliasType("RootArray", Annotated[list[Any], Field(min_length=1)])


class Root(RootModel[RootArray | dict[str, Any]]):
    root: RootArray | dict[str, Any] = Field(..., title='Root')
