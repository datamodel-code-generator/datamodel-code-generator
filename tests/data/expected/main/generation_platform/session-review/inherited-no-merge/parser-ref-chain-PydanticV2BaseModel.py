from __future__ import annotations
from pydantic import BaseModel, RootModel, constr
from typing import Optional, Union


class Payload(BaseModel):
    code: Optional[constr(min_length=1)] = None
    left: Optional[str] = None


class Payload1(BaseModel):
    code: Optional[int] = None
    right: Optional[bool] = None


class Alias1(BaseModel):
    code: Optional[str] = None
    left: Optional[str] = None


class Alias2(BaseModel):
    code: Optional[int] = None
    right: Optional[bool] = None


class Alias(RootModel[Union[Alias1, Alias2]]):
    root: Union[Alias1, Alias2]


class Choice1(BaseModel):
    code: Optional[str] = None
    left: Optional[str] = None


class Choice(RootModel[Choice1]):
    root: Choice1


class Parent(BaseModel):
    payload: Optional[Alias] = None


class Child(Parent):
    payload: Optional[Union[Payload, Payload1]] = None


