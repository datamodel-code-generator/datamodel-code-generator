"""Second family of colliding Python model names."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType

from tests.data.python.input_model.collision_a import Box
from tests.data.python.input_model.collision_a import Data as SharedData
from tests.data.python.input_model.collision_a import Parent as SharedParent


class Data(BaseModel):
    name: str
    child: Data | None = None


class Parent(BaseModel):
    second: str


class RootB(Parent):
    data: Data
    shared: SharedData
    reserved: Data_2


class Custom(BaseModel):
    value: str


Numbers = TypeAliasType("Numbers", list[int])


class Root(BaseModel):
    data: Data
    reserved: Data_2 | None = None
    numbers: Numbers = []
    values: frozenset[Data_2] = frozenset()
    custom: Custom | None = Field(None, json_schema_extra={"x-python-type": "Custom | None"})


class SharedRoot(SharedParent):
    shared: SharedData


class Kind(Enum):
    VALUE = "b"
    OTHER = "other-b"


class Mode(Enum):
    VALUE = "mode-b"
    OTHER = "mode-other-b"


class PlainB(BaseModel):
    data: Data
    kind: Kind | None = None
    mode: Mode | None = None


class Same(BaseModel):
    value: str


class SameRootB(BaseModel):
    data: Same


class EmptyDefinitions(BaseModel):
    second: str


class Recursive(Parent):
    name: str
    child: Recursive | None = None


class GenericRootB(BaseModel):
    data: Box[str]


class Data_2(BaseModel):
    reserved: bool
