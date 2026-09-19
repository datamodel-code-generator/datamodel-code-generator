from __future__ import annotations
from typing import List, Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class Detail:
    code: Optional[str] = None
    count: Optional[int] = None



@dataclass
class Payload:
    detail: Optional[Detail] = None
    label: Optional[str] = None



Pair: TypeAlias = List[Union[str, int]]



@dataclass
class Node:
    detail: Optional[Detail] = None
    label: Optional[str] = None



@dataclass
class Parent:
    payload: Optional[Node] = None
    position: Optional[Pair] = None



@dataclass
class Child(Parent):
    payload: Optional[Payload] = None
    position: Optional[List[Union[str, int]]] = None