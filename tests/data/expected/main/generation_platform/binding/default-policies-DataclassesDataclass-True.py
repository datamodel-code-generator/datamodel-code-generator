from __future__ import annotations
from typing import Optional
from dataclasses import dataclass



@dataclass
class Defaults:
    implicit: Optional[str] = None
    schema_null: Optional[str] = None
    override_null: Optional[str] = None
    nullable: Optional[str] = None
    required_value: Optional[int] = None
    required_nullable: Optional[str] = None