from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import RootModel


class Type1Enum(Enum):
    enumOne = 'enumOne'
    enumTwo = 'enumTwo'


class Type1(RootModel[List[Type1Enum]]):
    root: List[Type1Enum]


class Type2(Enum):
    enumFour = 'enumFour'
    enumFive = 'enumFive'
