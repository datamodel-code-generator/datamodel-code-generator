from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TestObject(BaseModel):
    test_string: Optional[str] = None


class Test(BaseModel):
    TestObject_1: Optional[TestObject] = Field(None, alias='TestObject', title='TestObject')
