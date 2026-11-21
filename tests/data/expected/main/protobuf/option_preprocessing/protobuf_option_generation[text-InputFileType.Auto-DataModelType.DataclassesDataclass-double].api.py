# Protobuf option regression

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Payload:
    text: str | None = 'x(y),z'
    second: str | None = 'a]b\\\'"'
    marker: str | None = 'option (unknown) = "x"; // /* [,'
    count: int | None = 7