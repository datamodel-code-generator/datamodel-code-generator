from __future__ import annotations
from typing import Annotated, List, Literal, Union
from pydantic import BaseModel, Field


class A(BaseModel):
    kind: Literal['a']


class B(BaseModel):
    kind: Literal['b']


class Envelope(BaseModel):
    value: Union[A, B] = Field(..., discriminator='kind')
    values: List[Annotated[Union[A, B], Field(discriminator='kind')]]


