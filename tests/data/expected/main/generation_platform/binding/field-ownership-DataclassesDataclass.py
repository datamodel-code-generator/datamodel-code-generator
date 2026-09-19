from __future__ import annotations
from typing import Optional
from dataclasses import dataclass



@dataclass
class A:
    root: Optional[str] = None
    shared_key: Optional[str] = 'A'



@dataclass
class B(A):
    left: Optional[int] = None
    shared_key: Optional[str] = 'B'



@dataclass
class C(A):
    right: Optional[bool] = None
    shared_key: Optional[str] = 'C'



@dataclass
class Leaf(B, C):
    leaf_key: Optional[float] = None