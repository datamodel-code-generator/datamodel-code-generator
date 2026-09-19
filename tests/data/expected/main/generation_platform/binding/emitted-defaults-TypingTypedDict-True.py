from __future__ import annotations
from typing_extensions import NotRequired
from typing import Dict, List, TypedDict



class Defaults(TypedDict):
    missing: NotRequired[str]
    explicit_null: NotRequired[str]
    literal: NotRequired[int]
    items: NotRequired[List[int]]
    mapping: NotRequired[Dict[str, bool]]
    factory: NotRequired[List[str]]
    required: bool