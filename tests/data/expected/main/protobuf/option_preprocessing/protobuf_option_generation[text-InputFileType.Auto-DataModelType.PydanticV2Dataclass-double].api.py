# Protobuf option regression

from __future__ import annotations

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass


@dataclass(config=ConfigDict(extra='forbid'))
class Payload:
    text: str | None = 'x(y),z'
    second: str | None = 'a]b\\\'"'
    marker: str | None = 'option (unknown) = "x"; // /* [,'
    count: int | None = 7