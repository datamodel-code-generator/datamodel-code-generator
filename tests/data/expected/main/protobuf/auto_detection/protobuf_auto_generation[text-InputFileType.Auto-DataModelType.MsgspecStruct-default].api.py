# Protobuf detection regression

from __future__ import annotations

from msgspec import UNSET, Struct, UnsetType


class M(Struct):
    value: str | UnsetType = 'key: value'
    count: int | UnsetType = UNSET