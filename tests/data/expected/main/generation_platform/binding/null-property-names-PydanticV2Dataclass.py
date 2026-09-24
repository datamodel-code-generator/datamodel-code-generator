from __future__ import annotations
from typing import List, Optional, Union
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class Choice1:
    a: Optional[str] = None



@dataclass
class Choice2:
    b: Optional[int] = None



Choice = TypeAliasType("Choice", Union[Choice1, Choice2])



@dataclass
class BaseArrayItem:
    a: Optional[str] = None



BaseArray = TypeAliasType("BaseArray", List[BaseArrayItem])



Array = TypeAliasType("Array", BaseArray)