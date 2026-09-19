from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import List, Union



class BaseRequest(Struct):
    ship_to: Union[str, UnsetType] = field(name='ship-to', default=UNSET)



class Base(Struct):
    id: Union[int, UnsetType] = UNSET
    ship_to: Union[str, UnsetType] = field(name='ship-to', default=UNSET)



class ItemRequest(Struct):
    ship_to: str = field(name='ship-to')
    secret: Union[str, UnsetType] = UNSET
    values: Union[List[str], UnsetType] = UNSET



class ItemResponse(Struct):
    ship_to: str = field(name='ship-to')
    id: Union[int, UnsetType] = UNSET
    values: Union[List[str], UnsetType] = UNSET



class Item(Base, kw_only=True):
    ship_to: str = field(name='ship-to')
    secret: Union[str, UnsetType] = UNSET
    values: Union[List[str], UnsetType] = UNSET