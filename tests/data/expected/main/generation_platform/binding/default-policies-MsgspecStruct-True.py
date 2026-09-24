from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import Union



class Defaults(Struct):
    implicit: Union[str, UnsetType] = UNSET
    schema_null: Union[str, UnsetType] = UNSET
    override_null: Union[str, UnsetType] = UNSET
    nullable: Union[str, UnsetType] = UNSET
    required_value: Union[int, UnsetType] = UNSET
    required_nullable: Union[str, UnsetType] = UNSET