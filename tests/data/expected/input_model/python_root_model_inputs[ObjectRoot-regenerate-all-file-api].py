# RootModel input control

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel


class Item(BaseModel):
    first: int = Field(..., title='First')
    second: str | None = Field('default', title='Second')


class ObjectRoot(RootModel[Item]):
    root: Item = Field(..., description='ObjectRoot input control.', title='ObjectRoot')
