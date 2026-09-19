from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class Foo(BaseModel):
    value: Optional[str] = None