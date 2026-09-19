from __future__ import annotations
from collections.abc import Mapping, Sequence
from pydantic import BaseModel


class Container(BaseModel):
    values: frozenset[str]
    sequence: Sequence[int]
    mapping: Mapping[str, bool]


