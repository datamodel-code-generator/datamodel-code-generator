from __future__ import annotations
from typing import Any, Dict, List, Union
from msgspec import Struct



class Values(Struct):
    any: Any
    list_any: List[Any]
    choice: Union[str, int]
    dictionary: Dict[str, str]
    pattern: Dict[str, int]