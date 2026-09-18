from __future__ import annotations
from typing import List, Optional
from pydantic import Field, constr
from .base import Base


class Item(Base):
    secret: Optional[str] = None
    values: Optional[List[constr(min_length=1)]] = None
    ship_to: str = Field(..., alias='ship-to')