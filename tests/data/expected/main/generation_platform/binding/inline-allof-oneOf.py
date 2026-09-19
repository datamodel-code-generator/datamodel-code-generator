from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, RootModel


class Value1(BaseModel):
    label: Optional[str] = None


class Value2(BaseModel):
    pass


class Value3(Value1, Value2):
    pass


class Value(RootModel[Value3]):
    root: Value3


class Record(BaseModel):
    value: Optional[Value] = None


