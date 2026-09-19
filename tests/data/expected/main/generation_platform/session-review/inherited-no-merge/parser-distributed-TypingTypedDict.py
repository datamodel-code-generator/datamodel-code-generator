from __future__ import annotations
from typing_extensions import NotRequired
from typing import List, TypeAlias, TypedDict, Union



class PayloadItem(TypedDict):
    item: NotRequired[int]



Payload: TypeAlias = List[PayloadItem]



class Payload1(TypedDict):
    code: NotRequired[str]



class Parent(TypedDict):
    payload: NotRequired[Union[List[PayloadItem], Payload1]]



class Child(Parent):
    payload: NotRequired[Union[Payload, Payload1]]