from __future__ import annotations
from typing import Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class Value:
    left: Optional[str] = None



@dataclass
class Value1:
    right: Optional[int] = None



@dataclass
class Payload1:
    value: Optional[Union[Value, Value1]] = None



Payload: TypeAlias = Payload1