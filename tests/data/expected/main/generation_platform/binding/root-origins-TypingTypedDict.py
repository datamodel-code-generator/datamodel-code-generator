from __future__ import annotations
from typing_extensions import NotRequired
from typing import Dict, List, TypeAlias, TypedDict




ArrItem = TypedDict('ArrItem', {
    'ship-to': NotRequired[str],})




Arr: TypeAlias = List[ArrItem]




Map = TypedDict('Map', {
    'map-key': NotRequired[int],})




Map1: TypeAlias = Dict[str, Map]