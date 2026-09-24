from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import List, TypeAlias, Union



class PayloadItem(Struct):
    item: Union[int, UnsetType] = UNSET



Payload: TypeAlias = List[PayloadItem]



class Payload1(Struct):
    code: Union[str, UnsetType] = UNSET



class Parent(Struct):
    payload: Union[List[PayloadItem], Payload1, UnsetType] = UNSET



class Child(Parent):
    payload: Union[Payload, Payload1, UnsetType] = UNSET