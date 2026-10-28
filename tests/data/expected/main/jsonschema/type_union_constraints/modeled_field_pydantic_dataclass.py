# Type-specific union constraints

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType


@dataclass
class Value:
    x: int | None = None


ValueString = TypeAliasType("ValueString", Annotated[str, Field(min_length=2)])


@dataclass
class Root:
    value: Value | ValueString | None
    before: bool | None = True
    after: int | None = 7
