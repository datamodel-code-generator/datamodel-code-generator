from __future__ import annotations
from pydantic.dataclasses import dataclass
from typing import Optional
from typing_extensions import TypeAliasType



@dataclass
class Base:
    pass



@dataclass
class Nested1:
    label: Optional[str] = None



@dataclass
class Nested2:
    pass



@dataclass
class Nested3(Nested1, Nested2):
    pass



Nested = TypeAliasType("Nested", Nested3)



@dataclass
class Mixed1:
    code: Optional[int] = None



@dataclass
class Mixed2(Mixed1, Base):
    pass



Mixed = TypeAliasType("Mixed", Mixed2)



@dataclass
class Holder:
    nested: Optional[Nested] = None
    mixed: Optional[Mixed] = None