from __future__ import annotations
from pydantic import BaseModel, RootModel
from typing import Optional


class Base(BaseModel):
    pass


class Nested1(BaseModel):
    label: Optional[str] = None


class Nested2(BaseModel):
    pass


class Nested3(Nested1, Nested2):
    pass


class Nested(RootModel[Nested3]):
    root: Nested3


class Mixed1(BaseModel):
    code: Optional[int] = None


class Mixed2(Mixed1, Base):
    pass


class Mixed(RootModel[Mixed2]):
    root: Mixed2


class Holder(BaseModel):
    nested: Optional[Nested] = None
    mixed: Optional[Mixed] = None


