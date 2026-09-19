from __future__ import annotations
from typing import List, Optional, Union
from dataclasses import dataclass



@dataclass
class PayloadItem:
    code: Optional[str] = None



@dataclass
class PayloadItem1:
    code: Optional[int] = None



@dataclass
class PayloadItem2:
    code: Optional[str] = None



@dataclass
class PayloadItem3:
    code: Optional[int] = None



@dataclass
class Parent:
    payload: Optional[List[Union[PayloadItem2, PayloadItem3]]] = None



@dataclass
class Child(Parent):
    payload: Optional[List[Union[PayloadItem, PayloadItem1]]] = None