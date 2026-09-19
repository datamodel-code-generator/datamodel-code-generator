from __future__ import annotations
from pydantic import Field, constr
from typing import Optional
from pydantic.dataclasses import dataclass



@dataclass
class BaseRequest:
    raw_note: Optional[constr(min_length=1)] = Field('base', alias='raw-note')
    secret: Optional[str] = None



@dataclass
class BaseResponse:
    raw_note: Optional[constr(min_length=1)] = Field('base', alias='raw-note')
    id: Optional[int] = None



@dataclass
class Base:
    raw_note: Optional[constr(min_length=1)] = Field('base', alias='raw-note')
    id: Optional[int] = None
    secret: Optional[str] = None



@dataclass
class ChildRequest:
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note')
    secret: Optional[str] = None



@dataclass
class ChildResponse:
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note')
    id: Optional[int] = None



@dataclass
class Child(Base):
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note')



@dataclass
class GrandChildRequest:
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note', description='Inherited note')
    secret: Optional[str] = None



@dataclass
class GrandChildResponse:
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note', description='Inherited note')
    id: Optional[int] = None



@dataclass
class GrandChild(Child):
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note', description='Inherited note')