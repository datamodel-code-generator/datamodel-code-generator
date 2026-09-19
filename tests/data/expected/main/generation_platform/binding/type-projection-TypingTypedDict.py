from __future__ import annotations
from uuid import UUID
from typing_extensions import NotRequired
from typing import Dict, List, Literal, Optional, Set, TypedDict, Union
from uuid import UUID
import argparse



class Child(TypedDict):
    value: NotRequired[str]



class Types(TypedDict):
    flag: NotRequired[bool]
    count: NotRequired[int]
    name: NotRequired[str]
    moment: NotRequired[str]
    nullable: NotRequired[Optional[str]]
    items: NotRequired[List[int]]
    unique: NotRequired[Set[str]]
    dictionary: NotRequired[Dict[str, bool]]
    choice: NotRequired[Union[str, int]]
    position: NotRequired[List[Union[str, int]]]
    child: NotRequired[Child]
    nullable_choice: NotRequired[Optional[Union[int, str]]]
    nullable_items: NotRequired[List[Optional[Union[int, str]]]]
    external: NotRequired[str]
    literal: NotRequired[Literal[True, 1, 'one']]
    native: NotRequired[UUID]
    bound_native: NotRequired[argparse.HelpFormatter._Section]