from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import Dict, List, TypeAlias, Union



class ArrItem(Struct):
    ship_to: Union[str, UnsetType] = field(name='ship-to', default=UNSET)



Arr: TypeAlias = List[ArrItem]



class Map(Struct):
    map_key: Union[int, UnsetType] = field(name='map-key', default=UNSET)



Map1: TypeAlias = Dict[str, Map]