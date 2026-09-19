from __future__ import annotations
from typing import Optional
from pydantic import Field
from pydantic.dataclasses import dataclass



@dataclass
class A:
    root: Optional[str] = None
    shared_key: Optional[str] = Field('A', alias='shared-key')



@dataclass
class B(A):
    left: Optional[int] = None
    shared_key: Optional[str] = Field('B', alias='shared-key')



@dataclass
class C(A):
    right: Optional[bool] = None
    shared_key: Optional[str] = Field('C', alias='shared-key')



@dataclass
class Leaf(B, C):
    leaf_key: Optional[float] = Field(None, alias='leaf-key')