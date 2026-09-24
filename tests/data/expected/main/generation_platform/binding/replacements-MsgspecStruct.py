from __future__ import annotations
from typing import List, Union
from msgspec import Struct, UNSET, UnsetType



class Record(Struct):
    value: Union[str, UnsetType] = UNSET



class Holder(Struct):
    text: Union[str, UnsetType] = UNSET
    list: Union[List[str], UnsetType] = UNSET
    original: Union[Record, UnsetType] = UNSET
    copy: Union[Record, UnsetType] = UNSET
    wrapper: Union[Record, UnsetType] = UNSET