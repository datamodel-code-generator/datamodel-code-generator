from __future__ import annotations
from typing import Optional
from pydantic import Field, constr
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class SideKey:
    nested_key: Optional[str] = Field(None, alias='nested-key')



@dataclass
class RefUse1:
    shared_key: Optional[constr(min_length=1)] = Field(None, alias='shared-key')
    side_key: Optional[SideKey] = Field(None, alias='side-key')



RefUse = TypeAliasType("RefUse", RefUse1)



@dataclass
class Combined:
    shared_key: Optional[constr(min_length=1, max_length=9)] = Field(None, alias='shared-key')



@dataclass
class Base:
    shared_key: Optional[constr(min_length=1)] = Field(None, alias='shared-key')



@dataclass
class Other:
    shared_key: Optional[constr(max_length=9)] = Field(None, alias='shared-key')