from __future__ import annotations
from typing import Tuple
from pydantic.dataclasses import dataclass



@dataclass
class Record:
    value: Tuple[()]