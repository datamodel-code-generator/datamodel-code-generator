from __future__ import annotations
from pydantic import BaseModel, RootModel, constr
from typing import Any, Dict, Optional, Union


class Combined(BaseModel):
    shared: constr(min_length=1, max_length=9)
    extra: Optional[int] = None
    flag: Optional[Any] = None
    ghost: Any


class Base(BaseModel):
    shared: constr(min_length=1)


class Other(BaseModel):
    shared: Optional[constr(max_length=9)] = None
    extra: Optional[int] = None
    flag: Optional[Any] = None


class Sibling1(BaseModel):
    a: Optional[constr(max_length=3)] = None
    open: Optional[Any] = None


class Sibling2(BaseModel):
    a: Optional[constr(max_length=3)] = None
    open: Optional[Any] = None
    b: Optional[str] = None


class Sibling(RootModel[Union[Sibling1, Sibling2]]):
    root: Union[Sibling1, Sibling2]


class Plain(BaseModel):
    a: Optional[str] = None
    open: Optional[Any] = None


class Appended1(BaseModel):
    c: Optional[str] = None


class Appended(RootModel[Union[Appended1, Dict[str, Any]]]):
    root: Union[Appended1, Dict[str, Any]]


class Branched1(BaseModel):
    c: Optional[str] = None


class Branched(RootModel[Branched1]):
    root: Branched1


class Thing1(BaseModel):
    a: Optional[str] = None


class Thing2(BaseModel):
    a: Optional[str] = None
    b: Optional[str] = None


class Thing3(BaseModel):
    a: Optional[str] = None


class Thing(RootModel[Union[Thing1, Thing2, Thing3]]):
    root: Union[Thing1, Thing2, Thing3]


class ThingBase1(BaseModel):
    a: Optional[str] = None


class ThingBase2(BaseModel):
    a: Optional[str] = None
    b: Optional[str] = None


class ThingBase(RootModel[Union[ThingBase1, ThingBase2]]):
    root: Union[ThingBase1, ThingBase2]


