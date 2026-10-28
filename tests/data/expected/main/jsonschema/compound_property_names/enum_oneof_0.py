# Compound property names

from __future__ import annotations

from enum import Enum

from pydantic import Field, RootModel


class Root1(Enum):
    a = 'a'


class Root2(Enum):
    b = 'b'


class Root(RootModel[dict[Root1 | Root2, int]]):
    root: dict[Root1 | Root2, int] = Field(..., title='Root')
