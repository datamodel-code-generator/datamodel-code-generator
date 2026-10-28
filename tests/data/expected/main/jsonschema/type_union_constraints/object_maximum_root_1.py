# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, RootModel
from typing_extensions import TypeAliasType

RootObject = TypeAliasType("RootObject", Annotated[dict[str, Any], Field(max_length=1)])


class Root(RootModel[RootObject | list[Any]]):
    root: RootObject | list[Any] = Field(..., title='Root')
