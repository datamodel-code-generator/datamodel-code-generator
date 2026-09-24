from __future__ import annotations
from typing import Optional
from dataclasses import dataclass



@dataclass
class BaseRequest:
    value: Optional[str] = None
    note: Optional[str] = None



@dataclass
class Base:
    value: Optional[str] = None
    id: Optional[int] = None
    note: Optional[str] = None



@dataclass
class ChildRequest:
    value: Optional[int] = None
    note: Optional[str] = None



@dataclass
class Child(Base):
    value: Optional[int] = None
    note: Optional[str] = None