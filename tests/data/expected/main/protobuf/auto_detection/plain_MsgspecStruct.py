# Protobuf detection regression

from __future__ import annotations

from msgspec import Struct, UnsetType


class M(Struct):
    value: str | UnsetType = ''
    count: int | UnsetType = 0
