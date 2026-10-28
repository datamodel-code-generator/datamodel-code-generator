# Protobuf option regression

from __future__ import annotations

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass


@dataclass(config=ConfigDict(extra='forbid'))
class Payload:
    text: str | None = 'ok'
    """
    Docs [x(y)
    """
    second: str | None = 'value'
    """
    Docs [x(y)]
    option (unknown) = "keep this comment";
    """
    count: int | None = 7
    """
    Literal quotes ' " and /* [ ( , ]
    """
