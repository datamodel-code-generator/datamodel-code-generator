from __future__ import annotations
from typing import Optional
from pydantic import Field, constr
from pydantic.dataclasses import dataclass



@dataclass
class ShipTo:
    postal_code: Optional[str] = Field(None, alias='postal-code')



@dataclass
class ChildRequest:
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')
    note: Optional[constr(min_length=2)] = None



@dataclass
class ChildResponse:
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')
    note: Optional[constr(min_length=2)] = None
    id: Optional[int] = None



@dataclass
class RenamedParentRequest:
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')
    note: Optional[constr(min_length=1)] = None



@dataclass
class RenamedParentResponse:
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')
    note: Optional[constr(min_length=1)] = None
    id: Optional[int] = None