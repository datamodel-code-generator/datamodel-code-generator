from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import Any, TypeAlias, Union



class ShipTo(Struct):
    postal_code: Union[str, UnsetType] = field(name='postal-code', default=UNSET)



class Choice1(Struct):
    common_key: Union[str, UnsetType] = field(name='common-key', default=UNSET)
    ship_to: Union[ShipTo, UnsetType] = field(name='ship-to', default=UNSET)



class Choice2(Struct):
    common_key: Union[str, UnsetType] = field(name='common-key', default=UNSET)



Never: TypeAlias = Any



class Named(Struct):
    external_name: Union[str, UnsetType] = field(name='external-name', default=UNSET)



Choice: TypeAlias = Union[Choice1, Choice2, Named]