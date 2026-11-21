from __future__ import annotations

from enum import Enum
from typing import Dict, List

from msgspec import UNSET, Struct, UnsetType


class Status(Enum):
    ready = 'ready'
    waiting = 'waiting'


class Nested(Struct):
    id: int


class UnsetFastPath(Struct):
    required_value: str
    plain: str | UnsetType = UNSET
    items: List[str] | UnsetType = UNSET
    mapping: Dict[str, int] | UnsetType = UNSET
    constrained: str | UnsetType = UNSET
    nullable: str | None | UnsetType = UNSET
    combined: str | int | UnsetType = UNSET
    nested: Nested | UnsetType = UNSET
    status: Status | UnsetType = UNSET
