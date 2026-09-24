from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import TypeAlias, Union



class Payload(Struct):
    code: Union[str, UnsetType] = UNSET
    left: Union[str, UnsetType] = UNSET



class Payload1(Struct):
    code: Union[int, UnsetType] = UNSET
    right: Union[bool, UnsetType] = UNSET



class Alias1(Struct):
    code: Union[str, UnsetType] = UNSET
    left: Union[str, UnsetType] = UNSET



class Alias2(Struct):
    code: Union[int, UnsetType] = UNSET
    right: Union[bool, UnsetType] = UNSET



Alias: TypeAlias = Union[Alias1, Alias2]



class Choice1(Struct):
    code: Union[str, UnsetType] = UNSET
    left: Union[str, UnsetType] = UNSET



Choice: TypeAlias = Choice1



class Parent(Struct):
    payload: Union[Alias, UnsetType] = UNSET



class Child(Parent):
    payload: Union[Payload, Payload1, UnsetType] = UNSET