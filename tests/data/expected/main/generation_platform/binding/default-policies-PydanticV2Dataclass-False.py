from __future__ import annotations
from typing import Optional
from pydantic.dataclasses import dataclass



@dataclass
class Defaults:
    required_value: int
    required_nullable: str
    implicit: Optional[str] = None
    schema_null: Optional[str] = None
    override_null: Optional[str] = None
    nullable: Optional[str] = None