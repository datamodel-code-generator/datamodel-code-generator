# Type-specific union constraints

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel


class Root(BaseModel):
    before: Optional[bool] = True
    value: Union[str, int]
    after: Optional[int] = 7
