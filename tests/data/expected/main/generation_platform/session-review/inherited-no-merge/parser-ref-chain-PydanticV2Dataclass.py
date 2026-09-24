from __future__ import annotations
from pydantic import constr
from typing import Optional, Union
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class Payload:
    code: Optional[constr(min_length=1)] = None
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



Alias = TypeAliasType("Alias", Union[Alias1, Alias2])



@dataclass
class Choice1:
    code: Optional[str] = None
    left: Optional[str] = None



Choice = TypeAliasType("Choice", Choice1)



@dataclass
class Parent:
    payload: Optional[Alias] = None



@dataclass
class Child(Parent):
    payload: Optional[Union[Payload, Payload1]] = None