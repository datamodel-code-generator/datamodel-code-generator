from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, RootModel


class Pet(BaseModel):
    id: int
    name: str
    tag: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]


class Error(BaseModel):
    code: int
    message: str


class Event(BaseModel):
    name: Optional[str] = None


class Result(BaseModel):
    event: Optional[Event] = None


class Events(RootModel[List[Event]]):
    root: List[Event]


class EventRoot(RootModel[Event]):
    root: Event


class EventObject(BaseModel):
    event: Optional[Event] = None


class DuplicateObject1(BaseModel):
    event: Optional[List[Event]] = None


class Event1(BaseModel):
    event: Optional[Event] = None


class DuplicateObject2(BaseModel):
    event: Optional[Event1] = None


class DuplicateObject3(RootModel[Event]):
    root: Event
