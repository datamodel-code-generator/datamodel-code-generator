# Compound property names

from __future__ import annotations

from enum import Enum
from typing import Any, Union

from pydantic import Field, RootModel


class Root(RootModel[dict[Union["Name", Any], Any]]):
    root: dict[Union["Name", Any], Any] = Field(..., title='Root')


class Name(Enum):
    a = 'a'
    b = 'b'
