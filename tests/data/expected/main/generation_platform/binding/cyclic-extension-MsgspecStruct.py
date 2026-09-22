from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import Union



class Pet(Struct):
    name: Union[str, UnsetType] = UNSET