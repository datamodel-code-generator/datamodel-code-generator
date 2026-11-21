from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel


class Model499(BaseModel):
    id: int
    name: str
    value: Optional[float] = None
    active: Optional[bool] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, str]] = None
