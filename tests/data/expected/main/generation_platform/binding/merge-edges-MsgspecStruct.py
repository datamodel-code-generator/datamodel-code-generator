from __future__ import annotations
from typing import Any, Dict, TypeAlias, Union
from msgspec import Struct, UNSET, UnsetType



class Combined(Struct):
    shared: str
    ghost: Any
    extra: Union[int, UnsetType] = UNSET
    flag: Union[Any, UnsetType] = UNSET



class Base(Struct):
    shared: str



class Other(Struct):
    shared: Union[str, UnsetType] = UNSET
    extra: Union[int, UnsetType] = UNSET
    flag: Union[Any, UnsetType] = UNSET



class Sibling1(Struct):
    a: Union[str, UnsetType] = UNSET
    open: Union[Any, UnsetType] = UNSET



class Sibling2(Struct):
    a: Union[str, UnsetType] = UNSET
    open: Union[Any, UnsetType] = UNSET
    b: Union[str, UnsetType] = UNSET



Sibling: TypeAlias = Union[Sibling1, Sibling2]



class Plain(Struct):
    a: Union[str, UnsetType] = UNSET
    open: Union[Any, UnsetType] = UNSET



class Appended1(Struct):
    c: Union[str, UnsetType] = UNSET



Appended: TypeAlias = Union[Appended1, Dict[str, Any]]



class Branched1(Struct):
    c: Union[str, UnsetType] = UNSET



Branched: TypeAlias = Branched1



class Thing1(Struct):
    a: Union[str, UnsetType] = UNSET



class Thing2(Struct):
    a: Union[str, UnsetType] = UNSET
    b: Union[str, UnsetType] = UNSET



class Thing3(Struct):
    a: Union[str, UnsetType] = UNSET



Thing: TypeAlias = Union[Thing1, Thing2, Thing3]



class ThingBase1(Struct):
    a: Union[str, UnsetType] = UNSET



class ThingBase2(Struct):
    a: Union[str, UnsetType] = UNSET
    b: Union[str, UnsetType] = UNSET



ThingBase: TypeAlias = Union[ThingBase1, ThingBase2]