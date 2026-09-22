from __future__ import annotations
from typing import Any, Dict, List, Union
from pydantic import BaseModel, constr


class Values(BaseModel):
    any: Any
    list_any: List[Any]
    choice: Union[str, int]
    dictionary: Dict[str, str]
    pattern: Dict[constr(pattern=r'^x'), int]


