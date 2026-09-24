from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, RootModel


class Base(BaseModel):
    optional: Optional[str] = None


class NullableName(RootModel[Optional[str]]):
    root: Optional[str] = None


class Edges(Base):
    needed: int
    strict: Optional[str] = None
    wire_name: Optional[int] = Field(default=1, alias='wire-name')
    alias_ref: Optional[NullableName] = None


