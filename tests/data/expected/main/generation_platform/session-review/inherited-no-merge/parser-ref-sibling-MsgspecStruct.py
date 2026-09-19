from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import TypeAlias, Union



class Value(Struct):
    code: Union[str, UnsetType] = UNSET



class Value1(Struct):
    code: Union[int, UnsetType] = UNSET



class Payload(Struct):
    value: Union[Value, Value1, UnsetType] = UNSET



class Value2(Struct):
    code: Union[str, UnsetType] = UNSET



class Value3(Struct):
    code: Union[int, UnsetType] = UNSET



class Payload1(Struct):
    value: Union[Value2, Value3, UnsetType] = UNSET



class Parent(Struct):
    payload: Union[Payload1, UnsetType] = UNSET



class Choice1(Struct):
    code: Union[str, UnsetType] = UNSET



Choice: TypeAlias = Choice1



class Child(Parent):
    payload: Union[Payload, UnsetType] = UNSET