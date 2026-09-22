from __future__ import annotations
from typing import Optional, Tuple, Union
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType
from pydantic import SerializeAsAny



@dataclass
class NullableRecord:
    value: Optional[str] = None



NullableName = TypeAliasType("NullableName", Optional[str])



@dataclass
class Base:
    kind: Optional[str] = None



@dataclass
class Child(Base):
    extra: Optional[int] = None



@dataclass
class Holder:
    record: Optional[NullableRecord]
    name: NullableName
    maybe: Optional[str]
    pair: Tuple[int, str]
    base: Optional[SerializeAsAny[Base]] = None