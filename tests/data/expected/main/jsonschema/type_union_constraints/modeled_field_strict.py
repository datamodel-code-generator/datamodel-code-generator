# Type-specific union constraints

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StrictInt, StrictStr
from typing_extensions import TypeAliasType


class Value(BaseModel):
    x: StrictInt | None = None


ValueString = TypeAliasType("ValueString", Annotated[StrictStr, Field(min_length=2)])


class Root(BaseModel):
    before: bool | None = True
    value: Value | ValueString | None
    after: StrictInt | None = 7
