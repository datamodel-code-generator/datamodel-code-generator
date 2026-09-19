from __future__ import annotations
from typing import Optional
from pydantic import constr
from pydantic.dataclasses import dataclass



@dataclass
class BaseRequest:
    value: Optional[str] = None
    note: Optional[constr(min_length=1)] = None



@dataclass
class Base:
    value: Optional[str] = None
    id: Optional[int] = None
    note: Optional[constr(min_length=1)] = None



@dataclass
class ChildRequest:
    value: Optional[int] = None
    note: Optional[constr(min_length=2)] = None



@dataclass
class Child(Base):
    value: Optional[int] = None
    note: Optional[constr(min_length=2)] = None