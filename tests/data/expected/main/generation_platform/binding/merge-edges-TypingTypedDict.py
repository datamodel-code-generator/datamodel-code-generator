from __future__ import annotations
from typing_extensions import NotRequired
from typing import Any, Dict, TypeAlias, TypedDict, Union



class Combined(TypedDict):
    shared: str
    extra: NotRequired[int]
    flag: NotRequired[Any]
    ghost: Any



class Base(TypedDict):
    shared: str



class Other(TypedDict):
    shared: NotRequired[str]
    extra: NotRequired[int]
    flag: NotRequired[Any]



class Sibling1(TypedDict):
    a: NotRequired[str]
    open: NotRequired[Any]



class Sibling2(TypedDict):
    a: NotRequired[str]
    open: NotRequired[Any]
    b: NotRequired[str]



Sibling: TypeAlias = Union[Sibling1, Sibling2]



class Plain(TypedDict):
    a: NotRequired[str]
    open: NotRequired[Any]



class Appended1(TypedDict):
    c: NotRequired[str]



Appended: TypeAlias = Union[Appended1, Dict[str, Any]]



class Branched1(TypedDict):
    c: NotRequired[str]



Branched: TypeAlias = Branched1



class Thing1(TypedDict):
    a: NotRequired[str]



class Thing2(TypedDict):
    a: NotRequired[str]
    b: NotRequired[str]



class Thing3(TypedDict):
    a: NotRequired[str]



Thing: TypeAlias = Union[Thing1, Thing2, Thing3]



class ThingBase1(TypedDict):
    a: NotRequired[str]



class ThingBase2(TypedDict):
    a: NotRequired[str]
    b: NotRequired[str]



ThingBase: TypeAlias = Union[ThingBase1, ThingBase2]