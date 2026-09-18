from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, constr


class ItemResponse(BaseModel):
    id: Optional[int] = None
    ship_to: str = Field(..., alias='ship-to')
    values: Optional[List[constr(min_length=1)]] = None