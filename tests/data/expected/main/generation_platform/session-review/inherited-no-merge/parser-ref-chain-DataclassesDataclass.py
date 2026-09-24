from __future__ import annotations
from typing import Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class Payload:
    code: Optional[str] = None
    left: Optional[str] = None



@dataclass
class Payload1:
    code: Optional[int] = None
    right: Optional[bool] = None



@dataclass
class Alias1:
    code: Optional[str] = None
    left: Optional[str] = None



@dataclass
class Alias2:
    code: Optional[int] = None
    right: Optional[bool] = None



Alias: TypeAlias = Union[Alias1, Alias2]



@dataclass
class Choice1:
    code: Optional[str] = None
    left: Optional[str] = None



Choice: TypeAlias = Choice1



@dataclass
class Parent:
    payload: Optional[Alias] = None



@dataclass
class Child(Parent):
    payload: Optional[Union[Payload, Payload1]] = None