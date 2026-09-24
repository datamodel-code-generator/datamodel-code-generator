from __future__ import annotations
from typing import List, Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class Nested:
    same: Optional[str] = None
    common: Optional[int] = None
    specific: Optional[bool] = None



@dataclass
class Value:
    left: Optional[str] = None



@dataclass
class Value1:
    right: Optional[int] = None



@dataclass
class Payload1:
    nested: Optional[Nested] = None
    values: Optional[List[Union[Value, Value1]]] = None



@dataclass
class Nested1:
    same: Optional[str] = None
    common: Optional[int] = None



@dataclass
class Value2:
    left: Optional[str] = None



@dataclass
class Payload2:
    nested: Optional[Nested1] = None
    values: Optional[List[Value2]] = None
    tail: Optional[str] = None



Payload: TypeAlias = Union[Payload1, Payload2]