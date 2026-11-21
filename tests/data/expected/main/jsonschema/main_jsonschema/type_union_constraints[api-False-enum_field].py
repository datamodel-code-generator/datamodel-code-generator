# Type-specific union constraints

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Value(Enum):
    int_1 = 1
    a = 'a'


class Root(BaseModel):
    value: Value
