# Protobuf detection regression

from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    value: str | None = None
