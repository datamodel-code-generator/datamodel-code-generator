# Type-specific union constraints

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType


class Value(BaseModel):
    x: int | None = None


ValueString = TypeAliasType("ValueString", Annotated[str, Field(min_length=2)])


class Root(BaseModel):
    before: bool | None = True
    value: Value | ValueString | None
    after: int | None = 7
