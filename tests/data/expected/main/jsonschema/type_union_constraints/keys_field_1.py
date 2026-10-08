# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType

ValueObject = TypeAliasType(
    "ValueObject", dict[Annotated[str, Field(pattern=r'^a')], Any]
)


class Root(BaseModel):
    before: bool | None = True
    value: list[Any] | ValueObject
    after: int | None = 7
