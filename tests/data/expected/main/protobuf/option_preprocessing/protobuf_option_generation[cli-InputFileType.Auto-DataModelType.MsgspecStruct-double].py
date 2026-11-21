# Protobuf option regression

from __future__ import annotations

from msgspec import Struct, UnsetType


class Payload(Struct):
    text: str | UnsetType = 'x(y),z'
    second: str | UnsetType = 'a]b\\\'"'
    marker: str | UnsetType = 'option (unknown) = "x"; // /* [,'
    count: int | UnsetType = 7
