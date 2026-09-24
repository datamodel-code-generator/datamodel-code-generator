from __future__ import annotations
from pydantic import BaseModel, Field, RootModel, constr
from typing import List, Optional, Union


class Nested(BaseModel):
    same: Optional[constr(min_length=1, max_length=3)] = None
    common: Optional[int] = None
    specific: Optional[bool] = None


class Value(BaseModel):
    left: Optional[str] = None


class Value1(BaseModel):
    right: Optional[int] = None


class Payload1(BaseModel):
    nested: Optional[Nested] = None
    values: Optional[List[Union[Value, Value1]]] = Field(None, max_length=2)


class Nested1(BaseModel):
    same: Optional[constr(min_length=1)] = None
    common: Optional[int] = None


class Value2(BaseModel):
    left: Optional[str] = None


class Payload2(BaseModel):
    nested: Optional[Nested1] = None
    values: Optional[List[Value2]] = Field(None, max_length=1)
    tail: Optional[str] = None


class Payload(RootModel[Union[Payload1, Payload2]]):
    root: Union[Payload1, Payload2]


