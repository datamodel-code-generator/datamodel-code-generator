# Protobuf option regression

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Payload:
    text: str | None = 'ok'
    """
    Ordinary description
    """
    count: int | None = 7