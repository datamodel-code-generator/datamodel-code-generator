from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import TypeAlias, Union



class Envelope1(Struct):
    ship_to: Union[str, UnsetType] = field(name='ship-to', default=UNSET)
    other_key: Union[int, UnsetType] = field(name='other-key', default=UNSET)



class Envelope2(Struct):
    other_key: Union[int, UnsetType] = field(name='other-key', default=UNSET)
    ship_to: Union[str, UnsetType] = field(name='ship-to', default=UNSET)



class Variants1(Struct):
    ship_to: Union[str, UnsetType] = field(name='ship-to', default=UNSET)
    other_key: Union[int, UnsetType] = field(name='other-key', default=UNSET)



Variants: TypeAlias = Variants1



class Envelope(Struct):
    nested: Union[Variants, UnsetType] = UNSET