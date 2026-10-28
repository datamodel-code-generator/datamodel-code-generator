# Protobuf option regression

from __future__ import annotations

from dataclasses import dataclass


@dataclass
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
