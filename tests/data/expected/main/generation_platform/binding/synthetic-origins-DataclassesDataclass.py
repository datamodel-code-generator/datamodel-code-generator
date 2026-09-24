from __future__ import annotations
from typing import Any, Dict, Optional, TypeAlias
from dataclasses import dataclass, field
from enum import Enum



@dataclass
class Payload:
    normal: Optional[str] = None



Dictionary: TypeAlias = Dict[str, str]


class Choice(Enum):
    first = 'first'
    second = 'second'



@dataclass
class Required(Payload):
    undeclared: Any = field(kw_only=True)