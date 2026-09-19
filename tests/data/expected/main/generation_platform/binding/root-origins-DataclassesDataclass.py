from __future__ import annotations
from typing import Dict, List, Optional, TypeAlias
from dataclasses import dataclass



@dataclass
class ArrItem:
    ship_to: Optional[str] = None



Arr: TypeAlias = List[ArrItem]



@dataclass
class Map:
    map_key: Optional[int] = None



Map1: TypeAlias = Dict[str, Map]