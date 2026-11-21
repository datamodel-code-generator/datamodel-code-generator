# Protobuf option regression

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Payload(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    text: str | None = 'x(y),z'
    second: str | None = 'a]b\\\'"'
    marker: str | None = 'option (unknown) = "x"; // /* [,'
    count: int | None = 7
