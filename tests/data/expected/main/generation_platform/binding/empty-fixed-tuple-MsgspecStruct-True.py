from __future__ import annotations
from msgspec import Struct



class Record(Struct):
    value: tuple[()]