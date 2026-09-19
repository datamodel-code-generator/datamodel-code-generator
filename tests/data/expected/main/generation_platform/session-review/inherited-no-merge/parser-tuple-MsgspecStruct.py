from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import List, Union



class PayloadItem(Struct):
    code: Union[str, UnsetType] = UNSET



class PayloadItem1(Struct):
    code: Union[int, UnsetType] = UNSET



class PayloadItem2(Struct):
    code: Union[str, UnsetType] = UNSET



class PayloadItem3(Struct):
    code: Union[int, UnsetType] = UNSET



class Parent(Struct):
    payload: Union[List[Union[PayloadItem2, PayloadItem3]], UnsetType] = UNSET



class Child(Parent):
    payload: Union[List[Union[PayloadItem, PayloadItem1]], UnsetType] = UNSET