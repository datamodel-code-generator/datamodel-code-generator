from __future__ import annotations
from typing import Optional
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType
from pydantic import Field



@dataclass
class Base:
    optional: Optional[str] = None



NullableName = TypeAliasType("NullableName", Optional[str])



@dataclass
class Edges(Base):
    needed: int = Field(..., kw_only=True)
    strict: Optional[str] = None
    wire_name: Optional[int] = Field(default=1, alias='wire-name')
    alias_ref: Optional[NullableName] = None