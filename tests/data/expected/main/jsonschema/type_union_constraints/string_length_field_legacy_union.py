# Type-specific union constraints

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, constr


class Root(BaseModel):
    before: Optional[bool] = True
    value: Union[constr(min_length=2), int]
    after: Optional[int] = 7
