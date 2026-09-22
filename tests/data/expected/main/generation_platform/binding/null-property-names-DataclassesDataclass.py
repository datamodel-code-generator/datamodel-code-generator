from __future__ import annotations
from typing import List, Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class Choice1:
    a: Optional[str] = None



@dataclass
class Choice2:
    b: Optional[int] = None



Choice: TypeAlias = Union[Choice1, Choice2]



@dataclass
class BaseArrayItem:
    a: Optional[str] = None



BaseArray: TypeAlias = List[BaseArrayItem]



Array: TypeAlias = BaseArray