from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import Optional, Tuple, Union



class NullableRecord(Struct):
    value: Union[str, UnsetType] = UNSET



class Base(Struct):
    kind: Union[str, UnsetType] = UNSET



class Child(Base):
    extra: Union[int, UnsetType] = UNSET



class Holder(Struct):
    record: Optional[NullableRecord]
    name: Optional[str]
    maybe: Optional[str]
    pair: Tuple[int, str]
    base: Union[Base, UnsetType] = UNSET