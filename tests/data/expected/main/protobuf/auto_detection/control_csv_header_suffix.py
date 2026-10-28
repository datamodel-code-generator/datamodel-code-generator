# Protobuf detection regression

from __future__ import annotations

from pydantic import BaseModel, Field


class Model(BaseModel):
    syntax____proto3___metadata: str = Field(..., alias="syntax = 'proto3'; metadata")
    name: str
