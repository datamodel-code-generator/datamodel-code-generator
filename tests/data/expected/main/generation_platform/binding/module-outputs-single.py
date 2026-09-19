from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, constr


class BaseRequest(BaseModel):
    ship_to: Optional[str] = Field(None, alias='ship-to')


class Base(BaseModel):
    id: Optional[int] = None
    ship_to: Optional[str] = Field(None, alias='ship-to')


class ItemRequest(BaseModel):
    ship_to: str = Field(..., alias='ship-to')
    secret: Optional[str] = None
    values: Optional[List[constr(min_length=1)]] = None


class ItemResponse(BaseModel):
    id: Optional[int] = None
    ship_to: str = Field(..., alias='ship-to')
    values: Optional[List[constr(min_length=1)]] = None


class Item(Base):
    secret: Optional[str] = None
    values: Optional[List[constr(min_length=1)]] = None
    ship_to: str = Field(..., alias='ship-to')