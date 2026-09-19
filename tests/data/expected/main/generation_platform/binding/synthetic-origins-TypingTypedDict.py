from __future__ import annotations
from typing_extensions import NotRequired, TypedDict
from typing import Any, Dict, TypeAlias
from enum import Enum



class Payload(TypedDict, extra_items=int):
    normal: NotRequired[str]



Dictionary: TypeAlias = Dict[str, str]


class Choice(Enum):
    first = 'first'
    second = 'second'



class Required(Payload):
    undeclared: Any