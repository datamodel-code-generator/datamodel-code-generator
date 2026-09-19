from __future__ import annotations
from pydantic import Field, constr
from typing import List, Optional, Union
from pydantic.dataclasses import dataclass



@dataclass
class PayloadItem:
    code: Optional[constr(min_length=1)] = None



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
    payload: Optional[List[Union[PayloadItem2, PayloadItem3]]] = Field(None, max_length=2)



@dataclass
class Child(Parent):
    payload: Optional[List[Union[PayloadItem, PayloadItem1]]] = Field(None, max_length=2)