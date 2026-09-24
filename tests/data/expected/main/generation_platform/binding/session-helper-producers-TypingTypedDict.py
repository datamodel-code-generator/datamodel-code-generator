from __future__ import annotations
from typing import Any, Dict, List, TypedDict, Union



class Values(TypedDict):
    any: Any
    list_any: List[Any]
    choice: Union[str, int]
    dictionary: Dict[str, str]
    pattern: Dict[str, int]