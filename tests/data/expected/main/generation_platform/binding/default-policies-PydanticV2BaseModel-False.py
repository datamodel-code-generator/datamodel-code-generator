from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class Defaults(BaseModel):
    implicit: Optional[str] = None
    schema_null: Optional[str] = None
    override_null: Optional[str] = None
    nullable: Optional[str] = None
    required_value: int
    required_nullable: str


