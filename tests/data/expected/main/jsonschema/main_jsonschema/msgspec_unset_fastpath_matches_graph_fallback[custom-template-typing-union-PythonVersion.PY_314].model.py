from enum import StrEnum
from typing import Dict, List, Union

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
    # type_hint=Union[str, UnsetType]; annotated=; field=UNSET; data_type=str
    plain: Union[str, UnsetType] = UNSET
    # type_hint=Union[List[str], UnsetType]; annotated=; field=UNSET; data_type=List[str]
    items: Union[List[str], UnsetType] = UNSET
    # type_hint=Union[Dict[str, int], UnsetType]; annotated=; field=UNSET; data_type=Dict[str, int]
    mapping: Union[Dict[str, int], UnsetType] = UNSET
    # type_hint=Union[str, UnsetType]; annotated=; field=UNSET; data_type=str
    constrained: Union[str, UnsetType] = UNSET
    # type_hint=Union[str, None, UnsetType]; annotated=; field=UNSET; data_type=Optional[str]
    nullable: Union[str, None, UnsetType] = UNSET
    # type_hint=Union[str, int, UnsetType]; annotated=; field=UNSET; data_type=Union[str, int]
    combined: Union[str, int, UnsetType] = UNSET
    # type_hint=Union[Nested, UnsetType]; annotated=; field=UNSET; data_type=Nested
    nested: Union[Nested, UnsetType] = UNSET
    # type_hint=Union[Status, UnsetType]; annotated=; field=UNSET; data_type=Status
    status: Union[Status, UnsetType] = UNSET
