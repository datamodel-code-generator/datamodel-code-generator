from __future__ import annotations
from typing_extensions import NotRequired
from typing import List, TypeAlias, TypedDict, Union



class Detail(TypedDict):
    code: NotRequired[str]
    count: NotRequired[int]



class Payload(TypedDict):
    detail: NotRequired[Detail]
    label: NotRequired[str]



Pair: TypeAlias = List[Union[str, int]]



class Node(TypedDict):
    detail: NotRequired[Detail]
    label: NotRequired[str]



class Parent(TypedDict):
    payload: NotRequired[Node]
    position: NotRequired[Pair]



class Child(Parent):
    payload: NotRequired[Payload]
    position: NotRequired[List[Union[str, int]]]