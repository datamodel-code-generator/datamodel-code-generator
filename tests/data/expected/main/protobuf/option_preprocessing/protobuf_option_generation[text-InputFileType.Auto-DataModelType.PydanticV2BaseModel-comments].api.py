# Protobuf option regression

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Payload(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    text: str | None = 'ok'
    """
    Docs [x(y)
    """
    second: str | None = 'value'
    """
    Docs [x(y)]
    option (unknown) = "keep this comment";
    """
    count: int | None = 7
    """
    Literal quotes ' " and /* [ ( , ]
    """