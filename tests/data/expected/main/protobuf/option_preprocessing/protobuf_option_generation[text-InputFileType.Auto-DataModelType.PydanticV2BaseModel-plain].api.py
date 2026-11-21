# Protobuf option regression

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Payload(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    text: str | None = 'ok'
    """
    Ordinary description
    """
    count: int | None = 7