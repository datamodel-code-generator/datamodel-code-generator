# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType

ValueObject = TypeAliasType(
    "ValueObject", Annotated[dict[str, Any], Field(max_length=1)]
)


class Root(BaseModel):
    before: bool | None = True
    value: ValueObject | list[Any]
    after: int | None = 7
