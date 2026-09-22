from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class Pet(BaseModel):
    name: Optional[str] = None


