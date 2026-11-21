# Compound property names

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, RootModel


class Root1(Enum):
    a = 'a'


class Root2(Enum):
    b = 'b'


class Root(RootModel[dict[Root1 | Root2, Any]]):
    root: dict[Root1 | Root2, Any] = Field(..., title='Root')
