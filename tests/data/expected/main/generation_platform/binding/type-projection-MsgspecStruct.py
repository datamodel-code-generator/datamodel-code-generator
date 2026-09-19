from __future__ import annotations
from msgspec import Struct, UNSET, UnsetType
from typing import Dict, List, Literal, Optional, Set, Union



class Child(Struct):
    value: Union[str, UnsetType] = UNSET



class Types(Struct):
    flag: Union[bool, UnsetType] = UNSET
    count: Union[int, UnsetType] = UNSET
    name: Union[str, UnsetType] = UNSET
    moment: Union[str, UnsetType] = UNSET
    nullable: Union[str, None, UnsetType] = UNSET
    items: Union[List[int], UnsetType] = UNSET
    unique: Union[Set[str], UnsetType] = UNSET
    dictionary: Union[Dict[str, bool], UnsetType] = UNSET
    choice: Union[str, int, UnsetType] = UNSET
    position: Union[List[Union[str, int]], UnsetType] = UNSET
    child: Union[Child, UnsetType] = UNSET
    nullable_choice: Union[Union[int, str], None, UnsetType] = UNSET
    nullable_items: Union[List[Optional[Union[int, str]]], UnsetType] = UNSET
    external: Union[str, UnsetType] = UNSET
    literal: Union[Literal[1, 'one'], bool, UnsetType] = UNSET