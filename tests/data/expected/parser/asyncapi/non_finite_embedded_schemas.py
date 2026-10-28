from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, RootModel


class ProtobufValue(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    value: Optional[float] = float('inf')


class ValuesXmlValuePayload(RootModel[float]):
    root: float = Field(float('nan'), title='XmlValue')
