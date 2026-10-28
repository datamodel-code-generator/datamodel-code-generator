# Protobuf detection regression

from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    syntax: str
    edition: str
    message: str
