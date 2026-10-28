# Type-specific union constraints

from __future__ import annotations

from typing import Annotated, Optional, Union

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType


class Value(BaseModel):
    x: Optional[int] = None


ValueString = TypeAliasType("ValueString", Annotated[str, Field(min_length=2)])


class Root(BaseModel):
    before: Optional[bool] = True
    value: Optional[Union[Optional[Value], ValueString]]
    after: Optional[int] = 7
