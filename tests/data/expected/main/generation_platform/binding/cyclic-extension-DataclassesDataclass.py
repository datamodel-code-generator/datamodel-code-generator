from __future__ import annotations
from typing import Optional
from dataclasses import dataclass



@dataclass
class Pet:
    name: Optional[str] = None