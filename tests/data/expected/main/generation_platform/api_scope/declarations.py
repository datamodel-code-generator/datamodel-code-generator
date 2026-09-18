from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, RootModel
from enum import Enum


class Foo(BaseModel):
    id: Optional[int] = None


class Unused(Enum):
    available = 'available'
    missing = 'missing'


class PageParameter(RootModel[int]):
    root: int


class TraceHeader(RootModel[str]):
    root: str


class ListPetsQueryLimitParameter(RootModel[int]):
    root: int