from __future__ import annotations
from typing import Optional, Tuple, Union
from dataclasses import dataclass



@dataclass
class NullableRecord:
    value: Optional[str] = None



@dataclass
class Base:
    kind: Optional[str] = None



@dataclass
class Child(Base):
    extra: Optional[int] = None



@dataclass
class Holder:
    record: Optional[NullableRecord]
    name: Optional[str]
    maybe: Optional[str]
    pair: Tuple[int, str]
    base: Optional[Base] = None