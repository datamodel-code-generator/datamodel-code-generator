"""First family of colliding Python model names."""
from __future__ import annotations

from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic_core import core_schema


class Data(BaseModel):
    id: int
    child: Data | None = None


class Parent(BaseModel):
    first: int


class RootA(Parent):
    data: Data


class Root(BaseModel):
    data: Data


class Kind(Enum):
    VALUE = "a"
    OTHER = "other-a"


class Mode(Enum):
    VALUE = "mode-a"
    OTHER = "mode-other-a"


class PlainA(BaseModel):
    data: Data
    kind: Kind | None = None
    mode: Mode | None = None


class Same(BaseModel):
    value: str


class SameRootA(BaseModel):
    data: Same


class ExtraDefinitions(BaseModel):
    model_config = ConfigDict(json_schema_extra={"$defs": {"Spare": {"type": "integer"}}, "allOf": [{"type": "object"}]})
    first: int


class Recursive(Parent):
    id: int
    child: Recursive | None = None


T = TypeVar("T")


class Box(BaseModel, Generic[T]):
    value: T


class Arbitrary:
    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        return core_schema.is_instance_schema(cls, ref="Arbitrary")


class GenericRootA(BaseModel):
    data: Box[int]
    arbitrary: Arbitrary | None = None
