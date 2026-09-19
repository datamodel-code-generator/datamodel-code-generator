from __future__ import annotations
from typing import FrozenSet, Mapping, Sequence
from pydantic import BaseModel


class Container(BaseModel):
    values: FrozenSet[str]
    sequence: Sequence[int]
    mapping: Mapping[str, bool]


