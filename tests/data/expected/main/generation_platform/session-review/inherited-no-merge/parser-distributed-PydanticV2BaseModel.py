from __future__ import annotations
from typing import List, Optional, Union
from pydantic import BaseModel, Field, RootModel


class PayloadItem(BaseModel):
    item: Optional[int] = None


class Payload(RootModel[List[PayloadItem]]):
    root: List[PayloadItem] = Field(..., min_length=1)


class Payload1(BaseModel):
    code: Optional[str] = None


class Parent(BaseModel):
    payload: Optional[Union[List[PayloadItem], Payload1]] = None


class Child(Parent):
    payload: Optional[Union[Payload, Payload1]] = None


