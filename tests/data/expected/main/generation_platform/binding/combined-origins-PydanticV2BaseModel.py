from __future__ import annotations
from typing import Any, Optional, Union
from pydantic import BaseModel, Field, RootModel


class ShipTo(BaseModel):
    postal_code: Optional[str] = Field(None, alias='postal-code')


class Choice1(BaseModel):
    common_key: Optional[str] = Field(None, alias='common-key')
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')


class Choice2(BaseModel):
    common_key: Optional[str] = Field(None, alias='common-key')


class Never(RootModel[Any]):
    root: Any


class Named(BaseModel):
    external_name: Optional[str] = Field(None, alias='external-name')


class Choice(RootModel[Union[Choice1, Choice2, Named]]):
    root: Union[Choice1, Choice2, Named]


