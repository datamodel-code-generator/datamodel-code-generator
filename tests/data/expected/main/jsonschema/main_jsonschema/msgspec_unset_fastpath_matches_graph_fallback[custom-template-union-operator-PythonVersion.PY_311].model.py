from __future__ import annotations

from enum import StrEnum
from typing import Dict, List

from msgspec import UNSET, Struct, UnsetType


class Status(StrEnum):
    ready = 'ready'
    waiting = 'waiting'


class Nested(Struct):
    # type_hint=int; annotated=; field=; data_type=int
    id: int


class UnsetFastPath(Struct):
    # type_hint=str; annotated=; field=; data_type=str
    required_value: str
    # type_hint=str | UnsetType; annotated=; field=UNSET; data_type=str
    plain: str | UnsetType = UNSET
    # type_hint=List[str] | UnsetType; annotated=; field=UNSET; data_type=List[str]
    items: List[str] | UnsetType = UNSET
    # type_hint=Dict[str, int] | UnsetType; annotated=; field=UNSET; data_type=Dict[str, int]
    mapping: Dict[str, int] | UnsetType = UNSET
    # type_hint=str | UnsetType; annotated=; field=UNSET; data_type=str
    constrained: str | UnsetType = UNSET
    # type_hint=str | None | UnsetType; annotated=; field=UNSET; data_type=str | None
    nullable: str | None | UnsetType = UNSET
    # type_hint=str | int | UnsetType; annotated=; field=UNSET; data_type=str | int
    combined: str | int | UnsetType = UNSET
    # type_hint=Nested | UnsetType; annotated=; field=UNSET; data_type=Nested
    nested: Nested | UnsetType = UNSET
    # type_hint=Status | UnsetType; annotated=; field=UNSET; data_type=Status
    status: Status | UnsetType = UNSET
