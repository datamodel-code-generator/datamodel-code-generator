from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional


class Record(BaseModel):
    value: Optional[str] = None


class Holder(BaseModel):
    text: Optional[str] = None
    list_: Optional[List[str]] = Field(None, alias='list')
    original: Optional[Record] = None
    copy_: Optional[Record] = Field(None, alias='copy')
    wrapper: Optional[Record] = None


