from __future__ import annotations
from typing_extensions import NotRequired
from typing import Optional, Tuple, TypeAlias, TypedDict, Union



class NullableRecord(TypedDict):
    value: NotRequired[str]



NullableName: TypeAlias = Optional[str]



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