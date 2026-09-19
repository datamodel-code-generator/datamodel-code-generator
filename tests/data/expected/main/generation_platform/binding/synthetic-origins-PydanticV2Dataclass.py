from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic.dataclasses import dataclass
from pydantic import ConfigDict, Field
from typing_extensions import TypeAliasType
from enum import Enum



@dataclass(config=ConfigDict(extra='allow'))
class Payload:
    normal: Optional[str] = None



Dictionary = TypeAliasType("Dictionary", Dict[str, str])


class Choice(Enum):
    first = 'first'
    second = 'second'



@dataclass
class Required(Payload):
    undeclared: Any = Field(..., kw_only=True)