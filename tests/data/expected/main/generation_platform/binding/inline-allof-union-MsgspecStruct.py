from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import TypeAlias, Union



class Nested1(Struct):
    label: Union[str, UnsetType] = UNSET



class Nested2(Struct):
    pass



class Nested3(Nested1, Nested2):
    pass



Nested: TypeAlias = Nested3



class Holder(Struct):
    nested: Union[Nested, UnsetType] = UNSET