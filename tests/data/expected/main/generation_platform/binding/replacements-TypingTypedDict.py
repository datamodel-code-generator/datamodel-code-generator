from __future__ import annotations
from typing import List, TypedDict
from typing_extensions import NotRequired



class Record(TypedDict):
    value: NotRequired[str]



class Holder(TypedDict):
    text: NotRequired[str]
    list: NotRequired[List[str]]
    original: NotRequired[Record]
    copy: NotRequired[Record]
    wrapper: NotRequired[Record]