from __future__ import annotations
from typing import List, Optional
from dataclasses import dataclass, field



@dataclass
class BaseRequest:
    ship_to: Optional[str] = None



@dataclass
class Base:
    id: Optional[int] = None
    ship_to: Optional[str] = None



@dataclass
class ItemRequest:
    ship_to: str
    secret: Optional[str] = None
    values: Optional[List[str]] = None



@dataclass
class ItemResponse:
    ship_to: str
    id: Optional[int] = None
    values: Optional[List[str]] = None



@dataclass
class Item(Base):
    ship_to: str = field(kw_only=True)
    secret: Optional[str] = None
    values: Optional[List[str]] = None