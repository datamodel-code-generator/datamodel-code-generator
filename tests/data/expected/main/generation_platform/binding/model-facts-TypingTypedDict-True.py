from __future__ import annotations
from typing_extensions import Required, TypedDict



class Record(TypedDict, total=False, closed=True):
    wireName: Required[int]