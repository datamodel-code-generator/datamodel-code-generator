from __future__ import annotations
from pydantic import constr
from typing import Optional, Union
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class Value:
    code: Optional[constr(min_length=1)] = None



@dataclass
class Value1:
    code: Optional[int] = None



@dataclass
class Payload:
    value: Optional[Union[Value, Value1]] = None



@dataclass
class Value2:
    code: Optional[str] = None



@dataclass
class Value3:
    code: Optional[int] = None



@dataclass
class Payload1:
    value: Optional[Union[Value2, Value3]] = None



@dataclass
class Parent:
    payload: Optional[Payload1] = None



@dataclass
class Choice1:
    code: Optional[str] = None



Choice = TypeAliasType("Choice", Choice1)



@dataclass
class Child(Parent):
    payload: Optional[Payload] = None