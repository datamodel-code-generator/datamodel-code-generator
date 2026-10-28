# Protobuf detection regression

from __future__ import annotations

from pydantic import BaseModel, Field


class Model(BaseModel):
    field___syntax____proto3__: str = Field(..., alias="// syntax = 'proto3';")
    edition: str
    message: str
