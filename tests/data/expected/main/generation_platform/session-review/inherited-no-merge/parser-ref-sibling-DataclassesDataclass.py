from __future__ import annotations
from typing import Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class Value:
    code: Optional[str] = None



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



Choice: TypeAlias = Choice1



@dataclass
class Child(Parent):
    payload: Optional[Payload] = None