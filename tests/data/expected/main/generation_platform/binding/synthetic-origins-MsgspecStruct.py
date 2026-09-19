from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import Any, Dict, TypeAlias, Union
from enum import Enum



class Payload(Struct):
    normal: Union[str, UnsetType] = UNSET



Dictionary: TypeAlias = Dict[str, str]


class Choice(Enum):
    first = 'first'
    second = 'second'



class Required(Payload, kw_only=True):
    undeclared: Any