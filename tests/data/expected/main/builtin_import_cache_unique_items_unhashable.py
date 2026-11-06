from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class Item(BaseModel):
    values: Optional[List[Dict[str, Any]]] = [{'a': 1}]


class Left(BaseModel):
    item: Optional[Item] = None


class Model(BaseModel):
    left: Optional[Left] = None
    right: Optional[Left] = None
