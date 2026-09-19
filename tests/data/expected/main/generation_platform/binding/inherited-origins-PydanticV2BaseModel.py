from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, constr


class ShipTo(BaseModel):
    postal_code: Optional[str] = Field(None, alias='postal-code')


class ChildRequest(BaseModel):
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')
    note: Optional[constr(min_length=2)] = None


class ChildResponse(BaseModel):
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')
    note: Optional[constr(min_length=2)] = None
    id: Optional[int] = None


class RenamedParentRequest(BaseModel):
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')
    note: Optional[constr(min_length=1)] = None


class RenamedParentResponse(BaseModel):
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')
    note: Optional[constr(min_length=1)] = None
    id: Optional[int] = None


