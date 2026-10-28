# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType

ValueArray = TypeAliasType("ValueArray", Annotated[list[int], Field(min_length=1)])


class Root(BaseModel):
    before: bool | None = True
    value: dict[str, Any] | ValueArray
    after: int | None = 7
