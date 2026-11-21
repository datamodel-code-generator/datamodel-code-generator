from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import ConfigDict, RootModel

from custom_module import Base


class Pet(Base):
    id: int
    name: str
    tag: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]


class User(Base):
    model_config = ConfigDict(
        extra='allow',
    )
    id: int
    name: str
    tag: Optional[str] = None


class Users(RootModel[List[User]]):
    root: List[User]


class Id(RootModel[str]):
    root: str


class Rules(RootModel[List[str]]):
    root: List[str]


class Error(Base):
    model_config = ConfigDict(
        extra='forbid',
    )
    code: int
    message: str


class Event(Base):
    name: Optional[str] = None


class Result(Base):
    event: Optional[Event] = None


class Broken(Base):
    foo: Optional[str] = None
    bar: Optional[int] = None


class BrokenArray(Base):
    broken: Optional[Dict[str, List[Broken]]] = None


class FileSetUpload(Base):
    task_id: Optional[str] = None
    tags: Dict[str, List[str]]


class Test(Base):
    broken: Optional[Dict[str, Broken]] = None
    failing: Optional[Dict[str, str]] = {}
