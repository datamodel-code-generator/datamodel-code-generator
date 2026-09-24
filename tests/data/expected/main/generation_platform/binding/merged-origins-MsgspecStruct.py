from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import TypeAlias, Union



class SideKey(Struct):
    nested_key: Union[str, UnsetType] = field(name='nested-key', default=UNSET)



class RefUse1(Struct):
    shared_key: Union[str, UnsetType] = field(name='shared-key', default=UNSET)
    side_key: Union[SideKey, UnsetType] = field(name='side-key', default=UNSET)



RefUse: TypeAlias = RefUse1



class Combined(Struct):
    shared_key: Union[str, UnsetType] = field(name='shared-key', default=UNSET)



class Base(Struct):
    shared_key: Union[str, UnsetType] = field(name='shared-key', default=UNSET)



class Other(Struct):
    shared_key: Union[str, UnsetType] = field(name='shared-key', default=UNSET)