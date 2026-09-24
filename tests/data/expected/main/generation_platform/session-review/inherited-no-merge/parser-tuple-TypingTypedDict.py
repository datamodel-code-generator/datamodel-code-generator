from __future__ import annotations
from typing_extensions import NotRequired
from typing import List, TypedDict, Union



class PayloadItem(TypedDict):
    code: NotRequired[str]



class PayloadItem1(TypedDict):
    code: NotRequired[int]



class PayloadItem2(TypedDict):
    code: NotRequired[str]



class PayloadItem3(TypedDict):
    code: NotRequired[int]



class Parent(TypedDict):
    payload: NotRequired[List[Union[PayloadItem2, PayloadItem3]]]



class Child(Parent):
    payload: NotRequired[List[Union[PayloadItem, PayloadItem1]]]