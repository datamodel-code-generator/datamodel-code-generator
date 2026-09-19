from __future__ import annotations
from typing import Dict, List, Optional
from dataclasses import dataclass, field



@dataclass
class Defaults:
    required: bool
    missing: Optional[str] = None
    explicit_null: Optional[str] = None
    literal: Optional[int] = 4
    items: Optional[List[int]] = field(default_factory=lambda: [1, 2])
    mapping: Optional[Dict[str, bool]] = field(default_factory=lambda: {'enabled': True})
    factory: List[str] = field(default_factory=list)