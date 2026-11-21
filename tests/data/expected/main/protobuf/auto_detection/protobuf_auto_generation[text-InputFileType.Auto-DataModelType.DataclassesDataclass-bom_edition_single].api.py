# Protobuf detection regression

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class M:
    value: str | None = None
    count: int | None = None