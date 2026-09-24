from __future__ import annotations
from pydantic import BaseModel, Field, constr
from typing import List, Optional, Union


class PayloadItem(BaseModel):
    code: Optional[constr(min_length=1)] = None


class PayloadItem1(BaseModel):
    code: Optional[int] = None


class PayloadItem2(BaseModel):
    code: Optional[str] = None


class PayloadItem3(BaseModel):
    code: Optional[int] = None


class Parent(BaseModel):
    payload: Optional[List[Union[PayloadItem2, PayloadItem3]]] = Field(None, max_length=2)


class Child(Parent):
    payload: Optional[List[Union[PayloadItem, PayloadItem1]]] = Field(None, max_length=2)


