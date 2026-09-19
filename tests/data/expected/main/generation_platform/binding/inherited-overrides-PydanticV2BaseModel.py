from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, constr


class BaseRequest(BaseModel):
    value: Optional[str] = None
    note: Optional[constr(min_length=1)] = None


class Base(BaseModel):
    value: Optional[str] = None
    id: Optional[int] = None
    note: Optional[constr(min_length=1)] = None


class ChildRequest(BaseModel):
    value: Optional[int] = None
    note: Optional[constr(min_length=2)] = None


class Child(Base):
    value: Optional[int] = None
    note: Optional[constr(min_length=2)] = None


