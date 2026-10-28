# Protobuf detection regression

from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    syntax: str | None = 'syntax = "proto3";'