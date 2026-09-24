from __future__ import annotations
from typing_extensions import NotRequired
from typing import List, TypeAlias, TypedDict, Union



class Nested(TypedDict):
    same: NotRequired[str]
    common: NotRequired[int]
    specific: NotRequired[bool]



class Value(TypedDict):
    left: NotRequired[str]



class Value1(TypedDict):
    right: NotRequired[int]



class Payload1(TypedDict):
    nested: NotRequired[Nested]
    values: NotRequired[List[Union[Value, Value1]]]



class Nested1(TypedDict):
    same: NotRequired[str]
    common: NotRequired[int]



class Value2(TypedDict):
    left: NotRequired[str]



class Payload2(TypedDict):
    nested: NotRequired[Nested1]
    values: NotRequired[List[Value2]]
    tail: NotRequired[str]



Payload: TypeAlias = Union[Payload1, Payload2]