from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import List, TypeAlias, Union



class Nested(Struct):
    same: Union[str, UnsetType] = UNSET
    common: Union[int, UnsetType] = UNSET
    specific: Union[bool, UnsetType] = UNSET



class Value(Struct):
    left: Union[str, UnsetType] = UNSET



class Value1(Struct):
    right: Union[int, UnsetType] = UNSET



class Payload1(Struct):
    nested: Union[Nested, UnsetType] = UNSET
    values: Union[List[Union[Value, Value1]], UnsetType] = UNSET



class Nested1(Struct):
    same: Union[str, UnsetType] = UNSET
    common: Union[int, UnsetType] = UNSET



class Value2(Struct):
    left: Union[str, UnsetType] = UNSET



class Payload2(Struct):
    nested: Union[Nested1, UnsetType] = UNSET
    values: Union[List[Value2], UnsetType] = UNSET
    tail: Union[str, UnsetType] = UNSET



Payload: TypeAlias = Union[Payload1, Payload2]