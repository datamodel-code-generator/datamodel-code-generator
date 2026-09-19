from __future__ import annotations
from typing import Any, List, Optional, Union
from pydantic import BaseModel, Field


class ShipTo(BaseModel):
    in_: Optional[str] = Field(None, alias='in')


class ABCItem(BaseModel):
    kebab_key: Optional[int] = Field(None, alias='kebab-key')


class Envelope(BaseModel):
    ship_to: ShipTo = Field(..., alias='ship-to')
    a_b_c: Optional[List[ABCItem]] = Field(None, alias='a/b~c')
    anything: Optional[Any] = None
    nothing: Optional[Any] = None
    tuple: Optional[List[Union[str, int]]] = Field(None, max_length=2)