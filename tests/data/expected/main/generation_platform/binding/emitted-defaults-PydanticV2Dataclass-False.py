from __future__ import annotations
from pydantic.experimental.missing_sentinel import MISSING
from typing import Dict, List, Optional, Union
from pydantic import Field
from pydantic.dataclasses import dataclass



@dataclass
class Defaults:
    required: bool
    missing: Union[str, MISSING] = MISSING
    explicit_null: Optional[str] = None
    literal: Optional[int] = 4
    items: Optional[List[int]] = Field(default_factory=lambda: [1, 2])
    mapping: Optional[Dict[str, bool]] = Field(default_factory=lambda: {'enabled': True})
    factory: List[str] = Field(default_factory=list)