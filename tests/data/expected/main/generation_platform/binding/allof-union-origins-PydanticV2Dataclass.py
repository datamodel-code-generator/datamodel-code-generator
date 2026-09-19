from __future__ import annotations
from typing import Optional
from pydantic import Field
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class Envelope1:
    ship_to: Optional[str] = Field(None, alias='ship-to')
    other_key: Optional[int] = Field(None, alias='other-key')



@dataclass
class Envelope2:
    other_key: Optional[int] = Field(None, alias='other-key')
    ship_to: Optional[str] = Field(None, alias='ship-to')



@dataclass
class Variants1:
    ship_to: Optional[str] = Field(None, alias='ship-to')
    other_key: Optional[int] = Field(None, alias='other-key')



Variants = TypeAliasType("Variants", Variants1)



@dataclass
class Envelope:
    nested: Optional[Variants] = None