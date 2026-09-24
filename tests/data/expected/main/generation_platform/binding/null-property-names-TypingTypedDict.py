from __future__ import annotations
from typing_extensions import NotRequired
from typing import List, TypeAlias, TypedDict, Union



class Choice1(TypedDict):
    a: NotRequired[str]



class Choice2(TypedDict):
    b: NotRequired[int]



Choice: TypeAlias = Union[Choice1, Choice2]



class BaseArrayItem(TypedDict):
    a: NotRequired[str]



BaseArray: TypeAlias = List[BaseArrayItem]



Array: TypeAlias = BaseArray