# Protobuf option regression

from __future__ import annotations

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass


@dataclass(config=ConfigDict(extra='forbid'))
class Payload:
    text: str | None = 'ok'
    """
    Ordinary description
    """
    count: int | None = 7
