# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, StrictInt
from typing_extensions import TypeAliasType

ValueArray = TypeAliasType("ValueArray", Annotated[list[Any], Field(min_length=1)])


class Root(BaseModel):
    before: bool | None = True
    value: ValueArray | dict[str, Any]
    after: StrictInt | None = 7
