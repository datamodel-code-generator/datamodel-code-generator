from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import TypeAlias, Union



class Base(Struct):
    pass



class Nested1(Struct):
    label: Union[str, UnsetType] = UNSET



class Nested2(Struct):
    pass



class Nested3(Nested1, Nested2):
    pass



Nested: TypeAlias = Nested3



class Mixed1(Struct):
    code: Union[int, UnsetType] = UNSET



class Mixed2(Mixed1, Base):
    pass



Mixed: TypeAlias = Mixed2



class Holder(Struct):
    nested: Union[Nested, UnsetType] = UNSET
    mixed: Union[Mixed, UnsetType] = UNSET