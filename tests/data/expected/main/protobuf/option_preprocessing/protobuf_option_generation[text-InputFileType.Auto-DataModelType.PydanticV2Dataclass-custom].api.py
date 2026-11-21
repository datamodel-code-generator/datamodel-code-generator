# Protobuf option regression

from __future__ import annotations

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass


@dataclass(config=ConfigDict(extra='forbid'))
class Config:
    note: str | None = None
    numbers: list[int] | None = Field(default_factory=list)
    child: Config | None = None


@dataclass(config=ConfigDict(extra='forbid'))
class Payload:
    text: str | None = 'x(y),z]\\\'"'
    """
    Docs [x(y)
    """
    second: str | None = 'a\\"\'[,]'
    count: int | None = -7
    joined: str | None = 'ab'
    absent: str | None = None