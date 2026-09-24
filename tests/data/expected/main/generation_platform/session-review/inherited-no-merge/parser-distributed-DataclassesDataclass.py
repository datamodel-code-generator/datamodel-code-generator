from __future__ import annotations
from typing import List, Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class PayloadItem:
    item: Optional[int] = None



Payload: TypeAlias = List[PayloadItem]



@dataclass
class Payload1:
    code: Optional[str] = None



@dataclass
class Parent:
    payload: Optional[Union[List[PayloadItem], Payload1]] = None



@dataclass
class Child(Parent):
    payload: Optional[Union[Payload, Payload1]] = None