from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypeAlias, TypedDict, Union



class Value(TypedDict):
    code: NotRequired[str]



class Value1(TypedDict):
    code: NotRequired[int]



class Payload(TypedDict):
    value: NotRequired[Union[Value, Value1]]



class Value2(TypedDict):
    code: NotRequired[str]



class Value3(TypedDict):
    code: NotRequired[int]



class Payload1(TypedDict):
    value: NotRequired[Union[Value2, Value3]]



class Parent(TypedDict):
    payload: NotRequired[Payload1]



class Choice1(TypedDict):
    code: NotRequired[str]



Choice: TypeAlias = Choice1



class Child(Parent):
    payload: NotRequired[Payload]