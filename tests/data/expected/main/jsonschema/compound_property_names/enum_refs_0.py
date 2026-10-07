# Compound property names

from __future__ import annotations

from enum import Enum
from typing import Union

from pydantic import Field, RootModel


class Root(RootModel[dict[Union["A", "B"], int]]):
    root: dict[Union["A", "B"], int] = Field(..., title='Root')


class A(Enum):
    a = 'a'


class B(Enum):
    b = 'b'
