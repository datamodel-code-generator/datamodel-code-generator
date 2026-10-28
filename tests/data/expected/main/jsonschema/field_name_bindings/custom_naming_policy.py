from __future__ import annotations

from enum import Enum
from typing import List
from typing import Optional as Optional_aliased

from pydantic import BaseModel, Field


class Choice(Enum):
    list = 'list'
    Optional = 'Optional'
    Field = 'Field'


class Child(BaseModel):
    value: Optional_aliased[int] = None


class Record(BaseModel):
    list: str
    Field: str
    Optional: str
    list_aliased: Optional_aliased[str] = None
    list_aliased_1: Optional_aliased[str] = None
    items: Optional_aliased[List[int]] = None
    count: Optional_aliased[int] = Field(None, description='Field list')
    choice: Optional_aliased[Choice] = None
    child: Optional_aliased[Child] = None
