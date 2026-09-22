from __future__ import annotations
from typing import Optional, Union
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class Value:
    left: Optional[str] = None



@dataclass
class Value1:
    right: Optional[int] = None



@dataclass
class Payload1:
    value: Optional[Union[Value, Value1]] = None



Payload = TypeAliasType("Payload", Payload1)