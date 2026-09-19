from __future__ import annotations
from typing import Dict, List, Optional, TypeAlias, Union
from msgspec import Struct, UNSET, UnsetType



NestedItem: TypeAlias = str



Choice: TypeAlias = str



Choice1: TypeAlias = int



class Metadata(Struct):
    integer: int
    number: float
    nested: List[List[NestedItem]]
    mapping: Dict[str, int]
    choice: Union[Choice, Choice1]
    nullable: Optional[str]
    optional: Union[str, UnsetType] = UNSET