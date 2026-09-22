from __future__ import annotations
from typing import Tuple
from msgspec import Struct



class Record(Struct):
    value: Tuple[()]