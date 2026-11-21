# Protobuf detection regression

from __future__ import annotations

from pydantic import BaseModel, Field


class Model(BaseModel):
    syntax____proto3___: str = Field(..., alias="syntax = 'proto3'; ")
    message: str