from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import Union



class ShipTo(Struct):
    postal_code: Union[str, UnsetType] = field(name='postal-code', default=UNSET)



class ChildRequest(Struct):
    ship_to: Union[ShipTo, UnsetType] = field(name='ship-to', default=UNSET)
    note: Union[str, UnsetType] = UNSET



class ChildResponse(Struct):
    ship_to: Union[ShipTo, UnsetType] = field(name='ship-to', default=UNSET)
    note: Union[str, UnsetType] = UNSET
    id: Union[int, UnsetType] = UNSET



class RenamedParentRequest(Struct):
    ship_to: Union[ShipTo, UnsetType] = field(name='ship-to', default=UNSET)
    note: Union[str, UnsetType] = UNSET



class RenamedParentResponse(Struct):
    ship_to: Union[ShipTo, UnsetType] = field(name='ship-to', default=UNSET)
    note: Union[str, UnsetType] = UNSET
    id: Union[int, UnsetType] = UNSET