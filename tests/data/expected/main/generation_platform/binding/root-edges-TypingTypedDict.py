from __future__ import annotations
from typing_extensions import NotRequired
from typing import List, TypeAlias, TypedDict



class Obj(TypedDict):
    k: NotRequired[str]



class BaseItem(TypedDict):
    k: NotRequired[str]



Base: TypeAlias = List[BaseItem]



Value: TypeAlias = Base