# RootModel input control

from __future__ import annotations

from pydantic import BaseModel, Field


class OrdinaryBase(BaseModel):
    first: int = Field(..., title='First')


class OrdinaryChild(OrdinaryBase):
    second: str | None = Field('default', title='Second')
