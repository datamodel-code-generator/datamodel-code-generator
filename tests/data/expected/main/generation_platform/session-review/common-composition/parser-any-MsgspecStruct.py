from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import TypeAlias, Union



class Value(Struct):
    left: Union[str, UnsetType] = UNSET



class Value1(Struct):
    right: Union[int, UnsetType] = UNSET



class Payload1(Struct):
    value: Union[Value, Value1, UnsetType] = UNSET



Payload: TypeAlias = Payload1