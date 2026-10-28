# Type-specific union constraints

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from typing_extensions import TypeAliasType

Root = TypeAliasType(
    "Root",
    Annotated[
        Annotated[int, Field(ge=2)] | Annotated[str, Field(max_length=2)],
        Field(..., title='Root'),
    ],
)
