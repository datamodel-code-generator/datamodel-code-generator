from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, RootModel


class Pet(BaseModel):
    id: int
    name: str
    tag: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]


class Error(BaseModel):
    code: int
    message: str


class Resolved(BaseModel):
    resolved: Optional[List[str]] = None
