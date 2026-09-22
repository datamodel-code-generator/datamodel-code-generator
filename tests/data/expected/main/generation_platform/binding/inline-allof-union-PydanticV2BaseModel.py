from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, RootModel


class Nested1(BaseModel):
    label: Optional[str] = None


class Nested2(BaseModel):
    pass


class Nested3(Nested1, Nested2):
    pass


class Nested(RootModel[Nested3]):
    root: Nested3


class Holder(BaseModel):
    nested: Optional[Nested] = None


