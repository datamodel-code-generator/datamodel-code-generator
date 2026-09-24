from __future__ import annotations
from pydantic import constr
from typing import Any, Dict, Optional, Union
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class Combined:
    shared: constr(min_length=1, max_length=9)
    ghost: Any
    extra: Optional[int] = None
    flag: Optional[Any] = None



@dataclass
class Base:
    shared: constr(min_length=1)



@dataclass
class Other:
    shared: Optional[constr(max_length=9)] = None
    extra: Optional[int] = None
    flag: Optional[Any] = None



@dataclass
class Sibling1:
    a: Optional[constr(max_length=3)] = None
    open: Optional[Any] = None



@dataclass
class Sibling2:
    a: Optional[constr(max_length=3)] = None
    open: Optional[Any] = None
    b: Optional[str] = None



Sibling = TypeAliasType("Sibling", Union[Sibling1, Sibling2])



@dataclass
class Plain:
    a: Optional[str] = None
    open: Optional[Any] = None



@dataclass
class Appended1:
    c: Optional[str] = None



Appended = TypeAliasType("Appended", Union[Appended1, Dict[str, Any]])



@dataclass
class Branched1:
    c: Optional[str] = None



Branched = TypeAliasType("Branched", Branched1)



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



Thing = TypeAliasType("Thing", Union[Thing1, Thing2, Thing3])



@dataclass
class ThingBase1:
    a: Optional[str] = None



@dataclass
class ThingBase2:
    a: Optional[str] = None
    b: Optional[str] = None



ThingBase = TypeAliasType("ThingBase", Union[ThingBase1, ThingBase2])