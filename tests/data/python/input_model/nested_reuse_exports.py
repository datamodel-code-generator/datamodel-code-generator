"""Successful top-level and explicit nested reexport controls."""
from pydantic import BaseModel


class Direct(BaseModel):
    value: int


class Outer:
    class Inner(BaseModel):
        value: int


Inner = Outer.Inner


class DirectRoot(BaseModel):
    child: Direct


class ExportRoot(BaseModel):
    child: Inner
