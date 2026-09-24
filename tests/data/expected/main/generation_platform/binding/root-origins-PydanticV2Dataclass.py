from __future__ import annotations
from typing import Dict, List, Optional
from pydantic import Field
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class ArrItem:
    ship_to: Optional[str] = Field(None, alias='ship-to')



Arr = TypeAliasType("Arr", List[ArrItem])



@dataclass
class Map:
    map_key: Optional[int] = Field(None, alias='map-key')



Map1 = TypeAliasType("Map1", Dict[str, Map])