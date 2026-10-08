# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, RootModel
from typing_extensions import TypeAliasType

RootObject = TypeAliasType(
    "RootObject", dict[Annotated[str, Field(pattern=r'^a')], Any]
)


class Root(RootModel[list[Any] | RootObject]):
    root: list[Any] | RootObject = Field(..., title='Root')
