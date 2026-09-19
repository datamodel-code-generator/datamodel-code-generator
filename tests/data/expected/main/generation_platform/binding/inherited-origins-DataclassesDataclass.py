from __future__ import annotations
from typing import Optional
from dataclasses import dataclass



@dataclass
class ShipTo:
    postal_code: Optional[str] = None



@dataclass
class ChildRequest:
    ship_to: Optional[ShipTo] = None
    note: Optional[str] = None



@dataclass
class ChildResponse:
    ship_to: Optional[ShipTo] = None
    note: Optional[str] = None
    id: Optional[int] = None



@dataclass
class RenamedParentRequest:
    ship_to: Optional[ShipTo] = None
    note: Optional[str] = None



@dataclass
class RenamedParentResponse:
    ship_to: Optional[ShipTo] = None
    note: Optional[str] = None
    id: Optional[int] = None