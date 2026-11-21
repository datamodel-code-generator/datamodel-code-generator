# Type-specific union constraints

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, Field


class Root(BaseModel):
    before: Optional[bool] = True
    value: Union[int, float] = Field(..., ge=2.0)
    after: Optional[int] = 7
