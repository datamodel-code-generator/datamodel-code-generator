from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class First(BaseModel):
    ship_to: Optional[str] = Field(None, alias='ship-to')


class Second(BaseModel):
    ship_to: Optional[str] = Field(None, alias='ship-to')