from __future__ import annotations
from typing import TypeAlias, TypedDict
from typing_extensions import NotRequired



class Base(TypedDict):
    pass



class Nested1(TypedDict):
    label: NotRequired[str]



class Nested2(TypedDict):
    pass



class Nested3(Nested1, Nested2):
    pass



Nested: TypeAlias = Nested3



class Mixed1(TypedDict):
    code: NotRequired[int]



class Mixed2(Mixed1, Base):
    pass



Mixed: TypeAlias = Mixed2



class Holder(TypedDict):
    nested: NotRequired[Nested]
    mixed: NotRequired[Mixed]