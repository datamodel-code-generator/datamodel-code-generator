from __future__ import annotations
from pydantic import BaseModel, RootModel, constr
from typing import Optional, Union


class Value(BaseModel):
    code: Optional[constr(min_length=1)] = None


class Value1(BaseModel):
    code: Optional[int] = None


class Payload(BaseModel):
    value: Optional[Union[Value, Value1]] = None


class Value2(BaseModel):
    code: Optional[str] = None


class Value3(BaseModel):
    code: Optional[int] = None


class Payload1(BaseModel):
    value: Optional[Union[Value2, Value3]] = None


class Parent(BaseModel):
    payload: Optional[Payload1] = None


class Choice1(BaseModel):
    code: Optional[str] = None


class Choice(RootModel[Choice1]):
    root: Choice1


class Child(Parent):
    payload: Optional[Payload] = None


