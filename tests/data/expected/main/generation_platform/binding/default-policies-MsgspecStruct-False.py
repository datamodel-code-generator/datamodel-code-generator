from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import Union



class Defaults(Struct):
    required_value: int
    required_nullable: str
    implicit: Union[str, UnsetType] = UNSET
    schema_null: Union[str, UnsetType] = UNSET
    override_null: Union[str, UnsetType] = UNSET
    nullable: Union[str, UnsetType] = UNSET