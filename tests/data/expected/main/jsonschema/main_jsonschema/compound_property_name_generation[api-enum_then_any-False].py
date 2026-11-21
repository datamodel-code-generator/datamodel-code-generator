# Compound property names

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, RootModel


class Root1(Enum):
    a = 'a'
    b = 'b'


class Root(RootModel[dict[Root1 | Any, Any]]):
    root: dict[Root1 | Any, Any] = Field(..., title='Root')
