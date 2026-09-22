from __future__ import annotations
from pydantic import Field, constr
from typing import List, Optional, Union
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class Nested:
    same: Optional[constr(min_length=1, max_length=3)] = None
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
    values: Optional[List[Union[Value, Value1]]] = Field(None, max_length=2)



@dataclass
class Nested1:
    same: Optional[constr(min_length=1)] = None
    common: Optional[int] = None



@dataclass
class Value2:
    left: Optional[str] = None



@dataclass
class Payload2:
    nested: Optional[Nested1] = None
    values: Optional[List[Value2]] = Field(None, max_length=1)
    tail: Optional[str] = None



Payload = TypeAliasType("Payload", Union[Payload1, Payload2])