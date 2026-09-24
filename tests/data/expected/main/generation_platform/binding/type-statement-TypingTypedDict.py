from __future__ import annotations
from typing import NotRequired, Optional, Tuple, TypedDict, Union



class NullableRecord(TypedDict):
    value: NotRequired[str]



type NullableName = Optional[str]



class Base(TypedDict):
    kind: NotRequired[str]



class Child(Base):
    extra: NotRequired[int]



class Holder(TypedDict):
    record: Optional[NullableRecord]
    name: NullableName
    maybe: Optional[str]
    pair: Tuple[int, str]
    base: NotRequired[Base]