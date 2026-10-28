# Protobuf detection regression

from __future__ import annotations

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass


@dataclass(config=ConfigDict(extra='forbid'))
class M:
    value: str | None = ''
    count: int | None = 0
