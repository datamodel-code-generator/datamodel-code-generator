from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypedDict



class BaseRequest(TypedDict):
    value: NotRequired[str]
    note: NotRequired[str]



class Base(TypedDict):
    value: NotRequired[str]
    id: NotRequired[int]
    note: NotRequired[str]



class ChildRequest(TypedDict):
    value: NotRequired[int]
    note: NotRequired[str]



class Child(Base):
    value: NotRequired[int]
    note: NotRequired[str]