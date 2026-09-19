from __future__ import annotations
from pydantic import BaseModel, Field, constr
from typing import Optional


class BaseRequest(BaseModel):
    raw_note: Optional[constr(min_length=1)] = Field('base', alias='raw-note')
    secret: Optional[str] = None


class BaseResponse(BaseModel):
    raw_note: Optional[constr(min_length=1)] = Field('base', alias='raw-note')
    id: Optional[int] = None


class Base(BaseModel):
    raw_note: Optional[constr(min_length=1)] = Field('base', alias='raw-note')
    id: Optional[int] = None
    secret: Optional[str] = None


class ChildRequest(BaseModel):
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note')
    secret: Optional[str] = None


class ChildResponse(BaseModel):
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note')
    id: Optional[int] = None


class Child(Base):
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note')


class GrandChildRequest(BaseModel):
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note', description='Inherited note')
    secret: Optional[str] = None


class GrandChildResponse(BaseModel):
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note', description='Inherited note')
    id: Optional[int] = None


class GrandChild(Child):
    raw_note: Optional[constr(min_length=1)] = Field(None, alias='raw-note', description='Inherited note')


