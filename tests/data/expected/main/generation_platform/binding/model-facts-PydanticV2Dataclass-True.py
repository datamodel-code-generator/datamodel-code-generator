from __future__ import annotations
from pydantic.dataclasses import dataclass
from pydantic import ConfigDict



@dataclass(frozen=True, slots=True, kw_only=True, config=ConfigDict(extra='forbid', strict=True, populate_by_name=True))
class Record:
    wireName: int