from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class Base(BaseModel):
    id: Optional[int] = None
    ship_to: Optional[str] = Field(None, alias='ship-to')