from __future__ import annotations
from typing import Optional, TypeAlias
from dataclasses import dataclass



@dataclass
class Nested1:
    label: Optional[str] = None



@dataclass
class Nested2:
    pass



@dataclass
class Nested3(Nested1, Nested2):
    pass



Nested: TypeAlias = Nested3



@dataclass
class Holder:
    nested: Optional[Nested] = None