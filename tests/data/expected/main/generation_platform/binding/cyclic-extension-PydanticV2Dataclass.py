from __future__ import annotations
from typing import Optional
from pydantic.dataclasses import dataclass



@dataclass
class Pet:
    name: Optional[str] = None