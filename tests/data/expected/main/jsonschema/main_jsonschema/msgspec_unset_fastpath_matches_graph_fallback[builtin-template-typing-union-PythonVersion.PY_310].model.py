from __future__ import annotations

from enum import Enum
from typing import Dict, List, Union

from msgspec import UNSET, Struct, UnsetType


class Status(Enum):
    ready = 'ready'
    waiting = 'waiting'


class Nested(Struct):
    id: int


class UnsetFastPath(Struct):
    required_value: str
    plain: Union[str, UnsetType] = UNSET
    items: Union[List[str], UnsetType] = UNSET
    mapping: Union[Dict[str, int], UnsetType] = UNSET
    constrained: Union[str, UnsetType] = UNSET
    nullable: Union[str, None, UnsetType] = UNSET
    combined: Union[str, int, UnsetType] = UNSET
    nested: Union[Nested, UnsetType] = UNSET
    status: Union[Status, UnsetType] = UNSET
