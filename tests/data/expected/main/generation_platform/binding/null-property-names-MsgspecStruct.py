from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import List, TypeAlias, Union



class Choice1(Struct):
    a: Union[str, UnsetType] = UNSET



class Choice2(Struct):
    b: Union[int, UnsetType] = UNSET



Choice: TypeAlias = Union[Choice1, Choice2]



class BaseArrayItem(Struct):
    a: Union[str, UnsetType] = UNSET



BaseArray: TypeAlias = List[BaseArrayItem]



Array: TypeAlias = BaseArray