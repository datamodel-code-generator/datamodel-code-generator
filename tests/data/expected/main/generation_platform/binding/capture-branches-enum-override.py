from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, RootModel, constr


class Parent(BaseModel):
    flag: Optional[Any] = None
    other: Optional[Any] = None
    code: Optional[str] = None


class Child(Parent):
    flag: Optional[Any] = None
    other: Optional[constr(min_length=1)] = None
    code: Optional[constr(min_length=2)] = None


class Node(BaseModel):
    next: Optional[Node] = None


class Holder(BaseModel):
    node: Optional[Node] = None


class A(BaseModel):
    x: Optional[str] = None


class B(BaseModel):
    x: Optional[int] = None


class C(BaseModel):
    x: Optional[int] = None
    y: Optional[str] = None


class Extra(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    __annotations__ = {
        '__pydantic_extra__': Dict[str, List[str]],
    }
    a: Optional[str] = 'y'


class Open(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    __annotations__ = {
        '__pydantic_extra__': Dict[str, Any],
    }
    a: Optional[str] = 'y'


class Code(RootModel[str]):
    root: str


class Limited(RootModel[constr(max_length=5)]):
    root: constr(max_length=5)


class Defaulted(BaseModel):
    a: Optional[str] = 'y'


Node.model_rebuild()