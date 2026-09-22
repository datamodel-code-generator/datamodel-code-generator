from __future__ import annotations
from typing import Optional, Union
from pydantic import BaseModel, RootModel


class Value(BaseModel):
    left: Optional[str] = None


class Value1(BaseModel):
    right: Optional[int] = None


class Payload1(BaseModel):
    value: Optional[Union[Value, Value1]] = None


class Payload(RootModel[Payload1]):
    root: Payload1


