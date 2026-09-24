from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class A(BaseModel):
    root: Optional[str] = None
    shared_key: Optional[str] = Field('A', alias='shared-key')


class B(A):
    left: Optional[int] = None
    shared_key: Optional[str] = Field('B', alias='shared-key')


class C(A):
    right: Optional[bool] = None
    shared_key: Optional[str] = Field('C', alias='shared-key')


class Leaf(B, C):
    leaf_key: Optional[float] = Field(None, alias='leaf-key')


