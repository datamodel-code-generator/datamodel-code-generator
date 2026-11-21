from __future__ import annotations
from typing import List, Optional
from pydantic import AnyUrl, BaseModel, RootModel


class Pet(BaseModel):
    id: int
    name: str
    tag: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]


class User(BaseModel):
    id: int
    name: str
    tag: Optional[str] = None


class Users(RootModel[List[User]]):
    root: List[User]


class Id(RootModel[str]):
    root: str


class Rules(RootModel[List[str]]):
    root: List[str]


class Error(BaseModel):
    code: int
    message: str


class Api(BaseModel):
    apiKey: Optional[str] = None
    apiVersionNumber: Optional[str] = None
    apiUrl: Optional[AnyUrl] = None
    apiDocumentationUrl: Optional[AnyUrl] = None


class Apis(RootModel[List[Api]]):
    root: List[Api]


class Event(BaseModel):
    name: Optional[str] = None


class Result(BaseModel):
    event: Optional[Event] = None