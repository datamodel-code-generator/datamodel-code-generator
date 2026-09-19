from __future__ import annotations
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, RootModel


class ArrItem(BaseModel):
    ship_to: Optional[str] = Field(None, alias='ship-to')


class Arr(RootModel[List[ArrItem]]):
    root: List[ArrItem]


class Map(BaseModel):
    map_key: Optional[int] = Field(None, alias='map-key')


class Map1(RootModel[Dict[str, Map]]):
    root: Dict[str, Map]


