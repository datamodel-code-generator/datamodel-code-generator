from __future__ import annotations
from typing import Optional, TypeAlias
from dataclasses import dataclass, field



@dataclass
class Base:
    optional: Optional[str] = None



NullableName: TypeAlias = Optional[str]



@dataclass
class Edges(Base):
    needed: int = field(kw_only=True)
    strict: Optional[str] = None
    wire_name: Optional[int] = 1
    alias_ref: Optional[NullableName] = None