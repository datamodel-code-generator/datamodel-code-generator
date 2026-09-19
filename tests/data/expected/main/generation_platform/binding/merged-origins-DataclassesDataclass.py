from __future__ import annotations
from typing import Optional, TypeAlias
from dataclasses import dataclass



@dataclass
class SideKey:
    nested_key: Optional[str] = None



@dataclass
class RefUse1:
    shared_key: Optional[str] = None
    side_key: Optional[SideKey] = None



RefUse: TypeAlias = RefUse1



@dataclass
class Combined:
    shared_key: Optional[str] = None



@dataclass
class Base:
    shared_key: Optional[str] = None



@dataclass
class Other:
    shared_key: Optional[str] = None