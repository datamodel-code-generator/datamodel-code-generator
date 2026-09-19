from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, RootModel, constr


class SideKey(BaseModel):
    nested_key: Optional[str] = Field(None, alias='nested-key')


class RefUse1(BaseModel):
    shared_key: Optional[constr(min_length=1)] = Field(None, alias='shared-key')
    side_key: Optional[SideKey] = Field(None, alias='side-key')


class RefUse(RootModel[RefUse1]):
    root: RefUse1


class Combined(BaseModel):
    shared_key: Optional[constr(min_length=1, max_length=9)] = Field(None, alias='shared-key')


class Base(BaseModel):
    shared_key: Optional[constr(min_length=1)] = Field(None, alias='shared-key')


class Other(BaseModel):
    shared_key: Optional[constr(max_length=9)] = Field(None, alias='shared-key')


