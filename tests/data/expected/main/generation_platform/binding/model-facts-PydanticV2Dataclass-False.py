from __future__ import annotations
from pydantic.dataclasses import dataclass
from pydantic import ConfigDict



@dataclass(config=ConfigDict(extra='forbid'))
class Record:
    wireName: int