from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, RootModel


class Envelope1(BaseModel):
    ship_to: Optional[str] = Field(None, alias='ship-to')
    other_key: Optional[int] = Field(None, alias='other-key')


class Envelope2(BaseModel):
    other_key: Optional[int] = Field(None, alias='other-key')
    ship_to: Optional[str] = Field(None, alias='ship-to')


class Variants1(BaseModel):
    ship_to: Optional[str] = Field(None, alias='ship-to')
    other_key: Optional[int] = Field(None, alias='other-key')


class Variants(RootModel[Variants1]):
    root: Variants1


class Envelope(BaseModel):
    nested: Optional[Variants] = None


