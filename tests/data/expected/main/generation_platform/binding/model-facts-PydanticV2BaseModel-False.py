from __future__ import annotations
from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    wireName: int


