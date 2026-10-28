# Protobuf option regression

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    note: str | None = None
    numbers: list[int] | None = []
    child: Config | None = None


class Payload(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    text: str | None = 'x(y),z]\\\'"'
    """
    Docs [x(y)
    """
    second: str | None = 'a\\"\'[,]'
    count: int | None = -7
    joined: str | None = 'ab'
    absent: str | None = None


Config.model_rebuild()