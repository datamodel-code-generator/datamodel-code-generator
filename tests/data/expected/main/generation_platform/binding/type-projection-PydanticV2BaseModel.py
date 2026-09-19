from __future__ import annotations
from typing import Dict, List, Literal, Optional, Set, Union
from pydantic import AwareDatetime, BaseModel, Field, conint, constr


class Child(BaseModel):
    value: Optional[str] = None


class Types(BaseModel):
    flag: Optional[bool] = None
    count: Optional[conint(ge=2)] = None
    name: Optional[constr(pattern=r'^a/b~c$')] = None
    moment: Optional[AwareDatetime] = None
    nullable: Optional[str] = None
    items: Optional[List[int]] = None
    unique: Optional[Set[str]] = None
    dictionary: Optional[Dict[str, bool]] = None
    choice: Optional[Union[str, int]] = None
    position: Optional[List[Union[str, int]]] = Field(None, max_length=2)
    child: Optional[Child] = None
    nullable_choice: Optional[Union[int, str]] = None
    nullable_items: Optional[List[Optional[Union[int, str]]]] = None
    external: Optional[str] = None
    literal: Optional[Literal[True, 1, 'one']] = None


