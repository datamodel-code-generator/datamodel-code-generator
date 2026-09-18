from __future__ import annotations
from pydantic import BaseModel, Field, constr
from typing import List, Optional


class ItemRequest(BaseModel):
    ship_to: str = Field(..., alias='ship-to')
    secret: Optional[str] = None
    values: Optional[List[constr(min_length=1)]] = None