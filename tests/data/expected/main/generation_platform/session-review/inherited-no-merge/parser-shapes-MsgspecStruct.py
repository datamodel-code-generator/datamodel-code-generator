from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import List, TypeAlias, Union



class Detail(Struct):
    code: Union[str, UnsetType] = UNSET
    count: Union[int, UnsetType] = UNSET



class Payload(Struct):
    detail: Union[Detail, UnsetType] = UNSET
    label: Union[str, UnsetType] = UNSET



Pair: TypeAlias = List[Union[str, int]]



class Node(Struct):
    detail: Union[Detail, UnsetType] = UNSET
    label: Union[str, UnsetType] = UNSET



class Parent(Struct):
    payload: Union[Node, UnsetType] = UNSET
    position: Union[Pair, UnsetType] = UNSET



class Child(Parent):
    payload: Union[Payload, UnsetType] = UNSET
    position: Union[List[Union[str, int]], UnsetType] = UNSET