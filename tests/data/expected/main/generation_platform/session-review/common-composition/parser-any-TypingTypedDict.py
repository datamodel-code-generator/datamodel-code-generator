from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypeAlias, TypedDict, Union



class Value(TypedDict):
    left: NotRequired[str]



class Value1(TypedDict):
    right: NotRequired[int]



class Payload1(TypedDict):
    value: NotRequired[Union[Value, Value1]]



Payload: TypeAlias = Payload1