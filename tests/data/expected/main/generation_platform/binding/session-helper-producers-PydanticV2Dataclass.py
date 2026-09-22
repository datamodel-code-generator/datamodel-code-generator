from __future__ import annotations
from typing import Any, Dict, List, Union
from pydantic import constr
from pydantic.dataclasses import dataclass



@dataclass
class Values:
    any: Any
    list_any: List[Any]
    choice: Union[str, int]
    dictionary: Dict[str, str]
    pattern: Dict[constr(pattern=r'^x'), int]