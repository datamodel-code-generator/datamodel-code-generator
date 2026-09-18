from __future__ import annotations
from typing import Optional, Union
from pydantic import BaseModel


class UnionModel(BaseModel):
    a: Optional[int] = None


class Foo(BaseModel):
    value: Optional[int] = None


class Uses(BaseModel):
    first: Optional[Foo] = None
    second: Optional[Foo] = None
    union: Optional[Union[UnionModel, str]] = None