from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class Bar(BaseModel):
    count: Optional[int] = None