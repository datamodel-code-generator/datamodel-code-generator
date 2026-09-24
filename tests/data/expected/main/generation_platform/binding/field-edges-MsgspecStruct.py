from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import Optional, TypeAlias, Union



class Base(Struct):
    optional: Union[str, UnsetType] = UNSET



NullableName: TypeAlias = Optional[str]



class Edges(Base, kw_only=True):
    needed: int
    strict: Union[str, UnsetType] = UNSET
    wire_name: Union[int, UnsetType] = field(name='wire-name', default=1)
    alias_ref: Union[NullableName, UnsetType] = UNSET