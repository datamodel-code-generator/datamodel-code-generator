# Compound property names

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from typing_extensions import TypeAliasType

Root1 = TypeAliasType("Root1", Annotated[str, Field(pattern=r'^a', min_length=2)])


Root = TypeAliasType("Root", Annotated[dict[Root1, int], Field(..., title='Root')])
