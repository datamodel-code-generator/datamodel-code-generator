from __future__ import annotations
from typing import List, Optional
from pydantic import Field, constr
from pydantic.dataclasses import dataclass



@dataclass
class BaseRequest:
    ship_to: Optional[str] = Field(None, alias='ship-to')



@dataclass
class Base:
    id: Optional[int] = None
    ship_to: Optional[str] = Field(None, alias='ship-to')



@dataclass
class ItemRequest:
    ship_to: str = Field(..., alias='ship-to')
    secret: Optional[str] = None
    values: Optional[List[constr(min_length=1)]] = None



@dataclass
class ItemResponse:
    id: Optional[int] = None
    ship_to: str = Field(..., alias='ship-to')
    values: Optional[List[constr(min_length=1)]] = None



@dataclass
class Item(Base):
    secret: Optional[str] = None
    values: Optional[List[constr(min_length=1)]] = None
    ship_to: str = Field(..., alias='ship-to', kw_only=True)