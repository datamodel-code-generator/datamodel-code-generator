from __future__ import annotations
from typing import List, Optional
from dataclasses import dataclass



@dataclass
class Record:
    value: Optional[str] = None



@dataclass
class Holder:
    text: Optional[str] = None
    list: Optional[List[str]] = None
    original: Optional[Record] = None
    copy: Optional[Record] = None
    wrapper: Optional[Record] = None