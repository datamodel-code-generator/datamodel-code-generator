from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import List, TypeAlias, Union



class Obj(Struct):
    k: Union[str, UnsetType] = UNSET



class BaseItem(Struct):
    k: Union[str, UnsetType] = UNSET



Base: TypeAlias = List[BaseItem]



Value: TypeAlias = Base