from __future__ import annotations
from typing import Optional, TypeAlias
from dataclasses import dataclass



@dataclass
class Envelope1:
    ship_to: Optional[str] = None
    other_key: Optional[int] = None



@dataclass
class Envelope2:
    other_key: Optional[int] = None
    ship_to: Optional[str] = None



@dataclass
class Variants1:
    ship_to: Optional[str] = None
    other_key: Optional[int] = None



Variants: TypeAlias = Variants1



@dataclass
class Envelope:
    nested: Optional[Variants] = None