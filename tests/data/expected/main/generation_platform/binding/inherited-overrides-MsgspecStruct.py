from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import Union



class BaseRequest(Struct):
    value: Union[str, UnsetType] = UNSET
    note: Union[str, UnsetType] = UNSET



class Base(Struct):
    value: Union[str, UnsetType] = UNSET
    id: Union[int, UnsetType] = UNSET
    note: Union[str, UnsetType] = UNSET



class ChildRequest(Struct):
    value: Union[int, UnsetType] = UNSET
    note: Union[str, UnsetType] = UNSET



class Child(Base):
    value: Union[int, UnsetType] = UNSET
    note: Union[str, UnsetType] = UNSET