# Compound property names

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, RootModel


class Root1(Enum):
    a = 'a'


class Root(RootModel[dict[Root1 | Any, int]]):
    root: dict[Root1 | Any, int] = Field(..., title='Root')
