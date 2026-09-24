from __future__ import annotations
from typing import Optional
from dataclasses import dataclass



@dataclass
class BaseRequest:
    raw_note: Optional[str] = 'base'
    secret: Optional[str] = None



@dataclass
class BaseResponse:
    raw_note: Optional[str] = 'base'
    id: Optional[int] = None



@dataclass
class Base:
    raw_note: Optional[str] = 'base'
    id: Optional[int] = None
    secret: Optional[str] = None



@dataclass
class ChildRequest:
    raw_note: Optional[str] = None
    secret: Optional[str] = None



@dataclass
class ChildResponse:
    raw_note: Optional[str] = None
    id: Optional[int] = None



@dataclass
class Child(Base):
    raw_note: Optional[str] = None



@dataclass
class GrandChildRequest:
    raw_note: Optional[str] = None
    secret: Optional[str] = None



@dataclass
class GrandChildResponse:
    raw_note: Optional[str] = None
    id: Optional[int] = None



@dataclass
class GrandChild(Child):
    raw_note: Optional[str] = None