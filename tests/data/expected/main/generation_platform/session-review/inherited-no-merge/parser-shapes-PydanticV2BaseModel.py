from __future__ import annotations
from pydantic import BaseModel, Field, RootModel, constr
from typing import List, Optional, Union


class Detail(BaseModel):
    code: Optional[constr(min_length=1)] = None
    count: Optional[int] = None


class Payload(BaseModel):
    detail: Optional[Detail] = None
    label: Optional[str] = None


class DetailModel(BaseModel):
    code: Optional[str] = None
    count: Optional[int] = None


class Pair(RootModel[List[Union[str, int]]]):
    root: List[Union[str, int]] = Field(..., max_length=2)


class Node(BaseModel):
    detail: Optional[DetailModel] = None
    label: Optional[str] = None


class Parent(BaseModel):
    payload: Optional[Node] = None
    position: Optional[Pair] = None


class Child(Parent):
    payload: Optional[Payload] = None
    position: Optional[List[Union[constr(min_length=1), int]]] = Field(None, max_length=2)


