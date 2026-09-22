from __future__ import annotations
from typing import List, Optional, TypeAlias
from dataclasses import dataclass



@dataclass
class Obj:
    k: Optional[str] = None



@dataclass
class BaseItem:
    k: Optional[str] = None



Base: TypeAlias = List[BaseItem]



Value: TypeAlias = Base