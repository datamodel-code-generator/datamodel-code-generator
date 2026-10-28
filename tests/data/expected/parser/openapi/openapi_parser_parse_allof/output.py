from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import AwareDatetime, BaseModel, RootModel, conint


class Pet(BaseModel):
    id: int
    name: str
    tag: Optional[str] = None


class Car(BaseModel):
    number: str


class AllOfref(Pet, Car):
    pass


class AllOfobj(BaseModel):
    name: Optional[str] = None
    number: Optional[str] = None


class AllOfCombine(Pet):
    birthdate: Optional[date] = None
    size: Optional[conint(ge=1)] = None


class AnyOfCombine(Pet, Car):
    age: Optional[str] = None


class Item(Pet, Car):
    age: Optional[str] = None


class AnyOfCombineInObject(BaseModel):
    item: Optional[Item] = None


class AnyOfCombineInArrayItem(Pet, Car):
    age: Optional[str] = None


class AnyOfCombineInArray(RootModel[List[AnyOfCombineInArrayItem]]):
    root: List[AnyOfCombineInArrayItem]


class AnyOfCombineInRoot(Pet, Car):
    age: Optional[str] = None
    birthdate: Optional[AwareDatetime] = None


class AnyOfCombineUnknownObjectInRoot(RootModel[List[Pet]]):
    root: List[Pet]


class AnyOfCombineUnknownObjectInArray(Pet):
    pass


class Error(BaseModel):
    code: int
    message: str


class AllOfNested3(AllOfCombine):
    name: AnyOfCombine


class AllOfNested2(AllOfNested3):
    name: AllOfNested1


class AllOfNested1(AllOfNested2):
    name: AllOfCombine
