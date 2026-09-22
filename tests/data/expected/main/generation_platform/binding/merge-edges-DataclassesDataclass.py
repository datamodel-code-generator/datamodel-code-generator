from __future__ import annotations
from typing import Any, Dict, Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class Combined:
    shared: str
    ghost: Any
    extra: Optional[int] = None
    flag: Optional[Any] = None



@dataclass
class Base:
    shared: str



@dataclass
class Other:
    shared: Optional[str] = None
    extra: Optional[int] = None
    flag: Optional[Any] = None



@dataclass
class Sibling1:
    a: Optional[str] = None
    open: Optional[Any] = None



@dataclass
class Sibling2:
    a: Optional[str] = None
    open: Optional[Any] = None
    b: Optional[str] = None



Sibling: TypeAlias = Union[Sibling1, Sibling2]



@dataclass
class Plain:
    a: Optional[str] = None
    open: Optional[Any] = None



@dataclass
class Appended1:
    c: Optional[str] = None



Appended: TypeAlias = Union[Appended1, Dict[str, Any]]



@dataclass
class Branched1:
    c: Optional[str] = None



Branched: TypeAlias = Branched1



@dataclass
class Thing1:
    a: Optional[str] = None



@dataclass
class Thing2:
    a: Optional[str] = None
    b: Optional[str] = None



@dataclass
class Thing3:
    a: Optional[str] = None



Thing: TypeAlias = Union[Thing1, Thing2, Thing3]



@dataclass
class ThingBase1:
    a: Optional[str] = None



@dataclass
class ThingBase2:
    a: Optional[str] = None
    b: Optional[str] = None



ThingBase: TypeAlias = Union[ThingBase1, ThingBase2]