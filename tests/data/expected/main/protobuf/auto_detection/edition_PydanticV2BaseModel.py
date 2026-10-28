# Protobuf detection regression

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class M(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: str | None = None
    count: int | None = None
