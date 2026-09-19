from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType, field
from typing import Dict, List, Union



class Defaults(Struct):
    required: bool
    missing: Union[str, UnsetType] = UNSET
    explicit_null: Union[str, UnsetType] = UNSET
    literal: Union[int, UnsetType] = 4
    items: Union[List[int], UnsetType] = field(default_factory=lambda: [1, 2])
    mapping: Union[Dict[str, bool], UnsetType] = field(default_factory=lambda: {'enabled': True})
    factory: Union[List[str], UnsetType] = field(default_factory=list)