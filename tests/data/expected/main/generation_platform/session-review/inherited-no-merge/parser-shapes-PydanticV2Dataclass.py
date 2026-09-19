from __future__ import annotations
from pydantic import Field, constr
from typing import Annotated, List, Optional, Union
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class Detail:
    code: Optional[constr(min_length=1)] = None
    count: Optional[int] = None



@dataclass
class Payload:
    detail: Optional[Detail] = None
    label: Optional[str] = None



@dataclass
class DetailModel:
    code: Optional[str] = None
    count: Optional[int] = None



Pair = TypeAliasType("Pair", Annotated[List[Union[str, int]], Field(..., max_length=2)])



@dataclass
class Node:
    detail: Optional[DetailModel] = None
    label: Optional[str] = None



@dataclass
class Parent:
    payload: Optional[Node] = None
    position: Optional[Pair] = None



@dataclass
class Child(Parent):
    payload: Optional[Payload] = None
    position: Optional[List[Union[constr(min_length=1), int]]] = Field(None, max_length=2)