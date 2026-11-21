from __future__ import annotations
from pydantic import BaseModel, RootModel
from typing import List, Optional
from . import models


class OptionalModel(RootModel[str]):
    root: str


class Id(RootModel[str]):
    root: str


class Error(BaseModel):
    code: int
    message: str


class Result(BaseModel):
    event: Optional[models.Event] = None


class Source(BaseModel):
    country: Optional[str] = None


class DifferentTea(BaseModel):
    foo: Optional[Tea] = None
    nested: Optional[Tea_1] = None


class Tea(BaseModel):
    flavour: Optional[str] = None
    id: Optional[Id] = None


class Cocoa(BaseModel):
    quality: Optional[int] = None


class Tea_1(BaseModel):
    flavour: Optional[str] = None
    id: Optional[Id] = None
    self: Optional[Tea_1] = None
    optional: Optional[List[OptionalModel]] = None


class TeaClone(BaseModel):
    flavour: Optional[str] = None
    id: Optional[Id] = None
    self: Optional[Tea_1] = None
    optional: Optional[List[OptionalModel]] = None


class ListModel(RootModel[List[Tea_1]]):
    root: List[Tea_1]