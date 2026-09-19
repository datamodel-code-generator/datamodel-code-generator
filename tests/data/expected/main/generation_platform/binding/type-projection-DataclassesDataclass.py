from __future__ import annotations
from uuid import UUID
from typing import Dict, List, Literal, Optional, Set, Union
from dataclasses import dataclass
from uuid import UUID
import argparse



@dataclass
class Child:
    value: Optional[str] = None



@dataclass
class Types:
    flag: Optional[bool] = None
    count: Optional[int] = None
    name: Optional[str] = None
    moment: Optional[str] = None
    nullable: Optional[str] = None
    items: Optional[List[int]] = None
    unique: Optional[Set[str]] = None
    dictionary: Optional[Dict[str, bool]] = None
    choice: Optional[Union[str, int]] = None
    position: Optional[List[Union[str, int]]] = None
    child: Optional[Child] = None
    nullable_choice: Optional[Union[int, str]] = None
    nullable_items: Optional[List[Optional[Union[int, str]]]] = None
    external: Optional[str] = None
    literal: Optional[Literal[True, 1, 'one']] = None
    native: Optional[UUID] = None
    bound_native: Optional[argparse.HelpFormatter._Section] = None