from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypeAlias, TypedDict, Union



class Payload(TypedDict):
    code: NotRequired[str]
    left: NotRequired[str]



class Payload1(TypedDict):
    code: NotRequired[int]
    right: NotRequired[bool]



class Alias1(TypedDict):
    code: NotRequired[str]
    left: NotRequired[str]



class Alias2(TypedDict):
    code: NotRequired[int]
    right: NotRequired[bool]



Alias: TypeAlias = Union[Alias1, Alias2]



class Choice1(TypedDict):
    code: NotRequired[str]
    left: NotRequired[str]



Choice: TypeAlias = Choice1



class Parent(TypedDict):
    payload: NotRequired[Alias]



class Child(Parent):
    payload: NotRequired[Union[Payload, Payload1]]