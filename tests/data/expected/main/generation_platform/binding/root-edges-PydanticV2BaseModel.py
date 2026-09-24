from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, RootModel, constr


class Obj(BaseModel):
    k: Optional[str] = None


class BaseItem(BaseModel):
    k: Optional[constr(max_length=5)] = None


class Base(RootModel[List[BaseItem]]):
    root: List[BaseItem]


class ValueItem(BaseModel):
    k: Optional[constr(max_length=5)] = None


class Value(RootModel[List[ValueItem]]):
    root: List[ValueItem]


