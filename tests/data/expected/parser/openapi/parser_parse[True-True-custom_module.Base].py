from __future__ import annotations

from typing import List, Optional

from pydantic import AnyUrl, RootModel

from custom_module import Base


class Pet(Base):
    id: int
    name: str
    tag: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]


class User(Base):
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
    code: int
    message: str


class Api(Base):
    apiKey: Optional[str] = None
    apiVersionNumber: Optional[str] = None
    apiUrl: Optional[AnyUrl] = None
    apiDocumentationUrl: Optional[AnyUrl] = None


class Apis(RootModel[List[Api]]):
    root: List[Api]


class Event(Base):
    name: Optional[str] = None


class Result(Base):
    event: Optional[Event] = None
