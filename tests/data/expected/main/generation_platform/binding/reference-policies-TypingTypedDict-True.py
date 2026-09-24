from __future__ import annotations
from typing_extensions import NotRequired
from typing import Optional, Tuple, TypedDict, Union



class NullableRecord(TypedDict):
    value: NotRequired[str]



class Base(TypedDict):
    kind: NotRequired[str]



class Child(Base):
    extra: NotRequired[int]



class Holder(TypedDict):
    record: Optional[NullableRecord]
    name: Optional[str]
    maybe: Optional[str]
    pair: Tuple[int, str]
    base: NotRequired[Base]