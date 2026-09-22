from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypeAlias, TypedDict



class Nested1(TypedDict):
    label: NotRequired[str]



class Nested2(TypedDict):
    pass



class Nested3(Nested1, Nested2):
    pass



Nested: TypeAlias = Nested3



class Holder(TypedDict):
    nested: NotRequired[Nested]