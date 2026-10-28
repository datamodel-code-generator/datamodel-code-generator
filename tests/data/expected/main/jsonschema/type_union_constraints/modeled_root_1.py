# Type-specific union constraints

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, RootModel
from typing_extensions import TypeAliasType


class Root1(BaseModel):
    x: int | None = None


RootString = TypeAliasType("RootString", Annotated[str, Field(min_length=2)])


class Root(RootModel[Root1 | RootString | None]):
    root: Root1 | RootString | None = Field(None, title='Root')
