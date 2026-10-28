# Protobuf option regression

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Config:
    note: str | None = None
    numbers: list[int] | None = field(default_factory=list)
    child: Config | None = None


@dataclass
class Payload:
    text: str | None = 'x(y),z]\\\'"'
    """
    Docs [x(y)
    """
    second: str | None = 'a\\"\'[,]'
    count: int | None = -7
    joined: str | None = 'ab'
    absent: str | None = None
