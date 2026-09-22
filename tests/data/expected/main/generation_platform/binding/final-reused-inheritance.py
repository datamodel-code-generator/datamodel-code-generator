from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class Base(BaseModel):
    value: Optional[str] = None


class ForwardBase(Base):
    pass


class Holder(BaseModel):
    base: Optional[ForwardBase] = None


class ForwardDerived(ForwardBase):
    value: str


