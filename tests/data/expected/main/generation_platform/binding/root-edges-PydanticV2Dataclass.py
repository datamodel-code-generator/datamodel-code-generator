from __future__ import annotations
from typing import List, Optional
from pydantic.dataclasses import dataclass
from pydantic import constr
from typing_extensions import TypeAliasType



@dataclass
class Obj:
    k: Optional[str] = None



@dataclass
class BaseItem:
    k: Optional[constr(max_length=5)] = None



Base = TypeAliasType("Base", List[BaseItem])



Value = TypeAliasType("Value", Base)