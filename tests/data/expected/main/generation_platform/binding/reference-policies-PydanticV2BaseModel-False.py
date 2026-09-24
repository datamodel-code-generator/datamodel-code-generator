from __future__ import annotations
from typing import Optional, Tuple, Union
from pydantic import BaseModel, RootModel, SerializeAsAny


class NullableRecord(BaseModel):
    value: Optional[str] = None


class NullableName(RootModel[Optional[str]]):
    root: Optional[str]


class Base(BaseModel):
    kind: Optional[str] = None


class Child(Base):
    extra: Optional[int] = None


class Holder(BaseModel):
    record: Optional[NullableRecord]
    name: Optional[NullableName]
    maybe: Optional[str]
    pair: Tuple[int, str]
    base: Optional[SerializeAsAny[Base]] = None


