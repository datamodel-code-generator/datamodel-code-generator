# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType

ValueArray = TypeAliasType("ValueArray", Annotated[list[Any], Field(min_length=1)])


@dataclass
class Root:
    value: ValueArray | dict[str, Any]
    before: bool | None = True
    after: int | None = 7
